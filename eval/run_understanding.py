"""Part A: understanding accuracy. Scripted baseline vs Claude reader on data/eval/replies.jsonl.

Run: python -m eval.run_understanding   (Claude part needs ANTHROPIC_API_KEY; replies cached on disk)
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from alasalama import llm
from alasalama.db import ROOT
from alasalama.reader import read_reply as claude_read
from alasalama.rules import decide
from alasalama.schemas import CheckInForm
from alasalama.templates import load_template
from eval.baseline_reader import read_reply as keyword_read

REPLIES = ROOT / "data" / "eval" / "replies.jsonl"
OUT = ROOT / "results" / "understanding.json"
NOW = datetime(2026, 11, 2, 10, 0)


def load() -> list[dict]:
    return [json.loads(l) for l in REPLIES.read_text(encoding="utf-8").splitlines() if l.strip()]


def route_for(form: CheckInForm, tpl_name: str) -> str:
    tpl = load_template(tpl_name)
    for m in form.medications:
        if m.name == "medicine" and tpl["critical_medicines"]:
            m.name = tpl["critical_medicines"][0]
    return decide(form, tpl, tpl["critical_medicines"]).route


def score(rows: list[dict], preds: dict[str, tuple[CheckInForm | None, str]]) -> dict:
    red = [r for r in rows if r["expected_route"] == "red_flag"]
    con = [r for r in rows if r["expected_route"] == "concern"]
    calm = [r for r in rows if r["expected_route"] in ("routine", "question")]
    rt = lambda r: preds[r["id"]][1]
    pain = [r for r in rows if preds[r["id"]][0] is not None]
    return {
        "n": len(rows),
        "red_flag_recall": sum(rt(r) == "red_flag" for r in red) / len(red),
        "concern_recall": sum(rt(r) in ("concern", "red_flag") for r in con) / len(con),
        "false_alarm_rate": sum(rt(r) in ("concern", "red_flag") for r in calm) / len(calm),
        "pain_accuracy": sum(preds[r["id"]][0].pain_score == r["expected_pain"] for r in pain) / len(pain),
        "route_accuracy": sum(rt(r) == r["expected_route"] for r in rows) / len(rows),
        "missed_red_flags": [r["id"] for r in red if rt(r) != "red_flag"],
        "reader_failures": [r["id"] for r in rows if preds[r["id"]][0] is None],
    }


def run(use_claude: bool = True) -> dict:
    rows = load()
    llm.CACHE_DIR = ROOT / "data" / "cache" / "eval_reader"
    kw = {r["id"]: (lambda f: (f, route_for(f, r["template"])))(keyword_read(r["text"])) for r in rows}
    result = {"keyword_bot": score(rows, kw), "reviewed": all(r["reviewed"] for r in rows),
              "note": "Labels are unreviewed; a human must check the Arabic and the expected routes."}
    if use_claude and llm.available():
        cl = {}
        for r in rows:
            f = claude_read(r["text"], r["template"], r["language"] if r["language"] != "mixed" else "ar", NOW)
            if f is None:
                cl[r["id"]] = (None, "nurse_fallback")
            else:
                cl[r["id"]] = (f, route_for(f, r["template"]))
        for k, (f, rt) in list(cl.items()):
            if rt == "nurse_fallback":
                cl[k] = (f, "concern")
        result["claude_reader"] = score(rows, {k: v for k, v in cl.items()})
        result["claude_misroutes"] = [
            {"id": r["id"], "text": r["text"], "expected": r["expected_route"], "got": cl[r["id"]][1]}
            for r in rows if cl[r["id"]][1] != r["expected_route"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run("--no-llm" not in sys.argv), ensure_ascii=False, indent=2))
