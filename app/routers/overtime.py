from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from app.supabase_client import supabase
from app.auth import require_role, get_current_user
from app.db_helpers import get_or_404
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()

class OvertimeCreate(BaseModel):
    employee_name: str = Field(..., min_length=1)
    employee_id: str = Field(..., min_length=1)
    position: str = Field(..., min_length=1)
    # Was silently dropped on every save before this — the form has always sent it
    # (autofilled from the employee picker), but this model never declared the field,
    # so every existing record ended up with no department at all ("Unassigned" in the
    # By Section breakdown regardless of who actually worked it).
    department: Optional[str] = None
    overtime_type: str
    date: str
    # Reason/contact/exact times are no longer mandatory — a fast path for when someone is
    # pressed for time: either give start_time+end_time (hours computed from them) OR just
    # `hours` directly. validate_has_duration below requires one or the other.
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours: Optional[float] = Field(None, gt=0, le=24)
    reason: Optional[str] = None
    contact_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    # Reference/cost log only — same scope as breakdowns.spares_used and
    # work_orders.spares_used, neither of which touches Spares-module stock
    # (current_quantity stays a Stores-department function, per explicit product
    # decision). unit_price is whatever the Spares register listed at pick time.
    spares_used: Optional[List[Dict[str, Any]]] = None

    @validator('hours', always=True)
    def validate_has_duration(cls, v, values):
        if v is None and not (values.get('start_time') and values.get('end_time')):
            raise ValueError('Provide either start_time and end_time, or hours directly.')
        return v

class OvertimeUpdate(BaseModel):
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    overtime_type: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours: Optional[float] = Field(None, gt=0, le=24)
    reason: Optional[str] = None
    contact_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    spares_used: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None

# GET all overtime
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_overtime(status: Optional[str] = None, overtime_type: Optional[str] = None):
    try:
        logger.info("Fetching overtime data...")
        
        query = supabase.table("overtime").select("*")
        
        if status:
            query = query.eq("status", status)
        if overtime_type:
            query = query.eq("overtime_type", overtime_type)
            
        response = query.order("created_at", desc=True).execute()
        
        logger.info(f"Supabase response: {response}")
        
        if hasattr(response, 'data'):
            data = response.data
        else:
            data = response
            
        logger.info(f"Returning {len(data) if data else 0} records")
        for record in (data or []):
            if isinstance(record.get('spares_used'), str):
                try:
                    record['spares_used'] = json.loads(record['spares_used'])
                except (TypeError, ValueError):
                    record['spares_used'] = []
        return data or []
        
    except Exception as e:
        logger.error(f"Error fetching overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching overtime: {str(e)}")

