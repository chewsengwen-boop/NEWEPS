import pandas as pd

from app.doc2us_live import Doc2UsLiveRunner


def test_medication_search_terms_fall_back_to_active_ingredient_for_unknown_brand():
    runner = Doc2UsLiveRunner('/tmp/doc2us-test-screenshots')
    row = pd.Series({
        'item_name': 'EQOVEX 90MG 10S',
        'active_ingredients': 'ETORICOXIB',
    })

    terms = runner._medication_search_terms_for_row(row)

    assert terms[:2] == ['EQOVEX', 'EQOVEX 90MG 10S']
    assert 'ETORICOXIB' in terms
    assert terms.index('ETORICOXIB') > terms.index('EQOVEX 90MG 10S')


def test_medication_search_terms_split_combination_active_ingredients():
    runner = Doc2UsLiveRunner('/tmp/doc2us-test-screenshots')
    row = pd.Series({
        'item_name': 'NORGESIC 35/450MG 12S (NEW) BOX PACKING',
        'active_ingredients': 'ORPHENADRINE, PARACETAMOL',
    })

    terms = runner._medication_search_terms_for_row(row)

    assert 'ORPHENADRINE' in terms
    assert 'PARACETAMOL' in terms
