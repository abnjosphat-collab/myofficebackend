# backend/app/routers/ai_safety.py
# SHEQ Safety Analysis — Polars-powered, no external AI API required
from fastapi import APIRouter, HTTPException, Depends
from app.auth import get_current_user, require_role
from app.aggregation import count_by
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

try:
    import polars as pl
    _POLARS = True
except ImportError:
    _POLARS = False
    logger.warning("polars not installed — falling back to pure-Python aggregation")


# ── Risk score weights ───────────────────────────────────────────────────────
# The 0-100 risk score is a weighted sum of 6 components, each independently
# capped so no single metric can dominate the total. These are a safety-policy
# choice (how much each signal should count), not a code-correctness question —
# extracted here so they're a named, documented, easy-to-find/tune block instead
# of magic numbers buried inline in the formula. Changing a weight still needs a
# code change + deploy; this doesn't make them runtime-configurable, just visible.
RISK_WEIGHTS = {
    # Near-miss reports still open, as a % of all near-misses this period.
    "near_miss_open_ratio":      {"weight": 0.20, "cap": 20},
    # Each open critical-priority SHEQ inspection finding.
    "critical_finding_points":   {"per_item": 5,  "cap": 20},
    # Each finding that's gone overdue.
    "overdue_finding_points":    {"per_item": 4,  "cap": 20},
    # Corrective/action-plan items still pending, as a % of all actions tracked.
    "pending_action_ratio":      {"weight": 0.15, "cap": 15},
    # Each logged work-stoppage incident this period.
    "work_stoppage_points":      {"per_item": 3,  "cap": 15},
    # VFL (visible-felt-leadership) observations flagged unsafe, as a % of total.
    "vfl_unsafe_ratio":          {"weight": 0.10, "cap": 10},
}
# Caps above sum to 100 by design — the score is always in [0, 100].


# ── Input schema ───────────────────────────────────────────────────────────────

class SafetyDataInput(BaseModel):
    near_miss:     list[dict[str, Any]] = []
    work_stoppage: list[dict[str, Any]] = []
    vfl:           list[dict[str, Any]] = []
    pto:           list[dict[str, Any]] = []
    inspections:   list[dict[str, Any]] = []
    pachedu:       list[dict[str, Any]] = []
    period_label:  Optional[str] = "selected period"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _top(records: list[dict], col: str, n: int = 5) -> list[dict]:
    """Return top-n values for a column, sorted by count desc. Pure Python fallback."""
    values = (str(r.get(col) or "").strip() for r in records)
    counts = count_by((v for v in values if v), lambda v: v)
    return [{"value": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])[:n]]


def _top_pl(records: list[dict], col: str, n: int = 5) -> list[dict]:
    """Polars version of _top — faster for large datasets."""
    if not records or not _POLARS:
        return _top(records, col, n)
    try:
        import polars as pl
        df = pl.DataFrame(records, infer_schema_length=500)
        if col not in df.columns:
            return []
        return (
            df.filter(pl.col(col).is_not_null() & (pl.col(col).cast(str).str.strip_chars() != ""))
              .group_by(col)
              .agg(pl.len().alias("count"))
              .sort("count", descending=True)
              .head(n)
              .rename({col: "value"})
              .to_dicts()
        )
    except Exception:
        return _top(records, col, n)


def _date(r: dict) -> Optional[datetime]:
    for f in ("date", "breakdown_date", "submittedAt", "created_at", "createdAt"):
        v = r.get(f)
        if v:
            try:
                d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return d.replace(tzinfo=None)
            except Exception:
                pass
    return None


