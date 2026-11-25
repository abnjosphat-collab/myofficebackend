# backend/app/routers/employees.py
from fastapi import APIRouter, HTTPException, Query, Depends
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.cache import cached, invalidate_namespace, build_key, cache_get, cache_set
from app.db_helpers import fetch_all_pages, get_or_404
from app.concurrency import assert_unchanged, touch
from app.errors import server_error
from app.scope import apply_list_scope, assert_row_visible, row_visible, stamp_create, viewer_scope, section_of, requested_department, exclude_separate_departments, SEPARATE_DEPARTMENTS
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter()


#: Canonical display for a Zimbabwe mobile number (grouping callers see).
PHONE_EXAMPLE = "+263 77 8973 142"


class PhoneError(ValueError):
    """A new phone value that cannot be understood (422 field error, never silent)."""


def _canonical_single_phone(number: str) -> str:
    """Canonical form of one phone number; raises PhoneError when malformed.

    Zimbabwe mobiles collapse to ``+263 XX XXXX XXX`` (2-4-3 grouping).
    Zimbabwe landlines (``0...``) and other internationals (``+<cc> ...``)
    are accepted and lightly normalised (separators removed) but keep their
    own shape. Digit caps: 9 national digits for Zimbabwe, at most 15 digits
    (E.164) otherwise — longer input is rejected, never silently truncated.
    """
    text = number.strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    digits = re.sub(r"[\s\-.()]", "", text)
    if not digits or not re.fullmatch(r"\+?\d+", digits):
        raise PhoneError(
            f"'{number.strip()}' is not a valid phone number "
            f"(Zimbabwe mobile example: {PHONE_EXAMPLE})."
        )
    if digits.startswith("+"):
        digits = digits[1:]
        if digits.startswith("263"):
            national = digits[3:]
            if len(national) == 9 and national.startswith("7") and national.isdigit():
                return f"+263 {national[0:2]} {national[2:6]} {national[6:9]}"
            if national.isdigit() and len(national) in (9, 10):
                return f"+263 {national}"
            raise PhoneError(
                f"'{number.strip()}' is not a valid +263 number "
                f"(Zimbabwe mobile example: {PHONE_EXAMPLE})."
            )
        if 7 <= len(digits) <= 15:
            return f"+{digits}"
        raise PhoneError(
            f"'{number.strip()}' is not a valid international number "
            f"(Zimbabwe mobile example: {PHONE_EXAMPLE})."
        )
    if digits.startswith("07") and len(digits) == 10 and digits.isdigit():
        national = digits[1:]
        return f"+263 {national[0:2]} {national[2:6]} {national[6:9]}"
    if digits.startswith("0") and digits.isdigit() and len(digits) in (9, 10):
        return f"+263 {digits[1:]}"
    if len(digits) == 9 and digits.startswith("7") and digits.isdigit():
        return f"+263 {digits[0:2]} {digits[2:6]} {digits[6:9]}"
    raise PhoneError(
        f"'{number.strip()}' is not a valid phone number "
        f"(Zimbabwe mobile example: {PHONE_EXAMPLE})."
    )


def _normalize_name_part(match: "re.Match[str]") -> str:
    """Normalise one whitespace/hyphen/apostrophe-separated part.

    Uniform-case words are title-cased (``mercy``/``MERCY`` -> ``Mercy``),
    inverted-case words too (``mERCY`` -> ``Mercy``); genuinely mixed-case
    words keep their interior capitals (``McDonald`` stays as typed, first
    letter uppercased) so real names survive. Never rejects.
    """
    word = match.group(0)
    if word.isupper() or word.islower():
        return word.capitalize()
    if len(word) > 1 and word[0].islower() and word[1:].isupper():
        return word.capitalize()
    return word[:1].upper() + word[1:] if word[:1].isalpha() else word


def normalize_person_name(value: object) -> str:
    """Canonical person name: trim, collapse whitespace, then normalise each
    space/hyphen/apostrophe-separated part (see :func:`_normalize_name_part`).

    ``mercy`` / ``MERCY`` / ``mERCY mazarire`` all become ``Mercy Mazarire``,
    while ``McDonald`` and ``O'Brien`` keep their shape. Normalise-only:
    casing is fixed, nothing contentful is ever rejected.
    """
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        return ""
    return re.sub(r"[^\s\-']+", _normalize_name_part, cleaned)


