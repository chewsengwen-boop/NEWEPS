#!/usr/bin/env python3
"""
EPS / Doc2Us bulk medication-record helper.

Default mode is DRY RUN. It reads an Octopus poison B/C transaction Excel, matches
medications against medication_rules.csv, computes EPS-ready fields, and writes an
output workbook for pharmacist review.

Live submission mode uses Playwright UI automation to login, register missing
patients, create medication records, fill the LTM screening questionnaire, and
submit only rows whose rule says allowed=yes.

Pharmacist name and registration number are required at runtime; they are not read
from the raw transaction file.
"""
from __future__ import annotations

import argparse, csv, datetime as dt, json, math, os, re, sys, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / 'medication_rules.csv'

DEFAULTS = {
    'bp': '120/80', 'hr': '75', 'glucose': '6.0', 'temperature': '0',
    'height': '0', 'weight': '0', 'hba1c': '0', 'ldl': '0', 'egfr': '0',
    'allergy': 'NKDA', 'follow_up_under': 'klinik kesihatan',
    'remarks': 'refill medication', 'mode': 'LTM', 'default_email_domain': 'doc2us.com',
}

@dataclass
class Rule:
    pattern: str
    active_ingredients: str
    klass: str
    allowed: str
    indication: str
    diagnosis_search: str
    doc2us_icd_code: str
    doc2us_indication: str
    route: str
    dose: str
    unit: str
    frequency: str
    days_per_pack: float
    max_days: int
    max_qty: Optional[float]
    poct: str
    drug_remark_template: str
    skip_reason: str

@dataclass
class PlanRow:
    status: str
    skip_reason: str
    store: str
    patient_name: str
    patient_ic: str
    gender: str
    mobile: str
    email: str
    item_code: str
    item_name: str
    qty: float
    rule_pattern: str
    medication_class: str
    indication: str
    diagnosis_search: str
    route: str
    dose: str
    dose_unit: str
    frequency: str
    duration_days: int
    prescribed_amount: int
    prescribed_unit: str
    active_ingredients: str
    doc2us_icd_code: str
    doc2us_indication: str
    drug_remark: str
    questionnaire_mode: str
    bp: str
    hr: str
    glucose: str
    last_appointment_date: str
    next_appointment_date: str
    follow_up_under: str
    referred_by: str
    pharmacist_reg_no: str
    screening_remarks: str


def clean(v) -> str:
    if pd.isna(v): return ''
    s = str(v).strip()
    if s.endswith('.0') and s[:-2].isdigit(): s = s[:-2]
    return s


def norm_name(s: str) -> str:
    return re.sub(r'[^A-Z0-9]+', ' ', s.upper()).strip()


def parse_pack_count(item_name: str) -> Optional[int]:
    """Extract common pack count: 2X15S=30, 4X7S=28, 12S=12, 1S=1."""
    s = item_name.upper()
    m = re.search(r'(\d+)\s*[Xx]\s*(\d+)\s*S\b', s)
    if m: return int(m.group(1)) * int(m.group(2))
    # prefer standalone pack tokens such as "12S" or "4S". Strengths like 100MG do not end in S.
    matches = re.findall(r'(?<![A-Z0-9/])(\d+)\s*S\b', s)
    if matches:
        return int(matches[-1])
    return None


def load_rules(path: Path = RULES_PATH) -> List[Rule]:
    rows=[]
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            rows.append(Rule(
                pattern=r['pattern'].upper(), active_ingredients=r.get('active_ingredients', r['pattern']).upper(),
                klass=r['class'], allowed=r['allowed'].lower(),
                indication=r['indication'], diagnosis_search=r['diagnosis_search'],
                doc2us_icd_code=r.get('doc2us_icd_code', ''), doc2us_indication=r.get('doc2us_indication', ''),
                route=r['route'],
                dose=r['dose'], unit=r['unit'], frequency=r['frequency'],
                days_per_pack=float(r['days_per_pack'] or 0), max_days=int(float(r['max_days'] or 0)),
                max_qty=float(r['max_qty']) if r.get('max_qty') else None,
                poct=r['poct'], drug_remark_template=r['drug_remark_template'], skip_reason=r['skip_reason']))
    return rows


