import pandas as pd

from app.doc2us_live import Doc2UsLiveRunner
from app.web_logic import apply_review_defaults


def test_pharmaniaga_simvastatin_search_terms_do_not_start_with_broad_manufacturer(tmp_path):
    runner = Doc2UsLiveRunner(tmp_path)
    row = pd.Series({
        'item_name': '#PHARMANIAGA SIMVASTATIN 20MG 10S',
        'active_ingredients': 'SIMVASTATIN',
    })
    terms = runner._medication_search_terms_for_row(row)
    assert terms[0] == 'SIMVASTATIN'
    assert 'PHARMANIAGA' not in terms[:2]


def test_review_unmatched_3x10_row_does_not_guess_duration_7():
    plan = pd.DataFrame([{
        'status': 'REVIEW',
        'item_name': '*GLYXAMBI 10/5MG 3X10S',
        'active_ingredients': 'LINAGLIPTIN/ EMPAGLIFLOZIN',
        'qty': 1,
        'duration_days': 0,
        'prescribed_amount': 0,
        'route': '',
        'dose': '',
        'dose_unit': '',
        'frequency': '',
        'prescribed_unit': '',
        'indication': '',
        'diagnosis_search': '',
        'doc2us_icd_code': '',
        'doc2us_indication': '',
    }])
    updated = apply_review_defaults(plan)
    assert updated.loc[0, 'duration_days'] in ('', 0)
    assert updated.loc[0, 'frequency'] == ''
    assert updated.loc[0, 'route'] == ''
