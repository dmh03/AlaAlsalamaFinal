"""Handle one inbound patient reply end to end: read -> rules -> act. Everything is audited."""
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from . import audit, doctors, llm
from .channels import get_channel
from .clock import now
from .models import CheckIn, Decision, Episode, Extraction, Message, NurseTask, Patient
from .outbound import send_guarded
from .tasks import make_task
from .reader import read_reply
from .rules import RuleDecision, decide
from .schemas import AppointmentTriage, CheckInForm, QuestionMatches
from .templates import load_template
from .templates import rules as _rules
from .textnorm import norm as _norm

THANKS = {
    "ar": "شكراً لك، الله يعطيك العافية. المتابعة القادمة {when}.",
    "en": "Thank you. Your next check-in is {when}.",
}
CLARIFY = {
    "ar": "عذراً ما فهمت ردك تماماً. ممكن توضح أكثر كيف حالك الحين وكم الألم من 0 إلى 10؟",
    "en": "Sorry, I did not fully understand. Could you tell me a bit more about how you feel now, and your pain from 0 to 10?",
}
NO_ANSWER_NOTE = {
    "ar": "سؤالك وصلنا وبنخلي الممرضة ترد عليك.",
    "en": "We received your question and a nurse will get back to you.",
}
NURSE_FOLLOWUP = {
    "ar": "هلا، معك الممرضة من المتابعة. وصلنا ردك وبنتواصل معك اليوم لنطمن عليك.",
    "en": "Hello, this is the follow-up nurse. We received your reply and will contact you today to check on you.",
}
END_NOTE = {"ar": "هذي كانت آخر متابعة. الله يتمم عليك بالصحة.", "en": "That was your last check-in. Wishing you good health."}

FOLLOWUP_MILD = {
    "ar": "شكراً على طمانتك لنا. هل عندك أي أعراض ثانية مثل حرارة أو تورم أو ألم جديد؟ والممرضة بتتواصل معك اليوم.",
    "en": "Thank you for telling us. Do you have any other symptoms, such as a fever, swelling or new pain? A nurse will also contact you today.",
}
INVESTIGATE_URGENT = {
    "ar": "متى بدأت الأعراض؟ وهل هي تزيد؟ وهل أحد معك الحين؟ الممرضة المناوبة منبهة وتنتظر ردك.",
    "en": "When did this start, is it getting worse, and is someone with you right now? The on-call nurse has been alerted and is waiting for your reply.",
}
URGENT_STILL_OPEN = {
    "ar": "شكراً على التحديث. الممرضة المناوبة تتابع حالتك. وإذا زادت الأعراض اتصل بـ 997 فوراً.",
    "en": "Thank you for the update. The on-call nurse is following your case. If your symptoms get worse, call 997 right away.",
}

ACK = {
    "thanks": {"ar": ("العفو، الله يعطيك العافية. المتابعة القادمة {when}.", "العفو، الله يعطيك العافية."),
               "en": ("You're welcome. Your next check-in is {when}.", "You're welcome.")},
    "greeting": {"ar": ("هلا فيك! المتابعة القادمة {when}. وإذا شي يقلقك قبلها اكتب لنا هنا.",
                        "هلا فيك! وإذا شي يقلقك اكتب لنا هنا."),
                 "en": ("Hello! Your next check-in is {when}. If anything worries you before then, write to us here.",
                        "Hello! If anything worries you, write to us here.")},
}


_THANKS = {_norm(x) for x in [
    "thanks", "thank you", "thank you so much", "thanks a lot", "many thanks", "thx", "ty", "ok", "okay", "ok thanks",
    "okay thanks", "ok thank you", "okay thank you", "noted", "got it", "alright", "thanks nurse", "thank you nurse",
    "thanks doctor", "thank you doctor", "شكرا", "شكراً لك", "شكرا جزيلا", "مشكور", "مشكورين", "يعطيك العافية",
    "الله يعطيك العافية", "الله يعافيك", "تمام", "تمام شكرا", "طيب", "طيب شكرا", "اوكي", "اوكي شكرا", "ماشي",
    "جزاك الله خير", "جزاك الله خيرا", "الله يجزاك خير", "تسلم", "الله يسلمك", "يسلمك الله"]}
