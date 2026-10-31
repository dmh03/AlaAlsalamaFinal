from datetime import datetime

import pytest
from sqlmodel import select

from alasalama import clock
from alasalama.channels import get_channel
from alasalama.models import Episode, Message, NurseTask, Patient
from alasalama.telegram_channel import TelegramChannel, handle_update, patient_for_chat, telegram_map
from alasalama.tick import tick


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ALASALAMA_CHANNEL", "telegram")
    monkeypatch.setenv("ALASALAMA_TELEGRAM_MAP", "محمد=555000111")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")


def _update(chat_id, text, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}


def test_map_parsing_and_lookup(monkeypatch):
    monkeypatch.setenv("ALASALAMA_TELEGRAM_MAP", "Sara=1; Khalid=2 ;Empty=")
    assert telegram_map() == {"Sara": "1", "Khalid": "2"}
    assert patient_for_chat(2) == "Khalid" and patient_for_chat(99) is None


def test_channel_switch():
    assert isinstance(get_channel(), TelegramChannel)


def test_only_mapped_patient_is_sent(db, monkeypatch):
    sent = []
    monkeypatch.setattr("alasalama.telegram_channel.send_text", lambda chat_id, text: sent.append(chat_id) or {})
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        assert tick(s)["sent"] == 30
    assert sent and set(sent) == {"555000111"}


def test_unmapped_chat_and_commands_are_ignored(db):
    assert handle_update(_update(999, "hello")) is None
    assert handle_update(_update(555000111, "/start")) is None
    assert handle_update({"update_id": 3, "message": {"chat": {"id": 555000111}}}) is None


def test_mapped_reply_reaches_a_nurse(db, monkeypatch):
    monkeypatch.setattr("alasalama.telegram_channel.send_text", lambda chat_id, text: {})
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
    res = handle_update(_update(555000111, "ما أقدر أتنفس"))
    assert res is not None
    with db.session() as s:
        p = s.exec(select(Patient).where(Patient.name == "محمد")).first()
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id)).first()
        assert [m.text for m in s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "in")).all()] == ["ما أقدر أتنفس"]
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1


def test_offset_is_saved_and_reloaded(tmp_path, monkeypatch):
    from alasalama.telegram_channel import load_offset, save_offset

    monkeypatch.setenv("ALASALAMA_TELEGRAM_OFFSET", str(tmp_path / "sub" / "offset.txt"))
    assert load_offset() is None
    save_offset(4242)
    assert load_offset() == 4242
    (tmp_path / "sub" / "offset.txt").write_text("garbage")
    assert load_offset() is None


def test_send_failure_never_crashes_tick(db, monkeypatch):
    monkeypatch.setattr("alasalama.telegram_channel.send_text", lambda c, t: (_ for _ in ()).throw(RuntimeError("boom")))
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        assert tick(s)["sent"] == 30
