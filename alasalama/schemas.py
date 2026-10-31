from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Symptom(str, Enum):
    fever = "fever"
    wound_redness = "wound_redness"
    wound_discharge = "wound_discharge"
    wound_opening = "wound_opening"
    heavy_bleeding = "heavy_bleeding"
    chest_pain = "chest_pain"
    breathing_difficulty = "breathing_difficulty"
    breathless_lying_down = "breathless_lying_down"
    calf_pain_or_swelling = "calf_pain_or_swelling"
    leg_swelling = "leg_swelling"
    rapid_weight_gain = "rapid_weight_gain"
    dizziness_or_fainting = "dizziness_or_fainting"
    confusion = "confusion"
    vomiting = "vomiting"
    severe_headache = "severe_headache"
    other = "other"


class MedItem(BaseModel):
    name: str
    taken: Optional[bool] = None


class CheckInForm(BaseModel):
    pain_score: Optional[int] = Field(None, ge=0, le=10, description="Current pain 0-10, null if not stated")
    symptoms: list[Symptom] = []
    symptoms_worsening: Optional[bool] = None
    medications: list[MedItem] = []
    questions: list[str] = []
    wants_human: bool = False
    reply_language: str = Field("ar", description="ar, en or mixed")
    suspicious_input: bool = False
    confidence: float = Field(1.0, ge=0, le=1)
    evidence: list[str] = []
    unclear_reason: Optional[str] = None


class AppointmentTriage(BaseModel):
    decision: Literal["book", "ask", "none"]
    reason: str = Field("", max_length=300)


class QuestionMatch(BaseModel):
    question: str
    answer_id: str


class QuestionMatches(BaseModel):
    matches: list[QuestionMatch]