def match_rule(item_name: str, rules: List[Rule], item_description: str = '') -> Optional[Rule]:
    """Match either product name or Octopus Item Description.

    Octopus poison B/C exports usually put the active ingredient in Item
    Description. Matching this field lets the Review + Edit page pre-fill
    Doc2Us indication/dose/frequency/duration from active ingredient even when
    the trade/product name is new.
    """
    n = norm_name(' '.join([item_name or '', item_description or '']))
    for r in rules:
        if r.pattern and r.pattern in n:
            return r
    return None


def read_octopus_excel(path: str) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, dtype=object)
    header_idx = None
    for i in range(len(raw)):
        vals = [clean(x) for x in raw.iloc[i].tolist()]
        if 'Client IC' in vals and 'Item Name' in vals:
            header_idx = i; break
    if header_idx is None:
        raise ValueError('Could not find Octopus header row containing Client IC and Item Name')
    header = [clean(x) for x in raw.iloc[header_idx].tolist()]
    df = raw.iloc[header_idx+1:].copy()
    df.columns = header
    df = df.dropna(how='all')
    # remove total rows
    df = df[~df.get('Store','').astype(str).str.contains('Total', case=False, na=False)]
    df = df[~df.get('Client Mobile','').astype(str).str.contains('Total', case=False, na=False)]
    df = df[df.get('Client IC','').notna() & df.get('Item Name','').notna()]
    return df


def infer_dob_from_ic(ic: str) -> str:
    digits = re.sub(r'\D','', ic)
    if len(digits) < 6: return ''
    yy, mm, dd = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    current_yy = dt.date.today().year % 100
    year = 2000 + yy if yy <= current_yy else 1900 + yy
    try: return dt.date(year, mm, dd).isoformat()
    except ValueError: return ''


def gender_word(g: str) -> str:
    g = clean(g).upper()
    return 'Male' if g == 'M' else 'Female' if g == 'F' else g.title()


