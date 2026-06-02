import io
from pathlib import Path
import pandas as pd

from app import web_logic
from app.web_logic import (
    authenticate,
    make_job_id,
    process_upload,
    load_plan,
    save_edited_plan,
    create_submit_package,
    load_doc2us_indication_options,
    render_indication_select,
    validate_doc2us_ready_row,
    doc2us_deploy_columns,
    import_edited_doc2us_queue,
    build_doc2us_automation_manifest,
    deploy_doc2us_ready_rows,
    EDITABLE_COLUMNS,
)

SAMPLE = '/mnt/c/Users/User/Downloads/OUTLET POISON B&C TRANSACTION NO_01-06-2026 (Web).xlsx'


def test_authenticate_accepts_doc2us_trial_account():
    assert authenticate('qsbjc1@alpropharmacy.com', 'Alpro-123') is True


def test_authenticate_rejects_wrong_password():
    assert authenticate('qsbjc1@alpropharmacy.com', 'wrong') is False


def test_make_job_id_is_safe_and_unique():
    a = make_job_id()
    b = make_job_id()
    assert a != b
    assert '/' not in a and '..' not in a


def _sample_job(tmp_path):
    with open(SAMPLE, 'rb') as f:
        data = f.read()
    return process_upload(
        file_bytes=data,
        original_filename='raw.xlsx',
        pharmacist_name='Johnny Chew Seng Wen',
        reg_no='018161',
        apply_date='2026-06-01',
        jobs_dir=tmp_path,
    )


def test_process_upload_generates_ready_review_omit(tmp_path):
    job = _sample_job(tmp_path)
    assert Path(job['output_path']).exists()
    counts = job['counts']
    assert counts['READY'] == 7
    assert counts['REVIEW'] == 1
    assert counts['OMIT'] == 1
    df = pd.read_excel(job['output_path'], sheet_name='EPS_PLAN')
    assert 'ZOCOL' in df[df.status == 'OMIT'].iloc[0].item_name
    assert df[df.patient_name.str.contains('LU SIEW', na=False)].iloc[0].status == 'REVIEW'
    statin = df[df.item_name.str.contains('ROSUVASTATIN', case=False, na=False)].iloc[0]
    assert statin.active_ingredients == 'ROSUVASTATIN'
    assert statin.doc2us_icd_code == '5C80.0Z'
    assert statin.doc2us_indication == 'Hypercholesterolaemia, unspecified'


def test_doc2us_default_indications_are_loaded_from_harvested_dropdown():
    options = load_doc2us_indication_options()
    assert ('BA00.Z', 'Essential hypertension, unspecified') in options
    assert ('5C80.0Z', 'Hypercholesterolaemia, unspecified') in options


def test_indication_select_preserves_ai_prereview_choice_and_allows_dropdown_change():
    html = render_indication_select(3, 'BA00.Z', 'Essential hypertension, unspecified')
    assert 'name="row_3_doc2us_icd_code"' in html
    assert 'BA00.Z - Essential hypertension, unspecified' in html
    assert 'selected' in html
    assert '5C80.0Z - Hypercholesterolaemia, unspecified' in html


def test_save_edited_plan_updates_review_row_and_rebuilds_workbook(tmp_path):
    job = _sample_job(tmp_path)
    df = load_plan(tmp_path, job['job_id'])
    idx = int(df[df.patient_name.str.contains('LU SIEW', na=False)].index[0])
    saved = save_edited_plan(tmp_path, job['job_id'], {
        str(idx): {
            'status': 'READY',
            'skip_reason': '',
            'indication': 'Hypertension',
            'frequency': 'Once daily',
            'duration_days': '30',
            'prescribed_amount': '30',
            'item_name': 'AMLODIPINE 10MG EDITED',
            'active_ingredients': 'AMLODIPINE',
        }
    })
    assert saved['counts']['READY'] == 8
    edited = load_plan(tmp_path, job['job_id'])
    assert edited.loc[idx, 'status'] == 'READY'
    assert edited.loc[idx, 'prescribed_amount'] == 30
    assert edited.loc[idx, 'item_name'] == 'AMLODIPINE 10MG EDITED'
    plan_path = next((tmp_path / job['job_id']).glob('*_EPS_PLAN.xlsx'))
    downloaded = pd.read_excel(plan_path, sheet_name='EPS_PLAN')
    assert downloaded.loc[idx, 'item_name'] == 'AMLODIPINE 10MG EDITED'


