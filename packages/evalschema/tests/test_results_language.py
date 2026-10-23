# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-11: language applicability of results: P-59 (language-independent metrics, untagged input,
``language_not_observed``) and N14 (user-declared languages) (ADR 0004 Decisions 6 and 7).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.results``:
``effective_language(turn, transcript, definition) -> EffectiveLanguage | None``
(``(language, source)`` with source ``observed`` or ``declared``; ``turn`` may be ``None`` when no
turn is read), ``check_language(definition, turns, transcripts) -> LanguageCheck``
(``na_reason``, ``language_source``, ``declared_language_transcript_ids``) and
``applicability(definition, run, *, turns=(), transcripts=()) -> Applicability`` (the precedence of
ADR 0004 Decision 4: ``na_reason``, ``missing_kinds``, ``language_source``,
``declared_language_transcript_ids``). The steps for a ``per_turn_share`` metric are applied to
one turn at a time by passing a single turn.

Configured languages (Evaluator, Persona, ScriptedTurn, DatasetItem, AgentTarget) are never an
input of these functions. Tags are assembled from parts where the T-DRIFT matcher would flag the
literal. Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api, data_json
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    judge_definition,
    latency_definition,
    make,
    registered_class,
)
from ._support_b3 import (
    assert_rejected3,
    channel_map_doc,
    entity_class3,
    language_declaration,
    make3,
    per_turn_summary_doc,
    result_doc,
)

OTHER = EXCLUDED_LANGUAGE_TAGS[0] + "-IN"  # an observed tag outside the nine configured languages
COVERED = ["en", "hi", "mr"]


def _results() -> Any:
    return api("results")


def _definition(languages: Any = None, *, script_sensitive: bool = False, **over: Any) -> Any:
    """A judge definition over the transcript, language-dependent unless ``languages`` says so."""
    return make(
        "MetricDefinition",
        **judge_definition(
            languages=COVERED if languages is None else languages,
            script_sensitive=script_sensitive,
            **over,
        ),
    )


def _independent(**over: Any) -> Any:
    return _definition("language_independent", key="agent_turn_count", **over)


def _transcript(source: str = "our_stt", **over: Any) -> Any:
    return make3("Transcript", source=source, **over)


def _turn(transcript: Any, seq: int = 0, **over: Any) -> Any:
    return make3("Turn", transcript_id=str(transcript.id), seq=seq, **over)


def _turns(transcript: Any, *tags: str | None, **over: Any) -> list[Any]:
    return [_turn(transcript, seq, language=tag, **over) for seq, tag in enumerate(tags)]


def _run(**over: Any) -> Any:
    fields = {"available_inputs": ["audio", "transcript"], **over}
    return make3("Run", **fields)


def _lang(definition: Any, turns: list[Any], *transcripts: Any) -> Any:
    return _results().check_language(definition, turns, list(transcripts))


def _declared(source: str = "agent_export", **over: Any) -> Any:
    return _transcript(source, declared_language=language_declaration(**over))


# ---------------------------------------------------------------------------- P-59


def test_language_independent_metric_skips_language_check() -> None:
    """P-59 (ADR 0004 Decisions 6a and 6b, I23): a metric whose ``languages`` is
    ``"language_independent"`` has no language step at all: untagged input, a language outside the
    nine, a romanized tag, ``und``, ``mul`` and ``zxx`` change nothing, and no language provenance
    is recorded (``language_source`` None). The same inputs make a language-dependent metric not
    applicable."""
    transcript = _transcript()
    independent = _independent()
    dependent = _definition()
    inputs: list[tuple[list[str | None], str | None]] = [
        ([None], "language_not_observed"),
        ([OTHER], "unsupported_language"),
        (["hi-Latn"], None),  # covered, and the definition is not script-sensitive
        (["und"], "language_not_observed"),
        (["mul"], "unsupported_language"),
        (["zxx"], "unsupported_language"),
        ([None, OTHER, "hi-Latn", "und"], "language_not_observed"),
        (["hi-IN", None], "language_not_observed"),
    ]
    for tags, dependent_reason in inputs:
        turns = _turns(transcript, *tags)
        outcome = _lang(independent, turns, transcript)
        assert outcome.na_reason is None, tags
        assert outcome.language_source is None
        assert list(outcome.declared_language_transcript_ids) == []
        full = _results().applicability(independent, _run(), turns=turns, transcripts=[transcript])
        assert full.na_reason is None
        assert _lang(dependent, turns, transcript).na_reason == dependent_reason, tags
    # the declaration of a transcript is not read either
    declared = _declared()
    declared_turns = _turns(declared, None)
    assert _lang(independent, declared_turns, declared) == (None, None, [])
    # not even script-sensitive: a language-independent metric is never script-sensitive
    with pytest.raises(ValueError, match=r"script_sensitive|language_independent"):
        make(
            "MetricDefinition",
            **judge_definition(languages="language_independent", script_sensitive=True),
        )


def test_check_language_without_turns_or_transcripts() -> None:
    """N29 (resolved in revision 11; ADR 0004 Decision 6d; api-surface 22.9 item 5): with no turn
    and no transcript there is no tag, so no observed language. A language-dependent definition
    (script-sensitive or not) gives ``("language_not_observed", None, [])``: the reason, no
    provenance, no declaration IDs. A language-independent definition has no language step and
    gives ``(None, None, [])``. Any empty sequence type counts as empty. Proposed name (SCOPE
    section 5, P1-11)."""
    results = _results()
    dependent = _definition()
    script_sensitive = _definition(script_sensitive=True, key="wer_judge")
    independent = _independent()
    for empty_turns, empty_transcripts in (([], []), ((), ()), ([], ()), ((), [])):
        for definition in (dependent, script_sensitive):
            outcome = results.check_language(definition, empty_turns, empty_transcripts)
            assert tuple(outcome) == ("language_not_observed", None, [])
            assert outcome.na_reason == "language_not_observed"
            assert outcome.language_source is None
            assert list(outcome.declared_language_transcript_ids) == []
        outcome = results.check_language(independent, empty_turns, empty_transcripts)
        assert tuple(outcome) == (None, None, [])
    keyword = results.check_language(definition=dependent, turns=[], transcripts=[])
    assert tuple(keyword) == ("language_not_observed", None, [])


