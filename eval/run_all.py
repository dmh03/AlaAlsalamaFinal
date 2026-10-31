"""make eval: understanding accuracy + 100-patient process simulation -> results/summary.json

Usage: python -m eval.run_all [--n 100] [--seed 7] [--no-prewarm]
"""
import argparse
import json
from datetime import datetime

import yaml

from alasalama import llm
from alasalama.db import ROOT
from eval import manual_baseline, run_understanding, simulate

SUMMARY = ROOT / "results" / "summary.json"


def assumptions() -> list[dict]:
    out = []
    for k, v in manual_baseline.load_cfg().items():
        out.append(dict(name=f"manual: {k}", value=v["value"], source=v["source"],
                        placeholder="ASSUMPTION" in v["source"]))
    rules = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())
    for k, v in rules["nurse_review_minutes"].items():
        out.append(dict(name=f"Ala Salama nurse review minutes: {k}", value=v,
                        source="demo value, needs clinician/operations sign-off", placeholder=True))
    for text in [
        "Patients are simulated by Claude Sonnet from hidden truth cards; they are not real people.",
        "Ground-truth severity uses the same demo thresholds as the rules engine (needs clinician sign-off).",
        "In the manual model, a reached patient is always judged correctly by the nurse (optimistic for manual).",
        "Manual false alarms are not modelled.",
        "Manual: patients marked 'never responsive' are never reached; others are reached with the configured rate.",
        "Time to alert is measured from the check-in due time for both processes.",
    ]:
        out.append(dict(name="modelling assumption", value=None, source=text, placeholder=False))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-prewarm", action="store_true")
    a = ap.parse_args()
    if not llm.available():
        raise SystemExit("ANTHROPIC_API_KEY is required for make eval")

    print("A. understanding accuracy ...", flush=True)
    understanding = run_understanding.run(True)
    cards = simulate.make_cards(a.n, a.seed)
    if not a.no_prewarm:
        print(f"B. pre-warming caches for {a.n} patients ...", flush=True)
        simulate.prewarm(cards)
    print("B. running Ala Salama on the simulated clock ...", flush=True)
    ala = simulate.run_simulation(cards, a.seed)
    print("B. running manual baseline model ...", flush=True)
    manual = manual_baseline.run(cards, a.seed)
    summary = dict(generated_at=datetime.now().isoformat(timespec="seconds"), patients=a.n, seed=a.seed,
                   synthetic_data=True, ala_salama=ala, manual_baseline=manual, understanding=understanding,
                   assumptions=assumptions(),
                   has_placeholder_assumptions=any(x["placeholder"] and "ASSUMPTION" in str(x["source"]) for x in assumptions()))
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ala_salama", "manual_baseline")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