_GREETINGS = {_norm(x) for x in [
    "hi", "hello", "hey", "good morning", "good evening", "السلام عليكم", "السلام عليكم ورحمة الله", "سلام", "مرحبا",
    "هلا", "هلا والله", "اهلا", "اهلين", "صباح الخير", "مساء الخير"]}


def _pleasantry_kind(text: str) -> Optional[str]:
    n = _norm(text)
    if len(n) > 40:
        return None
    return "thanks" if n in _THANKS else "greeting" if n in _GREETINGS else None


LANG_SWITCHED = {
    "ar": "تمام، بنكمل بالعربي.",
    "en": "Sure, we'll continue in English.",
}


def _wants_language(text: str) -> Optional[str]:
    """A short message asking to change language, e.g. 'can we do this in Arabic?'. Deterministic, no model."""
    n = _norm(text)
    if len(n) > 40:
        return None
    if "عربي" in n or "arabic" in n:
        return "ar"
    if "english" in n or "انجليزي" in n or "انكليزي" in n or "انقلش" in n:
        return "en"
    return None


def _is_arabic_script(text: str) -> bool:
    ar = sum(1 for c in text if "\u0600" <= c <= "\u06ff")
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    return ar > 0 and ar >= lat


def _is_english(text: str) -> bool:
    """Clearly English: no Arabic script, and at least two Latin words (or one plain greeting/answer word).
    A lone token such as "ok" or a medicine name does not count."""
    if any("\u0600" <= c <= "\u06ff" for c in text):
        return False
    words = re.findall(r"[A-Za-z]{2,}", text)
    return len(words) >= 2 or (len(words) == 1 and words[0].lower() in _ENGLISH_ONE_WORD)


_ENGLISH_ONE_WORD = {"thanks", "thank", "hello", "hi", "hey", "fine", "good", "great", "yes", "no", "better", "worse"}


def emergency_text(tpl: dict, language: str) -> str:
    """Fixed text from the template. Never LLM-written, never passes through the guard."""
    return tpl["emergency"][language]


def critical_names(ep: Episode, tpl: dict) -> list[str]:
    return [m["name"] for m in json.loads(ep.medicines_json) if m["critical"]] + list(tpl.get("critical_medicines", []))


def _next_checkin_when(s: Session, ep: Episode, lang: str) -> Optional[str]:
    nxt = s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id, CheckIn.status == "scheduled")
                 .order_by(CheckIn.due_at)).first()
    if not nxt:
        return None
    return (f"يوم {nxt.due_at:%Y-%m-%d} الساعة {nxt.due_at:%H:%M}" if lang == "ar"
            else f"on {nxt.due_at:%Y-%m-%d} at {nxt.due_at:%H:%M}")


def _open_checkin(s: Session, ep: Episode) -> Optional[CheckIn]:
    return s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id, CheckIn.status == "sent")
                  .order_by(CheckIn.due_at.desc())).first()


def _match_questions(questions: list[str], tpl: dict, lang: str, t: datetime, ep_id: int) -> dict[str, str]:
    qa = {a["id"]: a for a in tpl["qa"]}
    catalog = "\n".join(f"{a['id']}: {a['q_' + lang]}" for a in tpl["qa"])
    user = f"Approved answers:\n{catalog}\n\nPatient questions:\n" + llm.wrap_untrusted("\n".join(questions))
    try:
        res = llm.parse("match_questions", llm.SONNET, llm.prompt("match_questions"), user, QuestionMatches, t, ep_id)
        return {m.question: (m.answer_id if m.answer_id in qa else "none") for m in res.matches}
    except llm.LLMUnavailable:
        return {q: "none" for q in questions}


