from .db import init_db, session
from .tick import tick

if __name__ == "__main__":
    init_db()
    with session() as s:
        print(tick(s))
