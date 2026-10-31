"""Agent decides on a check-up for routine messages; episodes never auto-close."""
from datetime import datetime

from sqlmodel import select

from alasalama import clock, llm
from alasalama.models import CheckIn, Episode, NurseTask
from alasalama.router import CHECKUP_ASK, CHECKUP_DECLINED, handle_reply
from alasalama.schemas import AppointmentTriage, CheckInForm
from alasalama.tick import tick
from test_doctors import _out
from test_reply_tiers import _start

FINE = CheckInForm(pain_score=3, confidence=0.95)


def _decide(monkeypatch, decision):
    monkeypatch.setattr(llm, "parse", lambda *a, **k: AppointmentTriage(decision=decision, reason="pinkish wound"))


def test_agent_book_sends_the_offer_and_logs_a_task(db, monkeypatch):
    _decide(monkeypatch, "book")
    with db.session() as s:
        ep = _start(s)
        handle_reply(s, ep.id, "x", FINE)
        assert _out(s, ep, "offer")
        tasks = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()
        assert any("check-up" in x.reason for x in tasks)


def test_agent_ask_then_yes_offers_and_no_declines(db, monkeypatch):
    _decide(monkeypatch, "ask")
    with db.session() as s:
        ep = _start(s)
        handle_reply(s, ep.id, "x", FINE)
        assert _out(s, ep, "checkup_ask")[-1].text == CHECKUP_ASK["en"]
        assert not _out(s, ep, "offer")
        assert handle_reply(s, ep.id, "yes")["route"] == "checkup_accepted"
        assert _out(s, ep, "offer")


def test_agent_ask_then_no(db, monkeypatch):
    _decide(monkeypatch, "ask")
    with db.session() as s:
        ep = _start(s)
        handle_reply(s, ep.id, "x", FINE)
        assert handle_reply(s, ep.id, "no thanks")["route"] == "checkup_declined"
        assert _out(s, ep, "answer")[-1].text == CHECKUP_DECLINED["en"]
        assert not _out(s, ep, "offer")


def test_agent_none_or_unavailable_keeps_the_normal_reply(db, monkeypatch):
    _decide(monkeypatch, "none")
    with db.session() as s:
        ep = _start(s)
        handle_reply(s, ep.id, "x", FINE)
        assert _out(s, ep, "thanks") and not _out(s, ep, "offer") and not _out(s, ep, "checkup_ask")

    def offline(*a, **k):
        raise llm.LLMUnavailable("offline")
    monkeypatch.setattr(llm, "parse", offline)
    with db.session() as s:
        ep = s.exec(select(Episode)).first()
        n = len(_out(s, ep, "thanks"))
        handle_reply(s, ep.id, "x", FINE)
        assert len(_out(s, ep, "thanks")) == n + 1


def test_episode_stays_open_after_the_last_checkin(db):
    with db.session() as s:
        ep = _start(s)
        for c in s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id)).all():
            c.status = "replied"
            s.add(c)
        s.commit()
        clock.set_time(s, datetime(2027, 6, 1))
        tick(s)
        assert s.get(Episode, ep.id).status == "active"
