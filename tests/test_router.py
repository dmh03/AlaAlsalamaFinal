import json
from datetime import datetime

from sqlmodel import select

from alasalama import clock
from alasalama.models import Episode, Message, NurseTask, LLMCall
from alasalama.router import emergency_text, handle_reply
from alasalama.schemas import CheckInForm
from alasalama.templates import load_template
from alasalama.tick import tick


def _setup(db, name):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
        from alasalama.models import Patient
        p = s.exec(select(Patient).where(Patient.name == name)).first()
        return s.exec(select(Episode).where(Episode.patient_id == p.id)).first().id


def test_red_flag_sends_fixed_text_and_urgent_task(db):
    ep_id = _setup(db, "محمد")
    with db.session() as s:
        form = CheckInForm(symptoms=["breathless_lying_down", "leg_swelling"], pain_score=None, confidence=0.95)
        r = handle_reply(s, ep_id, "ما أقدر أتنفس إذا انسدحت ورجولي منتفخة", form)
        assert r["route"] == "red_flag"
        msgs = s.exec(select(Message).where(Message.kind == "emergency")).all()
        assert msgs[0].text == emergency_text(load_template("heart_failure"), "ar")
        assert "997" in msgs[0].text
        task = s.exec(select(NurseTask)).first()
        assert task.priority == "urgent" and (task.due_by - task.created_at).seconds == 15 * 60
        assert s.exec(select(LLMCall)).all() == []


def test_concern_creates_same_day_task(db):
    ep_id = _setup(db, "عبدالله")
    with db.session() as s:
        form = CheckInForm(symptoms=["wound_redness"], pain_score=4, confidence=0.9,
                           medications=[{"name": "إبرة السيولة", "taken": False}])
        assert handle_reply(s, ep_id, "x", form)["route"] == "concern"
        t = s.exec(select(NurseTask)).first()
        assert t.priority == "same_day" and "critical medicine" in t.reason


def test_reader_failure_goes_to_nurse(db):
    ep_id = _setup(db, "Sara")
    with db.session() as s:
        assert handle_reply(s, ep_id, "I feel fine")["route"] == "nurse_fallback"
        assert len(s.exec(select(NurseTask)).all()) == 1
