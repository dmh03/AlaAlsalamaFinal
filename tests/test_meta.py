import hashlib
import hmac
import json
from datetime import datetime

import pytest
from sqlmodel import select

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from alasalama import clock  # noqa: E402
from alasalama.models import Episode, Message, NurseTask, Patient  # noqa: E402
from alasalama.tick import tick  # noqa: E402


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("ALASALAMA_PHONE_MAP", "محمد=+966500000999")
    monkeypatch.setenv("ALASALAMA_CHANNEL", "meta")
    monkeypatch.setenv("META_VERIFY_TOKEN", "vtoken")
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    from webhooks.app import app

    return TestClient(app)


def _payload(number: str, body: str) -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [{"from": number, "type": "text", "text": {"body": body}}]}}]}]}


def test_verification_handshake(client):
    ok = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "vtoken", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_unmapped_number_is_ignored(db, client):
    r = client.post("/webhooks/meta", json=_payload("966511111111", "hello"))
    assert r.status_code == 200
    with db.session() as s:
        assert s.exec(select(Message).where(Message.direction == "in")).all() == []


def test_mapped_reply_reaches_a_nurse_and_only_mapped_patient_is_sent(db, client, monkeypatch):
    sent = []
    monkeypatch.setattr("alasalama.meta_channel.send_text", lambda number, text: sent.append(number) or {})
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        tick(s)
    r = client.post("/webhooks/meta", json=_payload("966500000999", "ما أقدر أتنفس"))
    assert r.status_code == 200
    with db.session() as s:
        p = s.exec(select(Patient).where(Patient.name == "محمد")).first()
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id)).first()
        assert [m.text for m in s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "in")).all()] == ["ما أقدر أتنفس"]
        assert len(s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()) == 1
    assert sent and set(sent) == {"+966500000999"}


def test_signature_checked(db, monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "appsecret")
    monkeypatch.delenv("ALASALAMA_SKIP_SIGNATURE", raising=False)
    monkeypatch.setenv("ALASALAMA_PHONE_MAP", "")
    from webhooks.app import app

    c = TestClient(app)
    raw = json.dumps(_payload("966500000999", "x")).encode()
    assert c.post("/webhooks/meta", content=raw, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    good = "sha256=" + hmac.new(b"appsecret", raw, hashlib.sha256).hexdigest()
    assert c.post("/webhooks/meta", content=raw, headers={"X-Hub-Signature-256": good}).status_code == 200


def test_send_failure_never_crashes(db, monkeypatch):
    from alasalama.channels import get_channel
    from alasalama.meta_channel import MetaWhatsAppChannel

    monkeypatch.setenv("ALASALAMA_CHANNEL", "meta")
    monkeypatch.setenv("ALASALAMA_PHONE_MAP", "محمد=+966500000999")
    monkeypatch.setattr("alasalama.meta_channel.send_text", lambda n, t: (_ for _ in ()).throw(RuntimeError("boom")))
    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        assert tick(s)["sent"] == 30
    assert isinstance(get_channel(), MetaWhatsAppChannel)
