from datetime import datetime

import pytest

from alasalama import clock
from alasalama.models import Patient
from alasalama.router import _is_arabic_script, _wants_language, handle_reply
from alasalama.tick import tick
from test_doctors import _ep, _out


@pytest.mark.parametrize("text,want", [
    ("ممكن بالعربي؟", "ar"), ("can we do this in Arabic?", "ar"), ("english please", "en"), ("بالانجليزي لو سمحت", "en"),
    ("pain 3", None), ("I speak arabic at home and my wound hurts a lot, please tell the nurse today", None)])
def test_language_request(text, want):
    assert _wants_language(text) == want


def test_arabic_script_detection():
    assert _is_arabic_script("الألم ٣") and not _is_arabic_script("pain 3") and not _is_arabic_script("ok")


def test_request_switches_language_and_reasks_open_questions(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
        ep = _ep(s, "Sara")
        p = s.get(Patient, ep.patient_id)
        p.language = "en"
        s.add(p)
        s.commit()
        res = handle_reply(s, ep.id, "ممكن بالعربي؟")
        assert res["route"] == "language_switched"
        s.refresh(p)
        assert p.language == "ar"
        last = _out(s, ep)[-1].text
        assert "بالعربي" in last and "1." in last


@pytest.mark.parametrize("text,english", [
    ("I feel great and the pain is 1", True), ("thanks", True), ("hello", True), ("ok", False), ("Ibuprofen", False),
    ("أخذت Ibuprofen اليوم", False), ("٣", False), ("2", False)])
def test_english_detection(text, english):
    from alasalama.router import _is_english
    assert _is_english(text) is english


def test_arabic_patient_writing_english_gets_english_back(db):
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
        ep = _ep(s, "Sara")
        p = s.get(Patient, ep.patient_id)
        p.language = "ar"
        s.add(p)
        s.commit()
        handle_reply(s, ep.id, "I feel fine, pain is 2 and the wound is clean")
        s.refresh(p)
        assert p.language == "en"
        assert not any("\u0600" <= c <= "\u06ff" for c in _out(s, ep)[-1].text)
        handle_reply(s, ep.id, "ممكن أسأل سؤال؟")
        s.refresh(p)
        assert p.language == "ar"