def test_save_edited_plan_handles_full_browser_form_string_values(tmp_path):
    job = _sample_job(tmp_path)
    df = load_plan(tmp_path, job['job_id']).fillna('')
    edits = {}
    for idx, row in df.iterrows():
        edits[str(idx)] = {col: str(row.get(col, '')) for col in EDITABLE_COLUMNS if col in df.columns}
    first = str(df.index[0])
    edits[first]['patient_ic'] = '550722135174'
    edits[first]['mobile'] = '60123456789'
    edits[first]['status'] = 'READY'
    saved = save_edited_plan(tmp_path, job['job_id'], edits)
    updated = load_plan(tmp_path, job['job_id'])
    assert saved['counts']['READY'] >= 1
    assert str(updated.loc[int(first), 'patient_ic']) == '550722135174'


def test_save_edited_plan_creates_missing_new_editable_columns(tmp_path):
    job = _sample_job(tmp_path)
    plan_path = next((tmp_path / job['job_id']).glob('*_EPS_PLAN.xlsx'))
    df = pd.read_excel(plan_path, sheet_name='EPS_PLAN')
    df = df.drop(columns=[c for c in ['active_ingredients', 'doc2us_icd_code', 'doc2us_indication'] if c in df.columns])
    with pd.ExcelWriter(plan_path, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='EPS_PLAN')
    idx = int(df.index[0])
    save_edited_plan(tmp_path, job['job_id'], {str(idx): {
        'status': 'READY',
        'active_ingredients': 'Amlodipine',
        'doc2us_icd_code': 'I10',
        'doc2us_indication': 'Essential hypertension',
    }})
    updated = load_plan(tmp_path, job['job_id'])
    assert updated.loc[idx, 'active_ingredients'] == 'Amlodipine'
    assert updated.loc[idx, 'doc2us_icd_code'] == 'I10'


def test_create_submit_package_contains_ready_rows_only(tmp_path):
    job = _sample_job(tmp_path)
    package = create_submit_package(tmp_path, job['job_id'])
    assert Path(package['queue_path']).exists()
    q = pd.read_excel(package['queue_path'], sheet_name='DOC2US_READY_UPLOAD')
    assert set(q['status']) == {'READY'}
    assert len(q) == job['counts']['READY']
    assert list(q.columns) == doc2us_deploy_columns()
    assert q['doc2us_icd_code'].notna().all()
    assert q['active_ingredients'].notna().all()


def test_ready_row_validation_requires_doc2us_fields_before_deploy():
    row = pd.Series({
        'patient_name': 'Test Patient', 'patient_ic': '900101131234', 'mobile': '0123456789',
        'item_name': 'AMLODIPINE 10MG', 'active_ingredients': 'AMLODIPINE',
        'doc2us_icd_code': 'BA00.Z', 'doc2us_indication': 'Essential hypertension, unspecified',
        'route': 'Oral', 'dose': '1', 'dose_unit': 'tab(s)/cap(s)', 'frequency': 'Every morning',
        'duration_days': 10, 'prescribed_amount': 10, 'prescribed_unit': 'tablet(s)',
        'questionnaire_mode': 'LTM', 'bp': '120/80', 'next_appointment_date': '2026-06-11',
        'follow_up_under': 'klinik kesihatan', 'referred_by': 'Johnny Chew Seng Wen',
        'pharmacist_reg_no': '018161', 'screening_remarks': 'come refill medication',
    })
    assert validate_doc2us_ready_row(row) == []
    row['doc2us_icd_code'] = ''
    assert 'Doc2Us indication dropdown must be selected' in validate_doc2us_ready_row(row)


def test_create_submit_package_downgrades_invalid_ready_rows_to_review(tmp_path):
    job = _sample_job(tmp_path)
    df = load_plan(tmp_path, job['job_id'])
    idx = int(df[df.status == 'READY'].index[0])
    save_edited_plan(tmp_path, job['job_id'], {str(idx): {'doc2us_icd_code': '', 'doc2us_indication': ''}})
    package = create_submit_package(tmp_path, job['job_id'])
    assert package['invalid_count'] == 1
    assert package['count'] == job['counts']['READY'] - 1
    refreshed = load_plan(tmp_path, job['job_id'])
    assert refreshed.loc[idx, 'status'] == 'REVIEW'
    assert 'Doc2Us indication dropdown must be selected' in refreshed.loc[idx, 'skip_reason']


