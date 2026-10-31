"""Part B: process simulation. 100 synthetic patients from a fixed seed, each with a hidden truth card.
Sonnet role-plays replies from the card (cached on disk). Ala Salama runs end to end on the simulated clock.

All patients are synthetic. The "patient" is an LLM, not a real person: results show how the agent behaves
against simulated replies and are NOT clinical evidence.
"""
import json
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlmodel import select

from alasalama import clock, db, guard, llm, planner
from alasalama.db import ROOT, reset_db, session
from alasalama.models import CheckIn, ClockState, Decision, Episode, LLMCall, Message, NurseTask, Patient
from alasalama.reader import read_reply
from alasalama.router import critical_names, handle_reply
from alasalama.seed import EXTRA_NAMES_AR, EXTRA_NAMES_EN, TEMPLATES
from alasalama.templates import load_template, rules
from alasalama.tick import build_checkin_text, next_due, tick

START = datetime(2026, 11, 1, 8, 0)
DISCHARGE = datetime(2026, 11, 1)
END = datetime(2026, 12, 6)
NOW = datetime(2026, 11, 2, 10, 0)
CACHE = ROOT / "data" / "cache" / "sim"
SIM_DB = ROOT / "data" / "eval_sim.db"

SYMPTOM_DESC = {
    "chest_pain": "pain in the chest", "breathing_difficulty": "trouble breathing",
    "heavy_bleeding": "heavy bleeding, soaking through pads or the bandage",
    "breathless_lying_down": "short of breath when lying down", "confusion": "feeling confused",
    "fever": "fever, feeling hot", "wound_redness": "the wound looks red",
    "wound_discharge": "yellowish fluid coming out of the wound", "wound_opening": "the wound edge has opened a little",
    "calf_pain_or_swelling": "calf pain and swelling", "leg_swelling": "swollen legs",
    "rapid_weight_gain": "gained weight quickly over a few days", "dizziness_or_fainting": "dizzy, nearly fainted",
    "vomiting": "vomiting", "severe_headache": "a very severe headache",
}
OFF_LIST_QUESTION = "Is it okay to drink coffee or tea?"


