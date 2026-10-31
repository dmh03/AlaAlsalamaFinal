from sqlmodel import select

from alasalama import llm
from alasalama.models import AuditEvent, Episode, Message, NurseTask, Patient
from alasalama.router import NO_ANSWER_NOTE, handle_reply
from alasalama.schemas import CheckInForm

Q = CheckInForm(questions=["Are there any suggested medicines I should take?"], confidence=0.95)


def _ep(s, name="Sara"):
    p = s.exec(select(Patient).where(Patient.name == name)).first()
    return s.exec(select(Episode).where(Episode.patient_id == p.id)).first()


def _fake(monkeypatch, text):
    real = llm.complete
    monkeypatch.setattr(llm, "complete", lambda purpose, *a, **k: text if purpose == "answer_question" else real(purpose, *a, **k))


def _answers(s, ep):
    return [m for m in s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out")).all()
            if m.kind == "answer"]


def test_llm_answer_is_sent_and_the_nurse_still_gets_the_question(db, monkeypatch):
    _fake(monkeypatch, "I can't recommend medicines in chat. Your doctor or nurse can advise you, and the care team will follow up.")
    with db.session() as s:
        ep = _ep(s)
        assert handle_reply(s, ep.id, "x", Q)["route"] == "question"
        assert _answers(s, ep)[-1].text.startswith("I can't recommend medicines")
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
        assert "llm_answer_sent" in [a.event for a in s.exec(select(AuditEvent)).all()]


def test_guard_blocked_answer_falls_back_to_the_fixed_note(db, monkeypatch):
    _fake(monkeypatch, "You should stop taking your medicine and take ibuprofen 400 mg.")
    with db.session() as s:
        ep = _ep(s)
        handle_reply(s, ep.id, "x", Q)
        assert _answers(s, ep)[-1].text == NO_ANSWER_NOTE["en"]
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
        assert "llm_answer_blocked" in [a.event for a in s.exec(select(AuditEvent)).all()]


def test_empty_or_overlong_model_output_falls_back(db, monkeypatch):
    for bad in ("", "word " * 150):
        _fake(monkeypatch, bad)
        with db.session() as s:
            ep = _ep(s)
            handle_reply(s, ep.id, "x", Q)
            assert _answers(s, ep)[-1].text == NO_ANSWER_NOTE["en"]


def test_offline_mode_still_uses_the_fixed_note(db):
    with db.session() as s:
        ep = _ep(s)
        handle_reply(s, ep.id, "x", Q)
        assert _answers(s, ep)[-1].text == NO_ANSWER_NOTE["en"]
