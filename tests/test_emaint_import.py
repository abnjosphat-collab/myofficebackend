# tests/test_emaint_import.py — M7: the eMaint import has hand-checked answers.
# The assumed eMaint columns live in tests/fixtures/emaint_assets.csv and
# emaint_pm.csv; every expected status below was worked out by hand from the
# fixture rows and the documented match rules (exact code/name = exists,
# case-only drift = close/held, meters and odd rhythms = invalid).
import csv
import io
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.routers.emaint_import as ei_mod
from app.emaint_import import (
    ASSET_FIELDS,
    SCHEDULE_FIELDS,
    build_asset_insert,
    build_schedule_insert,
    derive_recurrence,
    extract_asset,
    extract_schedule,
    find_machine,
    flag_asset_duplicates,
    flag_schedule_duplicates,
    match_asset,
    match_headers,
    parse_cost,
    parse_date,
)
from app.routers.emaint_import import (
    ImportRows,
    commit_assets,
    commit_schedules,
    infer_asset_columns,
    infer_schedule_columns,
    preview_assets,
    preview_schedules,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_rows(name):
    with open(FIXTURES / name, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(r) for r in reader]


def _full_mapping(fields, headers):
    mapping, _ = match_headers(headers, fields)
    assert all(v is not None for v in mapping.values()), mapping
    return mapping


REG = [
    {"id": 7, "equipment_id": "CRS-1", "name": "Crusher 1", "section": None},
    {"id": 9, "equipment_id": "MILL-3", "name": "Mill 3", "section": None},
    {"id": 12, "equipment_id": "CMP-2", "name": "Compressor No 2", "section": None},
]


# ─── headers ─────────────────────────────────────────────────────────────────

def test_fixture_headers_match_the_template_exactly():
    headers, _ = _fixture_rows("emaint_assets.csv")
    mapping, confidence = match_headers(headers, ASSET_FIELDS)
    assert all(v is not None for v in mapping.values())
    assert set(confidence.values()) == {1.0}
    headers, _ = _fixture_rows("emaint_pm.csv")
    mapping, confidence = match_headers(headers, SCHEDULE_FIELDS)
    assert all(v is not None for v in mapping.values())
    assert set(confidence.values()) == {1.0}


def test_aliases_match_with_lower_confidence():
    mapping, confidence = match_headers(["Code", "Name", "SerialNo", "Install Date"], ASSET_FIELDS)
    assert mapping["asset_id"] == "Code" and confidence["asset_id"] == 0.8
    assert mapping["name"] == "Name" and confidence["name"] == 0.8
    assert mapping["serial_number"] == "SerialNo"
    assert mapping["commission_date"] == "Install Date"
    assert mapping["model"] is None and confidence["model"] == 0.0


def test_header_matching_ignores_case_spacing_and_underscores():
    mapping, _ = match_headers(["assetid", "ASSET  DESCRIPTION", "serial_number"], ASSET_FIELDS)
    assert mapping["asset_id"] == "assetid"
    assert mapping["name"] == "ASSET  DESCRIPTION"
    assert mapping["serial_number"] == "serial_number"


# ─── asset extraction ────────────────────────────────────────────────────────

def test_extract_asset_full_row_hand_checked():
    _, rows = _fixture_rows("emaint_assets.csv")
    headers, _ = _fixture_rows("emaint_assets.csv")
    values, errors = extract_asset(rows[1], _full_mapping(ASSET_FIELDS, headers))
    assert errors == []
    assert values["asset_id"] == "CRS-9" and values["name"] == "New Conveyor"
    assert values["category"] == "Conveyors" and values["status"] == "operational"
    assert values["commission_date"] == "2023-06-15" and values["purchase_cost"] == 45000.0
    assert values["held"] == {"manufacturer": "BeltCo", "criticality": "Medium"}
    assert values["notes"] == "Feed conveyor" and values["serial_number"] == "SN-BELT-2"


def test_extract_asset_status_words_and_blank_default():
    def status_of(word):
        values, errors = extract_asset({"Status": word, "Asset Description": "X"},
                                       {"name": "Asset Description", "status": "Status"})
        return values["status"], errors

    assert status_of("Active") == ("operational", [])
    assert status_of("Under Maintenance") == ("maintenance", [])
    assert status_of("Retired") == ("out_of_service", [])
    assert status_of("Standby") == ("reserved", [])
    assert status_of("") == ("operational", [])
    assert status_of(None) == ("operational", [])
    _, errors = extract_asset({"Status": "Sold", "Asset Description": "X"},
                              {"name": "Asset Description", "status": "Status"})
    assert errors == ["Status 'Sold' is not one of: Active, Under Maintenance, Out of Service, Retired, Standby"]


def test_extract_asset_rejects_bad_name_criticality_date_and_cost():
    values, errors = extract_asset(
        {"Asset Description": "", "Criticality": "Extreme", "Commission Date": "32/13/2026",
         "Purchase Cost": "-5", "Status": "Active"},
        {"name": "Asset Description", "criticality": "Criticality", "commission_date": "Commission Date",
         "purchase_cost": "Purchase Cost", "status": "Status"})
    assert errors == ["No Asset Description (the register name is required)",
                      "Criticality 'Extreme' is not High, Medium or Low",
                      "Commission Date '32/13/2026' is not a date (use YYYY-MM-DD or D/M/YYYY)",
                      "'-5' is not a cost (a non-negative number)"]
    assert values["status"] == "operational"


def test_parse_date_accepts_iso_dmy_and_excel_datetimes():
    from datetime import datetime

    assert parse_date("2026-11-06") == (date(2026, 11, 6), None)
    assert parse_date("2026-11-06 00:00:00") == (date(2026, 11, 6), None)
    assert parse_date("6/11/2026") == (date(2026, 11, 6), None)
    assert parse_date("06-11-2026") == (date(2026, 11, 6), None)
    assert parse_date(datetime(2026, 11, 6, 8, 30)) == (date(2026, 11, 6), None)
    assert parse_date("") == (None, None) and parse_date(None) == (None, None)
    assert parse_date("next Friday")[1] == "'next Friday' is not a date (use YYYY-MM-DD or D/M/YYYY)"


def test_parse_cost_accepts_plain_and_formatted_amounts():
    assert parse_cost("120000") == (120000.0, None)
    assert parse_cost("R1,234.50") == (1234.5, None)
    assert parse_cost(45000) == (45000.0, None)
    assert parse_cost("") == (None, None)
    assert parse_cost("lots")[1] == "'lots' is not a cost (a non-negative number)"
    assert parse_cost("-5")[1] == "'-5' is not a cost (a non-negative number)"


# ─── schedule extraction ─────────────────────────────────────────────────────

def test_extract_schedule_full_row_hand_checked():
    _, rows = _fixture_rows("emaint_pm.csv")
    headers, _ = _fixture_rows("emaint_pm.csv")
    values, errors = extract_schedule(rows[0], _full_mapping(SCHEDULE_FIELDS, headers))
    assert errors == []
    assert values["title"] == "Weekly liner check" and values["frequency"] == "weekly"
    assert values["asset_id"] == "CRS-1" and values["asset_name"] == "Crusher 1"
    assert values["priority"] == "high" and values["active"] is True
    assert values["next_due"] == "2026-11-06"
    # 2026-11-06 is a Friday: the rhythm keeps the file's weekday and day.
    assert values["recurrence"] == {"recurrence_type": "weekly", "recurrence_dow": 5,
                                    "recurrence_dom": 6, "recurrence_months": []}
    assert values["ref"] == {"pm_id": "PM-101", "asset_id": "CRS-1", "last_done": "2026-10-30"}


def test_extract_schedule_priority_and_active_words():
    def row_of(priority, active):
        values, errors = extract_schedule(
            {"PM Description": "T", "Frequency": "Weekly", "Asset ID": "A",
             "Priority": priority, "Active": active},
            {"title": "PM Description", "frequency": "Frequency", "asset_id": "Asset ID",
             "priority": "Priority", "active": "Active"})
        return values, errors

    assert row_of("Critical", "No")[0]["priority"] == "urgent"
    assert row_of("Critical", "No")[0]["active"] is False
    assert row_of("", "")[0]["priority"] == "medium"
    assert row_of("", "")[0]["active"] is True
    _, errors = row_of("Top", "Maybe")
    assert errors == ["Priority 'Top' is not one of: Low, Medium, High, Urgent",
                      "Active 'Maybe' is not Yes or No"]


def test_extract_schedule_rejects_unsupported_rhythms_by_name():
    def freq_error(word):
        _, errors = extract_schedule({"PM Description": "T", "Frequency": word, "Asset ID": "A"},
                                     {"title": "PM Description", "frequency": "Frequency",
                                      "asset_id": "Asset ID"})
        return errors

    assert freq_error("Semi-Annual") == [
        "Frequency 'Semi-Annual' is not one of: Daily, Weekly, Biweekly, Monthly, Quarterly, Annual"]
    assert freq_error("") == [
        "Frequency 'blank' is not one of: Daily, Weekly, Biweekly, Monthly, Quarterly, Annual"]
    assert freq_error("Every 250 hours") == [
        "Frequency 'Every 250 hours' is meter-based (running hours or distance); "
        "meter PMs are not imported — set them up as hour-meter services instead"]
    _, errors = extract_schedule({"PM Description": "", "Frequency": "Weekly"},
                                 {"title": "PM Description", "frequency": "Frequency"})
    assert errors == ["No PM Description (the schedule title is required)",
                      "No Asset ID or Asset Description (the machine is required)"]


def test_derive_recurrence_anchors_on_next_due():
    assert derive_recurrence("monthly", "2026-11-18") == {
        "recurrence_type": "monthly", "recurrence_dow": 3, "recurrence_dom": 18, "recurrence_months": []}
    assert derive_recurrence("yearly", "2026-11-18")["recurrence_months"] == [10]
    assert derive_recurrence("quarterly", "") == {
        "recurrence_type": "quarterly", "recurrence_dow": 1, "recurrence_dom": 1,
        "recurrence_months": [0, 3, 6, 9]}
    assert derive_recurrence("daily", "")["recurrence_type"] == "daily"


# ─── matching ────────────────────────────────────────────────────────────────

def test_match_asset_exists_close_and_new():
    status, hit, note = match_asset({"asset_id": "CRS-1", "name": "Renamed Crusher"}, REG)
    assert status == "exists" and hit["id"] == 7 and "CRS-1" in note
    status, hit, note = match_asset({"asset_id": "", "name": "Mill 3"}, REG)
    assert status == "exists" and hit["id"] == 9
    status, hit, note = match_asset({"asset_id": "OTHER", "name": "Mill 3"}, REG)
    assert status == "exists" and "differs from the file's OTHER" in note
    status, hit, note = match_asset({"asset_id": "", "name": "mill 3"}, REG)
    assert status == "close" and hit["id"] == 9 and "closely matches" in note
    status, hit, note = match_asset({"asset_id": "NEW-1", "name": "New Pump"}, REG)
    assert status == "new" and hit is None and note == ""


def test_find_machine_is_exact_with_a_close_hint():
    machine, hint = find_machine("MILL-3", "Wrong Name", REG)
    assert machine["id"] == 9 and hint is None  # the code is the key
    machine, hint = find_machine("", "Mill 3", REG)
    assert machine["id"] == 9 and hint is None
    machine, hint = find_machine("", "mill 3", REG)
    assert machine is None and hint["id"] == 9  # hinted, never matched
    machine, hint = find_machine("NOPE-1", "Unknown Machine", REG)
    assert machine is None and hint is None


def test_flag_asset_duplicates_marks_repeats_as_invalid():
    previews = [{"row": 2, "values": {"asset_id": "A1", "name": "P1"}, "errors": []},
                {"row": 3, "values": {"asset_id": "A1", "name": "P2"}, "errors": []},
                {"row": 4, "values": {"asset_id": "", "name": "P1"}, "errors": []},
                {"row": 5, "values": {"asset_id": "", "name": ""}, "errors": ["No name"]}]
    flag_asset_duplicates(previews)
    assert "status" not in previews[0]
    assert (previews[1]["status"], previews[1]["errors"]) == (
        "invalid", ["Same Asset ID as row 2 in this file (imported once)"])
    assert "status" not in previews[3] and previews[3]["errors"] == ["No name"]


def test_flag_schedule_duplicates_marks_repeats_as_invalid():
    previews = [{"row": 2, "values": {"asset_id": "A1", "asset_name": "", "title": "Check"}, "errors": []},
                {"row": 3, "values": {"asset_id": "a1", "asset_name": "", "title": " check "}, "errors": []}]
    flag_schedule_duplicates(previews)
    assert "status" not in previews[0]
    assert (previews[1]["status"], previews[1]["errors"]) == (
        "invalid", ["Same machine and title as row 2 in this file (imported once)"])


def test_build_asset_insert_writes_live_columns_and_holds_the_rest():
    values = {"asset_id": "", "name": "N", "status": "", "category": "", "model": "M",
              "serial_number": "", "location": "", "department": "", "supplier": "",
              "warranty_info": "", "notes": "", "commission_date": "", "purchase_cost": None,
              "held": {"manufacturer": "Acme", "criticality": "High"}}
    assert build_asset_insert(values, set()) == {"name": "N", "status": "operational", "model": "M"}
    got = build_asset_insert({**values, "asset_id": "A1"}, {"manufacturer"})
    assert got["equipment_id"] == "A1" and got["manufacturer"] == "Acme" and "criticality" not in got


def test_build_schedule_insert_links_only_once_the_m1_columns_exist():
    values = {"title": "T", "department": "Eng", "allocated_to": "A", "estimated_hours": "2",
              "instructions": "Do it", "priority": "high", "active": True, "next_due": "2026-11-06",
              "recurrence": {"recurrence_type": "weekly", "recurrence_dow": 5,
                             "recurrence_dom": 6, "recurrence_months": []}}
    pre = build_schedule_insert(values, {"id": 7, "name": "Crusher 1"}, False)
    assert pre["equipment_info"] == "Crusher 1" and "equipment_id" not in pre
    assert "classification" not in pre and pre["next_due_date"] == "2026-11-06"
    post = build_schedule_insert({**values, "next_due": ""}, {"id": 7, "name": "Crusher 1"}, True)
    assert post["equipment_id"] == 7 and post["classification"] == "planned_maintenance"
    assert "next_due_date" not in post and post["job_request_details"] == "T"


# ─── endpoints ───────────────────────────────────────────────────────────────

class _FakeUploadFile:
    def __init__(self, filename, content):
        self.filename = filename
        self._content = content
        self._offset = 0

    async def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, state, calls, failures):
        self.table_name = table_name
        self.state = state
        self.calls = calls
        self.failures = failures
        self.filters = []
        self._insert = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def execute(self):
        if self._insert is not None:
            self.calls.append({"table": self.table_name, "insert": dict(self._insert)})
            if self.table_name in self.failures:
                raise self.failures[self.table_name]
            saved = dict(self._insert)
            saved.setdefault("id", 1000 + len(self.calls))
            self.state.setdefault(self.table_name, []).append(saved)
            return _Resp([saved])
        self.calls.append({"table": self.table_name, "filters": list(self.filters)})
        if f"{self.table_name}:read" in self.failures:
            raise self.failures[f"{self.table_name}:read"]
        rows = list(self.state.get(self.table_name, []))
        for col, val in self.filters:
            rows = [r for r in rows if r.get(col) == val]
        return _Resp(rows)


