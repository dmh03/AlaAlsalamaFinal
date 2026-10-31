"""Optional Telegram channel (Bot API). Turned on with ALASALAMA_CHANNEL=telegram.

Safety: same rule as the WhatsApp channels. A patient is only ever messaged if explicitly mapped to a Telegram chat id in
ALASALAMA_TELEGRAM_MAP (e.g. "Sara=123456789;Khalid=987654321"). Seed patients are never contacted. Keep the bot token
and chat ids in your local .env only; never commit them.
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from . import audit
from .channels import WebChatChannel
from .models import Episode, Message, Patient


def telegram_map() -> dict[str, str]:
    out = {}
    for part in os.environ.get("ALASALAMA_TELEGRAM_MAP", "").split(";"):
        if "=" in part:
            name, chat_id = part.split("=", 1)
            if chat_id.strip():
                out[name.strip()] = chat_id.strip()
    return out


def patient_for_chat(chat_id) -> Optional[str]:
    chat_id = str(chat_id).strip()
    for name, cid in telegram_map().items():
        if cid == chat_id:
            return name
    return None


def _offset_path() -> Path:
    return Path(os.environ.get("ALASALAMA_TELEGRAM_OFFSET", Path(__file__).resolve().parent.parent / "data" / "cache" / "telegram_offset.txt"))


def load_offset() -> Optional[int]:
    """The next update_id to ask Telegram for. Persisted so a restart never replays messages that were already handled."""
    try:
        return int(_offset_path().read_text().strip())
    except (OSError, ValueError):
        return None


def save_offset(offset: int) -> None:
    p = _offset_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(offset))


def api(method: str, http_timeout: float = 15, **params) -> dict:
    """Call one Bot API method and return its `result`. Raises RuntimeError on any API error.
    `http_timeout` is the HTTP client timeout; any `timeout=` in params is Telegram's own long-poll parameter."""
    import httpx

    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    r = httpx.post(url, json=params, timeout=http_timeout)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} {r.status_code}: {str(data.get('description', r.text))[:200]}")
    return data["result"]


def send_text(chat_id: str, text: str) -> dict:
    return api("sendMessage", chat_id=chat_id, text=text)


class TelegramChannel(WebChatChannel):
    """Stores every message in the DB (so the dashboard still shows it) and also sends mapped patients a Telegram message."""

    def send(self, s: Session, episode: Episode, patient: Patient, text: str, at: datetime,
             kind: str = "checkin", checkin_id: Optional[int] = None, sender: str = "agent") -> Message:
        m = super().send(s, episode, patient, text, at, kind, checkin_id, sender)
        chat_id = telegram_map().get(patient.name)
        if not chat_id:
            return m
        try:
            send_text(chat_id, text)
            audit.log(s, at, "agent", "telegram_sent", episode.id, kind=kind)
        except Exception as e:  # noqa: BLE001  (a delivery failure must never crash tick or the router)
            audit.log(s, at, "agent", "telegram_send_failed", episode.id, kind=kind, error=repr(e)[:300])
        return m


def handle_update(update: dict) -> Optional[dict]:
    """Process one getUpdates item. Returns the router result, or None if ignored (not text / not a mapped chat)."""
    from sqlmodel import select

    from .db import init_db, session
    from .router import handle_reply

    msg = update.get("message") or {}
    text = msg.get("text")
    chat_id = (msg.get("chat") or {}).get("id")
    if not text or chat_id is None or text.startswith("/"):
        return None
    name = patient_for_chat(chat_id)
    if name is None:
        return None
    init_db()
    with session() as s:
        p = s.exec(select(Patient).where(Patient.name == name)).first()
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id, Episode.status == "active")).first() if p else None
        if ep is None:
            return None
        return handle_reply(s, ep.id, text)
