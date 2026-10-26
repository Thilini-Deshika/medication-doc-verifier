"""Verification of one medication against its retrieved reference passages.

Division of labour:
  - name resolution and the Unsupported decision are deterministic
  - the LLM compares parameters, but must cite a passage and quote it verbatim
  - citations are validated against the passage text; an unverifiable claim is
    downgraded to "not addressed" instead of being trusted
  - the final status is computed from the validated per-parameter results
"""

import json
import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.grounding import score_in
from app.llm.base import LLM
from app.llm.prompts import VERIFY_SYSTEM
from app.llm.schemas import ParameterCheckOut, VerificationOut
from app.verification.resolver import Resolution
from app.verification.retrieval import Passage, Retrieval

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"none": 0, "low": 1, "moderate": 2, "high": 3}


@dataclass
class VerificationResult:
    status: str
    severity: str
    explanation: str
    matched_monograph: str | None
    name_match: str | None
    parameter_checks: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    retrieval: dict = field(default_factory=dict)


def prescription_view(entity) -> dict:
    dose = None
    if entity.dose_value is not None:
        dose = f"{entity.dose_value:g} {entity.dose_unit or ''}".strip()
    view = {
        "drug_name": entity.drug_name,
        "dose": dose,
        "route": entity.route,
        "frequency": entity.frequency,
        "timing": entity.timing,
        "duration": entity.duration,
        "indication": entity.indication,
    }
    return {k: v for k, v in view.items() if v}


def render_passages(passages: list[Passage]) -> str:
    out = []
    for p in passages:
        c = p.chunk
        pages = f"page {c.page_start}" if c.page_start == c.page_end else f"pages {c.page_start}-{c.page_end}"
        out.append(f"[{p.id}] ({pages}; {c.section_path or 'document'})\n{c.text}")
    return "\n\n".join(out)


def retrieval_record(retrieval: Retrieval, resolution: Resolution) -> dict:
    return {
        "resolution": {
            "status": resolution.status,
            "monograph": resolution.monograph,
            "score": resolution.score,
            "suggestions": resolution.suggestions,
        },
        "passages": [
            {
                "id": p.id,
                "chunk_id": str(p.chunk.id),
                "channel": p.channel,
                "score": None if p.score is None else round(p.score, 4),
                "page": p.chunk.page_start,
                "path": p.chunk.section_path,
            }
            for p in retrieval.passages
        ],
    }


def unsupported_no_monograph(entity, retrieval: Retrieval, reference_title: str) -> VerificationResult:
    res = retrieval.resolution
    msg = f"The reference document \"{reference_title}\" has no monograph for {entity.drug_name}, so its prescribing parameters cannot be verified."
    if res.suggestions:
        names = ", ".join(f"{n} ({s:.0f}% similar)" for n, s in res.suggestions)
        msg += f" Closest monograph names: {names}. Near matches are not treated as the same drug."
    mentions = [p for p in retrieval.passages if p.channel == "mention"]
    if mentions:
        msg += " The name appears elsewhere in the reference but not as a monograph."
    return VerificationResult(
        status="unsupported",
        severity="moderate",
        explanation=msg,
        matched_monograph=None,
        name_match="none",
        retrieval=retrieval_record(retrieval, res),
    )


def _validate_check(check: ParameterCheckOut, passages: dict[str, Passage], quote_threshold: float) -> tuple[ParameterCheckOut, str | None]:
    """Return the check, downgraded to not_addressed if its citation does not hold up."""
    if check.result == "not_addressed":
        return check, None
    passage = passages.get(check.passage_id or "")
    if passage is None:
        reason = "cited passage was not among the retrieved passages"
    elif not check.quote or score_in(check.quote, passage.chunk.text) < quote_threshold:
        reason = "quoted text does not appear in the cited passage"
    else:
        return check, None
    downgraded = check.model_copy(
        update={
            "result": "not_addressed",
            "severity": "none",
            "explanation": f"{check.explanation} (Citation could not be validated: {reason}; treated as not addressed.)",
        }
    )
    return downgraded, reason


def verify_entity(
    llm: LLM,
    entity,
    retrieval: Retrieval,
    reference_title: str,
    settings: Settings,
) -> VerificationResult:
    res = retrieval.resolution
    if res.status == "none":
        return unsupported_no_monograph(entity, retrieval, reference_title)

    passages = retrieval.by_id()
    user = (
        "Prescription (from the document being verified):\n"
        + json.dumps(prescription_view(entity), indent=2)
        + f"\n\nFormulary passages for {res.monograph}:\n\n"
        + render_passages(retrieval.passages)
    )
    out = llm.generate(system=VERIFY_SYSTEM, user=user, output=VerificationOut, purpose="verify_entity")

    checks: list[ParameterCheckOut] = []
    dropped = 0
    for c in out.parameter_checks:
        validated, reason = _validate_check(c, passages, settings.quote_match_threshold)
        if reason:
            dropped += 1
            logger.warning(
                "verification citation rejected",
                extra={"fields": {"drug": entity.drug_name, "parameter": c.parameter, "reason": reason}},
            )
        checks.append(validated)

    mismatches = [c for c in checks if c.result == "mismatch"]
    matches = [c for c in checks if c.result == "match"]
    name_note = ""
    if res.status == "fuzzy":
        name_note = f" Matched to monograph {res.monograph} by approximate name ({res.score:.0f}% similar); confirm it is the same drug."

    if mismatches:
        status = "contradicted"
        severity = max((c.severity for c in mismatches), key=lambda s: SEVERITY_ORDER[s])
        if severity == "none":
            severity = "moderate"
    elif matches:
        status, severity = "supported", "none"
    else:
        status, severity = "unsupported", "moderate"

    if status == "unsupported":
        explanation = (
            f"A monograph for {res.monograph} exists, but no prescribing parameter could be compared with a validated citation."
            f"{name_note}"
        )
    else:
        explanation = out.summary.strip() + name_note

    citations: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in sorted(checks, key=lambda c: c.result != "mismatch"):
        if c.result == "not_addressed":
            continue
        p = passages[c.passage_id]
        key = (str(p.chunk.id), c.quote.strip())
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "chunk": p.chunk,
                "role": "contradicting" if c.result == "mismatch" else "supporting",
                "parameter": c.parameter,
                "quote": c.quote.strip(),
            }
        )

    return VerificationResult(
        status=status,
        severity=severity,
        explanation=explanation,
        matched_monograph=res.monograph,
        name_match=res.status,
        parameter_checks=[c.model_dump() for c in checks],
        citations=citations,
        retrieval=retrieval_record(retrieval, res) | {"rejected_citations": dropped},
    )
