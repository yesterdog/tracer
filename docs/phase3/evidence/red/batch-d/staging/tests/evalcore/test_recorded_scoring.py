# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-19: result mapping (contract docs/phase3/api-surface-batch-d.md sections 7 and 8).

The base scenario is hand-computed: 11 export tokens against 11 STT tokens with 9 hits, 1
substitution (paani spelled without its long a), 1 deletion (teen) and 1 insertion (abhi).
MER = 3/12, three spans, confidences 0.25, 1 and 1. The test values of the threshold and the
review budget are in ``evalcore_batchd_support`` and are not defaults of the product.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    ABHI,
    BASE_EXPORT,
    COUNT,
    CREATED_AT,
    GAAV,
    KEY_CANARY,
    KEYS,
    MER,
    NAMASKAR,
    PAANI,
    SHARE,
    STT_PHRASES,
    STT_TEXTS,
    TEEN,
    TEST_ONLY_FLAG_MIN_CONFIDENCE,
    TEXT_CANARY,
    ChunkScript,
    FakeEngine,
    base_document,
    configuration,
    export_text,
    flags_of,
    mask_ids,
    mod,
    results_by_key,
    run_pipeline,
    scripts,
    sentence,
)
from tracer_evalschema.bundle import check_run_bundle
from tracer_evalschema.events import check_evidence_resolves
from tracer_evalschema.results import check_result_against_definition


def _definition(bundle: Any, key: str) -> Any:
    return next(d for d in bundle.snapshot.metric_definitions if d.key == key)


# ---------------------------------------------------------------------- scored results


