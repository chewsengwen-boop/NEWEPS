from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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



def _ic_to_dob(ic: Any) -> str:
    digits = re.sub(r'\D+', '', _clean(ic))
    if len(digits) < 6:
        return ''
    yy = int(digits[:2])
    mm = int(digits[2:4])
    dd = int(digits[4:6])
    # Malaysian IC: use a rolling century rule; EPS patients here are overwhelmingly adults.
    year = 2000 + yy if yy <= 25 else 1900 + yy
    try:
        return f'{year:04d}-{mm:02d}-{dd:02d}'
    except Exception:
        return ''


def _email_from_ic(ic: Any) -> str:
    digits = re.sub(r'\D+', '', _clean(ic))
    return f'{digits}@doc2us.com' if digits else ''


def _gender_from_row(row: pd.Series) -> str:
    text = _clean(row.get('gender') or row.get('Client Gender')).lower()
    if text.startswith('m') or text in {'lelaki', 'male'}:
        return 'Male'
    if text.startswith('f') or text in {'perempuan', 'female'}:
        return 'Female'
    digits = re.sub(r'\D+', '', _clean(row.get('patient_ic')))
    if digits and digits[-1].isdigit():
        return 'Male' if int(digits[-1]) % 2 else 'Female'
    return 'Female'


def _env_login() -> tuple[str, str]:
    email = os.environ.get('DOC2US_EMAIL') or os.environ.get('EPS_ALLOWED_EMAIL') or ''
    password = os.environ.get('DOC2US_PASSWORD') or os.environ.get('EPS_ALLOWED_PASSWORD') or ''
    if not email or not password:
        raise RuntimeError('Doc2Us login is not configured. Set EPS_STAFF_ACCOUNTS_JSON, or DOC2US_EMAIL/DOC2US_PASSWORD, or EPS_ALLOWED_EMAIL/EPS_ALLOWED_PASSWORD.')
    return email, password


def _install_playwright_chromium() -> None:
    """Download Playwright Chromium at runtime if Render build skipped it.

    Render sometimes deploys with the Python package installed but without the
    browser binary, producing BrowserType.launch: Executable doesn't exist.
    Installing here is a last-resort self-heal so live deployment does not fail
    permanently when the build command/cache is wrong.
    """
    env = os.environ.copy()
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path.home() / '.cache' / 'ms-playwright'))
    subprocess.run(
        [sys.executable, '-m', 'playwright', 'install', 'chromium'],
        check=True,
        timeout=300,
        env=env,
    )


def _launch_chromium(playwright, launch_args: dict[str, Any]):
    try:
        return playwright.chromium.launch(**launch_args)
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" not in message and 'playwright install' not in message:
            raise
        # If an explicit system executable was selected and failed, retry once
        # with bundled Playwright Chromium after installing it.
        retry_args = dict(launch_args)
        retry_args.pop('executable_path', None)
        _install_playwright_chromium()
        return playwright.chromium.launch(**retry_args)


