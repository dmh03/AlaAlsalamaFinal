from datetime import datetime, timedelta

from sqlmodel import Session

from . import audit, llm
from .models import Episode, NurseTask


def make_task(s: Session, ep: Episode, t: datetime, priority: str, reason: str, sla_minutes: int,
              brief_facts: str = "", suggested_action: str = "", suggested_text: str = "") -> NurseTask:
    brief = reason
    if brief_facts:
        try:
            brief = llm.complete("nurse_brief", llm.SONNET, llm.prompt("nurse_brief"),
                                 llm.wrap_untrusted(brief_facts), t, ep.id, 300)
        except llm.LLMUnavailable:
            brief = f"{reason}\n{brief_facts}"
    task = NurseTask(episode_id=ep.id, priority=priority, reason=reason, brief=brief,
                     suggested_action=suggested_action, suggested_text=suggested_text,
                     created_at=t, due_by=t + timedelta(minutes=sla_minutes))
    s.add(task)
    s.flush()
    audit.log(s, t, "agent", "nurse_task_created", ep.id, task_id=task.id, priority=priority, reason=reason)
    return task
