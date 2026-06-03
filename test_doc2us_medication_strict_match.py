from pathlib import Path

import pandas as pd

from app import eps_bulk_core


def test_item_description_active_ingredient_matches_rule_and_prefills_fields(tmp_path, monkeypatch):
    raw_rows = [
        ['Report title', '', '', '', '', '', '', '', '', '', '', '', ''],
        ['Store', 'Item Division', 'Client Name', 'Client IC', 'Client Gender', 'Client Mobile', 'Sales Person', 'Item Code', 'Item Description', 'Item Name', 'Qty', 'Sales Type', 'Net Sold Price ex. MGST Tax'],
        ['QSBFL1', '500 - POISON B', 'TEST PATIENT', '790728135127', 'M', '60123456789', 'PHARMACIST', 'L66472', 'PERINDOPRIL TERT-BUTYLAMINE', 'COVINACE 8MG 10S', '1', '0', '10'],
    ]
    raw_path = tmp_path / 'raw.xlsx'
    pd.DataFrame(raw_rows).to_excel(raw_path, index=False, header=False)

    monkeypatch.setattr(eps_bulk_core, 'RULES_PATH', Path(__file__).resolve().parents[1] / 'data' / 'medication_rules.csv')
    plan = eps_bulk_core.make_plan(str(raw_path), 'PHARMACIST', '12345', pd.Timestamp('2026-06-03').date())
    row = plan.iloc[0]

    assert row['active_ingredients'] == 'PERINDOPRIL TERT-BUTYLAMINE'
    assert row['indication'] == 'Hypertension'
    assert row['doc2us_icd_code'] == 'BA00.Z'
    assert row['doc2us_indication'] == 'Essential hypertension, unspecified'
    assert row['frequency'] == 'Every morning'
    assert int(row['duration_days']) == 10
    assert int(row['prescribed_amount']) == 10
    assert row['bp'] == '120/80'
    assert row['hr'] == '75'
    assert row['glucose'] == '6.0'