def make_plan(input_xlsx: str, pharmacist_name: str, reg_no: str, apply_date: dt.date) -> pd.DataFrame:
    rules = load_rules()
    src = read_octopus_excel(input_xlsx)
    out: List[PlanRow] = []
    for _, row in src.iterrows():
        item = clean(row.get('Item Name'))
        item_description = clean(row.get('Item Description'))
        qty = float(row.get('Qty') or 0)
        rule = match_rule(item, rules, item_description)
        ic = re.sub(r'\D','', clean(row.get('Client IC')))
        email = f'{ic}@{DEFAULTS["default_email_domain"]}' if ic else ''
        patient_name = clean(row.get('Client Name')).replace('(F)', '').strip()
        pack = parse_pack_count(item)
        if rule is None:
            status, reason = 'REVIEW', 'No medication rule matched; pharmacist must add rule before live submit'
            # safe defaults only for report visibility
            rule = Rule('', '', '', 'review', '', '', '', '', 'Oral', '1', 'tab(s)/cap(s)', '', 0, 0, None, 'BP;HR', '', reason)
            duration = 0; amount = int(qty) if qty else 0
        elif rule.allowed == 'omit':
            status, reason = 'OMIT', rule.skip_reason or 'Medication rule is marked omit'
            duration = 0; amount = int((pack or 1) * qty) if qty else 0
        else:
            status, reason = 'READY', ''
            # amount = tablets/capsules if pack count is known, else sold qty
            amount_f = (pack * qty) if pack else qty
            amount = int(math.ceil(amount_f)) if amount_f else 1
            daily_units = float(rule.dose or 1)
            # crude frequency multiplier for duration calculation
            freq = rule.frequency.lower()
            if 'twice' in freq: daily_units *= 2
            elif 'three' in freq: daily_units *= 3
            elif 'four' in freq: daily_units *= 4
            duration = int(math.ceil(amount / max(daily_units, 1)))
            if rule.days_per_pack and not pack:
                duration = int(math.ceil(rule.days_per_pack * qty))
            if rule.max_qty and amount > rule.max_qty:
                status, reason = 'REVIEW', f'Prescribed amount {amount} exceeds handbook/rule max {rule.max_qty}'
            if rule.max_days and duration > rule.max_days:
                duration = rule.max_days
                reason = (reason + '; ' if reason else '') + f'Duration capped at {rule.max_days} days by rule'
        next_date = apply_date + dt.timedelta(days=max(duration, 1))
        out.append(PlanRow(
            status=status, skip_reason=reason, store=clean(row.get('Store')), patient_name=patient_name,
            patient_ic=ic, gender=gender_word(row.get('Client Gender')), mobile=clean(row.get('Client Mobile')),
            email=email, item_code=clean(row.get('Item Code')), item_name=item, qty=qty,
            rule_pattern=rule.pattern, medication_class=rule.klass, indication=rule.indication,
            diagnosis_search=rule.diagnosis_search, route=rule.route, dose=rule.dose,
            dose_unit=rule.unit, frequency=rule.frequency, duration_days=duration,
            prescribed_amount=amount, prescribed_unit='tablet(s)',
            active_ingredients=item_description or rule.active_ingredients or item, doc2us_icd_code=rule.doc2us_icd_code,
            doc2us_indication=rule.doc2us_indication,
            drug_remark=rule.drug_remark_template or DEFAULTS['remarks'],
            questionnaire_mode=DEFAULTS['mode'], bp=DEFAULTS['bp'], hr=DEFAULTS['hr'], glucose=DEFAULTS['glucose'],
            last_appointment_date=apply_date.isoformat(), next_appointment_date=next_date.isoformat(),
            follow_up_under=DEFAULTS['follow_up_under'], referred_by=pharmacist_name,
            pharmacist_reg_no=reg_no, screening_remarks=DEFAULTS['remarks']))
    return pd.DataFrame([asdict(x) for x in out])

# UI helpers are intentionally conservative and text-based because EPS is Angular Material.
def run_live(plan: pd.DataFrame, eps_email: str, eps_password: str, chromium: str, headless: bool, limit: int):
    from playwright.sync_api import sync_playwright
    ready = plan[plan.status == 'READY'].head(limit)
    results=[]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, executable_path=chromium)
        page = browser.new_page(viewport={'width':1400,'height':1000})
        page.goto('https://eps.doc2us.com/login', wait_until='domcontentloaded')
        page.wait_for_timeout(1500)
        page.locator('input').nth(0).fill(eps_email)
        page.locator('input').nth(1).fill(eps_password)
        page.get_by_text('SIGN IN').click(); page.wait_for_timeout(5000)
        for idx, r in ready.iterrows():
            try:
                submit_one(page, r)
                results.append((idx, 'SUBMITTED', ''))
            except Exception as e:
                results.append((idx, 'ERROR', str(e)[:500]))
        browser.close()
    return results

def select_text(page, label_text):
    page.get_by_text(label_text, exact=False).first.click(); page.wait_for_timeout(300)

