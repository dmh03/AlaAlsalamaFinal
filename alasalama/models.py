from datetime import datetime
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Patient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone: str
    language: str = "ar"
    preferred_hour: int = 10
    consent: bool = True


class Episode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    template: str
    procedure_code: str
    discharge_date: NaiveDatetime
    medicines_json: str = "[]"
    status: str = "active"
    plan_summary: str = ""
    closing_summary: str = ""


class CheckIn(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    day: int
    due_at: NaiveDatetime
    status: str = "scheduled"
    attempts: int = 0
    last_sent_at: Optional[NaiveDatetime] = None
    clarified: bool = False


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    checkin_id: Optional[int] = None
    direction: str
    sender: str = "agent"
    text: str
    kind: str = "checkin"
    created_at: NaiveDatetime


class Extraction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: int = Field(foreign_key="message.id")
    form_json: str
    created_at: NaiveDatetime


class Decision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: int = Field(foreign_key="message.id")
    route: str
    reasons_json: str
    created_at: NaiveDatetime


class NurseTask(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    priority: str
    reason: str
    brief: str = ""
    suggested_action: str = ""
    suggested_text: str = ""
    status: str = "open"
    created_at: NaiveDatetime
    due_by: NaiveDatetime
    resolved_at: Optional[NaiveDatetime] = None


class Doctor(SQLModel, table=True):
    """Fictional demo roster (config/doctors.yaml). Replace with the hospital's real roster / booking system."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name_en: str
    name_ar: str
    specialty: str
    specialty_en: str
    specialty_ar: str
    clinic_en: str
    clinic_ar: str
    days_json: str = "[]"
    times_json: str = "[]"


class Appointment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    doctor_id: int = Field(foreign_key="doctor.id")
    starts_at: NaiveDatetime
    status: str = "offered"
    option_no: int = 0
    created_at: NaiveDatetime


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    at: NaiveDatetime
    episode_id: Optional[int] = Field(default=None, index=True)
    actor: str
    event: str
    detail_json: str = "{}"


class LLMCall(SQLModel, table=True):
    __tablename__ = "llm_calls"
    id: Optional[int] = Field(default=None, primary_key=True)
    at: NaiveDatetime
    episode_id: Optional[int] = None
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    ok: bool = True


class ClockState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    mode: str = "demo"
    sim_now: NaiveDatetime = datetime(2026, 11, 1, 8, 0)
