#!/usr/bin/env python3
"""Doc2Us EPS browser automation skeleton for the deploy queue.

Safety design:
- Default is dry-run. It logs planned actions but does not click final live submit/request buttons.
- Patient registration and prescription request steps are represented as explicit confirmation gates.
- Use this after pharmacist review of DOC2US_READY_UPLOAD. Doctor approval remains required inside Doc2Us.

Usage:
  python scripts/doc2us_dry_run_import.py jobs/<job_id>/*_DOC2US_READY_QUEUE.xlsx --email qsbjc1@alpropharmacy.com

Set DOC2US_PASSWORD in the environment or pass --password for local testing.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.web_logic import build_doc2us_automation_manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('queue_xlsx', help='Doc2Us READY queue workbook')
    ap.add_argument('--email', default=os.environ.get('DOC2US_EMAIL', 'qsbjc1@alpropharmacy.com'))
    ap.add_argument('--password', default=os.environ.get('DOC2US_PASSWORD', ''))
    ap.add_argument('--dry-run', action='store_true', default=True)
    args = ap.parse_args()

    manifest = build_doc2us_automation_manifest(args.queue_xlsx, dry_run=True)
    manifest['login_email'] = args.email
    manifest['password_supplied'] = bool(args.password)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    print('\nNEXT IMPLEMENTATION STEP: map these manifest actions to exact Doc2Us selectors in Playwright.')
    print('Final request/submit buttons must remain blocked behind a pharmacist confirmation gate.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