def test_agent_channel_unknown_only_when_agent_audio_alone_is_missing() -> None:
    """N23 (owner, revision 11; ADR 0004 Decisions 3 and 5, the five worked examples of 5a;
    api-surface 22.9 item 4): ``applicability`` reports ``agent_channel_unknown`` only when
    ``agent_audio`` is the single missing kind, ``audio`` is available and no ChannelMap
    identifies exactly one agent channel, and then ``missing_kinds`` is empty. Every other
    combination is ``missing_input`` listing all missing kinds in the order of ``requires``.
    The language steps do not run in any of the missing-input cases, so ``language_source`` is
    None and no declaration ID is reported. Proposed name (SCOPE section 5, P1-11)."""
    results = _results()
    transcript = _transcript()
    tagged = _turns(transcript, "hi-IN")

    def outcome(
        requires: list[str],
        inputs: list[str],
        maps: list[Any] | None = None,
        *,
        language_step_runs: bool = False,
    ) -> Any:
        definition = _definition(requires=requires, key="agent_audio_check")
        run = _run(available_inputs=inputs, channel_maps=maps or [])
        found = results.applicability(definition, run, turns=tagged, transcripts=[transcript])
        # the same answer for a language-independent metric: the language step never ran
        plain = _definition(
            "language_independent", requires=requires, key="agent_audio_independent"
        )
        again = results.applicability(plain, run)
        assert (again.na_reason, list(again.missing_kinds)) == (
            found.na_reason,
            list(found.missing_kinds),
        )
        # with nothing missing the language step runs and reports its provenance
        assert found.language_source == ("observed" if language_step_runs else None)
        assert list(found.declared_language_transcript_ids) == []
        return found

    # (1) a recording, no channel map, or only unknown or mixed channels: the channel is unknown
    for maps in (
        None,
        [channel_map_doc(channels=[{"index": 0, "role": "unknown"}])],
        [channel_map_doc(channels=[{"index": 0, "role": "mixed"}])],
        [
            channel_map_doc(
                channels=[{"index": 0, "role": "caller"}, {"index": 1, "role": "unknown"}]
            )
        ],
    ):
        found = outcome(["agent_audio"], ["audio"], maps)
        assert found.na_reason == "agent_channel_unknown", maps
        assert list(found.missing_kinds) == []
    # (2) no recording at all: nothing to identify a channel in
    found = outcome(["agent_audio"], [])
    assert (found.na_reason, list(found.missing_kinds)) == ("missing_input", ["agent_audio"])
    found = outcome(["agent_audio"], ["transcript"])
    assert (found.na_reason, list(found.missing_kinds)) == ("missing_input", ["agent_audio"])
    # (3) the recording itself is missing too: both kinds, in the order of requires
    found = outcome(["audio", "agent_audio"], [])
    assert (found.na_reason, list(found.missing_kinds)) == (
        "missing_input",
        ["audio", "agent_audio"],
    )
    # (4) another kind is missing as well: the channel question is listed, not hidden
    found = outcome(["agent_audio", "reference_text"], ["audio"])
    assert (found.na_reason, list(found.missing_kinds)) == (
        "missing_input",
        ["agent_audio", "reference_text"],
    )
    # (4b) the same, with the channel question last in requires: the order of requires is kept
    found = outcome(["reference_text", "agent_audio"], ["audio"])
    assert (found.na_reason, list(found.missing_kinds)) == (
        "missing_input",
        ["reference_text", "agent_audio"],
    )
    # (5) a channel map identifies the agent but the producer did not list agent_audio
    found = outcome(["agent_audio"], ["audio"], [channel_map_doc()])
    assert (found.na_reason, list(found.missing_kinds)) == ("missing_input", ["agent_audio"])
    # and when the input is listed (the Run rule requires the map) nothing is missing
    found = outcome(
        ["agent_audio"], ["audio", "agent_audio"], [channel_map_doc()], language_step_runs=True
    )
    assert (found.na_reason, list(found.missing_kinds)) == (None, [])


def test_untagged_input_is_language_not_observed() -> None:
    """P-59 (ADR 0004 Decisions 6c and 6e, I24): a language-dependent metric whose input has no
    observed language tag is not_applicable with ``language_not_observed``, never
    ``unsupported_language`` (the language may well be supported). A result with that reason is a
    valid not_applicable result, with no language provenance when no turn has a language."""
    transcript = _transcript()
    definition = _definition()
    turns = _turns(transcript, None, None)
    outcome = _lang(definition, turns, transcript)
    assert outcome.na_reason == "language_not_observed"
    assert outcome.language_source is None
    assert list(outcome.declared_language_transcript_ids) == []
    full = _results().applicability(definition, _run(), turns=turns, transcripts=[transcript])
    assert full.na_reason == "language_not_observed"
    assert full.missing_kinds == []
    result = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source=None,
            na_reason=outcome.na_reason,
        )
    )
    _results().check_result_against_definition(result, definition)
    assert (result.value, result.passed) == (None, None)
    # tagged input is a different story
    assert _lang(definition, _turns(transcript, "hi-IN"), transcript).na_reason is None


