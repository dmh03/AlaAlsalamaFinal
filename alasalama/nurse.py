"""Nurse actions: approve / edit-and-send / resolve / close. The agent performs the follow-through."""
from typing import Optional

from sqlmodel import Session

from . import audit
from .channels import get_channel
from .clock import now
from .models import CheckIn, Episode, NurseTask, Patient
from sqlmodel import select


def _send_nurse_text(s: Session, task: NurseTask, text: str, actor_event: str) -> None:
    ep = s.get(Episode, task.episode_id)
    p = s.get(Patient, ep.patient_id)
    t = now(s)
    get_channel().send(s, ep, p, text, t, "nurse", None, sender="nurse")
    audit.log(s, t, "nurse", actor_event, ep.id, task_id=task.id, text=text)


def resolve(s: Session, task_id: int, note: str = "") -> None:
    task = s.get(NurseTask, task_id)
    task.status, task.resolved_at = "resolved", now(s)
    s.add(task)
    audit.log(s, now(s), "nurse", "task_resolved", task.episode_id, task_id=task_id, note=note)
    s.commit()


def approve(s: Session, task_id: int) -> None:
    """Carry out the suggested follow-through, then close the task."""
    task = s.get(NurseTask, task_id)
    if task.suggested_text:
        _send_nurse_text(s, task, task.suggested_text, "suggested_message_approved")
    audit.log(s, now(s), "nurse", "suggested_action_approved", task.episode_id, task_id=task_id,
              action=task.suggested_action)
    resolve(s, task_id, "approved suggested action")


def edit_and_send(s: Session, task_id: int, text: str) -> None:
    task = s.get(NurseTask, task_id)
    _send_nurse_text(s, task, text, "nurse_message_sent")
    resolve(s, task_id, "edited message sent")


def close_episode(s: Session, episode_id: int) -> None:
    ep = s.get(Episode, episode_id)
    ep.status = "closed"
    s.add(ep)
    for c in s.exec(select(CheckIn).where(CheckIn.episode_id == episode_id, CheckIn.status.in_(["scheduled", "sent"]))).all():
        c.status = "done"
        s.add(c)
    audit.log(s, now(s), "nurse", "episode_closed", episode_id)
    s.commit()
