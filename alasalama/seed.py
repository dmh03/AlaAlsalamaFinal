"""Load 30 synthetic patients + discharge records. Run: python -m alasalama.seed"""
import json
import random
from datetime import datetime

from .db import ROOT, reset_db, session
from .doctors import load_doctors
from .models import ClockState, Episode, Patient

SEED_FILE = ROOT / "data" / "seed" / "patients.json"

TEMPLATES = {
    "knee_replacement": ("DEMO-KNEE-01", [("Enoxaparin injection", True), ("Paracetamol", False)]),
    "c_section": ("DEMO-CS-01", [("Ibuprofen", False), ("Iron supplement", False)]),
    "heart_failure": ("DEMO-HF-01", [("Furosemide", True), ("Bisoprolol", True)]),
}

DEMO_SIX = [
    ("عبدالله", "ar", "knee_replacement", 10, [("إبرة السيولة", True), ("Paracetamol", False)]),
    ("Sara", "en", "c_section", 11, None),
    ("فهد", "ar", "heart_failure", 9, None),
    ("نورة", "ar", "c_section", 12, None),
    ("محمد", "ar", "heart_failure", 10, None),
    ("Khalid", "ar", "knee_replacement", 16, None),
]
EXTRA_NAMES_AR = ["سلمان", "ريم", "ماجد", "هند", "تركي", "العنود", "سلطان", "لطيفة", "بندر", "منيرة",
                  "ناصر", "الجوهرة", "يوسف", "سارة", "خالد", "مها"]
EXTRA_NAMES_EN = ["Omar", "Lina", "Faisal", "Maha", "Yazan", "Dana", "Rakan", "Hessa"]


def build_patients() -> list[dict]:
    rng = random.Random(7)
    out = []
    for i, (name, lang, tpl, hour, meds) in enumerate(DEMO_SIX):
        out.append(dict(name=name, language=lang, template=tpl, hour=hour, demo=True,
                        meds=meds or TEMPLATES[tpl][1]))
    names = [(n, "ar") for n in EXTRA_NAMES_AR] + [(n, "en") for n in EXTRA_NAMES_EN]
    rng.shuffle(names)
    for name, lang in names[:24]:
        tpl = rng.choice(list(TEMPLATES))
        out.append(dict(name=name, language=lang, template=tpl, hour=rng.choice([9, 10, 11, 13, 16, 20]),
                        demo=False, meds=TEMPLATES[tpl][1]))
    for i, p in enumerate(out):
        p["phone"] = f"+966500000{i:03d}"
        p["consent"] = True
        p["discharge_date"] = "2026-11-01"
    return out


def ensure_seed_file() -> list[dict]:
    if not SEED_FILE.exists():
        SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SEED_FILE.write_text(json.dumps(build_patients(), ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def load_seed(reset: bool = True) -> int:
    rows = ensure_seed_file()
    if reset:
        reset_db()
    with session() as s:
        s.add(ClockState())
        for r in rows:
            p = Patient(name=r["name"], phone=r["phone"], language=r["language"],
                        preferred_hour=r["hour"], consent=r["consent"])
            s.add(p)
            s.flush()
            code, _ = TEMPLATES[r["template"]]
            meds = [{"name": n, "critical": c} for n, c in r["meds"]]
            s.add(Episode(patient_id=p.id, template=r["template"], procedure_code=code,
                          discharge_date=datetime.fromisoformat(r["discharge_date"]),
                          medicines_json=json.dumps(meds, ensure_ascii=False)))
        load_doctors(s)
        s.commit()
    return len(rows)


if __name__ == "__main__":
    print(f"Seeded {load_seed()} synthetic patients.")
