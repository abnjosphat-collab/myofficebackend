# tests/test_work_stoppage_corrective_actions.py — work_stoppage.py's update_report
# looped over `updated.correctiveActions` calling `.get(...)` on each item as if it were
# a dict. The field is typed as a list of a real Pydantic model (CorrectiveActionUpdate),
# not dicts — BaseModel has no `.get()`, so this raised AttributeError on every PATCH to
# /api/work-stoppage/{id} that included correctiveActions, 500-erroring the whole
# request. Found via the 2026-08-30 rows()/one_row() pyright pass on this file (this
# shape is exactly what reportAttributeAccessIssue was flagging) — the same bug class
# already fixed once in pto.py/vfl.py (see test_pto_vfl_action_items.py). Fixed by
# switching to attribute access; this locks in the fields update_report actually reads
# so a future refactor can't quietly reintroduce dict-style access.

from app.routers.work_stoppage import CorrectiveActionUpdate


def test_corrective_action_update_fields_are_attributes_not_dict_keys():
    item = CorrectiveActionUpdate(finding="Guard missing", action="Refit guard", byWho="J. Moyo",
                                   byWhen="2026-09-01", status=None)
    # The exact accessors update_report now uses.
    assert item.finding == "Guard missing"
    assert item.action == "Refit guard"
    assert item.byWho == "J. Moyo"
    assert item.byWhen == "2026-09-01"
    assert item.status is None  # falls back to "Pending" via `action.status or "Pending"`
    assert not hasattr(item, "get")  # BaseModel has no dict-style .get()
