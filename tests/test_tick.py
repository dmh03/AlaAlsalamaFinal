from datetime import datetime


def test_prayer_shift_moves_out_of_window():
    from alasalama.clock import prayer_shift

    assert prayer_shift(datetime(2026, 11, 2, 11, 50)) == datetime(2026, 11, 2, 12, 5)
    assert prayer_shift(datetime(2026, 11, 2, 10, 0)) == datetime(2026, 11, 2, 10, 0)


def test_tick_idempotent(db):
    from alasalama import clock
    from alasalama.models import Message
    from alasalama.tick import tick
    from sqlmodel import select

    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 2, 23, 0))
        first = tick(s)
        n = len(s.exec(select(Message)).all())
        second = tick(s)
        assert first["sent"] == 30 and second["sent"] == 0
        assert len(s.exec(select(Message)).all()) == n


def test_no_future_checkins(db):
    from alasalama import clock
    from alasalama.tick import tick

    with db.session() as s:
        clock.set_time(s, datetime(2026, 11, 1, 8, 0))
        assert tick(s)["sent"] == 0
