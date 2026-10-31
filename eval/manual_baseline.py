"""Manual follow-up process model (nurses phone patients from a list). Parameters: config/baseline.yaml.
Every parameter currently carries an ASSUMPTION marker: replace with published figures before presenting as fact."""
import random
import statistics
from datetime import timedelta

import yaml

from alasalama.db import ROOT
from eval.simulate import DISCHARGE, severity

CFG = ROOT / "config" / "baseline.yaml"


def load_cfg() -> dict:
    return yaml.safe_load(CFG.read_text(encoding="utf-8"))


def run(cards: list[dict], seed: int = 7, cfg: dict | None = None) -> dict:
    cfg = cfg or load_cfg()
    rng = random.Random(f"{seed}-manual")
    nurses = cfg["nurses_available"]["value"]
    cap = nurses * cfg["calls_per_nurse_per_day"]["value"]
    mins = cfg["minutes_per_call"]["value"]
    reach = cfg["reach_rate_per_attempt"]["value"]
    max_att = cfg["max_call_attempts"]["value"]

    reqs = []
    for pc in cards:
        for card in pc["days"]:
            due = DISCHARGE + timedelta(days=card["day"], hours=pc["hour"])
            reqs.append(dict(pc=pc, truth=severity(card, pc["template"]), due=due, ready=card["day"],
                             attempts=0, reached_at=None, order=len(reqs)))
    calls = 0
    for day in range(1, 61):
        todo = sorted([r for r in reqs if r["reached_at"] is None and r["attempts"] < max_att and r["ready"] <= day],
                      key=lambda r: (r["ready"], r["order"]))[:cap]
        for k, r in enumerate(todo):
            at = max(DISCHARGE + timedelta(days=day, hours=9, minutes=(k // nurses) * mins), r["due"])
            r["attempts"] += 1
            calls += 1
            if r["pc"]["resp"] != "never" and rng.random() < reach:
                r["reached_at"] = at
            else:
                r["ready"] = day + 1

    def within48(r):
        return r["reached_at"] is not None and r["reached_at"] - r["due"] <= timedelta(hours=48)

    red = [r for r in reqs if r["truth"] == "red_flag"]
    con = [r for r in reqs if r["truth"] == "concern"]
    caught = [r for r in red if within48(r)]
    alerts = [(r["reached_at"] - r["due"]).total_seconds() / 60 for r in caught]
    return {
        "patients": len(cards), "checkins": len(reqs),
        "reached_within_48h": sum(within48(r) for r in reqs) / len(reqs),
        "red_flags_total": len(red), "red_flags_caught_48h": len(caught),
        "red_flag_median_alert_minutes": round(statistics.median(alerts), 1) if alerts else None,
        "concerns_total": len(con), "concerns_caught_48h": sum(within48(r) for r in con),
        "false_alarms": None, "false_alarm_rate": None,
        "calls_made": calls, "nurse_minutes_per_patient": round(calls * mins / len(cards), 2),
        "llm_cost_per_patient_usd": 0.0,
    }