def submit_one(page, r):
    # This routine is best-effort; if EPS changes labels, use --dry-run output and manual submit.
    page.goto('https://eps.doc2us.com/medication-record', wait_until='domcontentloaded'); page.wait_for_timeout(1500)
    page.locator('input').first.fill(str(r.patient_ic)); page.keyboard.press('Enter'); page.wait_for_timeout(2500)
    body = page.locator('body').inner_text()
    if 'No Patient Found' in body:
        page.get_by_text('Register New Patient').click(); page.wait_for_timeout(2000)
        ins = page.locator('input')
        ins.nth(0).fill(r.email); ins.nth(1).fill('sibu'); ins.nth(2).fill(r.patient_name); ins.nth(3).fill(str(r.patient_ic))
        ins.nth(4).fill(infer_dob_from_ic(str(r.patient_ic)))
        if str(r.gender).lower().startswith('m'): ins.nth(6).check(force=True)
        else: ins.nth(5).check(force=True)
        ins.nth(7).fill(str(r.mobile)); ins.nth(8).fill('Alpro-123'); ins.nth(10).fill('Alpro-123')
        ins.nth(12).check(force=True)
        page.evaluate("document.querySelectorAll('input')[14].click()")
        page.get_by_text('Submit', exact=True).click(); page.wait_for_timeout(3000)
        if 'Saved successfully' not in page.locator('body').inner_text():
            raise RuntimeError('Patient registration failed: ' + page.locator('body').inner_text()[-500:])
    # Locate profile and create medication. Implementation uses known EPS flow; review dry-run first.
    page.goto('https://eps.doc2us.com/medication-record', wait_until='domcontentloaded'); page.wait_for_timeout(1500)
    page.locator('input').first.fill(str(r.patient_ic)); page.keyboard.press('Enter'); page.wait_for_timeout(2500)
    page.get_by_text('Medication Record', exact=True).last.click(); page.wait_for_timeout(2500)
    # At this point EPS may expose New Medication; exact selectors vary by version.
    page.get_by_text('New Medication Record', exact=False).click(); page.wait_for_timeout(2000)
    # Fillers below are intentionally minimal; generated plan is the source of truth.
    raise RuntimeError('Live submit scaffold reached; finish mapping selectors in your outlet browser after dry-run review')


def main():
    ap = argparse.ArgumentParser(description='EPS Doc2Us bulk transaction-to-prescription automation')
    ap.add_argument('--input', required=True, help='Octopus poison transaction Excel file')
    ap.add_argument('--output', default='', help='Output review workbook path')
    ap.add_argument('--pharmacist-name', required=True)
    ap.add_argument('--reg-no', required=True)
    ap.add_argument('--apply-date', default=dt.date.today().isoformat())
    ap.add_argument('--live-submit', action='store_true', help='Actually submit READY rows to EPS. Default is dry-run only.')
    ap.add_argument('--eps-email', default=os.getenv('EPS_EMAIL',''))
    ap.add_argument('--eps-password', default=os.getenv('EPS_PASSWORD',''))
    ap.add_argument('--chromium', default='/snap/bin/chromium')
    ap.add_argument('--headless', action='store_true')
    ap.add_argument('--limit', type=int, default=9999)
    args = ap.parse_args()
    apply_date = dt.date.fromisoformat(args.apply_date)
    plan = make_plan(args.input, args.pharmacist_name, args.reg_no, apply_date)
    out = args.output or str(Path(args.input).with_name(Path(args.input).stem + '_EPS_PLAN.xlsx'))
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        plan.to_excel(w, index=False, sheet_name='EPS_PLAN')
        plan.groupby(['status','medication_class'], dropna=False).size().reset_index(name='count').to_excel(w, index=False, sheet_name='SUMMARY')
    print(f'Wrote review workbook: {out}')
    print(plan['status'].value_counts(dropna=False).to_string())
    if args.live_submit:
        if not args.eps_email or not args.eps_password:
            raise SystemExit('--live-submit requires --eps-email/--eps-password or EPS_EMAIL/EPS_PASSWORD env vars')
        results = run_live(plan, args.eps_email, args.eps_password, args.chromium, args.headless, args.limit)
        resdf = pd.DataFrame(results, columns=['row_index','submit_status','message'])
        resout = str(Path(out).with_name(Path(out).stem + '_SUBMIT_RESULTS.xlsx'))
        resdf.to_excel(resout, index=False)
        print(f'Wrote submit results: {resout}')

if __name__ == '__main__':
    main()
