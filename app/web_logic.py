from __future__ import annotations

import os
import shutil
import uuid
import csv
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Dict, Any

import pandas as pd

from . import eps_bulk_core
from .doc2us_live import submit_doc2us_queue_live

EDITABLE_COLUMNS = [
    'status', 'skip_reason', 'patient_name', 'patient_ic', 'mobile', 'email', 'item_name', 'active_ingredients', 'indication',
    'diagnosis_search', 'doc2us_icd_code', 'doc2us_indication', 'route', 'dose', 'dose_unit', 'frequency', 'duration_days', 'prescribed_amount',
    'prescribed_unit', 'drug_remark', 'questionnaire_mode', 'bp', 'hr', 'glucose', 'last_appointment_date',
    'next_appointment_date', 'follow_up_under', 'referred_by', 'pharmacist_reg_no', 'screening_remarks'
]
NUMERIC_COLUMNS = {'qty', 'duration_days', 'prescribed_amount'}
DOC2US_DEPLOY_COLUMNS = [
    'status', 'patient_name', 'patient_ic', 'mobile', 'email',
    'item_name', 'active_ingredients', 'indication', 'doc2us_icd_code', 'doc2us_indication', 'diagnosis_search',
    'route', 'dose', 'dose_unit', 'frequency', 'duration_days', 'prescribed_amount', 'prescribed_unit', 'drug_remark',
    'questionnaire_mode', 'bp', 'hr', 'glucose', 'last_appointment_date', 'next_appointment_date',
    'follow_up_under', 'referred_by', 'pharmacist_reg_no', 'screening_remarks'
]

MAX_STAFF_ACCOUNTS = 7

DEFAULT_REVIEW_VALUES = {
    'route': 'Oral',
    'dose': '1',
    'dose_unit': 'tab(s)/cap(s)',
    'frequency': 'Once daily',
    'duration_days': 7,
    'prescribed_unit': 'tab(s)/cap(s)',
    'questionnaire_mode': 'LTM',
    'bp': '120/80',
    'hr': '75',
    'glucose': '6.0',
    'screening_remarks': 'refill medication',
    'drug_remark': 'refill medication',
}

INDICATION_KEYWORDS = [
    (('diabetes', 'diabetic', 'metformin', 'gliclazide', 'insulin', 'sitagliptin', 'empagliflozin', 'dapagliflozin'), '5A11'),
    (('cholesterol', 'hyperchol', 'atorvastatin', 'rosuvastatin', 'simvastatin', 'ezetimibe', 'fenofibrate'), '5C80.0Z'),
    (('gout', 'allopurinol', 'febuxostat', 'colchicine'), 'FA25'),
    (('pain', 'paracetamol', 'celecoxib', 'etoricoxib', 'diclofenac', 'tramadol', 'pregabalin', 'gabapentin'), 'MG3Z'),
    (('thyroid', 'levothyroxine', 'carbimazole'), '5A0Z'),
    (('glaucoma', 'latanoprost', 'timolol'), '9C61.Z'),
    (('hepatitis b', 'tenofovir', 'entecavir'), '1E51.0Z'),
    (('heart', 'angina', 'bisoprolol', 'carvedilol', 'isosorbide'), 'BA6Z'),
    (('hypertension', 'amlodipine', 'perindopril', 'losartan', 'valsartan', 'telmisartan', 'irbesartan', 'hydrochlorothiazide'), 'BA00.Z'),
]


def _doc2us_desc_by_code() -> dict[str, str]:
    return {code: desc for code, desc in load_doc2us_indication_options()}


def _choose_default_indication(row: pd.Series) -> tuple[str, str]:
    existing_code = str(row.get('doc2us_icd_code', '') or '').strip()
    existing_text = str(row.get('doc2us_indication', '') or '').strip()
    desc_by_code = _doc2us_desc_by_code()
    if existing_code and existing_code in desc_by_code:
        return existing_code, existing_text or desc_by_code[existing_code]
    haystack = ' '.join(str(row.get(c, '') or '') for c in ['indication', 'diagnosis_search', 'item_name', 'active_ingredients']).lower()
    for keywords, code in INDICATION_KEYWORDS:
        if any(k in haystack for k in keywords):
            return code, desc_by_code.get(code, '')
    fallback_code = os.environ.get('EPS_DEFAULT_DOC2US_ICD_CODE', 'BA00.Z').strip() or 'BA00.Z'
    return fallback_code, desc_by_code.get(fallback_code, 'Essential hypertension, unspecified')


