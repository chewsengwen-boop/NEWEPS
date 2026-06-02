from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from html import escape

from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .web_logic import authenticate, process_upload, load_plan, save_edited_plan, create_submit_package, EDITABLE_COLUMNS, render_indication_select, load_doc2us_indication_options, import_edited_doc2us_queue, build_doc2us_automation_manifest, deploy_doc2us_ready_rows, read_deployment_progress, write_deployment_progress, get_staff_account_by_app_email

BASE = Path(__file__).resolve().parents[1]
JOBS_DIR = BASE / 'jobs'

app = FastAPI(title='EPS Shared Automation', version='0.2.0')

CSS = """
body{font-family:Arial,sans-serif;background:#f6f8fb;color:#1f2937;margin:0;padding:32px}.card{max-width:520px;margin:40px auto;background:white;padding:28px;border-radius:14px;box-shadow:0 8px 30px #0001}.wide{max-width:1500px;margin:20px auto;background:white;padding:24px;border-radius:14px;box-shadow:0 8px 30px #0001}label{display:block;margin:14px 0;font-weight:600}input,select,textarea{box-sizing:border-box;display:block;width:100%;padding:9px;margin-top:6px;border:1px solid #cbd5e1;border-radius:8px}textarea{min-width:180px;min-height:42px}button,.button{background:#0f766e;color:white;border:0;border-radius:8px;padding:11px 16px;text-decoration:none;display:inline-block;font-weight:700;cursor:pointer}.secondary{background:#475569}.danger{background:#b91c1c}.warnbtn{background:#b45309}.err{background:#fee2e2;color:#991b1b;padding:10px;border-radius:8px}.note{background:#eef6ff;padding:12px;border-radius:8px;margin-top:18px}.safety{background:#fff7ed;border-left:5px solid #f97316;padding:12px;border-radius:8px;margin:14px 0}.summary{margin:14px 0;line-height:2.5}.pill{padding:8px 12px;border-radius:999px;margin-right:10px;font-weight:700}.READY{background:#dcfce7}.REVIEW{background:#fef3c7}.OMIT{background:#fee2e2}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #e5e7eb;padding:7px;vertical-align:top}th{background:#f1f5f9;text-align:left;position:sticky;top:0}.grid{overflow:auto;max-height:72vh}.rowactions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.small{font-size:12px;color:#64748b}.workflow{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:16px 0}.step{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px}.step b{color:#0f766e}.stickybar{position:sticky;top:0;background:white;z-index:5;padding-bottom:8px;border-bottom:1px solid #e5e7eb}
"""


def html_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>')


def require_login(request: Request):
    email = request.cookies.get('eps_email')
    if not email:
        return None
    return email


