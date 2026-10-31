import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import streamlit as st
from sqlmodel import select

from common import badge, kpi, setup
from alasalama import clock
from alasalama.db import session
from alasalama.models import CheckIn, Episode, NurseTask, Patient

setup("Nurse queue", "Only the exceptions. The agent handles everything else.")

with session() as s:
    t = clock.now(s)
    checked = len(s.exec(select(CheckIn).where(CheckIn.status != "scheduled")).all())
    open_tasks = s.exec(select(NurseTask).where(NurseTask.status == "open")).all()
    patients = {e.id: s.get(Patient, e.patient_id) for e in s.exec(select(Episode)).all()}
    order = {"urgent": 0, "same_day": 1, "routine": 2}
    n = {k: sum(1 for x in open_tasks if x.priority == k) for k in order}
    k = st.columns(4)
    k[0].markdown(kpi(checked, "Check-ins done"), unsafe_allow_html=True)
    k[1].markdown(kpi(n["urgent"], "Urgent", alert=n["urgent"] > 0), unsafe_allow_html=True)
    k[2].markdown(kpi(n["same_day"], "Same day"), unsafe_allow_html=True)
    k[3].markdown(kpi(n["routine"], "Routine"), unsafe_allow_html=True)
    st.caption(f"Simulated time: {t:%Y-%m-%d %H:%M}")
    st.write("")
    for task in sorted(open_tasks, key=lambda x: (order[x.priority], x.due_by)):
        left = int((task.due_by - t).total_seconds() // 60)
        sla = badge(f"{left} min left", "info") if left >= 0 else badge(f"overdue {-left} min", "urgent")
        label = {"urgent": "Urgent", "same_day": "Same day", "routine": "Routine"}[task.priority]
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        c1.markdown(
            f'<div class="card accent-{task.priority}"><div class="row-title">{html.escape(patients[task.episode_id].name)} '
            f'&nbsp;{badge(label, task.priority)} {sla}</div>'
            f'<div class="row-sub">{html.escape(task.reason)}</div></div>', unsafe_allow_html=True)
        if c2.button("Open case", key=f"o{task.id}", type="primary"):
            st.session_state["case_episode"] = task.episode_id
            st.session_state["case_task"] = task.id
            st.switch_page("pages/3_Case.py")
    if not open_tasks:
        st.success("No exceptions. The agent is handling everything.")
