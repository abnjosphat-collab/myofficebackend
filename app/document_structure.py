# backend/app/document_structure.py — the Asset Management System (AMS) folder tree, and the plan for creating it.
#
# The tree is the structure Engineering Planning set out for the document library. It is data here, not code, so a
# change to the structure is a change to this list. `plan_seed` is a pure function: folders that exist in, steps out. It
# never touches the database, so the rule "create what is missing, leave what exists alone" is testable on its own and
# the router only has to carry the steps out.
#
# A folder is recognised by its `seed_key`, not its name, so a folder somebody renamed is still the same folder and is
# not created a second time. A folder that was made by hand before the structure existed (same name, with or without the
# section number) is adopted: it gets the key and the numbered name, and keeps every file in it.
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_NAME = "Asset Management System (AMS)"
ROOT_KEY = "ams"

Tree = Tuple[str, Sequence["Tree"]]


def _n(name: str, *children: Tree) -> Tree:
    return (name, children)


AMS_SECTIONS: List[Tree] = [
    _n("1. Organizational Context",
       _n("1.1 Internal & External Issues"),
       _n("1.2 Stakeholder Requirements"),
       _n("1.3 AMS Scope & Boundaries"),
       _n("1.4 Asset Hierarchy & Data Governance")),
    _n("2. Leadership",
       _n("2.1 Organizational Structure & Roles"),
       _n("2.2 Asset Management Policy"),
       _n("2.3 Responsibilities & Authorities (RACI)")),
    _n("3. Planning",
       _n("3.1 Risk Management",
          _n("Asset Risk Register"), _n("Criticality Analysis"), _n("Hazard & Reliability Risk Assessments")),
       _n("3.2 AM Objectives & KPIs"),
       _n("3.3 Asset Management Plans (AMP)", _n("Strategic AMP"), _n("Annual AMP")),
       _n("3.4 Budget, Forecast & Demand Planning"),
       _n("3.5 Change Management (Engineering Change)")),
    _n("4. Support",
       _n("4.1 Resources & Competence", _n("Training Records & Competency Matrix"), _n("Contractor Management")),
       _n("4.2 Communication & Awareness"),
       _n("4.3 Asset Information & Registers",
          _n("Asset Register"), _n("Drawings & Schematics"), _n("CMMS Data & Maintenance Strategies")),
       _n("4.4 Documented Information", _n("SOPs / SWPs"), _n("Document Control"), _n("Record Retention Schedule"))),
    _n("5. Operation",
       _n("5.1 Operational Control & Maintenance Execution"),
       _n("5.2 Management of Change (MOC)"),
       _n("5.3 Outsourcing (Contractor Management)"),
       _n("5.4 Lifecycle Delivery",
          _n("Acquisition & Commissioning"), _n("Operation & Maintenance"),
          _n("Refurbishment / Overhaul"), _n("Disposal / Decommissioning"))),
    _n("6. Performance Evaluation",
       _n("6.1 Monitoring, Measurement & Analysis",
          _n("Condition Monitoring Reports"), _n("Performance Dashboards / Reliability KPIs")),
       _n("6.2 Internal Audits"),
       _n("6.3 Management Review")),
    _n("7. Improvement",
       _n("7.1 Non-Conformities & Corrective Actions"),
       _n("7.2 Continual Improvement / Lessons Learned")),
    _n("8. Asset Portfolio (Mining-Specific)",
       _n("8.1 Asset Classes",
          _n("Fixed Plant"), _n("Mobile Equipment"), _n("Underground Infrastructure"), _n("Surface Infrastructure"),
          _n("Services (Power, Water, Ventilation, Pumping)")),
       _n("8.2 Asset Lifecycle Records",
          _n("Fixed Plant",
             _n("Acquisition"), _n("Operation & Maintenance"), _n("Modification History"),
             _n("Condition Reports"), _n("Decommissioning")),
          _n("Mobile Equipment",
             _n("Acquisition"), _n("Operation & Maintenance"), _n("Overhaul / Rebuild"), _n("Disposal")),
          _n("Underground Infrastructure",
             _n("Acquisition / Construction"), _n("Maintenance"), _n("Modifications"), _n("Condition Monitoring")),
          _n("Services",
             _n("Ventilation Systems"), _n("Pumping / Dewatering Systems"),
             _n("Electrical Power Distribution"), _n("Utilities Infrastructure")))),
    _n("9. AMS Governance (Optional but Recommended)",
       _n("Governance Framework & Procedures"), _n("Steering Committee Minutes"), _n("AMS Risk & Opportunity Reviews")),
]