def render_review(job_id: str, request: Request, notice: str = '') -> HTMLResponse:
    email = require_login(request)
    if not email:
        return RedirectResponse('/', status_code=303)
    df = load_plan(JOBS_DIR, job_id).fillna('')
    counts = df['status'].value_counts(dropna=False).to_dict()
    pills = ''.join(f'<span class="pill {escape(str(k))}">{escape(str(k))}: {int(v)}</span>' for k, v in counts.items())
    msg = f'<div class="note">{escape(notice)}</div>' if notice else ''
    rows = []
    for idx, r in df.iterrows():
        status = escape(str(r.get('status', '')))
        status_select = '<select name="row_{0}_status"><option {1}>READY</option><option {2}>REVIEW</option><option {3}>OMIT</option></select>'.format(
            idx,
            'selected' if status == 'READY' else '',
            'selected' if status == 'REVIEW' else '',
            'selected' if status == 'OMIT' else '',
        )
        indication_select = render_indication_select(idx, str(r.get('doc2us_icd_code','')), str(r.get('doc2us_indication','')))
        rows.append(f'''<tr class="{status}">
<td>{idx}<br>{status_select}</td>
<td><textarea name="row_{idx}_skip_reason">{escape(str(r.get('skip_reason','')))}</textarea></td>
<td><input name="row_{idx}_patient_name" value="{escape(str(r.get('patient_name','')))}"><span class="small">IC</span><input name="row_{idx}_patient_ic" value="{escape(str(r.get('patient_ic','')))}"><span class="small">Mobile</span><input name="row_{idx}_mobile" value="{escape(str(r.get('mobile','')))}"><span class="small">Email</span><input name="row_{idx}_email" value="{escape(str(r.get('email','')))}"></td>
<td><span class="small">Medication item</span><input name="row_{idx}_item_name" value="{escape(str(r.get('item_name','')))}"><span class="small">Active ingredient(s)</span><input name="row_{idx}_active_ingredients" value="{escape(str(r.get('active_ingredients','')))}"><span class="small">Qty: {escape(str(r.get('qty','')))} | Class: {escape(str(r.get('medication_class','')))}</span></td>
<td><input name="row_{idx}_indication" value="{escape(str(r.get('indication','')))}"><span class="small">AI pre-reviewed Doc2Us indication dropdown</span>{indication_select}<span class="small">Diagnosis search</span><input name="row_{idx}_diagnosis_search" value="{escape(str(r.get('diagnosis_search','')))}"></td>
<td><span class="small">Route</span><input name="row_{idx}_route" value="{escape(str(r.get('route','')))}"><span class="small">Dose</span><input name="row_{idx}_dose" value="{escape(str(r.get('dose','')))}"><span class="small">Unit</span><input name="row_{idx}_dose_unit" value="{escape(str(r.get('dose_unit','')))}"><span class="small">Frequency</span><input name="row_{idx}_frequency" value="{escape(str(r.get('frequency','')))}"></td>
<td><span class="small">Days</span><input name="row_{idx}_duration_days" value="{escape(str(r.get('duration_days','')))}"><span class="small">Amount</span><input name="row_{idx}_prescribed_amount" value="{escape(str(r.get('prescribed_amount','')))}"><span class="small">Unit</span><input name="row_{idx}_prescribed_unit" value="{escape(str(r.get('prescribed_unit','')))}"></td>
<td><span class="small">BP</span><input name="row_{idx}_bp" value="{escape(str(r.get('bp','')))}"><span class="small">HR</span><input name="row_{idx}_hr" value="{escape(str(r.get('hr','')))}"><span class="small">Glucose</span><input name="row_{idx}_glucose" value="{escape(str(r.get('glucose','')))}"><span class="small">Next appt</span><input name="row_{idx}_next_appointment_date" value="{escape(str(r.get('next_appointment_date','')))}"></td>
<td><textarea name="row_{idx}_drug_remark">{escape(str(r.get('drug_remark','')))}</textarea><span class="small">Screening remarks</span><textarea name="row_{idx}_screening_remarks">{escape(str(r.get('screening_remarks','')))}</textarea></td>
</tr>''')
    body = f'''<main class="wide">
<h1>EPS Plan Review + Edit</h1>
<p>Logged in as {escape(email)}</p>{msg}
<div class="stickybar">
<div class="workflow">
  <div class="step"><b>1 Upload</b><br><span class="small">Upload raw Excel data</span></div>
  <div class="step"><b>2 Edit / Omit</b><br><span class="small">Edit rows here; set unwanted rows to OMIT</span></div>
  <div class="step"><b>3 Deploy</b><br><span class="small">Send READY rows to Doc2Us EPS</span></div>
  <div class="step"><b>4 Notification</b><br><span class="small">Medication/prescription count shown after deploy</span></div>
</div>
<div class="summary">{pills}</div>
<div class="rowactions">
<a class="button secondary" href="/upload">Upload another file</a>
</div>
</div>
<form method="post" action="/save/{escape(job_id)}">
<p class="note"><b>Editable now:</b> status, patient info, active-ingredient-mapped medication, AI pre-reviewed Doc2Us indication dropdown, dose, frequency, days, amount, BP/HR/glucose, remarks. Change REVIEW to READY only after pharmacist confirms the medication details are correct.</p>
<div class="grid"><table><thead><tr><th># / Status</th><th>Reason</th><th>Patient</th><th>Medication</th><th>Indication</th><th>Dose/Frequency</th><th>Duration/Amount</th><th>Screening</th><th>Remarks</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="rowactions"><button type="submit">Save Edits</button><button type="submit" formaction="/deploy/{escape(job_id)}" class="warnbtn">Save Edits + Start Batch Deploy To Doc2Us</button></p>
</form>
<script>
document.querySelectorAll('select[name$="_doc2us_icd_code"]').forEach(function(sel){{
  sel.addEventListener('change', function(){{
    var hidden = sel.parentElement.querySelector('input[name$="_doc2us_indication"]');
    var opt = sel.options[sel.selectedIndex];
    if (hidden && opt) hidden.value = opt.getAttribute('data-desc') || '';
  }});
}});
</script>
<p class="note"><b>Deploy rule:</b> READY rows deploy to Doc2Us. OMIT rows are skipped. One READY medication row equals one prescription request.</p>
</main>'''
    return html_page('Review EPS Plan', body)


