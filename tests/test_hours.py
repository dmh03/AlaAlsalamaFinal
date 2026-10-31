import json
from datetime import datetime

from sqlmodel import select

from alasalama import clock, doctors
from alasalama.models import Appointment, Doctor
from test_doctors import _concern


def test_working_hours_become_half_hour_slots(db):
    with db.session() as s:
        doctors.ensure_doctors(s)
        d = s.exec(select(Doctor)).first()
        slots = doctors.slot_times(d)
        assert slots[0] == "09:00" and slots[-1] == "19:30" and len(slots) == 22
        assert "20:00" not in slots


def test_every_slot_is_open_until_it_is_booked(db):
    with db.session() as s:
        ep = _concern(s, "Sara")
        now = clock.now(s)
        doc = s.exec(select(Doctor).where(Doctor.specialty == "obgyn")).first()
        open_before = doctors.free_slots(s, "obgyn", now, n=500, doctor_ids={doc.id})
        assert len(open_before) > 40
        first = open_before[0]
        s.add(Appointment(episode_id=ep.id, doctor_id=doc.id, starts_at=first[0], status="booked", created_at=now))
        s.commit()
        open_after = doctors.free_slots(s, "obgyn", now, n=500, doctor_ids={doc.id})
        assert len(open_after) == len(open_before) - 1
        assert (first[0], doc.id) not in {(w, d.id) for w, d in open_after}


def test_a_legacy_fixed_time_roster_is_moved_to_working_hours(db):
    with db.session() as s:
        doctors.ensure_doctors(s)
        d = s.exec(select(Doctor)).first()
        d.times_json = json.dumps(["09:00", "10:30"])
        s.add(d)
        s.commit()
        assert doctors.slot_times(d) == ["09:00", "10:30"]
        doctors.ensure_doctors(s)
        s.commit()
        s.refresh(d)
        assert len(doctors.slot_times(d)) == 22


def test_nothing_is_offered_outside_working_hours(db):
    with db.session() as s:
        _concern(s, "Sara")
        now = clock.now(s)
        for st, d in doctors.free_slots(s, "obgyn", now, n=500):
            assert datetime(st.year, st.month, st.day, 9, 0) <= st < datetime(st.year, st.month, st.day, 20, 0)