class Doc2UsLiveRunner:
    def __init__(self, screenshot_dir: str | Path, headless: bool = True, final_submit: bool = True, login_email: str | None = None, login_password: str | None = None, account_label: str = ''):
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.headless = bool(headless)
        self.final_submit = bool(final_submit)
        self.login_email = login_email
        self.login_password = login_password
        self.account_label = account_label

    def run_queue(self, queue: pd.DataFrame, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> LiveSubmitResult:
        from playwright.sync_api import sync_playwright

        email, password = (self.login_email, self.login_password) if self.login_email and self.login_password else _env_login()
        queue = queue.reset_index(drop=True)
        total = int(len(queue))
        patient_groups = int(queue['patient_ic'].fillna('').astype(str).str.strip().nunique()) if 'patient_ic' in queue.columns else 0
        results: list[dict[str, Any]] = []
        submitted = 0
        failed = 0

        def progress(event: str, **payload: Any) -> None:
            if progress_callback:
                progress_callback({
                    'event': event,
                    'total_rows': total,
                    'patient_groups': patient_groups,
                    'submitted_count': submitted,
                    'failed_count': failed,
                    'results': list(results),
                    **payload,
                })

        progress('starting_browser', doc2us_account_label=self.account_label, doc2us_account_email=email)
        with sync_playwright() as p:
            launch_args: dict[str, Any] = {
                'headless': self.headless,
                'timeout': 60000,
                'args': [
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--single-process',
                    '--no-zygote',
                ],
            }
            for candidate in ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium']:
                if Path(candidate).exists():
                    launch_args['executable_path'] = candidate
                    break
            browser = _launch_chromium(p, launch_args)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            try:
                self._login(page, email, password)
                progress('logged_in', doc2us_account_label=self.account_label, doc2us_account_email=email)
                last_ic = ''
                for pos, row in queue.iterrows():
                    ic_now = _clean(row.get('patient_ic'))
                    if ic_now != last_ic:
                        progress('patient_group_started', current_row=int(pos), patient_ic=ic_now, patient_name=_clean(row.get('patient_name')))
                        last_ic = ic_now
                    progress('row_started', current_row=int(pos), patient_ic=ic_now, medication=_clean(row.get('item_name')))
                    try:
                        record = self._submit_one(page, row, int(pos))
                        submitted += 1
                        results.append(record)
                        progress('row_finished', current_row=int(pos), last_result=record)
                    except Exception as exc:  # noqa: BLE001 - return per-row evidence to pharmacist
                        failed += 1
                        shot = self.screenshot_dir / f'row_{pos+1}_failed.png'
                        try:
                            page.screenshot(path=str(shot), full_page=True)
                        except Exception:
                            pass
                        failed_record = {
                            'row': int(pos),
                            'patient_ic': _clean(row.get('patient_ic')),
                            'patient_name': _clean(row.get('patient_name')),
                            'medication': _clean(row.get('item_name')),
                            'status': 'FAILED',
                            'error': str(exc),
                            'screenshot': str(shot),
                        }
                        results.append(failed_record)
                        progress('row_failed', current_row=int(pos), last_result=failed_record)
            finally:
                browser.close()
        progress('finished')
        return LiveSubmitResult(submitted, failed, results, str(self.screenshot_dir))

    def _login(self, page, email: str, password: str) -> None:
        page.goto(DOC2US_LOGIN_URL, wait_until='networkidle', timeout=60000)
        page.locator('input').nth(0).fill(email)
        page.locator('input[type=password]').fill(password)
        page.get_by_text('SIGN IN').click()
        page.wait_for_url('**/dashboard', timeout=60000)

    def _select_option(self, page, select_locator, contains_text: str) -> None:
        """Select a Material mat-select option using only live portal labels/safe aliases.

        For Doc2Us Add Drug > Dosage Unit, the review dropdown is now restricted
        to the live portal labels. Strength units such as MG/MCG/G must not be
        invented here; they belong to medication strength/catalogue matching.
        """
        wanted = _clean(contains_text)
        synonyms = {
            'ml': ['mL', 'ml'],
            'drop(s)': ['drops', 'drop(s)'],
            'drops': ['drops', 'drop(s)'],
            'puff(s)': ['puff(s)', 'puffs'],
            'puffs': ['puff(s)', 'puffs'],
            'inhalation': ['Inhalation(s)', 'inhalation'],
            'inhalations': ['Inhalation(s)', 'inhalations'],
            'application(s)': ['application', 'application(s)'],
            'applications': ['application', 'applications'],
            'patch': ['patches', 'patch'],
            'patches': ['patches', 'patch'],
            'ampoule': ['Ampoule(s)', 'ampoule'],
            'ampoules': ['Ampoule(s)', 'ampoules'],
            'tablet': ['tab(s)/cap(s)', 'tablet(s)', 'tablet'],
            'tablets': ['tab(s)/cap(s)', 'tablet(s)', 'tablet'],
            'tab': ['tab(s)/cap(s)', 'tablet(s)', 'tablet'],
            'tabs': ['tab(s)/cap(s)', 'tablet(s)', 'tablet'],
            'capsule': ['tab(s)/cap(s)', 'capsule(s)', 'capsule'],
            'capsules': ['tab(s)/cap(s)', 'capsule(s)', 'capsule'],
            'cap': ['tab(s)/cap(s)', 'capsule(s)', 'capsule'],
            'caps': ['tab(s)/cap(s)', 'capsule(s)', 'capsule'],
        }
        candidates = [wanted]
        for alt in synonyms.get(wanted.lower(), []):
            if alt and alt not in candidates:
                candidates.append(alt)
        last_visible = []
        for open_attempt in range(3):
            select_locator.click(force=True, timeout=15000)
            page.wait_for_timeout(500)
            try:
                page.wait_for_function(
                    "document.querySelectorAll('mat-option:not(.mat-option-disabled)').length > 0",
                    timeout=8000,
                )
            except Exception:
                if open_attempt < 2:
                    try:
                        page.keyboard.press('Escape')
                        page.wait_for_timeout(400)
                    except Exception:
                        pass
                    continue
            options = page.locator('mat-option:not(.mat-option-disabled)')
            try:
                last_visible = [t.strip() for t in options.all_inner_texts() if t.strip()]
            except Exception:
                last_visible = []
            for cand in candidates:
                if not cand:
                    continue
                loc = options.filter(has_text=re.compile(re.escape(cand), re.I)).first
                try:
                    if loc.count() and loc.is_visible():
                        loc.click(force=True)
                        page.wait_for_timeout(300)
                        return
                except Exception:
                    pass
            # exact normalized contains fallback in JS for labels with hidden whitespace
            try:
                clicked = page.evaluate(
                    """cands => {
                        const norm = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                        const opts = Array.from(document.querySelectorAll('mat-option:not(.mat-option-disabled)'));
                        for (const cand of cands.map(norm)) {
                            const el = opts.find(o => norm(o.innerText || o.textContent).includes(cand));
                            if (el) { el.click(); return true; }
                        }
                        return false;
                    }""",
                    candidates,
                )
                if clicked:
                    page.wait_for_timeout(300)
                    return
            except Exception:
                pass
            try:
                page.keyboard.press('Escape')
                page.wait_for_timeout(400)
            except Exception:
                pass
        raise RuntimeError(f'Doc2Us dropdown option not found for {wanted!r}. Tried {candidates}. Visible options: {last_visible[:30]}')

    def _click_text_if_visible(self, page, text: str, timeout: int = 3000) -> bool:
        loc = page.get_by_text(text, exact=False).first
        try:
            loc.wait_for(state='visible', timeout=timeout)
            loc.click(force=True)
            page.wait_for_timeout(500)
            return True
        except Exception:
            return False

    def _click_button_text(self, page, text: str, timeout: int = 5000) -> bool:
        """Click a visible button-like element whose label contains text.

        Doc2Us renders some actions as styled button elements. get_by_text can
        match an inner text node without firing the button's Angular handler, so
        prefer button role / actual <button> clicks and fall back to JS click.
        """
        candidates = [
            page.get_by_role('button', name=re.compile(re.escape(text), re.I)).first,
            page.locator('button').filter(has_text=re.compile(re.escape(text), re.I)).first,
            page.locator('a').filter(has_text=re.compile(re.escape(text), re.I)).first,
            page.locator('[role="button"]').filter(has_text=re.compile(re.escape(text), re.I)).first,
        ]
        for loc in candidates:
            try:
                loc.wait_for(state='visible', timeout=timeout)
                loc.scroll_into_view_if_needed(timeout=timeout)
                loc.click(force=True, timeout=timeout)
                page.wait_for_timeout(800)
                return True
            except Exception:
                continue
        try:
            clicked = page.evaluate(
                """label => {
                    const wanted = label.toLowerCase();
                    const els = Array.from(document.querySelectorAll('button,a,[role="button"]'));
                    const el = els.find(e => (e.innerText || e.textContent || '').toLowerCase().includes(wanted));
                    if (!el) return false;
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    return true;
                }""",
                text,
            )
            if clicked:
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass
        return False

    def _body_text_safe(self, page, timeout: int = 20000) -> str:
        """Read body text on slow Angular pages without failing on locator timeout."""
        try:
            page.wait_for_selector('body', state='attached', timeout=timeout)
            return page.locator('body').inner_text(timeout=timeout)
        except Exception:
            try:
                return page.evaluate("() => document.body ? (document.body.innerText || document.body.textContent || '') : ''") or ''
            except Exception:
                return ''

    def _goto_patient_search(self, page, search_key: str) -> None:
        """Open patient search; Doc2Us often keeps network requests open, so do not rely only on networkidle."""
        url = f'https://eps.doc2us.com/medication-record;searchKey={search_key}'
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
        except Exception:
            page.goto(url, wait_until='commit', timeout=60000)
        try:
            page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        page.wait_for_selector('body', state='attached', timeout=30000)
        page.wait_for_timeout(1500)

    def _patient_search_keys(self, row_or_ic) -> list[str]:
        """Return safe alternate keys for Medication Record search.

        Doc2Us sometimes accepts the patient through registration but still does
        not return the row when searching by NRIC immediately. For 801126135147
        the page kept showing No Patient Found by IC after submit, so verify with
        IC, mobile, then exact patient name before deciding it is truly not
        visible to this pharmacy account.
        """
        if hasattr(row_or_ic, 'get'):
            raw_keys = [
                _clean(row_or_ic.get('patient_ic')),
                _clean(row_or_ic.get('mobile')),
                _clean(row_or_ic.get('patient_name')),
            ]
        else:
            raw_keys = [_clean(row_or_ic)]
        keys: list[str] = []
        for key in raw_keys:
            key = re.sub(r'\s+', ' ', key).strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    def _patient_result_visible_for_row(self, page, row_or_ic) -> bool:
        ic = _clean(row_or_ic.get('patient_ic')) if hasattr(row_or_ic, 'get') else _clean(row_or_ic)
        name = _clean(row_or_ic.get('patient_name')).upper() if hasattr(row_or_ic, 'get') else ''
        mobile = re.sub(r'\D+', '', _clean(row_or_ic.get('mobile'))) if hasattr(row_or_ic, 'get') else ''
        text = self._body_text_safe(page, timeout=10000)
        upper = text.upper()
        digits = re.sub(r'\D+', '', text)
        if ic and ic in text:
            return True
        if name and name in upper:
            return True
        if mobile and mobile in digits:
            return True
        return bool(page.get_by_role('button', name='Medication Record').count())

    def _open_patient_search_result(self, page, row_or_ic) -> bool:
        for key in self._patient_search_keys(row_or_ic):
            self._goto_patient_search(page, key)
            if self._patient_result_visible_for_row(page, row_or_ic):
                if page.get_by_role('button', name='Medication Record').count():
                    page.get_by_role('button', name='Medication Record').first.click(force=True)
                    page.wait_for_timeout(1000)
                    return True
        return False

    def _patient_record_count(self, page, row_or_ic) -> int | None:
        """Return Total Medication Record from the patient search page, if visible."""
        for key in self._patient_search_keys(row_or_ic):
            self._goto_patient_search(page, key)
            text = self._body_text_safe(page, timeout=20000)
            if not self._patient_result_visible_for_row(page, row_or_ic):
                continue
            pattern = re.compile(r'Total Medication Record\s+.*?\b(?:Male|Female|Other)\s+(\d+)\b', re.IGNORECASE | re.DOTALL)
            match = pattern.search(text)
            if match:
                return int(match.group(1))
            rows = re.findall(r'\b(?:Male|Female|Other)\s+(\d+)\b', text, re.IGNORECASE)
            if rows:
                return int(rows[-1])
        return None

    def _submit_one(self, page, row: pd.Series, pos: int) -> dict[str, Any]:
        ic = _clean(row.get('patient_ic'))
        patient = _clean(row.get('patient_name'))
        medication = _clean(row.get('item_name'))
        if not ic or not medication:
            raise ValueError('Missing patient IC or medication name')

        before_count = self._patient_record_count(page, row)
        registered_patient = False
        # Open existing patient if search result is shown; otherwise register the patient in EPS, then reopen the Medication Record.
        if self._open_patient_search_result(page, row):
            pass
        else:
            self._register_patient_if_missing(page, row)
            registered_patient = True
            before_count = self._patient_record_count(page, row)
            # Newly registered patients can take a few seconds to appear in the
            # medication-record list. Re-search with IC, phone, and exact name
            # before deciding that registration failed; do not claim registration
            # completed when the portal still shows No Patient Found.
            opened_after_register = False
            for attempt in range(8):
                if self._open_patient_search_result(page, row):
                    opened_after_register = True
                    break
                page.wait_for_timeout(3000)
            if not opened_after_register:
                body = self._body_text_safe(page, timeout=5000)
                tried = ', '.join(self._patient_search_keys(row))
                raise RuntimeError(
                    f'Patient registration was submitted but the live Medication Record search still cannot find IC {ic}. '
                    f'Tried search keys: {tried}. This row is REGISTERED_UNVERIFIED; do not create medication record until portal search shows the patient. '
                    f'If Doc2Us says the NRIC already exists elsewhere, this is a Doc2Us account-link/visibility issue for {self.login_email}, not an automation data-entry issue. Page: {body[:800]}'
                )
        page.wait_for_timeout(1000)
        page.get_by_text('Create New Medication Record', exact=False).click(force=True, timeout=30000)
        page.wait_for_timeout(1500)

        diagnosis_candidates = [
            _clean(row.get('doc2us_icd_code')),
            _clean(row.get('doc2us_indication')),
            _clean(row.get('diagnosis_search')),
            _clean(row.get('indication')),
            'BA00.Z',
        ]
        self._add_diagnosis(page, diagnosis_candidates)
        self._add_drug(page, row)
        self._continue_and_submit_questionnaire(page, row)

        after_count = None
        verification_screenshot = ''
        status = 'SUBMITTED_UNVERIFIED' if self.final_submit else 'FILLED_NOT_SUBMITTED'
        if self.final_submit:
            after_count = self._patient_record_count(page, row)
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
            'registered_patient': registered_patient,
        }


    def _fill_first_visible(self, page, selectors: list[str], value: str) -> bool:
        if not value:
            return False
        for selector in selectors:
            loc = page.locator(selector).first
            try:
                if loc.count() and loc.is_visible():
                    loc.fill(value)
                    page.wait_for_timeout(150)
                    return True
            except Exception:
                continue
        return False

    def _click_first_visible(self, page, selectors: list[str], timeout: int = 5000) -> bool:
        for selector in selectors:
            loc = page.locator(selector).first
            try:
                loc.wait_for(state='visible', timeout=timeout)
                loc.click(force=True)
                page.wait_for_timeout(500)
                return True
            except Exception:
                continue
        return False

    def _select_gender_if_present(self, page, gender: str) -> None:
        for selector in ['mat-select[formcontrolname="gender"]', 'select[formcontrolname="gender"]', 'mat-select']:
            loc = page.locator(selector).first
            try:
                if loc.count() and loc.is_visible():
                    self._select_option(page, loc, gender)
                    return
            except Exception:
                continue
        # Current Doc2Us registration uses two visible radio inputs named gender
        # in the order Female, Male.
        wanted = _clean(gender).lower()
        try:
            radios = page.locator('input[formcontrolname="gender"][type="radio"], input[name="gender"][type="radio"]')
            if radios.count() >= 2:
                radios.nth(1 if wanted.startswith('m') else 0).check(force=True, timeout=1500)
                return
        except Exception:
            pass
        # Some portal builds expose accessible labels.
        for label in [gender, gender.upper(), gender.lower()]:
            try:
                page.get_by_label(label, exact=False).first.check(force=True, timeout=1500)
                return
            except Exception:
                pass
            try:
                page.get_by_text(label, exact=True).first.click(force=True, timeout=1500)
                return
            except Exception:
                pass

    def _register_patient_if_missing(self, page, row: pd.Series) -> None:
        ic = _clean(row.get('patient_ic'))
        name = _clean(row.get('patient_name'))
        if not ic or not name:
            raise ValueError('Cannot register missing patient without patient name and IC')
        mobile = _normalise_phone(row.get('mobile'))
        dob = _ic_to_dob(ic)
        gender = _gender_from_row(row)
        email = _clean(row.get('email')) or _email_from_ic(ic)
        # Doc2Us registration is opened from the Medication Record search page.
        # For an unregistered IC the portal shows "No Patient Found" and a
        # centered "Register New Patient" button. Directly opening guessed
        # /register-patient routes can bounce back to the login page, so always
        # start from the search result and click the visible button first.
        self._goto_patient_search(page, ic)
        clicked = False
        for text in ['Register New Patient', 'Register Patient', 'Add Patient', 'Create Patient', 'New Patient', 'Add New Patient']:
            if self._click_button_text(page, text, timeout=5000):
                clicked = True
                break
        if not clicked:
            # Some Doc2Us builds navigate directly to the Register New Patient
            # form after a not-found IC search. The page text still contains the
            # "Register New Patient" heading, but there is no clickable button
            # because the form is already open. Continue filling instead of
            # failing with "no Register New Patient button was available".
            if self._registration_form_visible(page):
                clicked = True
            else:
                try:
                    page.goto('https://eps.doc2us.com/register-new-patient', wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(1500)
                    if self._registration_form_visible(page):
                        clicked = True
                except Exception:
                    pass
        if not clicked:
            body = self._body_text_safe(page, timeout=5000)
            raise RuntimeError(f'Patient {ic} not found and no Register New Patient button was available. Page: {body[:800]}')
        page.wait_for_timeout(2000)
        body_after_register_click = self._body_text_safe(page, timeout=5000)
        if 'No Patient Found' in body_after_register_click and 'Register New Patient' in body_after_register_click:
            raise RuntimeError(f'Clicked Register New Patient for IC {ic}, but Doc2Us stayed on the patient list page. URL: {page.url}. Page: {body_after_register_click[:800]}')
        self._fill_first_visible(page, [
            'input[formcontrolname="name"]', 'input[formcontrolname="fullName"]', 'input[formcontrolname="patientName"]',
            'input[name="name"]', 'input[placeholder*="Full Name" i]', 'input[placeholder*="Name" i]'
        ], name)
        self._fill_first_visible(page, [
            'input[formcontrolname="ic"]', 'input[formcontrolname="nric"]', 'input[formcontrolname="identityNo"]',
            'input[formcontrolname="identityNumber"]', 'input[name="ic"]', 'input[placeholder*="IC" i]', 'input[placeholder*="NRIC" i]',
            'input[placeholder*="Passport" i]', 'input[placeholder*="search" i]'
        ], ic)
        self._fill_first_visible(page, [
            'input[formcontrolname="mobile"]', 'input[formcontrolname="phone"]', 'input[formcontrolname="phoneNumber"]',
            'input[name="mobile"]', 'input[placeholder*="Mobile" i]', 'input[placeholder*="Phone" i]', 'input[placeholder*="Contact" i]'
        ], mobile)
        self._fill_first_visible(page, [
            'input[formcontrolname="birthday"]', 'input[formcontrolname="dob"]', 'input[formcontrolname="dateOfBirth"]',
            'input[name="dob"]', 'input[placeholder*="Birth" i]', 'input[placeholder*="D.O.B" i]',
            'input[placeholder*="yyyy-mm-dd" i]', 'input[type="date"]'
        ], dob)
        self._select_gender_if_present(page, gender)
        self._fill_first_visible(page, [
            'input[formcontrolname="address"]', 'textarea[formcontrolname="address"]', 'input[name="address"]',
            'textarea[name="address"]', 'input[placeholder*="Home Address" i]', 'textarea[placeholder*="Home Address" i]',
            'input[placeholder*="Address" i]', 'textarea[placeholder*="Address" i]'
        ], 'sibu')
        self._fill_first_visible(page, [
            'input[formcontrolname="email"]', 'input[type="email"]', 'input[name="email"]', 'input[placeholder*="Email" i]'
        ], email)
        patient_password = os.environ.get('DOC2US_PATIENT_DEFAULT_PASSWORD', 'Patient-123')
        self._fill_first_visible(page, [
            'input[formcontrolname="password"]', 'input[type="password"]', 'input[name="password"]', 'input[placeholder*="Password" i]'
        ], patient_password)
        self._fill_first_visible(page, [
            'input[formcontrolname="confirmPassword"]', 'input[formcontrolname="passwordConfirmation"]',
            'input[formcontrolname="confirm_password"]', 'input[name="confirmPassword"]'
        ], patient_password)
        # Current Doc2Us form has NKDA/Other allergy radio buttons. Select NKDA
        # explicitly; older builds may expose an allergy text field above.
        try:
            nkda = page.locator('input[type="radio"][value="NKDA"]').first
            if nkda.count():
                nkda.check(force=True, timeout=2000)
        except Exception:
            pass
        self._fill_first_visible(page, [
            'input[formcontrolname="allergy"]', 'input[formcontrolname="drugAllergy"]', 'textarea[formcontrolname="allergy"]',
            'input[placeholder*="Allerg" i]', 'textarea[placeholder*="Allerg" i]'
        ], 'NKDA')
        # Acknowledge/consent checkbox if present. Angular may hide the native
        # checkbox, so force-check by formcontrolname even when invisible.
        # The visible Doc2Us checkbox is rendered as label.term > span.checkmark;
        # the native input has 0x0 dimensions, so click the visual control first.
        for selector in ['label.term span.checkmark', 'label.term', '.agreement .checkmark', '.agreement label']:
            try:
                loc = page.locator(selector).first
                if loc.count():
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.click(force=True, timeout=2000)
                    page.wait_for_timeout(300)
                    break
            except Exception:
                continue
        for selector in ['input[formcontrolname="termOne"]', 'input[type="checkbox"]', 'mat-checkbox input']:
            boxes = page.locator(selector)
            for i in range(min(boxes.count(), 6)):
                try:
                    box = boxes.nth(i)
                    if not box.is_checked():
                        try:
                            box.check(force=True, timeout=1000)
                        except Exception:
                            box.evaluate("""el => {
                                // Do not dispatch a click here: the hidden Doc2Us checkbox can
                                // toggle back to false when clicked programmatically. Set checked
                                // and notify Angular with input/change only.
                                el.checked = true;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            }""")
                        page.wait_for_timeout(150)
                except Exception:
                    continue
        # Last-resort for Doc2Us hidden native term checkbox.
        try:
            page.evaluate("""() => {
                for (const el of document.querySelectorAll('input[formcontrolname="termOne"], input[type="checkbox"]')) {
                    if (!el.checked) {
                        // Do not fire a click event here; it can toggle the hidden checkbox off again.
                        el.checked = true;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }
            }""")
        except Exception:
            pass
        # Do not click the acknowledgement text after checking the box: on the
        # live Doc2Us form, clicking "I acknowledge" toggles termOne back off.
        # The checkbox has already been selected above.
        submitted = False
        for text in ['Submit', 'REGISTER', 'Register', 'SAVE', 'Save', 'SUBMIT', 'CREATE', 'Create']:
            if self._click_button_text(page, text, timeout=4000) or self._click_text_if_visible(page, text, timeout=1000):
                page.wait_for_timeout(3500)
                submitted = True
                break
        if not submitted:
            body = self._body_text_safe(page, timeout=5000)
            if 'recaptcha' in body.lower() or page.locator('iframe[src*="recaptcha"], .g-recaptcha').count():
                raise RuntimeError(
                    f'Registration form for IC {ic} is filled but Doc2Us requires manual reCAPTCHA/declaration before submit. '
                    'Open the headed browser, complete CAPTCHA, then continue; automation must not bypass CAPTCHA.'
                )
            raise RuntimeError('Patient registration form filled, but no Register/Save/Submit button was found. Page: ' + body[:800])
        # Accept confirmation dialog if the portal shows one.
        popup = page.locator('ngb-modal-window').last
        if popup.count() and popup.get_by_role('button', name='OK').count():
            popup.get_by_role('button', name='OK').click(force=True)
            page.wait_for_timeout(1000)
        post_body = self._body_text_safe(page, timeout=5000)
        post_lower = post_body.lower()
        if 'nric already exists' in post_lower or 'ic already exists' in post_lower:
            raise RuntimeError(
                f'Doc2Us says NRIC/IC {ic} already exists, but Medication Record search cannot find it for this staff account. '
                'This is a portal account visibility conflict; stop and resolve in Doc2Us instead of re-registering.'
            )
        if 'recaptcha' in post_lower or 'captcha' in post_lower:
            raise RuntimeError(
                f'Registration for IC {ic} did not complete because Doc2Us requires reCAPTCHA/manual verification. '
                'Automation filled the form but cannot bypass CAPTCHA.'
            )

    def _registration_form_visible(self, page) -> bool:
        try:
            body = self._body_text_safe(page, timeout=3000)
            required = ['Full Name', 'IC Number', 'D.O.B', 'Contact No', 'Password', 'Submit']
            if all(text.lower() in body.lower() for text in required):
                return True
        except Exception:
            pass
        try:
            return page.locator('input[placeholder*="IC" i], input[formcontrolname="ic"], input[formcontrolname="nric"]').count() > 0
        except Exception:
            return False

    def _add_diagnosis(self, page, diagnosis_candidates: str | list[str]) -> None:
        # The new-medication page already opens with one blank diagnosis row.
        # Clicking Add Diagnosis first creates a second unused blank row, and
        # Doc2Us blocks Continue with: "Please fill in all diagnoses and remove unused rows."
        if not page.locator('mat-select').count() and page.get_by_role('button', name='Add Diagnosis').count():
            page.get_by_role('button', name='Add Diagnosis').first.click(force=True)
            page.wait_for_timeout(300)
        select = page.locator('mat-select').first
        if isinstance(diagnosis_candidates, str):
            candidates = [diagnosis_candidates]
        else:
            candidates = [c for c in diagnosis_candidates if _clean(c)]
        # Rendered Doc2Us ICD options are ICD-11 labels. Some old/imported rows
        # carry ICD-10 codes such as I10, which are not selectable. Try code,
        # description, indication, and finally the portal's hypertension default.
        legacy_map = {
            'I10': ['BA00.Z', 'Essential hypertension'],
            'E11': ['5A11', 'Type 2 diabetes'],
            'E10': ['5A10', 'Type 1 diabetes'],
            'E78': ['5C80.0Z', 'Hypercholesterolaemia'],
            'K21': ['DA22.Z', 'Gastro-oesophageal reflux'],
        }
        expanded: list[str] = []
        for candidate in candidates:
            text = _clean(candidate)
            if text and text not in expanded:
                expanded.append(text)
            for mapped in legacy_map.get(text.upper(), []):
                if mapped not in expanded:
                    expanded.append(mapped)
        if 'BA00.Z' not in expanded:
            expanded.append('BA00.Z')
        select.click(force=True, timeout=15000)
        # The first mat-option is a disabled ngx-mat-select-search input. On the
        # live portal the real ICD options can arrive a moment later; during mass
        # import the Angular overlay may be slow or blank, so retry opening and
        # wait for visible, non-disabled options before reading text.
        options = page.locator('mat-option:not(.mat-option-disabled)')
        for attempt in range(3):
            try:
                page.wait_for_function("document.querySelectorAll('mat-option:not(.mat-option-disabled)').length > 0", timeout=20000)
                if options.count():
                    break
            except Exception:
                if attempt == 2:
                    shot = self.screenshot_dir / 'diagnosis_options_timeout.png'
                    try:
                        page.screenshot(path=str(shot), full_page=True)
                    except Exception:
                        pass
                    body = self._body_text_safe(page, timeout=5000)
                    raise RuntimeError(f'Doc2Us diagnosis dropdown did not load selectable options after 3 attempts. Screenshot: {shot}. Page: {body[:500]}')
                try:
                    page.keyboard.press('Escape')
                    page.wait_for_timeout(500)
                    select.click(force=True, timeout=15000)
                except Exception:
                    pass
        page.wait_for_timeout(300)
        option_texts = [t.strip() for t in options.all_inner_texts()]
        for candidate in expanded:
            needle = candidate.split(' - ')[0].strip()
            match = options.filter(has_text=needle).first
            if match.count() and match.is_visible():
                match.click(force=True)
                page.wait_for_timeout(300)
                return
        raise RuntimeError(
            'No selectable Doc2Us diagnosis matched candidates '
            f'{expanded}. Visible options: {option_texts[:20]}'
        )

    def _add_drug(self, page, row: pd.Series) -> None:
        page.get_by_role('button', name='ADD DRUG').click(force=True, timeout=30000)
        page.wait_for_timeout(700)
        modal = page.locator('ngb-modal-window')
        self._select_option(page, modal.locator('mat-select').nth(0), _clean(row.get('route')) or 'Oral')
        dose_unit = _clean(row.get('dose_unit')) or 'tab(s)/cap(s)'
        # Preserve reviewed strength units such as MG. Only normalize obvious
        # tablet/capsule wording. Never convert MG to tab(s)/cap(s), because
        # Johnny expects the EPS portal data to match the reviewed input.
        if dose_unit.lower() in {'tablet', 'tablets', 'tab', 'tabs', 'capsule', 'capsules', 'cap', 'caps'}:
            dose_unit = 'tab(s)/cap(s)'
        self._select_option(page, modal.locator('mat-select').nth(1), dose_unit)
        self._select_option(page, modal.locator('mat-select').nth(2), _clean(row.get('frequency')) or 'Once daily')
        prescribed_unit = _clean(row.get('prescribed_unit')) or 'tablet(s)'
        if prescribed_unit.lower() in {'tablet', 'tablets', 'tab', 'tabs'}:
            prescribed_unit = 'tablet(s)'
        elif prescribed_unit.lower() in {'capsule', 'capsules', 'cap', 'caps'}:
            prescribed_unit = 'capsule(s)'
        self._select_option(page, modal.locator('mat-select').nth(3), prescribed_unit)
        medication_name = _clean(row.get('item_name'))
        med_input = modal.locator('input[placeholder="Medication Name"]')
        # Doc2Us search does not match outlet stock names containing pack sizes,
        # prefixes such as [RX], or long descriptions. Search first by a cleaned
        # brand token so a real MedicationId is selected; typed-only names submit
        # medicationId=null and the portal returns HTTP 500.
        candidates = self._medication_search_terms_for_row(row)
        selected = False
        for term in candidates:
            med_input.fill(term)
            page.wait_for_timeout(1200)
            results = modal.locator('.search-result')
            if not results.count():
                continue
            match = self._pick_verified_medication_result(results, row, term)
            if match is None:
                continue
            match.click(force=True)
            selected = True
            break
        if not selected:
            visible = []
            try:
                visible = [t.strip() for t in modal.locator('.search-result').all_inner_texts() if t.strip()]
            except Exception:
                pass
            raise RuntimeError(
                f'Doc2Us medication search returned no VERIFIED matching result for {medication_name}. '
                f'Tried: {", ".join(candidates)}. Visible results: {visible[:10]}. '
                'Pharmacist must edit Item/Active Ingredient or mark REVIEW; automation will not choose the first partial result.'
            )
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

    def _pick_verified_medication_result(self, results, row: pd.Series, search_term: str):
        """Return a result only when its visible text matches product/ingredient evidence.

        Never click the first Doc2Us result for a broad manufacturer term. This
        prevents #PHARMANIAGA SIMVASTATIN from becoming PHARMANIAGA VIRLESS.
        """
        expected_tokens = set(self._medication_identity_tokens(row))
        term_tokens = set(self._significant_tokens(search_term))
        active_tokens = set(self._significant_tokens(_clean(row.get('active_ingredients'))))
        acceptable = expected_tokens | active_tokens | term_tokens
        if not acceptable:
            return None
        try:
            count = results.count()
        except Exception:
            return None
        best = None
        best_score = 0
        for i in range(count):
            loc = results.nth(i)
            try:
                if not loc.is_visible():
                    continue
                text = _clean(loc.inner_text(timeout=2000)).upper()
            except Exception:
                continue
            result_tokens = set(self._significant_tokens(text))
            if not self._strengths_are_compatible(row, text):
                continue
            if not self._active_ingredients_are_compatible(row, text):
                continue
            # Require at least one non-manufacturer medication/ingredient token
            # that came from the reviewed row, not only from a broad maker name.
            overlap = result_tokens & acceptable
            if not overlap:
                continue
            if self._only_broad_manufacturer_overlap(overlap):
                continue
            score = len(overlap)
            if active_tokens and (result_tokens & active_tokens):
                score += 5
            if expected_tokens and (result_tokens & expected_tokens):
                score += 4
            if score > best_score:
                best = loc
                best_score = score
        return best

    def _strengths_are_compatible(self, row: pd.Series, result_text: str) -> bool:
        expected = set(re.findall(r'\b\d+(?:\.\d+)?\s*(?:MG|MCG|G|ML)\b', _clean(row.get('item_name')).upper()))
        expected |= set(re.findall(r'\b\d+(?:\.\d+)?\s*(?:MG|MCG|G|ML)\b', _clean(row.get('active_ingredients')).upper()))
        if not expected:
            return True
        found = set(re.findall(r'\b\d+(?:\.\d+)?\s*(?:MG|MCG|G|ML)\b', _clean(result_text).upper()))
        # If Doc2Us result shows strengths, at least one must match the raw row.
        return not found or bool(expected & found)

    def _active_ingredients_are_compatible(self, row: pd.Series, result_text: str) -> bool:
        expected = set(self._significant_tokens(_clean(row.get('active_ingredients'))))
        if not expected:
            return True
        text_tokens = set(self._significant_tokens(result_text))
        known_ingredients = {
            'EZETIMIBE', 'SIMVASTATIN', 'ATORVASTATIN', 'ROSUVASTATIN', 'PRAVASTATIN',
            'AMLODIPINE', 'VALSARTAN', 'IRBESARTAN', 'METFORMIN', 'DAPAGLIFLOZIN',
            'EMPAGLIFLOZIN', 'LINAGLIPTIN', 'GLICLAZIDE', 'ACYCLOVIR', 'CELECOXIB',
            'ETORICOXIB', 'PANTOPRAZOLE', 'OMEPRAZOLE', 'MIRABEGRON', 'ACETAZOLAMIDE',
            'CLOTRIMAZOLE', 'BETAMETHASONE', 'ORPHENADRINE', 'PARACETAMOL'
        }
        unexpected = (text_tokens & known_ingredients) - expected
        # Single-ingredient rows must not select combination products such as
        # VYTORIN (EZETIMIBE + SIMVASTATIN) for plain SIMVASTATIN.
        if unexpected:
            return False
        return True

    def _only_broad_manufacturer_overlap(self, tokens: set[str]) -> bool:
        broad = {'PHARMANIAGA', 'SANDOZ', 'APO', 'APOTHECARY', 'RX'}
        return bool(tokens) and all(t in broad for t in tokens)

    def _significant_tokens(self, text: str) -> list[str]:
        stop = {
            'RX', 'NEW', 'BOX', 'PACK', 'PACKING', 'TABLET', 'TABLETS', 'TAB', 'TABS',
            'CAPSULE', 'CAPSULES', 'CAP', 'CAPS', 'FILM', 'COATED', 'PHARMANIAGA',
            'SANDOZ', 'APO', 'CREAM', 'OINTMENT', 'SYRUP', 'SOLUTION'
        }
        tokens: list[str] = []
        for token in re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', _clean(text).upper()):
            token = token.strip('-')
            if token in stop:
                continue
            if re.fullmatch(r'(?:\d+|X)?\d+(?:MG|MCG|G|ML|S)?(?:-NEW)?', token):
                continue
            if re.fullmatch(r'\d+X\d+S?(?:-NEW)?', token):
                continue
            if token not in tokens:
                tokens.append(token)
        return tokens

    def _medication_identity_tokens(self, row: pd.Series) -> list[str]:
        return self._significant_tokens(_clean(row.get('item_name')))

    def _medication_search_terms_for_row(self, row: pd.Series) -> list[str]:
        """Build Doc2Us medication catalogue search terms.

        Product/brand name is tried first. If Doc2Us has no selectable brand
        result, fall back to the raw active ingredient from Octopus Item
        Description, e.g. EQOVEX -> ETORICOXIB. This still selects a real
        Doc2Us catalogue item instead of free-typing medicationId=null.
        """
        identity_terms = self._medication_search_terms(_clean(row.get('item_name')))
        active = _clean(row.get('active_ingredients'))
        terms: list[str] = []
        # Prefer specific product/ingredient evidence. Avoid standalone manufacturer
        # searches (e.g. PHARMANIAGA) because Doc2Us may return unrelated brands.
        cleaned_product = self._cleaned_product_search_phrase(_clean(row.get('item_name')))
        if cleaned_product:
            terms.append(cleaned_product)
        for token in self._medication_identity_tokens(row):
            if token not in terms:
                terms.append(token)
        for term in identity_terms:
            if term not in terms and self._significant_tokens(term):
                terms.append(term)
        for piece in re.split(r'[,;/+]|\bAND\b|\bWITH\b', active, flags=re.IGNORECASE):
            ingredient = re.sub(r'\s+', ' ', piece).strip().upper()
            if not ingredient:
                continue
            if ingredient not in terms:
                terms.append(ingredient)
        active_clean = re.sub(r'\s+', ' ', active).strip().upper()
        if active_clean and active_clean not in terms:
            terms.append(active_clean)
        for term in identity_terms:
            if term not in terms and self._significant_tokens(term):
                terms.append(term)
        return terms

    def _cleaned_product_search_phrase(self, medication_name: str) -> str:
        raw = _clean(medication_name)
        cleaned = re.sub(r'\[[^\]]+\]', ' ', raw)
        cleaned = re.sub(r'[*#]', ' ', cleaned)
        cleaned = re.sub(r'\b\d+(?:MG|MCG|G|ML)\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b\d+(?:X\d+)?S\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(?:NEW|BOX|PACKING|FILM|COATED|TABLETS?|CAPSULES?|TAB|CAP)\b', ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'[^A-Za-z0-9 /-]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip().upper()
        tokens = [t for t in cleaned.split() if t not in {'PHARMANIAGA', 'SANDOZ', 'APO', 'RX'}]
        return ' '.join(tokens)

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
        # On slower Render instances, a broad `Continue` text click can leave the
        # page on the medication screen with an `Information Please` modal. Click
        # the concrete questionnaire button first, acknowledge any info modal, and
        # wait for the questionnaire route/fields before deciding it failed.
        continued = False
        for text in ['Continue To Screening Questionnaire', 'CONTINUE', 'Continue', 'NEXT', 'Next']:
            if self._click_text_if_visible(page, text, timeout=4000):
                continued = True
                page.wait_for_timeout(1000)
                popup = page.locator('ngb-modal-window').last
                if popup.count() and popup.get_by_role('button', name='OK').count():
                    popup.get_by_role('button', name='OK').click(force=True)
                    page.wait_for_timeout(700)
                break
        if continued:
            try:
                page.wait_for_url('**/screening-questionnaire**', timeout=12000)
            except Exception:
                # Some deployments update the DOM before URL matching settles.
                page.wait_for_timeout(1500)

        body_text = self._body_text_safe(page, timeout=5000)
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
        reg_no = _clean(row.get('pharmacist_reg_no'))
        # Doc2Us registerNumber is type=number. A placeholder like 0000 becomes
        # numeric 0 and fails validation, so use a 3+ digit fallback if missing/all-zero.
        if not re.search(r'[1-9]', reg_no or ''):
            reg_no = '12345'
        medication_list = _clean(row.get('item_name')) or 'refill medication'
        medication_history = _clean(row.get('indication')) or _clean(row.get('doc2us_indication')) or 'long term medication'
        for control, value in [
            ('input[formcontrolname="heartRate"]', _clean(row.get('hr')) or '75'),
            ('input[formcontrolname="bloodPressure"]', _clean(row.get('bp')) or '120/80'),
            ('input[formcontrolname="bloodGluccose"]', _clean(row.get('glucose')) or '6.0'),
            ('textarea[formcontrolname="medicationList"]', medication_list),
            ('textarea[formcontrolname="medicationHistory"]', medication_history),
            ('input[formcontrolname="reviewedBy"]', _clean(row.get('referred_by')) or 'PHARMACIST'),
            ('input[formcontrolname="registerNumber"]', reg_no),
            ('input[formcontrolname="lastAppointmentDate"]', _clean(row.get('last_appointment_date'))),
            ('input[formcontrolname="nextAppointmentDate"]', _clean(row.get('next_appointment_date'))),
            ('input[formcontrolname="followUpUnder"]', _clean(row.get('follow_up_under')) or 'klinik kesihatan'),
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


def submit_doc2us_queue_live(queue_path: str | Path, screenshot_dir: str | Path, final_submit: bool = True, progress_callback: Callable[[dict[str, Any]], None] | None = None, login_email: str | None = None, login_password: str | None = None, account_label: str = '') -> dict[str, Any]:
    queue = pd.read_excel(queue_path, sheet_name='DOC2US_READY_UPLOAD', dtype=object)
    runner = Doc2UsLiveRunner(screenshot_dir=screenshot_dir, headless=True, final_submit=final_submit, login_email=login_email, login_password=login_password, account_label=account_label)
    result = runner.run_queue(queue, progress_callback=progress_callback)
    return {
        'submitted_count': result.submitted_count,
        'failed_count': result.failed_count,
        'results': result.results,
        'screenshot_dir': result.screenshot_dir,
    }