@app.get('/', response_class=HTMLResponse)
def login_page(request: Request):
    error = request.query_params.get('error', '')
    err = f'<div class="err">{escape(error)}</div>' if error else ''
    body = f'''<main class="card">
<h1>EPS Shared Automation</h1>
<p>Use your staff web-app login. Each staff login is linked to its own Doc2Us account.</p>
{err}
<form method="post" action="/login">
<label>Email <input name="email" type="email" required></label>
<label>Password <input name="password" type="password" required></label>
<button type="submit">Login</button>
</form>
<p class="note">This tool prepares/submits data to reduce duplicate entry. It does not prescribe; pharmacist review remains required.</p>
</main>'''
    return html_page('EPS Automation Login', body)


@app.post('/login')
def login(email: str = Form(...), password: str = Form(...)):
    if not authenticate(email, password):
        return RedirectResponse('/?error=Invalid%20login.', status_code=303)
    resp = RedirectResponse('/upload', status_code=303)
    resp.set_cookie('eps_email', email, httponly=True, samesite='lax')
    return resp


@app.get('/upload', response_class=HTMLResponse)
def upload_page(request: Request):
    email = require_login(request)
    if not email:
        return RedirectResponse('/', status_code=303)
    body = f'''<main class="card wide">
<h1>Upload Octopus Poison B/C Excel</h1>
<p>Logged in as {escape(email)}</p>
<form method="post" action="/process" enctype="multipart/form-data">
<label>Pharmacist name as per IC <input name="pharmacist_name" required placeholder="e.g. Johnny Chew Seng Wen"></label>
<label>Registration number <input name="reg_no" required placeholder="e.g. 018161"></label>
<label>Application date <input name="apply_date" type="date" value="{date.today().isoformat()}" required></label>
<label>Raw Excel file <input name="excel_file" type="file" accept=".xlsx,.xls" required></label>
<button type="submit">Generate EPS Plan</button>
</form>
<section class="note"><b>Default questionnaire:</b> BP 120/80, HR 75, Glucose 6.0, Allergy NKDA, LTM, remarks refill medication. Email defaults to IC@doc2us.com.</section>
</main>'''
    return html_page('Upload EPS Data', body)


@app.post('/process', response_class=HTMLResponse)
async def process_file(
    request: Request,
    pharmacist_name: str = Form(...),
    reg_no: str = Form(...),
    apply_date: str = Form(...),
    excel_file: UploadFile = File(...),
):
    if not require_login(request):
        return RedirectResponse('/', status_code=303)
    if not excel_file.filename or not excel_file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(400, 'Please upload an Excel file')
    data = await excel_file.read()
    job = process_upload(data, excel_file.filename, pharmacist_name, reg_no, apply_date, JOBS_DIR)
    return RedirectResponse(f'/review/{job["job_id"]}', status_code=303)


@app.get('/review/{job_id}', response_class=HTMLResponse)
def review(job_id: str, request: Request):
    return render_review(job_id, request)


