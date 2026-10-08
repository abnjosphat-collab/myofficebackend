"""Tools workspace API using dedicated tables in the existing MyOffice Supabase."""
from __future__ import annotations

import csv, hashlib, hmac, io, re, secrets, uuid, zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
from pydantic import BaseModel, Field
from app.supabase_client import rows, supabase
from app.db_helpers import fetch_all_pages

router = APIRouter()
TABLE = {"accounts":"tools_workspace_accounts","sessions":"tools_workspace_sessions","employees":"tools_workspace_employees","tools":"tools_workspace_equipment","history":"tools_workspace_history","changes":"tools_workspace_changes","evidence":"tools_workspace_evidence","source_registers":"tools_workspace_source_registers","usage":"tools_workspace_usage","errors":"tools_workspace_errors","feedback":"tools_workspace_feedback","notification_reads":"tools_workspace_notification_reads","competencies":"tools_workspace_competencies","inspections":"tools_workspace_inspections","incidents":"tools_workspace_incidents","gate_passes":"tools_workspace_gate_passes","gate_pass_items":"tools_workspace_gate_pass_items","gate_pass_approvals":"tools_workspace_gate_pass_approvals"}
FEEDBACK_BUCKET = "tools-workspace-feedback"
EVIDENCE_BUCKET = "tools-workspace-evidence"
SOURCE_REGISTERS_BUCKET = "tools-workspace-source-registers"
_test_mode = False
_memory: dict[str,list[dict[str,Any]]] = {key:[] for key in TABLE}

def _now(): return datetime.now(timezone.utc).isoformat()
def _hash_password(password: str, salt: Optional[bytes]=None):
    salt=salt or secrets.token_bytes(16); digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return salt.hex(),digest.hex()
def _token_hash(token: str): return hashlib.sha256(token.encode()).hexdigest()
def _all(kind: str, newest=False):
    if _test_mode:
        data=[dict(row) for row in _memory[kind]]; return list(reversed(data)) if newest else data
    query=supabase.table(TABLE[kind]).select("*")
    order_column={"accounts":"created_at","sessions":"created_at","employees":"employee_number","tools":"register_number","history":"event_at","changes":"created_at","evidence":"uploaded_at","source_registers":"uploaded_at","usage":"created_at","errors":"created_at","feedback":"created_at","competencies":"updated_at","inspections":"inspected_at","incidents":"reported_at","gate_passes":"requested_at","gate_pass_items":"tool_id","gate_pass_approvals":"step_order"}.get(kind,"created_at")
    query=query.order(order_column,desc=newest)
    if kind not in {"notification_reads","gate_pass_items"}: query=query.order("id",desc=newest)
    return fetch_all_pages(lambda start,end:query.range(start,end).execute(),extract_rows=rows)
def _find(kind: str, field: str, value: Any):
    if _test_mode: return next((dict(row) for row in _memory[kind] if row.get(field)==value),None)
    data=rows(supabase.table(TABLE[kind]).select("*").eq(field,value).limit(1).execute()); return data[0] if data else None
def _insert(kind: str, row: dict[str,Any]):
    if _test_mode: _memory[kind].append(dict(row)); return dict(row)
    data=rows(supabase.table(TABLE[kind]).insert(row).execute())
    if not data: raise HTTPException(500,"The record could not be saved.")
    return data[0]
def _update(kind: str, row_id: str, values: dict[str,Any]):
    if _test_mode:
        row=next((item for item in _memory[kind] if item["id"]==row_id),None)
        if not row: raise HTTPException(404,"Record not found.")
        row.update(values); return dict(row)
    data=rows(supabase.table(TABLE[kind]).update(values).eq("id",row_id).execute())
    if not data: raise HTTPException(404,"Record not found.")
    return data[0]
def _upsert(kind: str, row: dict[str,Any], conflict: str):
    if _test_mode:
        fields=conflict.split(",")
        existing=next((item for item in _memory[kind] if all(item.get(field)==row.get(field) for field in fields)),None)
        if existing: existing.update(row); return dict(existing)
        _memory[kind].append(dict(row)); return dict(row)
    data=rows(supabase.table(TABLE[kind]).upsert(row,on_conflict=conflict).execute())
    if not data: raise HTTPException(500,"The record could not be saved.")
    return data[0]
def _account_role(account:dict[str,Any]):
    return account.get("role") or ("admin" if account.get("can_issue") else "viewer")
def _public(account):
    role=_account_role(account)
    return {"id":account["id"],"name":account["name"],"username":account["username"],"role":role,"department":account.get("department"),"can_issue":role=="issuer","approval_roles":account.get("approval_roles") or [],"signing_pin_configured":bool(account.get("signing_pin_hash")),"created_at":account["created_at"]}
