"""Three reply tiers: positive for good news, a follow-up question for mild concerns, 997 plus investigation for red flags."""
from datetime import datetime

from sqlmodel import select

from alasalama import clock, guard, llm
from alasalama.models import Message, NurseTask
from alasalama.router import FOLLOWUP_MILD, INVESTIGATE_URGENT, URGENT_STILL_OPEN, handle_reply
from alasalama.schemas import CheckInForm
from alasalama.tick import tick
from test_doctors import CONCERN, _ep, _out


def _start(s, name="Sara"):
    clock.set_time(s, datetime(2026, 11, 2, 23, 0))
    tick(s)
    return _ep(s, name)


def test_mild_concern_asks_a_follow_up_before_any_appointment_offer(db):
    with db.session() as s:
        ep = _start(s)
        assert handle_reply(s, ep.id, "x", CONCERN)["route"] == "concern"
        assert _out(s, ep, "followup")[-1].text == FOLLOWUP_MILD["en"]
        assert not _out(s, ep, "offer")
        assert s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id, NurseTask.priority == "same_day")).first()


def test_red_flag_sends_997_then_fixed_investigation_questions_and_an_urgent_task(db):
    with db.session() as s:
        ep = _start(s)
        r = handle_reply(s, ep.id, "x", CheckInForm(symptoms=["heavy_bleeding"], confidence=0.95))
        assert r["route"] == "red_flag"
        out = [m for m in _out(s, ep) if m.kind in ("emergency", "followup")]
        assert [m.kind for m in out] == ["emergency", "followup"] and "997" in out[0].text
        assert out[1].text == INVESTIGATE_URGENT["en"]
        assert s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id, NurseTask.priority == "urgent")).first()


def test_red_flag_path_never_calls_the_model_or_guard(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("red-flag messages must not use the guard")

    def offline(*a, **k):
        raise llm.LLMUnavailable("offline")
    with db.session() as s:
        ep = _start(s)
        monkeypatch.setattr(llm, "complete", offline)
        monkeypatch.setattr(llm, "parse", boom)
        monkeypatch.setattr(guard, "check", boom)
        handle_reply(s, ep.id, "x", CheckInForm(symptoms=["heavy_bleeding"], confidence=0.95))
        assert [m.kind for m in _out(s, ep)[-2:]] == ["emergency", "followup"]


def test_good_news_gets_a_warm_composed_message(db, monkeypatch):
    with db.session() as s:
        ep = _start(s)
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "Wonderful news, Sara, your pain is low. See you at the next check-in.")
        r = handle_reply(s, ep.id, "all good, pain 1", CheckInForm(pain_score=1, confidence=0.95))
        assert r["route"] == "routine"
        assert "Wonderful news" in _out(s, ep, "thanks")[-1].text


def test_bad_composed_message_falls_back_to_the_fixed_thanks(db, monkeypatch):
    with db.session() as s:
        ep = _start(s)
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "Great! You can double your dose of tablets 5 mg.")
        handle_reply(s, ep.id, "all good", CheckInForm(pain_score=1, confidence=0.95))
        assert "dose" not in _out(s, ep, "thanks")[-1].text and "next check-in" in _out(s, ep, "thanks")[-1].text


def test_never_cheerful_while_an_urgent_case_is_open(db, monkeypatch):
    with db.session() as s:
        ep = _start(s)
        handle_reply(s, ep.id, "x", CheckInForm(symptoms=["heavy_bleeding"], confidence=0.95))
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "Wonderful news!")
        handle_reply(s, ep.id, "ok", CheckInForm(pain_score=2, confidence=0.95))
        assert _out(s, ep, "thanks")[-1].text == URGENT_STILL_OPEN["en"]
