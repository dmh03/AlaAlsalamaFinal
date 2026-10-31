from datetime import datetime
import os
from typing import Optional, Protocol

from sqlmodel import Session

from .models import Episode, Message, Patient


class Channel(Protocol):
    def send(self, s: Session, episode: Episode, patient: Patient, text: str, at: datetime,
             kind: str = "checkin", checkin_id: Optional[int] = None, sender: str = "agent") -> Message: ...


class WebChatChannel:
    """Stores messages in the DB; the dashboard renders them as a chat."""

    def send(self, s, episode, patient, text, at, kind="checkin", checkin_id=None, sender="agent"):
        m = Message(episode_id=episode.id, checkin_id=checkin_id, direction="out", sender=sender,
                    text=text, kind=kind, created_at=at)
        s.add(m)
        s.flush()
        return m

    def receive(self, s, episode, text, at, checkin_id=None) -> Message:
        m = Message(episode_id=episode.id, checkin_id=checkin_id, direction="in", sender="patient",
                    text=text, kind="reply", created_at=at)
        s.add(m)
        s.flush()
        return m


def get_channel() -> WebChatChannel:
    which = os.environ.get("ALASALAMA_CHANNEL", "webchat").lower()
    if which == "telegram":
        from .telegram_channel import TelegramChannel

        return TelegramChannel()
    return WebChatChannel()
