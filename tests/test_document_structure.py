# tests/test_document_structure.py — the AMS folder tree matches the structure Engineering Planning sent, and creating it is
# safe to repeat: what exists is skipped, what was made by hand is adopted with its files, nothing is created twice.
from app import document_structure as ds
from app.document_rules import MAX_FOLDER_DEPTH


def names_under(parent_key):
    return [n.name for n in ds.flatten() if n.parent_key == parent_key]


def node(name):
    return next(n for n in ds.flatten() if n.name == name)


# ─── the tree itself ───────────────────────────────────────────────────────────────────────────────────────────────

def test_the_root_and_nine_sections():
    nodes = ds.flatten()
    assert nodes[0].name == "Asset Management System (AMS)" and nodes[0].parent_key is None
    assert [n.name.split(".")[0] for n in nodes if n.depth == 1] == [str(i) for i in range(1, 10)]


def test_every_folder_in_the_email_is_present_under_the_right_parent():
    risk = node("3.1 Risk Management").key
    assert names_under(risk) == ["Asset Risk Register", "Criticality Analysis", "Hazard & Reliability Risk Assessments"]
    assert names_under(node("3. Planning").key) == [
        "3.1 Risk Management", "3.2 AM Objectives & KPIs", "3.3 Asset Management Plans (AMP)",
        "3.4 Budget, Forecast & Demand Planning", "3.5 Change Management (Engineering Change)"]
    assert names_under(node("4.4 Documented Information").key) == ["SOPs / SWPs", "Document Control", "Record Retention Schedule"]
    assert names_under(node("5.4 Lifecycle Delivery").key) == [
        "Acquisition & Commissioning", "Operation & Maintenance", "Refurbishment / Overhaul", "Disposal / Decommissioning"]
    assert names_under(node("8.1 Asset Classes").key) == [
        "Fixed Plant", "Mobile Equipment", "Underground Infrastructure", "Surface Infrastructure", "Services (Power, Water, Ventilation, Pumping)"]
    assert names_under(node("9. AMS Governance (Optional but Recommended)").key) == [
        "Governance Framework & Procedures", "Steering Committee Minutes", "AMS Risk & Opportunity Reviews"]


def test_the_lifecycle_records_group_by_asset_class():
    records = node("8.2 Asset Lifecycle Records").key
    assert names_under(records) == ["Fixed Plant", "Mobile Equipment", "Underground Infrastructure", "Services"]
    fixed = next(n for n in ds.flatten() if n.parent_key == records and n.name == "Fixed Plant")
    assert names_under(fixed.key) == ["Acquisition", "Operation & Maintenance", "Modification History", "Condition Reports", "Decommissioning"]


def test_folder_count_and_depth_are_within_limits():
    nodes = ds.flatten()
    assert len(nodes) == 85  # counted by hand from the email: 1 root + 5+4+11+13+9+6+3+29+4
    assert max(n.depth for n in nodes) + 1 <= MAX_FOLDER_DEPTH


def test_keys_are_unique_and_parents_come_first():
    nodes = ds.flatten()
    keys = [n.key for n in nodes]
    assert len(keys) == len(set(keys))
    seen = set()
    for n in nodes:
        assert n.parent_key is None or n.parent_key in seen
        seen.add(n.key)


def test_same_name_under_different_parents_gets_different_keys():
    fixed = [n for n in ds.flatten() if n.name == "Fixed Plant"]
    assert len(fixed) == 2 and fixed[0].key != fixed[1].key


# ─── matching names ────────────────────────────────────────────────────────────────────────────────────────────────

def test_normalise_ignores_numbering_case_ampersand_and_punctuation():
    assert ds.normalise("4.4 Documented Information") == ds.normalise("documented  information")
    assert ds.normalise("1.1 Internal & External Issues") == ds.normalise("Internal and External Issues")
    assert ds.normalise("3. Planning") == ds.normalise("Planning")
    assert ds.normalise("SOPs / SWPs") != ds.normalise("SOPs")