def test_und_and_unparseable_labels_are_language_not_observed() -> None:
    """P-59 (ADR 0004 Decision 6c): ``und`` (the producer said it did not determine the language)
    and a raw label that did not normalize to a tag (``language_raw`` present while the tag is
    None) count as untagged, on the turn and on the transcript. A label that does normalize
    (``hi_IN`` to ``hi-IN``) is an observation. ``mul`` and ``zxx`` are observations whose primary
    language is never covered."""
    definition = _definition()
    transcript = _transcript()
    for turns in (
        [_turn(transcript, language="und")],
        [_turn(transcript, language=None, language_raw="hindi")],
        [_turn(transcript, language=None, language_raw="")],
        [_turn(transcript, 0, language="hi-IN"), _turn(transcript, 1, language="und")],
    ):
        assert _lang(definition, turns, transcript).na_reason == "language_not_observed"
    for kwargs in (
        {"detected_language": "und"},
        {"detected_language": None, "detected_language_raw": "hindi"},
        {"detected_language": None, "detected_language_raw": ""},
    ):
        tagged = _transcript("agent_export", **kwargs)
        assert _lang(definition, [_turn(tagged)], tagged).na_reason == "language_not_observed"
        assert _lang(definition, [], tagged).na_reason == "language_not_observed"
    observed = _turn(transcript, language="hi-IN", language_raw="hi_IN")
    outcome = _lang(definition, [observed], transcript)
    assert (outcome.na_reason, outcome.language_source) == (None, "observed")
    for tag in ("mul", "zxx"):
        assert _lang(definition, _turns(transcript, tag), transcript).na_reason == (
            "unsupported_language"
        )


def test_turn_language_takes_precedence_over_transcript_language() -> None:
    """P-59 (ADR 0004 Decision 6c, api-surface 21.2 steps 1-4): a turn's own tag is used before
    its transcript's tag; the transcript's tag fills a turn with no label; and a turn with a label
    of its own that is not a tag (``und`` or an unparseable raw label) does not fall back to the
    transcript."""
    definition = _definition(languages=["en", "hi"])
    tagged = _transcript("our_stt", detected_language=OTHER)
    own = _lang(definition, [_turn(tagged, language="en-IN")], tagged)
    assert (own.na_reason, own.language_source) == (None, "observed")
    inherited = _lang(definition, [_turn(tagged)], tagged)
    assert inherited.na_reason == "unsupported_language"  # the transcript's tag is what was heard
    covered = _transcript("our_stt", detected_language="hi-IN")
    assert _lang(definition, [_turn(covered)], covered).na_reason is None
    for label in ({"language": "und"}, {"language": None, "language_raw": "hindi"}):
        outcome = _lang(definition, [_turn(covered, **label)], covered)
        assert outcome.na_reason == "language_not_observed", label
    # the turn's tag may be outside the list while the transcript's tag is inside
    own_outside = _lang(definition, [_turn(covered, language=OTHER)], covered)
    assert own_outside.na_reason == "unsupported_language"
    effective = _results().effective_language
    assert tuple(effective(_turn(covered, language="en-IN"), covered, definition)) == (
        "en-IN",
        "observed",
    )
    assert tuple(effective(_turn(covered), covered, definition)) == ("hi-IN", "observed")
    assert effective(_turn(covered, language="und"), covered, definition) is None


def test_configured_language_never_used_as_observed() -> None:
    """P-59 (ADR 0004 Decision 6c): configured languages describe intent, not what was heard.
    Whatever the Evaluator, Persona, ScriptedTurn, DatasetItem and AgentTarget say, an untagged
    input stays ``language_not_observed``; the functions have no parameter through which a
    configured language could enter."""
    configured = "mr-IN"
    evaluator = make("Evaluator", language=configured)
    persona = make("Persona", language=configured)
    target = make("AgentTarget", languages=[configured])
    item = make3("DatasetItem", language=configured)
    scripted = registered_class("ScriptedTurn").model_validate(
        {"index": 0, "text": "hello", "language": configured}
    )
    assert {
        evaluator.language,
        persona.language,
        target.languages[0],
        item.language,
        scripted.language,
    } == {configured}
    transcript = _transcript()
    definition = _definition()
    outcome = _lang(definition, _turns(transcript, None), transcript)
    assert outcome.na_reason == "language_not_observed"
    assert outcome.language_source is None
    results = _results()
    assert list(inspect.signature(results.check_language).parameters) == [
        "definition",
        "turns",
        "transcripts",
    ]
    assert set(inspect.signature(results.applicability).parameters) == {
        "definition",
        "run",
        "turns",
        "transcripts",
    }
    assert set(inspect.signature(results.effective_language).parameters) == {
        "turn",
        "transcript",
        "definition",
    }
    run_model = entity_class3("Run")
    assert not [name for name in run_model.model_fields if "language" in name]


def test_per_conversation_any_untagged_turn_is_language_not_observed() -> None:
    """P-59 (ADR 0004 Decision 6e): a per_conversation metric applies the language steps to all the
    turns it reads, so one untagged turn makes the whole result ``language_not_observed``: scoring
    the tagged turns alone would score a different input than the definition names. Wherever the
    untagged turn stands and however many turns are tagged. The tagged turns' provenance is
    still reported."""
    definition = _definition()
    transcript = _transcript()
    for position in (0, 1, 2, 49):
        tags: list[str | None] = ["hi-IN"] * 50
        tags[position] = None
        outcome = _lang(definition, _turns(transcript, *tags), transcript)
        assert outcome.na_reason == "language_not_observed", position
        assert outcome.language_source == "observed"  # other turns had an effective language
    all_tagged = _lang(definition, _turns(transcript, *(["hi-IN"] * 50)), transcript)
    assert (all_tagged.na_reason, all_tagged.language_source) == (None, "observed")
    only_untagged = _lang(definition, _turns(transcript, None), transcript)
    assert (only_untagged.na_reason, only_untagged.language_source) == (
        "language_not_observed",
        None,
    )


