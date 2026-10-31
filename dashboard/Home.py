import html
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlmodel import select

from common import kpi, setup
from alasalama import clock
from alasalama.db import session
from alasalama.models import Appointment, CheckIn, Decision, Doctor, Episode, Message, NurseTask, Patient

setup("Hello, welcome back", "Here is how your patients are doing today.")

with session() as s:
    t = clock.now(s)
    episodes = s.exec(select(Episode)).all()
    checkins = s.exec(select(CheckIn)).all()
    open_tasks = s.exec(select(NurseTask).where(NurseTask.status == "open")).all()
    all_tasks = s.exec(select(NurseTask)).all()
    booked = s.exec(select(Appointment).where(Appointment.status == "booked")).all()
    appts = s.exec(select(Appointment)).all()
    docs = {d.id: d.name_en for d in s.exec(select(Doctor)).all()}
    decisions = s.exec(select(Decision)).all()
    msgs = s.exec(select(Message)).all()

done = sum(1 for c in checkins if c.status != "scheduled")
urgent = sum(1 for x in open_tasks if x.priority == "urgent")

@st.fragment(run_every=1)
def hero_box():
    now = datetime.now()
    with session() as s2:
        urgent_tasks = s2.exec(select(NurseTask).where(NurseTask.status == "open", NurseTask.priority == "urgent")
                               .order_by(NurseTask.created_at)).all()
        rows = []
        for tk in urgent_tasks:
            ep = s2.get(Episode, tk.episode_id)
            pt = s2.get(Patient, ep.patient_id) if ep else None
            rows.append((pt.name if pt else f"Episode {tk.episode_id}", tk.reason, tk.due_by, tk.episode_id))
    clock_html = (f'<div class="hero-clock"><div class="t">{now:%H:%M:%S}</div>'
                  f'<div class="d">{now:%A, %d %B %Y}</div></div>')
    if rows:
        lines = ""
        for name, reason, due, eid in rows[:3]:
            short = reason if len(reason) <= 90 else reason[:87] + "…"
            lines += (f'<li><a class="case-link" href="/Case?case={eid}" target="_self"><b>{html.escape(name)}</b></a>'
                      f' needs urgent care: {html.escape(short)}'
                      f'<br><span>Respond now: nurse on duty · due by {due:%H:%M}</span></li>')
        more = f'<div class="more">+{len(rows) - 3} more urgent in the Nurse queue</div>' if len(rows) > 3 else ""
        st.markdown(f'<div class="hero alert">{clock_html}<div class="ar">🚨 تنبيه عاجل</div>'
                    f'<h1>Urgent: {len(rows)} patient{"s" if len(rows) != 1 else ""} need{"" if len(rows) != 1 else "s"} a nurse now</h1>'
                    f'<ul class="alert-list">{lines}</ul>{more}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="hero">{clock_html}<div class="ar">متابعتك ما تنتهي عند باب المستشفى</div>'
                    '<h1>Ala Salama</h1><p>No urgent cases right now. The agent is following every discharged patient '
                    'and will alert you here the moment one needs a nurse.</p></div>', unsafe_allow_html=True)


hero_box()

c = st.columns(5)
c[0].markdown(kpi(len(episodes), "Patients followed"), unsafe_allow_html=True)
c[1].markdown(kpi(done, "Check-ins done"), unsafe_allow_html=True)
c[2].markdown(kpi(len(open_tasks), "Need a nurse"), unsafe_allow_html=True)
c[3].markdown(kpi(urgent, "Urgent", alert=urgent > 0), unsafe_allow_html=True)
c[4].markdown(kpi(len(booked), "Appointments booked"), unsafe_allow_html=True)
st.caption(f"Simulated time: {t:%Y-%m-%d %H:%M}")

TEAL, AMBER, RED, GREEN, BLUE = "#0F766E", "#F59E0B", "#DC2626", "#22C55E", "#3B82F6"


