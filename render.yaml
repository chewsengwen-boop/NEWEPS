import pandas as pd

from app.doc2us_live import _email_from_ic, _gender_from_row, _ic_to_dob, _normalise_phone
from app.web_logic import DOC2US_DEPLOY_COLUMNS


def test_patient_registration_helpers_derive_ic_dob_email_and_phone():
    assert _ic_to_dob("930801136321") == "1993-08-01"
    assert _email_from_ic("930801-13-6321") == "930801136321@doc2us.com"
    assert _normalise_phone("012-345 6789") == "60123456789"


def test_patient_registration_gender_uses_raw_gender_then_ic_fallback():
    assert _gender_from_row(pd.Series({"gender": "Female", "patient_ic": "930801136321"})) == "Female"
    assert _gender_from_row(pd.Series({"gender": "", "patient_ic": "930801136321"})) == "Male"
    assert _gender_from_row(pd.Series({"gender": "", "patient_ic": "930801136322"})) == "Female"


def test_doc2us_deploy_queue_includes_gender_for_registration():
    assert "gender" in DOC2US_DEPLOY_COLUMNS
    assert DOC2US_DEPLOY_COLUMNS.index("gender") < DOC2US_DEPLOY_COLUMNS.index("mobile")
