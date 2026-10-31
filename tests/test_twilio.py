import os
from datetime import datetime

import pytest
from sqlmodel import select

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from alasalama import clock  # noqa: E402
from alasalama.models import Decision, Episode, Message, NurseTask, Patient  # noqa: E402
from alasalama.tick import tick  # noqa: E402


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("ALASALAMA_PHONE_MAP", "محمد=+966500000999")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ALASALAMA_CHANNEL", "twilio")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    from webhooks.app import app

    return TestClient(app)


def test_unmapped_number_is_ignored(db, client):
    r = client.post("/webhooks/twilio", data={"From": "whatsapp:+966511111111", "Body": "hello"})
    assert r.status_code == 200
    with db.session() as s:
        assert s.exec(select(Message).where(Message.direction == "in")).all() == []


def test_mapped_reply_runs_the_agent_and_sends_fixed_emergency(db, client, monkeypatch):
    sent = []
    from alasalama.twilio_channel import TwilioWhatsAppChannel

    class FakeMsgs:
        def create(self, **kw):
            sent.append(kw)

    class FakeClient:
        messages = FakeMsgs()

    monkeypatch.setattr(TwilioWhatsAppChannel, "_twilio", lambda self: FakeClient())
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
    r = client.post("/webhooks/twilio", data={"From": "whatsapp:+966500000999", "Body": "ما أقدر أتنفس"})
    assert r.status_code == 200 and "<Response>" in r.text
    with db.session() as s:
        p = s.exec(select(Patient).where(Patient.name == "محمد")).first()
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id)).first()
        assert [m.text for m in s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "in")).all()] == ["ما أقدر أتنفس"]
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
    assert all(k["to"] == "whatsapp:+966500000999" for k in sent)


def test_unmapped_patients_are_never_sent_externally(db, monkeypatch):
    from alasalama.channels import get_channel
    from alasalama.twilio_channel import TwilioWhatsAppChannel

    monkeypatch.setenv("ALASALAMA_CHANNEL", "twilio")
    monkeypatch.setenv("ALASALAMA_PHONE_MAP", "")
    monkeypatch.setattr(TwilioWhatsAppChannel, "_twilio", lambda self: (_ for _ in ()).throw(AssertionError("must not send")))
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        assert tick(s)["sent"] == 30
    assert isinstance(get_channel(), TwilioWhatsAppChannel)


def test_bad_signature_rejected(db, monkeypatch):
    pytest.importorskip("twilio")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.delenv("ALASALAMA_SKIP_SIGNATURE", raising=False)
    from webhooks.app import app

    r = TestClient(app).post("/webhooks/twilio", data={"From": "whatsapp:+966500000999", "Body": "x"},
                             headers={"X-Twilio-Signature": "bad"})
    assert r.status_code == 403
