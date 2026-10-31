import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from alasalama import clock, llm  # noqa: E402,F401
from alasalama.db import init_db, session  # noqa: E402
from alasalama.models import CheckIn, Episode, Patient  # noqa: E402
from alasalama.planner import plan_episode  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Switch to the real clock and schedule the focus patient's check-ins from now.")
    ap.add_argument("--patient", default=os.environ.get("ALASALAMA_ONLY_PATIENTS", "Sara").split(",")[0].strip())
    ap.add_argument("--first", type=int, default=1, help="minutes until the first check-in")
    ap.add_argument("--gap", type=int, default=5, help="minutes between check-ins")
    a = ap.parse_args()
    init_db()
    with session() as s:
        clock.set_mode(s, "live")
        p = s.exec(select(Patient).where(Patient.name == a.patient)).first()
        if p is None:
            raise SystemExit(f"no patient named {a.patient}; run `make seed` first")
        ep = s.exec(select(Episode).where(Episode.patient_id == p.id)).first()
        t = clock.now(s)
        plan_episode(s, ep)
        s.commit()
        cis = s.exec(select(CheckIn).where(CheckIn.episode_id == ep.id).order_by(CheckIn.day)).all()
        for i, c in enumerate(cis):
            c.due_at = t + timedelta(minutes=a.first + i * a.gap)
            c.status, c.attempts, c.last_sent_at, c.clarified = "scheduled", 0, None, False
            s.add(c)
        ep.status = "active"
        s.add(ep)
        s.commit()
        print(f"{a.patient}: {len(cis)} check-ins scheduled, first at {cis[0].due_at:%H:%M}, then every {a.gap} min. Real clock on.")


if __name__ == "__main__":
    main()