def test_results_pass_check_result_against_definition(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    bundle = run.bundle
    results = results_by_key(bundle)
    assert list(results) == list(KEYS)
    assert [r.metric.id for r in bundle.results] == [_definition(bundle, k).id for k in KEYS]
    for key, result in results.items():
        definition = _definition(bundle, key)
        check_result_against_definition(result, definition)
        assert result.status == "scored"
        assert result.metric.version == definition.version == 1
        assert result.metric.content_digest == definition.content_digest
        assert result.scorer is not None
        assert result.scorer.implementation_ref == f"builtin:{key}"
        assert (
            result.scorer.version == mod("registry").DEFAULT_REGISTRY.get(f"builtin:{key}").version
        )
        assert result.judge is None
        assert result.per_turn is None
        assert result.error is None
        assert result.na_reason is None
        assert result.missing_kinds == []
        assert result.created_at == CREATED_AT
    # exact values: MER 3/12, two flags, one flagged export token of eleven
    assert results[MER].value == 3 / 12 == 0.25
    assert type(results[MER].value) is float
    assert results[COUNT].value == 2
    assert type(results[COUNT].value) is int
    assert results[SHARE].value == 1 / 11
    assert type(results[SHARE].value) is float
    # no pass rule, no passed; the owner's budget decides the share only
    assert results[MER].passed is None
    assert results[COUNT].passed is None
    assert results[SHARE].passed is True
    check_run_bundle(bundle)


@pytest.mark.parametrize(
    ("budget", "expected"), [(0.25, True), (0.0909, False), (0.05, False), (1, True), (0, False)]
)
def test_share_passed_follows_the_owners_budget(
    tmp_path: Any, budget: float, expected: bool
) -> None:
    run = run_pipeline(tmp_path, config=configuration(budget=budget))
    share = results_by_key(run.bundle)[SHARE]
    assert share.value == 1 / 11
    assert share.passed is expected
    check_result_against_definition(share, _definition(run.bundle, SHARE))


def test_flags_metadata_validates_against_tracer_flags_1(tmp_path: Any) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    flags = mod("flags")
    flagsdoc = mod("flagsdoc")
    run = run_pipeline(tmp_path)
    results = results_by_key(run.bundle)
    validator = jsonschema.Draft202012Validator(flags.FLAGS_JSON_SCHEMA)
    documents = [flags_of(r) for r in results.values()]
    assert documents[0] == documents[1] == documents[2]
    for document in documents:
        assert not list(validator.iter_errors(document))
        flagsdoc.validate_flags_document(
            document, normaliser="mr-Deva/1", threshold=_threshold(), model="fake-model-1"
        )
    assert documents[0] == base_document()
    assert flagsdoc.FLAGS_METADATA_KEY == "tracer.flags"
    for result in results.values():
        assert set(result.metadata) == {"tracer.flags"}


def _threshold() -> Any:
    from decimal import Decimal

    return Decimal(str(TEST_ONLY_FLAG_MIN_CONFIDENCE))


def test_flags_metadata_has_no_text_and_no_metadata_flags(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    allowed_text = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$|^0$|^1$|^0\.[0-9]+$")
    for result in run.bundle.results:
        assert result.metadata_flags == []
        dumped = json.dumps(result.metadata, ensure_ascii=False)
        for word in (NAMASKAR, PAANI, TEEN, ABHI, GAAV):
            assert word not in dumped
        assert not re.search(r"[^\x00-\x7f]", dumped)
        stack = [result.metadata]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    assert isinstance(key, str)
                    assert re.fullmatch(r"[a-z_./0-9A-Z]+", key)
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
            else:
                assert type(node) in (int, str, bool)
                if isinstance(node, str):
                    assert allowed_text.match(node) or node in {
                        "word",
                        "requested",
                        "tracer.flags/1",
                    }
        assert "text" not in dumped.lower().replace("stt_disagreement", "")
        # the ids of the bundle appear nowhere in the document either
        for record in (*run.bundle.transcripts, *run.bundle.turns, *run.bundle.artifacts):
            assert str(record.id) not in dumped
    assert "file:" not in json.dumps([r.model_dump(mode="json") for r in run.bundle.results])


def test_evidence_refs_resolve_in_bundle(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    bundle = run.bundle
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    turn_two = next(t for t in bundle.turns if t.transcript_id == export.id and t.seq == 2)
    audio = bundle.artifacts[0]
    results = results_by_key(bundle)
    expected = [("turn", turn_two.id, None, None), ("artifact_range", audio.id, 6000, 8000)]
    for result in results.values():
        got = [(r.kind, r.ref_id, r.start_ms, r.end_ms) for r in result.evidence_refs]
        assert got == expected
    records = [*bundle.turns, *bundle.artifacts]
    for result in results.values():
        check_evidence_resolves(result.evidence_refs, records)
    # a span that is not a flag (the first, confidence 0.25) leaves no reference
    assert all(
        r.ref_id != next(t for t in bundle.turns if t.transcript_id == export.id and t.seq == 0).id
        for r in results[COUNT].evidence_refs
    )
    # the whole bundle resolves, and re-validating from JSON keeps it so
    check_run_bundle(bundle)
    assert type(bundle).model_validate_json(bundle.model_dump_json()) == bundle


def test_evidence_refs_list_distinct_turns_and_ranges_in_order(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path, config=configuration(threshold=0))
    results = results_by_key(run.bundle)
    export = next(t for t in run.bundle.transcripts if t.source == "agent_export")
    by_id = {t.id: t for t in run.bundle.turns}
    refs = results[COUNT].evidence_refs
    assert flags_of(results[COUNT])["tokens"]["flagged_export"] == 2
    assert [by_id[r.ref_id].seq for r in refs if r.kind == "turn"] == [0, 2]
    assert [(r.start_ms, r.end_ms) for r in refs if r.kind == "artifact_range"] == [
        (0, 2000),
        (6000, 8000),
    ]
    assert all(by_id[r.ref_id].transcript_id == export.id for r in refs if r.kind == "turn")
    assert [r.kind for r in refs] == ["turn", "turn", "artifact_range", "artifact_range"]
    assert results[COUNT].value == 3


def test_transcript_ids_required_with_two_transcripts(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    bundle = run.bundle
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    for result in bundle.results:
        assert result.transcript_ids == [export.id, stt.id]
    stripped = [r.model_copy(update={"transcript_ids": None}) for r in bundle.results]
    with pytest.raises(ValueError, match=r"\[applicability\].*transcript_ids required"):
        check_run_bundle(bundle.model_copy(update={"results": stripped}))
    # the same holds for a not-applicable result of a bundle that holds both transcripts
    only_caller = export_text([("user", sentence(NAMASKAR))])
    other = run_pipeline(tmp_path, export=only_caller)
    for result in other.bundle.results:
        assert result.status == "not_applicable"
        assert result.transcript_ids is not None
        assert len(result.transcript_ids) == 2
    stripped = [r.model_copy(update={"transcript_ids": None}) for r in other.bundle.results]
    with pytest.raises(ValueError, match=r"\[applicability\]"):
        check_run_bundle(other.bundle.model_copy(update={"results": stripped}))


def test_language_source_declared_from_export(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    export = next(t for t in run.bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in run.bundle.transcripts if t.source == "our_stt")
    assert export.declared_language is not None
    assert (export.declared_language.language, export.declared_language.source) == (
        "mr",
        "user_declared",
    )
    assert export.detected_language is None
    assert stt.declared_language is None
    for result in run.bundle.results:
        assert result.language_source == "declared"
        assert result.declared_language_transcript_ids == [export.id]
    # the declaration is the user's: with none, the language is not observed, never assumed
    undeclared = run_pipeline(tmp_path, export_language=None)
    for result in undeclared.bundle.results:
        assert result.status == "not_applicable"
        assert result.na_reason == "language_not_observed"
        assert result.language_source is None
        assert result.declared_language_transcript_ids == []
        assert result.value is None
        assert result.missing_kinds == []
        assert result.scorer is None
        assert result.metadata == {}
        assert result.evidence_refs == []
    # a declaration of a language the metric does not cover is not applicable, never scored
    hindi = run_pipeline(tmp_path, export_language="hi")
    hindi_export = next(t for t in hindi.bundle.transcripts if t.source == "agent_export")
    for result in hindi.bundle.results:
        assert result.status == "not_applicable"
        assert result.na_reason == "unsupported_language"
        assert result.language_source == "declared"
        assert result.declared_language_transcript_ids == [hindi_export.id]
    check_run_bundle(hindi.bundle)


# ---------------------------------------------------------------------- empty sides, errors


def _assert_error(bundle: Any, code: str) -> list[Any]:
    results = list(bundle.results)
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    for result in results:
        check_result_against_definition(
            result, next(d for d in bundle.snapshot.metric_definitions if d.id == result.metric.id)
        )
        assert result.status == "error"
        assert result.error is not None
        assert result.error.code == code
        assert result.value is None
        assert result.passed is None
        assert result.na_reason is None
        assert result.missing_kinds == []
        assert result.metadata == {}
        assert result.evidence_refs == []
        assert result.scorer is not None
        assert result.language_source == "declared"
        assert result.declared_language_transcript_ids == [export.id]
    check_run_bundle(bundle)
    return results


def test_empty_export_is_error_no_tokens_not_zero(tmp_path: Any) -> None:
    lines = [("assistant", "..."), ("user", sentence("!!", "-", "\u0964")), ("assistant", "")]
    run = run_pipeline(tmp_path, export=export_text(lines))
    results = _assert_error(run.bundle, "no_tokens")
    for result in results:
        message = result.error.message
        assert "export" in message
        assert "stt" not in message
        assert TEXT_CANARY not in message
    assert run.result.outcome == "not_scored"
    # the same code for an export that is only white space and joiners
    joiners = export_text([("assistant", "\u200d \u200c"), ("user", "\u00a0")])
    again = run_pipeline(tmp_path, export=joiners)
    _assert_error(again.bundle, "no_tokens")


def test_empty_stt_is_error_no_tokens_not_zero(tmp_path: Any) -> None:
    # every chunk empty: the STT transcript exists and holds no turn
    silent = FakeEngine(chunk_scripts=[ChunkScript(""), ChunkScript("  ")])
    run = run_pipeline(tmp_path, engine=silent)
    stt = next(t for t in run.bundle.transcripts if t.source == "our_stt")
    assert not [t for t in run.bundle.turns if t.transcript_id == stt.id]
    assert "transcript" in run.bundle.run.available_inputs
    results = _assert_error(run.bundle, "no_tokens")
    for result in results:
        message = result.error.message
        assert "stt" in message
        assert "export" not in message
    # turns that hold punctuation only have no token either
    marks = FakeEngine(chunk_scripts=scripts(texts=("... ,", "!!"), phrases=None))
    again = run_pipeline(tmp_path, engine=marks)
    results = _assert_error(again.bundle, "no_tokens")
    assert all("stt" in r.error.message and "export" not in r.error.message for r in results)
    # the export is checked first when both sides are empty
    both = run_pipeline(tmp_path, export=export_text([("assistant", "...")]), engine=silent)
    results = _assert_error(both.bundle, "no_tokens")
    assert all("export" in r.error.message for r in results)


@pytest.mark.parametrize(
    ("languages", "code", "head"),
    [
        (("hi", "mr"), "stt_unsupported_language", "1 of 2 stt turns"),
        (("hi", "hi"), "stt_unsupported_language", "2 of 2 stt turns"),
        (("en", "mr"), "stt_unsupported_language", "1 of 2 stt turns"),
        ((None, "mr"), "stt_language_not_observed", "1 of 2 stt turns"),
        ((None, None), "stt_language_not_observed", "2 of 2 stt turns"),
        (("und", "mr"), "stt_language_not_observed", "1 of 2 stt turns"),
        ((None, "hi"), "stt_language_not_observed", "1 of 2 stt turns"),
    ],
)
def test_stt_language_gate_is_an_error_never_a_score(
    tmp_path: Any, languages: tuple[str | None, ...], code: str, head: str
) -> None:
    engine = FakeEngine(chunk_scripts=scripts(languages=languages))
    run = run_pipeline(tmp_path, engine=engine)
    results = _assert_error(run.bundle, code)
    for result in results:
        assert result.error.message.startswith(head)
        assert not re.search(r"\b(?:hi|en|mr|und|None)\b", result.error.message)
        assert NAMASKAR not in result.error.message
    assert run.result.outcome == "not_scored"
    assert run.result.exit_code != 0


def test_stt_language_gate_ignores_empty_chunks_and_accepts_regional_tags(tmp_path: Any) -> None:
    engine = FakeEngine(
        chunk_scripts=[ChunkScript(STT_TEXTS[0], "mr-IN", STT_PHRASES[0]), ChunkScript("", "hi")]
    )
    run = run_pipeline(tmp_path, engine=engine)
    assert all(r.status == "scored" for r in run.bundle.results)
    assert len([t for t in run.bundle.turns if t.source == "our_stt"]) == 1


def test_alignment_size_cap_is_an_error_with_its_own_code(tmp_path: Any) -> None:
    align = mod("align")
    big = " ".join(f"w{i}" for i in range(align.ALIGN_MAX_TOKENS + 1))
    run = run_pipeline(tmp_path, export=export_text([("assistant", big)]))
    results = _assert_error(run.bundle, "alignment_too_large")
    assert all(str(align.ALIGN_MAX_TOKENS + 1) in r.error.message for r in results)
    assert all("w1 " not in r.error.message for r in results)
    # exactly at the cap is accepted by the aligner: the other side decides, here a mismatch
    ok = " ".join(f"w{i}" for i in range(50))
    again = run_pipeline(tmp_path, export=export_text([("assistant", ok)]))
    assert all(r.status == "scored" for r in again.bundle.results)


def test_levenshtein_size_cap_is_an_error_with_its_own_code(tmp_path: Any) -> None:
    flags = mod("flags")
    giant = "x" * (flags.LEVENSHTEIN_MAX_CHARS + 1)
    export = export_text([("assistant", sentence(NAMASKAR, giant, GAAV))])
    texts = (sentence(NAMASKAR, "yy", GAAV), "kk")
    engine = FakeEngine(chunk_scripts=scripts(texts=texts, phrases=None))
    run = run_pipeline(tmp_path, export=export, engine=engine)
    results = _assert_error(run.bundle, "levenshtein_too_large")
    assert all("xxxx" not in r.error.message for r in results)


def test_stt_without_provider_or_model_is_an_error_not_an_invented_value(tmp_path: Any) -> None:
    for provider, model in ((None, "m1"), ("p1", None), (None, None)):
        engine = FakeEngine(provider=provider, model=model)
        run = run_pipeline(tmp_path, engine=engine)
        _assert_error(run.bundle, "stt_provenance_missing")


def test_a_secret_looking_model_name_is_refused_as_an_error(tmp_path: Any) -> None:
    # built from parts: not a key of any account, only the shape that metadata_flags refuses
    model = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
    run = run_pipeline(tmp_path, engine=FakeEngine(model=model))
    results = _assert_error(run.bundle, "flags_document_refused")
    for result in results:
        assert model not in result.error.message
        assert "A1b2C3d4" not in result.error.message
    assert KEY_CANARY not in run.bundle.model_dump_json()


def test_score_results_is_pure_and_repeats_the_pipeline(tmp_path: Any) -> None:
    scoring = mod("scoring")
    run = run_pipeline(tmp_path)
    bundle = run.bundle
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    again = scoring.score_results(
        configuration=run.configuration,
        run=bundle.run,
        export=export,
        export_turns=[t for t in bundle.turns if t.transcript_id == export.id],
        stt=stt,
        stt_turns=[t for t in bundle.turns if t.transcript_id == stt.id],
        audio_artifact_id=bundle.artifacts[0].id,
        created_at=CREATED_AT,
    )
    assert [r.id for r in again] != [r.id for r in bundle.results]
    ours = mask_ids(json.dumps([r.model_dump(mode="json") for r in bundle.results], sort_keys=True))
    theirs = mask_ids(json.dumps([r.model_dump(mode="json") for r in again], sort_keys=True))
    assert ours == theirs
    assert [r.metric.id for r in again] == [r.metric.id for r in bundle.results]
    assert isinstance(again, tuple)
    assert not hasattr(scoring, "THRESHOLD")
    assert not hasattr(scoring, "FLAG_MIN_CONFIDENCE")


# ---------------------------------------------------------------------- thresholds


@pytest.mark.parametrize(
    ("threshold", "flagged", "count", "tokens"),
    [
        (0, (True, True, True), 3, 2),
        (0.25, (True, True, True), 3, 2),
        (0.2500001, (False, True, True), 2, 1),
        (0.3, (False, True, True), 2, 1),
        (1, (False, True, True), 2, 1),
    ],
)
def test_threshold_decides_flags_exactly_and_the_document_records_every_span(
    tmp_path: Any, threshold: float, flagged: tuple[bool, bool, bool], count: int, tokens: int
) -> None:
    run = run_pipeline(tmp_path, config=configuration(threshold=threshold))
    results = results_by_key(run.bundle)
    document = flags_of(results[COUNT])
    assert [s["flagged"] for s in document["spans"]] == list(flagged)
    assert len(document["spans"]) == 3
    assert results[COUNT].value == count
    assert document["tokens"]["flagged_export"] == tokens
    assert results[SHARE].value == tokens / 11
    assert results[MER].value == 0.25
    from decimal import Decimal

    assert document["flag_min_confidence"] == format(
        Decimal(repr(float(threshold))).normalize(), "f"
    ) or document["flag_min_confidence"] in {"0", "1"}


def test_texts_that_agree_after_normalisation_have_no_span(tmp_path: Any) -> None:
    export = export_text(
        [("assistant", sentence("WiFi", "\u0967\u0968\u0969", NAMASKAR)), ("user", sentence(GAAV))]
    )
    stt = (sentence("wifi", "123", NAMASKAR), GAAV)
    engine = FakeEngine(chunk_scripts=scripts(texts=stt, phrases=None))
    run = run_pipeline(tmp_path, export=export, engine=engine)
    results = results_by_key(run.bundle)
    assert results[MER].value == 0.0
    assert results[COUNT].value == 0
    assert results[SHARE].value == 0.0
    assert results[SHARE].passed is True
    assert all(r.evidence_refs == [] for r in results.values())
    document = flags_of(results[COUNT])
    assert document["spans"] == []
    assert document["alignment"] == {
        "unit": "word",
        "hits": 4,
        "substitutions": 0,
        "deletions": 0,
        "insertions": 0,
    }
    assert document["tokens"] == {"export": 4, "stt": 4, "flagged_export": 0}
    check_run_bundle(run.bundle)


def test_the_lla_and_la_spellings_are_never_merged(tmp_path: Any) -> None:
    from evalcore_batchd_support import VEL_LA, VEL_LLA  # type: ignore[import-not-found,unused-ignore]

    export = export_text([("assistant", sentence(NAMASKAR, VEL_LLA))])
    engine = FakeEngine(chunk_scripts=scripts(texts=(sentence(NAMASKAR, VEL_LA), ""), phrases=None))
    run = run_pipeline(tmp_path, export=export, engine=engine)
    results = results_by_key(run.bundle)
    document = flags_of(results[COUNT])
    assert document["alignment"]["substitutions"] == 1
    span = next(s for s in document["spans"] if s["substitutions"] == 1)
    assert (span["edit_distance"], span["key_length"], span["confidence"]) == (1, 3, "0.3333")
    assert span["flagged"] is True  # 1/3 is at or above the test threshold 0.3


def test_the_mapping_reads_the_stt_turns_in_seq_order_and_keeps_chunk_text_apart(
    tmp_path: Any,
) -> None:
    # two chunks never merge into one text: a word ending chunk 0 and one starting chunk 1 are
    # two tokens and two STT turns
    engine = FakeEngine(
        chunk_scripts=scripts(texts=(sentence(NAMASKAR), sentence(GAAV)), phrases=None)
    )
    export = export_text([("assistant", sentence(NAMASKAR, GAAV))])
    run = run_pipeline(tmp_path, export=export, engine=engine)
    results = results_by_key(run.bundle)
    assert results[MER].value == 0.0
    document = flags_of(results[COUNT])
    assert document["tokens"] == {"export": 2, "stt": 2, "flagged_export": 0}
    _ = (BASE_EXPORT, STT_TEXTS)


# ---------------------------------------------------------------------- a property of the whole


VOCABULARY = (NAMASKAR, PAANI, GAAV, TEEN, ABHI, "WiFi", "x1", "Y2", "kk")


def reference_counts(export: list[str], stt: list[str]) -> tuple[int, int, int, int]:
    """H, S, D, I of the minimal (edits, deletions plus insertions) alignment, by plain DP."""
    n, m = len(export), len(stt)
    best: list[list[tuple[tuple[int, int], tuple[int, int, int, int]]]] = [
        [((0, 0), (0, 0, 0, 0)) for _ in range(m + 1)] for _ in range(n + 1)
    ]
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            options = []
            if i and j:
                (e, d), (h, s, de, ins) = best[i - 1][j - 1]
                if export[i - 1].casefold() == stt[j - 1].casefold():
                    options.append(((e, d), (h + 1, s, de, ins)))
                else:
                    options.append(((e + 1, d), (h, s + 1, de, ins)))
            if i:
                (e, d), (h, s, de, ins) = best[i - 1][j]
                options.append(((e + 1, d + 1), (h, s, de + 1, ins)))
            if j:
                (e, d), (h, s, de, ins) = best[i][j - 1]
                options.append(((e + 1, d + 1), (h, s, de, ins + 1)))
            best[i][j] = min(options, key=lambda option: option[0])
    return best[n][m][1]


@pytest.fixture(scope="module")
def audio_module(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from evalcore_batchd_support import read_audio  # type: ignore[import-not-found,unused-ignore]

    return read_audio(tmp_path_factory.mktemp("audio"))


@settings(
    max_examples=40,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    export=st.lists(
        st.lists(st.sampled_from(VOCABULARY), min_size=1, max_size=5), min_size=1, max_size=3
    ),
    stt=st.tuples(
        st.lists(st.sampled_from(VOCABULARY), min_size=1, max_size=5),
        st.lists(st.sampled_from(VOCABULARY), min_size=1, max_size=5),
    ),
)
def test_scores_equal_an_independent_reference_on_random_calls(
    audio_module: Any, tmp_path: Any, export: list[list[str]], stt: tuple[list[str], list[str]]
) -> None:
    lines = [
        ("assistant" if n % 2 == 0 else "user", sentence(*words)) for n, words in enumerate(export)
    ]
    engine = FakeEngine(chunk_scripts=scripts(texts=tuple(sentence(*w) for w in stt), phrases=None))
    run = run_pipeline(tmp_path, audio=audio_module, export=export_text(lines), engine=engine)
    bundle = run.bundle
    check_run_bundle(bundle)
    results = results_by_key(bundle)
    flat_export = [w for words in export for w in words]
    flat_stt = [w for words in stt for w in words]
    hits, subs, dels, ins = reference_counts(flat_export, flat_stt)
    document = flags_of(results[COUNT])
    assert document["alignment"] == {
        "unit": "word",
        "hits": hits,
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
    }
    assert results[MER].value == (subs + dels + ins) / (hits + subs + dels + ins)
    assert 0.0 <= results[MER].value <= 1.0
    flagged = [s for s in document["spans"] if s["flagged"]]
    assert results[COUNT].value == len(flagged)
    assert results[SHARE].value == sum(s["export_tokens"] for s in flagged) / len(flat_export)
    assert document["tokens"] == {
        "export": len(flat_export),
        "stt": len(flat_stt),
        "flagged_export": sum(s["export_tokens"] for s in flagged),
    }
    assert results[MER].value == 0.0 if (subs, dels, ins) == (0, 0, 0) else results[MER].value > 0.0
    texts = {seq: line[1] for seq, line in enumerate(lines)}
    for span in document["spans"]:
        text = texts[span["turn_seq"]]
        assert 0 <= span["start"] <= span["end"] <= len(text)
        assert (span["start"] == span["end"]) == (span["export_tokens"] == 0)
        for word in text[span["start"] : span["end"]].split(" "):
            assert word == "" or word in VOCABULARY
    flagged_turns = {s["turn_seq"] for s in flagged}
    by_id = {t.id: t for t in bundle.turns}
    for result in results.values():
        assert {
            by_id[r.ref_id].seq for r in result.evidence_refs if r.kind == "turn"
        } == flagged_turns
        assert result.metadata == {"tracer.flags": document}


def test_flagged_spans_in_one_phrase_share_one_audio_range(tmp_path: Any) -> None:
    words = [NAMASKAR, "aa", GAAV, "bb", TEEN]
    export = export_text([("assistant", sentence(*words))])
    stt = (sentence(NAMASKAR, "xx", GAAV, "yy", TEEN), "")
    phrases = (((sentence(NAMASKAR, "xx", GAAV, "yy", TEEN), 100, 900),), None)
    engine = FakeEngine(chunk_scripts=scripts(texts=stt, phrases=phrases))
    run = run_pipeline(tmp_path, export=export, engine=engine, config=configuration(threshold=0.5))
    results = results_by_key(run.bundle)
    document = flags_of(results[COUNT])
    assert [s["flagged"] for s in document["spans"]] == [True, True]
    assert [(s["audio_start_ms"], s["audio_end_ms"]) for s in document["spans"]] == [(100, 900)] * 2
    ranges = [r for r in results[COUNT].evidence_refs if r.kind == "artifact_range"]
    assert [(r.start_ms, r.end_ms) for r in ranges] == [(100, 900)]


def test_a_refused_document_becomes_an_error_whatever_the_reason(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    scoring = mod("scoring")
    flagsdoc = mod("flagsdoc")

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise flagsdoc.FlagsDocumentError("flags_counts")

    monkeypatch.setattr(scoring, "validate_flags_document", refuse)
    run = run_pipeline(tmp_path)
    results = _assert_error(run.bundle, "flags_document_refused")
    assert all("flags_counts" in r.error.message for r in results)


def test_bundle_lists_the_export_before_the_stt_and_turns_in_seq_order(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path)
    bundle = run.bundle
    assert [t.source for t in bundle.transcripts] == ["agent_export", "our_stt"]
    assert [t.transcript_id for t in bundle.turns] == [bundle.transcripts[0].id] * 3 + [
        bundle.transcripts[1].id
    ] * 2
    assert [t.seq for t in bundle.turns] == [0, 1, 2, 0, 1]
    export = bundle.transcripts[0]
    assert export.declared_language.declared_at == CREATED_AT


def test_the_engine_receives_the_configured_language_it_was_given(tmp_path: Any) -> None:
    run = run_pipeline(tmp_path, language="hi", export_language="hi")
    assert run.engine.calls[0][1].configured_language == "hi"
    assert {r.na_reason for r in run.bundle.results} == {"unsupported_language"}