def apply_review_defaults(plan: pd.DataFrame) -> pd.DataFrame:
    """Prefill blank review/deploy fields so pharmacists edit from sensible defaults instead of blanks."""
    plan = plan.copy()
    for col in EDITABLE_COLUMNS:
        if col not in plan.columns:
            plan[col] = ''
    for idx, row in plan.iterrows():
        code, desc = _choose_default_indication(row)
        if _blank(plan.at[idx, 'doc2us_icd_code']):
            plan.at[idx, 'doc2us_icd_code'] = code
        if _blank(plan.at[idx, 'doc2us_indication']):
            plan.at[idx, 'doc2us_indication'] = desc
        if _blank(plan.at[idx, 'diagnosis_search']):
            plan.at[idx, 'diagnosis_search'] = desc or str(plan.at[idx, 'indication'] or '')
        for col, val in DEFAULT_REVIEW_VALUES.items():
            if col == 'duration_days':
                if not _positive_number(plan.at[idx, col]):
                    plan.at[idx, col] = val
            elif _blank(plan.at[idx, col]):
                plan.at[idx, col] = val
        if not _positive_number(plan.at[idx, 'prescribed_amount']):
            qty = row.get('qty', '')
            plan.at[idx, 'prescribed_amount'] = int(float(qty)) if _positive_number(qty) else 7
        if _blank(plan.at[idx, 'active_ingredients']):
            plan.at[idx, 'active_ingredients'] = str(row.get('item_name', '') or '')
    return plan

REQUIRED_DOC2US_FIELDS = {
    'patient_name': 'Patient name is required',
    'patient_ic': 'Patient IC is required',
    'mobile': 'Mobile number is required',
    'item_name': 'Medication item name is required',
    'active_ingredients': 'Active ingredient must be reviewed',
    'doc2us_icd_code': 'Doc2Us indication dropdown must be selected',
    'doc2us_indication': 'Doc2Us indication text must be selected',
    'route': 'Route is required',
    'dose': 'Dose is required',
    'dose_unit': 'Dose unit is required',
    'frequency': 'Frequency is required',
    'duration_days': 'Duration days must be more than 0',
    'prescribed_amount': 'Prescribed amount must be more than 0',
    'prescribed_unit': 'Prescribed unit is required',
    'questionnaire_mode': 'Minor Ailment / LTM mode is required',
    'bp': 'BP is required',
    'next_appointment_date': 'Next appointment date is required',
    'follow_up_under': 'Follow up under is required',
    'referred_by': 'Referred by is required',
    'pharmacist_reg_no': 'Pharmacist registration number is required',
    'screening_remarks': 'Screening remarks are required',
}



def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _deployment_progress_path(jobs_dir: str | Path, job_id: str) -> Path:
    return _safe_job_dir(jobs_dir, job_id) / 'doc2us_deployment_progress.json'


def write_deployment_progress(jobs_dir: str | Path, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
    progress = dict(data)
    progress['job_id'] = job_id
    progress['updated_at'] = _utc_now()
    path = _deployment_progress_path(jobs_dir, job_id)
    path.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding='utf-8')
    return progress


def read_deployment_progress(jobs_dir: str | Path, job_id: str) -> dict[str, Any]:
    path = _deployment_progress_path(jobs_dir, job_id)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'job_id': job_id, 'status': 'not_started', 'event': 'not_started', 'results': [], 'updated_at': ''}

def doc2us_deploy_columns() -> list[str]:
    return list(DOC2US_DEPLOY_COLUMNS)


def _blank(value: object) -> bool:
    return pd.isna(value) or str(value).strip() == ''


