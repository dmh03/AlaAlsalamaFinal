"""Chase schedule for check-ins that got no reply."""
from datetime import datetime, timedelta

from .clock import contact_shift, prayer_shift
from .models import CheckIn, Patient
from .templates import rules

REMINDER = {
    "ar": "تذكير لطيف: ننتظر ردك على متابعة اليوم {day}. كيف حالك؟",
    "en": "A gentle reminder: we are waiting for your day {day} check-in reply. How are you feeling?",
}
UNREACHABLE_SUGGESTED = {
    "ar": "حاولنا نتواصل معك ولم نوفق. ردّ علينا متى ما تيسر، أو تواصل مع المستشفى.",
    "en": "We tried to reach you. Please reply when you can, or contact the hospital.",
}


def next_step(ci: CheckIn, p: Patient, cfg: dict | None = None) -> tuple[str, datetime]:
    c = (cfg or rules())["chase"]
    last = ci.last_sent_at or ci.due_at
    if ci.attempts < c["max_attempts"]:
        if ci.attempts <= 1:
            at = last + timedelta(hours=c["reply_wait_hours"])
        else:
            at = (last + timedelta(days=1)).replace(
                hour=(p.preferred_hour + c["second_reminder_shift_hours"]) % 24, minute=0, second=0)
        return "reminder", prayer_shift(contact_shift(at))
    return "unreachable", last + timedelta(hours=c["reply_wait_hours"])
