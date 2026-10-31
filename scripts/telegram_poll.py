"""Telegram long-polling listener: reads patient replies and runs them through the agent. No tunnel or webhook needed.

    python scripts/telegram_poll.py                  # listen for replies AND send scheduled check-ins/reminders (leave running)
    python scripts/telegram_poll.py --no-scheduler   # replies only; check-ins go out only when something else runs the tick
    python scripts/telegram_poll.py --skip-backlog   # ignore anything sent while the listener was off
    python scripts/telegram_poll.py --find           # print chat ids of people who messaged the bot (to fill ALASALAMA_TELEGRAM_MAP)

The position in Telegram's message queue is saved to data/cache/telegram_offset.txt after every handled message, so
restarting the listener never replays (and re-answers) messages it has already handled.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alasalama import llm  # noqa: F401,E402  (loads .env)
from alasalama.telegram_channel import api, handle_update, load_offset, save_offset, telegram_map  # noqa: E402


def say(*a) -> None:
    print(*a, flush=True)


TICK_EVERY = 20


def run_scheduler() -> None:
    """Send whatever is due: check-ins, reminders, unreachable escalations. Same tick the rest of the system uses."""
    from alasalama.db import session
    from alasalama.tick import tick
    with session() as s:
        stats = tick(s)
    if stats.get("sent") or stats.get("reminders") or stats.get("unreachable") or stats.get("closed"):
        say(f"scheduler -> {stats}")


def find() -> None:
    seen = {}
    for u in api("getUpdates", offset=-50):
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("id") is not None:
            seen[c["id"]] = f"{c.get('first_name', '')} {c.get('last_name', '')} @{c.get('username', '')}".strip()
    if not seen:
        say("No messages yet. Open your bot in Telegram, press Start, send 'hi', then run this again.")
    for cid, who in seen.items():
        say(f"chat id {cid}   {who}")
        say(f"  -> put this in .env:  ALASALAMA_TELEGRAM_MAP=Sara={cid}")


def listen(skip_backlog: bool = False, scheduler: bool = True) -> None:
    me = api("getMe")
    say(f"Listening as @{me['username']}. Mapped patients: {sorted(telegram_map()) or 'none (set ALASALAMA_TELEGRAM_MAP)'}")
    api("deleteWebhook")
    offset = load_offset()
    if skip_backlog:
        pending = api("getUpdates", offset=-1)
        if pending:
            offset = pending[-1]["update_id"] + 1
            save_offset(offset)
        say("Skipped any messages sent while the listener was off.")
    say(f"Resuming from update {offset}." if offset is not None else "No saved position: handling everything waiting.")
    say("Scheduler on: due check-ins and reminders are sent automatically." if scheduler else "Scheduler off.")
    last_tick = 0.0
    while True:
        try:
            if scheduler and time.monotonic() - last_tick >= TICK_EVERY:
                last_tick = time.monotonic()
                try:
                    run_scheduler()
                except Exception as e:  # noqa: BLE001  (a failed tick must not stop reply handling)
                    say("scheduler error:", repr(e)[:200])
            params = {"timeout": 10, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            for u in api("getUpdates", http_timeout=40, **params):
                try:
                    res = handle_update(u)
                    if res is not None:
                        say(f"reply handled -> route={res.get('route')}")
                except Exception as e:  # noqa: BLE001  (one bad message must not stop the listener)
                    say("handler error:", repr(e)[:200])
                offset = u["update_id"] + 1
                save_offset(offset)
        except KeyboardInterrupt:
            return
        except Exception as e:  # noqa: BLE001  (network blips: wait and retry)
            say("poll error:", repr(e)[:200])
            time.sleep(3)


if __name__ == "__main__":
    find() if "--find" in sys.argv else listen(skip_backlog="--skip-backlog" in sys.argv,
                                               scheduler="--no-scheduler" not in sys.argv)
