"""Set types and physical sets: enter a set's contents once, then make and issue numbered physical sets.

A set type is the catalogue row (title plus ordered content lines). A physical
set stays an ordinary tools_workspace_equipment row with set_type_id, so
custody, issue/return, history, undo, inspections and the monthly toolbox
checks keep working. Its pieces are tools_workspace_set_lines rows numbered
SET-03/017, each with its own condition and status.

Set types are a mine-wide catalogue (they carry no department); every physical
set belongs to the department of the account that made it, and the usual
department scope applies to those.
"""
import re
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routers import tools_workspace as tw

router = APIRouter()

PIECE_CONDITIONS = ("Good", "Damaged", "Missing parts")
PIECE_STATUSES = ("present", "missing", "damaged")
MAX_ISSUE_PEOPLE = 100
MAX_MAKE_COUNT = 50


class SetTypeLineInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    size: Optional[str] = Field(default=None, max_length=40)
    qty: int = Field(default=1, ge=1, le=500)
    notes: Optional[str] = Field(default=None, max_length=500)


class SetTypeCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    make_model: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)
    competency_required: bool = False
    notes: Optional[str] = Field(default=None, max_length=2000)
    lines: List[SetTypeLineInput] = Field(min_length=1, max_length=2000)


class SetTypeUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=160)
    make_model: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)
    competency_required: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    active: Optional[bool] = None


class SetTypeLineUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    size: Optional[str] = Field(default=None, max_length=40)
    qty: Optional[int] = Field(default=None, ge=1, le=500)
    notes: Optional[str] = Field(default=None, max_length=500)
    line_no: Optional[int] = Field(default=None, ge=1)


class MakeInStoreInput(BaseModel):
    count: int = Field(default=1, ge=1, le=MAX_MAKE_COUNT)
    department: Optional[str] = Field(default=None, max_length=100)


class IssueSetsInput(BaseModel):
    employee_ids: List[str] = Field(min_length=1, max_length=MAX_ISSUE_PEOPLE)
    location: str = Field(min_length=1, max_length=240)
    job_reference: Optional[str] = Field(default=None, max_length=160)
    expected_return_at: Optional[str] = None
    assigned_equipment: List[str] = Field(default_factory=list, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=2000)
    conditions: Optional[Dict[str, Dict[str, str]]] = None
    override_due_checks: bool = False
    override_reason: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: Optional[str] = None
    department: Optional[str] = Field(default=None, max_length=100)


class SetReturnInput(BaseModel):
    location: str = Field(min_length=1, max_length=240)
    condition: str = Field(default="Good", max_length=60)
    notes: Optional[str] = Field(default=None, max_length=2000)
    piece_conditions: Optional[Dict[str, str]] = None
    piece_status: Optional[Dict[str, str]] = None
    idempotency_key: Optional[str] = None


