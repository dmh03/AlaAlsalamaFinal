from datetime import datetime

from sqlmodel import select

from alasalama import clock
from alasalama.models import CheckIn, Episode, Patient
from alasalama.tick import next_due, tick


def test_focus_limits_tick_to_named_patients(db, monkeypatch):
    monkeypatch.setenv("ALASALAMA_ONLY_PATIENTS", "Sara")
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        stats = tick(s)
        assert stats["sent"] == 1
        sara = s.exec(select(Patient).where(Patient.name == "Sara")).first()
        sara_ep = s.exec(select(Episode).where(Episode.patient_id == sara.id)).first()
        touched = {c.episode_id for c in s.exec(select(CheckIn)).all()}
        assert touched == {sara_ep.id}


def test_next_due_ignores_other_patients(db, monkeypatch):
    monkeypatch.setenv("ALASALAMA_ONLY_PATIENTS", "Sara")
    with db.session() as s:
        d = next_due(s)
        sara = s.exec(select(Patient).where(Patient.name == "Sara")).first()
        sara_ep = s.exec(select(Episode).where(Episode.patient_id == sara.id)).first()
        first = s.exec(select(CheckIn).where(CheckIn.episode_id == sara_ep.id).order_by(CheckIn.due_at)).first()
        assert d == first.due_at


def test_no_focus_means_everyone(db, monkeypatch):
    monkeypatch.delenv("ALASALAMA_ONLY_PATIENTS", raising=False)
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        assert tick(s)["sent"] == 30