# Folders are listed in the order above, spaced out so a folder made by hand can be slotted between two of them.
SORT_STEP = 10
# A folder made in the app starts after every seeded one.
CUSTOM_SORT_ORDER = 1000


@dataclass(frozen=True)
class Node:
    key: str
    parent_key: Optional[str]
    name: str
    sort_order: int
    depth: int


@dataclass(frozen=True)
class Step:
    """One thing the router does. `create` makes a folder, `adopt` renames and keys a folder that already exists,
    `skip` leaves one alone. `folder_id` is the existing folder for adopt and skip."""
    action: str
    key: str
    parent_key: Optional[str]
    name: str
    sort_order: int
    depth: int
    folder_id: Optional[str] = None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def normalise(name: str) -> str:
    """What two folder names must share to be the same folder: no section number, no case, "&" and "and" alike, no
    punctuation. "4.4 Documented Information" and "documented  information" are one folder."""
    text = re.sub(r"^\s*\d+(\.\d+)*\.?\s+", "", name or "")
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def flatten() -> List[Node]:
    """Every folder of the structure, parents before children, in the order they should be shown."""
    nodes: List[Node] = [Node(ROOT_KEY, None, ROOT_NAME, SORT_STEP, 0)]

    def walk(items: Sequence[Tree], parent_key: str, depth: int) -> None:
        for index, (name, children) in enumerate(items, start=1):
            key = f"{parent_key}/{slug(name)}"
            nodes.append(Node(key, parent_key, name, index * SORT_STEP, depth))
            walk(children, key, depth + 1)

    walk(AMS_SECTIONS, ROOT_KEY, 1)
    return nodes


def section_keys() -> Dict[str, str]:
    """The section number ("1" to "9") of each top-level folder, to its seed key."""
    return {node.name.split(".", 1)[0]: node.key for node in flatten() if node.depth == 1}


def plan_seed(existing: Sequence[Dict[str, object]]) -> List[Step]:
    """The steps that bring `existing` folders up to the AMS structure. Safe to run any number of times: run again
    straight after, every step is a skip.

    `existing` rows carry `id`, `parent_id`, `name` and `seed_key`.
    """
    by_key = {str(row["seed_key"]): row for row in existing if row.get("seed_key")}
    children: Dict[Optional[str], List[Dict[str, object]]] = {}
    for row in existing:
        parent = str(row["parent_id"]) if row.get("parent_id") else None
        children.setdefault(parent, []).append(row)

    claimed: set = set()
    resolved: Dict[str, Optional[str]] = {}  # seed key -> id of the existing folder, None when it is being created
    steps: List[Step] = []

    for node in flatten():
        row = by_key.get(node.key)
        if row is not None:
            resolved[node.key] = str(row["id"])
            steps.append(Step("skip", node.key, node.parent_key, str(row["name"]), node.sort_order, node.depth, str(row["id"])))
            continue

        parent_id = resolved.get(node.parent_key) if node.parent_key else None
        look_here = node.parent_key is None or parent_id is not None
        match = None
        if look_here:
            for candidate in children.get(parent_id, []):
                if candidate.get("seed_key") or str(candidate["id"]) in claimed:
                    continue
                if normalise(str(candidate["name"])) == normalise(node.name):
                    match = candidate
                    break
        if match is not None:
            claimed.add(str(match["id"]))
            resolved[node.key] = str(match["id"])
            steps.append(Step("adopt", node.key, node.parent_key, node.name, node.sort_order, node.depth, str(match["id"])))
        else:
            resolved[node.key] = None
            steps.append(Step("create", node.key, node.parent_key, node.name, node.sort_order, node.depth))
    return steps


def summarise(steps: Sequence[Step]) -> Dict[str, int]:
    counts = {"create": 0, "adopt": 0, "skip": 0}
    for step in steps:
        counts[step.action] += 1
    return {"created": counts["create"], "adopted": counts["adopt"], "skipped": counts["skip"], "total": len(steps)}