def _session(authorization: Optional[str]=Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Sign in to continue.")
    session=_find("sessions","token_hash",_token_hash(authorization[7:].strip()))
    if not session or session.get("expires_at","")<=_now(): raise HTTPException(401,"This session is no longer valid.")
    account=_find("accounts","id",session["account_id"])
    if not account: raise HTTPException(401,"This session is no longer valid.")
    return account
def _issuer(account=Depends(_session)):
    if _account_role(account)!="issuer": raise HTTPException(403,"Only an Issuer account can issue, receive, transfer or extend equipment.")
    if not account.get("department"): raise HTTPException(403,"This Issuer account needs an assigned department.")
    return account
def _admin(account=Depends(_session)):
    if _account_role(account)!="admin": raise HTTPException(403,"An administrator account is required.")
    return account
def _operator(account=Depends(_session)):
    if _account_role(account) not in {"admin","issuer"}: raise HTTPException(403,"An administrator or Issuer account is required.")
    return account
def _ensure_managed_department(account:dict[str,Any],department:Optional[str]):
    if _account_role(account)=="issuer" and department!=account.get("department"):
        raise HTTPException(403,f"This Issuer may manage only {account.get('department')} records.")
def _department_scope(account:dict[str,Any]):
    return None if _account_role(account)=="admin" else (account.get("department") or "__unassigned__")
def _visible_records(kind:str,account:dict[str,Any],newest=False):
    department=_department_scope(account)
    records=_all(kind,newest)
    return records if department is None else [row for row in records if row.get("department")==department]

FAULT_CONDITIONS={"Defective","Missing","Damaged"}
def _needs_attention(values: dict[str,Any]) -> bool:
    """A tool recorded as not working, or as defective, missing or damaged, is not fit to issue."""
    return values.get("working_status")=="not_working" or values.get("condition") in FAULT_CONDITIONS

def _tool_state(tool: dict[str,Any], actor: str) -> dict[str,Any]:
    state=dict(tool)
    state.setdefault("id",str(uuid.uuid4())); state.setdefault("status","available"); state.setdefault("condition","Good")
    state.setdefault("equipment_kind","other-equipment"); state.setdefault("department","Engineering"); state.setdefault("archived",False)
    state.setdefault("site_code","PP"); state.setdefault("area_code","UG"); state.setdefault("pre_use_check_required",True)
    state.setdefault("weekly_inspection_required",False); state.setdefault("monthly_inspection_required",True); state.setdefault("quarterly_inspection_required",True)
    state.setdefault("calibration_required",False); state.setdefault("criticality","standard"); state.setdefault("cctv_required",False); state.setdefault("gps_required",False); state.setdefault("required_ppe",[]); state.setdefault("ownership_type","company")
    state.setdefault("custody",None); state.setdefault("row_version",1); state.setdefault("created_by",actor); state.setdefault("created_at",_now()); state.setdefault("updated_at",_now())
    return state

def _apply_change(before: Optional[dict[str,Any]], after: dict[str,Any], action: str, actor: str, detail: str="", employee: Optional[dict[str,Any]]=None, idempotency_key: Optional[str]=None):
    """Apply one equipment mutation and its audit/change records atomically in Postgres."""
    after=_tool_state(after,actor); event={"id":str(uuid.uuid4()),"detail":detail,"event_at":_now(),"employee_id":employee.get("id") if employee else None,"employee_name":employee.get("name") if employee else None,"metadata":{}}
    if _test_mode:
        existing=next((row for row in _memory["tools"] if row["id"]==after["id"]),None)
        if existing: existing.clear(); existing.update(after); existing["row_version"]=int((before or {}).get("row_version",0))+1
        else: _memory["tools"].append(after)
        _memory["history"].append({"id":event["id"],"tool_id":after["id"],"tool_name":after["name"],"action":action,"detail":detail,"actor_name":actor,"employee_id":event["employee_id"],"employee_name":event["employee_name"],"event_at":event["event_at"],"metadata":{}})
        change={"id":str(uuid.uuid4()),"tool_id":after["id"],"action":action,"before_state":before or after,"after_state":dict(after),"actor_name":actor,"created_at":_now(),"undone_at":None,"undone_by":None}; _memory["changes"].append(change)
        return {"tool":dict(after),"change_id":change["id"]}
    payload={"idempotency_key":idempotency_key or str(uuid.uuid4()),"tool_id":before.get("id") if before else None,"expected_version":before.get("row_version") if before else None,"before_state":before or after,"after_state":after,"action":action,"actor_name":actor,"event":event}
    try:
        response=supabase.rpc("tools_workspace_apply_change",{"payload":payload}).execute(); data=getattr(response,"data",None) or {}
        return data.get("result",data)
    except Exception as exc:
        message=str(exc)
        if "STALE_VERSION" in message: raise HTTPException(409,"This record changed elsewhere. Refresh and try again.") from exc
        if "CONFLICT" in message or "duplicate" in message.lower(): raise HTTPException(409,"That register number already exists.") from exc
        raise

class RegisterAccount(BaseModel):
    name:str=Field(min_length=2,max_length=100); username:str=Field(min_length=3,max_length=254,pattern=r"^[^\s]+$"); password:str=Field(min_length=6,max_length=128); can_issue:bool=False
class Login(BaseModel): username:str; password:str
ApprovalRole = Literal["hos","hod","security","finance","general_manager"]
class AccountRoleUpdate(BaseModel):
    role:Literal["admin","issuer","viewer"]; department:Optional[str]=Field(default=None,max_length=100); approval_roles:list[ApprovalRole]=Field(default_factory=list,max_length=5)
class EmployeeInput(BaseModel):
    employee_number:str=Field(min_length=1,max_length=60); name:str=Field(min_length=2,max_length=120); department:str=Field(min_length=1,max_length=100); job_title:Optional[str]=Field(default=None,max_length=100); supervisor_name:Optional[str]=Field(default=None,max_length=120)
class ToolInput(BaseModel):
    register_number:Optional[str]=Field(default=None,max_length=80); name:str=Field(min_length=2,max_length=160); make_model:Optional[str]=Field(default=None,max_length=200); serial_number:Optional[str]=Field(default=None,max_length=120); category:Optional[str]=Field(default=None,max_length=100); equipment_kind:str=Field(default="other-equipment",max_length=80); storage_location:str=Field(min_length=1,max_length=240); department:str=Field(default="Engineering",max_length=100); section:Optional[str]=Field(default=None,max_length=100); condition:str=Field(default="Good",max_length=60); notes:Optional[str]=Field(default=None,max_length=2000); approval_ref:Optional[str]=Field(default=None,max_length=160); calibration:Optional[str]=Field(default=None,max_length=160); specifications:dict[str,str]=Field(default_factory=dict); site_code:str=Field(default="PP",min_length=2,max_length=8,pattern=r"^[A-Za-z0-9]+$"); area_code:str=Field(default="UG",min_length=1,max_length=8,pattern=r"^[A-Za-z0-9]+$"); department_code:Optional[str]=Field(default=None,max_length=8,pattern=r"^[A-Za-z0-9]+$"); activity_code:Optional[str]=Field(default=None,max_length=8,pattern=r"^[A-Za-z0-9]+$"); home_storage_location:Optional[str]=Field(default=None,max_length=240); storage_conditions:Optional[str]=Field(default=None,max_length=1000); maintenance_requirements:Optional[str]=Field(default=None,max_length=2000); pre_use_check_required:bool=True; weekly_inspection_required:bool=False; monthly_inspection_required:bool=True; quarterly_inspection_required:bool=True; calibration_required:bool=False; calibration_frequency_days:Optional[int]=Field(default=None,ge=1,le=3650); replacement_value:Optional[float]=Field(default=None,ge=0); criticality:Literal["standard","high","safety_critical"]="standard"; cctv_required:bool=False; gps_required:bool=False; required_ppe:list[str]=Field(default_factory=list,max_length=20); ownership_type:Literal["company","contractor"]="company"; contractor_name:Optional[str]=Field(default=None,max_length=200); oem_manual_ref:Optional[str]=Field(default=None,max_length=240); purchase_date:Optional[str]=Field(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"); procurement_cost:Optional[float]=Field(default=None,ge=0); insured:Optional[bool]=None; last_verified:Optional[str]=Field(default=None,max_length=40); working_status:Optional[Literal["working","not_working"]]=None
class ToolUpdate(BaseModel):
    register_number:Optional[str]=Field(default=None,min_length=1,max_length=80); name:Optional[str]=Field(default=None,min_length=2,max_length=160); make_model:Optional[str]=Field(default=None,max_length=200); serial_number:Optional[str]=Field(default=None,max_length=120); category:Optional[str]=Field(default=None,max_length=100); equipment_kind:Optional[str]=Field(default=None,max_length=80); storage_location:Optional[str]=Field(default=None,min_length=1,max_length=240); department:Optional[str]=Field(default=None,max_length=100); section:Optional[str]=Field(default=None,max_length=100); condition:Optional[str]=Field(default=None,max_length=60); notes:Optional[str]=Field(default=None,max_length=2000); approval_ref:Optional[str]=Field(default=None,max_length=160); calibration:Optional[str]=Field(default=None,max_length=160); specifications:Optional[dict[str,str]]=None; home_storage_location:Optional[str]=Field(default=None,max_length=240); storage_conditions:Optional[str]=Field(default=None,max_length=1000); maintenance_requirements:Optional[str]=Field(default=None,max_length=2000); pre_use_check_required:Optional[bool]=None; weekly_inspection_required:Optional[bool]=None; monthly_inspection_required:Optional[bool]=None; quarterly_inspection_required:Optional[bool]=None; calibration_required:Optional[bool]=None; calibration_frequency_days:Optional[int]=Field(default=None,ge=1,le=3650); replacement_value:Optional[float]=Field(default=None,ge=0); criticality:Optional[Literal["standard","high","safety_critical"]]=None; cctv_required:Optional[bool]=None; gps_required:Optional[bool]=None; required_ppe:Optional[list[str]]=Field(default=None,max_length=20); ownership_type:Optional[Literal["company","contractor"]]=None; contractor_name:Optional[str]=Field(default=None,max_length=200); oem_manual_ref:Optional[str]=Field(default=None,max_length=240); purchase_date:Optional[str]=Field(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"); procurement_cost:Optional[float]=Field(default=None,ge=0); insured:Optional[bool]=None; last_verified:Optional[str]=Field(default=None,max_length=40); working_status:Optional[Literal["working","not_working"]]=None
class IssueInput(BaseModel):
    employee_id:str; location:str=Field(min_length=1,max_length=240); expected_return_at:Optional[str]=None; job_reference:Optional[str]=Field(default=None,max_length=160); assigned_equipment:list[str]=Field(default_factory=list,max_length=30); notes:Optional[str]=Field(default=None,max_length=1000); override_due_checks:bool=False; override_reason:Optional[str]=Field(default=None,max_length=500)
class ReturnInput(BaseModel):
    location:str=Field(min_length=1,max_length=240); condition:Literal["Good","Damaged","Missing parts"]="Good"; notes:Optional[str]=Field(default=None,max_length=1000)
class MovementInput(BaseModel):
    kind:Literal["issue","return","transfer","extend"]; employee_id:Optional[str]=None; employee_name:Optional[str]=None; department:Optional[str]=None; location:Optional[str]=Field(default=None,max_length=240); expected_return_at:Optional[str]=None; job_reference:Optional[str]=Field(default=None,max_length=160); assigned_equipment:list[str]=Field(default_factory=list,max_length=30); condition:Optional[str]=Field(default="Good",max_length=60); notes:Optional[str]=Field(default=None,max_length=2000); approval_ref:Optional[str]=Field(default=None,max_length=160); calibration:Optional[str]=Field(default=None,max_length=160); pre_use_check_completed:bool=False; idempotency_key:Optional[str]=None; override_due_checks:bool=False; override_reason:Optional[str]=Field(default=None,max_length=500)
class ImportCommit(BaseModel):
    target:Literal["equipment","employees"]; rows:list[dict[str,Any]]=Field(max_length=500)
class UsageInput(BaseModel): event:str=Field(min_length=1,max_length=100); detail:Optional[str]=Field(default=None,max_length=500)
class ErrorInput(BaseModel): message:str=Field(min_length=1,max_length=2000); source:Optional[str]=Field(default=None,max_length=500); stack:Optional[str]=Field(default=None,max_length=8000)
class NotificationReadInput(BaseModel): keys:list[str]=Field(max_length=500)
class MarkReadyInput(BaseModel):
    resolution_note:str=Field(min_length=2,max_length=1000)
class CompetencyInput(BaseModel):
    employee_id:str; tool_id:Optional[str]=None; category:Optional[str]=Field(default=None,max_length=100); trained:bool; qualified:bool; authorized:bool; training_certificate_ref:Optional[str]=Field(default=None,max_length=200); training_expires_at:Optional[str]=None; qualification_ref:Optional[str]=Field(default=None,max_length=200); qualification_expires_at:Optional[str]=None; authorization_expires_at:Optional[str]=None; notes:Optional[str]=Field(default=None,max_length=1000); authorized_by:Optional[str]=Field(default=None,max_length=120)
class InspectionInput(BaseModel):
    inspection_type:Literal["pre_use","weekly","monthly","quarterly","calibration","maintenance","storage_audit","repair"]; outcome:Literal["passed","conditional","failed"]; inspected_at:Optional[str]=None; next_due_at:Optional[str]=None; condition:Optional[str]=Field(default=None,max_length=100); defects:Optional[str]=Field(default=None,max_length=1500); notes:Optional[str]=Field(default=None,max_length=1500); repair_quote:Optional[float]=Field(default=None,ge=0); new_equipment_price:Optional[float]=Field(default=None,gt=0)
class IncidentInput(BaseModel):
    incident_type:Literal["lost","damaged","stolen","missing_components","late_return"]; occurred_at:str; employee_id:Optional[str]=None; explanation:str=Field(min_length=5,max_length=3000)
class IncidentCloseInput(BaseModel):
    investigation_outcome:str=Field(min_length=5,max_length=3000); negligence_confirmed:bool=False; replacement_cost:Optional[float]=Field(default=None,ge=0); recovery_months:Optional[int]=Field(default=None,ge=1,le=6)
class SigningPinInput(BaseModel):
    password:str=Field(min_length=6,max_length=128); pin:str=Field(min_length=4,max_length=12,pattern=r"^\d+$")
class GatePassInput(BaseModel):
    movement_scope:Literal["internal","external"]; department:str=Field(min_length=1,max_length=100); destination:str=Field(min_length=2,max_length=240); purpose:str=Field(min_length=2,max_length=1000); expected_out_at:str; expected_return_at:Optional[str]=None; finance_required:bool=False; tool_ids:list[str]=Field(min_length=1,max_length=50); notes:Optional[str]=Field(default=None,max_length=1500)
class GatePassDecision(BaseModel):
    decision:Literal["approve","reject"]; credential:str=Field(min_length=4,max_length=128); comment:Optional[str]=Field(default=None,max_length=1000)

DEPARTMENT_CODES={"Engineering":"ENG","Mining":"MIN","Mine Technical Services":"MTS","Shared Services":"SS","IT":"IT"}
ACTIVITY_CODES={"angle-grinder":"AG","cordless-drill":"CD","rotary-hammer":"RH","digital-multimeter":"DM","clamp-meter":"CM","torque-wrench":"TW","inverter-welder":"WM","welding-equipment":"WM","laser-level":"LL","socket-set":"SS","laptop":"LT","work-lamp":"WL","survey-equipment":"SE","lifting-equipment":"LE","power-tool":"PT","hand-tool":"HT","test-instrument":"TI","tool-kit":"TK","other-equipment":"OE"}
QUARTER_COLOURS={1:"Blue",2:"Green",3:"Yellow",4:"White"}
def _parse_datetime(value:Optional[str]):
    if not value: return None
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError,ValueError): raise HTTPException(422,"Enter a valid date and time.")

def _next_number(prefix:str):
    normalized=prefix.upper()
    if _test_mode:
        values=[]
        for tool in _memory["tools"]:
            match=re.fullmatch(re.escape(normalized)+r"-(\d+)",str(tool.get("register_number","")),re.IGNORECASE)
            if match: values.append(int(match.group(1)))
        return f"{normalized}-{max(values,default=0)+1:02d}"
    response=supabase.rpc("tools_workspace_next_register_number",{"prefix_value":normalized}).execute()
    value=getattr(response,"data",None)
    if not value: raise HTTPException(500,"A register number could not be generated.")
    return str(value)

def _register_number(body:ToolInput):
    supplied=(body.register_number or "").strip().upper()
    if supplied: return supplied
    department_code=(body.department_code or DEPARTMENT_CODES.get(body.department) or re.sub(r"[^A-Za-z]","",body.department)[:3]).upper()
    activity_code=(body.activity_code or ACTIVITY_CODES.get(body.equipment_kind) or "OE").upper()
    return _next_number(f"{body.site_code}-{body.area_code}-{department_code}-{activity_code}")

def _not_expired(value:Optional[str],now:Optional[datetime]=None):
    return not value or bool(_parse_datetime(value) and _parse_datetime(value)>=(now or datetime.now(timezone.utc)))

def _competency_for(employee:dict[str,Any],tool:dict[str,Any],competencies:Optional[list[dict[str,Any]]]=None):
    matches=[row for row in (competencies if competencies is not None else _all("competencies")) if row.get("employee_id")==employee.get("id") and (row.get("tool_id")==tool.get("id") or (not row.get("tool_id") and row.get("category") and str(row.get("category")).lower()==str(tool.get("category") or "").lower()))]
    exact=next((row for row in matches if row.get("tool_id")==tool.get("id")),None)
    return exact or next(iter(matches),None)

def _is_eligible(employee:dict[str,Any],tool:dict[str,Any],competencies:Optional[list[dict[str,Any]]]=None):
    record=_competency_for(employee,tool,competencies)
    if not record: return False
    return bool(record.get("trained") and record.get("qualified") and record.get("authorized") and _not_expired(record.get("training_expires_at")) and _not_expired(record.get("qualification_expires_at")) and _not_expired(record.get("authorization_expires_at")))

def _latest_inspections(tool_id:str,inspections:Optional[list[dict[str,Any]]]=None):
    latest:dict[str,dict[str,Any]]={}
    for row in sorted((item for item in (inspections if inspections is not None else _all("inspections")) if item.get("tool_id")==tool_id),key=lambda item:item.get("inspected_at") or "",reverse=True):
        latest.setdefault(row["inspection_type"],row)
    return latest

def _inspection_due(tool:dict[str,Any],inspections:Optional[list[dict[str,Any]]]=None):
    latest=_latest_inspections(tool["id"],inspections); now=datetime.now(timezone.utc); due=[]
    requirements=(("weekly",tool.get("weekly_inspection_required")),("monthly",tool.get("monthly_inspection_required")),("quarterly",tool.get("quarterly_inspection_required")),("calibration",tool.get("calibration_required")))
    for inspection_type,required in requirements:
        if not required: continue
        record=latest.get(inspection_type); deadline=_parse_datetime(record.get("next_due_at")) if record else None
        if not record or record.get("outcome")!="passed" or not deadline or deadline<now: due.append(inspection_type)
    return due

def _inspection_colour(inspection_type:str,outcome:str,inspected_at:datetime):
    if outcome=="failed": return "Red"
    if inspection_type=="quarterly": return QUARTER_COLOURS[(inspected_at.month-1)//3+1]
    return "Amber" if outcome=="conditional" else "Green"

def _default_next_due(inspection_type:str,inspected_at:datetime,tool:dict[str,Any]):
    days={"pre_use":1,"weekly":7,"monthly":30,"quarterly":90,"calibration":tool.get("calibration_frequency_days") or 365,"maintenance":90,"storage_audit":90,"repair":0}[inspection_type]
    return (inspected_at+timedelta(days=days)).isoformat() if days else None

def _verify_signing_credential(account:dict[str,Any],credential:str):
    _,password_candidate=_hash_password(credential,bytes.fromhex(account["salt"]))
    if hmac.compare_digest(password_candidate,account["password_hash"]): return "password"
    if account.get("signing_pin_salt") and account.get("signing_pin_hash"):
        _,pin_candidate=_hash_password(credential,bytes.fromhex(account["signing_pin_salt"]))
        if hmac.compare_digest(pin_candidate,account["signing_pin_hash"]): return "pin"
    raise HTTPException(401,"The signing password or PIN is incorrect.")

def _notification_key(tool:dict[str,Any],kind:str):
    marker=(tool.get("custody") or {}).get("expected_return_at") if kind=="overdue" else tool.get("row_version",1)
    return f"{kind}:{tool['id']}:{marker or 'current'}"

def _effective_status(tool:dict[str,Any], now:Optional[datetime]=None):
    """Derive overdue state from custody so alerts do not depend on a scheduler."""
    status=tool.get("status")
    if status!="issued" or tool.get("archived"): return status
    due=(tool.get("custody") or {}).get("expected_return_at")
    if not due: return status
    try:
        deadline=datetime.fromisoformat(str(due).replace("Z","+00:00"))
        if deadline.tzinfo is None: deadline=deadline.replace(tzinfo=timezone.utc)
        return "overdue" if deadline.astimezone(timezone.utc)<(now or datetime.now(timezone.utc)) else status
    except (TypeError,ValueError):
        return status

def _effective_tool(tool:dict[str,Any]):
    return {**tool,"status":_effective_status(tool)}

def _active_notifications():
    alerts=[]
    now=datetime.now(timezone.utc)
    for stored_tool in _all("tools"):
        tool=_effective_tool(stored_tool); kind=tool.get("status")
        if tool.get("archived"): continue
        if kind=="overdue":
            due=_parse_datetime((tool.get("custody") or {}).get("expected_return_at"))
            kind="overdue_6h" if due and due+timedelta(hours=6)<=now else "overdue"
            alerts.append({"key":_notification_key(tool,kind),"kind":kind,"tool_id":tool["id"],"tool_name":tool.get("name","Equipment"),"department":tool.get("department","Engineering")})
        elif kind=="attention": alerts.append({"key":_notification_key(tool,kind),"kind":kind,"tool_id":tool["id"],"tool_name":tool.get("name","Equipment"),"department":tool.get("department","Engineering")})
        for inspection_type in _inspection_due(tool):
            alerts.append({"key":f"inspection:{tool['id']}:{inspection_type}","kind":"inspection_due","inspection_type":inspection_type,"tool_id":tool["id"],"tool_name":tool.get("name","Equipment"),"department":tool.get("department","Engineering")})
    for incident in _all("incidents"):
        if incident.get("status")=="closed": continue
        due=_parse_datetime(incident.get("investigation_due_at"))
        if due and due<now:
            tool=_find("tools","id",incident.get("tool_id")) or {}
            alerts.append({"key":f"incident:{incident['id']}","kind":"investigation_overdue","tool_id":incident.get("tool_id"),"tool_name":tool.get("name","Equipment incident"),"department":tool.get("department","Engineering")})
    return alerts

def _new_session(account_id):
    token=secrets.token_urlsafe(32); _insert("sessions",{"id":str(uuid.uuid4()),"token_hash":_token_hash(token),"account_id":account_id,"created_at":_now(),"expires_at":(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()}); return token
@router.post("/auth/register",status_code=201)
def register(body:RegisterAccount):
    username=body.username.strip().lower()
    if _find("accounts","username",username): raise HTTPException(409,"That username is already in use.")
    # The first account bootstraps administration. Every later self-registration is view-only;
    # an administrator explicitly assigns Issuer access and its department.
    role="admin" if not _all("accounts") else "viewer"
    salt,digest=_hash_password(body.password); account=_insert("accounts",{"id":str(uuid.uuid4()),"name":body.name.strip(),"username":username,"salt":salt,"password_hash":digest,"role":role,"department":None,"can_issue":False,"approval_roles":[],"signing_pin_salt":None,"signing_pin_hash":None,"created_at":_now()})
    return {"token":_new_session(account["id"]),"account":_public(account)}
@router.post("/auth/login")
def login(body:Login):
    account=_find("accounts","username",body.username.strip().lower())
    if not account: raise HTTPException(401,"Username or password is incorrect.")
    _,candidate=_hash_password(body.password,bytes.fromhex(account["salt"]))
    if not hmac.compare_digest(candidate,account["password_hash"]): raise HTTPException(401,"Username or password is incorrect.")
    return {"token":_new_session(account["id"]),"account":_public(account)}
@router.get("/auth/me")
def me(account=Depends(_session)): return _public(account)
@router.get("/accounts")
def list_accounts(_=Depends(_admin)): return [_public(account) for account in _all("accounts")]
@router.patch("/accounts/{account_id}")
def update_account_role(account_id:str,body:AccountRoleUpdate,admin=Depends(_admin)):
    account=_find("accounts","id",account_id)
    if not account: raise HTTPException(404,"Account was not found.")
    department=(body.department or "").strip() or None
    if body.role in {"issuer","viewer"} and not department: raise HTTPException(422,"Choose the department this account may view or manage.")
    if account_id==admin["id"] and body.role!="admin": raise HTTPException(409,"Assign another administrator before changing your own administrator role.")
    updated=_update("accounts",account_id,{"role":body.role,"department":department if body.role!="admin" else None,"can_issue":body.role=="issuer","approval_roles":list(dict.fromkeys(body.approval_roles))})
    return _public(updated)

@router.put("/auth/signing-pin")
def set_signing_pin(body:SigningPinInput,account=Depends(_session)):
    _verify_signing_credential(account,body.password)
    salt,digest=_hash_password(body.pin)
    _update("accounts",account["id"],{"signing_pin_salt":salt,"signing_pin_hash":digest})
    return {"signing_pin_configured":True}

@router.get("/notifications")
def notifications(account=Depends(_session)):
    read_rows=[row for row in _memory["notification_reads"] if row.get("account_id")==account["id"]] if _test_mode else rows(supabase.table(TABLE["notification_reads"]).select("alert_key").eq("account_id",account["id"]).execute())
    read={row["alert_key"] for row in read_rows}
    department=_department_scope(account)
    alerts=[{**alert,"read":alert["key"] in read} for alert in _active_notifications() if department is None or alert.get("department")==department]
    return {"alerts":alerts,"unread_count":sum(not alert["read"] for alert in alerts)}

@router.post("/notifications/read",status_code=202)
def read_notifications(body:NotificationReadInput,account=Depends(_session)):
    active={alert["key"] for alert in _active_notifications()}; accepted=list(dict.fromkeys(key for key in body.keys if key in active))
    for key in accepted:
        row={"account_id":account["id"],"alert_key":key,"read_at":_now()}
        if _test_mode:
            existing=next((item for item in _memory["notification_reads"] if item["account_id"]==account["id"] and item["alert_key"]==key),None)
            if existing: existing.update(row)
            else: _memory["notification_reads"].append(row)
        else: supabase.table(TABLE["notification_reads"]).upsert(row,on_conflict="account_id,alert_key").execute()
    return {"read":accepted}

@router.get("/employees")
def list_employees(account=Depends(_session)): return _visible_records("employees",account)
class EmployeeActiveInput(BaseModel): active:bool
@router.patch("/employees/{employee_id}")
def set_employee_active(employee_id:str,body:EmployeeActiveInput,account=Depends(_operator)):
    """Deactivate or reactivate an employee. Nothing is deleted: approvals and history stay, and the person simply stops being offered for issue and drops out of the default list."""
    employee=_find("employees","id",employee_id)
    if not employee: raise HTTPException(404,"Employee was not found.")
    _ensure_managed_department(account,employee.get("department"))
    if not body.active and any((tool.get("custody") or {}).get("employee_id")==employee["id"] for tool in _all("tools")):
        raise HTTPException(409,"This employee still holds equipment. Receive it back before deactivating them.")
    return _update("employees",employee["id"],{"active":body.active})
@router.post("/employees",status_code=201)
def create_employee(body:EmployeeInput,account=Depends(_operator)):
    _ensure_managed_department(account,body.department)
    number=body.employee_number.strip()
    if any(row["employee_number"].lower()==number.lower() for row in _all("employees")): raise HTTPException(409,"That employee number already exists.")
    return _insert("employees",{"id":str(uuid.uuid4()),**body.model_dump(),"employee_number":number,"active":True,"created_at":_now(),"created_by":account["name"]})
@router.get("/tools")
def list_tools(account=Depends(_session)):
    tools=_visible_records("tools",account)
    tool_ids={tool["id"] for tool in tools}
    evidence=[item for item in _all("evidence") if item.get("tool_id") in tool_ids]
    employees=[employee for employee in _visible_records("employees",account) if employee.get("active",True)]
    competencies=_all("competencies")
    inspections=[item for item in _all("inspections") if item.get("tool_id") in tool_ids]
    by_tool:dict[str,list[dict[str,Any]]]={}
    for item in evidence:
        if item.get("removed_at"): continue
        row=dict(item)
        if not _test_mode:
            try:
                signed=supabase.storage.from_(EVIDENCE_BUCKET).create_signed_url(item["storage_path"],3600)
                row["url"]=signed.get("signedURL") or signed.get("signedUrl")
            except Exception: row["url"]=None
        by_tool.setdefault(item["tool_id"],[]).append(row)
    result=[]
    for tool in tools:
        eligible=[{"id":employee["id"],"employee_number":employee["employee_number"],"name":employee["name"],"department":employee["department"],"job_title":employee.get("job_title")} for employee in employees if _is_eligible(employee,tool,competencies)]
        result.append({**_effective_tool(tool),"evidence":by_tool.get(tool["id"],[]),"eligible_employees":eligible,"inspection_due":_inspection_due(tool,inspections),"latest_inspections":_latest_inspections(tool["id"],inspections)})
    return result
@router.post("/tools",status_code=201)
def create_tool(body:ToolInput,account=Depends(_operator)):
    _ensure_managed_department(account,body.department)
    if body.ownership_type=="contractor" and not (body.contractor_name or "").strip(): raise HTTPException(422,"Record the contractor that owns this equipment.")
    number=_register_number(body)
    if any(row["register_number"].lower()==number.lower() for row in _all("tools")): raise HTTPException(409,"That register number already exists.")
    values=body.model_dump(); values["home_storage_location"]=values.get("home_storage_location") or values["storage_location"]
    if (values.get("replacement_value") or 0)>5000: values["cctv_required"]=True
    result=_apply_change(None,{"id":str(uuid.uuid4()),**values,"register_number":number,"status":"attention" if _needs_attention(values) else "available","custody":None},"created",account["name"],f"Added {number} to the register")
    return result["tool"]
@router.patch("/tools/{tool_id}")
def update_tool(tool_id:str,body:ToolUpdate,account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    values=body.model_dump(exclude_unset=True); after={**tool,**values}
    if (after.get("replacement_value") or 0)>5000: after["cctv_required"]=True
    # Recording a fault holds available equipment for attention; equipment already out stays out until it is received, and
    # clearing the fault is done with Mark ready (which records who resolved it), never silently by editing the details.
    if after.get("status")=="available" and _needs_attention(after): after["status"]="attention"
    return _apply_change(tool,after,"updated",account["name"],"Equipment details updated")["tool"]
@router.post("/tools/{tool_id}/archive")
def archive_tool(tool_id:str,account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    if tool.get("custody"): raise HTTPException(409,"Receive this tool before archiving it.")
    after={**tool,"archived":not bool(tool.get("archived"))}
    action="restored" if after["archived"] is False else "archived"
    return _apply_change(tool,after,action,account["name"],f"Equipment {action}")["tool"]

@router.post("/tools/{tool_id}/mark-ready")
def mark_tool_ready(tool_id:str,body:MarkReadyInput,account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    if tool.get("archived"): raise HTTPException(409,"Restore this tool before marking it ready for use.")
    if tool.get("custody"): raise HTTPException(409,"Receive this tool before marking it ready for use.")
    if tool.get("status")!="attention": raise HTTPException(409,"Only equipment held for attention can be marked ready for use.")
    note=body.resolution_note.strip()
    after={**tool,"status":"available","condition":"Good","notes":note}
    return _apply_change(tool,after,"released",account["name"],f"Marked ready for use: {note}")["tool"]

@router.get("/compliance")
def compliance(account=Depends(_session)):
    tool_ids={tool["id"] for tool in _visible_records("tools",account)}
    employee_ids={employee["id"] for employee in _visible_records("employees",account)}
    department=_department_scope(account)
    competencies=[row for row in _all("competencies",True) if department is None or row.get("tool_id") in tool_ids or row.get("employee_id") in employee_ids]
    inspections=[row for row in _all("inspections",True) if department is None or row.get("tool_id") in tool_ids]
    incidents=[row for row in _all("incidents",True) if department is None or row.get("tool_id") in tool_ids]
    gate_passes=[_gate_pass_view(item) for item in _all("gate_passes",True) if department is None or item.get("department")==department]
    return {"competencies":competencies,"inspections":inspections,"incidents":incidents,"gate_passes":gate_passes}

@router.post("/competencies",status_code=201)
def save_competency(body:CompetencyInput,account=Depends(_operator)):
    employee=_find("employees","id",body.employee_id)
    if not employee: raise HTTPException(404,"Employee was not found.")
    _ensure_managed_department(account,employee.get("department"))
    tool=_find("tools","id",body.tool_id) if body.tool_id else None
    if body.tool_id and not tool: raise HTTPException(404,"Tool was not found.")
    if tool:
        _ensure_managed_department(account,tool.get("department"))
    category=(body.category or "").strip() or None
    if not tool and not category: raise HTTPException(422,"Choose a specific tool or equipment category.")
    existing=next((row for row in _all("competencies") if row.get("employee_id")==employee["id"] and row.get("tool_id")==body.tool_id and (row.get("category") or "").lower()==(category or "").lower()),None)
    values={**body.model_dump(exclude_unset=True),"category":category,"authorized_by":((body.authorized_by or "").strip() or account["name"]) if body.authorized else None,"updated_at":_now()}
    if existing: return _update("competencies",existing["id"],values)
    return _insert("competencies",{"id":str(uuid.uuid4()),**values,"created_by":account["name"],"created_at":_now()})

@router.post("/tools/{tool_id}/inspections",status_code=201)
def record_inspection(tool_id:str,body:InspectionInput,account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    inspected=_parse_datetime(body.inspected_at) or datetime.now(timezone.utc)
    next_due=(_parse_datetime(body.next_due_at).isoformat() if body.next_due_at else _default_next_due(body.inspection_type,inspected,tool))
    repair_eligible=None
    if body.inspection_type=="repair" and body.repair_quote is not None and body.new_equipment_price is not None: repair_eligible=body.repair_quote<=body.new_equipment_price*.6
    row=_insert("inspections",{"id":str(uuid.uuid4()),"tool_id":tool_id,"inspection_type":body.inspection_type,"outcome":body.outcome,"inspected_at":inspected.isoformat(),"next_due_at":next_due,"inspector_account_id":account["id"],"inspector_name":account["name"],"colour_code":_inspection_colour(body.inspection_type,body.outcome,inspected),"condition":body.condition or tool.get("condition"),"defects":body.defects,"notes":body.notes,"repair_quote":body.repair_quote,"new_equipment_price":body.new_equipment_price,"repair_eligible":repair_eligible,"created_at":_now()})
    if body.outcome=="failed" and not tool.get("custody"):
        after={**tool,"status":"attention","condition":"Defective","notes":body.defects or body.notes or "Failed inspection"}
        _apply_change(tool,after,"inspection_failed",account["name"],f"{body.inspection_type.replace('_',' ').title()} failed")
    return row

@router.post("/tools/{tool_id}/incidents",status_code=201)
def report_incident(tool_id:str,body:IncidentInput,account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    employee=_find("employees","id",body.employee_id) if body.employee_id else None
    occurred=_parse_datetime(body.occurred_at)
    incident=_insert("incidents",{"id":str(uuid.uuid4()),"tool_id":tool_id,"incident_type":body.incident_type,"occurred_at":occurred.isoformat(),"reported_at":_now(),"reported_by_account_id":account["id"],"reported_by":account["name"],"employee_id":employee.get("id") if employee else None,"explanation":body.explanation.strip(),"investigation_due_at":(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(),"status":"open","negligence_confirmed":None,"replacement_cost":None,"recovery_months":None,"investigation_outcome":None,"closed_at":None,"closed_by":None})
    after={**tool,"status":"attention","condition":"Missing" if body.incident_type in {"lost","stolen"} else "Damaged","notes":body.explanation.strip()}
    _apply_change(tool,after,f"incident_{body.incident_type}",account["name"],f"{body.incident_type.replace('_',' ').title()} reported; investigation due within 24 hours",employee)
    return incident

@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id:str,body:IncidentCloseInput,account=Depends(_operator)):
    incident=_find("incidents","id",incident_id)
    if not incident: raise HTTPException(404,"Incident was not found.")
    tool=_find("tools","id",incident.get("tool_id"))
    if tool: _ensure_managed_department(account,tool.get("department"))
    if incident.get("status")=="closed": raise HTTPException(409,"This investigation is already closed.")
    if body.negligence_confirmed and body.replacement_cost is None: raise HTTPException(422,"Record the replacement cost when negligence is confirmed.")
    return _update("incidents",incident_id,{"status":"closed","investigation_outcome":body.investigation_outcome.strip(),"negligence_confirmed":body.negligence_confirmed,"replacement_cost":body.replacement_cost,"recovery_months":body.recovery_months or (6 if body.negligence_confirmed else None),"closed_at":_now(),"closed_by":account["name"]})

def _gate_pass_view(gate_pass:dict[str,Any]):
    items=[item for item in _all("gate_pass_items") if item.get("gate_pass_id")==gate_pass["id"]]
    tools=[]
    for item in items:
        tool=_find("tools","id",item.get("tool_id"))
        if tool: tools.append({"id":tool["id"],"register_number":tool["register_number"],"name":tool["name"],"serial_number":tool.get("serial_number"),"replacement_value":tool.get("replacement_value")})
    approvals=sorted((item for item in _all("gate_pass_approvals") if item.get("gate_pass_id")==gate_pass["id"]),key=lambda item:item["step_order"])
    return {**gate_pass,"tools":tools,"approvals":approvals}

@router.get("/gate-passes")
def list_gate_passes(_=Depends(_session)): return [_gate_pass_view(item) for item in _all("gate_passes",True)]

@router.post("/gate-passes",status_code=201)
def create_gate_pass(body:GatePassInput,account=Depends(_operator)):
    _ensure_managed_department(account,body.department)
    selected=[]
    for tool_id in list(dict.fromkeys(body.tool_ids)):
        tool=_find("tools","id",tool_id)
        if not tool: raise HTTPException(404,"One of the selected tools was not found.")
        if tool.get("department")!=body.department: raise HTTPException(409,"Every gate-pass item must belong to the selected department.")
        if tool.get("archived") or tool.get("status")=="attention": raise HTTPException(409,f"{tool.get('register_number')} is not fit for movement.")
        selected.append(tool)
    now=datetime.now(timezone.utc); pass_number=_next_number(f"GP-{now.year}"); gate_pass_id=str(uuid.uuid4())
    verification=hashlib.sha256(f"{gate_pass_id}:{pass_number}:{account['id']}".encode()).hexdigest()[:16].upper()
    gate_pass=_insert("gate_passes",{"id":gate_pass_id,"pass_number":pass_number,"movement_scope":body.movement_scope,"department":body.department,"destination":body.destination.strip(),"purpose":body.purpose.strip(),"expected_out_at":_parse_datetime(body.expected_out_at).isoformat(),"expected_return_at":_parse_datetime(body.expected_return_at).isoformat() if body.expected_return_at else None,"finance_required":body.finance_required,"status":"pending","requested_by_account_id":account["id"],"requested_by":account["name"],"requested_at":_now(),"approved_at":None,"closed_at":None,"notes":body.notes,"verification_code":verification})
    for tool in selected: _insert("gate_pass_items",{"gate_pass_id":gate_pass_id,"tool_id":tool["id"]})
    roles=["hos"] if body.movement_scope=="internal" else ["hos","hod","security",*((["finance"] if body.finance_required else [])),"general_manager"]
    for order,role in enumerate(roles,1): _insert("gate_pass_approvals",{"id":str(uuid.uuid4()),"gate_pass_id":gate_pass_id,"step_order":order,"role":role,"status":"pending","signer_account_id":None,"signer_name":None,"signed_at":None,"comment":None,"signature_method":None})
    return _gate_pass_view(gate_pass)

@router.post("/gate-passes/{gate_pass_id}/decision")
def decide_gate_pass(gate_pass_id:str,body:GatePassDecision,account=Depends(_session)):
    gate_pass=_find("gate_passes","id",gate_pass_id)
    if not gate_pass: raise HTTPException(404,"Gate pass was not found.")
    if gate_pass.get("status")!="pending": raise HTTPException(409,"This gate pass is no longer awaiting approval.")
    approvals=sorted((item for item in _all("gate_pass_approvals") if item.get("gate_pass_id")==gate_pass_id),key=lambda item:item["step_order"])
    current=next((item for item in approvals if item.get("status")=="pending"),None)
    if not current: raise HTTPException(409,"This gate pass has no pending approval step.")
    if current["role"] not in (account.get("approval_roles") or []): raise HTTPException(403,f"The next signature must be provided by {current['role'].replace('_',' ').title()}.")
    method=_verify_signing_credential(account,body.credential)
    status="approved" if body.decision=="approve" else "rejected"
    _update("gate_pass_approvals",current["id"],{"status":status,"signer_account_id":account["id"],"signer_name":account["name"],"signed_at":_now(),"comment":body.comment,"signature_method":method})
    if body.decision=="reject": _update("gate_passes",gate_pass_id,{"status":"rejected"})
    elif all(item["id"]==current["id"] or item.get("status")=="approved" for item in approvals): _update("gate_passes",gate_pass_id,{"status":"approved","approved_at":_now()})
    return _gate_pass_view(_find("gate_passes","id",gate_pass_id) or gate_pass)

@router.get("/gate-passes/{gate_pass_id}/pdf")
def gate_pass_pdf(gate_pass_id:str,_=Depends(_session)):
    gate_pass=_find("gate_passes","id",gate_pass_id)
    if not gate_pass: raise HTTPException(404,"Gate pass was not found.")
    if gate_pass.get("status")!="approved": raise HTTPException(409,"The signed PDF is available only after every required approval.")
    data=_gate_pass_view(gate_pass)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except ImportError as exc: raise HTTPException(503,"PDF generation is unavailable.") from exc
    output=io.BytesIO(); document=SimpleDocTemplate(output,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=16*mm,bottomMargin=16*mm,title=data["pass_number"]); styles=getSampleStyleSheet()
    story=[Paragraph("Pickstone Peerless Mine",styles["Title"]),Paragraph("Portable Tools and Equipment Gate Pass",styles["Heading2"]),Spacer(1,5*mm)]
    details=[["Gate pass",data["pass_number"]],["Movement",data["movement_scope"].title()],["Department",data["department"]],["Destination",data["destination"]],["Purpose",data["purpose"]],["Expected out",data["expected_out_at"]],["Expected return",data.get("expected_return_at") or "Not specified"],["Requested by",data["requested_by"]]]
    story.append(Table(details,colWidths=[38*mm,120*mm],style=TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#D9D9D9")),("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("VALIGN",(0,0),(-1,-1),"TOP"),("BOTTOMPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6)]))); story.extend([Spacer(1,5*mm),Paragraph("Equipment",styles["Heading3"])])
    equipment=[["Register number","Description","Serial number"]]+[[item["register_number"],item["name"],item.get("serial_number") or "-"] for item in data["tools"]]
    story.append(Table(equipment,repeatRows=1,colWidths=[45*mm,78*mm,35*mm],style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E78")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#D9D9D9")),("VALIGN",(0,0),(-1,-1),"TOP"),("BOTTOMPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6)]))); story.extend([Spacer(1,5*mm),Paragraph("Approval record",styles["Heading3"])])
    signatures=[["Role","Signed by","Date and time","Method"]]+[[item["role"].replace("_"," ").title(),item.get("signer_name") or "-",item.get("signed_at") or "-",(item.get("signature_method") or "-").title()] for item in data["approvals"]]
    story.append(Table(signatures,repeatRows=1,colWidths=[38*mm,45*mm,52*mm,23*mm],style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E78")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#D9D9D9")),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6)])))
    story.extend([Spacer(1,6*mm),Paragraph(f"Verification code: <b>{data['verification_code']}</b>",styles["BodyText"]),Paragraph("Valid only when all listed approvals show as signed. Security must verify equipment register numbers against the physical items.",styles["BodyText"])])
    document.build(story)
    return Response(output.getvalue(),media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{data["pass_number"]}.pdf"'})
@router.get("/history")
def history(account=Depends(_session)):
    department=_department_scope(account)
    if department is None: return _all("history",True)
    tool_ids={tool["id"] for tool in _visible_records("tools",account)}
    return [row for row in _all("history",True) if row.get("tool_id") in tool_ids]

@router.post("/analytics/usage",status_code=202)
def capture_usage(body:UsageInput,account=Depends(_session)): return _insert("usage",{"id":str(uuid.uuid4()),"event":body.event,"detail":body.detail,"account_id":account["id"],"account_name":account["name"],"created_at":_now()})
@router.post("/analytics/errors",status_code=202)
def capture_error(body:ErrorInput,account=Depends(_session)): return _insert("errors",{"id":str(uuid.uuid4()),**body.model_dump(),"account_id":account["id"],"account_name":account["name"],"created_at":_now()})
@router.get("/analytics")
def analytics(_=Depends(_admin)):
    usage,errors,feedback=_all("usage",True),_all("errors",True),_all("feedback",True)
    if not _test_mode:
        for item in feedback:
            if item.get("audio_path"):
                try:
                    signed=supabase.storage.from_(FEEDBACK_BUCKET).create_signed_url(item["audio_path"],3600)
                    item["audio_url"]=signed.get("signedURL") or signed.get("signedUrl")
                except Exception:
                    item["audio_url"]=None
    return {"usage":usage,"errors":errors,"feedback":feedback,"totals":{"usage":len(usage),"errors":len(errors),"feedback":len(feedback)}}
@router.post("/feedback",status_code=201)
async def create_feedback(text:str=Form(default=""),audio:Optional[UploadFile]=File(default=None),account=Depends(_session)):
    if not text.strip() and audio is None: raise HTTPException(400,"Write a suggestion or attach an audio recording.")
    data=await audio.read() if audio else b""
    if len(data)>25*1024*1024: raise HTTPException(413,"Audio feedback must be smaller than 25 MB.")
    row_id,path=str(uuid.uuid4()),None
    content_type=(audio.content_type or "audio/webm").split(";",1)[0].lower() if audio else None
    if audio and content_type not in {"audio/webm","audio/ogg","audio/mp4","audio/mpeg","audio/wav","audio/x-wav"}: raise HTTPException(415,"Choose a WebM, OGG, MP4, MP3 or WAV audio recording.")
    if audio and not _test_mode:
        extensions={"audio/webm":"webm","audio/ogg":"ogg","audio/mp4":"m4a","audio/mpeg":"mp3","audio/wav":"wav","audio/x-wav":"wav"}; path=f"{account['id']}/{row_id}.{extensions[content_type]}"
        supabase.storage.from_(FEEDBACK_BUCKET).upload(path,data,{"content-type":content_type,"cache-control":"3600"})
    return _insert("feedback",{"id":row_id,"text":text.strip() or None,"audio_path":path,"audio_filename":audio.filename if audio else None,"audio_content_type":content_type,"audio_size":len(data),"account_id":account["id"],"account_name":account["name"],"created_at":_now()})

def _move(tool_id:str,body:MovementInput,account:dict[str,Any]):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    assigned_department=(account.get("department") or "").strip()
    if tool.get("department")!=assigned_department: raise HTTPException(403,f"This Issuer may record movements only for {assigned_department} equipment.")
    open_loan=bool(tool.get("custody"))
    if body.kind=="issue" and (tool.get("archived") or tool.get("status")!="available"): raise HTTPException(409,"This tool is not available to issue.")
    if body.kind!="issue" and not open_loan: raise HTTPException(409,"This tool has no open loan.")
    employee=None; override_note=None
    if body.kind in {"issue","transfer"}:
        if body.employee_id: employee=_find("employees","id",body.employee_id) or _find("employees","employee_number",body.employee_id)
        if not employee and body.employee_name: employee=next((row for row in _all("employees") if row["name"].lower()==body.employee_name.lower()),None)
        if not employee: raise HTTPException(404,"Choose an employee from the Tools employee register.")
        if employee.get("department")!=assigned_department: raise HTTPException(403,f"Choose an employee from {assigned_department}.")
        if not _is_eligible(employee,tool): raise HTTPException(409,"This employee is not currently trained, qualified and authorized for this equipment.")
        due_checks=_inspection_due(tool)
        if due_checks:
            names=", ".join(item.replace("_"," ") for item in due_checks)
            if not body.override_due_checks: raise HTTPException(409,f"The {names} check is overdue. Complete it, or issue anyway with a written reason (an Administrator or Issuer may override).")
            reason=(body.override_reason or "").strip()
            if len(reason)<5: raise HTTPException(422,"Give a reason of at least 5 characters to issue equipment with an overdue check.")
            override_note=f"overdue {names} check overridden: {reason}"
        if body.kind=="issue" and tool.get("pre_use_check_required",True) and not body.pre_use_check_completed: raise HTTPException(409,"Confirm the employee completed the pre-use inspection before issue.")
    now=_now(); after=dict(tool); location=(body.location or tool.get("storage_location") or "Unspecified").strip()
    if body.kind=="return":
        after.update(status="available" if body.condition=="Good" else "attention",storage_location=location,condition=body.condition or "Good",notes=body.notes,custody=None,calibration=body.calibration or tool.get("calibration"))
    elif body.kind=="extend":
        custody={**tool["custody"],"expected_return_at":body.expected_return_at,"notes":body.notes}; after.update(status="issued",custody=custody,notes=body.notes)
    else:
        old=tool.get("custody") or {}; custody={**old,"employee_id":employee["id"],"employee_number":employee["employee_number"],"employee_name":employee["name"],"department":employee["department"],"location":location,"job_reference":body.job_reference or old.get("job_reference"),"assigned_equipment":body.assigned_equipment or old.get("assigned_equipment",[]),"expected_return_at":body.expected_return_at or old.get("expected_return_at"),"issued_at":old.get("issued_at") or now,"original_due_at":old.get("original_due_at") or body.expected_return_at,"issued_by":account["name"],"notes":body.notes,"approval_ref":body.approval_ref}; after.update(status="issued",storage_location=location,custody=custody,notes=body.notes,approval_ref=body.approval_ref or tool.get("approval_ref"))
    targets=", ".join(body.assigned_equipment)
    detail={"issue":f"Issued to {employee['name']}"+(f" for {targets}" if targets else "") if employee else "Issued","return":f"Returned to {location}","transfer":f"Transferred to {employee['name']}" if employee else "Transferred","extend":f"Return date changed to {body.expected_return_at}"}[body.kind]
    if override_note: detail+=f" ({override_note})"
    result=_apply_change(tool,after,body.kind,account["name"],detail,employee,body.idempotency_key)
    if body.kind=="issue" and tool.get("pre_use_check_required",True):
        inspected=datetime.now(timezone.utc)
        _insert("inspections",{"id":str(uuid.uuid4()),"tool_id":tool_id,"inspection_type":"pre_use","outcome":"passed","inspected_at":inspected.isoformat(),"next_due_at":_default_next_due("pre_use",inspected,tool),"inspector_account_id":account["id"],"inspector_name":employee["name"] if employee else account["name"],"colour_code":"Green","condition":tool.get("condition"),"defects":None,"notes":"Pre-use check confirmed during issue","created_at":_now()})
    return {"tool":result["tool"],"change_id":result["change_id"]}

@router.post("/tools/{tool_id}/commands")
def move_tool(tool_id:str,body:MovementInput,account=Depends(_issuer)): return _move(tool_id,body,account)
@router.post("/tools/{tool_id}/issue")
def issue_tool(tool_id:str,body:IssueInput,account=Depends(_issuer)):
    return _move(tool_id,MovementInput(kind="issue",employee_id=body.employee_id,location=body.location,expected_return_at=body.expected_return_at,job_reference=body.job_reference,assigned_equipment=body.assigned_equipment,notes=body.notes,pre_use_check_completed=True,override_due_checks=body.override_due_checks,override_reason=body.override_reason),account)
@router.post("/tools/{tool_id}/return")
def return_tool(tool_id:str,body:ReturnInput,account=Depends(_issuer)):
    return _move(tool_id,MovementInput(kind="return",location=body.location,condition=body.condition,notes=body.notes),account)

@router.post("/tools/{tool_id}/evidence",status_code=201)
async def upload_evidence(tool_id:str,files:list[UploadFile]=File(...),account=Depends(_operator)):
    tool=_find("tools","id",tool_id)
    if not tool: raise HTTPException(404,"Tool was not found.")
    _ensure_managed_department(account,tool.get("department"))
    allowed={"image/jpeg","image/png","image/webp","image/gif","image/avif","application/pdf"}; saved=[]
    for upload in files:
        content=await upload.read(); content_type=upload.content_type or "application/octet-stream"
        if content_type not in allowed: raise HTTPException(415,"Choose a JPG, PNG, WebP, GIF, AVIF or PDF file.")
        if not content: raise HTTPException(422,"An attachment was empty.")
        evidence_id=str(uuid.uuid4()); extension=(upload.filename or "file").rsplit(".",1)[-1]; path=f"{tool_id}/{evidence_id}.{extension}"
        if not _test_mode: supabase.storage.from_(EVIDENCE_BUCKET).upload(path,content,{"content-type":content_type})
        row={"id":evidence_id,"tool_id":tool_id,"storage_path":path,"original_name":upload.filename or "Attachment","content_type":content_type,"size_bytes":len(content),"uploaded_by":account["name"],"uploaded_at":_now(),"removed_at":None}
        try: saved.append(_insert("evidence",row))
        except Exception:
            if not _test_mode:
                try: supabase.storage.from_(EVIDENCE_BUCKET).remove([path])
                except Exception: pass
            raise
    _apply_change(tool,tool,"attachments",account["name"],f"Added {len(saved)} attachment(s)")
    return saved

@router.get("/source-registers")
def list_source_registers(account=Depends(_session)):
    saved=_visible_records("source_registers",account,True)
    if not _test_mode:
        for item in saved:
            try:
                signed=supabase.storage.from_(SOURCE_REGISTERS_BUCKET).create_signed_url(item["storage_path"],3600)
                item["url"]=signed.get("signedURL") or signed.get("signedUrl")
            except Exception: item["url"]=None
    return saved

@router.post("/source-registers",status_code=201)
async def upload_source_register(department:str=Form(...),notes:str=Form(default=""),file:UploadFile=File(...),account=Depends(_session)):
    content=await file.read(); filename=file.filename or "Source register"; extension=filename.lower().rsplit(".",1)[-1] if "." in filename else ""
    allowed_extensions={"pdf","xlsx","xlsm","xls","csv","docx","doc","ods","odt","jpg","jpeg","png","webp"}
    if extension not in allowed_extensions: raise HTTPException(415,"Choose a PDF, Excel, CSV, Word, OpenDocument or image file.")
    if not content: raise HTTPException(422,"The source register was empty.")
    if len(content)>50*1024*1024: raise HTTPException(413,"Source registers must be smaller than 50 MB.")
    register_id=str(uuid.uuid4()); path=f"{datetime.now(timezone.utc):%Y/%m}/{register_id}.{extension}"; content_type=file.content_type or "application/octet-stream"
    if not _test_mode: supabase.storage.from_(SOURCE_REGISTERS_BUCKET).upload(path,content,{"content-type":content_type})
    row={"id":register_id,"department":department.strip() or "Unassigned","notes":notes.strip() or None,"storage_path":path,"original_name":filename,"content_type":content_type,"size_bytes":len(content),"uploaded_by_account_id":account["id"],"uploaded_by":account["name"],"uploaded_at":_now()}
    try: return _insert("source_registers",row)
    except Exception:
        if not _test_mode:
            try: supabase.storage.from_(SOURCE_REGISTERS_BUCKET).remove([path])
            except Exception: pass
        raise

@router.get("/changes")
def changes(account=Depends(_session)):
    own=[row for row in _all("changes",True) if row.get("actor_name")==account["name"]]
    return {"undo":next((row for row in own if not row.get("undone_at")),None),"redo":next((row for row in own if row.get("undone_at")),None)}

@router.post("/changes/{direction}")
def restore_change(direction:Literal["undo","redo"],account=Depends(_operator)):
    own=[row for row in _all("changes",True) if row.get("actor_name")==account["name"]]
    change=next((row for row in own if bool(row.get("undone_at"))==(direction=="redo")),None)
    if not change: raise HTTPException(409,f"Nothing to {direction}.")
    if _test_mode:
        desired=change["after_state"] if direction=="redo" else change["before_state"]; _update("tools",change["tool_id"],desired)
        _update("changes",change["id"],{"undone_at":None if direction=="redo" else _now(),"undone_by":None if direction=="redo" else account["name"]})
        return {"tool":desired,"change_id":change["id"],"redo":direction=="redo"}
    response=supabase.rpc("tools_workspace_restore_change",{"change_key":change["id"],"actor":account["name"],"redo":direction=="redo"}).execute()
    return getattr(response,"data",None)

ALIASES={"tool id":"register_number","equipment id":"register_number","register number":"register_number","employee number":"employee_number","employee no":"employee_number","name":"name","tool":"name","equipment":"name","make/model":"make_model","model":"make_model","serial number":"serial_number","serial":"serial_number","location":"storage_location","storage location":"storage_location","department":"department","job title":"job_title","category":"category"}
def _rows_from_file(content:bytes,filename:str):
    suffix=filename.lower().rsplit(".",1)[-1]
    if suffix=="csv": return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    if suffix in {"xlsx","xlsm"}:
        book=load_workbook(io.BytesIO(content),read_only=True,data_only=True); return [list(row) for row in book.active.iter_rows(values_only=True)]
    if suffix=="docx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive: xml=archive.read("word/document.xml")
        root=ElementTree.fromstring(xml); ns={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return [["".join(cell.itertext()).strip() for cell in row.findall("w:tc",ns)] for row in root.findall(".//w:tr",ns)]
    raise HTTPException(415,"Choose an XLSX, XLSM, CSV or DOCX file.")
@router.post("/imports/preview")
async def preview_import(target:Literal["equipment","employees"]=Form(...),file:UploadFile=File(...),_=Depends(_session)):
    content=await file.read()
    if len(content)>20*1024*1024: raise HTTPException(413,"Import files must be smaller than 20 MB.")
    raw=[row for row in _rows_from_file(content,file.filename or "") if any(str(value or "").strip() for value in row)]
    if len(raw)<2: raise HTTPException(422,"The file needs a header row and at least one data row.")
    headers=[ALIASES.get(str(value or "").strip().lower(),str(value or "").strip().lower().replace(" ","_")) for value in raw[0]]
    required={"equipment":{"register_number","name","storage_location"},"employees":{"employee_number","name","department"}}[target]
    mapped=[{headers[i]:str(value or "").strip() for i,value in enumerate(row) if i<len(headers)} for row in raw[1:501]]
    preview=[{"row":i+2,"data":item,"errors":[f"Missing {field.replace('_',' ')}" for field in required if not item.get(field)]} for i,item in enumerate(mapped)]
    return {"target":target,"columns":headers,"rows":preview,"valid":sum(not row["errors"] for row in preview),"invalid":sum(bool(row["errors"]) for row in preview),"truncated":len(raw)>501}

@router.post("/imports/commit")
def commit_import(body:ImportCommit,account=Depends(_operator)):
    accepted=[]; rejected=[]
    for index,item in enumerate(body.rows,1):
        try:
            if body.target=="employees":
                model=EmployeeInput.model_validate(item)
                _ensure_managed_department(account,model.department)
                if _find("employees","employee_number",model.employee_number): raise ValueError("Employee number already exists")
                accepted.append(_insert("employees",{"id":str(uuid.uuid4()),**model.model_dump(),"active":True,"created_by":account["name"],"created_at":_now()}))
            else:
                model=ToolInput.model_validate(item)
                _ensure_managed_department(account,model.department)
                if _find("tools","register_number",model.register_number): raise ValueError("Register number already exists")
                accepted.append(_apply_change(None,{"id":str(uuid.uuid4()),**model.model_dump(),"status":"available","custody":None},"imported",account["name"],"Imported into the register")["tool"])
        except Exception as exc:
            rejected.append({"row":index,"error":str(exc)})
    return {"accepted":accepted,"rejected":rejected,"accepted_count":len(accepted),"rejected_count":len(rejected)}

def _reset_for_tests():
    global _test_mode; _test_mode=True
    for values in _memory.values(): values.clear()