def _positive_number(value: object) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def validate_doc2us_ready_row(row: pd.Series) -> list[str]:
    issues: list[str] = []
    for col, message in REQUIRED_DOC2US_FIELDS.items():
        if col in {'duration_days', 'prescribed_amount'}:
            if not _positive_number(row.get(col)):
                issues.append(message)
        elif _blank(row.get(col)):
            issues.append(message)
    mode = str(row.get('questionnaire_mode', '')).strip().upper()
    if mode and mode not in {'LTM', 'MINOR AILMENT', 'MINOR_AILMENT'}:
        issues.append('Questionnaire mode must be LTM or Minor Ailment')
    bp = str(row.get('bp', '')).strip()
    if bp and '/' not in bp:
        issues.append('BP must be in systolic/diastolic format')
    return issues


def load_doc2us_indication_options() -> list[tuple[str, str]]:
    """Doc2Us default EPS indication dropdown options harvested from /Api/Icd/GetDefaultIcdsForEPS.

    Kept as a local controlled list so the app can work offline and pharmacists can review the AI-preselected choice.
    """
    path = Path(__file__).resolve().parents[1] / 'data' / 'doc2us_default_indications.csv'
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [(r['icd_code'], r['icd_description']) for r in csv.DictReader(f)]


def render_indication_select(row_idx: int, selected_code: str, selected_text: str = '') -> str:
    selected_code = str(selected_code or '').strip()
    selected_text = str(selected_text or '').strip()
    options = ['<option value="">-- pharmacist choose Doc2Us indication --</option>']
    found = False
    for code, desc in load_doc2us_indication_options():
        is_selected = code == selected_code or (not selected_code and desc == selected_text)
        if is_selected:
            found = True
        selected_attr = 'selected' if is_selected else ''
        options.append(
            f'<option value="{escape(code)}" data-desc="{escape(desc)}" {selected_attr}>'
            f'{escape(code)} - {escape(desc)}</option>'
        )
    if selected_code and not found:
        options.insert(1, f'<option value="{escape(selected_code)}" data-desc="{escape(selected_text)}" selected>{escape(selected_code)} - {escape(selected_text)}</option>')
    hidden = f'<input type="hidden" name="row_{row_idx}_doc2us_indication" value="{escape(selected_text)}">'
    html_options = ''.join(options)
    return f'<select name="row_{row_idx}_doc2us_icd_code">{html_options}</select>{hidden}'


def get_staff_accounts() -> list[dict[str, str]]:
    """Return staff app-login + assigned Doc2Us-login accounts.

    Preferred Render env var: EPS_STAFF_ACCOUNTS_JSON
    [
      {"staff_label": "Staff A", "app_email": "staffa@example.com", "app_password": "...",
       "doc2us_email": "doc2us-a@example.com", "doc2us_password": "..."}
    ]

    Backward compatible fallback uses EPS_ALLOWED_EMAIL/PASSWORD and DOC2US_EMAIL/PASSWORD.
    """
    raw = os.environ.get('EPS_STAFF_ACCOUNTS_JSON', '').strip()
    accounts: list[dict[str, str]] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                parsed = parsed[:MAX_STAFF_ACCOUNTS]
                for i, item in enumerate(parsed):
                    if not isinstance(item, dict):
                        continue
                    app_email = str(item.get('app_email') or item.get('email') or '').strip().lower()
                    app_password = str(item.get('app_password') or item.get('password') or '')
                    doc_email = str(item.get('doc2us_email') or '').strip()
                    doc_password = str(item.get('doc2us_password') or '')
                    if app_email and app_password and doc_email and doc_password:
                        accounts.append({
                            'id': str(item.get('id') or app_email).strip().lower(),
                            'staff_label': str(item.get('staff_label') or item.get('label') or app_email).strip(),
                            'app_email': app_email,
                            'app_password': app_password,
                            'doc2us_email': doc_email,
                            'doc2us_password': doc_password,
                        })
        except json.JSONDecodeError:
            pass
    if accounts:
        return accounts[:MAX_STAFF_ACCOUNTS]
    app_email = os.environ.get('EPS_ALLOWED_EMAIL', 'qsbjc1@alpropharmacy.com').strip().lower()
    app_password = os.environ.get('EPS_ALLOWED_PASSWORD', 'Alpro-123')
    return [{
        'id': app_email,
        'staff_label': os.environ.get('EPS_STAFF_LABEL', app_email),
        'app_email': app_email,
        'app_password': app_password,
        'doc2us_email': os.environ.get('DOC2US_EMAIL') or app_email,
        'doc2us_password': os.environ.get('DOC2US_PASSWORD') or app_password,
    }]


