# backend/app/routers/ai_safety.py
# AI-powered safety analysis: Polars aggregation + Claude insights
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
import logging
import json
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Lazy imports so the server boots even if packages are missing ──────────────

def _polars():
    import polars as pl
    return pl

def _anthropic_client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.Anthropic(api_key=key)

# ── Input model ────────────────────────────────────────────────────────────────

class SafetyDataInput(BaseModel):
    near_miss:    list[dict[str, Any]] = []
    work_stoppage: list[dict[str, Any]] = []
    vfl:          list[dict[str, Any]] = []
    pto:          list[dict[str, Any]] = []
    inspections:  list[dict[str, Any]] = []
    pachedu:      list[dict[str, Any]] = []
    period_label: Optional[str] = "last 90 days"

# ── Polars aggregation ─────────────────────────────────────────────────────────

def _agg(data: SafetyDataInput) -> dict[str, Any]:
    """
    Use Polars to extract patterns from raw safety data.
    Returns a concise summary dict safe to embed in a Claude prompt.
    """
    pl = _polars()
    agg: dict[str, Any] = {}

    # ── Near Miss ──
    if data.near_miss:
        df = pl.DataFrame(data.near_miss, infer_schema_length=500)
        agg["near_miss_total"] = len(df)
        for col in ("location", "priority", "severity", "department", "incident_type"):
            if col in df.columns:
                top = (df.group_by(col)
                        .agg(pl.len().alias("n"))
                        .sort("n", descending=True)
                        .head(5)
                        .to_dicts())
                agg[f"nm_by_{col}"] = top
        open_statuses = {"open", "under_investigation", "Open", "Under Investigation"}
        if "status" in df.columns:
            agg["nm_open"]   = df.filter(pl.col("status").is_in(list(open_statuses))).height
            agg["nm_closed"] = df.filter(~pl.col("status").is_in(list(open_statuses))).height

    # ── Work Stoppage ──
    if data.work_stoppage:
        df = pl.DataFrame(data.work_stoppage, infer_schema_length=500)
        agg["ws_total"] = len(df)
        for col in ("location", "section", "stoppageType"):
            if col in df.columns:
                top = (df.group_by(col).agg(pl.len().alias("n")).sort("n", descending=True).head(5).to_dicts())
                agg[f"ws_by_{col}"] = top
        # Total pending corrective actions
        pend = sum(
            sum(1 for a in (r.get("correctiveActions") or []) if (a.get("status") or "") == "Pending")
            for r in data.work_stoppage
        )
        agg["ws_pending_actions"] = pend

    # ── VFL ──
    if data.vfl:
        df = pl.DataFrame(data.vfl, infer_schema_length=500)
        agg["vfl_total"] = len(df)
        if "behaviourCategory" in df.columns:
            bc = df.group_by("behaviourCategory").agg(pl.len().alias("n")).sort("n", descending=True).to_dicts()
            agg["vfl_behaviour"] = bc
        if "location" in df.columns:
            agg["vfl_top_locations"] = (df.group_by("location").agg(pl.len().alias("n"))
                                          .sort("n", descending=True).head(5).to_dicts())

    # ── PTO ──
    if data.pto:
        df = pl.DataFrame(data.pto, infer_schema_length=500)
        agg["pto_total"] = len(df)
        if "location" in df.columns:
            agg["pto_top_locations"] = (df.group_by("location").agg(pl.len().alias("n"))
                                          .sort("n", descending=True).head(5).to_dicts())
        if "observationType" in df.columns:
            agg["pto_by_type"] = df.group_by("observationType").agg(pl.len().alias("n")).to_dicts()

    # ── SHEQ Inspections ──
    if data.inspections:
        df = pl.DataFrame(data.inspections, infer_schema_length=500)
        agg["insp_total"] = len(df)
        for col in ("place", "section", "department", "status"):
            if col in df.columns:
                top = df.group_by(col).agg(pl.len().alias("n")).sort("n", descending=True).head(5).to_dicts()
                agg[f"insp_by_{col}"] = top

        # Flatten findings
        findings = []
        for insp in data.inspections:
            place = insp.get("place") or ""
            for f in (insp.get("findings") or []):
                findings.append({**f, "_place": place})
        if findings:
            fdf = pl.DataFrame(findings, infer_schema_length=500)
            agg["findings_total"] = len(fdf)
            if "priority" in fdf.columns:
                agg["findings_by_priority"] = fdf.group_by("priority").agg(pl.len().alias("n")).to_dicts()
                crit = fdf.filter(pl.col("priority") == "critical")
                if crit.height > 0:
                    cols_needed = [c for c in ("finding", "requiredAction", "_place") if c in crit.columns]
                    agg["critical_findings_sample"] = crit.select(cols_needed).head(6).to_dicts()
            if "status" in fdf.columns:
                agg["findings_by_status"] = fdf.group_by("status").agg(pl.len().alias("n")).to_dicts()
            if "_place" in fdf.columns:
                agg["findings_top_locations"] = (fdf.group_by("_place").agg(pl.len().alias("n"))
                                                    .sort("n", descending=True).head(6).to_dicts())

    # ── Pachedu ──
    if data.pachedu:
        df = pl.DataFrame(data.pachedu, infer_schema_length=500)
        agg["pach_total"] = len(df)
        for col in ("location", "behaviourType", "dept", "sectionChoice"):
            if col in df.columns:
                top = df.group_by(col).agg(pl.len().alias("n")).sort("n", descending=True).head(5).to_dicts()
                agg[f"pach_by_{col}"] = top
        # Impacts
        impacts: list[str] = []
        for r in data.pachedu:
            impacts.extend(r.get("impacts") or [])
        if impacts:
            imp_df = pl.DataFrame({"impact": impacts})
            agg["pach_top_impacts"] = (imp_df.group_by("impact").agg(pl.len().alias("n"))
                                             .sort("n", descending=True).head(8).to_dicts())

    return agg

