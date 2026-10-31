import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import streamlit as st
from sqlmodel import select

from common import bubble, setup
from alasalama import clock
from alasalama.db import session
from alasalama.models import Episode, Message, Patient
from alasalama.router import handle_reply

setup("Patient chat", "Reply as the patient in Arabic or English. The agent reads, decides and answers exactly as it would on a real channel.")

with session() as s:
    rows = s.exec(select(Episode, Patient).where(Episode.patient_id == Patient.id)).all()
    opts = {f"{p.name} ({e.template}, {p.language})": e.id for e, p in rows}
    label = st.selectbox("Patient", list(opts))
    ep_id = opts[label]
    st.caption(f"Simulated time: {clock.now(s):%Y-%m-%d %H:%M}")

    for m in s.exec(select(Message).where(Message.episode_id == ep_id).order_by(Message.created_at, Message.id)).all():
        bubble(m.text, "in" if m.direction == "in" else ("out nurse" if m.sender == "nurse" else "out"))
        st.caption(f"{m.created_at:%b %d %H:%M} · {m.kind}")

    reply = st.chat_input("Reply as the patient (Arabic or English)")
    if reply:
        with st.spinner("Agent is reading the reply..."):
            handle_reply(s, ep_id, reply)
        st.rerun()