def _history(s: Session, ep: Episode, n: int = 8) -> str:
    """Recent conversation, oldest first, without the newest patient message (that one is passed separately)."""
    rows = s.exec(select(Message).where(Message.episode_id == ep.id).order_by(Message.id.desc()).limit(n + 1)).all()
    if rows and rows[0].direction == "in":
        rows = rows[1:]
    who = lambda m: "Patient" if m.direction == "in" else ("Nurse" if m.sender == "nurse" else "Assistant")  # noqa: E731
    return "\n".join(f"{who(m)}: {' '.join(m.text.split())[:300]}" for m in reversed(rows[:n]))


def _answer_question(p: Patient, questions: list[str], t: datetime, ep_id: int, history: str = "") -> Optional[str]:
    """LLM-written reply to a question with no approved answer. None means use the fixed note instead."""
    user = (f"language: {p.language}\nname: {p.name}\nConversation so far:\n{llm.wrap_untrusted(history) if history else '(none)'}\n"
            f"Questions:\n" + llm.wrap_untrusted("\n".join(questions)))
    try:
        text = " ".join(llm.complete("answer_question", llm.SONNET, llm.prompt("answer_question"), user, t, ep_id, 1500).split())
    except llm.LLMUnavailable:
        return None
    return text if text and len(text) <= 500 else None


def _compose(mode: str, p: Patient, text: str, form: Optional[CheckInForm], tpl: dict, when: Optional[str],
             t: datetime, ep_id: int, history: str = "") -> Optional[str]:
    """LLM-written reply for positive / mild / clarify / pleasantry. None means use the fixed fallback."""
    lines = [f"mode: {mode}", f"language: {p.language}", f"name: {p.name}", f"next check-in: {when or 'none'}"]
    if form is not None:
        reported = [x.value for x in form.symptoms]
        ask = [x for x in tpl.get("concern_symptoms", []) if x not in reported]
        lines += [f"pain score: {form.pain_score}", f"symptoms reported: {reported}", f"symptoms to ask about: {ask}"]
    user = ("\n".join(lines) + f"\nConversation so far:\n{llm.wrap_untrusted(history) if history else '(none)'}\n"
            "Patient's new message:\n" + llm.wrap_untrusted(text))
    try:
        out = " ".join(llm.complete(f"reply_{mode}", llm.SONNET, llm.prompt("reply_style"), user, t, ep_id, 600).split())
    except llm.LLMUnavailable:
        return None
    return out if out and len(out) <= 400 else None


def _send_composed(s, ep, p, ci, t, mode, text, form, tpl, fallback: str, kind: str, when=None) -> None:
    msg = _compose(mode, p, text, form, tpl, when, t, ep.id, _history(s, ep))
    sent = send_guarded(s, ep, p, msg, t, kind, ci.id if ci else None, generated=True, task_on_block=False) if msg else None
    if sent is None:
        send_guarded(s, ep, p, fallback, t, kind, ci.id if ci else None, generated=False)
    audit.log(s, t, "agent", f"{kind}_sent", ep.id, mode=mode, composed=sent is not None)


CHECKUP_ASK = {
    "ar": "تبغى أرتب لك موعد فحص عند الدكتور؟ رد بنعم أو لا.",
    "en": "Would you like me to arrange a check-up appointment with a doctor? Reply yes or no.",
}
CHECKUP_DECLINED = {
    "ar": "تمام، لا مشكلة. اكتب لي في أي وقت إذا احتجت.",
    "en": "No problem. Write to me any time if you change your mind or anything worries you.",
}
CHECKUP_NO_SLOTS = {
    "ar": "سجلت طلبك، والممرضة بتتواصل معك لترتيب الموعد.",
    "en": "I have noted your request, and a nurse will contact you to arrange the appointment.",
}
_YES = {_norm(x) for x in [
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "yes please", "yes i would", "yes i do",
    "نعم", "ايه", "ايوه", "أيوه", "اي", "ابغى", "أبغى", "ابي", "أبي", "تمام", "اكيد", "أكيد", "طبعا", "ياليت", "نعم ابغى",
]}
_NO = {_norm(x) for x in [
    "no", "nope", "no thanks", "no thank you", "not now", "لا", "لا شكرا", "لا شكراً", "لاشكرا", "مو الحين", "ما ابغى", "ما ابي",
]}


