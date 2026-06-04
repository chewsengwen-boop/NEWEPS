"""Register Doc2Us patient 801126135147 only, without creating medication record.

Run on the deployed/ops machine with these env vars set:
  DOC2US_EMAIL or EPS_STAFF_EMAIL
  DOC2US_PASSWORD or EPS_STAFF_PASSWORD
Optional:
  DOC2US_PATIENT_DEFAULT_PASSWORD
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.doc2us_live import Doc2UsLiveRunner  # noqa: E402

RAW_PATH = Path(os.environ.get(
    "EPS_RAW_XLSX",
    "/mnt/c/Users/User/Downloads/Newest Doc2us_03-06-2026 (Web) (1) (1).xlsx",
))
TARGET_IC = "801126135147"


def load_target_row() -> pd.Series:
    raw = pd.read_excel(RAW_PATH, header=None, dtype=str).fillna("")
    hit = raw[raw.apply(lambda r: r.astype(str).str.contains(TARGET_IC, regex=False).any(), axis=1)]
    if hit.empty:
        raise SystemExit(f"Target IC {TARGET_IC} not found in {RAW_PATH}")
    r = hit.iloc[0]
    return pd.Series({
        "outlet": r.iloc[0],
        "patient_name": r.iloc[2],
        "patient_ic": r.iloc[3],
        "gender": r.iloc[4],
        "mobile": r.iloc[5],
        "active_ingredients": r.iloc[8],
        "item_name": r.iloc[9],
        "email": "",
    })


def main() -> None:
    email = os.environ.get("DOC2US_EMAIL") or os.environ.get("EPS_STAFF_EMAIL")
    password = os.environ.get("DOC2US_PASSWORD") or os.environ.get("EPS_STAFF_PASSWORD")
    if not email or not password:
        raise SystemExit("Missing DOC2US_EMAIL/DOC2US_PASSWORD or EPS_STAFF_EMAIL/EPS_STAFF_PASSWORD env vars")

    row = load_target_row()
    print("Registering patient only:")
    print(row.to_string())
    runner = Doc2UsLiveRunner(Path("/tmp/eps_register_801126135147"), headless=False, final_submit=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            runner._login(page, email, password)
            before = runner._patient_record_count(page, TARGET_IC)
            print(f"Before search count: {before}")
            if before is not None:
                print("Patient already exists; no registration needed.")
                return
            runner._register_patient_if_missing(page, row)
            after = runner._patient_record_count(page, TARGET_IC)
            print(f"After registration search count: {after}")
            shot = Path("/tmp/eps_register_801126135147/after_registration.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot), full_page=True)
            print(f"Screenshot: {shot}")
            if after is None:
                raise SystemExit("Registration submitted, but patient still not found in Medication Record search.")
            print("REGISTERED_OR_ALREADY_AVAILABLE")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
