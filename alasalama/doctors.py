"""Dummy doctor roster and doctor-appointment offers for non-emergency concerns.

On a `concern` route the patient gets a reassuring message with a few appointment options, and can reply with a number to
book. Who writes what:
  * the opening lines are written by the LLM and shown as written (an empty or overlong output falls back to a fixed
    opening) after the guard has checked the whole message;
  * the options are built from the database, never by the model;
  * booking, declining and slot conflicts are handled by plain code.
Red flags never come here: they get the fixed emergency text. The nurse task is created either way.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import yaml
from sqlmodel import Session, select

from . import audit, llm
from .db import ROOT
from .models import Appointment, Doctor, Episode, Message, NurseTask, Patient
from .outbound import send_guarded
from .schemas import CheckInForm
from .templates import load_template, rules
from .textnorm import norm

DOCTORS_FILE = ROOT / "config" / "doctors.yaml"
N_OPTIONS = 3
LEAD_TIME = timedelta(hours=3)
HORIZON_DAYS = 14
_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
WEEKDAY = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
           "ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]}

LEAD = {
    "en": "Thank you for letting us know. It is a good idea to have this checked by a doctor, and here are some appointment options.",
    "ar": "شكراً لأنك طمنتنا. من الأفضل أن يشوفك دكتور، وهذي مواعيد متاحة لك.",
}
HEADER = {"en": "Available {spec} appointments:", "ar": "مواعيد متاحة في {spec}:"}
ASK = {"en": 'Reply with the number to book (for example 1), or reply "no" if you prefer not to.',
       "ar": 'اكتب رقم الموعد للحجز (مثلاً 1)، أو اكتب "لا" إذا ما تبغى.'}
BOOKED = {
    "en": "Your appointment is booked: {doctor}, {when}, {clinic}. If you feel worse before then, call 997 or go to the nearest emergency department.",
    "ar": "تم حجز موعدك: {doctor}، {when}، {clinic}. وإذا ساءت حالتك قبل الموعد اتصل على 997 أو توجه لأقرب طوارئ.",
}
DECLINED = {"en": "No problem. You can write to us any time if you change your mind or if anything worries you.",
            "ar": "ولا يهمك. اكتب لنا في أي وقت إذا غيرت رأيك أو إذا شي يقلقك."}
TAKEN = {"en": "Sorry, that time was just taken. Here are other options.",
         "ar": "عذراً، هذا الموعد انحجز قبل شوي. هذي مواعيد ثانية."}
TAKEN_NONE = {"en": "Sorry, that time was just taken and no other appointments are available right now. We will let the nurse know.",
              "ar": "عذراً، هذا الموعد انحجز قبل شوي وما فيه مواعيد ثانية حالياً. بنبلغ الممرضة."}

_DECLINE = {norm(x) for x in [
    "no", "nope", "no thanks", "no thank you", "not now", "later", "maybe later", "no need", "i will wait", "i'll wait",
    "لا", "لا شكرا", "لا شكرا لك", "لا مو الحين", "مو الحين", "بعدين", "ما ابغى", "ما ابي", "لا ما ابغى", "لا ابي"]}
_PICK = re.compile(r"^(?:(?:option|number|choice|اختار|ابغى|ابي|الخيار|رقم|الموعد)\s+)?(\d)$")
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
        "الاول": 1, "الاولى": 1, "الثاني": 2, "الثانيه": 2, "الثالث": 3, "الثالثه": 3}
_ORD_RE = re.compile(r"^(?:the |option |الخيار )?(first|second|third|الاول|الثاني|الثالث)(?: one| option)?$")


def _hours(r: dict, default_step: int) -> str:
    start, end = r["hours"]
    return json.dumps({"start": start, "end": end, "slot_minutes": int(r.get("slot_minutes", default_step))})


def slot_times(d: Doctor) -> list[str]:
    """Every bookable start time in a working day, e.g. 09:00, 09:30 ... 19:30 for hours 09:00-20:00 in 30-minute slots.
    A legacy roster stored a fixed list of times; that is still understood."""
    t = json.loads(d.times_json)
    if isinstance(t, list):
        return t
    (h1, m1), (h2, m2) = ((int(x) for x in t[k].split(":")) for k in ("start", "end"))
    cur, end, step = h1 * 60 + m1, h2 * 60 + m2, t["slot_minutes"]
    out = []
    while cur + step <= end:
        out.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step
    return out


def hours_label(d: Doctor) -> str:
    t = json.loads(d.times_json)
    return f"{t['start']} - {t['end']} · {t['slot_minutes']} min slots" if isinstance(t, dict) else ", ".join(t)


def load_doctors(s: Session) -> int:
    cfg = yaml.safe_load(DOCTORS_FILE.read_text(encoding="utf-8"))
    step = cfg.get("slot_minutes", 30)
    for r in cfg["doctors"]:
        s.add(Doctor(name_en=r["name_en"], name_ar=r["name_ar"], specialty=r["specialty"],
                     specialty_en=r["specialty_en"], specialty_ar=r["specialty_ar"],
                     clinic_en=r["clinic_en"], clinic_ar=r["clinic_ar"],
                     days_json=json.dumps([_DAYS[d] for d in r["days"]]), times_json=_hours(r, step)))
    s.flush()
    return len(cfg["doctors"])


def ensure_doctors(s: Session) -> None:
    """A database created before the roster existed has the table but no rows. A roster that still has fixed time lists
    is moved to working hours in place, keeping doctor ids so existing appointments stay valid."""
    docs = s.exec(select(Doctor)).all()
    if not docs:
        load_doctors(s)
        return
    legacy = [d for d in docs if isinstance(json.loads(d.times_json), list)]
    if legacy:
        cfg = yaml.safe_load(DOCTORS_FILE.read_text(encoding="utf-8"))
        step = cfg.get("slot_minutes", 30)
        rows = {r["name_en"]: r for r in cfg["doctors"]}
        for d in legacy:
            if d.name_en in rows:
                d.times_json = _hours(rows[d.name_en], step)
                s.add(d)
        s.flush()


def free_slots(s: Session, specialty: str, after: datetime, n: int = N_OPTIONS, doctor_ids: Optional[set] = None,
               near: Optional[int] = None, exclude: Optional[set] = None) -> list[tuple[datetime, Doctor]]:
    """The soonest free slots, preferring different doctors. Booked slots are taken; merely offered ones are not held.
    doctor_ids limits to named doctors; near (minutes after midnight) returns the slots closest to that time of day;
    exclude is a set of (doctor_id, starts_at) already shown to the patient."""
    exclude = exclude or set()
    if doctor_ids:
        docs = s.exec(select(Doctor).where(Doctor.id.in_(doctor_ids)).order_by(Doctor.id)).all()
    else:
        docs = s.exec(select(Doctor).where(Doctor.specialty == specialty).order_by(Doctor.id)).all()
        if not docs:
            docs = s.exec(select(Doctor).where(Doctor.specialty == "general").order_by(Doctor.id)).all()
    booked = {(a.doctor_id, a.starts_at) for a in s.exec(select(Appointment).where(Appointment.status == "booked")).all()}
    earliest = after + LEAD_TIME
    cands: list[tuple[datetime, Doctor]] = []
    for d in docs:
        days, times = json.loads(d.days_json), slot_times(d)
        for off in range(HORIZON_DAYS + 1):
            day = (earliest + timedelta(days=off)).date()
            if day.weekday() not in days:
                continue
            for hhmm in times:
                h, m = (int(x) for x in hhmm.split(":"))
                st = datetime(day.year, day.month, day.day, h, m)
                if st >= earliest and (d.id, st) not in booked and (d.id, st) not in exclude:
                    cands.append((st, d))
    if near is not None:
        cands.sort(key=lambda c: (abs(c[0].hour * 60 + c[0].minute - near), c[0], c[1].id))
        return sorted(cands[:n], key=lambda c: (c[0], c[1].id))
    cands.sort(key=lambda c: (c[0], c[1].id))
    picked: list[tuple[datetime, Doctor]] = []
    seen: set = set()
    for c in cands:
        if c[1].id not in seen:
            picked.append(c)
            seen.add(c[1].id)
        if len(picked) == n:
            break
    for c in cands:
        if len(picked) >= n:
            break
        if c not in picked:
            picked.append(c)
    return sorted(picked, key=lambda c: (c[0], c[1].id))[:n]


def _name(d: Doctor, lang: str) -> str:
    return d.name_ar if lang == "ar" else d.name_en


def _when(st: datetime, lang: str) -> str:
    return f"{WEEKDAY[lang][st.weekday()]} {st:%Y-%m-%d} " + ("الساعة " if lang == "ar" else "at ") + f"{st:%H:%M}"


def _compose(lead: str, slots: list[tuple[datetime, Doctor]], lang: str) -> str:
    sep = "،" if lang == "ar" else ","
    lines = [f"{i}. {_name(d, lang)}{sep} {WEEKDAY[lang][st.weekday()]} {st:%Y-%m-%d} {st:%H:%M}"
             for i, (st, d) in enumerate(slots, 1)]
    spec = slots[0][1].specialty_ar if lang == "ar" else slots[0][1].specialty_en
    return f"{lead}\n\n{HEADER[lang].format(spec=spec)}\n" + "\n".join(lines) + f"\n\n{ASK[lang]}"


def _lead(s: Session, p: Patient, form: CheckInForm, t: datetime, episode_id: int) -> tuple[str, bool]:
    """(opening text, was it LLM-written). Anything off-spec falls back to the fixed opening."""
    fixed = LEAD[p.language]
    symptoms = ", ".join(x.value for x in form.symptoms) or "none stated"
    pain = form.pain_score if form.pain_score is not None else "not stated"
    user = llm.wrap_untrusted(f"language: {p.language}\nname: {p.name}\nreported: {symptoms}\npain: {pain}")
    try:
        text = " ".join(llm.complete("doctor_offer", llm.SONNET, llm.prompt("doctor_offer"), user, t, episode_id, 1000).split())
    except llm.LLMUnavailable:
        return fixed, False
    if not text or len(text) > 300:
        audit.log(s, t, "llm", "doctor_offer_rejected", episode_id, reason="empty or too long")
        return fixed, False
    return text, True


def offer(s: Session, ep: Episode, p: Patient, form: Optional[CheckInForm], t: datetime,
          checkin_id: Optional[int] = None, intro: Optional[str] = None,
          slots: Optional[list] = None) -> bool:
    """Send the reassuring message with appointment options. False means nothing was sent (caller falls back)."""
    ensure_doctors(s)
    lang = p.language
    if slots is None:
        slots = free_slots(s, load_template(ep.template).get("doctor_specialty", "general"), t)
    if not slots:
        audit.log(s, t, "agent", "no_appointments_available", ep.id)
        return False
    for a in s.exec(select(Appointment).where(Appointment.episode_id == ep.id, Appointment.status == "offered")).all():
        a.status = "expired"
        s.add(a)
    rows = []
    for i, (st, d) in enumerate(slots, 1):
        a = Appointment(episode_id=ep.id, doctor_id=d.id, starts_at=st, option_no=i, created_at=t)
        s.add(a)
        rows.append(a)
    s.flush()

    if intro is not None:
        lead, generated = intro, False
    elif form is not None:
        lead, generated = _lead(s, p, form, t, ep.id)
    else:
        lead, generated = LEAD[lang], False
    text = _compose(lead, slots, lang)
    if len(text) > rules()["guard"]["max_length"]:
        lead, generated = LEAD[lang], False
        text = _compose(lead, slots, lang)
    msg = send_guarded(s, ep, p, text, t, "offer", checkin_id, generated=generated, task_on_block=False)
    if msg is None and generated:
        msg = send_guarded(s, ep, p, _compose(LEAD[lang], slots, lang), t, "offer", checkin_id, generated=False,
                           task_on_block=False)
    if msg is None:
        for a in rows:
            a.status = "expired"
            s.add(a)
        return False
    audit.log(s, t, "agent", "appointments_offered", ep.id, llm_opening=generated,
              options=[f"{d.name_en} {st:%Y-%m-%d %H:%M}" for st, d in slots])
    return True


def pending_offer(s: Session, ep: Episode) -> list[Appointment]:
    """Offered options the patient can still answer: the latest offer, with no check-in, reminder or emergency since."""
    last = s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out", Message.kind == "offer")
                  .order_by(Message.id.desc())).first()
    if last is None:
        return []
    newer = s.exec(select(Message).where(Message.episode_id == ep.id, Message.direction == "out", Message.id > last.id,
                                         Message.kind.in_(("checkin", "reminder", "emergency")))).first()
    if newer is not None:
        return []
    return list(s.exec(select(Appointment).where(Appointment.episode_id == ep.id, Appointment.status == "offered")
                       .order_by(Appointment.option_no)).all())


def parse_pick(text: str, n: int) -> Optional[int]:
    """Whole-message option number: "2", "٢", "option 2", "the second one", "الخيار الثاني". Anything else is not a pick."""
    t = norm(text)
    m = _PICK.match(t)
    if m:
        k = int(m.group(1))
    else:
        o = _ORD_RE.match(t)
        k = _ORD.get(o.group(1)) if o else None
    return k if k and 1 <= k <= n else None


def _note_task(s: Session, ep: Episode, note: str) -> None:
    task = s.exec(select(NurseTask).where(NurseTask.episode_id == ep.id, NurseTask.status == "open")
                  .order_by(NurseTask.id.desc())).first()
    if task:
        task.brief = f"{task.brief}\n{note}".strip()
        s.add(task)


def handle_offer_reply(s: Session, ep: Episode, p: Patient, text: str, t: datetime) -> Optional[dict]:
    """Deterministic. A bare option number books it and a bare "no" declines. Anything else returns None and goes
    through the normal reader, so a symptom message is never swallowed here."""
    pending = pending_offer(s, ep)
    if not pending:
        return None
    lang = p.language
    k = parse_pick(text, len(pending))
    if k is None:
        if norm(text) not in _DECLINE:
            return None
        for a in pending:
            a.status = "declined"
            s.add(a)
        send_guarded(s, ep, p, DECLINED[lang], t, "answer", None, generated=False)
        audit.log(s, t, "agent", "appointment_declined", ep.id)
        return {"route": "appointment_declined", "reasons": ["patient declined the doctor options"]}

    chosen = next(a for a in pending if a.option_no == k)
    doc = s.get(Doctor, chosen.doctor_id)
    taken = s.exec(select(Appointment).where(Appointment.doctor_id == chosen.doctor_id, Appointment.starts_at == chosen.starts_at,
                                             Appointment.status == "booked", Appointment.episode_id != ep.id)).first()
    if taken is not None:
        for a in pending:
            a.status = "expired"
            s.add(a)
        s.flush()
        audit.log(s, t, "agent", "appointment_slot_taken", ep.id, doctor=doc.name_en, starts_at=f"{chosen.starts_at:%Y-%m-%d %H:%M}")
        if not offer(s, ep, p, None, t, intro=TAKEN[lang]):
            send_guarded(s, ep, p, TAKEN_NONE[lang], t, "answer", None, generated=False)
        return {"route": "appointment_reoffered", "reasons": ["chosen slot was already booked"]}

    chosen.status = "booked"
    s.add(chosen)
    for a in pending:
        if a is not chosen:
            a.status = "expired"
            s.add(a)
    clinic = doc.clinic_ar if lang == "ar" else doc.clinic_en
    send_guarded(s, ep, p, BOOKED[lang].format(doctor=_name(doc, lang), when=_when(chosen.starts_at, lang), clinic=clinic),
                 t, "booking", None, generated=False)
    _note_task(s, ep, f"Patient booked {doc.name_en} on {chosen.starts_at:%Y-%m-%d %H:%M} ({doc.clinic_en}).")
    audit.log(s, t, "agent", "appointment_booked", ep.id, doctor=doc.name_en, starts_at=f"{chosen.starts_at:%Y-%m-%d %H:%M}")
    return {"route": "appointment_booked", "reasons": [f"booked {doc.name_en}"]}


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ANCHOR = re.compile(r"\b(?:slots?|appointments?|reschedul\w*|schedul\w*|book(?:ing)?)\b|موعد|مواعيد|وقت|اوقات|احجز|حجز")
_OTHER = re.compile(r"\b(?:other|another|different|more|else)\b|غير|ثاني|ثانيه|اخر|بدل")
EN_PERIOD = {"morning": 9 * 60, "afternoon": 14 * 60, "evening": 18 * 60, "tonight": 18 * 60}
REOFFER = {
    "en": {"other": "Here are some other available options.",
           "near": "Here are the available times closest to what you asked for.",
           "far": "That exact time is not available, so here are the closest options."},
    "ar": {"other": "هذي مواعيد ثانية متاحة.",
           "near": "هذي أقرب المواعيد المتاحة لطلبك.",
           "far": "هذا الوقت بالضبط مو متاح، وهذي أقرب المواعيد."},
}


@dataclass
class Pref:
    near: Optional[int] = None
    doctor_ids: set = field(default_factory=set)
    other: bool = False


def parse_pref(text: str, docs: list[Doctor]) -> Pref:
    raw = text.lower().translate(_AR_DIGITS)
    n = norm(text).translate(_AR_DIGITS)
    pref = Pref(other=bool(_OTHER.search(raw) or _OTHER.search(n)))
    m = re.search(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", raw)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).startswith("p") else 0)
        pref.near = h * 60 + int(m.group(2) or 0)
    elif (m := re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", raw)):
        pref.near = int(m.group(1)) * 60 + int(m.group(2))
    elif (m := re.search(r"(\d{1,2})\s*(?:ونص|والنص)?\s*(?:ال)?(مساء|عصرا|عصر|ظهرا|ظهر|صباحا|صباح|صبح|ليلا|ليل)", n)):
        h, word = int(m.group(1)) % 24, m.group(2)
        if word in ("مساء", "عصرا", "عصر", "ليلا", "ليل") and h < 12:
            h += 12
        elif word in ("ظهرا", "ظهر") and h < 7:
            h += 12
        pref.near = h * 60 + (30 if "نص" in n else 0)
    else:
        for w, mins in EN_PERIOD.items():
            if re.search(rf"\b{w}\b", raw):
                pref.near = mins
                break
        else:
            if re.search(r"الصبح|صباحا", n):
                pref.near = 9 * 60
            elif re.search(r"العصر|ظهر", n):
                pref.near = 14 * 60
            elif re.search(r"مساء|المغرب|الليل", n):
                pref.near = 18 * 60
    for d in docs:
        en, ar = d.name_en.split()[1].lower(), norm(d.name_ar.split()[1])
        if en in raw or ar in n.split():
            pref.doctor_ids.add(d.id)
    return pref


def wants_other_slots(s: Session, text: str) -> bool:
    """Only called after the reader has already ruled out symptoms, and only while an offer is pending."""
    n = norm(text)
    pref = parse_pref(text, s.exec(select(Doctor)).all())
    return bool(_ANCHOR.search(text.lower()) or _ANCHOR.search(n) or pref.near is not None
                or (pref.doctor_ids and pref.other))


def reoffer(s: Session, ep: Episode, p: Patient, text: str, t: datetime, checkin_id: Optional[int] = None) -> bool:
    """New options that respect the patient's doctor / time-of-day / "other" request. False = nothing sent."""
    ensure_doctors(s)
    pref = parse_pref(text, s.exec(select(Doctor)).all())
    specialty = load_template(ep.template).get("doctor_specialty", "general")
    shown = {(a.doctor_id, a.starts_at) for a in s.exec(select(Appointment).where(Appointment.episode_id == ep.id)).all()}
    slots = free_slots(s, specialty, t, doctor_ids=pref.doctor_ids or None, near=pref.near,
                       exclude=shown if pref.other and pref.near is None else None)
    if not slots:
        return False
    msgs = REOFFER[p.language]
    if pref.near is not None:
        gap = min(abs(st.hour * 60 + st.minute - pref.near) for st, _ in slots)
        intro = msgs["far"] if gap > 60 else msgs["near"]
    else:
        intro = msgs["other"]
    return offer(s, ep, p, None, t, checkin_id, intro=intro, slots=slots)
