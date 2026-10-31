import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import streamlit as st
from sqlmodel import select

from common import badge, bubble, setup
from alasalama import nurse
from alasalama.db import session
from alasalama.models import Decision, Episode, Extraction, Message, NurseTask, Patient

setup("Case", "Timeline, what the agent understood, why the rules decided as they did, and the nurse's next step.")

with session() as s:
    ep_id = st.session_state.get("case_episode")
    _q = st.query_params.get("case")
    if _q and str(_q).isdigit():
        ep_id = int(_q)
    rows = s.exec(select(Episode, Patient).where(Episode.patient_id == Patient.id)).all()
    opts = {f"{p.name} ({e.template})": e.id for e, p in rows}
    keys = list(opts)
    idx = list(opts.values()).index(ep_id) if ep_id in opts.values() else 0
    ep_id = opts[st.selectbox("Patient", keys, index=idx)]
    ep = s.get(Episode, ep_id)
    p = s.get(Patient, ep.patient_id)

    left, right = st.columns([3, 2])
    with left:
        st.markdown(f'<div class="card"><div class="row-title">{p.name} &nbsp;{badge(ep.status, "info")}</div>'
                    f'<div class="row-sub">{ep.template} · {p.language}</div></div>', unsafe_allow_html=True)
        _open = s.exec(select(NurseTask).where(NurseTask.episode_id == ep_id, NurseTask.status == "open")).all()
        if _open:
            n = len(_open)
            if st.button(f"✓ Mark resolved ({n} open task{'s' if n != 1 else ''})", type="primary", key="resolve_all"):
                for t_ in _open:
                    nurse.resolve(s, t_.id)
                st.rerun()
        else:
            st.success("No open tasks. This case is resolved.")
        st.caption(ep.plan_summary)
        st.markdown("#### Timeline")
        msgs = s.exec(select(Message).where(Message.episode_id == ep_id).order_by(Message.created_at, Message.id)).all()
        for m in msgs:
            bubble(m.text, "in" if m.direction == "in" else ("out nurse" if m.sender == "nurse" else "out"))
            st.caption(f"{m.created_at:%b %d %H:%M} · {m.kind}")

    with right:
        inbound = [m for m in msgs if m.direction == "in"]
        if inbound:
            last = inbound[-1]
            ex = s.exec(select(Extraction).where(Extraction.message_id == last.id)).first()
            dec = s.exec(select(Decision).where(Decision.message_id == last.id)).first()
            if ex:
                form = json.loads(ex.form_json)
                st.markdown("#### Extracted form")
                st.json({k: v for k, v in form.items() if k != "evidence"})
                st.markdown("**Evidence**")
                for q in form.get("evidence", []):
                    st.markdown(f"> :orange[{q}]")
            if dec:
                st.markdown(f"#### Rule decision: `{dec.route}`")
                for r in json.loads(dec.reasons_json):
                    st.markdown(f"- {r}")
        tasks = s.exec(select(NurseTask).where(NurseTask.episode_id == ep_id).order_by(NurseTask.created_at.desc())).all()
        for task in tasks:
            with st.container(border=True):
                st.markdown(f"**Task #{task.id}** · {task.priority} · {task.status}")
                st.markdown("**Nurse brief**")
                st.write(task.brief)
                if task.status == "open":
                    st.caption(f"Suggested action: {task.suggested_action or '—'}")
                    msg = st.text_area("Message to patient", task.suggested_text, key=f"t{task.id}")
                    c = st.columns(3)
                    if c[0].button("Approve suggested", key=f"a{task.id}"):
                        nurse.approve(s, task.id); st.rerun()
                    if c[1].button("Edit and send", key=f"e{task.id}") and msg.strip():
                        nurse.edit_and_send(s, task.id, msg.strip()); st.rerun()
                    if c[2].button("Mark resolved", key=f"r{task.id}"):
                        nurse.resolve(s, task.id); st.rerun()
        if ep.status == "active" and st.button("Close episode"):
            nurse.close_episode(s, ep_id); st.rerun()