def extract_row_edits(form) -> dict[str, dict[str, str]]:
    edits: dict[str, dict[str, str]] = {}
    prefix = 'row_'
    for key, val in form.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        row_id, _, col = rest.partition('_')
        if not row_id.isdigit() or col not in EDITABLE_COLUMNS:
            continue
        edits.setdefault(row_id, {})[col] = str(val)
    return edits


@app.post('/save/{job_id}', response_class=HTMLResponse)
async def save(job_id: str, request: Request):
    app_email = require_login(request)
    if not app_email:
        return RedirectResponse('/', status_code=303)
    staff_account = get_staff_account_by_app_email(app_email)
    form = await request.form()
    save_edited_plan(JOBS_DIR, job_id, extract_row_edits(form))
    return render_review(job_id, request, 'Saved edits. You can now deploy READY rows to Doc2Us or keep editing/omitting rows.')


def render_deployment_result(job_id: str, result: dict) -> HTMLResponse:
    invalid = int(result.get('invalid_count', 0))
    failed = int(result.get('failed_count', 0))
    verified = int(result.get('verified_count', 0))
    invalid_note = ''
    if invalid:
        invalid_note = f'<div class="err">{invalid} row(s) are incomplete and were moved back to REVIEW. Fix them before live Doc2Us submission.</div>'
    failed_note = ''
    if failed:
        failed_note = f'<div class="err">{failed} row(s) failed during live Doc2Us browser submission. Check doc2us_deployment_manifest.json and screenshots in the job folder.</div>'
    result_rows = ''.join(
        f'<tr><td>{escape(str(r.get("row", "")))}</td><td>{escape(str(r.get("patient_ic", "")))}</td><td>{escape(str(r.get("medication", "")))}</td><td>{escape(str(r.get("status", "")))}</td><td>{escape(str(r.get("before_count", "")))}</td><td>{escape(str(r.get("after_count", "")))}</td><td>{escape(str(r.get("error", "")))}</td></tr>'
        for r in result.get('results', [])
    )
    result_table = f'<div class="grid"><table><thead><tr><th>Row</th><th>IC</th><th>Medication</th><th>Status</th><th>Before Count</th><th>After Count</th><th>Error</th></tr></thead><tbody>{result_rows}</tbody></table></div>' if result_rows else ''
    body = f'''<main class="card wide">
<h1>Doc2Us Live Deployment Status</h1>
{invalid_note}{failed_note}
<div class="summary">
<span class="pill READY">Verified EPS records created: {verified}</span>
<span class="pill OMIT">Failed: {failed}</span>
<span class="pill REVIEW">Patient groups: {escape(str(result.get('patient_group_count', '')))}</span>
<span class="pill READY">Doc2Us login count: {escape(str(result.get('login_count', '1')))}</span>
<span class="pill REVIEW">Staff: {escape(str(result.get('staff_label', '')))}</span>
</div>
<p>Batch mode uses one Doc2Us login/session for all READY rows. One medication equals one prescription request row. The Doc2Us account is linked to the logged-in staff.</p>
<div class="safety"><b>Status:</b> {escape(str(result.get('notification', 'Live deployment completed.')))}</div>
{result_table}
<p class="small">Evidence folder: {escape(str(result.get('screenshot_dir', '')))}</p>
<p class="rowactions"><a class="button secondary" href="/review/{escape(job_id)}">Back to Review</a><a class="button" href="/upload">Upload Next Raw Excel</a></p>
</main>'''
    return html_page('Doc2Us Deployment Notification', body)


