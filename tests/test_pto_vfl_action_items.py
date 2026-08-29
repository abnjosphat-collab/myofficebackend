# tests/test_pto_vfl_action_items.py — pto.py's update_pto_report and vfl.py's
# update_vfl_report each looped over `updated.actionPlan`/`updated.actions` calling
# `.get(...)` on each item as if it were a dict. Both fields are typed as a list of a
# real Pydantic model (ActionPlanItemUpdate / ActionItemUpdate), not dicts — BaseModel
# has no `.get()`, so this raised AttributeError on every PATCH that included one,
# 500-erroring the whole request. Found while investigating pyright's error breakdown
# (this shape is exactly what reportAttributeAccessIssue was flagging), not a
# hypothetical — confirmed live, then fixed by switching to attribute access. Neither
# router had a test file before this; this locks in the fields these endpoints
# actually read so a future refactor can't quietly reintroduce dict-style access.

from app.routers.pto import ActionPlanItemUpdate
from app.routers.vfl import ActionItemUpdate


def test_action_plan_item_update_fields_are_attributes_not_dict_keys():
    item = ActionPlanItemUpdate(action="Fix guard rail", byWhom="J. Moyo", byWhen="2026-09-01",
                                 status="In Progress", completedDate=None, remarks="")
    # The exact accessors pto.py's update_pto_report now uses.
    assert item.action == "Fix guard rail"
    assert item.byWhom == "J. Moyo"
    assert item.byWhen == "2026-09-01"
    assert item.status == "In Progress"
    assert item.completedDate is None
    assert item.remarks == ""
    assert not hasattr(item, "get")  # BaseModel has no dict-style .get()


def test_action_item_update_fields_are_attributes_not_dict_keys():
    item = ActionItemUpdate(action="Replace filter", responsible="T. Ncube", targetDate="2026-09-15",
                             status=None, completedDate=None, remarks=None)
    # The exact accessors vfl.py's update_vfl_report now uses.
    assert item.action == "Replace filter"
    assert item.responsible == "T. Ncube"
    assert item.targetDate == "2026-09-15"
    assert item.status is None  # falls back to "Pending" via `action.status or "Pending"`
    assert item.remarks is None  # falls back to "" via `action.remarks or ""`
    assert not hasattr(item, "get")
