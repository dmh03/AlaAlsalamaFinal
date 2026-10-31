"""The one place outgoing agent/nurse messages leave the system (except the fixed emergency text)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from . import audit, guard
from .channels import get_channel
from .models import Episode, Message, Patient
from .tasks import make_task


def send_guarded(s: Session, ep: Episode, p: Patient, text: str, t: datetime, kind: str,
                 checkin_id: Optional[int] = None, sender: str = "agent", generated: bool = True,
                 task_on_block: bool = True) -> Optional[Message]:
    """generated=False means fixed, pre-approved template text: deterministic checks only (no Haiku call).
    Returns None if blocked."""
    res = guard.check(text, t, ep.id, use_llm=generated)
    if res.allowed:
        return get_channel().send(s, ep, p, text, t, kind, checkin_id, sender)
    audit.log(s, t, "agent", "message_blocked", ep.id, reasons=res.reasons, kind=kind, text=text)
    if task_on_block:
        make_task(s, ep, t, "same_day", f"Outgoing {kind} message blocked by guard: {'; '.join(res.reasons)}",
                  240, "", "send_manually")
    return None