@app.post('/deploy/{job_id}', response_class=HTMLResponse)
async def deploy(job_id: str, request: Request, background_tasks: BackgroundTasks):
    app_email = require_login(request)
    if not app_email:
        return RedirectResponse('/', status_code=303)
    staff_account = get_staff_account_by_app_email(app_email)
    form = await request.form()
    save_edited_plan(JOBS_DIR, job_id, extract_row_edits(form))
    package = create_submit_package(JOBS_DIR, job_id)
    write_deployment_progress(JOBS_DIR, job_id, {
        'status': 'queued',
        'event': 'queued',
        'total_rows': int(package.get('count', 0)),
        'submitted_count': 0,
        'failed_count': 0,
        'results': [],
        'staff_label': staff_account.get('staff_label', app_email) if staff_account else app_email,
        'doc2us_account_email': staff_account.get('doc2us_email', '') if staff_account else '',
        'notification': 'Batch deployment queued. The system will login to the assigned staff Doc2Us account once and process all READY rows.',
    })
    background_tasks.add_task(deploy_doc2us_ready_rows, JOBS_DIR, job_id, True, app_email)
    return RedirectResponse(f'/deploy-status/{escape(job_id)}', status_code=303)


@app.get('/deploy-status/{job_id}', response_class=HTMLResponse)
def deploy_status(job_id: str, request: Request):
    if not require_login(request):
        return RedirectResponse('/', status_code=303)
    progress = read_deployment_progress(JOBS_DIR, job_id)
    if progress.get('status') == 'finished':
        return render_deployment_result(job_id, progress)
    rows = ''.join(
        f'<tr><td>{escape(str(r.get("row", "")))}</td><td>{escape(str(r.get("patient_ic", "")))}</td><td>{escape(str(r.get("medication", "")))}</td><td>{escape(str(r.get("status", "")))}</td><td>{escape(str(r.get("before_count", "")))}</td><td>{escape(str(r.get("after_count", "")))}</td><td>{escape(str(r.get("error", "")))}</td></tr>'
        for r in progress.get('results', [])
    )
    table = f'<div class="grid"><table><thead><tr><th>Row</th><th>IC</th><th>Medication</th><th>Status</th><th>Before</th><th>After</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else ''
    body = f'''<main class="card wide">
<meta http-equiv="refresh" content="5">
<h1>Doc2Us Batch Deployment Running</h1>
<div class="summary">
<span class="pill READY">Status: {escape(str(progress.get('status', 'queued')))}</span>
<span class="pill REVIEW">Event: {escape(str(progress.get('event', '')))}</span>
<span class="pill READY">Rows: {escape(str(progress.get('submitted_count', 0)))} / {escape(str(progress.get('total_rows', 0)))}</span>
<span class="pill OMIT">Failed: {escape(str(progress.get('failed_count', 0)))}</span>
<span class="pill REVIEW">Staff: {escape(str(progress.get('staff_label', '')))}</span>
</div>
<p class="note">This page auto-refreshes every 5 seconds. The browser automation keeps one assigned staff Doc2Us login/session and processes all READY rows in batch. REVIEW and OMIT rows are excluded.</p>
<div class="safety"><b>Current:</b> {escape(str(progress.get('notification') or progress.get('patient_ic') or progress.get('medication') or 'Starting...'))}</div>
<p class="small">Updated: {escape(str(progress.get('updated_at', '')))}</p>
{table}
<p class="rowactions"><a class="button secondary" href="/review/{escape(job_id)}">Back to Review</a></p>
</main>'''
    return html_page('Doc2Us Batch Deployment Running', body)


@app.post('/submit/{job_id}', response_class=HTMLResponse)
def submit_queue(job_id: str, request: Request):
    if not require_login(request):
        return RedirectResponse('/', status_code=303)
    package = create_submit_package(JOBS_DIR, job_id)
    invalid_note = ''
    if package.get('invalid_count'):
        invalid_note = f'<div class="err"><b>{int(package["invalid_count"])} READY row(s) were not deploy-ready.</b> They were moved back to REVIEW with reasons. Please fix them before deploying to Doc2Us.</div>'
    body = f'''<main class="card wide">
<h1>Doc2Us Deploy Queue Prepared</h1>
{invalid_note}
<p>{package['count']} validated READY rows are included. REVIEW and OMIT rows are excluded.</p>
<div class="safety"><b>Before deploy:</b> Choose the Excel file you want the automation to use. The automation package is prepared for Doc2Us data entry. Live prescription request still stops at pharmacist confirmation; Doctor approval remains required.</div>
<div class="workflow">
  <div class="step"><b>Deploy with Excel</b><br><span class="small">Upload the final Excel queue to use for Doc2Us automation.</span>
    <form method="post" action="/import-queue/{escape(job_id)}" enctype="multipart/form-data">
      <label>Excel file <input name="queue_file" type="file" accept=".xlsx,.xls" required></label>
      <button type="submit">Use This Excel To Deploy</button>
    </form>
  </div>
  <div class="step"><b>Do not deploy</b><br><span class="small">Go back and continue checking/editing rows.</span><br><br>
    <a class="button secondary" href="/review/{escape(job_id)}">Do Not Deploy</a>
  </div>
</div>
<p class="rowactions"><a class="button secondary" href="/review/{escape(job_id)}">Back To Review</a></p>
</main>'''
    return html_page('Doc2Us Submit Queue', body)


