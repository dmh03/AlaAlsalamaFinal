import pytest
from sqlmodel import select

from alasalama import doctors
from alasalama.models import Appointment, Doctor, Episode, Message, NurseTask, Patient
from alasalama.router import handle_reply
from alasalama.schemas import CheckInForm
from test_doctors import CONCERN, _concern, _ep, _offered, _out


def _q(text):
    return CheckInForm(questions=[text], confidence=0.95)


def _ask(s, ep, text):
    """The patient's real text is what the router parses; the form stands in for the reader's output."""
    return handle_reply(s, ep.id, text, _q(text))


def _docs(s):
    return s.exec(select(Doctor)).all()


@pytest.mark.parametrize("text,near", [
    ("anything around 6pm?", 18 * 60), ("6 PM please", 18 * 60), ("at 6:30pm", 18 * 60 + 30), ("12am", 0), ("12pm", 12 * 60),
    ("18:30", 18 * 60 + 30), ("in the morning", 9 * 60), ("this evening", 18 * 60), ("afternoon", 14 * 60),
    ("٦ مساء", 18 * 60), ("الساعة ٩ صباحا", 9 * 60), ("العصر", 14 * 60), ("no time given", None)])
def test_time_of_day_is_understood(db, text, near):
    with db.session() as s:
        assert doctors.parse_pref(text, _docs(s)).near == near


def test_doctor_is_found_by_first_name_in_english_and_arabic(db):
    with db.session() as s:
        by_name = {d.name_en: d.id for d in _docs(s)}
        assert doctors.parse_pref("slots for Dr.Layla?", _docs(s)).doctor_ids == {by_name["Dr. Layla Al-Harbi"]}
        assert doctors.parse_pref("ابغى موعد مع د. ليلى", _docs(s)).doctor_ids == {by_name["Dr. Layla Al-Harbi"]}
        assert doctors.parse_pref("what time works", _docs(s)).doctor_ids == set()


def test_asking_for_a_doctor_at_a_time_gets_that_doctors_slots_at_that_time(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        old = [a.id for a in _offered(s, ep)]
        r = _ask(s, ep, "Are there any other available slots for Dr.Layla at around 6pm ?")
        assert r["route"] == "question"
        msg = _out(s, ep)[-1]
        assert msg.kind == "offer" and "closest to what you asked for" in msg.text
        assert "closest options" not in msg.text
        new = _offered(s, ep)
        assert len(new) == 3 and not set(old) & {a.id for a in new}
        assert {s.get(Doctor, a.doctor_id).name_en for a in new} == {"Dr. Layla Al-Harbi"}
        assert {f"{a.starts_at:%H:%M}" for a in new} == {"18:00"}
        assert all(s.get(Appointment, i).status == "expired" for i in old)
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
        assert not [m for m in _out(s, ep) if m.kind == "answer"]


def test_a_time_outside_working_hours_is_not_pretended_available(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        _ask(s, ep, "any slots for Dr.Layla around 11pm?")
        msg = _out(s, ep)[-1]
        assert msg.kind == "offer" and "closest options" in msg.text
        assert {f"{a.starts_at:%H:%M}" for a in _offered(s, ep)} == {"19:30"}


def test_the_new_options_can_be_booked(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        _ask(s, ep, "any slots for Dr.Layla around 6pm?")
        assert handle_reply(s, ep.id, "2")["route"] == "appointment_booked"
        booked = s.exec(select(Appointment).where(Appointment.status == "booked")).one()
        assert s.get(Doctor, booked.doctor_id).name_en == "Dr. Layla Al-Harbi"


def test_other_options_never_repeat_what_was_already_shown(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        first = {(a.doctor_id, a.starts_at) for a in _offered(s, ep)}
        assert _ask(s, ep, "do you have other appointments?")["route"] == "question"
        assert _out(s, ep)[-1].kind == "offer" and "other available options" in _out(s, ep)[-1].text
        assert not first & {(a.doctor_id, a.starts_at) for a in _offered(s, ep)}


def test_time_close_to_the_request_says_so_without_the_apology(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        _ask(s, ep, "anything around 2pm?")
        assert "closest to what you asked for" in _out(s, ep)[-1].text


def test_arabic_request_gets_arabic_options(db):
    with db.session() as s:
        ep = _concern(s, "نورة")
        _ask(s, ep, "فيه موعد الساعة ٤ العصر؟")
        msg = _out(s, ep)[-1]
        assert msg.kind == "offer" and "د. " in msg.text and "Dr." not in msg.text


def test_a_medicine_question_while_an_offer_is_pending_is_not_a_scheduling_request(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        _ask(s, ep, "Are there any suggested medicines I should take?")
        assert _out(s, ep)[-1].kind == "answer"
        assert len(_offered(s, ep)) == 3


def test_no_offer_pending_means_no_scheduling_handling(db):
    with db.session() as s:
        ep = _ep(s, "Sara")
        _ask(s, ep, "any slots for Dr.Layla around 6pm?")
        assert not [m for m in _out(s, ep) if m.kind == "offer"]


def test_a_symptom_message_is_never_treated_as_a_scheduling_request(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        form = CheckInForm(symptoms=["heavy_bleeding"], questions=["can I see Dr.Layla at 6pm?"], confidence=0.95)
        assert handle_reply(s, ep.id, "I'm bleeding a lot, can I see Dr.Layla at 6pm?", form)["route"] == "red_flag"
        assert [m.kind for m in _out(s, ep)[-2:]] == ["emergency", "followup"]


def test_low_confidence_reschedule_request_is_not_unclear(db):
    """Live bug: the reader was unsure (0.4) of 'other times around 4pm?' because it answers no check-in question."""
    with db.session() as s:
        ep = _concern(s, "Sara")
        before = len(_offered(s, ep))
        form = CheckInForm(questions=["هل يوجد اوقات ثانيه حول الساعه ٤ العصر؟"], confidence=0.4)
        res = handle_reply(s, ep.id, "هل يوجد اوقات ثانيه حول الساعه ٤ العصر؟", form)
        assert res["route"] != "unclear"
        assert _out(s, ep)[-1].kind == "offer" and before == 3
        assert any(a.starts_at.hour == 16 for a in _offered(s, ep))


def test_low_confidence_without_an_offer_is_still_unclear(db):
    with db.session() as s:
        ep = _ep(s, "Sara")
        res = handle_reply(s, ep.id, "any slots around 6pm?", CheckInForm(questions=["any slots around 6pm?"], confidence=0.4))
        assert res["route"] == "unclear"


def test_nothing_available_falls_back_to_the_question_path(db, monkeypatch):
    with db.session() as s:
        ep = _concern(s, "Sara")
        monkeypatch.setattr(doctors, "free_slots", lambda *a, **k: [])
        _ask(s, ep, "any slots for Dr.Layla around 6pm?")
        assert _out(s, ep)[-1].kind == "answer"
