from datetime import datetime, timedelta

import yaml
from sqlmodel import Session

from .db import ROOT
from .models import ClockState


def _state(s: Session) -> ClockState:
    st = s.get(ClockState, 1)
    if st is None:
        st = ClockState()
        s.add(st)
        s.commit()
    return st


def now(s: Session) -> datetime:
    st = _state(s)
    return datetime.now().replace(microsecond=0) if st.mode == "live" else st.sim_now


def set_time(s: Session, t: datetime) -> None:
    st = _state(s)
    st.sim_now = t
    s.add(st)
    s.commit()


def advance(s: Session, **delta) -> datetime:
    t = now(s) + timedelta(**delta)
    set_time(s, t)
    return t


def set_mode(s: Session, mode: str) -> None:
    st = _state(s)
    st.mode = mode
    s.add(st)
    s.commit()


def contact_shift(t: datetime, start: int = 8, end: int = 21) -> datetime:
    """Keep contact times inside 08:00-21:00; otherwise move to the next 09:00."""
    if start <= t.hour < end:
        return t
    nxt = t if t.hour < start else t + timedelta(days=1)
    return nxt.replace(hour=start + 1, minute=0, second=0, microsecond=0)


def prayer_shift(t: datetime) -> datetime:
    """Never land inside +-margin of a prayer time; move to the end of the window."""
    cfg = yaml.safe_load((ROOT / "config" / "prayer_windows.yaml").read_text())
    margin = timedelta(minutes=cfg["margin_minutes"])
    moved = True
    while moved:
        moved = False
        for hhmm in cfg["times"].values():
            h, m = map(int, hhmm.split(":"))
            p = t.replace(hour=h, minute=m, second=0, microsecond=0)
            if p - margin <= t < p + margin:
                t = p + margin
                moved = True
    return t