@app.post('/import-queue/{job_id}', response_class=HTMLResponse)
async def import_queue(job_id: str, request: Request, queue_file: UploadFile = File(...)):
    if not require_login(request):
        return RedirectResponse('/', status_code=303)
    if not queue_file.filename or not queue_file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(400, 'Please upload an Excel file')
    data = await queue_file.read()
    result = import_edited_doc2us_queue(JOBS_DIR, job_id, data, queue_file.filename)
    body = f'''<main class="card wide">
<h1>File Ready For Doc2Us Deployment</h1>
<p>Imported {result['imported_count']} rows from your Excel.</p>
<p>READY rows available to deploy: {result['ready_count']}</p>
<p>Invalid rows moved to REVIEW: {result['invalid_count']}</p>
<div class="safety"><b>End phase ready:</b> The selected Excel has been validated. Press the button below to run the live Doc2Us browser automation. It will only report VERIFIED when the EPS Total Medication Record count increases after submit.</div>
<form method="post" action="/deploy/{escape(job_id)}">
  <button type="submit">Start Batch Deploy Using My Assigned Doc2Us Account</button>
</form>
<p class="rowactions"><a class="button secondary" href="/review/{escape(job_id)}">Back to Review</a></p>
</main>'''
    return html_page('Ready For Doc2Us Deployment', body)


@app.get('/automation-manifest/{job_id}')
def automation_manifest(job_id: str):
    if not job_id.isalnum():
        raise HTTPException(400, 'Invalid job id')
    package = create_submit_package(JOBS_DIR, job_id)
    manifest = build_doc2us_automation_manifest(package['queue_path'], dry_run=True)
    manifest_path = JOBS_DIR / job_id / 'doc2us_automation_dry_run_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    return FileResponse(str(manifest_path), filename='doc2us_automation_dry_run_manifest.json')


@app.get('/download-submit/{job_id}')
def download_submit(job_id: str):
    if not job_id.isalnum():
        raise HTTPException(400, 'Invalid job id')
    job_dir = JOBS_DIR / job_id
    files = list(job_dir.glob('*_DOC2US_READY_QUEUE.xlsx')) if job_dir.exists() else []
    if not files:
        create_submit_package(JOBS_DIR, job_id)
        files = list(job_dir.glob('*_DOC2US_READY_QUEUE.xlsx'))
    if not files:
        raise HTTPException(404, 'Submit queue not found')
    return FileResponse(str(files[0]), filename=files[0].name)


@app.get('/download/{job_id}')
def download(job_id: str):
    if not job_id.isalnum():
        raise HTTPException(400, 'Invalid job id')
    job_dir = JOBS_DIR / job_id
    files = list(job_dir.glob('*_EPS_PLAN.xlsx')) if job_dir.exists() else []
    if not files:
        raise HTTPException(404, 'File not found')
    return FileResponse(str(files[0]), filename=files[0].name)


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/download-source-zip')
def download_source_zip():
    zip_path = BASE.parent / 'eps-web-automation.zip'
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail='Zip file not found')
    return FileResponse(str(zip_path), filename='eps-web-automation.zip')