# ── Claude prompt & call ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert occupational health, safety and mining engineering consultant.
You analyze safety data from industrial operations and provide clear, actionable insights.
Your analysis is evidence-based, specific, and prioritized by risk severity.
Always respond with valid JSON only — no markdown fences, no preamble."""

def _build_prompt(agg: dict, period: str) -> str:
    return f"""
Analyze this safety data summary for an industrial operation (period: {period}).

DATA:
{json.dumps(agg, indent=2, default=str)}

Return ONLY a JSON object with exactly these keys:
{{
  "summary": "3-4 sentence executive summary of overall safety performance",
  "overall_risk": "low|medium|high|critical",
  "risk_score": <integer 0-100, higher = more risk>,
  "problem_areas": [
    {{
      "title": "concise problem title",
      "description": "specific description with counts/locations from the data",
      "severity": "low|medium|high|critical",
      "module": "which safety module this comes from",
      "count": <number of incidents/findings>,
      "location_or_dept": "specific location or department if identifiable"
    }}
  ],
  "trends": [
    {{
      "metric": "what is being tracked",
      "direction": "improving|worsening|stable",
      "insight": "what this means and why it matters"
    }}
  ],
  "recommendations": [
    {{
      "priority": "immediate|short_term|long_term",
      "action": "specific actionable step",
      "rationale": "why this is important based on the data",
      "owner": "who should own this (role/department)",
      "target": "measurable target or deadline"
    }}
  ],
  "top_risk_locations": ["list", "of", "locations"],
  "top_risk_departments": ["list", "of", "departments"],
  "generated_at": "{datetime.now(timezone.utc).isoformat()}"
}}

Rules:
- problem_areas: 3-6 items, most critical first
- recommendations: 4-8 items, sorted by priority (immediate first)
- Be specific — reference actual counts, locations, and module names from the data
- If data is sparse, say so in the summary and provide general best-practice recommendations
""".strip()

# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/ai/safety-analysis")
async def analyze_safety(data: SafetyDataInput):
    """
    Aggregate safety data with Polars, then use Claude to generate
    prioritised insights, problem areas, and recommendations.
    """
    total = (len(data.near_miss) + len(data.work_stoppage) + len(data.vfl) +
             len(data.pto) + len(data.inspections) + len(data.pachedu))
    if total == 0:
        return {
            "summary": "No safety data available for the selected period. Start logging incidents to enable AI analysis.",
            "overall_risk": "low",
            "risk_score": 0,
            "problem_areas": [],
            "trends": [],
            "recommendations": [
                {"priority": "immediate", "action": "Begin logging near miss incidents and SHEQ inspection findings",
                 "rationale": "Visibility is the first step to safety improvement", "owner": "SHEQ Manager", "target": "Week 1"}
            ],
            "top_risk_locations": [],
            "top_risk_departments": [],
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    try:
        # Polars aggregation
        agg = _agg(data)
    except Exception as e:
        logger.warning(f"Polars aggregation error (non-fatal): {e}")
        agg = {
            "near_miss_total":  len(data.near_miss),
            "ws_total":         len(data.work_stoppage),
            "vfl_total":        len(data.vfl),
            "pto_total":        len(data.pto),
            "insp_total":       len(data.inspections),
            "pach_total":       len(data.pachedu),
        }

    # Claude analysis
    try:
        client = _anthropic_client()
        prompt = _build_prompt(agg, data.period_label or "selected period")
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown code fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        result["_source"] = "claude-sonnet-4-6"
        result["_records_analysed"] = total
        return result
    except Exception as e:
        logger.error(f"Claude analysis error: {e}")
        raise HTTPException(500, f"AI analysis failed: {e}")