def _allowed_logins() -> dict[str, str]:
    return {a['app_email'].lower(): a['app_password'] for a in get_staff_accounts()}


def get_staff_account_by_app_email(app_email: str) -> dict[str, str] | None:
    wanted = (app_email or '').strip().lower()
    for account in get_staff_accounts():
        if account['app_email'].lower() == wanted:
            return account
    return None


def authenticate(email: str, password: str) -> bool:
    return _allowed_logins().get((email or '').strip().lower()) == (password or '')


def make_job_id() -> str:
    return uuid.uuid4().hex


def _safe_job_dir(jobs_dir: str | Path, job_id: str) -> Path:
    if not job_id or not str(job_id).isalnum():
        raise ValueError('Invalid job id')
    job_dir = Path(jobs_dir) / job_id
    if not job_dir.exists():
        raise FileNotFoundError('Job not found')
    return job_dir


def _plan_path(job_dir: Path) -> Path:
    files = list(job_dir.glob('*_EPS_PLAN.xlsx'))
    if not files:
        raise FileNotFoundError('Plan workbook not found')
    return files[0]


def _write_plan_workbook(plan: pd.DataFrame, output_path: Path) -> None:
    plan = plan.copy()
    if 'status' not in plan.columns:
        plan['status'] = 'REVIEW'
    if 'medication_class' not in plan.columns:
        plan['medication_class'] = ''
    with pd.ExcelWriter(output_path, engine='openpyxl') as w:
        plan.to_excel(w, index=False, sheet_name='EPS_PLAN')
        plan.groupby(['status', 'medication_class'], dropna=False).size().reset_index(name='count').to_excel(
            w, index=False, sheet_name='SUMMARY'
        )


def _job_summary(job_id: str, output_path: Path, plan: pd.DataFrame) -> Dict[str, Any]:
    counts = {str(k): int(v) for k, v in plan['status'].value_counts(dropna=False).to_dict().items()}
    preview_cols = ['status','skip_reason','patient_name','patient_ic','item_name','qty','medication_class','indication','frequency','duration_days','prescribed_amount','next_appointment_date']
    preview = plan[[c for c in preview_cols if c in plan.columns]].fillna('').to_dict(orient='records')
    return {
        'job_id': job_id,
        'output_path': str(output_path),
        'download_name': output_path.name,
        'counts': counts,
        'preview': preview,
    }


def load_plan(jobs_dir: str | Path, job_id: str) -> pd.DataFrame:
    job_dir = _safe_job_dir(jobs_dir, job_id)
    return pd.read_excel(_plan_path(job_dir), sheet_name='EPS_PLAN', dtype=object)


def process_upload(
    file_bytes: bytes,
    original_filename: str,
    pharmacist_name: str,
    reg_no: str,
    apply_date: str,
    jobs_dir: str | Path,
) -> Dict[str, Any]:
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = make_job_id()
    job_dir = jobs_dir / job_id
    job_dir.mkdir()
    safe_name = Path(original_filename or 'upload.xlsx').name
    input_path = job_dir / safe_name
    input_path.write_bytes(file_bytes)

    # The core module expects its medication_rules.csv beside itself. Use shared web data rules.
    web_rules = Path(__file__).resolve().parents[1] / 'data' / 'medication_rules.csv'
    core_rules = Path(eps_bulk_core.__file__).resolve().parent / 'medication_rules.csv'
    if web_rules.exists() and not core_rules.exists():
        shutil.copy2(web_rules, core_rules)

    plan = eps_bulk_core.make_plan(str(input_path), pharmacist_name, reg_no, pd.to_datetime(apply_date).date())
    plan = apply_review_defaults(plan)
    output_path = job_dir / f'{input_path.stem}_EPS_PLAN.xlsx'
    _write_plan_workbook(plan, output_path)
    summary = _job_summary(job_id, output_path, plan)
    summary['input_path'] = str(input_path)
    return summary