def test_import_edited_doc2us_queue_roundtrip_revalidates_rows(tmp_path):
    job = _sample_job(tmp_path)
    package = create_submit_package(tmp_path, job['job_id'])
    q = pd.read_excel(package['queue_path'], sheet_name='DOC2US_READY_UPLOAD').astype(object)
    q.loc[0, 'mobile'] = ''
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        q.to_excel(w, index=False, sheet_name='DOC2US_READY_UPLOAD')
    result = import_edited_doc2us_queue(tmp_path, job['job_id'], buf.getvalue(), 'edited.xlsx')
    assert result['imported_count'] == len(q)
    assert result['invalid_count'] == 1
    refreshed = load_plan(tmp_path, job['job_id'])
    assert (refreshed['status'] == 'REVIEW').sum() >= 2
    assert 'Mobile number is required' in '; '.join(refreshed['skip_reason'].fillna('').astype(str))


def test_build_doc2us_automation_manifest_is_dry_run_and_has_confirm_gate(tmp_path):
    job = _sample_job(tmp_path)
    package = create_submit_package(tmp_path, job['job_id'])
    manifest = build_doc2us_automation_manifest(package['queue_path'], dry_run=True)
    assert manifest['dry_run'] is True
    assert manifest['live_submit_enabled'] is False
    assert manifest['row_count'] == package['count']
    assert manifest['steps'][0]['action'] == 'login_doc2us_eps'
    assert any(step['action'] == 'register_patient_if_missing' for step in manifest['steps'])
    assert any(step['action'] == 'request_prescription_requires_manual_confirm' for step in manifest['steps'])


def test_save_edited_plan_does_not_crash_when_optional_summary_columns_missing(tmp_path):
    job = _sample_job(tmp_path)
    job_dir = tmp_path / job['job_id']
    plan_path = next(job_dir.glob('*_EPS_PLAN.xlsx'))
    df = pd.read_excel(plan_path, sheet_name='EPS_PLAN').drop(columns=['medication_class'])
    with pd.ExcelWriter(plan_path, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='EPS_PLAN')
    result = save_edited_plan(tmp_path, job['job_id'], {'0': {'status': 'REVIEW'}})
    assert 'REVIEW' in result['counts']


def test_deploy_doc2us_ready_rows_counts_one_medication_as_one_prescription(tmp_path):
    job = _sample_job(tmp_path)
    result = deploy_doc2us_ready_rows(tmp_path, job['job_id'])
    assert result['medication_count'] == job['counts']['READY']
    assert result['prescription_count'] == job['counts']['READY']
    assert result['dry_run'] is True
    assert 'medication(s)' in result['notification']
    assert 'prescription request(s)' in result['notification']
    assert (tmp_path / job['job_id'] / 'doc2us_deployment_manifest.json').exists()


def test_live_deploy_invokes_doc2us_submitter_and_records_real_counts(tmp_path, monkeypatch):
    job = _sample_job(tmp_path)
    calls = []

    def fake_submit(queue_path, screenshot_dir, final_submit=True, progress_callback=None):
        q = pd.read_excel(queue_path, sheet_name='DOC2US_READY_UPLOAD')
        calls.append((Path(queue_path), Path(screenshot_dir), final_submit, len(q), progress_callback is not None))
        if progress_callback:
            progress_callback({'event': 'logged_in', 'submitted_count': 0, 'failed_count': 0, 'results': [], 'total_rows': len(q), 'patient_groups': q['patient_ic'].astype(str).nunique()})
        return {
            'submitted_count': len(q),
            'failed_count': 0,
            'screenshot_dir': str(screenshot_dir),
            'results': [{'row': i, 'status': 'VERIFIED', 'before_count': 36 + i, 'after_count': 37 + i} for i in range(len(q))],
        }

    monkeypatch.setattr(web_logic, 'submit_doc2us_queue_live', fake_submit)
    result = deploy_doc2us_ready_rows(tmp_path, job['job_id'], live_submit=True)
    assert calls
    assert calls[0][2] is True
    assert calls[0][4] is True
    assert result['dry_run'] is False
    assert result['live_submit_enabled'] is True
    assert result['submitted_count'] == job['counts']['READY']
    assert result['failed_count'] == 0
    assert result['medication_count'] == job['counts']['READY']
    assert 'one Doc2Us login' in result['notification']
    assert result['verified_count'] == job['counts']['READY']
    assert result['batch_mode'] is True
    assert result['login_count'] == 1
    assert result['patient_group_count'] >= 1