def test_per_turn_share_untagged_turns_not_counted() -> None:
    """P-59 (ADR 0004 Decision 6e, I25): for a ``per_turn_share`` metric the steps run on each turn
    on its own: an untagged turn is a not_applicable turn, outside the denominator and counted in
    ``counts.not_applicable``; the share is over the counted turns; when no turn is counted the
    result's reason is the strongest of the turns' reasons."""
    definition = _definition(
        aggregation={"kind": "per_turn_share", "turn_rule": {"kind": "equals_true"}},
        pass_rule={"kind": "range", "min": 0.5, "inclusive": True},
    )
    transcript = _transcript()
    turns = _turns(transcript, "hi-IN", "hi-IN", None, "en-IN")
    per_turn = [_lang(definition, [turn], transcript).na_reason for turn in turns]
    assert per_turn == [None, None, "language_not_observed", None]
    verdicts = [True, False, None, True]  # the judge's per-turn answer where the turn applied
    passed = sum(1 for r, v in zip(per_turn, verdicts, strict=True) if r is None and v is True)
    failed = sum(1 for r, v in zip(per_turn, verdicts, strict=True) if r is None and v is False)
    not_applicable = sum(1 for r in per_turn if r is not None)
    summary = registered_class("PerTurnSummary").model_validate(
        per_turn_summary_doc(passed, failed, not_applicable, 0)
    )
    assert (summary.counted_turns, summary.total_turns) == (3, 4)
    assert summary.share == "0.666666666666666667"  # 2 of the 3 counted turns, not 2 of 4
    assert summary.counts.not_applicable == 1
    # no turn applies: every turn is not applicable and the reasons follow the precedence
    strongest = _results().strongest_na_reason
    mixed = [
        _lang(definition, [turn], transcript).na_reason
        for turn in _turns(transcript, None, OTHER, None)
    ]
    assert mixed == ["language_not_observed", "unsupported_language", "language_not_observed"]
    assert strongest(mixed) == "language_not_observed"
    outside = [
        _lang(definition, [turn], transcript).na_reason for turn in _turns(transcript, OTHER, OTHER)
    ]
    assert strongest(outside) == "unsupported_language"
    na = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source=None,
            na_reason=strongest(mixed),
            per_turn=per_turn_summary_doc(0, 0, 3, 0),
        )
    )
    assert na.per_turn.share is None


def test_unsupported_language_na_for_observed_bn_IN() -> None:
    """A-23, ADR 0004 I14: an observed tag outside the nine languages is a valid observation on
    a turn and a transcript and is never rejected at ingest; a metric whose ``languages`` do not
    cover its primary language is not_applicable with ``unsupported_language`` (not
    ``language_not_observed``, the language was observed), with the provenance ``observed``."""
    transcript = _transcript(detected_language=OTHER, detected_language_raw=OTHER)
    turn = _turn(transcript, language=OTHER)
    assert (turn.language, transcript.detected_language) == (OTHER, OTHER)
    definition = _definition(languages=["hi", "en"])
    for turns in ([turn], []):
        outcome = _lang(definition, turns, transcript)
        assert outcome.na_reason == "unsupported_language"
        assert outcome.language_source == "observed"
    full = _results().applicability(definition, _run(), turns=[turn], transcripts=[transcript])
    assert full.na_reason == "unsupported_language"
    result = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source="observed",
            na_reason="unsupported_language",
        )
    )
    _results().check_result_against_definition(result, definition)
    assert (result.value, result.passed) == (None, None)
    # an unsupported turn after a covered one still makes the result unsupported_language
    covered_first = _lang(
        definition, _turns(transcript, "hi-IN", "en-GB", OTHER, "hi-IN"), transcript
    )
    assert covered_first.na_reason == "unsupported_language"
    # the same primary language with another region or script is covered
    hindi = _definition(languages=["hi-IN"], key="hindi_only")
    assert _lang(hindi, _turns(transcript, "hi-Deva-IN"), transcript).na_reason is None


def _wer(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "key": "wer",
        "kind": "deterministic",
        "unit": "wer",
        "requires": ["transcript", "reference_text"],
        "languages": COVERED,
        "script_sensitive": True,
        "pass_rule": {"kind": "range", "max": 0.25, "inclusive": True},
        "implementation_ref": "builtin:wer",
    }
    return make("MetricDefinition", **latency_definition(**{**fields, **over}))


def test_wer_not_applicable_for_observed_romanized() -> None:
    """A script-sensitive metric (ADR 0004 Decision 7, I11; deferred from P1-04c): on observed text
    whose script is not a native script of its language (``hi-Latn``) it is not_applicable with
    ``unsupported_script``, and it says so for every configured language, derived from the
    data file ``languages.json`` and not from the metric key. Text without a script subtag is not
    treated as romanized."""
    wer = _wer()
    transcript = _transcript()
    outcome = _lang(wer, _turns(transcript, "hi-Latn"), transcript)
    assert (outcome.na_reason, outcome.language_source) == ("unsupported_script", "observed")
    result = entity_class3("MetricResult").model_validate(
        result_doc(
            wer,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source="observed",
            na_reason="unsupported_script",
        )
    )
    _results().check_result_against_definition(result, wer)
    for tag in ("hi-IN", "hi", "hi-Deva", "hi-Deva-IN", "en", "en-IN", "mr-Deva"):
        assert _lang(wer, _turns(transcript, tag), transcript).na_reason is None, tag
    assert _lang(wer, _turns(transcript, "hi-IN", "hi-Latn"), transcript).na_reason == (
        "unsupported_script"
    )
    rows = data_json("languages.json")["languages"]
    assert len(rows) == 9
    every_language = make(
        "MetricDefinition",
        **latency_definition(
            key="custom_error_rate",
            kind="deterministic",
            unit="cer",
            requires=["transcript"],
            languages=[row["language"] for row in rows],
            script_sensitive=True,
            pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
            implementation_ref="builtin:cer",
        ),
    )
    for row in rows:
        language, allowed = row["language"], row["allowed_scripts"]
        foreign = next(
            script for script in ("Latn", "Deva", "Arab", "Cyrl") if script not in allowed
        )
        bad = _lang(every_language, _turns(transcript, f"{language}-{foreign}"), transcript)
        assert bad.na_reason == "unsupported_script", (language, foreign)
        for script in allowed:
            good = _lang(every_language, _turns(transcript, f"{language}-{script}"), transcript)
            assert good.na_reason is None, (language, script)
    # the rule is the definition's flag, not the key
    renamed = _wer(key="word_errors_per_word")
    assert (
        _lang(renamed, _turns(transcript, "hi-Latn"), transcript).na_reason == "unsupported_script"
    )
    not_sensitive_named_wer = make(
        "MetricDefinition",
        **latency_definition(
            key="wer",
            kind="deterministic",
            unit="wer",
            requires=["transcript"],
            languages=COVERED,
            script_sensitive=False,
            pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
            implementation_ref="builtin:wer",
        ),
    )
    assert (
        _lang(not_sensitive_named_wer, _turns(transcript, "hi-Latn"), transcript).na_reason is None
    )