class _FakeSupabase:
    def __init__(self, state, calls, failures=None):
        self.state = state
        self.calls = calls
        self.failures = failures or {}

    def table(self, name):
        return _FakeQuery(name, self.state, self.calls, self.failures)


@pytest.fixture
def patch_env(monkeypatch):
    calls = []
    state = {}

    async def _no_invalidate(namespace):
        calls.append({"invalidated": namespace})

    def _patch(equipment=None, schedules=None, failures=None, held=False, links=False):
        state["equipment"] = [dict(r) for r in (equipment or [])]
        state["maintenance_schedules"] = [dict(r) for r in (schedules or [])]
        monkeypatch.setattr(ei_mod, "supabase", _FakeSupabase(state, calls, failures))

        def _has_columns(client, table, cols):
            if table == "equipment":
                return held
            return links

        monkeypatch.setattr(ei_mod, "has_columns", _has_columns)
        monkeypatch.setattr(ei_mod, "invalidate_namespace", _no_invalidate)
        return calls

    return _patch


def _infer_rows(name):
    """Fixture rows as the infer endpoint would hand them over (with _row)."""
    headers, rows = _fixture_rows(name)
    for lineno, row in enumerate(rows, start=2):
        row["_row"] = lineno
    return headers, rows


async def test_infer_reads_the_asset_csv_fixture():
    content = (FIXTURES / "emaint_assets.csv").read_bytes()
    got = await infer_asset_columns(_FakeUploadFile("assets.csv", content), current_user={"user_id": "u1"})
    assert got["total_rows"] == 7
    assert got["inferred"]["asset_id"] == "Asset ID" and got["inferred"]["name"] == "Asset Description"
    assert got["confidence"]["name"] == 1.0
    assert [r["_row"] for r in got["raw_rows"]] == [2, 3, 4, 5, 6, 7, 8]
    assert got["raw_rows"][0]["Asset ID"] == "CRS-1"