def make_cards(n: int = 100, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    names = list(TEMPLATES)
    cards = []
    for i in range(n):
        tpl_name = rng.choices(names, weights=[0.4, 0.3, 0.3])[0]
        tpl = load_template(tpl_name)
        lang = "ar" if rng.random() < 0.7 else "en"
        style = "mixed" if lang == "ar" and rng.random() < 0.2 else lang
        meds = [{"name": n_, "critical": c} for n_, c in TEMPLATES[tpl_name][1]]
        crit = [m["name"] for m in meds if m["critical"]]
        days = tpl["checkin_days"]
        red_day = rng.choice(days) if rng.random() < 0.15 else None
        concern_day = rng.choice(days) if rng.random() < 0.25 else None
        if concern_day == red_day:
            concern_day = None
        adherence = "good" if rng.random() < 0.85 else "poor"
        pc = dict(idx=i, name=rng.choice(EXTRA_NAMES_AR if lang == "ar" else EXTRA_NAMES_EN), template=tpl_name,
                  language=lang, style=style, resp=rng.choices(["always", "slow", "never"], [0.6, 0.25, 0.15])[0],
                  adherence=adherence, persona=rng.choice(["terse", "chatty"]),
                  hour=rng.choice([9, 10, 11, 13, 16, 20]), meds=meds, days=[])
        for d in days:
            pain = max(0, min(6, round(5 - d * 0.12 + rng.uniform(-1.5, 1.5)))) if tpl["ask_pain"] else None
            c = dict(day=d, pain=pain, symptoms=[], worsening=False, skipped_med=None, question=None, vague=False)
            if d == red_day:
                c["symptoms"] = [rng.choice(tpl["red_flag_symptoms"])]
            elif d == concern_day:
                kinds = ["symptom"] + (["pain", "worsening"] if tpl["ask_pain"] else []) + (["med"] if crit else [])
                kind = rng.choice(kinds)
                if kind == "pain":
                    c["pain"] = rng.randint(7, 9)
                elif kind == "worsening":
                    c["pain"], c["worsening"] = 5, True
                elif kind == "med":
                    c["skipped_med"] = crit[0]
                else:
                    c["symptoms"] = [rng.choice(tpl["concern_symptoms"])]
            if adherence == "poor" and crit and not c["symptoms"] and rng.random() < 0.3:
                c["skipped_med"] = crit[0]
            if rng.random() < 0.12:
                c["question"] = OFF_LIST_QUESTION if rng.random() < 0.25 else rng.choice(tpl["qa"])["q_en"]
            quiet = not c["symptoms"] and not c["skipped_med"] and not c["worsening"] and (c["pain"] or 0) < 7
            c["vague"] = bool(quiet and not c["question"] and rng.random() < 0.07)
            pc["days"].append(c)
        cards.append(pc)
    return cards


def severity(card: dict, tpl_name: str) -> str:
    """Ground truth under the same demo thresholds the agent uses (needs clinician sign-off)."""
    tpl = load_template(tpl_name)
    syms = set(card["symptoms"])
    if syms & set(tpl["red_flag_symptoms"]):
        return "red_flag"
    if ((card["pain"] or 0) >= rules()["concern"]["pain_score_min"] or syms & set(tpl["concern_symptoms"])
            or card["worsening"] or card["skipped_med"]):
        return "concern"
    return "none"


def gen_reply(pc: dict, card: dict, mode: str = "normal") -> str:
    tpl = load_template(pc["template"])
    asked = "\n".join(tpl["questions"][pc["language"]])
    flag = "CLARIFY" if mode == "clarify" else ("VAGUE" if card["vague"] else "NORMAL")
    truth = {} if flag == "VAGUE" else {
        "pain_score": card["pain"], "symptoms": [SYMPTOM_DESC[x] for x in card["symptoms"]],
        "getting_worse": card["worsening"], "skipped_medicine": card["skipped_med"],
        "question_to_ask": card["question"]}
    user = (f"Reply language: {pc['style']}\nPersona: {pc['persona']}\nMode: {flag}\n"
            f"Questions you were asked:\n{asked}\n\nTruth card:\n{json.dumps(truth, ensure_ascii=False)}")
    with llm.no_logging():
        return llm.complete("patient_sim", llm.SONNET, llm.prompt("patient_sim"), user, NOW, None, 250)


def reply_delay(seed: int, pc: dict, day: int):
    r = random.Random(f"{seed}-{pc['idx']}-{day}")
    if pc["resp"] == "always":
        return timedelta(minutes=r.randint(5, 90))
    if pc["resp"] == "slow":
        return timedelta(minutes=r.randint(7 * 60, 40 * 60))
    return None


def _transient(pc: dict):
    p = Patient(name=pc["name"], phone="", language=pc["language"], preferred_hour=pc["hour"], consent=True)
    ep = Episode(patient_id=0, template=pc["template"], procedure_code="", discharge_date=DISCHARGE,
                 medicines_json=json.dumps(pc["meds"], ensure_ascii=False))
    return p, ep


def prewarm(cards: list[dict], workers: int = 8) -> None:
    """Fill the disk cache in parallel (checkin text, guard, plan summary, patient replies, reader).
    Cache hits are still logged as cost in the main run, using the original token counts."""
    llm.CACHE_DIR = CACHE
    llm.LOGGING = False
    errors = []

    def work(args):
        i, k = args
        pc = cards[i]
        p, ep = _transient(pc)
        tpl = load_template(pc["template"])
        try:
            if k is None:
                planner._summary(ep, p, tpl, NOW)
                return
            card = pc["days"][k]
            text, generated = build_checkin_text(ep, p, CheckIn(day=card["day"], episode_id=0, due_at=NOW), NOW)
            if generated:
                guard.check(text, NOW, None)
            if pc["resp"] == "never":
                return
            crit = critical_names(ep, tpl)
            reply = gen_reply(pc, card)
            read_reply(reply, pc["template"], pc["language"], NOW, None, crit)
            if card["vague"]:
                read_reply(gen_reply(pc, card, "clarify"), pc["template"], pc["language"], NOW, None, crit)
        except Exception as e:  # noqa: BLE001
            errors.append((i, k, repr(e)[:120]))

    jobs = [(i, None) for i in range(len(cards))] + [(i, k) for i, pc in enumerate(cards) for k in range(len(pc["days"]))]
    with ThreadPoolExecutor(workers) as ex:
        for n, _ in enumerate(ex.map(work, jobs), 1):
            if n % 100 == 0:
                print(f"  prewarm {n}/{len(jobs)}", flush=True)
    llm.LOGGING = True
    if errors:
        print(f"  prewarm: {len(errors)} errors (they will be retried in the main run), e.g. {errors[:2]}")


def run_simulation(cards: list[dict], seed: int = 7) -> dict:
    llm.CACHE_DIR = CACHE
    llm.LOGGING = True
    db.DB_PATH = str(SIM_DB)
    db._engine = None
    reset_db()
    with session() as s:
        s.add(ClockState(mode="demo", sim_now=START))
        ep_of = {}
        for pc in cards:
            p = Patient(name=pc["name"], phone=f"+9665000{pc['idx']:05d}", language=pc["language"],
                        preferred_hour=pc["hour"], consent=True)
            s.add(p)
            s.flush()
            ep = Episode(patient_id=p.id, template=pc["template"], procedure_code=TEMPLATES[pc["template"]][0],
                         discharge_date=DISCHARGE, medicines_json=json.dumps(pc["meds"], ensure_ascii=False))
            s.add(ep)
            s.flush()
            ep_of[ep.id] = pc
        s.commit()
        pc_by_ep = ep_of
        card_by = {(pc["idx"], c["day"]): c for pc in cards for c in pc["days"]}

        clock.set_time(s, START)
        tick(s, START)
        pending, scheduled, it = [], set(), 0
        while it < 100000:
            it += 1
            cands = [x for x in [next_due(s)] + [p_["at"] for p_ in pending] if x]
            if not cands:
                break
            t = max(min(cands), clock.now(s))
            if t > END:
                break
            clock.set_time(s, t)
            tick(s, t)
            for ci in s.exec(select(CheckIn).where(CheckIn.status == "sent")).all():
                if ci.id in scheduled:
                    continue
                scheduled.add(ci.id)
                pc = pc_by_ep[ci.episode_id]
                d = reply_delay(seed, pc, ci.day)
                if d is not None:
                    pending.append(dict(at=ci.due_at + d, ci=ci.id, ep=ci.episode_id, mode="normal"))
            due = sorted([x for x in pending if x["at"] <= t], key=lambda x: x["at"])
            pending = [x for x in pending if x["at"] > t]
            for x in due:
                ci = s.get(CheckIn, x["ci"])
                if ci.status != "sent":
                    continue
                pc = pc_by_ep[x["ep"]]
                card = card_by[(pc["idx"], ci.day)]
                res = handle_reply(s, x["ep"], gen_reply(pc, card, x["mode"]))
                if res["route"] == "unclear" and x["mode"] == "normal":
                    pending.append(dict(at=t + timedelta(minutes=20), ci=ci.id, ep=x["ep"], mode="clarify"))
            if it % 200 == 0:
                print(f"  sim t={t:%Y-%m-%d %H:%M} events={it}", flush=True)
        clock.set_time(s, END)
        tick(s, END)
        return collect(s, cards, pc_by_ep)


def _med(x):
    return round(statistics.median(x), 1) if x else None


def collect(s, cards, pc_by_ep) -> dict:
    n = len(cards)
    inbound = {}
    for m in s.exec(select(Message).where(Message.direction == "in")).all():
        inbound.setdefault(m.checkin_id, []).append(m)
    routes = {d.message_id: d.route for d in s.exec(select(Decision)).all()}
    emerg = {}
    for m in s.exec(select(Message).where(Message.kind == "emergency")).all():
        emerg.setdefault(m.checkin_id, m)
    recs = []
    for ci in s.exec(select(CheckIn)).all():
        pc = pc_by_ep[ci.episode_id]
        card = next(c for c in pc["days"] if c["day"] == ci.day)
        msgs = [m for m in inbound.get(ci.id, []) if m.created_at - ci.due_at <= timedelta(hours=48)]
        rts = [routes.get(m.id) for m in msgs]
        em = emerg.get(ci.id)
        recs.append(dict(
            truth=severity(card, pc["template"]), reached48=bool(msgs),
            escalated=any(r in ("concern", "red_flag") for r in rts),
            red_routed=em is not None and em.created_at - ci.due_at <= timedelta(hours=48),
            alert_min=(em.created_at - ci.due_at).total_seconds() / 60 if em is not None else None))
    tasks = s.exec(select(NurseTask)).all()
    mins = rules()["nurse_review_minutes"]

    def tmin(t):
        if t.reason.startswith("Unreachable"):
            return mins["unreachable"]
        return {"urgent": mins["red_flag"], "same_day": mins["concern"], "routine": mins["routine"]}[t.priority]

    calls = s.exec(select(LLMCall)).all()
    by_purpose = {}
    for c in calls:
        b = by_purpose.setdefault(c.purpose, dict(calls=0, usd=0.0))
        b["calls"] += 1
        b["usd"] += llm.cost_usd(c)
    red = [r for r in recs if r["truth"] == "red_flag"]
    con = [r for r in recs if r["truth"] == "concern"]
    calm = [r for r in recs if r["truth"] == "none" and r["reached48"]]
    total_usd = sum(b["usd"] for b in by_purpose.values())
    return {
        "patients": n, "checkins": len(recs),
        "reached_within_48h": sum(r["reached48"] for r in recs) / len(recs),
        "red_flags_total": len(red), "red_flags_caught_48h": sum(r["red_routed"] for r in red),
        "red_flag_median_alert_minutes": _med([r["alert_min"] for r in red if r["red_routed"]]),
        "concerns_total": len(con), "concerns_caught_48h": sum(r["escalated"] for r in con),
        "false_alarms": sum(r["escalated"] for r in calm), "calm_checkins_reached": len(calm),
        "false_alarm_rate": (sum(r["escalated"] for r in calm) / len(calm)) if calm else None,
        "nurse_tasks": len(tasks), "nurse_minutes_per_patient": round(sum(tmin(t) for t in tasks) / n, 2),
        "unreachable_tasks": sum(t.reason.startswith("Unreachable") for t in tasks),
        "llm_cost_per_patient_usd": round(total_usd / n, 4), "llm_calls": len(calls),
        "llm_failed_calls": sum(not c.ok for c in calls),
        "llm_cost_by_purpose": {k: dict(calls=v["calls"], usd=round(v["usd"], 4)) for k, v in by_purpose.items()},
        "episodes_closed": sum(e.status == "closed" for e in s.exec(select(Episode)).all()),
    }
