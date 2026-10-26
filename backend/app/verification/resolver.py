"""Map a prescribed drug name to a reference monograph.

Matching is deliberately strict. Look-alike drug names are a classic source of
medication errors, so a near miss is never treated as a match: it is reported
as a suggestion and the medication is left unsupported.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.ingest.chunking import monograph_key

_FORM_WORDS = {"tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "oral", "injection"}


def clean_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"\b\d+(\.\d+)?\s*(mg|mcg|g|ml|units?)\b", " ", name, flags=re.I)
    words = [w for w in re.split(r"\s+", name.strip()) if w.lower() not in _FORM_WORDS]
    return " ".join(words)


@dataclass
class Resolution:
    status: str  # exact | fuzzy | none
    key: str | None = None
    monograph: str | None = None
    score: float = 0.0
    suggestions: list[tuple[str, float]] = field(default_factory=list)


def resolve(drug_name: str, monographs: dict[str, str], fuzzy_threshold: float) -> Resolution:
    """`monographs` maps monograph_key to its display name."""
    key = monograph_key(clean_name(drug_name))
    if key in monographs:
        return Resolution("exact", key, monographs[key], 100.0)
    scored = sorted(
        ((display, fuzz.ratio(key, k)) for k, display in monographs.items()),
        key=lambda t: t[1],
        reverse=True,
    )
    if scored and scored[0][1] >= fuzzy_threshold:
        best_key = next(k for k, d in monographs.items() if d == scored[0][0])
        return Resolution("fuzzy", best_key, scored[0][0], scored[0][1])
    return Resolution("none", suggestions=[(d, round(s, 1)) for d, s in scored[:3] if s >= 60])