# ─── the plan ──────────────────────────────────────────────────────────────────────────────────────────────────────

def row(id, name, parent_id=None, seed_key=None):
    return {"id": id, "name": name, "parent_id": parent_id, "seed_key": seed_key}


def test_an_empty_library_creates_everything():
    steps = ds.plan_seed([])
    assert all(s.action == "create" for s in steps) and len(steps) == len(ds.flatten())


def test_running_it_twice_changes_nothing():
    first = ds.plan_seed([])
    made = [row(f"id-{i}", s.name, None, s.key) for i, s in enumerate(first)]
    by_key = {m["seed_key"]: m["id"] for m in made}
    for m, s in zip(made, first):
        m["parent_id"] = by_key.get(s.parent_key) if s.parent_key else None
    again = ds.plan_seed(made)
    assert {s.action for s in again} == {"skip"} and ds.summarise(again)["created"] == 0


def test_a_renamed_seeded_folder_is_still_the_same_folder():
    steps = ds.plan_seed([row("a", "Asset Management System", None, "ams")])
    assert steps[0].action == "skip" and steps[0].name == "Asset Management System"
    assert sum(1 for s in steps if s.key == "ams") == 1


def test_a_folder_made_by_hand_is_adopted_with_its_id():
    existing = [
        row("root", "Asset Management System (AMS)", None, "ams"),
        row("s4", "4. Support", "root", "ams/4-support"),
        row("hand", "Documented Information", "s4", None),
    ]
    steps = {s.key: s for s in ds.plan_seed(existing)}
    adopted = steps["ams/4-support/4-4-documented-information"]
    assert adopted.action == "adopt" and adopted.folder_id == "hand" and adopted.name == "4.4 Documented Information"


def test_a_hand_made_folder_in_another_place_is_left_alone():
    existing = [
        row("root", "Asset Management System (AMS)", None, "ams"),
        row("s1", "1. Organizational Context", "root", "ams/1-organizational-context"),
        row("sops", "SOPs", "s1", None),
    ]
    steps = ds.plan_seed(existing)
    assert not any(s.folder_id == "sops" for s in steps)


def test_one_hand_made_folder_is_adopted_once():
    existing = [
        row("root", "Asset Management System (AMS)", None, "ams"),
        row("s8", "8. Asset Portfolio (Mining-Specific)", "root", "ams/8-asset-portfolio-mining-specific"),
        row("s81", "8.1 Asset Classes", "s8", "ams/8-asset-portfolio-mining-specific/8-1-asset-classes"),
        row("fp", "Fixed Plant", "s81", None),
    ]
    steps = ds.plan_seed(existing)
    adopted = [s for s in steps if s.action == "adopt"]
    assert len(adopted) == 1 and adopted[0].folder_id == "fp"
    # the other "Fixed Plant" (under 8.2) is a different folder and is created
    assert any(s.action == "create" and s.name == "Fixed Plant" for s in steps)


def test_a_top_level_folder_with_the_root_name_is_adopted():
    steps = ds.plan_seed([row("old", "asset management system (ams)", None, None)])
    assert steps[0].action == "adopt" and steps[0].folder_id == "old" and steps[0].name == "Asset Management System (AMS)"


def test_children_of_a_new_parent_are_created_not_searched_for():
    steps = ds.plan_seed([row("x", "Asset Risk Register", None, None)])
    assert all(s.action == "create" for s in steps if s.name == "Asset Risk Register")


def test_summary_counts():
    counts = ds.summarise(ds.plan_seed([row("a", "x", None, "ams")]))
    assert counts == {"created": 84, "adopted": 0, "skipped": 1, "total": 85}


def test_section_keys_cover_one_to_nine():
    assert sorted(ds.section_keys()) == [str(i) for i in range(1, 10)]