def _split_halves(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split records by date into older half vs newer half for trend comparison."""
    dated = [(r, _date(r)) for r in records]
    dated = [(r, d) for r, d in dated if d]
    if not dated:
        return [], []
    dated.sort(key=lambda x: x[1])
    mid = len(dated) // 2
    return [r for r, _ in dated[:mid]], [r for r, _ in dated[mid:]]


def _trend_direction(older: int, newer: int) -> str:
    if older == 0 and newer == 0:
        return "stable"
    if older == 0:
        return "worsening"
    delta = (newer - older) / max(older, 1)
    if delta > 0.20:
        return "worsening"
    if delta < -0.20:
        return "improving"
    return "stable"


def _risk_level(score: int) -> str:
    if score >= 70: return "critical"
    if score >= 50: return "high"
    if score >= 30: return "medium"
    return "low"


# ── Core analysis ──────────────────────────────────────────────────────────────

def analyse(data: SafetyDataInput) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    nm   = data.near_miss
    ws   = data.work_stoppage
    vfl  = data.vfl
    pto  = data.pto
    insp = data.inspections
    pach = data.pachedu

    # ── Flatten findings ──────────────────────────────────────────────────────
    findings: list[dict] = []
    for i in insp:
        place = i.get("place") or i.get("location") or ""
        dept  = i.get("department") or ""
        for f in (i.get("findings") or []):
            findings.append({**f, "_place": place, "_dept": dept})

    # ── Counts ────────────────────────────────────────────────────────────────
    nm_total   = len(nm)
    ws_total   = len(ws)
    vfl_total  = len(vfl)
    pto_total  = len(pto)
    insp_total = len(insp)
    pach_total = len(pach)
    total      = nm_total + ws_total + vfl_total + pto_total + insp_total + pach_total

    nm_open    = sum(1 for r in nm if str(r.get("status","")).lower() in {"open","under_investigation"})
    nm_high    = sum(1 for r in nm if str(r.get("priority", r.get("severity",""))).lower() in {"high","critical"})
    vfl_unsafe = sum(1 for r in vfl if "unsafe" in str(r.get("behaviourCategory","")).lower())
    vfl_safe   = vfl_total - vfl_unsafe

    ws_actions   = [a for r in ws for a in (r.get("correctiveActions") or [])]
    vfl_actions  = [a for r in vfl for a in (r.get("actions") or [])]
    pto_actions  = [a for r in pto for a in (r.get("actionPlan") or [])]
    all_actions  = ws_actions + vfl_actions + pto_actions
    act_pending  = sum(1 for a in all_actions if str(a.get("status","")).lower() in {"pending", "not started"})
    act_done     = sum(1 for a in all_actions if str(a.get("status","")).lower() in {"completed","done","closed"})

    critical_findings = [f for f in findings if str(f.get("priority","")).lower() == "critical"]
    open_findings     = [f for f in findings if str(f.get("status","")).lower() in {"open","in-progress","overdue"}]
    overdue_findings  = [f for f in findings if str(f.get("status","")).lower() == "overdue"]
    pach_intentional  = sum(1 for r in pach if r.get("behaviourType") == "Intentional")

    # ── Risk score (0–100, higher = more risk) ────────────────────────────────
    # See RISK_WEIGHTS above for what each component measures and why it's
    # weighted/capped the way it is.
    W = RISK_WEIGHTS
    nm_risk   = min(int((nm_open / max(nm_total, 1)) * 100 * W["near_miss_open_ratio"]["weight"]), W["near_miss_open_ratio"]["cap"])
    crit_risk = min(len(critical_findings) * W["critical_finding_points"]["per_item"], W["critical_finding_points"]["cap"])
    over_risk = min(len(overdue_findings) * W["overdue_finding_points"]["per_item"], W["overdue_finding_points"]["cap"])
    act_risk  = min(int((act_pending / max(len(all_actions), 1)) * 100 * W["pending_action_ratio"]["weight"]), W["pending_action_ratio"]["cap"])
    ws_risk   = min(ws_total * W["work_stoppage_points"]["per_item"], W["work_stoppage_points"]["cap"])
    vfl_risk  = min(int((vfl_unsafe / max(vfl_total, 1)) * 100 * W["vfl_unsafe_ratio"]["weight"]), W["vfl_unsafe_ratio"]["cap"])
    risk_score = nm_risk + crit_risk + over_risk + act_risk + ws_risk + vfl_risk

    # ── Hotspot discovery ─────────────────────────────────────────────────────
    # Top locations across all modules
    all_records = nm + ws + vfl + pto + pach
    location_cols = ("location", "place", "area")

    def _first_location(r: dict) -> str:
        for col in location_cols:
            v = str(r.get(col) or "").strip()
            if v and v.lower() not in {"", "n/a", "na", "unknown"}:
                return v
        return ""

    location_values = [_first_location(r) for r in all_records] + [str(f.get("_place") or "").strip() for f in findings]
    top_locations = count_by((v for v in location_values if v), lambda v: v)
    sorted_locations = sorted(top_locations.items(), key=lambda x: -x[1])[:6]

    # Top departments
    dept_values = (str(r.get("department") or r.get("dept") or r.get("_dept") or "").strip() for r in all_records + findings)
    dept_counts = count_by((v for v in dept_values if v and v.lower() not in {"", "n/a"}), lambda v: v)
    sorted_depts = sorted(dept_counts.items(), key=lambda x: -x[1])[:5]

    # Top NM locations
    nm_locs  = _top_pl(nm,       "location",   5)
    ws_locs  = _top_pl(ws,       "location",   5)
    fnd_locs = _top_pl(findings, "_place",     5)
    pach_locs = _top_pl(pach,    "location",  5)

    # ── Trends ────────────────────────────────────────────────────────────────
    nm_old,   nm_new   = _split_halves(nm)
    vfl_old,  vfl_new  = _split_halves(vfl)
    pto_old,  pto_new  = _split_halves(pto)
    insp_old, insp_new = _split_halves(insp)
    pach_old, pach_new = _split_halves(pach)

    # Higher incidents = worsening for NM/WS; higher for VFL/Inspections = improving
    nm_dir   = _trend_direction(len(nm_old),   len(nm_new))
    vfl_dir  = "improving" if _trend_direction(len(vfl_old), len(vfl_new)) == "worsening" else "stable" if len(vfl_new) == len(vfl_old) else "improving"
    insp_dir = "improving" if len(insp_new) >= len(insp_old) else "worsening"
    pach_dir = "improving" if len(pach_new) >= len(pach_old) else "worsening"

    vfl_old_unsafe = sum(1 for r in vfl_old if "unsafe" in str(r.get("behaviourCategory","")).lower())
    vfl_new_unsafe = sum(1 for r in vfl_new if "unsafe" in str(r.get("behaviourCategory","")).lower())
    vfl_beh_dir    = _trend_direction(vfl_old_unsafe, vfl_new_unsafe)

    old_crit = sum(1 for f in findings if str(f.get("priority","")).lower() == "critical"
                   and _date({**f, "date": f.get("date","")}) and
                   (_date({**f, "date": f.get("date","")}) or now) < now - timedelta(days=30))
    new_crit = len(critical_findings) - old_crit

    trends = []
    if nm_total > 0:
        trends.append({
            "metric": "Near Miss Reporting",
            "direction": nm_dir,
            "insight": f"{nm_new.__len__()} reports in second half vs {nm_old.__len__()} in first half. "
                       + ("Increased reporting may indicate improved safety culture or worsening conditions — review individually." if nm_dir == "worsening"
                          else "Reporting stable or decreasing.")
        })
    if vfl_total > 0:
        trends.append({
            "metric": "VFL Observation Activity",
            "direction": vfl_dir,
            "insight": f"{vfl_total} total observations: {vfl_safe} safe ({round(vfl_safe/max(vfl_total,1)*100)}%), {vfl_unsafe} unsafe. "
                       + ("Unsafe behaviour rate requires attention." if vfl_unsafe > vfl_safe else "Safe behaviour predominates — positive indicator.")
        })
    if len(findings) > 0:
        trends.append({
            "metric": "Inspection Findings",
            "direction": "worsening" if len(critical_findings) > 2 else "stable" if len(open_findings) < 3 else "worsening",
            "insight": f"{len(findings)} total findings: {len(critical_findings)} critical, {len(open_findings)} open, {len(overdue_findings)} overdue."
        })
    if insp_total > 0:
        trends.append({
            "metric": "Inspection Activity",
            "direction": insp_dir,
            "insight": f"{insp_total} inspections recorded. {'Activity increasing — good compliance trend.' if insp_dir == 'improving' else 'Inspection rate decreasing — schedule more inspections.'}"
        })
    if pach_total > 0:
        trends.append({
            "metric": "Pachedu Observations",
            "direction": pach_dir,
            "insight": f"{pach_total} observations: {pach_intentional} intentional, {pach_total - pach_intentional} unintentional. "
                       + ("Observation rate increasing — good engagement." if pach_dir == "improving" else "Consider increasing Pachedu observation frequency.")
        })

    # ── Problem areas ─────────────────────────────────────────────────────────
    problem_areas = []

    # Critical findings
    if critical_findings:
        locs = list({f.get("_place","") for f in critical_findings if f.get("_place")})
        problem_areas.append({
            "title": "Critical Inspection Findings Unresolved",
            "description": f"{len(critical_findings)} critical finding(s) require immediate corrective action."
                           + (f" Affected locations: {', '.join(locs[:3])}." if locs else ""),
            "severity": "critical",
            "module": "SHEQ Inspections",
            "count": len(critical_findings),
            "location_or_dept": ", ".join(locs[:2]) if locs else ""
        })

    # Overdue actions
    if overdue_findings:
        problem_areas.append({
            "title": "Overdue Corrective Actions",
            "description": f"{len(overdue_findings)} corrective action(s) are past their due date and remain open.",
            "severity": "high",
            "module": "SHEQ Inspections",
            "count": len(overdue_findings),
            "location_or_dept": ""
        })

    # High-risk near misses
    if nm_high > 0 or nm_open > 2:
        problem_areas.append({
            "title": "Open / High-Priority Near Miss Reports",
            "description": f"{nm_open} near miss report(s) open, {nm_high} classified as high/critical priority."
                           + (f" Top location: {nm_locs[0]['value']} ({nm_locs[0]['count']} incidents)." if nm_locs else ""),
            "severity": "high" if nm_high > 0 else "medium",
            "module": "Near Miss",
            "count": nm_open,
            "location_or_dept": nm_locs[0]["value"] if nm_locs else ""
        })

    # Work stoppages
    if ws_total > 0:
        problem_areas.append({
            "title": "Work Stoppages Issued",
            "description": f"{ws_total} work stoppage(s) issued with {act_pending} corrective action(s) still pending."
                           + (f" Top location: {ws_locs[0]['value']}." if ws_locs else ""),
            "severity": "high" if ws_total > 3 else "medium",
            "module": "Work Stoppage",
            "count": ws_total,
            "location_or_dept": ws_locs[0]["value"] if ws_locs else ""
        })

    # Unsafe VFL behaviour
    if vfl_unsafe > 0 and vfl_total > 0:
        unsafe_pct = round(vfl_unsafe / vfl_total * 100)
        if unsafe_pct > 30:
            problem_areas.append({
                "title": "High Unsafe Behaviour Rate in VFL",
                "description": f"{vfl_unsafe} of {vfl_total} VFL observations ({unsafe_pct}%) recorded as unsafe behaviour.",
                "severity": "high" if unsafe_pct > 50 else "medium",
                "module": "VFL",
                "count": vfl_unsafe,
                "location_or_dept": ""
            })

    # High-risk PTO
    pto_high_risk = sum(1 for r in pto if r.get("riskAssessment", {}) and
                        any(r["riskAssessment"].get(k) == "No" for k in ("made","identified","effective")))
    if pto_high_risk > 0:
        problem_areas.append({
            "title": "High-Risk PTO Observations",
            "description": f"{pto_high_risk} PTO observation(s) flagged as high risk (inadequate risk assessment).",
            "severity": "high",
            "module": "PTO",
            "count": pto_high_risk,
            "location_or_dept": ""
        })

    # Repeat hotspot (location with 3+ incidents)
    for loc, cnt in sorted_locations:
        if cnt >= 3 and loc:
            problem_areas.append({
                "title": f"Repeat Incident Hotspot: {loc}",
                "description": f"{cnt} incidents across modules recorded at {loc}. This location requires focused intervention.",
                "severity": "high" if cnt >= 5 else "medium",
                "module": "Multi-module",
                "count": cnt,
                "location_or_dept": loc
            })
            break  # Only report the worst one

    # Pachedu intentional behaviour
    if pach_intentional > 0 and pach_total > 0:
        int_pct = round(pach_intentional / pach_total * 100)
        if int_pct > 30:
            problem_areas.append({
                "title": "Intentional Unsafe Behaviour Observed",
                "description": f"{pach_intentional} of {pach_total} Pachedu observations ({int_pct}%) were intentional. Targeted awareness and disciplinary review recommended.",
                "severity": "high" if int_pct > 50 else "medium",
                "module": "Pachedu",
                "count": pach_intentional,
                "location_or_dept": pach_locs[0]["value"] if pach_locs else ""
            })

    # Sort: critical first
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    problem_areas.sort(key=lambda p: sev_order.get(p["severity"], 4))

    # ── Recommendations (rule-based, prioritised) ─────────────────────────────
    recommendations = []

    if critical_findings:
        recommendations.append({
            "priority": "immediate",
            "action": f"Close all {len(critical_findings)} critical inspection finding(s) — assign dedicated team",
            "rationale": "Critical findings present the highest risk of injury or regulatory non-compliance",
            "owner": "SHEQ Manager",
            "target": "Within 24–48 hours"
        })

    if overdue_findings:
        recommendations.append({
            "priority": "immediate",
            "action": f"Review and close {len(overdue_findings)} overdue corrective action(s)",
            "rationale": "Overdue actions indicate systemic follow-up failure and increase liability",
            "owner": "Section Supervisors",
            "target": "This week"
        })

    if nm_high > 0:
        recommendations.append({
            "priority": "immediate",
            "action": f"Conduct root-cause analysis on {nm_high} high/critical near miss report(s)",
            "rationale": "High-priority near misses are precursors to serious incidents if not resolved promptly",
            "owner": "SHEQ Officer",
            "target": "Within 48 hours"
        })

    if nm_open > 3:
        recommendations.append({
            "priority": "short_term",
            "action": f"Clear the {nm_open} open near miss reports — assign owners and set closure deadlines",
            "rationale": "Open reports left unresolved erode reporting culture and miss learning opportunities",
            "owner": "Department Heads",
            "target": "Next 5 working days"
        })

    if vfl_unsafe > vfl_safe and vfl_total > 0:
        recommendations.append({
            "priority": "short_term",
            "action": "Increase VFL coaching frequency and run targeted safety awareness sessions",
            "rationale": f"Unsafe behaviour ({vfl_unsafe}/{vfl_total} observations) exceeds safe rate — culture intervention required",
            "owner": "SHEQ & Supervisors",
            "target": "Next 2 weeks"
        })

    if act_pending > 5:
        recommendations.append({
            "priority": "short_term",
            "action": f"Implement weekly action-closure review meeting — {act_pending} corrective actions pending",
            "rationale": "High pending action count indicates weak accountability and follow-through",
            "owner": "Operations Manager",
            "target": "Start next Monday"
        })

    if sorted_locations and sorted_locations[0][1] >= 3:
        loc, cnt = sorted_locations[0]
        recommendations.append({
            "priority": "short_term",
            "action": f"Conduct targeted safety walkdown at '{loc}' — {cnt} incidents recorded there",
            "rationale": "Repeat incidents at the same location signal an unresolved environmental or procedural hazard",
            "owner": "Safety Officer + Section Engineer",
            "target": "Within 1 week"
        })

    if insp_total < 2:
        recommendations.append({
            "priority": "short_term",
            "action": "Increase SHEQ inspection frequency — fewer than 2 inspections recorded in period",
            "rationale": "Infrequent inspections allow hazards to persist undetected",
            "owner": "SHEQ Manager",
            "target": "Schedule weekly minimum"
        })

    if pto_total < 2 and total > 5:
        recommendations.append({
            "priority": "short_term",
            "action": "Increase PTO observation activity — less than 2 observations recorded",
            "rationale": "PTO observations are a leading indicator; low numbers suggest insufficient safety leadership visibility",
            "owner": "Senior Management",
            "target": "Minimum 4 per week"
        })

    if vfl_total < 2 and total > 5:
        recommendations.append({
            "priority": "short_term",
            "action": "Increase VFL observation rate — target at least 2 per week",
            "rationale": "VFL observations build safety culture and identify risks before they cause incidents",
            "owner": "Supervisors",
            "target": "Weekly target: 2+"
        })

    if pach_intentional / max(pach_total, 1) > 0.4:
        recommendations.append({
            "priority": "long_term",
            "action": "Implement behaviour-based safety programme focused on intentional unsafe acts",
            "rationale": f"Intentional unsafe behaviour at {round(pach_intentional/max(pach_total,1)*100)}% of Pachedu observations requires systemic culture change",
            "owner": "HR & SHEQ",
            "target": "Next 30 days — programme launch"
        })

    recommendations.append({
        "priority": "long_term",
        "action": "Establish monthly SHEQ performance review with all department heads",
        "rationale": "Regular cross-functional reviews drive accountability and surface systemic issues",
        "owner": "General Manager",
        "target": "Monthly recurring"
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    level = _risk_level(risk_score)
    if total == 0:
        summary = "No safety records found for the selected period. Begin logging incidents across all modules to enable meaningful analysis."
    else:
        parts = []
        if nm_open > 0 or nm_high > 0:
            parts.append(f"{nm_open} open near miss report(s)")
        if critical_findings:
            parts.append(f"{len(critical_findings)} critical inspection finding(s)")
        if overdue_findings:
            parts.append(f"{len(overdue_findings)} overdue action(s)")
        if ws_total > 0:
            parts.append(f"{ws_total} work stoppage(s)")
        concern_str = (", ".join(parts[:3]) + ".") if parts else "No immediate critical concerns identified."
        top_loc_str = f" Highest incident location: {sorted_locations[0][0]} ({sorted_locations[0][1]} incidents)." if sorted_locations else ""
        summary = (
            f"{total} total safety records analysed across {sum(1 for x in [nm,ws,vfl,pto,insp,pach] if x)} active modules. "
            f"Risk score: {risk_score}/100 ({level.upper()}).{top_loc_str} "
            f"Key concerns: {concern_str} "
            f"{len(recommendations)} recommendations generated."
        )

    return {
        "summary":              summary,
        "overall_risk":         level,
        "risk_score":           risk_score,
        "problem_areas":        problem_areas,
        "trends":               trends,
        "recommendations":      recommendations,
        "top_risk_locations":   [loc for loc, _ in sorted_locations],
        "top_risk_departments": [dept for dept, _ in sorted_depts],
        "_source":              "polars-analysis",
        "_records_analysed":    total,
        "generated_at":         now.isoformat(),
        # Extra detail for the frontend
        "_breakdown": {
            "nm_open":          nm_open,
            "nm_high":          nm_high,
            "critical_findings":len(critical_findings),
            "overdue_findings": len(overdue_findings),
            "act_pending":      act_pending,
            "act_done":         act_done,
            "vfl_unsafe_pct":   round(vfl_unsafe / max(vfl_total, 1) * 100),
            "ws_total":         ws_total,
        }
    }


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/ai/safety-analysis")
async def analyze_safety(data: SafetyDataInput, current_user: dict = Depends(get_current_user)):
    """
    Analyse SHEQ safety data using Polars aggregation and rule-based scoring.
    Returns risk score, hotspots, trends, and prioritised recommendations.
    No external API required.
    """
    try:
        return analyse(data)
    except Exception as e:
        logger.error(f"Safety analysis error: {e}")
        raise HTTPException(500, f"Analysis failed: {e}")