def normalize_national_id(value: object) -> str:
    """Canonical national ID: uppercase, dashes as the only separator.

    ``21 - 275641 v21`` becomes ``21-275641-V21``. Normalise-only: shape is
    never validated and nothing is ever rejected.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    out = re.sub(r"[\s./_]+", "-", text.upper())
    out = re.sub(r"-+", "-", out).strip("-")
    return out


class Employee(BaseModel):
    """
    Employee record. `employee_id` (mine number) is the user-visible identifier
    (e.g. "C1165", "PM365"), is freely editable, and is optional: a new hire
    has none until it is issued (usually the start of the following month).
    Blank collapses to '' (never NULL) so there is no NOT NULL risk, and a
    blank id never clashes. Clearing an issued employee_id is rejected (422):
    overtime, leave and PPE history is linked by that id text.
    The immutable database primary key is the integer `id` returned
    in API responses — the frontend uses that for all update/delete calls.
    """
    employee_id: Optional[str] = Field(default='',
                             max_length=50,
                             description="Human-readable employee ID, e.g. C1165 or PM365; blank until issued")
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    id_number: Optional[str] = Field(default='',
                             max_length=50,
                             description="National ID or passport number; blank until provided")
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    date_of_engagement: Optional[date] = Field(None, description="Date of employment")
    designation: str = Field(..., min_length=1, description="Job title / position")
    employee_class: Optional[str] = None  # Permanent, Contract, Internship, Part-Time
    employment_type: Optional[str] = None  # NEC or SALARIED
    supervisor: Optional[str] = None
    section: Optional[str] = None
    department: Optional[str] = None
    grade: Optional[str] = None
    qualifications: Optional[List[str]] = Field(default_factory=list)
    drivers_license_class: Optional[str] = None
    ppe_issue_date: Optional[date] = None
    offences: Optional[List[str]] = Field(default_factory=list)
    awards_recognition: Optional[List[str]] = Field(default_factory=list)
    other_positions: Optional[List[str]] = Field(default_factory=list)
    previous_employer: Optional[str] = None
    archived: Optional[bool] = False
    # Optimistic concurrency: the updated_at the editor opened (popped before every write).
    expected_updated_at: Optional[str] = None

    class Config:
        json_encoders = {date: lambda v: v.isoformat()}

    @validator('date_of_engagement', pre=True)
    def empty_engagement_date(cls, v):
        if v is None or v == '':
            return None
        return v

    @validator('first_name', 'last_name')
    def clean_name(cls, v: str) -> str:
        # Normalise case, collapse whitespace — one space between words, none
        # at the ends: "Tafadzwa " and "Stanley  Musevenzo" were found in live
        # data, and names that differ by a space are treated as different
        # people by every search and every match. Content is never rejected
        # for its casing ("mERCY" -> "Mercy"); only a wholly blank name is.
        cleaned = normalize_person_name(v)
        if not cleaned:
            raise ValueError('Name cannot be empty')
        return cleaned

    @validator('employee_id')
    def clean_employee_id(cls, v) -> str:
        # Optional: blank collapses to '' (never NULL). A given number keeps
        # the compact-text rule — it must contain at least one letter or
        # digit, so dot-only placeholders (".............") typed on the
        # register are rejected, not stored.
        if v is None:
            return ''
        cleaned = v.strip().upper() if isinstance(v, str) else v
        if not cleaned:
            return ''
        if not re.search(r'[A-Z0-9]', cleaned):
            raise ValueError('Employee ID must contain at least one letter or digit')
        return cleaned

    @validator('id_number')
    def clean_id_number(cls, v) -> str:
        # Optional and normalise-only: uppercase, dashes as the only
        # separator ("21 - 275641 v21" -> "21-275641-V21"). Shape is never
        # validated, nothing is ever rejected; blank collapses to ''.
        return normalize_national_id(v)

    @validator('phone')
    def clean_phone(cls, v):
        # Optional; a filled number is canonicalised (ZW mobiles collapse to
        # +263 XX XXXX XXX) and over-long input is rejected, never truncated.
        if v is None:
            return None
        text = v.strip() if isinstance(v, str) else v
        if not text:
            return None
        return _canonical_single_phone(text)

    @validator('email')
    def clean_email(cls, v):
        # Optional, but a filled address must look like one — the same rule
        # as the employee form (frontend lib/email.ts isEmailLike).
        if v is None:
            return None
        text = v.strip() if isinstance(v, str) else v
        if not text:
            return None
        if not re.match(r'^\S+@\S+\.\S+$', text):
            raise ValueError('Enter a valid email address.')
        return text

    @validator('qualifications', 'offences', 'awards_recognition', 'other_positions',
               pre=True, always=True)
    def ensure_list(cls, v):
        return v if isinstance(v, list) else []


class BulkNormalizeItem(BaseModel):
    """Partial employee update produced by roster normalization."""
    id: int = Field(..., gt=0)
    designation: Optional[str] = None
    section: Optional[str] = None
    phone: Optional[str] = None
    archived: Optional[bool] = None


class BulkNormalizeRequest(BaseModel):
    updates: List[BulkNormalizeItem] = Field(..., min_items=1)


class BulkNormalizeResponse(BaseModel):
    succeeded: int
    failed: int
    errors: List[str] = Field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────

# Records that refer to a person by their employee_id text. Changing the id must carry their history with it, or the
# overtime, leave and PPE entered under the old id are left pointing at nobody. (Once the database has the foreign keys
# from supabase_migration_integrity_constraints.sql it does this itself with ON UPDATE CASCADE; this keeps it true before.)
_RECORDS_BY_EMPLOYEE_ID = ("overtime", "leaves", "ppe_records")


def _follow_changed_employee(old: dict, new: dict) -> None:
    """Carry a personnel edit into the copies overtime, leaves and PPE keep beside the id: a new number re-points the
    rows, and a changed name, designation, department or section rewrites that table's copied columns under the (new)
    number. Values compare stripped (a whitespace tidy-up alone follows nothing) but write exactly as stored.
    Blank ids never re-point: rows keyed by '' cannot be attributed to one
    person, so an id issued later does not adopt them (and a cleared id is
    rejected with 422 before this runs)."""
    old_id, new_id = old.get("employee_id"), new.get("employee_id")
    if new_id != old_id and old_id and new_id:
        for table in _RECORDS_BY_EMPLOYEE_ID:
            supabase.table(table).update({"employee_id": new_id}).eq("employee_id", old_id).execute()
    old_name = " ".join(f"{old.get('first_name') or ''} {old.get('last_name') or ''}".split())
    new_name = " ".join(f"{new.get('first_name') or ''} {new.get('last_name') or ''}".split())
    copies: dict = {"overtime": {}, "leaves": {}, "ppe_records": {}}
    if new_name != old_name:
        for table in copies:
            copies[table]["employee_name"] = new_name
    if (new.get("designation") or "").strip() != (old.get("designation") or "").strip():
        for table in copies:
            copies[table]["position"] = new.get("designation")
    if (new.get("department") or "").strip() != (old.get("department") or "").strip():
        copies["overtime"]["department"] = new.get("department")
        copies["ppe_records"]["department"] = new.get("department")
    if (new.get("section") or "").strip() != (old.get("section") or "").strip():
        copies["ppe_records"]["mine_section"] = new.get("section")
    for table, payload in copies.items():
        # Copies are keyed by employee_id text: with a blank new id there is
        # no single person to attribute them to, so nothing follows.
        if payload and new_id:
            supabase.table(table).update(payload).eq("employee_id", new_id).execute()


def _records_that_use(employee_id: str) -> list:
    """The tables that still hold records for this person (empty when none)."""
    used = []
    for table in _RECORDS_BY_EMPLOYEE_ID:
        found = _data(supabase.table(table).select("id").eq("employee_id", employee_id).limit(1).execute())
        if found:
            used.append(table)
    return used


def _dates_to_db(data: dict) -> dict:
    """Convert date objects → ISO strings for Supabase."""
    out = data.copy()
    for field in ('date_of_engagement', 'ppe_issue_date'):
        val = out.get(field)
        if val == '':
            out[field] = None
        elif isinstance(val, date):
            out[field] = val.isoformat()
    return out


def _prepare_employee_write(data: dict, existing: Optional[dict] = None) -> dict:
    """
    Build a Supabase write payload. Omit `archived` when unchanged or still
    active (false) so routine edits work even if PostgREST's schema cache is
    briefly stale after the column was added.
    """
    out = _dates_to_db(data)
    new_archived = bool(out.get('archived', False))
    old_archived = bool((existing or {}).get('archived', False))
    if new_archived == old_archived or (not new_archived and not old_archived):
        out.pop('archived', None)
    return out


def _dates_from_db(data: dict) -> dict:
    """Convert ISO date strings → date objects; normalise arrays."""
    out = data.copy()
    for field in ('date_of_engagement', 'ppe_issue_date'):
        if out.get(field) and isinstance(out[field], str):
            try:
                out[field] = date.fromisoformat(out[field])
            except (ValueError, TypeError):
                out[field] = None
    for field in ('qualifications', 'offences', 'awards_recognition', 'other_positions'):
        if not isinstance(out.get(field), list):
            out[field] = []
    return out


def _data(response) -> list:
    return response.data if hasattr(response, 'data') else response


# ── Routes ────────────────────────────────────────────────────────────────────
# NOTE: health/status and search/{query} are declared BEFORE /{id} so FastAPI
#       matches them first (they contain non-integer path segments).

@router.get("/health/status", tags=["Health"])
async def employees_health():
    try:
        _data(supabase.table("employees").select("id").limit(1).execute())
        return {"status": "healthy", "service": "employees", "database": "connected"}
    except Exception as e:
        logger.error(f"Employees health check failed: {e}")
        raise HTTPException(503, detail="Employees service unhealthy.")


@router.get("/search/{query}")
async def search_employees(
    query: str,
    search_by: str = Query("all", enum=["all", "name", "id", "id_number", "email"]),
    current_user: dict = Depends(get_current_user),
):
    """Search employees by various fields."""
    try:
        q = f"%{query}%"
        if search_by == "name":
            r = supabase.table("employees").select("*") \
                .or_(f"first_name.ilike.{q},last_name.ilike.{q}").execute()
        elif search_by == "id":
            r = supabase.table("employees").select("*").ilike("employee_id", q).execute()
        elif search_by == "id_number":
            r = supabase.table("employees").select("*").ilike("id_number", q).execute()
        elif search_by == "email":
            r = supabase.table("employees").select("*").ilike("email", q).execute()
        else:
            r = supabase.table("employees").select("*") \
                .or_(f"first_name.ilike.{q},last_name.ilike.{q},"
                     f"employee_id.ilike.{q},id_number.ilike.{q}").execute()

        rows = _data(r)
        if viewer_scope(current_user) is not None:
            rows = [e for e in rows if row_visible(current_user, "employees", e)]
        else:
            # Separate departments (Mining) stay out of unscoped search.
            rows = [e for e in rows if (e.get("department") or "") not in SEPARATE_DEPARTMENTS]
        return [_dates_from_db(e) for e in rows] if rows else []
    except Exception as e:
        raise server_error(e, "Searching employees")


@router.post("/bulk-normalize")
async def bulk_normalize_employees(body: BulkNormalizeRequest, current_user: dict = Depends(require_role('manager'))):
    """
    Apply roster normalization patches in one request.
    Updates only the fields provided per employee — no full-record validation.
    """
    succeeded = 0
    failed = 0
    errors: List[str] = []
    scope = viewer_scope(current_user)

    for item in body.updates:
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        patch: dict = item.model_dump(exclude_unset=True)
        patch.pop("id", None)

        if not patch:
            continue

        try:
            if scope is not None:
                target = _data(supabase.table("employees").select("id,section").eq("id", item.id).execute())
                if not target or not row_visible(current_user, "employees", target[0]):
                    failed += 1
                    errors.append(f"Employee #{item.id} not found")
                    continue
                if patch.get("section") is not None and patch["section"] != section_of(current_user):
                    failed += 1
                    errors.append(f"Employee #{item.id} is outside your section")
                    continue
            rows = _data(
                supabase.table("employees").update(patch).eq("id", item.id).execute()
            )
            if rows:
                succeeded += 1
            else:
                failed += 1
                errors.append(f"Employee #{item.id} not found")
        except Exception as e:
            failed += 1
            errors.append(f"Employee #{item.id}: {e}")

    if succeeded:
        await invalidate_namespace("employees")

    return BulkNormalizeResponse(succeeded=succeeded, failed=failed, errors=errors[:25])


@router.get("")
@router.get("/")
async def get_employees(department: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    """Return all employees. Separate departments (Mining) are left out unless
    the request says ?department=Mining — admins and managers only, everyone
    else gets 403. Any other department value filters to it, as before.
    Manual build_key() caching (not @cached): the department slicer is part
    of the key."""
    try:
        scope = viewer_scope(current_user)
        if scope is not None:
            rows = await run_in_threadpool(lambda: fetch_all_pages(
                lambda start, end: apply_list_scope(
                    supabase.table("employees").select("*")
                    .order("employee_id", desc=False).order("id", desc=False),
                    current_user, "employees").range(start, end).execute()
            ))
            return [_dates_from_db(e) for e in rows] if rows else []
        asked = requested_department(current_user, department)
        wanted = asked or (department.strip() if isinstance(department, str) and department.strip() else None)
        cache_key = build_key("employees", _fn="get_employees", department=asked or wanted or "__default__")
        hit = await cache_get(cache_key)
        if hit is not None:
            return hit
        query = supabase.table("employees").select("*").order("employee_id", desc=False).order("id", desc=False)
        if wanted:
            query = query.eq("department", wanted)
        else:
            query = exclude_separate_departments(query)
        rows = await run_in_threadpool(lambda: fetch_all_pages(
            lambda start, end: query.range(start, end).execute()
        ))
        result = [_dates_from_db(e) for e in rows] if rows else []
        await cache_set(cache_key, result, ttl=60, namespace="employees")
        return result
    except Exception as e:
        raise server_error(e, "Loading employees")


@router.get("/{id}")
async def get_employee(id: int, current_user: dict = Depends(get_current_user)):
    """Return a single employee by their database ID."""
    try:
        row = get_or_404(supabase, "employees", id, detail=f"Employee #{id} not found")
        assert_row_visible(current_user, "employees", row, detail=f"Employee #{id} not found")
        return _dates_from_db(row)
    except HTTPException:
        raise
    except Exception as e:
        raise server_error(e, "Loading employee")


@router.post("")
@router.post("/")
async def create_employee(employee: Employee, current_user: dict = Depends(get_current_user)):
    """Create a new employee. A given employee_id must be unique; a blank one
    (not yet issued) never clashes and skips the check."""
    try:
        # Reject duplicate employee_id — but a blank id never clashes.
        if employee.employee_id:
            clash = _data(
                supabase.table("employees")
                .select("id")
                .eq("employee_id", employee.employee_id)
                .execute()
            )
            if clash:
                raise HTTPException(
                    400,
                    detail=f"Employee ID '{employee.employee_id}' is already in use. "
                           "Choose a different ID."
                )

        data = employee.model_dump()
        data.pop('expected_updated_at', None)  # creates cannot conflict; never stored
        payload = stamp_create(current_user, "employees", _prepare_employee_write(data))
        rows = _data(supabase.table("employees").insert(payload).execute())
        if not rows:
            raise server_error(RuntimeError("Supabase returned no row for the insert"), "Creating employee")
        await invalidate_namespace("employees")
        return _dates_from_db(rows[0])

    except HTTPException:
        raise
    except Exception as e:
        raise server_error(e, "Creating employee")


@router.put("/{id}")
async def update_employee(id: int, updated: Employee, current_user: dict = Depends(get_current_user)):
    """
    Update an employee record identified by the integer database ID.
    employee_id in the request body can be freely changed to any value
    (e.g. from EMP_1165 to C1165) as long as it isn't already used by
    a *different* employee.
    """
    try:
        # Confirm the employee exists (the person columns ride along so a rename or re-designation can be carried
        # into the overtime, leave and PPE copies by _follow_changed_employee below).
        try:
            existing_rows = _data(
                supabase.table("employees").select("id,employee_id,first_name,last_name,designation,department,section,archived,updated_at").eq("id", id).execute()
            )
        except Exception:
            existing_rows = _data(
                supabase.table("employees").select("id,employee_id,first_name,last_name,designation,department,section").eq("id", id).execute()
            )
            for row in existing_rows:
                row.setdefault("archived", False)
        if not existing_rows:
            raise HTTPException(404, detail=f"Employee #{id} not found")

        existing = existing_rows[0]
        scope = viewer_scope(current_user)
        if scope is not None:
            assert_row_visible(current_user, "employees", existing, detail=f"Employee #{id} not found")
            if scope["kind"] == "section":
                if updated.section != scope["value"]:
                    raise HTTPException(404, detail=f"Employee #{id} not found")
            elif (updated.department or "") != scope["value"]:
                raise HTTPException(404, detail=f"Employee #{id} not found")
        data = updated.model_dump()
        assert_unchanged(existing, data.pop('expected_updated_at', None))
        old_emp_id = existing.get('employee_id') or ''
        new_emp_id = updated.employee_id or ''

        # Clearing an issued id is rejected (422): overtime, leave and PPE
        # history is linked by that id text, and a blank cannot carry it.
        if old_emp_id and not new_emp_id:
            raise HTTPException(
                422,
                detail=f"Employee ID '{old_emp_id}' is used by overtime, leave or PPE records and cannot be cleared. "
                       "Issue a replacement ID instead."
            )

        # If employee_id is changing to a given (non-blank) value, make sure
        # the new one isn't taken — a blank id never clashes.
        if new_emp_id != old_emp_id and new_emp_id:
            clash = _data(
                supabase.table("employees")
                .select("id")
                .eq("employee_id", new_emp_id)
                .neq("id", id)
                .execute()
            )
            if clash:
                raise HTTPException(
                    400,
                    detail=f"Employee ID '{new_emp_id}' is already used by another employee."
                )

        payload = _prepare_employee_write(data, existing)
        touch(existing, payload)
        rows = _data(
            supabase.table("employees").update(payload).eq("id", id).execute()
        )
        if not rows:
            raise server_error(RuntimeError("Supabase returned no row for the update"), "Updating employee")
        _follow_changed_employee(existing, {
            "employee_id": new_emp_id, "first_name": updated.first_name, "last_name": updated.last_name,
            "designation": updated.designation, "department": updated.department, "section": updated.section,
        })
        await invalidate_namespace("employees")
        return _dates_from_db(rows[0])

    except HTTPException:
        raise
    except Exception as e:
        raise server_error(e, "Updating employee")


@router.delete("/{id}")
async def delete_employee(id: int, current_user: dict = Depends(require_role('manager'))):
    """Delete an employee by their database ID."""
    try:
        existing_rows = _data(
            supabase.table("employees")
            .select("id,employee_id,first_name,last_name,section")
            .eq("id", id)
            .execute()
        )
        if not existing_rows:
            raise HTTPException(404, detail=f"Employee #{id} not found")
        if viewer_scope(current_user) is not None:
            assert_row_visible(current_user, "employees", existing_rows[0], detail=f"Employee #{id} not found")

        emp = existing_rows[0]
        name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip() or "Unknown"

        if _records_that_use(emp['employee_id']):
            raise HTTPException(409, detail=f"{name} has overtime, leave or PPE records, so the record cannot be deleted. Archive the employee instead; that keeps the history.")

        supabase.table("employees").delete().eq("id", id).execute()
        await invalidate_namespace("employees")

        return {
            "success": True,
            "detail": f"{name} ({emp['employee_id']}) deleted",
            "deleted_id": id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise server_error(e, "Deleting employee")
