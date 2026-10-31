"""Scripted-bot baseline: keywords and regex only. Same output schema as the LLM reader."""
import re

from alasalama.schemas import CheckInForm, MedItem

NEG = re.compile(r"\b(no|not|never|without|n't)\b|ما\s|مافي|ما فيه|ماني|بدون|لا\s", re.I)

SYM = {
    "chest_pain": [r"chest pain", r"pain in (my )?chest", r"ألم في الصدر", r"وجع في الصدر", r"صدري يوجعني"],
    "breathing_difficulty": [r"can'?t breathe", r"trouble breathing", r"short of breath", r"difficulty breathing",
                             r"ما أقدر أتنفس", r"صعوبة في التنفس", r"ضيق نفس", r"نفسي ضايق"],
    "breathless_lying_down": [r"lying down", r"انسدحت", r"منسدح"],
    "heavy_bleeding": [r"heavy bleeding", r"bleeding a lot", r"soaking", r"نزيف شديد", r"ينزف كثير", r"نزيف كثير"],
    "fever": [r"fever", r"temperature", r"حرارة", r"سخونة", r"حمى"],
    "wound_redness": [r"red(ness)?\b", r"محمر", r"احمرار"],
    "wound_discharge": [r"discharge", r"pus", r"إفرازات", r"صديد"],
    "wound_opening": [r"opened up", r"wound open", r"الجرح انفتح", r"انفتح الجرح"],
    "calf_pain_or_swelling": [r"calf", r"ساق", r"بطة رجلي"],
    "leg_swelling": [r"swollen (legs?|feet)", r"legs? (are |is )?swollen", r"swelling", r"منتفخ", r"تورم"],
    "rapid_weight_gain": [r"gained .* (kg|kilo)", r"weight gain", r"زاد وزني"],
    "dizziness_or_fainting": [r"dizzy", r"faint", r"دوخة", r"دايخ", r"أغمي"],
    "confusion": [r"confus", r"مشوش"],
    "vomiting": [r"vomit", r"throwing up", r"استفراغ", r"أرجع"],
    "severe_headache": [r"severe headache", r"صداع شديد"],
}
HUMAN = [r"\bnurse\b", r"\bdoctor\b", r"speak to (a )?(person|human)", r"ممرضة", r"دكتور", r"طبيب", r"أبي أكلم"]
INJECT = [r"ignore (your|all|previous)", r"show me all", r"system prompt", r"تجاهل", r"انس التعليمات"]
WORSE = [r"worse", r"getting worse", r"أسوأ", r"يزيد", r"زاد"]
NOTWORSE = [r"not worse", r"no worse", r"same", r"better", r"مو أسوأ", r"أحسن", r"نفسه"]


def _any(pats, text):
    return any(re.search(p, text, re.I) for p in pats)


def read_reply(text: str) -> CheckInForm:
    t = text.strip()
    syms = []
    for sym, pats in SYM.items():
        for p in pats:
            for m in re.finditer(p, t, re.I):
                pre = t[max(0, m.start() - 14): m.start()]
                if not NEG.search(pre):
                    syms.append(sym)
                    break
            else:
                continue
            break
    nums = [int(x) for x in re.findall(r"(?<![\d/])(\d{1,2})(?![\d/])", t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
            if int(x) <= 10]
    pain = nums[0] if nums else None
    meds = []
    if re.search(r"stopped|skipped|didn'?t take|not taking|موقف|ما أخذت|ما اخذت|تركت", t, re.I):
        meds.append(MedItem(name="medicine", taken=False))
    qs = [t] if re.search(r"\?|؟", t) else []
    return CheckInForm(
        pain_score=pain, symptoms=sorted(set(syms)),
        symptoms_worsening=True if (_any(WORSE, t) and not _any(NOTWORSE, t)) else None,
        medications=meds, questions=qs, wants_human=_any(HUMAN, t), suspicious_input=_any(INJECT, t),
        confidence=0.9 if (syms or pain is not None or qs) else 0.5,
    )
