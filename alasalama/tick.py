"""tick(): process everything due up to the clock time. Idempotent via status fields.

Single-patient test mode: set ALASALAMA_ONLY_PATIENTS=Sara (several names separated by , or ;) and tick/next_due ignore
every other patient, so a live test is fast and only touches the people you named. Unset it to run all patients."""
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from . import audit, llm
from .chase import REMINDER, UNREACHABLE_SUGGESTED, next_step
from .clock import now
from .models import CheckIn, Episode, Message, NurseTask, Patient
from .outbound import send_guarded
from .planner import plan_episode
from .tasks import make_task
from .templates import load_template, rules


def build_checkin_text(ep: Episode, p: Patient, ci: CheckIn, t: datetime) -> tuple[str, bool]:
    """Returns (text, generated_by_llm). Template fallback text is fixed and needs no classifier."""
    tpl = load_template(ep.template)
    qs = tpl["questions"][p.language]
    first = p.name.split()[0]
    try:
        user = llm.wrap_untrusted(f"language: {p.language}\nname: {first}\nday: {ci.day}\nquestions:\n- " + "\n- ".join(qs))
        text = llm.complete("checkin_message", llm.SONNET, llm.prompt("checkin_message"), user, t, ep.id, 400)
        if text:
            return text, True
    except llm.LLMUnavailable:
        pass
    if p.language == "ar":
        return (f"الحمد لله على السلامة {first}! هذي متابعة اليوم {ci.day}:\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))), False
    return (f"Hi {first}, glad you're home! Day {ci.day} check-in:\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))), False


def _fallback_checkin_text(ep, p, ci) -> str:
    qs = load_template(ep.template)["questions"][p.language]
    return "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))


def _send_checkin(s, ep, p, ci, t, stats):
    text, generated = build_checkin_text(ep, p, ci, t)
    m = send_guarded(s, ep, p, text, ci.due_at, "checkin", ci.id, generated=generated, task_on_block=False)
    if m is None:
        send_guarded(s, ep, p, _fallback_checkin_text(ep, p, ci), ci.due_at, "checkin", ci.id, generated=False)
    ci.status, ci.attempts, ci.last_sent_at = "sent", 1, ci.due_at
    s.add(ci)
    audit.log(s, ci.due_at, "agent", "checkin_sent", ep.id, day=ci.day)
    stats["sent"] += 1


def _chase(s, ep, p, ci, t, stats):
    """Advance reminders / unreachable for one unanswered check-in, as far as time t allows."""
    while ci.status == "sent":
        kind, at = next_step(ci, p)
        if at > t:
            return
        if kind == "reminder":
            send_guarded(s, ep, p, REMINDER[p.language].format(day=ci.day), at, "reminder", ci.id, generated=False)
            ci.attempts += 1
            ci.last_sent_at = at
            s.add(ci)
            audit.log(s, at, "agent", "reminder_sent", ep.id, day=ci.day, attempt=ci.attempts)
            stats["reminders"] += 1
        else:
            ci.status = "unreachable"
            s.add(ci)
            make_task(s, ep, at, "routine", f"Unreachable: no reply to day {ci.day} check-in after {ci.attempts} attempts",
                      rules()["sla_minutes"]["routine"], "", "call_patient", UNREACHABLE_SUGGESTED[p.language])
            audit.log(s, at, "agent", "marked_unreachable", ep.id, day=ci.day)
            stats["unreachable"] += 1


def _close(s, ep, t):
    ep.status = "closed"
    msgs = s.exec(select(Message).where(Message.episode_id == ep.id)).all()
    tasks = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id)).all()
    cis = s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id)).all()
    facts = (f"template: {ep.template}; check-ins: {[(c.day, c.status) for c in cis]}; "
             f"patient replies: {sum(m.direction == 'in' for m in msgs)}; nurse tasks: {[(k.priority, k.reason) for k in tasks]}")
    try:
        ep.closing_summary = llm.complete("episode_summary", llm.SONNET, llm.prompt("episode_summary"),
                                          llm.wrap_untrusted(facts), t, ep.id, 250)
    except llm.LLMUnavailable:
        ep.closing_summary = f"Episode closed. {facts}"
    s.add(ep)
    audit.log(s, t, "agent", "episode_closed", ep.id)


def focus_names() -> Optional[set]:
    raw = os.environ.get("ALASALAMA_ONLY_PATIENTS", "")
    names = {n.strip() for n in raw.replace(";", ",").split(",") if n.strip()}
    return names or None


def active_episodes(s: Session) -> list:
    """Active episodes, limited to the focus patients when ALASALAMA_ONLY_PATIENTS is set."""
    eps = s.exec(select(Episode).where(Episode.status == "active")).all()
    focus = focus_names()
    if focus is None:
        return list(eps)
    return [e for e in eps if s.get(Patient, e.patient_id).name in focus]


def tick(s: Session, until: Optional[datetime] = None) -> dict:
    llm.bind(s)
    t = until or now(s)
    stats = {"sent": 0, "reminders": 0, "unreachable": 0, "closed": 0, "planned": 0}

    for ep in active_episodes(s):
        if not s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id)).first():
            plan_episode(s, ep)
            stats["planned"] += 1
        p = s.get(Patient, ep.patient_id)
        if not p.consent:
            continue
        cis = s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id).order_by(CheckIn.day)).all()
        for ci in cis:
            if ci.status == "scheduled" and ci.due_at <= t:
                _send_checkin(s, ep, p, ci, t, stats)
            if ci.status == "sent":
                _chase(s, ep, p, ci, t, stats)
        last = cis[-1] if cis else None
        if (rules().get("episode", {}).get("auto_close", True) and last
                and last.status in ("replied", "unreachable", "done") and t >= last.due_at):
            _close(s, ep, t)
            stats["closed"] += 1
        s.commit()
    return stats


def next_due(s: Session) -> Optional[datetime]:
    """Earliest upcoming event: a scheduled check-in or the next chase step."""
    eps = active_episodes(s)
    ids = {e.id for e in eps}
    planned = {c.episode_id for c in s.exec(select(CheckIn)).all()}
    if any(e.id not in planned for e in eps):
        tick(s, datetime(1970, 1, 1))
    cands = []
    for c in s.exec(select(CheckIn).where(CheckIn.status == "scheduled").order_by(CheckIn.due_at)).all():
        if c.episode_id in ids:
            cands.append(c.due_at)
            break
    for ci in s.exec(select(CheckIn).where(CheckIn.status == "sent")).all():
        if ci.episode_id not in ids:
            continue
        ep = s.get(Episode, ci.episode_id)
        if ep.status == "active":
            cands.append(next_step(ci, s.get(Patient, ep.patient_id))[1])
    return min(cands) if cands else None
