import json
from datetime import datetime

import pytest
import yaml
from sqlmodel import select

from alasalama import clock, doctors, llm
from alasalama.db import ROOT
from alasalama.models import Appointment, AuditEvent, Doctor, Episode, Message, NurseTask, Patient
from alasalama.router import handle_reply
from alasalama.schemas import CheckInForm
from alasalama.tick import tick

CONCERN = CheckInForm(pain_score=4, symptoms=["wound_redness"], confidence=0.9)


def _ep(s, name):
    p = s.exec(select(Patient).where(Patient.name == name)).first()
    return s.exec(select(Episode).where(Episode.patient_id == p.id)).first()


def _concern(s, name):
    """Send the day-1 check-in, then answer it with a concern. Returns the episode."""
    clock.set_time(s, datetime(2026, 11, 2, 23, 0))
    tick(s)
    ep = _ep(s, name)
    assert handle_reply(s, ep.id, "x", CONCERN)["route"] == "concern"
    assert _out(s, ep, "followup") and not _offered(s, ep)
    assert handle_reply(s, ep.id, "just the redness", CONCERN)["route"] == "concern"
    return ep


def _out(s, ep, kind=None):
    q = select(Message).where(Message.episode_id == ep.id, Message.direction == "out").order_by(Message.id)
    return [m for m in s.exec(q).all() if kind is None or m.kind == kind]


def _offered(s, ep):
    return s.exec(select(Appointment).where(Appointment.episode_id == ep.id, Appointment.status == "offered")
                  .order_by(Appointment.option_no)).all()


def _fake_opening(monkeypatch, text):
    """Replace only the doctor_offer model call; every other purpose behaves as in offline mode."""
    real = llm.complete

    def fake(purpose, *a, **k):
        return text if purpose == "doctor_offer" else real(purpose, *a, **k)

    monkeypatch.setattr(llm, "complete", fake)


def test_roster_covers_every_template_specialty(db):
    with db.session() as s:
        have = {d.specialty for d in s.exec(select(Doctor)).all()}
    for f in (ROOT / "config" / "templates").glob("*.yaml"):
        assert yaml.safe_load(f.read_text(encoding="utf-8"))["doctor_specialty"] in have
    assert "general" in have