async def test_infer_reads_the_pm_csv_fixture():
    content = (FIXTURES / "emaint_pm.csv").read_bytes()
    got = await infer_schedule_columns(_FakeUploadFile("pm.csv", content), current_user={"user_id": "u1"})
    assert got["total_rows"] == 6
    assert got["inferred"]["frequency"] == "Frequency" and got["inferred"]["title"] == "PM Description"


async def test_infer_skips_blank_lines_but_keeps_true_row_numbers():
    content = b"\xef\xbb\xbfAsset ID,Asset Description\nA-1,Pump 1\n\n   \nA-2,Pump 2\n"
    got = await infer_asset_columns(_FakeUploadFile("bom.csv", content), current_user={"user_id": "u1"})
    assert got["total_rows"] == 2  # the BOM is swallowed, the blank lines skipped
    assert [r["_row"] for r in got["raw_rows"]] == [2, 5]
    assert got["all_columns"] == ["Asset ID", "Asset Description"]


async def test_infer_reads_xlsx_with_excel_dates():
    import openpyxl as _oxl

    book = _oxl.Workbook()
    sheet = book.active
    sheet.append(["Asset ID", "Asset Description", "Commission Date"])
    sheet.append(["A-1", "Pump 1", date(2023, 6, 15)])
    sheet.append([None, None, None])
    sheet.append(["A-2", "Pump 2", "not a date"])
    buf = io.BytesIO()
    book.save(buf)
    got = await infer_asset_columns(_FakeUploadFile("assets.xlsx", buf.getvalue()), current_user={"user_id": "u1"})
    assert got["total_rows"] == 2
    assert got["raw_rows"][0]["Commission Date"] == "2023-06-15"
    assert [r["_row"] for r in got["raw_rows"]] == [2, 4]