def test_romanized_observed_scored_when_not_script_sensitive() -> None:
    """ADR 0004 I12 (deferred from P1-04c): the same romanized text with a definition that is not
    script-sensitive (a judge of goal completion) is applicable and scored: the result is a normal
    scored result whose language provenance is ``observed``."""
    judge = _definition()
    transcript = _transcript()
    turns = _turns(transcript, "hi-Latn", "hi-Latn")
    outcome = _lang(judge, turns, transcript)
    assert (outcome.na_reason, outcome.language_source) == (None, "observed")
    full = _results().applicability(judge, _run(), turns=turns, transcripts=[transcript])
    assert full.na_reason is None
    scored = entity_class3("MetricResult").model_validate(
        result_doc(judge, value=True, passed=True, language_source=outcome.language_source)
    )
    _results().check_result_against_definition(scored, judge)
    assert scored.status == "scored"


# ---------------------------------------------------------------------------- N14


def test_declared_language_lifts_language_not_observed() -> None:
    """N14 (ADR 0004 Decisions 6e and 6g, I29): for a metric that is not script-sensitive, a
    declaration covers the turns that have no label of their own, so ``language_not_observed``
    no longer applies to them: the result is applicable (scored, or ``unsupported_language`` when
    the declared language is not covered), the provenance is ``declared`` and the transcript is
    listed. A turn with its own tag next to a covered turn keeps its own source; one covered
    turn is enough for ``declared``."""
    definition = _definition()
    declared = _declared()
    outcome = _lang(definition, _turns(declared, None, None), declared)
    assert outcome.na_reason is None
    assert outcome.language_source == "declared"
    assert list(outcome.declared_language_transcript_ids) == [declared.id]
    full = _results().applicability(
        definition, _run(), turns=_turns(declared, None), transcripts=[declared]
    )
    assert (full.na_reason, full.language_source) == (None, "declared")
    assert list(full.declared_language_transcript_ids) == [declared.id]
    mixed = _lang(definition, _turns(declared, "hi-IN", None), declared)
    assert (mixed.na_reason, mixed.language_source) == (None, "declared")
    several = [_declared() for _ in range(4)]
    spread = [turn for transcript in several for turn in _turns(transcript, None)]
    many = _lang(definition, spread, *several)
    assert many.language_source == "declared"
    assert [str(i) for i in many.declared_language_transcript_ids] == sorted(
        str(t.id) for t in several
    )
    observed_only = _lang(definition, _turns(declared, "hi-IN"), declared)
    assert (observed_only.na_reason, observed_only.language_source) == (None, "observed")
    assert list(observed_only.declared_language_transcript_ids) == []
    # no turn is read (audio-only metric): the declaration of the transcript supplies the language
    audio_only = _lang(definition, [], declared)
    assert (audio_only.na_reason, audio_only.language_source) == (None, "declared")
    assert list(audio_only.declared_language_transcript_ids) == [declared.id]
    # without the declaration the same turns are not applicable
    plain = _transcript("agent_export")
    assert _lang(definition, _turns(plain, None), plain).na_reason == "language_not_observed"


def test_declared_language_never_treated_as_observed() -> None:
    """N14 (ADR 0004 Decision 6g, I29): a declaration fills no observed field: the transcript's
    ``detected_language`` and ``detected_language_raw`` and the turns' ``language`` and
    ``language_raw`` stay as imported, also after the language steps ran; it is never copied to
    another transcript of the same call (an ``our_stt`` transcript of the call has its own tags and
    its own provenance); the result says ``declared``, not ``observed``."""
    definition = _definition()
    declared = _declared()
    turns = _turns(declared, None, None)
    outcome = _lang(definition, turns, declared)
    assert outcome.language_source == "declared"
    assert declared.detected_language is None
    assert declared.detected_language_raw is None
    assert all((t.language, t.language_raw) == (None, None) for t in turns)
    dumped = declared.model_dump(mode="json")
    assert dumped["detected_language"] is None
    assert dumped["declared_language"]["language"] == "mr-IN"
    own = _transcript("our_stt")
    own_turns = _turns(own, "mr-IN", "mr-IN")
    own_outcome = _lang(definition, own_turns, own)
    assert (own_outcome.language_source, list(own_outcome.declared_language_transcript_ids)) == (
        "observed",
        [],
    )
    both = _lang(definition, [*turns, *own_turns], declared, own)
    assert both.language_source == "declared"
    assert list(both.declared_language_transcript_ids) == [declared.id]  # not the observed one
    assert own.declared_language is None
    effective = _results().effective_language(turns[0], declared, definition)
    assert tuple(effective) == ("mr-IN", "declared")


def test_declared_language_not_used_for_und_or_unparseable_labels() -> None:
    """N14 (ADR 0004 Decision 6g, I29): a declaration fills only absence. A turn with a label of its
    own that is not a tag (``und``, or a raw label that did not parse) keeps 'no observed
    language' and the declaration does not cover it, even though its transcript has one."""
    definition = _definition()
    declared = _declared()
    for label in ({"language": "und"}, {"language": None, "language_raw": "marathi"}):
        outcome = _lang(definition, [_turn(declared, **label)], declared)
        assert outcome.na_reason == "language_not_observed", label
        assert outcome.language_source is None
        assert list(outcome.declared_language_transcript_ids) == []
    mixed = _lang(
        definition,
        [_turn(declared, 0), _turn(declared, 1, language="und")],
        declared,
    )
    assert mixed.na_reason == "language_not_observed"
    assert mixed.language_source == "declared"  # the first turn did use the declaration
    assert list(mixed.declared_language_transcript_ids) == [declared.id]
    effective = _results().effective_language
    assert effective(_turn(declared, language="und"), declared, definition) is None
    assert effective(_turn(declared, language_raw="x"), declared, definition) is None


