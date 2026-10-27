import pytest

from app.config import get_settings
from app.ingest.embeddings import get_embedder
from app.llm.schemas import ParameterCheckOut, VerificationOut
from app.verification.resolver import resolve
from app.verification.retrieval import monograph_index, retrieve
from app.verification.verifier import verify_entity
from tests.fakes import ScriptedLLM


class Rx:
    def __init__(self, name, dose=None, unit="mg", route="PO", frequency=None, timing=None, duration=None, indication=None):
        self.drug_name, self.dose_value, self.dose_unit = name, dose, unit
        self.route, self.frequency, self.timing, self.duration, self.indication = route, frequency, timing, duration, indication


def run(db, reference, rx, llm):
    settings = get_settings()
    index = monograph_index(db, reference.id)
    res = resolve(rx.drug_name, index, settings.fuzzy_name_threshold)
    retrieval = retrieve(db, reference.id, rx, res, get_embedder(), settings)
    return verify_entity(llm, rx, retrieval, reference.title, settings), retrieval


def passage_with(retrieval, text):
    return next(p for p in retrieval.passages if text in p.chunk.text)


def scripted(retrieval_getter):
    """LLM whose answer is built from the passages it is shown."""

    def build(user):
        return None

    return build


def test_wrong_timing_is_contradicted_with_a_citation(db, reference):
    rx = Rx("Zorvamide", 20, frequency="every morning")
    retrieval = None

    def answer(user):
        # locate the passage id in the prompt text like a real model would
        line = next(l for l in user.split("\n\n") if "Standard adult dose" in l)
        pid = line.split("]")[0].lstrip("[")
        return VerificationOut(
            parameter_checks=[
                ParameterCheckOut(parameter="dose", prescribed="20 mg", reference="20 mg", result="match", severity="none", passage_id=pid, quote="20 mg orally once daily", explanation="same dose"),
                ParameterCheckOut(parameter="timing", prescribed="every morning", reference="at bedtime", result="mismatch", severity="high", passage_id=pid, quote="at bedtime", explanation="given in the morning instead of at bedtime"),
            ],
            summary="Timing conflicts with the formulary.",
        )

    from app.llm.schemas import VerificationOut as VO

    result, retrieval = run(db, reference, rx, ScriptedLLM({VO: answer}))
    assert result.status == "contradicted"
    assert result.severity == "high"
    contradicting = [c for c in result.citations if c["role"] == "contradicting"]
    assert contradicting and contradicting[0]["chunk"].field_label == "Standard adult dose"
    assert contradicting[0]["chunk"].page_start == 1
    assert contradicting[0]["chunk"].section_path == "I. Test Agents > Zorvamide > Standard adult dose"


def test_all_parameters_matching_is_supported(db, reference):
    rx = Rx("Calmitol", 5, frequency="twice daily")

    def answer(user):
        line = next(l for l in user.split("\n\n") if "Standard adult dose" in l)
        pid = line.split("]")[0].lstrip("[")
        return VerificationOut(
            parameter_checks=[
                ParameterCheckOut(parameter="dose", prescribed="5 mg", reference="5 mg", result="match", severity="none", passage_id=pid, quote="5 mg orally twice daily", explanation="same"),
            ],
            summary="Consistent.",
        )

    result, _ = run(db, reference, rx, ScriptedLLM({VerificationOut: answer}))
    assert result.status == "supported"
    assert result.citations[0]["role"] == "supporting"


def test_drug_without_monograph_is_unsupported_and_llm_is_not_called(db, reference):
    llm = ScriptedLLM({})
    result, retrieval = run(db, reference, Rx("Xenoflex", 10, frequency="once daily"), llm)
    assert result.status == "unsupported"
    assert result.citations == []
    assert "no monograph for Xenoflex" in result.explanation
    assert llm.calls == []


def test_lookalike_name_is_not_resolved_to_another_monograph(db, reference):
    llm = ScriptedLLM({})
    result, _ = run(db, reference, Rx("Zorvamine", 20), llm)  # one letter off Zorvamide
    assert result.status == "unsupported"
    assert llm.calls == []
    assert "Zorvamide" in result.explanation  # offered as a suggestion only


def test_fabricated_quote_is_rejected_and_never_produces_a_verdict(db, reference):
    rx = Rx("Zorvamide", 20, frequency="every morning")

    def answer(user):
        pid = next(l for l in user.split("\n\n") if "Standard adult dose" in l).split("]")[0].lstrip("[")
        return VerificationOut(
            parameter_checks=[
                ParameterCheckOut(parameter="dose", prescribed="20 mg", reference="10 mg", result="mismatch", severity="high", passage_id=pid, quote="the dose is ten milligrams in all cases", explanation="invented"),
            ],
            summary="Wrong dose.",
        )

    result, _ = run(db, reference, rx, ScriptedLLM({VerificationOut: answer}))
    assert result.status != "contradicted"
    assert result.status == "unsupported"
    assert result.citations == []
    assert result.retrieval["rejected_citations"] == 1


def test_citing_a_passage_that_was_not_retrieved_is_rejected(db, reference):
    rx = Rx("Calmitol", 5)
    answer = VerificationOut(
        parameter_checks=[
            ParameterCheckOut(parameter="dose", prescribed="5 mg", reference="5 mg", result="match", severity="none", passage_id="P99", quote="5 mg orally twice daily", explanation="x"),
        ],
        summary="ok",
    )
    result, _ = run(db, reference, rx, ScriptedLLM({VerificationOut: answer}))
    assert result.status == "unsupported"
    assert result.citations == []


def test_retrieval_includes_the_monograph_dose_and_cross_references(db, reference):
    res = resolve("Zorvamide", monograph_index(db, reference.id), 95)
    r = retrieve(db, reference.id, Rx("Zorvamide", 20, frequency="every morning"), res, get_embedder(), get_settings())
    labels = [(p.channel, p.chunk.field_label) for p in r.passages]
    assert ("scoped", "Standard adult dose") in labels
    assert ("scoped", "Prescribing notes") in labels
    assert any(p.channel == "mention" and "must be taken at bedtime" in p.chunk.text for p in r.passages)
    assert len({p.id for p in r.passages}) == len(r.passages)


@pytest.mark.parametrize(
    "written",
    ["Zorvamide", "zorvamide", "ZORVAMIDE 20 mg tablets", "Zorvamide (oral)"],
)
def test_name_variants_resolve_exactly(db, reference, written):
    res = resolve(written, monograph_index(db, reference.id), 95)
    assert res.status == "exact" and res.monograph == "Zorvamide"
