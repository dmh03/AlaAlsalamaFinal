"""Optional WhatsApp channel via Meta's WhatsApp Cloud API. Turned on with ALASALAMA_CHANNEL=meta.

Same safety rule as the Twilio channel: only patients listed in ALASALAMA_PHONE_MAP are ever messaged externally.
Free-form text can only be sent inside the 24-hour window that opens when the person messages the business number.
"""
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from . import audit
from .channels import WebChatChannel
from .models import Episode, Message, Patient
from .twilio_channel import phone_map

GRAPH_VERSION = "v21.0"


def send_text(number: str, text: str) -> dict:
    """POST one text message. `number` is international format; Meta wants digits only (no +)."""
    import httpx

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{os.environ['META_WA_PHONE_ID']}/messages"
    payload = {"messaging_product": "whatsapp", "to": number.lstrip("+"), "type": "text",
               "text": {"body": text, "preview_url": False}}
    r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {os.environ['META_WA_TOKEN']}"}, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Meta API {r.status_code}: {r.text[:300]}")
    return r.json()


class MetaWhatsAppChannel(WebChatChannel):
    """Stores every message in the DB and also sends mapped patients a WhatsApp through the Cloud API."""

    def send(self, s: Session, episode: Episode, patient: Patient, text: str, at: datetime,
             kind: str = "checkin", checkin_id: Optional[int] = None, sender: str = "agent") -> Message:
        m = super().send(s, episode, patient, text, at, kind, checkin_id, sender)
        number = phone_map().get(patient.name)
        if not number:
            return m
        try:
            send_text(number, text)
            audit.log(s, at, "agent", "whatsapp_sent", episode.id, kind=kind, provider="meta")
        except Exception as e:  # noqa: BLE001  (a delivery failure must never crash tick or the router)
            audit.log(s, at, "agent", "whatsapp_send_failed", episode.id, kind=kind, provider="meta", error=repr(e)[:300])
        return m
