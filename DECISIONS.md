# Decisions and assumptions

1. Procedure codes are invented demo codes (e.g. `DEMO-KNEE-01`), not real coding-system codes.
2. Seed `patients.json` is generated deterministically (seed 7) by `alasalama/seed.py` if missing; the six demo patients are fixed.
3. Names are fictional first names only; phone numbers are fake `+966500000xxx`.
4. SQLite in WAL mode; SQLModel for models; datetimes stored naive, as Riyadh local time.
5. Model IDs `claude-haiku-5-5` and `claude-sonnet-5-5` are taken from the brief and have been verified working against the live API.
6. All thresholds and clinical content are demo values — needs clinician sign-off.
7. The reader receives the patient's medicine list so descriptions like "blood thinner injection" map to the listed critical medicine name; otherwise the missed-critical-medicine rule cannot fire.
8. A reply is matched to the latest check-in in status `sent`; replies are handled one at a time.
9. A patient replying after a check-in was marked unreachable does not reopen it (visible in the simulation as late slow responders).
10. Contact times are kept between 08:00 and 21:00 and out of the +-20 minute prayer windows (approximate Riyadh times, not authoritative).
11. Check-in messages are written by Sonnet; if the guard blocks one, the approved template questions are sent instead.
12. Fixed template text (reminders, thanks, approved answers) gets the deterministic guard only; LLM-written text also goes through the Haiku classifier, which fails closed.
13. Simulation: patients are role-played by Sonnet from truth cards and are never counted as system cost. Replies are cached on disk (`data/cache/`) so reruns are free and identical.
14. Ground-truth severity in the simulation uses the same demo thresholds as the rules engine, so it measures whether the agent reads and routes correctly, not whether the thresholds are clinically right.
15. Manual baseline: all parameters are placeholders until a published figure is filled in; manual false alarms are not modelled; a reached patient is assumed to be judged correctly.
16. Nurse review minutes for Ala Salama (`config/rules.yaml`) are demo values.
17. The labelled reply set (52 replies, `reviewed: false`) was written together with the reader prompt, so reader accuracy on it is probably optimistic; a human must review the Arabic and the expected routes.
18. Telegram is the live external channel (`ALASALAMA_CHANNEL=telegram`). Only patients named in `ALASALAMA_TELEGRAM_MAP` are messaged externally, so synthetic seed patients never reach a real chat. Messages are always also stored in the DB. A delivery failure is audited and never crashes `tick` or the router.
