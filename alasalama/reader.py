from datetime import datetime
from typing import Optional

from . import llm
from .schemas import CheckInForm
from .templates import load_template


def read_reply(text: str, tpl_name: str, language: str, now: datetime, episode_id: Optional[int] = None,
               medicines: Optional[list[str]] = None) -> Optional[CheckInForm]:
    """Returns None if the LLM fails; caller must route to a nurse. Never guess.

    `medicines` is the patient's medicine list, so a description like "blood thinner injection"
    can be reported under the listed name."""
    tpl = load_template(tpl_name)
    meds = medicines if medicines is not None else tpl.get("critical_medicines", [])
    asked = "\n".join(tpl["questions"].get(language, tpl["questions"]["en"]))
    user = (f"Questions that were asked:\n{asked}\n\n"
            f"Patient's medicines (report a mentioned medicine under the matching name from this list when it clearly refers to it):\n"
            f"{', '.join(meds) or 'none listed'}\n\nPatient reply:\n{llm.wrap_untrusted(text)}")
    try:
        return llm.parse("reader", llm.HAIKU, llm.prompt("reader"), user, CheckInForm, now, episode_id)
    except llm.LLMUnavailable:
        return None