def save_edited_plan(jobs_dir: str | Path, job_id: str, edits: dict[str, dict[str, str]]) -> Dict[str, Any]:
    job_dir = _safe_job_dir(jobs_dir, job_id)
    output_path = _plan_path(job_dir)
    plan = pd.read_excel(output_path, sheet_name='EPS_PLAN', dtype=object)
    for col in EDITABLE_COLUMNS:
        if col not in plan.columns:
            plan[col] = ''
    text_columns = [c for c in EDITABLE_COLUMNS if c not in NUMERIC_COLUMNS and c in plan.columns]
    for col in text_columns:
        plan[col] = plan[col].astype(object)
    for row_key, values in edits.items():
        if not str(row_key).isdigit():
            continue
        idx = int(row_key)
        if idx not in plan.index:
            continue
        for col in EDITABLE_COLUMNS:
            if col not in values or col not in plan.columns:
                continue
            val = values[col]
            if col == 'status':
                val = str(val or '').strip().upper()
                if val not in {'READY', 'REVIEW', 'OMIT'}:
                    val = 'REVIEW'
            elif col in NUMERIC_COLUMNS:
                try:
                    val = int(float(val)) if str(val).strip() != '' else 0
                except ValueError:
                    val = 0
            else:
                val = str(val or '').strip()
            plan.at[idx, col] = val
    plan = apply_review_defaults(plan)
    _write_plan_workbook(plan, output_path)
    return _job_summary(job_id, output_path, plan)


def _load_doc2us_queue(path: str | Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name='DOC2US_READY_UPLOAD')
    except ValueError:
        return pd.read_excel(path)


def _normalise_deploy_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in DOC2US_DEPLOY_COLUMNS:
        if col not in out.columns:
            out[col] = ''
    out = out[DOC2US_DEPLOY_COLUMNS]
    out['status'] = out['status'].fillna('READY').astype(str).str.upper().replace({'': 'READY'})
    return out


def import_edited_doc2us_queue(
    jobs_dir: str | Path,
    job_id: str,
    file_bytes: bytes,
    original_filename: str,
) -> Dict[str, Any]:
    job_dir = _safe_job_dir(jobs_dir, job_id)
    safe_name = Path(original_filename or 'edited_doc2us_queue.xlsx').name
    import_path = job_dir / f'imported_{safe_name}'
    import_path.write_bytes(file_bytes)
    imported = _normalise_deploy_frame(_load_doc2us_queue(import_path))
    plan_path = _plan_path(job_dir)
    plan = pd.read_excel(plan_path, sheet_name='EPS_PLAN')
    imported_count = 0
    invalid_count = 0
    for _, row in imported.iterrows():
        patient_ic = str(row.get('patient_ic', '')).strip()
        item_name = str(row.get('item_name', '')).strip()
        if not patient_ic or not item_name:
            invalid_count += 1
            continue
        matches = plan[(plan['patient_ic'].astype(str).str.strip() == patient_ic) & (plan['item_name'].astype(str).str.strip() == item_name)]
        if matches.empty:
            new_row = {col: '' for col in plan.columns}
            for col in imported.columns:
                if col in new_row:
                    new_row[col] = row.get(col, '')
            new_row['skip_reason'] = ''
            plan = pd.concat([plan, pd.DataFrame([new_row])], ignore_index=True)
            target_idx = plan.index[-1]
        else:
            target_idx = matches.index[0]
            for col in imported.columns:
                if col in plan.columns:
                    plan.at[target_idx, col] = row.get(col, '')
        issues = validate_doc2us_ready_row(plan.loc[target_idx])
        if issues:
            invalid_count += 1
            plan.at[target_idx, 'status'] = 'REVIEW'
            plan.at[target_idx, 'skip_reason'] = '; '.join(issues)
        else:
            plan.at[target_idx, 'status'] = 'READY'
            plan.at[target_idx, 'skip_reason'] = ''
        imported_count += 1
    plan = apply_review_defaults(plan)
    _write_plan_workbook(plan, plan_path)
    package = create_submit_package(jobs_dir, job_id)
    return {
        'job_id': job_id,
        'import_path': str(import_path),
        'imported_count': int(imported_count),
        'invalid_count': int(invalid_count),
        'queue_path': package['queue_path'],
        'ready_count': package['count'],
    }


