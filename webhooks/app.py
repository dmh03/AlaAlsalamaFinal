"""Inbound WhatsApp webhook. Run: make webhook   (then expose it, e.g. `ngrok http 8000`, and set the sandbox
"when a message comes in" URL to https://<tunnel>/webhooks/twilio, plus TWILIO_WEBHOOK_URL to the same URL).
"""
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request, Response
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from alasalama.db import init_db, session
from alasalama.models import Episode, Patient
from alasalama.router import handle_reply
from alasalama.twilio_channel import patient_for_number

app = FastAPI(title="Ala Salama webhooks (demo, synthetic data)")
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _handle(number: str, body: str) -> dict:
    init_db()
    name = patient_for_number(number)
    if name is None:
        return {"handled": False, "reason": "number not mapped to a demo patient"}
    with session() as s:
        p = s.exec(select(Patient).where(Patient.name == name)).first()
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id, Episode.status == "active")).first() if p else None
        if ep is None:
            return {"handled": False, "reason": "no active episode"}
        return {"handled": True, **handle_reply(s, ep.id, body)}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/webhooks/meta")
def meta_verify(request: Request):
    """Meta calls this once when you save the webhook, to check you own the URL."""
    q = request.query_params
    expected = os.environ.get("META_VERIFY_TOKEN")
    if q.get("hub.mode") == "subscribe" and expected and q.get("hub.verify_token") == expected:
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "verify token mismatch")


@app.post("/webhooks/meta")
async def meta_inbound(request: Request):
    raw = await request.body()
    secret = os.environ.get("META_APP_SECRET")
    if secret and os.environ.get("ALASALAMA_SKIP_SIGNATURE") != "1":
        import hashlib
        import hmac

        want = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(403, "invalid Meta signature")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "bad json")
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                logging.getLogger("uvicorn.error").warning("META STATUS %s id=%s errors=%s", st.get("status"),
                                                           str(st.get("id", ""))[-12:], st.get("errors"))
            for msg in change.get("value", {}).get("messages", []):
                if msg.get("type") == "text":
                    await run_in_threadpool(_handle, "+" + msg.get("from", ""), msg["text"].get("body", ""))
    return {"ok": True}


@app.post("/webhooks/twilio")
async def twilio_inbound(request: Request):
    form = dict(await request.form())
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if token and os.environ.get("ALASALAMA_SKIP_SIGNATURE") != "1":
        from twilio.request_validator import RequestValidator

        url = os.environ.get("TWILIO_WEBHOOK_URL", str(request.url))
        if not RequestValidator(token).validate(url, form, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(403, "invalid Twilio signature")
    await run_in_threadpool(_handle, form.get("From", ""), form.get("Body", ""))
    return Response(EMPTY_TWIML, media_type="application/xml")
