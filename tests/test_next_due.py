from datetime import datetime

from sqlmodel import select

from alasalama.models import CheckIn, Message
from alasalama.tick import next_due, tick


def test_next_due_works_on_fresh_seed(db):
    with db.session() as s:
        nd = next_due(s)
        assert nd is not None and nd.year == 2026
        assert s.exec(select(Message)).all() == []
        assert len(s.exec(select(CheckIn)).all()) == 150
        assert tick(s, nd)["sent"] > 0