def deploy_doc2us_ready_rows(jobs_dir: str | Path, job_id: str, live_submit: bool = False, app_email: str = "") -> Dict[str, Any]:
    """Prepare or execute the end-phase Doc2Us deployment result.

    One READY medication row equals one prescription request. live_submit=True runs
    the Playwright browser automation against Doc2Us EPS and records per-row results.
    """
    job_dir = _safe_job_dir(jobs_dir, job_id)
    package = create_submit_package(jobs_dir, job_id)
    manifest = build_doc2us_automation_manifest(package['queue_path'], dry_run=not live_submit)
    manifest['live_submit_enabled'] = bool(live_submit)
    manifest['prescription_count'] = int(package['count'])
    manifest['medication_count'] = int(package['count'])
    manifest['invalid_count'] = int(package.get('invalid_count', 0))
    staff_account = get_staff_account_by_app_email(app_email) if app_email else (get_staff_accounts()[0] if get_staff_accounts() else None)
    if staff_account:
        manifest['staff_label'] = staff_account.get('staff_label', '')
        manifest['doc2us_account_email'] = staff_account.get('doc2us_email', '')
    if live_submit and int(package['count']) > 0:
        try:
            def _progress(event: dict[str, Any]) -> None:
                base = {'status': 'running'}
                if staff_account:
                    base.update({'staff_label': staff_account.get('staff_label', ''), 'doc2us_account_email': staff_account.get('doc2us_email', '')})
                write_deployment_progress(jobs_dir, job_id, {**base, **event})

            live = submit_doc2us_queue_live(
                package['queue_path'],
                screenshot_dir=job_dir / 'doc2us_live_screenshots',
                final_submit=True,
                progress_callback=_progress,
                login_email=staff_account.get('doc2us_email') if staff_account else None,
                login_password=staff_account.get('doc2us_password') if staff_account else None,
                account_label=staff_account.get('staff_label', '') if staff_account else '',
            )
        except Exception as exc:
            live = {
                'submitted_count': 0,
                'failed_count': int(package.get('count', 0)),
                'results': [
                    {
                        'row': '',
                        'patient_ic': '',
                        'medication': '',
                        'status': 'FAILED',
                        'error': f'Website deploy runner crashed before/while starting Doc2Us browser automation: {type(exc).__name__}: {exc}',
                    }
                ],
                'screenshot_dir': str(job_dir / 'doc2us_live_screenshots'),
            }
        manifest.update(live)
        manifest['batch_mode'] = True
        manifest['login_count'] = 1
        manifest['patient_group_count'] = int(_normalise_deploy_frame(_load_doc2us_queue(package['queue_path']))['patient_ic'].fillna('').astype(str).str.strip().nunique())
        manifest['dry_run'] = False
        verified = sum(1 for r in live.get('results', []) if str(r.get('status')) == 'VERIFIED')
        manifest['verified_count'] = int(verified)
        manifest['notification'] = (
            f"Batch live Doc2Us submission finished with one Doc2Us login: {verified} medication record(s) verified, "
            f"{int(live.get('failed_count', 0))} failed, across {manifest['patient_group_count']} patient group(s). "
            f"{int(package.get('invalid_count', 0))} invalid READY row(s) moved back to REVIEW."
        )
        write_deployment_progress(jobs_dir, job_id, {'status': 'finished', 'event': 'finished', **manifest})
    else:
        manifest['notification'] = (
            f"Doc2Us deployment prepared: {int(package['count'])} medication(s) / "
            f"{int(package['count'])} prescription request(s). "
            f"{int(package.get('invalid_count', 0))} invalid READY row(s) moved back to REVIEW."
        )
    manifest_path = Path(jobs_dir) / job_id / 'doc2us_deployment_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    return manifest


