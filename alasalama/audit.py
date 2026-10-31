import json
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from .models import AuditEvent


def log(s: Session, at: datetime, actor: str, event: str, episode_id: Optional[int] = None, **detail) -> None:
    s.add(AuditEvent(at=at, episode_id=episode_id, actor=actor, event=event,
                     detail_json=json.dumps(detail, ensure_ascii=False, default=str)))