# POST create overtime
@router.post("")
@router.post("/")
async def create_overtime(overtime: OvertimeCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating overtime for: {overtime.employee_name}")
        
        data_to_insert = {
            "employee_name": overtime.employee_name,
            "employee_id": overtime.employee_id,
            "position": overtime.position,
            "department": overtime.department,
            "overtime_type": overtime.overtime_type,
            "date": overtime.date,
            "start_time": overtime.start_time,
            "end_time": overtime.end_time,
            "hours": overtime.hours,
            "reason": overtime.reason,
            "contact_number": overtime.contact_number,
            "emergency_contact": overtime.emergency_contact,
            "spares_used": json.dumps(overtime.spares_used or []),
            "status": "pending",
            "applied_date": datetime.utcnow().isoformat()
        }

        logger.info(f"Inserting data: {data_to_insert}")

        response = supabase.table("overtime").insert(data_to_insert).execute()

        logger.info(f"Supabase insert response: {response}")

        if hasattr(response, 'data') and response.data:
            created_data = response.data[0]
            if isinstance(created_data.get('spares_used'), str):
                try:
                    created_data['spares_used'] = json.loads(created_data['spares_used'])
                except (TypeError, ValueError):
                    created_data['spares_used'] = []
            logger.info(f"Successfully created overtime with ID: {created_data.get('id')}")
            return created_data
        else:
            logger.error("No data returned from Supabase")
            raise HTTPException(status_code=500, detail="Failed to create overtime - no data returned")
            
    except Exception as e:
        logger.error(f"Error creating overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating overtime: {str(e)}")

# PATCH update overtime
@router.patch("/{overtime_id}")
async def update_overtime(overtime_id: int, updated: OvertimeUpdate, authorization: Optional[str] = Header(None), current_user: dict = Depends(get_current_user)):
    # Any edit requires a signed-in user (current_user); approve/reject additionally
    # requires manager+ (checked below against the same Authorization header).
    if updated.status in ('approved', 'rejected'):
        approver = await require_role('manager')(authorization)
        logger.info(f"Approval action '{updated.status}' by {approver['email']} (role: {approver['role']})")
    try:
        logger.info(f"Updating overtime {overtime_id}")
        
        # Check if exists
        get_or_404(supabase, "overtime", overtime_id, detail="Overtime not found")
        
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        if 'spares_used' in data_to_update:
            data_to_update['spares_used'] = json.dumps(data_to_update['spares_used'] or [])

        response = supabase.table("overtime").update(data_to_update).eq("id", overtime_id).execute()

        if hasattr(response, 'data') and response.data:
            result = response.data[0]
            if isinstance(result.get('spares_used'), str):
                try:
                    result['spares_used'] = json.loads(result['spares_used'])
                except (TypeError, ValueError):
                    result['spares_used'] = []
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating overtime: {str(e)}")

# DELETE overtime
@router.delete("/{overtime_id}")
async def delete_overtime(overtime_id: int, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Deleting overtime {overtime_id}")
        
        # Check if exists
        get_or_404(supabase, "overtime", overtime_id, detail="Overtime not found")
        
        supabase.table("overtime").delete().eq("id", overtime_id).execute()
        
        return {"success": True, "message": "Overtime deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting overtime: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting overtime: {str(e)}")


# ── Analyze — reads reason text + volume/trend, no external AI API required ────────
# Same "no external AI API" design as app/routers/ai_safety.py: polars aggregation
# with a pure-Python fallback, rule-based problem areas/recommendations. The frontend
# sends whatever it already has on screen (i.e. the currently filtered records), so
# analysis is automatically scoped to whatever dates/status/type/employee(s) are
# selected — this endpoint never queries the DB itself.

try:
    import polars as pl
    _POLARS = True
except ImportError:
    _POLARS = False
    logger.warning("polars not installed — overtime analysis falling back to pure-Python aggregation")

import re
from collections import Counter, defaultdict

_STOPWORDS = {
    'the', 'a', 'an', 'to', 'for', 'and', 'or', 'on', 'in', 'of', 'with', 'at', 'by',
    'from', 'was', 'were', 'is', 'are', 'this', 'that', 'be', 'been', 'as', 'due',
    'because', 'it', 'its', 'their', 'his', 'her', 'they', 'he', 'she', 'we', 'our',
    'not', 'no', 'yes', 'will', 'would', 'should', 'could', 'has', 'have', 'had',
    'per', 'into', 'out', 'up', 'down', 'over', 'after', 'before', 'during', 'so',
    'than', 'then', 'also', 'just', 'still', 'etc', 'work', 'overtime', 'ot',
}


def _hours(r: dict) -> float:
    h = r.get('hours')
    if h is not None:
        try:
            return float(h)
        except (TypeError, ValueError):
            pass
    start, end = r.get('start_time'), r.get('end_time')
    if not start or not end:
        return 0.0
    try:
        sh, sm = (int(x) for x in str(start).split(':')[:2])
        eh, em = (int(x) for x in str(end).split(':')[:2])
        mins = (eh * 60 + em) - (sh * 60 + sm)
        if mins < 0:
            mins += 24 * 60  # overnight shift
        return round(mins / 60, 2)
    except (ValueError, IndexError):
        return 0.0


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9']*", text.lower()) if w not in _STOPWORDS and len(w) > 2]


def _ngrams(tokens: list[str], n: int) -> list[str]:
    return [' '.join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# Known recurring-task categories that should always be reported as ONE combined
# entry regardless of whatever equipment/location happens to be named alongside them
# — e.g. "winder daily checks" and "southwell daily checks" are the same routine task
# ("Daily Checks"), not two separate causes. User-identified; extend this list as more
# such categories come up rather than letting them fragment across n-grams.
KNOWN_CATEGORIES: list[tuple[str, str]] = [
    ('daily check', 'Daily Checks'),
    ('shaft exam', 'Shaft Exam'),
    ('declutch', 'Declutching'),
    ('loco breakdown', 'Loco Breakdown'),
    ('attended loco', 'Loco Breakdown'),
]


def _categorize(reason: str) -> Optional[str]:
    low = reason.lower()
    for pattern, label in KNOWN_CATEGORIES:
        if pattern in low:
            return label
    return None


def _group_hours(records: list[dict], key_fn) -> dict:
    """Sum hours per group. Polars path for larger record sets, pure-Python fallback."""
    if _POLARS and len(records) > 20:
        try:
            rows = [{'_k': key_fn(r) or 'Unassigned', '_h': _hours(r)} for r in records]
            df = pl.DataFrame(rows)
            grouped = df.group_by('_k').agg(pl.col('_h').sum()).to_dicts()
            return {g['_k']: g['_h'] for g in grouped}
        except Exception:
            pass
    out: dict = defaultdict(float)
    for r in records:
        out[key_fn(r) or 'Unassigned'] += _hours(r)
    return dict(out)


def _date(r: dict):
    v = r.get('date')
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10])
    except ValueError:
        return None


WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _weekday(r: dict) -> Optional[int]:
    d = _date(r)
    return d.weekday() if d else None


def _start_hour(r: dict) -> Optional[int]:
    start = r.get('start_time')
    if not start:
        return None
    try:
        return int(str(start).split(':')[0]) % 24
    except (ValueError, IndexError):
        return None


def _split_halves(records: list[dict]):
    dated = [(r, _date(r)) for r in records]
    dated = [(r, d) for r, d in dated if d]
    if not dated:
        return [], []
    dated.sort(key=lambda x: x[1])
    mid = len(dated) // 2
    return [r for r, _ in dated[:mid]], [r for r, _ in dated[mid:]]


def _trend_direction(old_val: float, new_val: float) -> str:
    if old_val == 0 and new_val == 0:
        return 'stable'
    if old_val == 0:
        return 'worsening'
    delta = (new_val - old_val) / max(old_val, 1)
    if delta > 0.20:
        return 'worsening'
    if delta < -0.20:
        return 'improving'
    return 'stable'


def _iso_week_key(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _weekly_series(records: list[dict]) -> list[dict]:
    """Real week-by-week totals for the trend chart, not just an older/newer split."""
    weekly: dict = defaultdict(float)
    for r in records:
        d = _date(r)
        if d:
            weekly[_iso_week_key(d)] += _hours(r)
    return [{'week': k, 'hours': round(v, 1)} for k, v in sorted(weekly.items())]


def _equipment_names() -> list[str]:
    """Real equipment names from the register, lowercased — used so a reason-text
    mention can be reported as a CONFIRMED machine match, not just a frequent word.
    Best-effort: analysis still works (minus this one signal) if this fails."""
    try:
        r = supabase.table("equipment").select("name").limit(1000).execute()
        return sorted({str(row.get('name') or '').strip().lower() for row in (r.data or []) if len(str(row.get('name') or '').strip()) > 3})
    except Exception as e:
        logger.warning(f"Could not fetch equipment for cross-reference: {e}")
        return []


def _records_for_phrase(records: list[dict], phrase: str, known_labels: set[str]) -> list[dict]:
    """Every record that contributed to a given top-reasons phrase — the "drill down
    into the underlying records" set for that category. Mirrors exactly how the phrase
    was derived in the first place (a KNOWN_CATEGORIES label vs. a raw n-gram)."""
    out = []
    for r in records:
        reason = str(r.get('reason') or '')
        if phrase in known_labels:
            if _categorize(reason) == phrase:
                out.append(r)
        else:
            tokens = _tokenize(reason)
            if phrase in set(_ngrams(tokens, 2) + _ngrams(tokens, 3)):
                out.append(r)
    return out


class OvertimeAnalysisInput(BaseModel):
    records: List[Dict[str, Any]] = []
    period_label: Optional[str] = "selected period"


def _analyze_overtime(data: OvertimeAnalysisInput) -> dict:
    records = data.records
    total = len(records)
    total_hours = sum(_hours(r) for r in records)

    # ── Volume by employee / section / type ──────────────────────────────────
    emp_hours = _group_hours(records, lambda r: r.get('employee_name') or r.get('employee_id'))
    dept_hours = _group_hours(records, lambda r: r.get('department'))
    type_hours = _group_hours(records, lambda r: r.get('overtime_type'))

    top_employees = sorted(emp_hours.items(), key=lambda x: -x[1])[:5]
    top_depts = sorted(dept_hours.items(), key=lambda x: -x[1])[:5]

    double_time_hours = type_hours.get('weekend', 0) + type_hours.get('holiday', 0)
    double_time_pct = round(double_time_hours / total_hours * 100) if total_hours else 0

    # ── Machines mentioned — cross-referenced against the real Equipment register, so
    # this is a CONFIRMED match, not just a frequent word (there's no structured
    # machine field on overtime records, only free-text `reason`). ───────────────────
    equipment_names = _equipment_names()
    machine_hours: dict = defaultdict(float)
    machine_count: Counter = Counter()
    if equipment_names:
        for r in records:
            reason_low = str(r.get('reason') or '').lower()
            if not reason_low:
                continue
            h = _hours(r)
            for name in equipment_names:
                if name in reason_low:
                    machine_hours[name] += h
                    machine_count[name] += 1
    top_machines = sorted(
        ({'name': n.title(), 'count': c, 'hours': round(machine_hours[n], 1)} for n, c in machine_count.items()),
        key=lambda x: -x['hours'],
    )[:8]

    # ── Text mining on `reason` — bigrams/trigrams surface multi-word causes
    # like "5 level" or "rope change" that single-word frequency would miss. A
    # record matching a KNOWN_CATEGORIES pattern is bucketed under that one
    # canonical label instead — it doesn't also get fragmented into n-grams. ──
    phrase_hours: dict = defaultdict(float)
    phrase_count: Counter = Counter()
    for r in records:
        reason = str(r.get('reason') or '')
        h = _hours(r)
        category = _categorize(reason)
        if category:
            phrase_hours[category] += h
            phrase_count[category] += 1
            continue
        tokens = _tokenize(reason)
        phrases = set(_ngrams(tokens, 2) + _ngrams(tokens, 3))
        for p in phrases:
            phrase_hours[p] += h
            phrase_count[p] += 1

    # A bigram and the trigram it's part of ("winder daily" / "winder daily checks")
    # describe the same underlying task at different n-gram lengths, not two separate
    # recurring causes — collapse a shorter phrase into a longer one already kept
    # when it's a substring AND its count is essentially the same occurrence set
    # (allowing some slack since the longer phrase can't match every occurrence of
    # the shorter one, e.g. free-text variation at the start/end of the sentence).
    candidates = [{'phrase': p, 'count': c, 'hours': round(phrase_hours[p], 1)} for p, c in phrase_count.items() if c >= 2]
    candidates.sort(key=lambda x: (-len(x['phrase'].split()), -x['count']))
    deduped_phrases: list[dict] = []
    for cand in candidates:
        if any(cand['phrase'] in kept['phrase'] and cand['count'] <= kept['count'] * 1.15 for kept in deduped_phrases):
            continue
        deduped_phrases.append(cand)
    top_phrases = sorted(deduped_phrases, key=lambda x: (-x['count'], -x['hours']))[:8]

    # ── Category detail — drill-down data for each top recurring reason: who's most
    # affected, which weekday it clusters on, which spare (if any) keeps coming up
    # alongside it, and the underlying records themselves. ────────────────────────
    known_labels = {label for _, label in KNOWN_CATEGORIES}
    category_detail = []
    for p in top_phrases:
        recs = _records_for_phrase(records, p['phrase'], known_labels)
        if not recs:
            continue
        emp_hours_cat = _group_hours(recs, lambda r: r.get('employee_name') or r.get('employee_id'))
        top_emp_cat = max(emp_hours_cat.items(), key=lambda x: x[1])[0] if emp_hours_cat else None
        weekday_counts = Counter(wd for r in recs if (wd := _weekday(r)) is not None)
        top_weekday = WEEKDAY_LABELS[weekday_counts.most_common(1)[0][0]] if weekday_counts else None
        spare_counts: Counter = Counter()
        for r in recs:
            for s in (r.get('spares_used') or []):
                name = str((s or {}).get('name') or '').strip()
                if name:
                    spare_counts[name] += 1
        top_spare = spare_counts.most_common(1)[0][0] if spare_counts else None
        detail_records = sorted(
            [{'employee_name': r.get('employee_name') or r.get('employee_id') or 'Unknown',
              'date': r.get('date'), 'hours': round(_hours(r), 1),
              'reason': str(r.get('reason') or '')[:120]} for r in recs],
            key=lambda x: x['date'] or '', reverse=True,
        )[:15]
        category_detail.append({
            'category': p['phrase'], 'instances': p['count'], 'hours': p['hours'],
            'avg_hours': round(p['hours'] / p['count'], 1) if p['count'] else 0,
            'pct_of_total': round(p['hours'] / total_hours * 100) if total_hours else 0,
            'top_weekday': top_weekday, 'top_employee': top_emp_cat, 'top_spare': top_spare,
            'records': detail_records,
        })

    # ── Trend — a real week-by-week series for the chart, plus the older/newer-half
    # direction (still used for the qualitative "worsening/improving/stable" framing
    # elsewhere) ───────────────────────────────────────────────────────────────────
    weekly_series = _weekly_series(records)
    older, newer = _split_halves(records)
    older_hours = sum(_hours(r) for r in older)
    newer_hours = sum(_hours(r) for r in newer)
    trend_dir = _trend_direction(older_hours, newer_hours)
    trends = []
    if total > 0:
        trends.append({
            'metric': 'Overtime Volume',
            'direction': trend_dir,
            'insight': f"{newer_hours:.1f}h in the second half of the period vs {older_hours:.1f}h in the first half."
                       + (' Overtime is trending up — review staffing levels.' if trend_dir == 'worsening'
                          else ' Overtime is trending down.' if trend_dir == 'improving' else ' Overtime volume is stable.'),
            'older_hours': round(older_hours, 1),
            'newer_hours': round(newer_hours, 1),
        })

    # ── When overtime happens — hour-of-day × weekday, weighted by hours (not just
    # count of starts). Only records with a real start_time can be placed on an hour
    # axis; the "pressed for time" hours-only fast path has no time to bucket. Also
    # keeps the actual entries behind each cell (capped, largest first) so the
    # frontend can show "what happened here" on hover/click, not just a number. ────
    hour_weekday_hours = [[0.0] * 7 for _ in range(24)]
    punch_records: dict = defaultdict(list)
    for r in records:
        wd, hr = _weekday(r), _start_hour(r)
        if wd is None or hr is None:
            continue
        h = _hours(r)
        hour_weekday_hours[hr][wd] += h
        if h > 0:
            punch_records[f"{hr}-{wd}"].append({
                'employee_name': r.get('employee_name') or r.get('employee_id') or 'Unknown',
                'hours': round(h, 1),
                'reason': (r.get('reason') or '')[:100],
                'date': r.get('date'),
            })
    hour_weekday_hours = [[round(v, 1) for v in row] for row in hour_weekday_hours]
    for key, entries in punch_records.items():
        entries.sort(key=lambda x: -x['hours'])
        punch_records[key] = entries[:12]

    # ── Problem areas ─────────────────────────────────────────────────────────
    problem_areas = []

    if top_employees and total_hours > 0:
        top_name, top_h = top_employees[0]
        top_share = round(top_h / total_hours * 100)
        if top_share >= 25 and len(records) >= 4:
            problem_areas.append({
                'title': f'Overtime Concentrated on {top_name}',
                'description': f'{top_name} accounts for {top_h:.1f}h ({top_share}%) of all overtime hours in this view.',
                'severity': 'high' if top_share >= 40 else 'medium',
            })

    if top_depts and total_hours > 0:
        top_dept, top_dh = top_depts[0]
        dept_share = round(top_dh / total_hours * 100)
        if top_dept != 'Unassigned' and dept_share >= 40 and len(top_depts) > 1:
            problem_areas.append({
                'title': f'{top_dept} Carries a Disproportionate Overtime Load',
                'description': f'{top_dept} accounts for {top_dh:.1f}h ({dept_share}%) of overtime — investigate staffing levels for this section.',
                'severity': 'medium',
            })

    if double_time_pct >= 30 and total_hours > 0:
        problem_areas.append({
            'title': 'High Weekend/Holiday (2.0x) Overtime Share',
            'description': f'{double_time_hours:.1f}h ({double_time_pct}%) of overtime is at the double-time rate — the most expensive kind.',
            'severity': 'high' if double_time_pct >= 50 else 'medium',
        })

    for p in top_phrases[:3]:
        if p['count'] >= 3:
            problem_areas.append({
                'title': f"Recurring Cause: \"{p['phrase']}\"",
                'description': f"\"{p['phrase']}\" appears in {p['count']} overtime reasons, totalling {p['hours']}h — either a routine task that could move into normal shift hours, or a recurring fault worth a permanent fix. Worth a look either way.",
                'severity': 'high' if p['count'] >= 5 else 'medium',
            })

    sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    problem_areas.sort(key=lambda p: sev_order.get(p['severity'], 4))

    # ── Recommendations (rule-based, prioritised) ─────────────────────────────
    recommendations = []

    if top_employees and total_hours > 0 and round(top_employees[0][1] / total_hours * 100) >= 25:
        recommendations.append({
            'priority': 'short_term',
            'action': f'Redistribute overtime load away from {top_employees[0][0]}',
            'rationale': 'A single employee carrying a large share of overtime risks burnout and creates a staffing single point of failure.',
            'target': 'Next roster cycle',
        })

    for p in top_phrases[:3]:
        if p['count'] >= 3:
            recommendations.append({
                'priority': 'short_term',
                'action': f"Review recurring overtime driver: \"{p['phrase']}\"",
                'rationale': f"Logged {p['count']} times ({p['hours']}h total). If it's routine work, consider moving it into normal shift hours; if it's a recurring fault, a preventive fix would be cheaper than repeat overtime.",
                'target': 'Within 2 weeks',
            })

    # Spare-parts correlation — a category that keeps needing the same spare is a
    # concrete, checkable lead (equipment reliability / spare availability), stronger
    # than the generic "review this" recommendation above.
    for cat in category_detail[:3]:
        if cat['top_spare'] and cat['instances'] >= 3:
            recommendations.append({
                'priority': 'short_term',
                'action': f"Investigate equipment reliability behind \"{cat['category']}\"",
                'rationale': f"\"{cat['top_spare']}\" recurs alongside this category ({cat['instances']} instances, {cat['hours']}h) — worth checking whether preventive maintenance, a spare-availability issue, or equipment condition is driving the repeat need.",
                'target': 'Within 2 weeks',
            })

    if double_time_pct >= 30 and total_hours > 0:
        recommendations.append({
            'priority': 'short_term',
            'action': 'Evaluate shift restructuring to reduce weekend/holiday overtime exposure',
            'rationale': f'{double_time_pct}% of overtime is at the 2.0x rate — the highest-cost category.',
            'target': 'Next scheduling review',
        })

    if trend_dir == 'worsening':
        recommendations.append({
            'priority': 'short_term',
            'action': 'Review current staffing levels — overtime volume is trending up',
            'rationale': f'Hours rose from {older_hours:.1f}h to {newer_hours:.1f}h across the period.',
            'target': 'This month',
        })

    recommendations.append({
        'priority': 'long_term',
        'action': 'Establish a monthly overtime review with section heads',
        'rationale': 'Regular review catches recurring drivers (equipment, staffing gaps, seasonal load) before they become entrenched.',
        'target': 'Monthly recurring',
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    if total == 0:
        summary = 'No overtime records in the current view — adjust the filters to include some, or there is genuinely nothing to analyse for this selection.'
    else:
        parts = []
        if problem_areas:
            parts.append(problem_areas[0]['title'].lower())
        if top_phrases:
            parts.append(f"top recurring cause \"{top_phrases[0]['phrase']}\" ({top_phrases[0]['count']}x)")
        concern_str = ('; '.join(parts) + '.') if parts else 'no major concentration or recurrence concerns identified.'
        summary = (
            f"{total} overtime record(s) analysed, {total_hours:.1f}h total. "
            f"Trend: {trend_dir}. Key concern: {concern_str} "
            f"{len(recommendations)} recommendation(s) generated."
        )

    # ── Response — 4 explicit sections: what the data shows (facts), what recurs
    # (patterns, still descriptive), what might explain it (interpretation, not fact),
    # and what to do about it. Kept as one flat top-level shape rather than nesting
    # under 4 keys, so a chart binding directly to e.g. `weekly_series` doesn't need an
    # extra path segment — the *section* each field belongs to is a frontend grouping
    # concern (which card it renders under), not a backend data-shape concern. ────────
    return {
        'summary': summary,
        # Observed facts — plain tallies for this selection, no interpretation.
        'total_hours': round(total_hours, 1),
        'total_instances': total,
        'employees_involved': len(emp_hours),
        'sections_involved': len([d for d in dept_hours if d != 'Unassigned']),
        'avg_hours_per_instance': round(total_hours / total, 1) if total else 0,
        'avg_hours_per_employee': round(total_hours / len(emp_hours), 1) if emp_hours else 0,
        'double_time_pct': double_time_pct,
        # Patterns — recurring/descriptive, still data not interpretation.
        'top_reasons': top_phrases,
        'category_detail': category_detail,
        'top_machines': top_machines,
        'top_employees': [{'name': n, 'hours': round(h, 1)} for n, h in top_employees],
        'top_sections': [{'section': s, 'hours': round(h, 1)} for s, h in top_depts],
        'weekly_series': weekly_series,
        'trend_direction': trend_dir,
        'trends': trends,
        'hour_weekday_hours': hour_weekday_hours,
        'weekday_labels': WEEKDAY_LABELS,
        'punch_records': dict(punch_records),
        # Possible causes — interpretive hypotheses, not facts (see the "Possible
        # Causes" section heading on the frontend for that framing).
        'possible_causes': problem_areas,
        # Recommendations — practical actions to investigate.
        'recommendations': recommendations,
        '_source': 'polars-analysis' if _POLARS else 'pure-python-analysis',
        '_records_analysed': total,
        'generated_at': datetime.utcnow().isoformat(),
    }


@router.post("/analyze", dependencies=[Depends(get_current_user)])
async def analyze_overtime(data: OvertimeAnalysisInput):
    """
    Analyse overtime records (whatever the frontend currently has filtered) using
    polars aggregation (falls back to pure Python) plus rule-based text mining on
    the reason field. No external AI API required.
    """
    try:
        return _analyze_overtime(data)
    except Exception as e:
        logger.error(f"Overtime analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")