def panel(title: str, sub: str):
    st.markdown(f'<div class="row-title" style="margin-top:6px">{title}</div><div class="row-sub" style="margin-bottom:8px">{sub}</div>',
                unsafe_allow_html=True)


st.markdown("#### Activity")
left, right = st.columns(2)
with left:
    panel("Check-ins by day", "Where each scheduled check-in stands")
    if checkins:
        df = pd.DataFrame([{"Day": f"Day {c.day}", "Status": c.status} for c in checkins])
        order = [f"Day {d}" for d in sorted({c.day for c in checkins})]
        pivot = df.groupby(["Day", "Status"]).size().unstack(fill_value=0).reindex(order)
        st.bar_chart(pivot, height=260)
    else:
        st.info("No check-ins yet.")
with right:
    panel("How replies were routed", "The rules engine's decision for every reply")
    if decisions:
        counts = pd.Series([d.route for d in decisions]).value_counts().rename("Replies")
        st.bar_chart(counts, height=260, color=TEAL)
    else:
        st.info("No replies yet.")

left, right = st.columns(2)
with left:
    panel("Messages per day", "Outgoing from the agent and incoming from patients")
    if msgs:
        mdf = pd.DataFrame([{"Date": m.created_at.date(), "Direction": "Agent" if m.direction == "out" else "Patient"} for m in msgs])
        st.bar_chart(mdf.groupby(["Date", "Direction"]).size().unstack(fill_value=0), height=260, color=[TEAL, AMBER][:mdf.Direction.nunique()])
    else:
        st.info("No messages yet.")
with right:
    panel("Nurse tasks", "By priority, open versus resolved")
    if all_tasks:
        tdf = pd.DataFrame([{"Priority": x.priority, "State": "Open" if x.status == "open" else "Resolved"} for x in all_tasks])
        piv = tdf.groupby(["Priority", "State"]).size().unstack(fill_value=0).reindex(["urgent", "same_day", "routine"]).dropna(how="all")
        st.bar_chart(piv, height=260, color=[RED, GREEN][:piv.shape[1]] if list(piv.columns) == ["Open", "Resolved"] else None)
    else:
        st.success("No nurse tasks. The agent is handling everything.")

left, right = st.columns(2)
with left:
    panel("Appointments by doctor", "Booked and offered slots")
    if appts:
        adf = pd.DataFrame([{"Doctor": docs.get(a.doctor_id, "?"), "Status": a.status} for a in appts if a.status in ("booked", "offered")])
        if not adf.empty:
            st.bar_chart(adf.groupby(["Doctor", "Status"]).size().unstack(fill_value=0), height=260, horizontal=True)
        else:
            st.info("No active appointments.")
    else:
        st.info("No appointments yet. They appear when a patient reports a concern.")
with right:
    panel("Pain scores reported", "Distribution of 0 to 10 scores read from replies")
    import json
    from alasalama.models import Extraction
    with session() as s2:
        pains = [json.loads(e.form_json).get("pain_score") for e in s2.exec(select(Extraction)).all()]
    pains = [p for p in pains if p is not None]
    if pains:
        dist = pd.Series(pains).value_counts().reindex(range(0, 11), fill_value=0).rename("Replies")
        dist.index = [str(i) for i in dist.index]
        st.bar_chart(dist, height=260, color=AMBER)
    else:
        st.info("No pain scores yet.")

st.markdown("#### Workspace")
nav = st.columns(3)
with nav[0]:
    st.page_link("pages/1_Nurse_queue.py", label="Nurse queue", icon="🩺")
    st.caption("Exceptions that need a human, urgent first.")
    st.page_link("pages/3_Case.py", label="Case", icon="📋")
    st.caption("Timeline, extracted form, rule decision and brief.")
with nav[1]:
    st.page_link("pages/2_Doctors.py", label="Doctors", icon="👩‍⚕️")
    st.caption("Doctor roster and booked slots.")
    st.page_link("pages/4_Patient_chat.py", label="Patient chat", icon="💬")
    st.caption("Reply as the patient.")
with nav[2]:
    st.caption("Move the simulated clock.")
