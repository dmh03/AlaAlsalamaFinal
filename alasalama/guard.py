"""Outgoing-message guard. Applied to every outgoing message except the fixed emergency text."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from . import llm
from .templates import rules

_D = r"[\d٠-٩]"
DOSAGE = re.compile(
    rf"{_D}+(?:[.,]{_D}+)?\s*(?:mg|mcg|µg|ml|iu|units?|tablets?|pills?|capsules?|ملغ|ملجم|مجم|مل|وحدة|وحدات|حبة|حبات|حبتين)\b",
    re.I)
MED_CHANGE = [
    re.compile(r"\b(?:stop|skip|double|increase|decrease|reduce|change|switch|halve|start taking)\b.{0,30}"
               r"\b(?:medic\w*|dose|dosage|tablets?|pills?|injections?|drugs?)\b", re.I),
    re.compile(r"\b(?:do not|don't|dont)\s+take\b", re.I),
    re.compile(r"(?:وقف|أوقف|اوقف|لا تاخذ|لا تأخذ|ما تاخذ|زيد|زِد|قلل|غيّر|غير|بدّل|بدل)\s.{0,25}"
               r"(?:الدواء|دواء|دوائك|الجرعة|الحبوب|الحبة|الإبرة|الابرة|ابرة|إبرة)"),
]


class GuardVerdict(BaseModel):
    allowed: bool
    reasons: list[str] = []


@dataclass
class GuardResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def deterministic_check(text: str, max_len: Optional[int] = None) -> GuardResult:
    max_len = max_len or rules()["guard"]["max_length"]
    reasons = []
    if DOSAGE.search(text):
        reasons.append("contains a dosage pattern")
    if any(p.search(text) for p in MED_CHANGE):
        reasons.append("contains medicine-change wording")
    if len(text) > max_len:
        reasons.append(f"longer than {max_len} characters")
    return GuardResult(not reasons, reasons)


def check(text: str, now: datetime, episode_id: Optional[int] = None, use_llm: bool = True) -> GuardResult:
    r = deterministic_check(text)
    if not r.allowed:
        return r
    if use_llm and llm.available():
        try:
            v = llm.parse("guard", llm.HAIKU, llm.prompt("guard"), llm.wrap_untrusted(text), GuardVerdict,
                          now, episode_id, max_tokens=200)
        except llm.LLMUnavailable:
            return GuardResult(False, ["guard classifier failed; failing closed"])
        if not v.allowed:
            return GuardResult(False, v.reasons or ["blocked by classifier"])
    return GuardResult(True, [])
