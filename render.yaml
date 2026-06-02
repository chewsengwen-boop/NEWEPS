from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app import main
from app.web_logic import doc2us_deploy_columns


def _make_ready_job(tmp_path):
    job_id = 'routejob123'
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    row = {col: '' for col in doc2us_deploy_columns()}
    row.update({
        'status': 'READY',
        'patient_name': 'Test Patient',
        'patient_ic': '900101131234',
        'mobile': '0123456789',
        'email': '900101131234@doc2us.com',
        'item_name': 'Amlodipine 10mg',
        'active_ingredients': 'Amlodipine',
        'indication': 'Hypertension',
        'doc2us_icd_code': 'BA00.Z',
        'doc2us_indication': 'Essential hypertension, unspecified',
        'diagnosis_search': 'BA00.Z',
        'route': 'Oral',
        'dose': '1',
        'dose_unit': 'tab(s)/cap(s)',
        'frequency': 'Once daily',
        'duration_days': 30,
        'prescribed_amount': 30,
        'prescribed_unit': 'tablet(s)',
        'drug_remark': 'refill medication',
        'questionnaire_mode': 'LTM',
        'bp': '120/80',
        'hr': '75',
        'glucose': '6.0',
        'last_appointment_date': '2026-05-01',
        'next_appointment_date': '2026-06-30',
        'follow_up_under': 'Alpro',
        'referred_by': 'Johnny Chew Seng Wen',
        'pharmacist_reg_no': '018161',
        'screening_remarks': 'refill medication',
        'skip_reason': '',
        'medication_class': 'B',
    })
    plan_path = job_dir / 'route_EPS_PLAN.xlsx'
    pd.DataFrame([row]).to_excel(plan_path, index=False, sheet_name='EPS_PLAN')
    return job_id


def test_website_deploy_button_runs_live_submitter_without_500(tmp_path, monkeypatch):
    job_id = _make_ready_job(tmp_path)
    calls = []

    def fake_submit(queue_path, screenshot_dir, final_submit=True):
        q = pd.read_excel(queue_path, sheet_name='DOC2US_READY_UPLOAD')
        calls.append({'queue_path': str(queue_path), 'screenshot_dir': str(screenshot_dir), 'final_submit': final_submit, 'rows': len(q)})
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
        return {
            'submitted_count': len(q),
            'failed_count': 0,
            'screenshot_dir': str(screenshot_dir),
            'results': [{
                'row': 0,
                'patient_ic': '900101131234',
                'medication': 'Amlodipine 10mg',
                'status': 'VERIFIED',
                'before_count': 10,
                'after_count': 11,
            }],
        }

    monkeypatch.setattr(main, 'JOBS_DIR', tmp_path)
    monkeypatch.setattr('app.web_logic.submit_doc2us_queue_live', fake_submit)

    client = TestClient(main.app)
    client.cookies.set('eps_email', 'qsbjc1@alpropharmacy.com')
    response = client.post(f'/deploy/{job_id}', data={}, follow_redirects=True)

    assert response.status_code == 200
    assert 'Doc2Us Batch Deployment Running' in response.text or 'Doc2Us Live Deployment Status' in response.text
    assert 'one Doc2Us login/session' in response.text or 'Doc2Us login count: 1' in response.text
    assert (tmp_path / job_id / 'doc2us_deployment_progress.json').exists()
