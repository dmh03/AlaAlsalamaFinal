from datetime import datetime

from sqlmodel import select

from alasalama import clock
from alasalama.models import Episode, Message, NurseTask, Patient
from alasalama.router import _pleasantry_kind, handle_reply
from alasalama.schemas import CheckInForm
from alasalama.tick import tick


def _ep(s, name):
    p = s.exec(select(Patient).where(Patient.name == name)).first()
    return s.exec(select(Episode).where(Episode.patient_id == p.id)).first()


def _closed_checkin(s, name):
    """Send the day-1 check-in, then answer it so the check-in is closed (our last message is the thank-you)."""
    clock.set_time(s, datetime(2026, 11, 2, 23, 0))
    tick(s)
    ep = _ep(s, name)
    assert handle_reply(s, ep.id, "pain 2, all fine", CheckInForm(pain_score=2, confidence=0.95))["route"] == "routine"
    return ep


def _out(s, ep):
    return s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out").order_by(Message.id)).all()


def test_pleasantry_matching_is_whole_message_only():
    for text in ["Thank you", "thanks!", "Thank you 🙏", "ok", "شكراً", "شكرا جزيلا", "الله يعطيك العافية", "السلام عليكم", "Hello"]:
        assert _pleasantry_kind(text), text
    for text in ["thanks but my chest hurts", "ok but I can't breathe", "شكرا بس عندي ألم", "4", "I have a question",
                 "thank you " * 10]:
        assert _pleasantry_kind(text) is None, text


def test_thank_you_after_closed_checkin_is_acknowledged_not_questioned(db):
    with db.session() as s:
        ep = _closed_checkin(s, "Sara")
        before = len(_out(s, ep))
        r = handle_reply(s, ep.id, "Thank you")
        assert r["route"] == "acknowledged"
        out = _out(s, ep)
        assert len(out) == before + 1 and out[-1].kind == "thanks" and out[-1].text.startswith("You're welcome")
        assert "clarify" not in [m.kind for m in out]
        assert s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all() == []


def test_arabic_thanks_gets_an_arabic_acknowledgement(db):
    with db.session() as s:
        ep = _closed_checkin(s, "محمد")
        assert handle_reply(s, ep.id, "شكراً")["route"] == "acknowledged"
        assert _out(s, ep)[-1].text.startswith("العفو")


def test_thanks_plus_symptom_is_never_swallowed(db):
    with db.session() as s:
        ep = _closed_checkin(s, "Sara")
        r = handle_reply(s, ep.id, "thanks but my chest hurts")
        assert r["route"] != "acknowledged"
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1


def test_ok_while_a_checkin_is_open_is_not_swallowed(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
        ep = _ep(s, "Sara")
        assert handle_reply(s, ep.id, "ok")["route"] != "acknowledged"


def test_missing_pain_outside_a_checkin_is_not_unclear(db):
    with db.session() as s:
        ep = _closed_checkin(s, "Sara")
        r = handle_reply(s, ep.id, "I'm fine", CheckInForm(pain_score=None, confidence=0.9))
        assert r["route"] == "routine"
        assert "clarify" not in [m.kind for m in _out(s, ep)]