def _yes_no(text: str) -> Optional[bool]:
    n = _norm(text)
    return True if n in _YES else (False if n in _NO else None)


def _assess_checkup(s, ep, p, ci, t, text, form, tpl) -> tuple[str, str]:
    """The agent decides whether this message warrants a doctor check-up: book, ask the patient, or none.
    It only recommends; the offer text and slots come from the DB. Returns (outcome, reason); 'unavailable' means no decision."""
    history = _history(s, ep)
    user = (f"language: {p.language}\nname: {p.name}\npain score: {form.pain_score}\n"
            f"symptoms read: {[x.value for x in form.symptoms]}\n"
            f"Conversation so far:\n{llm.wrap_untrusted(history) if history else '(none)'}\n"
            "Patient's new message:\n" + llm.wrap_untrusted(text))
    s.commit()
    try:
        tri = llm.parse("appointment_triage", llm.SONNET, llm.prompt("appointment_triage"), user, AppointmentTriage, t, ep.id, 300)
    except llm.LLMUnavailable:
        audit.log(s, t, "agent", "appointment_triage_unavailable", ep.id)
        return "unavailable", ""
    audit.log(s, t, "agent", "appointment_triage", ep.id, decision=tri.decision, reason=tri.reason)
    ci_id = ci.id if ci else None
    if tri.decision == "book":
        s.commit()
        if doctors.offer(s, ep, p, form, t, ci_id):
            return "offered", tri.reason
        return "none", tri.reason
    if tri.decision == "ask":
        _send_composed(s, ep, p, ci, t, "checkup_ask", text, form, tpl, CHECKUP_ASK[p.language], "checkup_ask")
        return "asked", tri.reason
    return "none", tri.reason