async def test_infer_refuses_garbage_empty_and_wrong_types():
    with pytest.raises(HTTPException) as exc:
        await infer_asset_columns(_FakeUploadFile("assets.xlsx", b"not a workbook"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await infer_asset_columns(_FakeUploadFile("headers.csv", b"Asset ID,Asset Description\n"),
                                  current_user={"user_id": "u1"})
    assert exc.value.status_code == 400 and "no data rows" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await infer_asset_columns(_FakeUploadFile("setup.exe", b"x"), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400


async def test_infer_refuses_files_above_the_row_limit():
    body = "Asset ID,Asset Description\n" + "".join(f"A-{i},Pump {i}\n" for i in range(2001))
    with pytest.raises(HTTPException) as exc:
        await infer_asset_columns(_FakeUploadFile("huge.csv", body.encode()), current_user={"user_id": "u1"})
    assert exc.value.status_code == 400 and "2000-row" in exc.value.detail


async def test_assets_preview_matches_the_whole_fixture_by_hand(patch_env):
    patch_env(equipment=list(REG[:2]))  # CMP-2 deliberately absent: row 8 must come out new
    headers, rows = _infer_rows("emaint_assets.csv")
    got = await preview_assets(ImportRows(rows=rows, mapping=_full_mapping(ASSET_FIELDS, headers)),
                               current_user={"user_id": "u1"})
    assert [(r["row"], r["status"]) for r in got["rows"]] == [
        (2, "exists"),   # CRS-1/Crusher 1: the code is on the register
        (3, "new"),      # CRS-9/New Conveyor: genuinely new
        (4, "exists"),   # MILL-3/Mill 3: exact name
        (5, "close"),    # "mill 3": case drift — held, never auto-matched
        (6, "invalid"),  # no name at all
        (7, "invalid"),  # Status "Sold" is not a register status
        (8, "new"),      # CMP-2 is not in this register
    ]
    assert got["summary"] == {"new": 2, "exists": 2, "close": 1, "invalid": 2}
    assert got["held_columns"] == [] and got["register_size"] == 2
    assert got["rows"][3]["match"]["name"] == "Mill 3"
    assert got["rows"][0]["values"]["held"] == {"manufacturer": "Acme", "criticality": "High"}


async def test_assets_preview_reports_held_columns_once_they_exist(patch_env):
    patch_env(equipment=list(REG), held=True)
    headers, rows = _infer_rows("emaint_assets.csv")
    got = await preview_assets(ImportRows(rows=rows[:1], mapping=_full_mapping(ASSET_FIELDS, headers)),
                               current_user={"user_id": "u1"})
    assert got["held_columns"] == ["manufacturer", "criticality"]


async def test_assets_preview_needs_its_columns_and_rows(patch_env):
    patch_env(equipment=list(REG))
    with pytest.raises(HTTPException) as exc:
        await preview_assets(ImportRows(rows=[{"_row": 2}], mapping={"name": None}),
                             current_user={"user_id": "u1"})
    assert exc.value.status_code == 422 and "Asset Description" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await preview_assets(ImportRows(rows=[], mapping={"name": "Asset Description"}),
                             current_user={"user_id": "u1"})
    assert exc.value.status_code == 422


async def test_assets_preview_scopes_the_register(patch_env):
    calls = patch_env(equipment=[{**REG[0], "section": "Projects"}, {**REG[1], "section": "Mining"}])
    headers, rows = _infer_rows("emaint_assets.csv")
    got = await preview_assets(ImportRows(rows=rows[:1], mapping=_full_mapping(ASSET_FIELDS, headers)),
                               current_user={"user_id": "u2", "scope": "Projects"})
    assert got["rows"][0]["status"] == "exists"  # Crusher 1 is in-scope
    assert ("section", "Projects") in [tuple(f) for c in calls if c.get("table") == "equipment"
                                       for f in c.get("filters", [])]


async def test_assets_commit_creates_only_what_is_new(patch_env):
    calls = patch_env(equipment=list(REG[:2]))
    headers, rows = _infer_rows("emaint_assets.csv")
    got = await commit_assets(ImportRows(rows=rows, mapping=_full_mapping(ASSET_FIELDS, headers)),
                              current_user={"user_id": "u1"})
    assert (got["created"], got["skipped"], got["errors"], got["total"]) == (2, 5, 0, 7)
    assert [(r["row"], r["status"]) for r in got["rows"]] == [
        (2, "exists"), (3, "created"), (4, "exists"), (5, "close"),
        (6, "invalid"), (7, "invalid"), (8, "created")]
    inserts = [c["insert"] for c in calls if "insert" in c]
    assert [(i["equipment_id"], i["name"]) for i in inserts] == [
        ("CRS-9", "New Conveyor"), ("CMP-2", "Compressor No 2")]
    assert "manufacturer" not in inserts[0]  # held: the column does not exist
    assert {"invalidated": "equipment"} in calls


async def test_assets_commit_generates_a_code_and_stamps_the_section(patch_env):
    calls = patch_env(equipment=[])
    rows = [{"_row": 2, "Asset Description": "Codeless Pump", "Status": "Active"}]
    got = await commit_assets(ImportRows(rows=rows, mapping={"name": "Asset Description", "status": "Status"}),
                              current_user={"user_id": "u2", "scope": "Projects"})
    assert got["created"] == 1
    insert = next(c["insert"] for c in calls if "insert" in c)
    assert insert["equipment_id"].startswith("EQ-") and insert["section"] == "Projects"


async def test_assets_commit_keeps_going_past_a_failed_row(patch_env):
    patch_env(equipment=[], failures={"equipment": RuntimeError("boom")})
    rows = [{"_row": 2, "Asset Description": "Pump 1"}, {"_row": 3, "Asset Description": "Pump 2"}]
    got = await commit_assets(ImportRows(rows=rows, mapping={"name": "Asset Description"}),
                              current_user={"user_id": "u1"})
    assert (got["created"], got["errors"]) == (0, 2)
    assert all(r["status"] == "error" for r in got["rows"])


async def test_schedules_preview_matches_the_whole_fixture_by_hand(patch_env):
    existing = [{"id": 11, "name": "Quarterly service", "equipment_info": "Crusher 1", "active": True}]
    patch_env(equipment=list(REG), schedules=existing)
    headers, rows = _infer_rows("emaint_pm.csv")
    got = await preview_schedules(ImportRows(rows=rows, mapping=_full_mapping(SCHEDULE_FIELDS, headers)),
                                  current_user={"user_id": "u1"})
    assert [(r["row"], r["status"]) for r in got["rows"]] == [
        (2, "new"),      # PM-101: Crusher 1 + Weekly liner check
        (3, "exists"),   # PM-102: already scheduled for Crusher 1
        (4, "invalid"),  # PM-103: NOPE-1 names no register machine
        (5, "invalid"),  # PM-104: Semi-Annual is not a rhythm here
        (6, "new"),      # PM-105: Mill 3 + Monthly vibration reading
        (7, "new"),      # PM-106: created paused (Active No)
    ]
    assert got["summary"] == {"new": 3, "exists": 1, "close": 0, "invalid": 2}
    assert "NOPE-1" in got["rows"][2]["errors"][0]
    assert got["rows"][4]["values"]["recurrence"]["recurrence_dom"] == 18
    assert got["rows"][5]["values"]["active"] is False


async def test_schedules_preview_hints_the_near_miss_machine(patch_env):
    patch_env(equipment=list(REG), schedules=[])
    rows = [{"_row": 2, "Asset Description": "mill 3", "PM Description": "Check",
             "Frequency": "Weekly"}]
    got = await preview_schedules(
        ImportRows(rows=rows, mapping={"asset_name": "Asset Description", "title": "PM Description",
                                       "frequency": "Frequency"}),
        current_user={"user_id": "u1"})
    assert got["rows"][0]["status"] == "invalid"
    assert "closest: 'Mill 3' [MILL-3]" in got["rows"][0]["errors"][0]


async def test_schedules_preview_needs_machine_title_and_rhythm(patch_env):
    patch_env(equipment=list(REG))
    rows = [{"_row": 2, "PM Description": "T", "Frequency": "Weekly"}]
    with pytest.raises(HTTPException) as exc:
        await preview_schedules(ImportRows(rows=rows, mapping={"title": "PM Description",
                                                               "frequency": "Frequency"}),
                                current_user={"user_id": "u1"})
    assert exc.value.status_code == 422 and "Asset ID or Asset Description" in exc.value.detail


async def test_schedules_commit_creates_linked_schedules(patch_env):
    existing = [{"id": 11, "name": "Quarterly service", "equipment_info": "Crusher 1", "active": True}]
    calls = patch_env(equipment=list(REG), schedules=existing, links=True)
    headers, rows = _infer_rows("emaint_pm.csv")
    got = await commit_schedules(ImportRows(rows=rows, mapping=_full_mapping(SCHEDULE_FIELDS, headers)),
                                 current_user={"user_id": "manager-1"})
    assert (got["created"], got["skipped"], got["errors"], got["total"]) == (3, 3, 0, 6)
    inserts = [c["insert"] for c in calls if "insert" in c]
    assert [(i["name"], i["equipment_info"], i["equipment_id"], i["created_by"]) for i in inserts] == [
        ("Weekly liner check", "Crusher 1", 7, "manager-1"),
        ("Monthly vibration reading", "Mill 3", 9, "manager-1"),
        ("Retired weekly check", "Compressor No 2", 12, "manager-1"),
    ]
    assert inserts[0]["classification"] == "planned_maintenance"
    assert inserts[0]["next_due_date"] == "2026-11-06" and inserts[2]["active"] is False


async def test_schedules_commit_pre_sql_writes_names_and_defaults_the_due_date(patch_env):
    calls = patch_env(equipment=list(REG), schedules=[], links=False)
    rows = [{"_row": 2, "Asset ID": "CRS-1", "PM Description": "Daily rounds", "Frequency": "Daily"}]
    got = await commit_schedules(
        ImportRows(rows=rows, mapping={"asset_id": "Asset ID", "title": "PM Description",
                                       "frequency": "Frequency"}),
        current_user={"user_id": "manager-1"})
    assert got["created"] == 1
    insert = next(c["insert"] for c in calls if "insert" in c)
    assert insert["equipment_info"] == "Crusher 1" and "equipment_id" not in insert
    assert "classification" not in insert
    from app.routers.schedules import next_occurrence
    from app.clock import today_harare

    assert insert["next_due_date"] == next_occurrence(insert, today_harare()).isoformat()
    assert insert["recurrence_type"] == "daily"


async def test_schedules_commit_stamps_the_section(patch_env):
    calls = patch_env(equipment=[{**REG[0], "section": "Projects"}], schedules=[])
    rows = [{"_row": 2, "Asset ID": "CRS-1", "PM Description": "Daily rounds", "Frequency": "Daily"}]
    got = await commit_schedules(
        ImportRows(rows=rows, mapping={"asset_id": "Asset ID", "title": "PM Description",
                                       "frequency": "Frequency"}),
        current_user={"user_id": "m2", "scope": "Projects"})
    assert got["created"] == 1
    assert next(c["insert"] for c in calls if "insert" in c)["section"] == "Projects"


async def test_preview_db_failure_is_500(patch_env):
    patch_env(equipment=[], failures={"equipment:read": RuntimeError("boom")})
    headers, rows = _infer_rows("emaint_assets.csv")
    with pytest.raises(HTTPException) as exc:
        await preview_assets(ImportRows(rows=rows[:1], mapping=_full_mapping(ASSET_FIELDS, headers)),
                             current_user={"user_id": "u1"})
    assert exc.value.status_code == 500