def test_observed_turn_tag_takes_precedence_over_declared_language() -> None:
    """N14 (ADR 0004 Decision 6g, I29): a turn with its own tag uses that tag (provenance
    ``observed``), not the declaration: with a declared ``mr`` and a metric that covers only
    ``hi`` and ``en``, a turn tagged ``en-IN`` is applicable while an untagged one is
    ``unsupported_language`` (its language is the declared one)."""
    definition = _definition(languages=["hi", "en"])
    declared = _declared(language="mr-IN")
    own = _lang(definition, [_turn(declared, language="en-IN")], declared)
    assert (own.na_reason, own.language_source) == (None, "observed")
    assert list(own.declared_language_transcript_ids) == []
    covered_by_declaration = _lang(definition, [_turn(declared)], declared)
    assert covered_by_declaration.na_reason == "unsupported_language"
    effective = _results().effective_language
    assert tuple(effective(_turn(declared, language="en-IN"), declared, definition)) == (
        "en-IN",
        "observed",
    )


def test_declared_language_still_gives_unsupported_language() -> None:
    """N14 (ADR 0004 Decision 6g, I30): a declaration does not make a language supported: when the
    declared language's primary language is not in ``languages`` the result is
    ``unsupported_language`` with provenance ``declared`` and the declaring transcript listed;
    ``missing_input`` still comes before it."""
    definition = _definition(languages=["hi", "en"])
    declared = _declared(language="mr-IN")
    outcome = _lang(definition, _turns(declared, None), declared)
    assert (outcome.na_reason, outcome.language_source) == ("unsupported_language", "declared")
    assert list(outcome.declared_language_transcript_ids) == [declared.id]
    result = entity_class3("MetricResult").model_validate(
        result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source="declared",
            na_reason="unsupported_language",
            declared_language_transcript_ids=[str(declared.id)],
        )
    )
    _results().check_result_against_definition(result, definition)
    needs_reference = _definition(
        languages=["hi", "en"], requires=["transcript", "reference_text"], key="needs_reference"
    )
    full = _results().applicability(
        needs_reference, _run(), turns=_turns(declared, None), transcripts=[declared]
    )
    assert full.na_reason == "missing_input"
    assert full.language_source is None
    assert list(full.declared_language_transcript_ids) == []


def test_declaration_does_not_suppress_unsupported_script() -> None:
    """N14 (ADR 0004 Decisions 6e and 6g, I30): a declaration never hides ``unsupported_script``: a
    script-sensitive metric over turns that carry their own romanized tag (``hi-Latn``) of a
    transcript that also holds a declaration is ``unsupported_script`` with provenance
    ``observed`` and no declared transcript. (For a metric that is not script-sensitive the same
    turns are applicable.)"""
    sensitive = _definition(script_sensitive=True, key="native_script_check")
    declared = _declared(language="hi-IN")
    turns = _turns(declared, "hi-Latn", "hi-Latn")
    outcome = _lang(sensitive, turns, declared)
    assert (outcome.na_reason, outcome.language_source) == ("unsupported_script", "observed")
    assert list(outcome.declared_language_transcript_ids) == []
    lenient = _lang(_definition(), turns, declared)
    assert (lenient.na_reason, lenient.language_source) == (None, "observed")
    result = entity_class3("MetricResult").model_validate(
        result_doc(
            sensitive,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source="observed",
            na_reason="unsupported_script",
        )
    )
    _results().check_result_against_definition(result, sensitive)


def test_script_sensitive_metric_ignores_declared_language() -> None:
    """N14 (ADR 0004 Decision 6g, I30): a script-sensitive metric never uses a declared language,
    because a declaration states the language and not the script of the text. A turn whose
    language would come only from the declaration has no effective language for such a metric:
    ``language_not_observed``, exactly as without the declaration, and no provenance; the same
    turns are applicable for a metric that is not script-sensitive. A result cannot claim
    ``declared`` for a script-sensitive definition."""
    sensitive = _definition(script_sensitive=True, key="native_script_check")
    lenient = _definition()
    declared = _declared()
    turns = _turns(declared, None, None)
    outcome = _lang(sensitive, turns, declared)
    assert outcome.na_reason == "language_not_observed"
    assert outcome.language_source is None
    assert list(outcome.declared_language_transcript_ids) == []
    assert _lang(lenient, turns, declared).language_source == "declared"
    assert _results().effective_language(turns[0], declared, sensitive) is None
    assert _results().effective_language(turns[0], declared, lenient) is not None
    # observed turns beside declared-only ones: still not observed, and observed turns keep theirs
    mixed = _lang(sensitive, _turns(declared, "hi-IN", None), declared)
    assert (mixed.na_reason, mixed.language_source) == ("language_not_observed", "observed")
    claimed = result_doc(
        sensitive,
        value=True,
        passed=True,
        language_source="declared",
        declared_language_transcript_ids=[str(declared.id)],
    )
    result = entity_class3("MetricResult").model_validate(claimed)
    with pytest.raises(ValueError, match="script"):
        _results().check_result_against_definition(result, sensitive)


