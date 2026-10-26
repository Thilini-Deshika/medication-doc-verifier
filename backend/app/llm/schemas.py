from typing import Literal

from pydantic import BaseModel


class SummaryOut(BaseModel):
    summary: str


CriticalCategory = Literal[
    "medication_timing",
    "duration_limit",
    "report_immediately",
    "follow_up",
    "monitoring_or_test",
    "avoid_or_restriction",
    "other_warning",
]


class CriticalPointOut(BaseModel):
    text: str
    category: CriticalCategory
    priority: int
    source_quote: str
    related_drug: str | None = None


class CriticalPointsOut(BaseModel):
    points: list[CriticalPointOut]


class MedicationOut(BaseModel):
    drug_name: str
    dose_value: float | None = None
    dose_unit: str | None = None
    route: str | None = None
    frequency: str | None = None
    timing: str | None = None
    duration: str | None = None
    indication: str | None = None
    source_page: int | None = None
    source_quote: str | None = None


class EntitiesOut(BaseModel):
    medications: list[MedicationOut]


Parameter = Literal["dose", "frequency", "route", "timing", "duration", "indication"]


class ParameterCheckOut(BaseModel):
    parameter: Parameter
    prescribed: str | None = None
    reference: str | None = None
    result: Literal["match", "mismatch", "not_addressed"]
    severity: Literal["high", "moderate", "low", "none"]
    passage_id: str | None = None
    quote: str | None = None
    explanation: str


class VerificationOut(BaseModel):
    parameter_checks: list[ParameterCheckOut]
    summary: str
