from datetime import datetime

import pytest
from sqlmodel import select

from alasalama import clock, guard, llm
from alasalama.models import CheckIn, Episode, Message, NurseTask, Patient
from alasalama.router import emergency_text, handle_reply
from alasalama.schemas import CheckInForm
from alasalama.templates import load_template
from alasalama.tick import tick

TEMPLATES = ["knee_replacement", "c_section", "heart_failure"]


def _ep(s, name):
    p = s.exec(select(Patient).where(Patient.name == name)).first()
    return s.exec(select(Episode).where(Episode.patient_id == p.id)).first()


def _start(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)


@pytest.mark.parametrize("name", TEMPLATES)
def test_emergency_text_is_fixed_in_both_languages(name):
    t = load_template(name)
    for lang in ("ar", "en"):
        txt = emergency_text(t, lang)
        assert txt == t["emergency"][lang] and "997" in txt
    en = t["emergency"]["en"].lower()
    assert "chest pain" in en and "trouble breathing" in en and "heavy bleeding" in en
    assert "call 997 now" in en and "alerted the on-call nurse" in en


def test_emergency_never_touches_llm_or_guard(db, monkeypatch):
    _start(db)
    calls = []

    def spy_guard(*a, **k):
        calls.append("guard")
        raise AssertionError("emergency message must not pass through the guard")

    def spy_parse(*a, **k):
        calls.append("parse")
        raise AssertionError("emergency path must not call the LLM reader/classifier")

    monkeypatch.setattr(guard, "check", spy_guard)
    monkeypatch.setattr(llm, "parse", spy_parse)
    with db.session() as s:
        ep = _ep(s, "محمد")
        form = CheckInForm(symptoms=["breathless_lying_down"], pain_score=None, confidence=0.9)
        handle_reply(s, ep.id, "ما أقدر أتنفس إذا انسدحت", form)
        assert calls == []
        m = s.exec(select(Message).where(Message.kind == "emergency")).one()
        assert m.text == emergency_text(load_template("heart_failure"), "ar")


def test_guard_block_creates_task_instead_of_sending(db):
    from alasalama.outbound import send_guarded

    _start(db)
    with db.session() as s:
        ep = _ep(s, "Sara")
        p = s.get(Patient, ep.patient_id)
        before = len(s.exec(select(Message)).all())
        assert send_guarded(s, ep, p, "Take 500 mg now", datetime(2026, 11, 3, 9, 0), "answer", generated=False) is None
        assert len(s.exec(select(Message)).all()) == before
        assert any("blocked by guard" in t.reason for t in s.exec(select(NurseTask)).all())


def test_demo_patients_behave_as_expected(db):
    _start(db)
    with db.session() as s:
        f = CheckInForm(symptoms=["wound_redness"], pain_score=None, confidence=0.9,
                        medications=[{"name": "إبرة السيولة", "taken": False}])
        assert handle_reply(s, _ep(s, "عبدالله").id, "x", f)["route"] == "concern"
        assert handle_reply(s, _ep(s, "Sara").id, "fine, pain 2", CheckInForm(pain_score=2))["route"] == "routine"
        assert not [t for t in s.exec(select(NurseTask)).all() if t.episode_id == _ep(s, "Sara").id]
        f = CheckInForm(symptoms=["breathless_lying_down", "leg_swelling"], pain_score=None)
        assert handle_reply(s, _ep(s, "محمد").id, "x", f)["route"] == "red_flag"
        f = CheckInForm(confidence=0.5, pain_score=None, unclear_reason="vague")
        assert handle_reply(s, _ep(s, "Khalid").id, "x", f)["route"] == "unclear"
        kinds = [m.kind for m in s.exec(select(Message).where(Message.episode_id == _ep(s, "Khalid").id)).all()]
        assert "clarify" in kinds
        ci = s.exec(select(CheckIn).where(CheckIn.episode_id == _ep(s, "Sara").id, CheckIn.day == 3)).first()
        assert ci.status in ("replied", "sent", "scheduled")


def test_question_sends_approved_answer_verbatim(db, monkeypatch):
    from alasalama import router

    _start(db)
    monkeypatch.setattr(router, "_match_questions", lambda qs, tpl, lang, t, ep: {qs[0]: "showering"})
    with db.session() as s:
        ep = _ep(s, "نورة")
        f = CheckInForm(pain_score=None, questions=["متى أقدر أتروش؟"], confidence=0.9)
        assert handle_reply(s, ep.id, "متى أقدر أتروش؟", f)["route"] == "question"
        want = load_template("c_section")["qa"][0]["a_ar"]
        assert want in [m.text for m in s.exec(select(Message).where(Message.kind == "answer")).all()]


@pytest.mark.integration
def test_reader_on_labelled_set_red_flag_recall():
    import json

    from eval import run_understanding

    r = run_understanding.run(True)
    got = r["claude_reader"]["red_flag_recall"]
    print(f"\nclaude reader red-flag recall: {got:.0%}  route accuracy: {r['claude_reader']['route_accuracy']:.0%}")
    assert got >= 0.90
