"""Rehearse the DEMO.md script against a fresh seed, using the real API. Run: python scripts/rehearse_demo.py"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlmodel import select

from alasalama import clock
from alasalama.db import session
from alasalama.models import Episode, Message, NurseTask, Patient
from alasalama.router import handle_reply
from alasalama.seed import load_seed
from alasalama.tick import next_due, tick

load_seed()
with session() as s:
    nd = datetime(2026, 11, 2, 21, 0)
    clock.set_time(s, nd)
    print("advance to", nd, tick(s, nd))

    def ep(n):
        p = s.exec(select(Patient).where(Patient.name == n)).first()
        return s.exec(select(Episode).where(Episode.patient_id == p.id)).first().id

    replies = [
        ("عبدالله", "الجرح محمر شوي وموقف إبرة السيولة من أمس لأنها توجعني"),
        ("Sara", "I feel fine, pain is a 2"),
        ("نورة", "متى أقدر أتروش؟"),
        ("محمد", "ما أقدر أتنفس إذا انسدحت ورجولي منتفخة"),
        ("Khalid", "it's ok I guess, بس فيه شي غريب"),
    ]
    for n, t in replies:
        print(n, "->", handle_reply(s, ep(n), t)["route"])
    for _ in range(200):
        nd = next_due(s)
        if not nd or nd > datetime(2026, 11, 4):
            break
        clock.set_time(s, max(nd, clock.now(s)))
        tick(s)
    f = ep("فهد")
    print("fahd messages:", [m.kind for m in s.exec(select(Message).where(Message.episode_id == f)).all()])
    print("fahd tasks:", [t.reason[:60] for t in s.exec(select(NurseTask).where(NurseTask.episode_id == f)).all()])
    print("open tasks:", [(t.priority, t.reason[:40]) for t in s.exec(select(NurseTask).where(NurseTask.status == "open")).all()][:8])
