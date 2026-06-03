EQOVEX / active ingredient medication search fix

Problem:
Doc2Us catalogue returned no selectable medication for brand/product name EQOVEX 90MG 10S.
Old live runner tried only: EQOVEX, EQOVEX 90MG 10S.

Fix:
Live runner now tries product/brand search terms first, then raw active ingredient from Item Description.
For EQOVEX 90MG 10S with active_ingredients ETORICOXIB it now tries:
EQOVEX -> EQOVEX 90MG 10S -> ETORICOXIB

This still selects a real Doc2Us catalogue item; it does not free-type a medication with null MedicationId.

Verification:
pytest result after patch: 26 passed, 1 warning.
Targeted candidate test output: ['EQOVEX', 'EQOVEX 90MG 10S', 'ETORICOXIB']
