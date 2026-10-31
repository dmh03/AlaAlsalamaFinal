# DEMO.md

Synthetic data only. Set `ANTHROPIC_API_KEY`, then `make seed && make dashboard`.
Every page shows the "Demo with synthetic data" banner.

## 0:00 — The problem (20 s)
"After discharge, nurses phone patients from a list, chase people who don't answer, and decide who needs a doctor. It's slow, inconsistent, and many patients are never reached. Ala Salama takes ownership of the 30-day follow-up. Nurses see only exceptions."

## 0:20 — Start the clock (20 s) · *Control panel*
- Click **+1 day** twice (Nov 1 08:00 → Nov 3 08:00). All 30 patients receive their day-1 check-in at their own preferred hour, in their language, and some already get a reminder.
- Point out: planned from the procedure code, scheduled at the patient's preferred hour, shifted out of prayer windows.

## 0:40 — Six patients, six routes (90 s) · *Patient chat*
Pick each patient and reply as them:

| Patient | Type | Expect |
|---|---|---|
| **عبدالله** (knee) | `الجرح محمر شوي وموقف إبرة السيولة من أمس لأنها توجعني` | Concern: missed critical medicine plus wound redness. Same-day task. |
| **Sara** (C-section) | `I feel fine, pain is a 2` | Routine. Thanks and next check-in. No nurse needed. |
| **نورة** (C-section) | `متى أقدر أتروش؟` | Approved answer sent verbatim. |
| **محمد** (heart failure) | `ما أقدر أتنفس إذا انسدحت ورجولي منتفخة` | **Red flag.** Fixed 997 message instantly. Urgent task (15 min SLA). |
| **Khalid** (knee) | `it's ok I guess, بس فيه شي غريب` | Unclear. One clarifying question. |
| **فهد** (heart failure) | *never replies* | Control panel: **+1 day** a few times. Reminder, second reminder, then an unreachable task. |

Say: "Claude reads the reply. A deterministic rules engine decides the route. The red-flag message is fixed text. No LLM writes it."

## 2:10 — The nurse's view (30 s) · *Nurse queue → Case*
- Headline: "N check-ins done, M need you". Urgent first, with SLA countdowns.
- Open محمد's case: timeline, extracted form with evidence quotes, the rule decision and its reasons, Claude's brief.
- Click **Approve suggested action**. The agent sends the nurse's message and closes the task.

## 2:40 — Proof (20 s) · *Results*
- 100 simulated patients: reached within 48 h and red-flag alert time, against a manual-process model.
- **Say it plainly:** the patients are simulated and the manual baseline parameters are still placeholders. The page says so in red.
- Keyword bot vs Claude reader on 52 labelled replies, including Saudi dialect red flags the keyword bot misses.

## Close
"The agent owns the loop, the nurse owns the judgment. Every action is in the audit log."

## If something goes wrong
- No API key: the chat still works, but replies go to a nurse as "could not be read".
- **Reset demo data** in the Control panel restores the start state.
