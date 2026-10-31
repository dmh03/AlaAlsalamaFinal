from datetime import datetime

import pytest
from sqlmodel import select

from alasalama import clock
from alasalama.guard import deterministic_check
from alasalama.models import CheckIn, Episode, Message, NurseTask, Patient
from alasalama.router import handle_reply
from alasalama.schemas import CheckInForm
from alasalama.templates import load_template
from alasalama.tick import tick


@pytest.mark.parametrize("text", [
    "Take 500 mg twice a day", "خذ 5 مل الصباح", "Inject 40 units", "Stop taking your medication today",
    "لا تاخذ الدواء اليوم", "وقف الإبرة", "x" * 700, "Take 2 tablets",
])
def test_guard_blocks(text):
    assert not deterministic_check(text).allowed


@pytest.mark.parametrize("text", [
    "How is your pain from 0 to 10?", "كيف حالك اليوم؟", "Your next check-in is on 2026-11-04 at 11:00.",
    "Contact us if pain increases. For emergencies call 997.",
])
def test_guard_allows(text):
    assert deterministic_check(text).allowed


def test_approved_templates_pass_guard():
    for n in ["knee_replacement", "c_section", "heart_failure"]:
        t = load_template(n)
        for a in t["qa"]:
            assert deterministic_check(a["a_ar"]).allowed, (n, a["id"], "ar")
            assert deterministic_check(a["a_en"]).allowed, (n, a["id"], "en")
        for lang in ("ar", "en"):
            for q in t["questions"][lang]:
                assert deterministic_check(q).allowed


def _ep(s, name):
    p = s.exec(select(Patient).where(Patient.name == name)).first()
    return s.exec(select(Episode).where(Episode.patient_id == p.id)).first()


def test_chase_sequence_and_unreachable(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 10, 0))
        tick(s)
        ep = _ep(s, "فهد")
        get_ci = lambda: s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id, CheckIn.day == 1)).first()
        kinds = lambda: [m.kind for m in s.exec(select(Message).where(Message.episode_id == ep.id)).all()]
        assert get_ci().status == "sent" and get_ci().attempts == 1
        clock.set_time(s, datetime(2026, 11, 2, 16, 0)); tick(s)
        assert kinds().count("reminder") == 1
        clock.set_time(s, datetime(2026, 11, 2, 16, 30)); tick(s); tick(s)
        assert kinds().count("reminder") == 1
        clock.set_time(s, datetime(2026, 11, 3, 23, 0)); tick(s)
        assert kinds().count("reminder") == 2
        clock.set_time(s, datetime(2026, 11, 5, 8, 0)); tick(s)
        assert get_ci().status == "unreachable"
        tasks = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()
        assert len([t for t in tasks if t.reason.startswith("Unreachable")]) == 1
        tick(s)
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == len(tasks)


def test_clarify_then_nurse_task(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0)); tick(s)
        ep = _ep(s, "Khalid")
        unclear = CheckInForm(confidence=0.4, pain_score=None, unclear_reason="vague")
        assert handle_reply(s, ep.id, "ok I guess", unclear)["route"] == "unclear"
        assert [m.kind for m in s.exec(select(Message).where(Message.episode_id == ep.id)).all()].count("clarify") == 1
        assert s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all() == []
        handle_reply(s, ep.id, "idk", unclear)
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