class PieceUpdateInput(BaseModel):
    condition: Optional[str] = Field(default=None, max_length=60)
    status: Optional[Literal["present", "missing", "damaged"]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


def _type_or_404(set_type_id: str) -> dict[str, Any]:
    found = tw._find("set_types", "id", set_type_id)
    if not found:
        raise HTTPException(404, "That set type was not found.")
    return found


def _type_lines(set_type_id: str) -> list[dict[str, Any]]:
    return sorted(
        [row for row in tw._all("set_type_lines") if row.get("set_type_id") == set_type_id],
        key=lambda row: (row.get("line_no") or 0, row.get("created_at") or ""),
    )


def _physical_sets(set_type_id: str) -> list[dict[str, Any]]:
    return [row for row in tw._all("tools") if row.get("set_type_id") == set_type_id]


def _set_pieces(set_tool_id: str) -> list[dict[str, Any]]:
    return sorted(
        [row for row in tw._all("set_lines") if row.get("set_tool_id") == set_tool_id],
        key=lambda row: (row.get("line_no") or 0, row.get("piece_number") or ""),
    )


def _type_counts(set_type: dict[str, Any]) -> dict[str, Any]:
    physical = _physical_sets(set_type["id"])
    out = sum(1 for row in physical if row.get("custody"))
    attention = sum(1 for row in physical if not row.get("custody") and row.get("status") == "attention")
    return {
        "line_count": len(_type_lines(set_type["id"])),
        "physical_total": len(physical),
        "in_store": sum(1 for row in physical if not row.get("custody") and row.get("status") == "available"),
        "out": out,
        "attention": attention,
    }


def _type_view(set_type: dict[str, Any]) -> dict[str, Any]:
    return {**set_type, **_type_counts(set_type)}


def _renumber_type_lines(set_type_id: str) -> list[dict[str, Any]]:
    """Keep a type's lines numbered 1..N in their current order."""
    lines = _type_lines(set_type_id)
    for position, row in enumerate(lines, start=1):
        if row.get("line_no") != position:
            tw._update("set_type_lines", row["id"], {"line_no": position})
    return _type_lines(set_type_id)


def _piece_number(register_number: str, sequence: int) -> str:
    return f"{register_number}/{sequence:03d}"


def _managed_department(account: dict[str, Any], department: Optional[str]) -> str:
    """The department new physical sets belong to: the issuer's own, or the
    named one for an admin. Issuers can only ever make sets for their own."""
    wanted = (department or "").strip() or (account.get("department") or "").strip()
    tw._ensure_managed_department(account, wanted)
    if not wanted:
        raise HTTPException(422, "Name the department these sets belong to.")
    return wanted


def _idem_result(key: Optional[str]) -> Optional[dict[str, Any]]:
    if not key:
        return None
    found = tw._find("idempotency", "key", key)
    return found.get("result") if found else None


def _idem_store(key: str, result: dict[str, Any]) -> None:
    try:
        tw._insert("idempotency", {"key": key, "result": result})
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "conflict" in str(exc).lower():
            return
        raise


def _make_physical_set(
    set_type: dict[str, Any],
    type_lines: list[dict[str, Any]],
    department: str,
    actor: str,
) -> dict[str, Any]:
    """Create one available physical set with its numbered pieces."""
    number = tw._next_number("SET")
    if any(row.get("register_number", "").lower() == number.lower() for row in tw._all("tools")):
        raise HTTPException(409, "That register number already exists.")
    values = {
        "id": str(uuid.uuid4()),
        "register_number": number,
        "name": set_type["title"],
        "make_model": set_type.get("make_model"),
        "category": set_type.get("category"),
        "equipment_kind": "other-equipment",
        "storage_location": "Store",
        "home_storage_location": "Store",
        "department": department,
        "set_type_id": set_type["id"],
        "competency_required": bool(set_type.get("competency_required", False)),
        "trade_sections": tw.suggest_trade_sections(set_type.get("title"), "other-equipment", set_type.get("category")),
        "weekly_inspection_required": False,
        "monthly_inspection_required": False,
        "quarterly_inspection_required": False,
        "calibration_required": False,
        "status": "available",
        "condition": "Good",
        "custody": None,
    }
    created = tw._apply_change(None, values, "created", actor, f"Set {number} made from type {set_type['title']}")["tool"]
    sequence = 0
    for line in type_lines:
        for _ in range(int(line.get("qty") or 1)):
            sequence += 1
            tw._insert(
                "set_lines",
                {
                    "id": str(uuid.uuid4()),
                    "set_tool_id": created["id"],
                    "line_no": sequence,
                    "piece_number": _piece_number(number, sequence),
                    "name": line.get("name"),
                    "size": line.get("size"),
                    "qty": 1,
                    "notes": line.get("notes"),
                    "condition": "Good",
                    "status": "present",
                    "set_type_line_id": line.get("id"),
                    "created_at": tw._now(),
                    "updated_at": tw._now(),
                },
            )
    return {**created, "pieces": _set_pieces(created["id"])}


@router.get("/set-types")
def list_set_types(account=Depends(tw._session)):
    """Every set type with line counts and physical-set counts."""
    scope = tw._department_scope(account)
    out = []
    for set_type in tw._all("set_types"):
        view = _type_view(set_type)
        if scope is not None:
            physical = [row for row in _physical_sets(set_type["id"]) if row.get("department") == scope]
            view = {
                **set_type,
                "line_count": view["line_count"],
                "physical_total": len(physical),
                "in_store": sum(1 for row in physical if not row.get("custody") and row.get("status") == "available"),
                "out": sum(1 for row in physical if row.get("custody")),
                "attention": sum(1 for row in physical if not row.get("custody") and row.get("status") == "attention"),
            }
        out.append(view)
    return out


@router.get("/set-types/{set_type_id}")
def get_set_type(set_type_id: str, account=Depends(tw._session)):
    set_type = _type_or_404(set_type_id)
    return {"set_type": _type_view(set_type), "lines": _type_lines(set_type_id)}


@router.post("/set-types", status_code=201)
def create_set_type(body: SetTypeCreate, account=Depends(tw._operator)):
    """Enter a set's contents once: title plus lines in one call."""
    set_type = tw._insert(
        "set_types",
        {
            "id": str(uuid.uuid4()),
            "title": body.title.strip(),
            "make_model": (body.make_model or "").strip() or None,
            "category": (body.category or "").strip() or None,
            "competency_required": body.competency_required,
            "notes": (body.notes or "").strip() or None,
            "active": True,
            "created_by": account["name"],
            "created_at": tw._now(),
            "updated_at": tw._now(),
            "row_version": 1,
        },
    )
    for position, line in enumerate(body.lines, start=1):
        tw._insert(
            "set_type_lines",
            {
                "id": str(uuid.uuid4()),
                "set_type_id": set_type["id"],
                "line_no": position,
                "name": line.name.strip(),
                "size": (line.size or "").strip() or None,
                "qty": line.qty,
                "notes": (line.notes or "").strip() or None,
                "created_at": tw._now(),
            },
        )
    return {"set_type": _type_view(set_type), "lines": _type_lines(set_type["id"])}


@router.patch("/set-types/{set_type_id}")
def update_set_type(set_type_id: str, body: SetTypeUpdate, account=Depends(tw._operator)):
    """Edit a type. Changes future sets only; made sets keep their own pieces."""
    set_type = _type_or_404(set_type_id)
    values = {key: value for key, value in body.model_dump(exclude_unset=True).items()}
    if "title" in values:
        values["title"] = values["title"].strip()
    for key in ("make_model", "category", "notes"):
        if key in values:
            values[key] = (values[key] or "").strip() or None
    values["updated_at"] = tw._now()
    values["row_version"] = int(set_type.get("row_version") or 1) + 1
    updated = tw._update("set_types", set_type_id, values)
    return {"set_type": _type_view(updated), "lines": _type_lines(set_type_id)}


@router.post("/set-types/{set_type_id}/lines", status_code=201)
def add_set_type_line(set_type_id: str, body: SetTypeLineInput, account=Depends(tw._operator)):
    _type_or_404(set_type_id)
    existing = _type_lines(set_type_id)
    tw._insert(
        "set_type_lines",
        {
            "id": str(uuid.uuid4()),
            "set_type_id": set_type_id,
            "line_no": len(existing) + 1,
            "name": body.name.strip(),
            "size": (body.size or "").strip() or None,
            "qty": body.qty,
            "notes": (body.notes or "").strip() or None,
            "created_at": tw._now(),
        },
    )
    return {"lines": _renumber_type_lines(set_type_id)}


@router.patch("/set-type-lines/{line_id}")
def edit_set_type_line(line_id: str, body: SetTypeLineUpdate, account=Depends(tw._operator)):
    line = tw._find("set_type_lines", "id", line_id)
    if not line:
        raise HTTPException(404, "That content line was not found.")
    values = {key: value for key, value in body.model_dump(exclude_unset=True).items() if key != "line_no"}
    if "name" in values:
        values["name"] = values["name"].strip()
    for key in ("size", "notes"):
        if key in values:
            values[key] = (values[key] or "").strip() or None
    if values:
        tw._update("set_type_lines", line_id, values)
    if body.line_no is not None:
        siblings = [row for row in _type_lines(line["set_type_id"]) if row["id"] != line_id]
        position = max(1, min(body.line_no, len(siblings) + 1))
        siblings.insert(position - 1, {**line, **values})
        for index, row in enumerate(siblings, start=1):
            tw._update("set_type_lines", row["id"], {"line_no": index})
    return {"lines": _renumber_type_lines(line["set_type_id"])}


@router.delete("/set-type-lines/{line_id}")
def remove_set_type_line(line_id: str, account=Depends(tw._operator)):
    line = tw._find("set_type_lines", "id", line_id)
    if not line:
        raise HTTPException(404, "That content line was not found.")
    if tw._find("set_lines", "set_type_line_id", line_id):
        raise HTTPException(409, "Sets were already made from this line. It stays for those sets.")
    tw._delete_where("set_type_lines", "id", line_id)
    return {"lines": _renumber_type_lines(line["set_type_id"])}


@router.post("/set-types/{set_type_id}/make-in-store", status_code=201)
def make_sets_in_store(set_type_id: str, body: MakeInStoreInput, account=Depends(tw._operator)):
    """Make physical sets with their pieces, available in store."""
    set_type = _type_or_404(set_type_id)
    department = _managed_department(account, body.department)
    type_lines = _type_lines(set_type_id)
    if not type_lines:
        raise HTTPException(422, "Add content lines to the set type first.")
    made = [_make_physical_set(set_type, type_lines, department, account["name"]) for _ in range(body.count)]
    return {"sets": made}


def _free_store_set(set_type_id: str, department: str) -> Optional[dict[str, Any]]:
    candidates = [
        row
        for row in _physical_sets(set_type_id)
        if row.get("department") == department
        and not row.get("archived")
        and not row.get("custody")
        and row.get("status") == "available"
    ]
    return sorted(candidates, key=lambda row: row.get("register_number") or "")[0] if candidates else None


def _issue_one_set(
    set_type: dict[str, Any],
    head: dict[str, Any],
    employee: dict[str, Any],
    body: IssueSetsInput,
    department: str,
    actor: str,
    requested_id: str = "",
) -> dict[str, Any]:
    """Issue one physical set to one person. Raises HTTPException on refusal."""
    movement = tw.MovementInput(
        kind="issue",
        location=body.location,
        expected_return_at=body.expected_return_at,
        override_due_checks=body.override_due_checks,
        override_reason=body.override_reason,
    )
    tw._check_loan_row(head, employee, movement, head.get("register_number"))
    pieces = _set_pieces(head["id"])
    by_line = {str(line.get("line_no")): line for line in _type_lines(set_type["id"])}
    wanted = (body.conditions or {}).get(requested_id or employee["id"], {})
    for line_no, condition in wanted.items():
        if condition not in PIECE_CONDITIONS:
            raise HTTPException(422, f"Record each piece as {', '.join(PIECE_CONDITIONS)}.")
        matches = [piece for piece in pieces if str((by_line.get(str(piece.get("set_type_line_id"))) or {}).get("line_no") or piece.get("line_no")) == str(line_no)]
        if not matches:
            raise HTTPException(422, f"Line {line_no} is not part of this set.")
        for piece in matches:
            tw._update("set_lines", piece["id"], {"condition": condition, "updated_at": tw._now()})
    now = tw._now()
    old = head.get("custody") or {}
    custody = {
        **old,
        "employee_id": employee["id"],
        "employee_number": employee.get("employee_number"),
        "employee_name": employee["name"],
        "department": employee["department"],
        "location": body.location.strip(),
        "job_reference": body.job_reference or old.get("job_reference"),
        "assigned_equipment": body.assigned_equipment or old.get("assigned_equipment", []),
        "expected_return_at": body.expected_return_at or old.get("expected_return_at"),
        "issued_at": now,
        "original_due_at": old.get("original_due_at") or body.expected_return_at,
        "issued_by": actor,
        "notes": body.notes,
        "pre_use_pending": head.get("pre_use_check_required", True),
    }
    after = {**head, "status": "issued", "storage_location": body.location.strip(), "custody": custody, "notes": body.notes}
    detail = f"Set {head.get('register_number')} issued to {employee['name']} ({len(pieces)} pieces)"
    applied = tw._apply_change(head, after, "issue", actor, detail, employee, None)
    return applied["tool"]


@router.post("/set-types/{set_type_id}/issue", status_code=201)
def issue_sets_to_people(set_type_id: str, body: IssueSetsInput, account=Depends(tw._operator)):
    """Issue a set to several people: a free set from store, or a new one,
    issued to each person in turn. Partial success is reported per person."""
    set_type = _type_or_404(set_type_id)
    if not set_type.get("active", True):
        raise HTTPException(409, "That set type is retired. Reactivate it to issue more sets.")
    department = _managed_department(account, body.department)
    type_lines = _type_lines(set_type_id)
    if not type_lines:
        raise HTTPException(422, "Add content lines to the set type first.")
    request_key = (body.idempotency_key or "").strip() or f"issue:{set_type_id}:{uuid.uuid4()}"
    replayed = _idem_result(request_key)
    if isinstance(replayed, dict):
        return {**replayed, "replayed": True}
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for employee_id in body.employee_ids:
        if employee_id in seen:
            continue
        seen.add(employee_id)
        employee = tw._find("employees", "id", employee_id) or tw._find("employees", "employee_number", employee_id)
        if not employee:
            results.append({"employee_id": employee_id, "employee_name": None, "ok": False, "reason": "is not on the Tools register"})
            continue
        if not employee.get("active", True):
            results.append({"employee_id": employee_id, "employee_name": employee.get("name"), "ok": False, "reason": "inactive"})
            continue
        assigned = (account.get("department") or "").strip() or employee.get("department")
        if employee.get("department") != assigned:
            results.append({"employee_id": employee_id, "employee_name": employee.get("name"), "ok": False, "reason": f"works in {employee.get('department')}, not {assigned}"})
            continue
        try:
            head = _free_store_set(set_type_id, employee.get("department") or department) or _make_physical_set(
                set_type, type_lines, employee.get("department") or department, account["name"]
            )
            if set_type.get("competency_required") and not tw._is_eligible(employee, head):
                raise HTTPException(409, f"{employee.get('name')} is not currently approved for {set_type.get('title')}.")
            issued = _issue_one_set(set_type, head, employee, body, department, account["name"], requested_id=employee_id)
            results.append(
                {
                    "employee_id": employee_id,
                    "employee_name": employee.get("name"),
                    "ok": True,
                    "set_id": issued["id"],
                    "register_number": issued.get("register_number"),
                    "piece_count": len(_set_pieces(issued["id"])),
                }
            )
        except HTTPException as exc:
            results.append({"employee_id": employee_id, "employee_name": employee.get("name"), "ok": False, "reason": str(exc.detail)})
    response = {
        "set_type_id": set_type_id,
        "results": results,
        "issued": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "idempotency_key": request_key,
    }
    _idem_store(request_key, response)
    return {**response, "replayed": False}


def _piece_or_404(piece_id: str) -> dict[str, Any]:
    piece = tw._find("set_lines", "id", piece_id)
    if not piece:
        raise HTTPException(404, "That piece was not found.")
    return piece


def _head_or_404(tool_id: str) -> dict[str, Any]:
    head = tw._find("tools", "id", tool_id)
    if not head:
        raise HTTPException(404, "Tool was not found.")
    if not head.get("set_type_id"):
        raise HTTPException(422, "That tool is not a set made from a set type.")
    return head


@router.patch("/sets/pieces/{piece_id}")
def edit_piece(piece_id: str, body: PieceUpdateInput, account=Depends(tw._operator)):
    """Piece-level edits keep a history entry on the parent set."""
    piece = _piece_or_404(piece_id)
    head = _head_or_404(str(piece.get("set_tool_id")))
    tw._ensure_managed_department(account, head.get("department"))
    values: dict[str, Any] = {}
    if body.condition is not None:
        if body.condition not in PIECE_CONDITIONS:
            raise HTTPException(422, f"Record each piece as {', '.join(PIECE_CONDITIONS)}.")
        values["condition"] = body.condition
    if body.status is not None:
        values["status"] = body.status
    if body.notes is not None:
        values["notes"] = body.notes.strip() or None
    if not values:
        return {"piece": piece}
    values["updated_at"] = tw._now()
    updated = tw._update("set_lines", piece_id, values)
    detail = f"Piece {piece.get('piece_number')} {', '.join(f'{key} set to {value}' for key, value in values.items() if key != 'updated_at')}"
    tw._apply_change(head, head, "piece_updated", account["name"], detail)
    return {"piece": updated}


@router.post("/sets/{tool_id}/return")
def return_set(tool_id: str, body: SetReturnInput, account=Depends(tw._operator)):
    """Receive a set with per-piece condition and status. A missing or
    damaged piece marks that piece and puts the set in Needs attention."""
    head = _head_or_404(tool_id)
    tw._ensure_managed_department(account, head.get("department"))
    if not head.get("custody"):
        raise HTTPException(409, "This set has no open loan.")
    idem_key = (body.idempotency_key or "").strip()
    stored = _idem_result(idem_key) if idem_key else None
    if isinstance(stored, dict):
        return {**stored, "replayed": True}
    pieces = _set_pieces(tool_id)
    by_id = {piece["id"]: piece for piece in pieces}
    for piece_id, condition in (body.piece_conditions or {}).items():
        if piece_id not in by_id:
            raise HTTPException(422, "Every piece entry must belong to this set.")
        if condition not in PIECE_CONDITIONS:
            raise HTTPException(422, f"Record each piece as {', '.join(PIECE_CONDITIONS)}.")
    for piece_id, status in (body.piece_status or {}).items():
        if piece_id not in by_id:
            raise HTTPException(422, "Every piece entry must belong to this set.")
        if status not in PIECE_STATUSES:
            raise HTTPException(422, "Record each piece as present, missing or damaged.")
    changed: list[str] = []
    for piece in pieces:
        values: dict[str, Any] = {}
        condition = (body.piece_conditions or {}).get(piece["id"], body.condition)
        status = (body.piece_status or {}).get(piece["id"], "present")
        if condition != (piece.get("condition") or "Good"):
            values["condition"] = condition
        if status != (piece.get("status") or "present"):
            values["status"] = status
        if values:
            values["updated_at"] = tw._now()
            tw._update("set_lines", piece["id"], values)
            changed.append(f"{piece.get('piece_number')} {status}/{condition}")
    refreshed = _set_pieces(tool_id)
    unfit = [piece for piece in refreshed if piece.get("status") != "present" or (piece.get("condition") or "Good") != "Good"]
    after = {
        **head,
        "status": "attention" if unfit else "available",
        "condition": tw._worst_condition([(piece.get("condition") or "Good") for piece in refreshed]) if refreshed else body.condition,
        "storage_location": body.location.strip(),
        "notes": body.notes,
        "custody": None,
    }
    detail = f"Set returned to {body.location.strip()} ({len(refreshed)} pieces)"
    if changed:
        detail += f": {', '.join(changed[:8])}"
    applied = tw._apply_change(head, after, "return", account["name"], detail, None, (body.idempotency_key or "").strip() or None)
    response = {"tool": applied["tool"], "pieces": _set_pieces(tool_id)}
    if idem_key:
        _idem_store(idem_key, response)
    return response