def test_result_language_source_presence_and_values() -> None:
    """N14 (ADR 0004 Decision 6g, I31): ``language_source`` is ``observed``, ``declared`` or absent.
    On the result alone it is None when the language steps did not run (``missing_input``,
    ``agent_channel_unknown``). Against the definition: None for a language-independent metric;
    not ``declared`` for a script-sensitive one; and for a language-dependent metric required once
    the steps found a language (scored, error, ``unsupported_language``, ``unsupported_script``),
    free for ``language_not_observed``."""
    model = entity_class3("MetricResult")
    check = _results().check_result_against_definition
    dependent = _definition()
    independent = _independent()
    sensitive = _definition(script_sensitive=True, key="native_script_check")

    def na(definition: Any, reason: str, source: str | None, **extra: Any) -> Any:
        doc = result_doc(
            definition,
            status="not_applicable",
            value=None,
            passed=None,
            judge=None,
            language_source=source,
            na_reason=reason,
            **extra,
        )
        if reason == "missing_input":
            doc["missing_kinds"] = ["reference_text"]
        return model.model_validate(doc)

    for reason in ("missing_input", "agent_channel_unknown"):
        for source in ("observed", "declared"):
            with pytest.raises(ValidationError):
                na(dependent, reason, source, declared_language_transcript_ids=[])
        check(na(dependent, reason, None), dependent)
    for reason in ("unsupported_language", "unsupported_script"):
        check(na(dependent, reason, "observed"), dependent)
        with pytest.raises(ValueError, match="language_source"):
            check(na(dependent, reason, None), dependent)
    sources: list[str | None] = [None, "observed"]
    for maybe_source in sources:
        check(na(dependent, "language_not_observed", maybe_source), dependent)
    declared_id = [str(make3("Transcript").id)]
    check(
        na(
            dependent,
            "language_not_observed",
            "declared",
            declared_language_transcript_ids=declared_id,
        ),
        dependent,
    )
    scored = model.model_validate(result_doc(dependent, language_source="observed"))
    check(scored, dependent)
    unsourced = model.model_validate(result_doc(dependent, language_source=None))
    with pytest.raises(ValueError, match="language_source"):
        check(unsourced, dependent)
    errored = model.model_validate(
        result_doc(
            dependent,
            status="error",
            value=None,
            passed=None,
            judge=None,
            language_source="observed",
            error={"code": "judge_call_failed", "message": "x"},
        )
    )
    check(errored, dependent)
    no_source = model.model_validate(
        result_doc(
            dependent,
            status="error",
            value=None,
            passed=None,
            judge=None,
            language_source=None,
            error={"code": "judge_call_failed", "message": "x"},
        )
    )
    with pytest.raises(ValueError, match="language_source"):
        check(no_source, dependent)
    # independent definitions never record a source
    check(model.model_validate(result_doc(independent, language_source=None)), independent)
    for source in ("observed", "declared"):
        extra = {"declared_language_transcript_ids": declared_id} if source == "declared" else {}
        doc = result_doc(independent, language_source=source, **extra)
        with pytest.raises(ValueError, match="language"):
            check(model.model_validate(doc), independent)
    # a script-sensitive definition: observed is fine, declared is not
    check(model.model_validate(result_doc(sensitive, language_source="observed")), sensitive)
    for bad in ("Observed", "heard", "", "user_declared", True):
        assert_rejected3(model, result_doc(language_source=bad), field="language_source")


def test_declared_language_transcript_ids_iff_declared() -> None:
    """N14 (ADR 0004 Decision 6g, I31): ``declared_language_transcript_ids`` is non-empty if and
    only
    if ``language_source`` is ``declared``; the IDs are ``tsc_`` IDs, unique and sorted by their
    string form; the default is the empty list."""
    model = entity_class3("MetricResult")
    ids = sorted(str(make3("Transcript").id) for _ in range(3))
    declared = result_doc(language_source="declared", declared_language_transcript_ids=ids[:1])
    assert [str(i) for i in model.model_validate(declared).declared_language_transcript_ids] == ids[
        :1
    ]
    many = result_doc(language_source="declared", declared_language_transcript_ids=ids)
    assert [str(i) for i in model.model_validate(many).declared_language_transcript_ids] == ids
    assert (
        model.model_validate(
            result_doc(language_source="observed")
        ).declared_language_transcript_ids
        == []
    )
    assert (
        model.model_validate(result_doc(language_source=None)).declared_language_transcript_ids
        == []
    )
    assert_rejected3(model, result_doc(language_source="declared"))
    assert_rejected3(
        model, result_doc(language_source="declared", declared_language_transcript_ids=[])
    )
    for source in ("observed", None):
        assert_rejected3(
            model, result_doc(language_source=source, declared_language_transcript_ids=ids[:1])
        )
    assert_rejected3(
        model,
        result_doc(language_source="declared", declared_language_transcript_ids=[ids[0], ids[0]]),
    )
    assert_rejected3(
        model,
        result_doc(
            language_source="declared", declared_language_transcript_ids=list(reversed(ids))
        ),
    )
    for bad in (str(make3("Run").id), str(make3("Turn").id), "tsc_x", None, 3):
        assert_rejected3(
            model, result_doc(language_source="declared", declared_language_transcript_ids=[bad])
        )
    assert "declared_language_transcript_ids" not in (
        entity_class3("MetricResult").model_json_schema().get("required", [])
    )


# ------------------------------------------------------------- the steps and the worked examples


def test_effective_language_steps_table() -> None:
    """Proposed name (api-surface 21.2, the six steps): ``effective_language`` returns
    ``(language, source)`` or ``None``: 1 the turn's tag (not ``und``); 2 a turn with ``und`` or an
    unparseable raw label has none; 3 the transcript's tag (not ``und``); 4 a transcript with
    ``und``
    or an unparseable raw label has none; 5 the declaration, if the definition is not
    script-sensitive; 6 otherwise none. Configured languages are never read. With no turn read
    (``turn=None``) only steps 3 to 6 apply."""
    effective = _results().effective_language
    lenient = _definition()
    sensitive = _definition(script_sensitive=True, key="native_script_check")
    declared = _declared(language="mr")
    own = _transcript("agent_export", detected_language="ta-IN")
    bare = _transcript("agent_export")
    und = _transcript("agent_export", detected_language="und")
    raw = _transcript("agent_export", detected_language_raw="tamil")

    def got(turn: Any, transcript: Any, definition: Any = lenient) -> Any:
        value = effective(turn, transcript, definition)
        return None if value is None else tuple(value)

    assert got(_turn(own, language="en-IN"), own) == ("en-IN", "observed")  # 1
    assert got(_turn(declared, language="en-IN"), declared) == ("en-IN", "observed")  # 1 beats 5
    assert got(_turn(own, language="und"), own) is None  # 2
    assert got(_turn(own, language_raw="telugu"), own) is None  # 2
    assert got(_turn(declared, language="UND".lower()), declared) is None  # 2 beats 5
    assert got(_turn(own), own) == ("ta-IN", "observed")  # 3
    assert got(None, own) == ("ta-IN", "observed")  # 3, no turn read
    assert got(_turn(und), und) is None  # 4
    assert got(_turn(raw), raw) is None  # 4
    assert got(None, und) is None
    assert got(_turn(declared), declared) == ("mr", "declared")  # 5
    assert got(None, declared) == ("mr", "declared")  # 5, no turn read
    assert got(_turn(declared), declared, sensitive) is None  # 5 needs a lenient definition
    assert got(_turn(bare), bare) is None  # 6
    assert got(None, bare) is None
    assert got(_turn(bare), bare, sensitive) is None
    value = effective(_turn(own), own, lenient)
    assert (value.language, value.source) == ("ta-IN", "observed")
    # a turn of another transcript than the one given is the caller's mistake, never silently read
    stranger = _transcript("agent_export")
    with pytest.raises(ValueError, match="transcript"):
        _lang(lenient, [_turn(stranger)], own)


