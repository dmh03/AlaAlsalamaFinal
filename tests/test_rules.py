import pytest

from alasalama.rules import decide
from alasalama.schemas import CheckInForm, MedItem
from alasalama.templates import load_template

KNEE = load_template("knee_replacement")
HF = load_template("heart_failure")
CRIT = ["Enoxaparin injection"]


def F(**kw):
    kw.setdefault("pain_score", 2)
    return CheckInForm(**kw)


def test_routine():
    d = decide(F(), KNEE, CRIT)
    assert d.route == "routine" and d.reasons


def test_red_flag_and_sla():
    d = decide(F(symptoms=["chest_pain"]), KNEE, CRIT)
    assert (d.route, d.priority, d.sla_minutes) == ("red_flag", "urgent", 15)


def test_hf_red_flag_breathless_lying_down():
    assert decide(F(symptoms=["breathless_lying_down", "leg_swelling"], pain_score=None), HF, []).route == "red_flag"


@pytest.mark.parametrize("pain,route", [(6, "routine"), (7, "concern"), (10, "concern")])
def test_pain_boundary(pain, route):
    assert decide(F(pain_score=pain), KNEE, CRIT).route == route


@pytest.mark.parametrize("kw", [
    dict(symptoms_worsening=True), dict(symptoms=["wound_redness"]), dict(wants_human=True),
    dict(suspicious_input=True), dict(medications=[MedItem(name="Enoxaparin injection", taken=False)]),
])
def test_concern_triggers(kw):
    d = decide(F(**kw), KNEE, CRIT)
    assert d.route == "concern" and d.sla_minutes == 240


def test_non_critical_med_missed_is_not_concern():
    assert decide(F(medications=[MedItem(name="Paracetamol", taken=False)]), KNEE, CRIT).route == "routine"


def test_confidence_boundary():
    assert decide(F(confidence=0.69), KNEE, CRIT).route == "unclear"
    assert decide(F(confidence=0.7), KNEE, CRIT).route == "routine"


def test_missing_pain_unclear_only_when_asked():
    assert decide(F(pain_score=None), KNEE, CRIT).route == "unclear"
    assert decide(F(pain_score=None), HF, []).route == "routine"


def test_question_route():
    assert decide(F(questions=["when can I shower?"]), KNEE, CRIT).route == "question"
    assert decide(F(pain_score=None, questions=["متى أتروش؟"]), KNEE, CRIT).route == "question"


def test_precedence():
    f = F(symptoms=["chest_pain"], wants_human=True, confidence=0.1, questions=["x"])
    assert decide(f, KNEE, CRIT).route == "red_flag"
    f = F(wants_human=True, confidence=0.1, questions=["x"])
    assert decide(f, KNEE, CRIT).route == "concern"
    assert decide(F(confidence=0.1, questions=["x"]), KNEE, CRIT).route == "unclear"
    assert decide(F(questions=["x"]), KNEE, CRIT).route == "question"
