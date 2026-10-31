import json
from datetime import timedelta

from sqlmodel import Session, select

from . import audit, llm
from .clock import now, prayer_shift
from .models import CheckIn, Episode, Patient
from .templates import load_template, template_for_code


def plan_episode(s: Session, ep: Episode) -> list[CheckIn]:
    """Create check-ins from the template. Idempotent."""
    existing = s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id)).all()
    if existing:
        return list(existing)
    p = s.get(Patient, ep.patient_id)
    tpl = load_template(ep.template or template_for_code(ep.procedure_code))
    out = []
    for day in tpl["checkin_days"]:
        due = ep.discharge_date.replace(hour=0, minute=0) + timedelta(days=day, hours=p.preferred_hour)
        c = CheckIn(episode_id=ep.id, day=day, due_at=prayer_shift(due))
        s.add(c)
        out.append(c)
    t = now(s)
    ep.plan_summary = _summary(ep, p, tpl, t)
    s.add(ep)
    audit.log(s, t, "agent", "plan_created", ep.id, days=tpl["checkin_days"], template=tpl["name"])
    s.commit()
    return out


def _summary(ep: Episode, p: Patient, tpl: dict, t) -> str:
    meds = [m["name"] for m in json.loads(ep.medicines_json) if m["critical"]]
    facts = (f"procedure template: {tpl['name']}; check-in days: {tpl['checkin_days']}; language: {p.language}; "
             f"preferred hour: {p.preferred_hour}:00; critical medicines: {meds}")
    try:
        return llm.complete("plan_summary", llm.SONNET, llm.prompt("plan_summary"),
                            llm.wrap_untrusted(facts), t, ep.id, max_tokens=250)
    except llm.LLMUnavailable:
        return (f"{tpl['name'].replace('_', ' ').title()} follow-up: check-ins on days "
                f"{', '.join(map(str, tpl['checkin_days']))} at {p.preferred_hour}:00 ({p.language}). "
                f"Critical medicines: {', '.join(meds) or 'none'}. (Demo, needs clinician review)")
