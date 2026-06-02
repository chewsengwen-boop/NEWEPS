from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

DOC2US_LOGIN_URL = 'https://eps.doc2us.com/login'


@dataclass
class LiveSubmitResult:
    submitted_count: int
    failed_count: int
    results: list[dict[str, Any]]
    screenshot_dir: str


def _clean(value: Any) -> str:
    if pd.isna(value):
        return ''
    return str(value).strip()


def _num_text(value: Any, default: str = '1') -> str:
    text = _clean(value)
    if not text:
        return default
    try:
        n = float(text)
        return str(int(n)) if n.is_integer() else str(n)
    except ValueError:
        return text


def _normalise_phone(value: Any) -> str:
    phone = re.sub(r'\D+', '', _clean(value))
    if phone.startswith('60'):
        return phone
    if phone.startswith('0'):
        return '6' + phone
    if phone:
        return phone
    return '60120000000'


def _env_login() -> tuple[str, str]:
    email = os.environ.get('DOC2US_EMAIL') or os.environ.get('EPS_ALLOWED_EMAIL') or 'qsbjc1@alpropharmacy.com'
    password = os.environ.get('DOC2US_PASSWORD') or os.environ.get('EPS_ALLOWED_PASSWORD') or 'Alpro-123'
    return email, password


class Doc2UsLiveRunner:
    def __init__(self, screenshot_dir: str | Path, headless: bool = True, final_submit: bool = True):
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.headless = bool(headless)
        self.final_submit = bool(final_submit)

    def run_queue(self, queue: pd.DataFrame) -> LiveSubmitResult:
        from playwright.sync_api import sync_playwright

        email, password = _env_login()
        results: list[dict[str, Any]] = []
        submitted = 0
        failed = 0
        with sync_playwright() as p:
            launch_args: dict[str, Any] = {'headless': self.headless}
            for candidate in ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium']:
                if Path(candidate).exists():
                    launch_args['executable_path'] = candidate
                    break
            browser = p.chromium.launch(**launch_args)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            try:
                self._login(page, email, password)
                for pos, row in queue.reset_index(drop=True).iterrows():
                    try:
                        record = self._submit_one(page, row, int(pos))
                        submitted += 1
                        results.append(record)
                    except Exception as exc:  # noqa: BLE001 - return per-row evidence to pharmacist
                        failed += 1
                        shot = self.screenshot_dir / f'row_{pos+1}_failed.png'
                        try:
                            page.screenshot(path=str(shot), full_page=True)
                        except Exception:
                            pass
                        results.append({
                            'row': int(pos),
                            'patient_ic': _clean(row.get('patient_ic')),
                            'medication': _clean(row.get('item_name')),
                            'status': 'FAILED',
                            'error': str(exc),
                            'screenshot': str(shot),
                        })
            finally:
                browser.close()
        return LiveSubmitResult(submitted, failed, results, str(self.screenshot_dir))

    def _login(self, page, email: str, password: str) -> None:
        page.goto(DOC2US_LOGIN_URL, wait_until='networkidle', timeout=60000)
        page.locator('input').nth(0).fill(email)
        page.locator('input[type=password]').fill(password)
        page.get_by_text('SIGN IN').click()
        page.wait_for_url('**/dashboard', timeout=60000)

    def _select_option(self, page, select_locator, contains_text: str) -> None:
        select_locator.click(force=True, timeout=15000)
        page.wait_for_timeout(300)
        option = page.locator('mat-option').filter(has_text=contains_text).first
        option.wait_for(state='visible', timeout=15000)
        option.click(force=True)
        page.wait_for_timeout(300)

    def _click_text_if_visible(self, page, text: str, timeout: int = 3000) -> bool:
        loc = page.get_by_text(text, exact=False).first
        try:
            loc.wait_for(state='visible', timeout=timeout)
            loc.click(force=True)
            page.wait_for_timeout(500)
            return True
        except Exception:
            return False

    def _patient_record_count(self, page, ic: str) -> int | None:
        """Return Total Medication Record from the patient search page, if visible."""
        page.goto(f'https://eps.doc2us.com/medication-record;searchKey={ic}', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)
        text = page.locator('body').inner_text(timeout=10000)
        pattern = re.compile(rf'{re.escape(ic)}\s+\S+\s+(?:Male|Female|Other)\s+(\d+)\b', re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return int(match.group(1))
        fallback = re.search(r'Total Medication Record\s+.*?\b(?:Male|Female|Other)\s+(\d+)\b', text, re.IGNORECASE | re.DOTALL)
        if fallback:
            return int(fallback.group(1))
        return None

    def _submit_one(self, page, row: pd.Series, pos: int) -> dict[str, Any]:
        ic = _clean(row.get('patient_ic'))
        patient = _clean(row.get('patient_name'))
        medication = _clean(row.get('item_name'))
        if not ic or not medication:
            raise ValueError('Missing patient IC or medication name')

        before_count = self._patient_record_count(page, ic)
        # Open existing patient if search result is shown, otherwise stop clearly: auto-registration is intentionally not silent.
        if page.get_by_role('button', name='Medication Record').count():
            page.get_by_role('button', name='Medication Record').first.click(force=True)
        else:
            body = page.locator('body').inner_text(timeout=5000)
            raise RuntimeError(f'Patient not found or Medication Record button unavailable for IC {ic}. Register patient manually first. Page: {body[:500]}')
        page.wait_for_timeout(1000)
        page.get_by_text('Create New Medication Record', exact=False).click(force=True, timeout=30000)
        page.wait_for_timeout(1500)

        icd = _clean(row.get('doc2us_icd_code')) or _clean(row.get('diagnosis_search')) or _clean(row.get('doc2us_indication')) or 'BA00.Z'
        self._add_diagnosis(page, icd)
        self._add_drug(page, row)
        self._continue_and_submit_questionnaire(page, row)

        after_count = None
        verification_screenshot = ''
        status = 'SUBMITTED_UNVERIFIED' if self.final_submit else 'FILLED_NOT_SUBMITTED'
        if self.final_submit:
            after_count = self._patient_record_count(page, ic)
            verification_screenshot = str(self.screenshot_dir / f'row_{pos+1}_verified_count.png')
            page.screenshot(path=verification_screenshot, full_page=True)
            if before_count is not None and after_count is not None and after_count >= before_count + 1:
                status = 'VERIFIED'
            else:
                raise RuntimeError(
                    f'Final submit clicked, but EPS count did not increase for IC {ic}. '
                    f'Before={before_count}, after={after_count}. Check screenshot: {verification_screenshot}'
                )

        shot = self.screenshot_dir / f'row_{pos+1}_submitted.png'
        page.screenshot(path=str(shot), full_page=True)
        return {
            'row': int(pos),
            'patient_ic': ic,
            'patient_name': patient,
            'medication': medication,
            'status': status,
            'before_count': before_count,
            'after_count': after_count,
            'url': page.url,
            'screenshot': str(shot),
            'verification_screenshot': verification_screenshot,
        }

    def _add_diagnosis(self, page, icd_text: str) -> None:
        # The new-medication page already opens with one blank diagnosis row.
        # Clicking Add Diagnosis first creates a second unused blank row, and
        # Doc2Us blocks Continue with: "Please fill in all diagnoses and remove unused rows."
        if not page.locator('mat-select').count() and page.get_by_role('button', name='Add Diagnosis').count():
            page.get_by_role('button', name='Add Diagnosis').first.click(force=True)
            page.wait_for_timeout(300)
        select = page.locator('mat-select').first
        self._select_option(page, select, icd_text.split(' - ')[0])

    def _add_drug(self, page, row: pd.Series) -> None:
        page.get_by_role('button', name='ADD DRUG').click(force=True, timeout=30000)
        page.wait_for_timeout(700)
        modal = page.locator('ngb-modal-window')
        self._select_option(page, modal.locator('mat-select').nth(0), _clean(row.get('route')) or 'Oral')
        self._select_option(page, modal.locator('mat-select').nth(1), _clean(row.get('dose_unit')) or 'tab(s)/cap(s)')
        self._select_option(page, modal.locator('mat-select').nth(2), _clean(row.get('frequency')) or 'Once daily')
        self._select_option(page, modal.locator('mat-select').nth(3), _clean(row.get('prescribed_unit')) or 'tablet(s)')
        medication_name = _clean(row.get('item_name'))
        med_input = modal.locator('input[placeholder="Medication Name"]')
        # Doc2Us search does not match outlet stock names containing pack sizes,
        # prefixes such as [RX], or long descriptions. Search first by a cleaned
        # brand token so a real MedicationId is selected; typed-only names submit
        # medicationId=null and the portal returns HTTP 500.
        candidates = self._medication_search_terms(medication_name)
        selected = False
        for term in candidates:
            med_input.fill(term)
            page.wait_for_timeout(1200)
            results = modal.locator('.search-result')
            if results.count():
                preferred = results.filter(has_text=term).first
                if preferred.count() and preferred.is_visible():
                    preferred.click(force=True)
                else:
                    results.first.click(force=True)
                selected = True
                break
        if not selected:
            raise RuntimeError(f'Doc2Us medication search returned no selectable result for {medication_name}. Tried: {", ".join(candidates)}')
        modal.locator('.modal-title').click(force=True)
        page.wait_for_timeout(300)
        modal.locator('input[placeholder="e.g. 1"]').fill(_num_text(row.get('dose'), '1'))
        numbers = modal.locator('input[type=number]')
        if numbers.count() >= 3:
            numbers.nth(1).fill(_num_text(row.get('duration_days'), '7'))
            numbers.nth(2).fill(_num_text(row.get('prescribed_amount'), '10'))
        remark = _clean(row.get('drug_remark')) or 'refill medication'
        modal.locator('input[placeholder^="e.g. Take"]').fill(remark)
        modal.get_by_role('button', name='Add').click(force=True)
        page.wait_for_timeout(500)
        # The Add action opens a confirmation dialog. Confirm it so the drug is
        # actually inserted into the medication table before continuing.
        confirm = page.locator('ngb-modal-window').last
        if confirm.get_by_role('button', name='OK').count():
            confirm.get_by_role('button', name='OK').click(force=True)
        page.wait_for_timeout(2000)

    def _medication_search_terms(self, medication_name: str) -> list[str]:
        raw = _clean(medication_name)
        terms: list[str] = []
        for token in re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', raw):
            upper = token.upper().strip('-')
            if upper in {'RX', 'NEW', 'BOX', 'PACK', 'PACKING', 'TABLET', 'TABLETS', 'CAPSULE', 'CAPSULES', 'FILM', 'COATED'}:
                continue
            # Outlet stock names include pack-size fragments such as 4S, 2X7S,
            # and tokenized leftovers such as X7S / X15S-NEW; these never help
            # Doc2Us catalogue search and can produce empty result sets.
            if re.fullmatch(r'(?:\d+|X)?\d+(?:MG|ML|S)?(?:-NEW)?', upper):
                continue
            if re.fullmatch(r'\d+X\d+S?(?:-NEW)?', upper):
                continue
            if upper not in terms:
                terms.append(upper)
        cleaned = re.sub(r'\[[^\]]+\]', ' ', raw)
        cleaned = re.sub(r'[*#]', ' ', cleaned)
        cleaned = re.sub(r'\b\d+(?:MG|MCG|G|ML)\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b\d+(?:X\d+)?S\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:NEW|BOX|PACKING|FILM|COATED|TABLETS?|CAPSULES?)\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'[^A-Za-z0-9 /-]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip().upper()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
        for manufacturer in ['SANDOZ']:
            if manufacturer in terms and len(terms) > 1:
                terms = [t for t in terms if t != manufacturer] + [manufacturer]
        if raw and raw.upper() not in terms:
            terms.append(raw.upper())
        return terms or [raw]

    def _continue_and_submit_questionnaire(self, page, row: pd.Series) -> None:
        # Site labels have changed before, so use progressive button clicks and fill common questionnaire fields when present.
        for text in ['CONTINUE', 'Continue', 'NEXT', 'Next']:
            if self._click_text_if_visible(page, text, timeout=2500):
                break
        page.wait_for_timeout(1500)

        body_text = page.locator('body').inner_text(timeout=5000)
        # Fill screening questionnaire required fields. Doc2Us keeps previous
        # patient clinical defaults, but the pharmacist referral fields are
        # mandatory for the record to be created. Tick questionnaire mode first
        # because LTM reveals Last/Next Appointment and Follow Up fields.
        mode = _clean(row.get('questionnaire_mode')).upper()
        if mode in {'LTM', 'LONG TERM MEDICATION'}:
            box = page.locator('input[formcontrolname="ltm"]').first
            if box.count() and not box.is_checked():
                box.check(force=True)
                page.wait_for_timeout(300)
        elif mode in {'MINOR AILMENT', 'MINOR_AILMENT'}:
            box = page.locator('input[formcontrolname="minorAilment"]').first
            if box.count() and not box.is_checked():
                box.check(force=True)
                page.wait_for_timeout(300)
        for control, value in [
            ('input[formcontrolname="heartRate"]', _clean(row.get('hr')) or '75'),
            ('input[formcontrolname="bloodPressure"]', _clean(row.get('bp')) or '120/80'),
            ('input[formcontrolname="bloodGluccose"]', _clean(row.get('glucose')) or '6.0'),
            ('input[formcontrolname="reviewedBy"]', _clean(row.get('referred_by'))),
            ('input[formcontrolname="registerNumber"]', _clean(row.get('pharmacist_reg_no'))),
            ('input[formcontrolname="lastAppointmentDate"]', _clean(row.get('last_appointment_date'))),
            ('input[formcontrolname="nextAppointmentDate"]', _clean(row.get('next_appointment_date'))),
            ('input[formcontrolname="followUpUnder"]', _clean(row.get('follow_up_under'))),
            ('textarea[formcontrolname="remarks"]', _clean(row.get('screening_remarks')) or 'refill medication'),
        ]:
            if value:
                loc = page.locator(control).first
                if loc.count() and loc.is_visible():
                    loc.fill(value)

        if not self.final_submit:
            return
        # Final request buttons differ by workflow. Click the safest available positive action.
        for text in ['REQUEST PRESCRIPTION', 'Request Prescription', 'SUBMIT', 'Submit', 'CONFIRM', 'Confirm']:
            if self._click_text_if_visible(page, text, timeout=3500):
                page.wait_for_timeout(1000)
                # Submit may open a confirmation dialog; acknowledge it.
                popup = page.locator('ngb-modal-window').last
                if popup.count() and popup.get_by_role('button', name='OK').count():
                    popup.get_by_role('button', name='OK').click(force=True)
                page.wait_for_timeout(4000)
                return
        raise RuntimeError('Medication record filled, but no final request/submit button was found. Page text: ' + body_text[:800])


def submit_doc2us_queue_live(queue_path: str | Path, screenshot_dir: str | Path, final_submit: bool = True) -> dict[str, Any]:
    queue = pd.read_excel(queue_path, sheet_name='DOC2US_READY_UPLOAD', dtype=object)
    runner = Doc2UsLiveRunner(screenshot_dir=screenshot_dir, headless=True, final_submit=final_submit)
    result = runner.run_queue(queue)
    return {
        'submitted_count': result.submitted_count,
        'failed_count': result.failed_count,
        'results': result.results,
        'screenshot_dir': result.screenshot_dir,
    }