def build_doc2us_automation_manifest(queue_path: str | Path, dry_run: bool = True) -> Dict[str, Any]:
    queue_path = Path(queue_path)
    queue = _normalise_deploy_frame(_load_doc2us_queue(queue_path))
    steps: list[dict[str, Any]] = [{'action': 'login_doc2us_eps', 'url': 'https://eps.doc2us.com/login'}]
    for idx, row in queue.iterrows():
        patient = str(row.get('patient_name', '')).strip()
        ic = str(row.get('patient_ic', '')).strip()
        med = str(row.get('item_name', '')).strip()
        steps.extend([
            {'row': int(idx), 'patient_name': patient, 'patient_ic': ic, 'action': 'search_patient_by_ic'},
            {'row': int(idx), 'patient_name': patient, 'patient_ic': ic, 'action': 'register_patient_if_missing', 'manual_review_required': True},
            {'row': int(idx), 'patient_name': patient, 'patient_ic': ic, 'medication': med, 'action': 'fill_medication_record'},
            {'row': int(idx), 'patient_name': patient, 'patient_ic': ic, 'medication': med, 'action': 'request_prescription_requires_manual_confirm', 'manual_confirm_button_required': True},
        ])
    return {
        'queue_path': str(queue_path),
        'dry_run': bool(dry_run),
        'live_submit_enabled': False if dry_run else False,
        'row_count': int(len(queue)),
        'safety_note': 'Automation may fill/import data, but live prescription request requires pharmacist confirmation and Doctor approval.',
        'steps': steps,
    }


def create_submit_package(jobs_dir: str | Path, job_id: str) -> Dict[str, Any]:
    job_dir = _safe_job_dir(jobs_dir, job_id)
    output_path = _plan_path(job_dir)
    plan = pd.read_excel(output_path, sheet_name='EPS_PLAN')
    plan = apply_review_defaults(plan)
    _write_plan_workbook(plan, output_path)
    invalid_count = 0
    for idx, row in plan[plan['status'].astype(str).str.upper() == 'READY'].iterrows():
        issues = validate_doc2us_ready_row(row)
        if issues:
            invalid_count += 1
            plan.at[idx, 'status'] = 'REVIEW'
            existing_reason = str(plan.at[idx, 'skip_reason'] or '').strip()
            issue_text = '; '.join(issues)
            plan.at[idx, 'skip_reason'] = f'{existing_reason}; {issue_text}' if existing_reason else issue_text
    if invalid_count:
        _write_plan_workbook(plan, output_path)
    ready = plan[plan['status'].astype(str).str.upper() == 'READY'].copy()
    for col in DOC2US_DEPLOY_COLUMNS:
        if col not in ready.columns:
            ready[col] = ''
    ready = ready[DOC2US_DEPLOY_COLUMNS]
    queue_path = job_dir / f'{output_path.stem}_DOC2US_READY_QUEUE.xlsx'
    with pd.ExcelWriter(queue_path, engine='openpyxl') as w:
        ready.to_excel(w, index=False, sheet_name='DOC2US_READY_UPLOAD')
        pd.DataFrame({
            'step': [
                '1. Pharmacist checks every row in DOC2US_READY_UPLOAD.',
                '2. Open Doc2Us EPS and create medication record for each patient.',
                '3. Use active ingredient + Doc2Us indication fields to fill the diagnosis dropdown.',
                '4. Submit only after final pharmacist confirmation; Doctor approval remains required.'
            ]
        }).to_excel(w, index=False, sheet_name='DEPLOY_CHECKLIST')
    return {'job_id': job_id, 'queue_path': str(queue_path), 'count': int(len(ready)), 'invalid_count': int(invalid_count)}