def test_concern_offers_doctor_options_instead_of_nurse_promise(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        msg = _out(s, ep, "offer")[-1]
        assert "nurse will contact you today" not in msg.text
        assert [line[:2] for line in msg.text.splitlines() if line[:2] in ("1.", "2.", "3.")] == ["1.", "2.", "3."]
        assert "Dr. " in msg.text and len(msg.text) <= 600
        assert len(_offered(s, ep)) == 3
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1


def test_options_are_real_future_slots_for_the_right_specialty(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        now = clock.now(s)
        for a in _offered(s, ep):
            d = s.get(Doctor, a.doctor_id)
            assert d.specialty == "obgyn"
            assert a.starts_at >= now + doctors.LEAD_TIME
            assert a.starts_at.weekday() in json.loads(d.days_json)
            assert f"{a.starts_at:%H:%M}" in doctors.slot_times(d)
        assert len({a.doctor_id for a in _offered(s, ep)}) >= 2


def test_picking_an_option_books_it_and_notes_the_nurse_task(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        chosen = _offered(s, ep)[1]
        r = handle_reply(s, ep.id, "2")
        assert r["route"] == "appointment_booked"
        assert s.get(Appointment, chosen.id).status == "booked"
        others = s.exec(select(Appointment).where(Appointment.episode_id == ep.id, Appointment.id != chosen.id)).all()
        assert all(a.status == "expired" for a in others)
        assert "booked" in _out(s, ep)[-1].text and "997" in _out(s, ep)[-1].text and _out(s, ep)[-1].kind == "booking"
        assert "Patient booked" in s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).first().brief
        assert "appointment_booked" in [a.event for a in s.exec(select(AuditEvent)).all()]


@pytest.mark.parametrize("reply", ["1", "option 1", "Option 1.", "the first one", "#1"])
def test_pick_phrasings(db, reply):
    with db.session() as s:
        ep = _concern(s, "Sara")
        assert handle_reply(s, ep.id, reply)["route"] == "appointment_booked"


def test_arabic_patient_gets_arabic_and_arabic_indic_digits_work(db):
    with db.session() as s:
        ep = _concern(s, "نورة")
        msg = _out(s, ep, "offer")[-1]
        assert "د. " in msg.text and "Dr." not in msg.text
        assert handle_reply(s, ep.id, "٢")["route"] == "appointment_booked"
        assert "تم حجز موعدك" in _out(s, ep)[-1].text
        assert handle_reply(s, ep.id, "شكراً")["route"] == "acknowledged"


def test_out_of_range_or_wordy_replies_are_not_picks(db):
    with db.session() as s:
        assert doctors.parse_pick("4", 3) is None and doctors.parse_pick("0", 3) is None
        assert doctors.parse_pick("1 and 2", 3) is None and doctors.parse_pick("I take 2 tablets", 3) is None
        assert doctors.parse_pick("pain is 3", 3) is None


def test_declining_keeps_the_nurse_task_and_books_nothing(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        assert handle_reply(s, ep.id, "no thanks")["route"] == "appointment_declined"
        assert s.exec(select(Appointment).where(Appointment.status == "booked")).all() == []
        assert _offered(s, ep) == []
        assert len(s.exec(select(NurseTask).where(NurseTask.status == "open")).all()) == 1
        assert handle_reply(s, ep.id, "1")["route"] != "appointment_booked"


def test_thanks_while_the_offer_is_pending_does_not_cancel_it(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        assert handle_reply(s, ep.id, "thank you")["route"] == "acknowledged"
        assert handle_reply(s, ep.id, "2")["route"] == "appointment_booked"


def test_symptom_message_after_an_offer_is_never_swallowed(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        r = handle_reply(s, ep.id, "I have chest pain", CheckInForm(symptoms=["chest_pain"], confidence=0.95))
        assert r["route"] == "red_flag"
        assert doctors.pending_offer(s, ep) == []
        assert handle_reply(s, ep.id, "1")["route"] != "appointment_booked"


def test_a_new_checkin_ends_the_offer_so_a_bare_number_is_a_pain_score(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        s.add(Message(episode_id=ep.id, direction="out", text="day 3 check-in", kind="checkin", created_at=clock.now(s)))
        s.commit()
        assert doctors.pending_offer(s, ep) == []


def test_slot_taken_by_someone_else_reoffers_without_it(db):
    with db.session() as s:
        a_ep = _concern(s, "Sara")
        b_ep = _ep(s, "نورة")
        assert handle_reply(s, b_ep.id, "x", CONCERN)["route"] == "concern"
        assert handle_reply(s, b_ep.id, "just the redness", CONCERN)["route"] == "concern"
        first = _offered(s, a_ep)[0]
        b_first = _offered(s, b_ep)[0]
        assert (first.doctor_id, first.starts_at) == (b_first.doctor_id, b_first.starts_at)
        assert handle_reply(s, b_ep.id, "1")["route"] == "appointment_booked"
        assert handle_reply(s, a_ep.id, "1")["route"] == "appointment_reoffered"
        assert "just taken" in _out(s, a_ep, "offer")[-1].text
        assert all((a.doctor_id, a.starts_at) != (first.doctor_id, first.starts_at) for a in _offered(s, a_ep))
        assert s.exec(select(Appointment).where(Appointment.episode_id == a_ep.id, Appointment.status == "booked")).all() == []


def test_free_slots_respects_lead_time_and_working_days(db):
    with db.session() as s:
        thu_evening = datetime(2026, 11, 5, 20, 0)
        for st, d in doctors.free_slots(s, "obgyn", thu_evening):
            assert st >= datetime(2026, 11, 5, 23, 0) and st.weekday() not in (4, 5)
        assert doctors.free_slots(s, "unknown-specialty", thu_evening)


def test_llm_opening_is_used_when_it_passes_validation(db, monkeypatch):
    _fake_opening(monkeypatch, "Hi Sara, thanks for telling us. A doctor can take a look.")
    with db.session() as s:
        ep = _concern(s, "Sara")
        assert _out(s, ep, "offer")[-1].text.startswith("Hi Sara, thanks for telling us.")


@pytest.mark.parametrize("bad", ["", "Hi Sara " + "very " * 80 + "sorry"])
def test_invalid_llm_opening_falls_back_to_the_fixed_one(db, monkeypatch, bad):
    _fake_opening(monkeypatch, bad)
    with db.session() as s:
        ep = _concern(s, "Sara")
        assert _out(s, ep, "offer")[-1].text.startswith(doctors.LEAD["en"])


def test_guard_blocking_the_opening_falls_back_without_an_extra_nurse_task(db, monkeypatch):
    _fake_opening(monkeypatch, "Hi Sara, you should stop taking your medicine for now.")
    with db.session() as s:
        ep = _concern(s, "Sara")
        msg = _out(s, ep, "offer")[-1]
        assert msg.text.startswith(doctors.LEAD["en"]) and "stop taking" not in msg.text
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1


def test_no_doctors_falls_back_to_the_plain_thanks(db, monkeypatch):
    monkeypatch.setattr(doctors, "load_doctors", lambda _s: 0)
    with db.session() as s:
        for d in s.exec(select(Doctor)).all():
            s.delete(d)
        s.commit()
        ep = _concern(s, "Sara")
        assert _out(s, ep)[-1].kind == "thanks" and "nurse" in _out(s, ep)[-1].text.lower()


def test_emergency_path_is_untouched_and_uses_no_roster(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
        ep = _ep(s, "Sara")
        assert handle_reply(s, ep.id, "x", CheckInForm(symptoms=["heavy_bleeding"], confidence=0.95))["route"] == "red_flag"
        assert _out(s, ep, "offer") == [] and s.exec(select(Appointment)).all() == []
