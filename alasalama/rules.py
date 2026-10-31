"""Deterministic rules engine. No LLM. Precedence: red_flag > concern > unclear > question > routine."""
from dataclasses import dataclass, field

from .schemas import CheckInForm
from .templates import rules as load_rules


@dataclass
class RuleDecision:
    route: str
    reasons: list[str] = field(default_factory=list)
    priority: str = "routine"
    sla_minutes: int = 0


def _is_critical(name: str, critical_names: list[str]) -> bool:
    n = name.strip().lower()
    return any(n and (n in c.lower() or c.lower() in n) for c in critical_names)


def decide(form: CheckInForm, tpl: dict, critical_names: list[str], asked_pain: bool | None = None,
           cfg: dict | None = None) -> RuleDecision:
    cfg = cfg or load_rules()
    asked_pain = tpl.get("ask_pain", False) if asked_pain is None else asked_pain
    sla = cfg["sla_minutes"]
    syms = {s.value for s in form.symptoms}

    red = sorted(syms & set(tpl["red_flag_symptoms"]))
    if red:
        return RuleDecision("red_flag", [f"red-flag symptom: {x}" for x in red], "urgent", sla["red_flag"])

    c = cfg["concern"]
    reasons = []
    if form.pain_score is not None and form.pain_score >= c["pain_score_min"]:
        reasons.append(f"pain score {form.pain_score} >= {c['pain_score_min']}")
    if c["symptoms_worsening"] and form.symptoms_worsening is True:
        reasons.append("patient reports symptoms getting worse")
    reasons += [f"concern symptom: {x}" for x in sorted(syms & set(tpl["concern_symptoms"]))]
    if c["critical_med_missed"]:
        for m in form.medications:
            if m.taken is False and _is_critical(m.name, critical_names):
                reasons.append(f"critical medicine not taken: {m.name}")
    if c["wants_human"] and form.wants_human:
        reasons.append("patient asked for a human")
    if c["suspicious_input"] and form.suspicious_input:
        reasons.append("suspicious input (possible instruction injection)")
    if reasons:
        return RuleDecision("concern", reasons, "same_day", sla["concern"])

    u = cfg["unclear"]
    if form.confidence < u["confidence_below"]:
        return RuleDecision("unclear", [f"confidence {form.confidence:.2f} < {u['confidence_below']}"])
    if u["missing_pain_when_asked"] and asked_pain and form.pain_score is None and not form.questions:
        return RuleDecision("unclear", ["pain was asked but not given"])

    if form.questions:
        return RuleDecision("question", ["patient asked a question"], "routine", sla["routine"])
    return RuleDecision("routine", ["no concerns found"])
