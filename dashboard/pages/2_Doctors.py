import html
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import streamlit as st
from sqlmodel import select

from common import badge, setup
from alasalama import clock, doctors
from alasalama.db import session
from alasalama.models import Appointment, Doctor, Episode, Patient

setup("Doctors", "Demo roster of fictional doctors (config/doctors.yaml) and their appointments. Offered options are not held; only booked slots are taken.")

DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
COLORS = {"obgyn": "#BE185D", "orthopedics": "#B45309", "cardiology": "#B91C1C"}


def initials(name: str) -> str:
    parts = [w for w in name.replace("Dr.", "").replace("Dr", "").split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "DR"


def avatar(d, size=52) -> str:
    c = COLORS.get(d.specialty, "#0F766E")
    return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;background:{c}1A;color:{c};font-weight:700;'
            f'display:flex;align-items:center;justify-content:center;font-size:{size // 3}px;flex:0 0 {size}px">{initials(d.name_en)}</div>')


with session() as s:
    doctors.ensure_doctors(s)
    s.commit()
    docs = {d.id: d for d in s.exec(select(Doctor)).all()}
    names = {e.id: s.get(Patient, e.patient_id).name for e in s.exec(select(Episode)).all()}
    appts = s.exec(select(Appointment).order_by(Appointment.starts_at)).all()
    now_dt = clock.now(s)

active = [a for a in appts if a.status in ("booked", "offered")]
booked_by = {}
for a in active:
    booked_by.setdefault(a.doctor_id, []).append(a)

_q = st.query_params.get("doctor")
sel = int(_q) if _q and str(_q).isdigit() else None

if sel in docs:
    d = docs[sel]
    if st.button("← All doctors"):
        st.query_params.clear()
        st.rerun()
    days = json.loads(d.days_json)
    times = doctors.slot_times(d)
    st.markdown(
        f'<div class="card"><div class="step">{avatar(d, 72)}<div>'
        f'<div class="row-title" style="font-size:1.3rem">{html.escape(d.name_en)}</div>'
        f'<div class="row-sub" dir="rtl" style="text-align:left">{html.escape(d.name_ar)}</div>'
        f'<div style="margin-top:8px">{badge(d.specialty_en, "info")} {badge(d.clinic_en, "muted")}</div></div></div></div>',
        unsafe_allow_html=True)
    left, right = st.columns(2)
    with left:
        st.markdown("#### Weekly availability")
        chips = " ".join(
            f'<span class="badge {"b-booked" if i in days else "b-muted"}" style="margin:2px">{n}</span>' for i, n in enumerate(DAY))
        st.markdown(chips, unsafe_allow_html=True)
        st.markdown("&nbsp;")
        st.markdown(f"**Hours:** {doctors.hours_label(d)}")
        work_days = [now_dt.date() + timedelta(days=i) for i in range(14) if (now_dt + timedelta(days=i)).weekday() in days][:7]
        day_pick = st.selectbox("Day", work_days, format_func=lambda x: f"{x:%a %d %b %Y}")
        taken = {a.starts_at for a in booked_by.get(d.id, []) if a.status == "booked"}
        cells = []
        for hhmm in times:
            h, m = (int(x) for x in hhmm.split(":"))
            when = datetime(day_pick.year, day_pick.month, day_pick.day, h, m)
            cells.append(badge(hhmm, "muted" if when in taken else "booked"))
        st.markdown(" ".join(f'<span style="display:inline-block;margin:2px">{c}</span>' for c in cells), unsafe_allow_html=True)
        st.caption("Green slots are open. A slot stays open until a patient books it.")
        st.caption(f"Clinic (Arabic): {d.clinic_ar} · Specialty (Arabic): {d.specialty_ar}")
    with right:
        st.markdown("#### Appointments")
        mine = booked_by.get(d.id, [])
        if not mine:
            st.info("No booked or offered appointments for this doctor yet.")
        for a in mine:
            extra = f" · option {a.option_no}" if a.status == "offered" else ""
            st.markdown(
                f'<div class="card"><div class="row-title">{html.escape(names.get(a.episode_id, "?"))} &nbsp;{badge(a.status, a.status)}</div>'
                f'<div class="row-sub">{a.starts_at:%a %Y-%m-%d %H:%M}{extra}</div></div>', unsafe_allow_html=True)
else:
    specs = sorted({d.specialty_en for d in docs.values()})
    pick = st.multiselect("Specialty", specs, placeholder="All specialties")
    shown = [d for d in docs.values() if not pick or d.specialty_en in pick]
    st.caption(f"{len(shown)} doctors · {sum(1 for a in active if a.status == 'booked')} booked appointments")

    CLAMP = "word-break:break-word;"
    st.markdown("""<style>
.doc-wrap a.doc-link, .doc-wrap a.doc-link:visited, .doc-wrap a.doc-link:hover {text-decoration: none !important; color: inherit !important; display: block;}
.doc-wrap .doc-card {cursor: pointer; transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;}
.doc-wrap a.doc-link:hover .doc-card {transform: translateY(-8px); box-shadow: 0 14px 28px rgba(15,23,42,.16); border-color: #0F766E;}
.doc-wrap a.doc-link * {text-decoration: none !important;}
</style>""", unsafe_allow_html=True)
    for start in range(0, len(shown), 3):
        cols = st.columns(3)
        for col, d in zip(cols, shown[start:start + 3]):
            n_booked = sum(1 for a in booked_by.get(d.id, []) if a.status == "booked")
            n_offered = sum(1 for a in booked_by.get(d.id, []) if a.status == "offered")
            days = json.loads(d.days_json)
            with col:
                st.markdown(
                    f'<div class="doc-wrap"><a class="doc-link" href="?doctor={d.id}" target="_self">'
                    f'<div class="card doc-card" style="height:300px;box-sizing:border-box;display:flex;flex-direction:column">'
                    f'<div class="step" style="align-items:center">{avatar(d)}<div style="min-width:0;flex:1">'
                    f'<div class="row-title" style="{CLAMP}">{html.escape(d.name_en)}</div>'
                    f'<div class="row-sub" style="{CLAMP}">{html.escape(d.specialty_en)}</div></div></div>'
                    f'<div class="row-sub" style="margin-top:14px;{CLAMP}">{html.escape(d.clinic_en)}</div>'
                    f'<div class="row-sub" style="{CLAMP}">{", ".join(DAY[x] for x in days)}</div>'
                    f'<div class="row-sub" style="{CLAMP}">{doctors.hours_label(d).split(" · ")[0]}</div>'
                    f'<div style="margin-top:auto;padding-top:12px;display:flex;flex-wrap:wrap;gap:6px">'
                    f'{badge(f"{n_booked} booked", "booked" if n_booked else "muted")}'
                    f'{badge(f"{n_offered} offered", "offered" if n_offered else "muted")}</div></div></a></div>',
                    unsafe_allow_html=True)
