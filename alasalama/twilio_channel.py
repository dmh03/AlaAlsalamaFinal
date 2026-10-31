"""Optional WhatsApp channel (Twilio Sandbox). Turned on with ALASALAMA_CHANNEL=twilio.

Safety: a patient is only ever messaged externally if they are explicitly mapped to a number in
ALASALAMA_PHONE_MAP (e.g. "Sara=+9665XXXXXXXX;Khalid=+9665YYYYYYYY"). Seed patients carry fake numbers and are never
contacted. Keep real numbers in your local .env only; never commit them.
"""
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from . import audit
from .channels import WebChatChannel
from .models import Episode, Message, Patient


def phone_map() -> dict[str, str]:
    out = {}
    for part in os.environ.get("ALASALAMA_PHONE_MAP", "").split(";"):
        if "=" in part:
            name, num = part.split("=", 1)
            out[name.strip()] = num.strip().removeprefix("whatsapp:")
    return out


def patient_for_number(number: str) -> Optional[str]:
    number = number.removeprefix("whatsapp:").strip()
    for name, n in phone_map().items():
        if n == number:
            return name
    return None


class TwilioWhatsAppChannel(WebChatChannel):
    """Stores every message in the DB (so the dashboard still shows it) and also sends mapped patients a WhatsApp."""

    _client = None

    def _twilio(self):
        if TwilioWhatsAppChannel._client is None:
            from twilio.rest import Client

            TwilioWhatsAppChannel._client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        return TwilioWhatsAppChannel._client

    def send(self, s: Session, episode: Episode, patient: Patient, text: str, at: datetime,
             kind: str = "checkin", checkin_id: Optional[int] = None, sender: str = "agent") -> Message:
        m = super().send(s, episode, patient, text, at, kind, checkin_id, sender)
        number = phone_map().get(patient.name)
        if not number:
            return m
        try:
            self._twilio().messages.create(from_=os.environ["TWILIO_WHATSAPP_FROM"], to=f"whatsapp:{number}", body=text)
            audit.log(s, at, "agent", "whatsapp_sent", episode.id, kind=kind)
        except Exception as e:  # noqa: BLE001  (a delivery failure must never crash tick or the router)
            audit.log(s, at, "agent", "whatsapp_send_failed", episode.id, kind=kind, error=repr(e)[:200])
        return m