def test_untagged_import_worked_example_6f() -> None:
    """Proposed name (ADR 0004 Decision 6f, I26; the structure of the F8 example on synthetic
    records; the real twin test is ``test_f8_untagged_transcript_language_reasons`` in P1-36): an
    untagged ``agent_export`` transcript with no timing and no audio. p95 response latency
    (language independent, needs turn timing) is ``missing_input``; an agent turn count (language
    independent) is applicable; goal completion (judge, languages mr, hi, en, not script-sensitive)
    is ``language_not_observed`` whatever the configured language of the evaluator; WER (script
    sensitive, needs audio and a reference) is ``missing_input``, which comes before the language
    steps. A script-sensitive metric that has its inputs is ``language_not_observed``."""
    results = _results()
    transcript = _transcript("agent_export")
    turns = [_turn(transcript, 0, role="caller"), _turn(transcript, 1, role="agent")]
    run = _run(available_inputs=["agent_transcript"], observed_tier="T2")
    assert make("Evaluator", language="mr-IN").language == "mr-IN"
    latency = make(
        "MetricDefinition",
        **latency_definition(key="p95_latency", languages="language_independent"),
    )
    count = make(
        "MetricDefinition",
        **latency_definition(
            key="agent_turn_count",
            kind="deterministic",
            unit="count",
            requires=["agent_transcript"],
            languages="language_independent",
            pass_rule=None,
            implementation_ref="builtin:turn_count",
        ),
    )
    goal = _definition(languages=["mr", "hi", "en"], requires=["agent_transcript"])
    wer = make(
        "MetricDefinition",
        **latency_definition(
            key="wer",
            kind="deterministic",
            unit="wer",
            requires=["agent_audio", "reference_text"],
            languages=["mr", "hi", "en"],
            script_sensitive=True,
            pass_rule={"kind": "range", "max": 0.25, "inclusive": True},
            implementation_ref="builtin:wer",
        ),
    )
    native_script = _definition(
        languages=["mr", "hi", "en"],
        script_sensitive=True,
        requires=["agent_transcript"],
        key="native_script_check",
    )

    def of(definition: Any) -> Any:
        return results.applicability(definition, run, turns=turns, transcripts=[transcript])

    assert (of(latency).na_reason, of(latency).missing_kinds) == ("missing_input", ["turn_timing"])
    assert of(count).na_reason is None
    assert of(count).language_source is None
    assert (of(goal).na_reason, of(goal).language_source) == ("language_not_observed", None)
    assert of(wer).na_reason == "missing_input"
    assert set(of(wer).missing_kinds) == {"agent_audio", "reference_text"}
    assert of(native_script).na_reason == "language_not_observed"


def test_untagged_import_with_declaration_worked_example_6h() -> None:
    """Proposed name (ADR 0004 Decision 6h, I33; the real twin test is
    ``test_f8_declared_language_reasons`` in P1-36): the same import with a declared Marathi.
    Latency stays ``missing_input`` and the turn count stays applicable, both without provenance;
    goal completion is applicable with ``declared`` and the transcript listed; the same judge in
    a version that lists only Hindi and English is ``unsupported_language`` with ``declared``;
    WER stays ``missing_input`` and a script-sensitive metric with its inputs stays
    ``language_not_observed``; an ``our_stt`` transcript of the call with ``mr-IN`` turns gives
    ``observed`` on that transcript while the declaration stays on the export."""
    results = _results()
    declared = _declared(language="mr-IN")
    turns = [_turn(declared, 0, role="caller"), _turn(declared, 1, role="agent")]
    run = _run(available_inputs=["agent_transcript"], observed_tier="T2")
    latency = make(
        "MetricDefinition",
        **latency_definition(key="p95_latency", languages="language_independent"),
    )
    goal = _definition(languages=["mr", "hi", "en"], requires=["agent_transcript"])
    narrower = _definition(languages=["hi", "en"], requires=["agent_transcript"], key="goal_hi_en")
    native_script = _definition(
        languages=["mr", "hi", "en"],
        script_sensitive=True,
        requires=["agent_transcript"],
        key="native_script_check",
    )

    def of(definition: Any, call_turns: list[Any] | None = None, *others: Any) -> Any:
        return results.applicability(
            definition,
            run,
            turns=turns if call_turns is None else call_turns,
            transcripts=list(others) or [declared],
        )

    assert of(latency).na_reason == "missing_input"
    assert of(latency).language_source is None
    scored = of(goal)
    assert (scored.na_reason, scored.language_source) == (None, "declared")
    assert list(scored.declared_language_transcript_ids) == [declared.id]
    other = of(narrower)
    assert (other.na_reason, other.language_source) == ("unsupported_language", "declared")
    assert of(native_script).na_reason == "language_not_observed"
    assert of(native_script).language_source is None
    stt = _transcript("our_stt")
    stt_turns = _turns(stt, "mr-IN", "mr-IN")
    from_stt = of(goal, stt_turns, stt)
    assert (from_stt.na_reason, from_stt.language_source) == (None, "observed")
    assert list(from_stt.declared_language_transcript_ids) == []
    assert declared.declared_language is not None
    assert stt.declared_language is None
