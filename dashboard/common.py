"""Shared dashboard helpers."""
import html
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alasalama.db import init_db  # noqa: E402
from alasalama.db import session as _session  # noqa: E402

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Sans+Arabic:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, button, input, textarea {
  font-family: 'Inter', 'IBM Plex Sans Arabic', -apple-system, 'Segoe UI', sans-serif;
}
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stToolbarActions"],
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], .stDeployButton {display: none !important;}
header[data-testid="stHeader"] {background: transparent;}
[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapsedControl"] {
  display: flex !important; visibility: visible !important; opacity: 1 !important;
  position: fixed !important; top: 14px; left: 14px; z-index: 1000002 !important;
  background: #29594E !important; border-radius: 10px; padding: 6px 10px; box-shadow: 0 2px 8px rgba(0,0,0,.25);
}
header[data-testid="stHeader"] {z-index: 1000001;}
[data-testid="stExpandSidebarButton"] *, [data-testid="stSidebarCollapsedControl"] * {color: #FFFFFF !important;}
.block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1240px;}

/* sidebar */
[data-testid="stSidebar"] {background: #29594E; border-right: none;}
[data-testid="stSidebar"] * {color: #E4F3EF;}
[data-testid="stSidebarHeader"] {background: #29594E; padding: 0 !important; height: auto !important; min-height: 0 !important; margin: 0;}
[data-testid="stSidebarHeader"] a, [data-testid="stLogoLink"] {display: block; width: 100%;}
[data-testid="stSidebarHeader"] img, [data-testid="stLogo"], [data-testid="stSidebarLogo"] {height: 190px !important; max-height: none !important; width: 100% !important; object-fit: contain; margin: 0 !important;}
[data-testid="stSidebarContent"], [data-testid="stSidebarUserContent"], [data-testid="stSidebarNav"] {background: #29594E !important;}
[data-testid="stSidebarNav"] {border-top: 1px solid rgba(255,255,255,.12); padding-top: 8px;}
[data-testid="stSidebarNav"] a {border-radius: 8px; margin: 2px 10px; padding: 6px 10px;}
[data-testid="stSidebarNav"] a:hover {background: rgba(255,255,255,.08);}
[data-testid="stSidebarNav"] a[aria-current="page"] {background: rgba(255,255,255,.16);}
[data-testid="stSidebarNav"] a[aria-current="page"] span {font-weight: 600; color: #FFFFFF;}

/* page header */
.page-head {margin: 0 0 1.2rem 0;}
.page-head h1 {font-size: 1.85rem; font-weight: 700; letter-spacing: -0.02em; margin: 0; padding: 0; color: #0F172A;}
.page-head p {margin: .25rem 0 0 0; color: #64748B; font-size: .98rem;}
.demo-note {display: inline-flex; gap: 8px; align-items: center; background: #FFF7E6; color: #8A5A00; border: 1px solid #F6DDA8;
  padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 500; margin-bottom: 14px;}

/* cards */
.card {background: #FFFFFF; border: 1px solid #E5E9F0; border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 1px 2px rgba(15,23,42,.04); margin-bottom: 12px;}
.card h4 {margin: 0 0 4px 0; font-size: 1rem; font-weight: 600;}
.card p {margin: 0; color: #64748B; font-size: .92rem; line-height: 1.5;}
.kpi {background: #FFFFFF; border: 1px solid #E5E9F0; border-radius: 14px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,23,42,.04);}
.kpi .v {font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1;}
.kpi .l {color: #64748B; font-size: .82rem; margin-top: 4px; text-transform: uppercase; letter-spacing: .04em;}
.kpi.alert .v {color: #B91C1C;}
.hero {background: linear-gradient(135deg, #0B3B3A 0%, #0F766E 100%); color: #FFFFFF; border-radius: 18px; padding: 34px 36px; margin-bottom: 18px;}
.hero h1 {color: #FFFFFF; font-size: 2.3rem; margin: 0 0 6px 0; font-weight: 700; letter-spacing: -0.02em;}
.hero .ar {font-size: 1.25rem; color: #BDE8E3; margin-bottom: 10px; direction: rtl;}
.hero p {color: #D7EEEC; font-size: 1.02rem; max-width: 720px; margin: 0; line-height: 1.55;}
.hero {position: relative;}
.hero-clock {position: absolute; top: 26px; right: 34px; text-align: right;}
.hero-clock .t {font-size: 2rem; font-weight: 700; color: #FFFFFF; font-variant-numeric: tabular-nums; letter-spacing: .02em;}
.hero-clock .d {font-size: .9rem; color: #BDE8E3;}
.hero.alert {background: linear-gradient(135deg, #7F1D1D 0%, #DC2626 100%); animation: pulse 1.6s ease-in-out infinite;}
.hero.alert .ar, .hero.alert .hero-clock .d {color: #FECACA;}
.alert-list {margin: 10px 0 0 0; padding-left: 20px; color: #FFFFFF; font-size: 1.05rem; line-height: 1.5; max-width: 760px;}
.alert-list li {margin-bottom: 8px;}
.alert-list a.case-link, .alert-list a.case-link:visited {color: #FFFFFF !important; text-decoration: underline !important; text-underline-offset: 3px;}
.alert-list a.case-link:hover {background: rgba(255,255,255,.2); border-radius: 4px;}
.alert-list span {color: #FECACA; font-size: .92rem;}
.hero .more {margin-top: 6px; color: #FECACA; font-size: .9rem;}
@keyframes pulse {0%,100% {box-shadow: 0 0 0 0 rgba(220,38,38,.55);} 50% {box-shadow: 0 0 0 14px rgba(220,38,38,0);}}
.step {display: flex; gap: 14px; align-items: flex-start;}
.step .n {flex: 0 0 32px; height: 32px; border-radius: 50%; background: #E6F4F2; color: #0F766E; font-weight: 700;
  display: flex; align-items: center; justify-content: center;}

/* clickable cards */
a.doc-link, a.doc-link:hover {text-decoration: none; color: inherit; display: block;}
.doc-card {cursor: pointer; transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;}
a.doc-link:hover .doc-card {transform: translateY(-6px); box-shadow: 0 12px 24px rgba(15,23,42,.14); border-color: #0F766E;}

/* badges */
.badge {display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap;}
.b-urgent {background: #FEE2E2; color: #B91C1C;}
.b-same_day {background: #FFEDD5; color: #C2410C;}
.b-routine {background: #DCFCE7; color: #15803D;}
.b-info {background: #E0F2FE; color: #0369A1;}
.b-booked {background: #DCFCE7; color: #15803D;}
.b-offered {background: #FEF3C7; color: #A16207;}
.b-muted {background: #F1F5F9; color: #475569;}
.row-title {font-weight: 600; font-size: 1rem;}
.row-sub {color: #64748B; font-size: .9rem; margin-top: 2px;}
.accent-urgent {border-left: 4px solid #DC2626;}
.accent-same_day {border-left: 4px solid #F97316;}
.accent-routine {border-left: 4px solid #22C55E;}

/* chat */
.rtl {direction: rtl; text-align: right;}
.bubble {padding: 10px 14px; border-radius: 14px; margin: 6px 0 2px 0; max-width: 78%; white-space: pre-wrap; line-height: 1.5; font-size: .95rem;}
.out {background: #E6F4F2; color: #0B3B3A; border-bottom-left-radius: 4px; margin-right: auto;}
.in {background: #FFFFFF; border: 1px solid #E2E8F0; border-bottom-right-radius: 4px; margin-left: auto;}
.nurse {background: #DBEAFE; color: #1E3A8A;}

/* native widgets */
.stButton > button {border-radius: 10px; font-weight: 600; border: 1px solid #CBD5E1; padding: .45rem 1rem; transition: all .15s;}
.stButton > button:hover {border-color: #0F766E; color: #0F766E;}
.stButton > button[kind="primary"] {background: #0F766E; border-color: #0F766E; color: #FFFFFF;}
.stButton > button[kind="primary"]:hover {background: #0B5C56; color: #FFFFFF;}
[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E5E9F0; border-radius: 14px; padding: 14px 18px;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
h2, h3 {letter-spacing: -0.01em; font-weight: 600;}
</style>
"""


def _signature() -> str:
    """Cheap fingerprint of what the pages show; it changes when a message, task, booking or check-in changes."""
    from sqlalchemy import text
    parts = []
    with _session() as s:
        for tbl, expr in [("message", "count(*)||max(id)"), ("nursetask", "count(*)||max(id)||group_concat(status)"),
                          ("appointment", "count(*)||max(id)||group_concat(status)"),
                          ("checkin", "count(*)||group_concat(status)"), ("episode", "group_concat(status)")]:
            try:
                parts.append(str(s.exec(text(f"select {expr} from {tbl}")).first()))
            except Exception:  # noqa: BLE001
                parts.append("?")
    return "|".join(parts)


@st.fragment(run_every=3)
def _auto_refresh():
    """Reload the whole page when the data changed, so nobody has to press refresh."""
    sig = _signature()
    if st.session_state.get("_sig", sig) != sig:
        st.session_state["_sig"] = sig
        st.rerun()
    st.session_state["_sig"] = sig


def setup(title: str, subtitle: str = ""):
    st.set_page_config(page_title=f"Ala Salama · {title}", page_icon="🩺", layout="wide")
    init_db()
    _auto_refresh()
    st.markdown(CSS, unsafe_allow_html=True)
    logo = Path(__file__).parent / "assets" / "logo.png"
    if logo.exists():
        st.logo(str(logo), size="large")
    st.markdown('<span class="demo-note">Demo with synthetic data · not a medical device · clinical content needs clinician review</span>',
                unsafe_allow_html=True)
    sub = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="page-head"><h1>{html.escape(title)}</h1>{sub}</div>', unsafe_allow_html=True)


def is_ar(text: str) -> bool:
    return any("\u0600" <= c <= "\u06ff" for c in text)


def bubble(text: str, cls: str):
    rtl = " rtl" if is_ar(text) else ""
    st.markdown(f'<div class="bubble {cls}{rtl}">{html.escape(text)}</div>', unsafe_allow_html=True)


def badge(text: str, kind: str = "muted") -> str:
    return f'<span class="badge b-{kind}">{html.escape(text)}</span>'


def kpi(value, label: str, alert: bool = False) -> str:
    return f'<div class="kpi{" alert" if alert else ""}"><div class="v">{value}</div><div class="l">{html.escape(label)}</div></div>'