def handle_reply(s: Session, episode_id: int, text: str, form_override: Optional[CheckInForm] = None) -> dict:
    """form_override lets tests/eval bypass the LLM reader. Returns a summary dict."""
    llm.bind(s)
    ep = s.get(Episode, episode_id)
    p = s.get(Patient, ep.patient_id)
    tpl = load_template(ep.template)
    t = now(s)
    ch = get_channel()
    ci = _open_checkin(s, ep)
    inbound = ch.receive(s, ep, text, t, ci.id if ci else None)
    audit.log(s, t, "agent", "reply_received", ep.id, message_id=inbound.id)

    if form_override is None:
        want = _wants_language(text)
        if want and want != p.language:
            p.language = want
            s.add(p)
            audit.log(s, t, "rules", "language_switched", ep.id, to=want)
            msg = LANG_SWITCHED[want]
            if ci is not None:
                qs = tpl["questions"][want]
                msg += "\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs))
            send_guarded(s, ep, p, msg, t, "clarify", ci.id if ci else None, generated=False)
            s.commit()
            return {"route": "language_switched", "reasons": [f"patient asked for {want}"]}
        if p.language == "en" and want is None and _is_arabic_script(text):
            p.language = "ar"
            s.add(p)
            audit.log(s, t, "rules", "language_switched", ep.id, to="ar")
        elif p.language == "ar" and want is None and _is_english(text):
            p.language = "en"
            s.add(p)
            audit.log(s, t, "rules", "language_switched", ep.id, to="en")

    if form_override is None and ci is None:
        res = doctors.handle_offer_reply(s, ep, p, text, t)
        if res is not None:
            s.commit()
            return res

    prev_out = s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out")
                      .order_by(Message.id.desc())).first()
    if form_override is None and prev_out is not None and prev_out.kind == "checkup_ask" and _yes_no(text) is not None:
        lang0 = p.language
        if _yes_no(text):
            s.commit()
            if not doctors.offer(s, ep, p, None, t, ci.id if ci else None):
                send_guarded(s, ep, p, CHECKUP_NO_SLOTS[lang0], t, "answer", None, generated=False)
                make_task(s, ep, t, "routine", "Patient wants a check-up; no free slots", _rules()["sla_minutes"]["routine"],
                          f"Patient said yes to a check-up: {text}", "book_appointment")
            audit.log(s, t, "agent", "checkup_accepted", ep.id)
            route = "checkup_accepted"
        else:
            send_guarded(s, ep, p, CHECKUP_DECLINED[lang0], t, "answer", None, generated=False)
            audit.log(s, t, "agent", "checkup_declined", ep.id)
            route = "checkup_declined"
        s.commit()
        return {"route": route, "reasons": ["answer to the check-up question"]}
    if form_override is None and ci is None and prev_out is not None and prev_out.kind in ("thanks", "answer", "offer", "booking"):
        kind = _pleasantry_kind(text)
        if kind:
            when = _next_checkin_when(s, ep, p.language)
            with_when, without = ACK[kind][p.language]
            fallback = with_when.format(when=when) if when else without
            s.commit()
            _send_composed(s, ep, p, None, t, "pleasantry", text, None, tpl, fallback, "thanks", when)
            audit.log(s, t, "rules", "pleasantry_acknowledged", ep.id, kind=kind)
            s.commit()
            return {"route": "acknowledged", "reasons": ["pleasantry with no open check-in"]}

    form = form_override or read_reply(text, ep.template, p.language, t, ep.id, critical_names(ep, tpl))
    if form is None:
        make_task(s, ep, t, "same_day", "Reply could not be read automatically", 240,
                  f"Patient wrote: {text}", "review_reply")
        audit.log(s, t, "llm", "reader_failed", ep.id)
        s.commit()
        return {"route": "nurse_fallback", "reasons": ["reader unavailable"]}

    s.add(Extraction(message_id=inbound.id, form_json=form.model_dump_json(), created_at=t))
    d: RuleDecision = decide(form, tpl, critical_names(ep, tpl), asked_pain=bool(ci) and tpl.get("ask_pain", False))
    if (d.route == "unclear" and not form.symptoms and form.pain_score is None and form.symptoms_worsening is not True
            and not form.wants_human and not form.suspicious_input and doctors.pending_offer(s, ep)
            and doctors.wants_other_slots(s, text)):
        d = RuleDecision("question", ["patient asked for other appointment times"], "routine", _rules()["sla_minutes"]["routine"])
    if d.route == "unclear" and ci and ci.clarified:
        d.reasons.append("still unclear after clarifying question")
    s.add(Decision(message_id=inbound.id, route=d.route, reasons_json=json.dumps(d.reasons, ensure_ascii=False), created_at=t))
    audit.log(s, t, "rules", "decision", ep.id, route=d.route, reasons=d.reasons)

    facts = f"Patient reply: {text}\nExtracted: {form.model_dump_json()}\nReasons: {d.reasons}"
    lang = p.language
    if d.route == "red_flag":
        ch.send(s, ep, p, emergency_text(tpl, lang), t, "emergency", ci.id if ci else None)
        make_task(s, ep, t, "urgent", "RED FLAG: " + "; ".join(d.reasons), d.sla_minutes, facts, "call_patient_now")
        audit.log(s, t, "agent", "emergency_sent", ep.id)
        s.commit()
        ch.send(s, ep, p, INVESTIGATE_URGENT[lang], t, "followup", ci.id if ci else None)
        audit.log(s, t, "agent", "investigation_sent", ep.id)
    elif d.route == "concern":
        already_asked = prev_out is not None and prev_out.kind == "followup"
        open_task = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id, NurseTask.status == "open",
                                                   NurseTask.priority == "same_day")
                           .order_by(NurseTask.id.desc())).first() if already_asked else None
        if open_task:
            open_task.brief = f"{open_task.brief}\n\nPatient's answer to the follow-up question: {text}"
            s.add(open_task)
            audit.log(s, t, "agent", "followup_answer_added", ep.id, task_id=open_task.id)
        else:
            make_task(s, ep, t, "same_day", "; ".join(d.reasons), d.sla_minutes, facts, "contact_patient",
                      NURSE_FOLLOWUP[lang])
        if not already_asked and not (form.wants_human or form.suspicious_input):
            s.commit()
            _send_composed(s, ep, p, ci, t, "mild", text, form, tpl, FOLLOWUP_MILD[lang], "followup")
        elif not doctors.offer(s, ep, p, form, t, ci.id if ci else None):
            _thank(s, ep, p, ci, t, ch, lang, lead=True)
    elif d.route == "unclear":
        last_out = s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out")
                          .order_by(Message.id.desc())).first()
        already = ci.clarified if ci else bool(last_out and last_out.kind == "clarify")
        if not already:
            if ci:
                ci.clarified = True
                ci.last_sent_at, ci.attempts = t, 1
                s.add(ci)
            s.commit()
            _send_composed(s, ep, p, ci, t, "clarify", text, form, tpl, CLARIFY[lang], "clarify")
            audit.log(s, t, "agent", "clarifying_question_sent", ep.id)
            s.commit()
            return {"route": d.route, "reasons": d.reasons}
        make_task(s, ep, t, "same_day", "Unclear reply: " + "; ".join(d.reasons), 240, facts, "contact_patient")
    elif d.route == "question" and doctors.pending_offer(s, ep) and doctors.wants_other_slots(s, text) \
            and doctors.reoffer(s, ep, p, text, t, ci.id if ci else None):
        audit.log(s, t, "agent", "appointments_reoffered", ep.id, request=text[:120])
    elif d.route == "question":
        matches = _match_questions(form.questions, tpl, lang, t, ep.id)
        qa = {a["id"]: a for a in tpl["qa"]}
        unmatched = []
        for q, aid in matches.items():
            if aid == "none":
                unmatched.append(q)
            else:
                send_guarded(s, ep, p, qa[aid]["a_" + lang], t, "answer", ci.id if ci else None, generated=False)
                audit.log(s, t, "agent", "approved_answer_sent", ep.id, answer_id=aid)
        if unmatched or not matches:
            reply = _answer_question(p, unmatched or form.questions, t, ep.id, _history(s, ep))
            sent = None
            if reply:
                sent = send_guarded(s, ep, p, reply, t, "answer", ci.id if ci else None, generated=True, task_on_block=False)
                audit.log(s, t, "agent", "llm_answer_sent" if sent else "llm_answer_blocked", ep.id)
            if sent is None:
                send_guarded(s, ep, p, NO_ANSWER_NOTE[lang], t, "answer", ci.id if ci else None, generated=False)
            make_task(s, ep, t, "routine", "Question without approved answer: " + "; ".join(unmatched or form.questions),
                      d.sla_minutes, facts, "answer_question")
    else:
        urgent_open = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id, NurseTask.status == "open",
                                                      NurseTask.priority == "urgent")).first()
        if urgent_open:
            send_guarded(s, ep, p, URGENT_STILL_OPEN[lang], t, "thanks", ci.id if ci else None, generated=False)
        else:
            outcome, why = _assess_checkup(s, ep, p, ci, t, text, form, tpl)
            if outcome in ("offered", "asked"):
                make_task(s, ep, t, "routine", f"Agent suggested a check-up ({outcome}): {why}",
                          d.sla_minutes, facts, "review_reply")
            else:
                when = _next_checkin_when(s, ep, lang)
                fallback = THANKS[lang].format(when=when) if when else END_NOTE[lang]
                s.commit()
                _send_composed(s, ep, p, ci, t, "positive", text, form, tpl, fallback, "thanks", when)

    if ci:
        ci.status = "replied"
        s.add(ci)
    s.commit()
    return {"route": d.route, "reasons": d.reasons}


def _thank(s, ep, p, ci, t, ch, lang, lead=False):
    when = _next_checkin_when(s, ep, lang)
    if lead:
        msg = ("شكراً لك، الممرضة بتتواصل معك اليوم." if lang == "ar" else "Thank you. A nurse will contact you today.")
    else:
        msg = THANKS[lang].format(when=when) if when else END_NOTE[lang]
    send_guarded(s, ep, p, msg, t, "thanks", ci.id if ci else None, generated=False)
