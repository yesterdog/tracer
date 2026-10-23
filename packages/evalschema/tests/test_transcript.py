# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-10b: Transcript, Segment, Turn, Exchange, WallTime, TimelineOrigin and LanguageDeclaration
(ADR 0002 Decisions 3 and 4, ADR 0003, ADR 0004 Decision 6g, ADR 0005, ADR 0011).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.transcript``:
the entities ``Transcript`` (``tsc``), ``Turn`` (``turn``, with ``transcript_id`` linking it to its
transcript) and ``Exchange`` (``exc``); the value objects ``Segment``, ``WallTime``,
``TimelineOrigin`` and ``LanguageDeclaration``; and
``check_transcript(transcript, turns, exchanges=None) -> None``, the rules that need the records of
one transcript together (unique ``seq``, the timeline origin, the exchange references). It raises
``ValueError``.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ._support import api
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    contains_key,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    DEVANAGARI,
    OTHER_LABEL_RAW,
    OTHER_TAG,
    QUOTE_MARK,
    assert_input_hidden,
    assert_rejected3,
    doc3,
    entity_class3,
    ingest_warning_doc,
    language_declaration,
    make3,
    marked_id,
    marked_key,
    marked_set,
    marked_tag,
    new_id,
    privacy_human,
    segment_doc,
    tool_call_doc,
    transcript_doc,
    turn_doc,
)

NON_ASCII = "".join(chr(cp) for cp in (0x0939, 0x093F, 0x0928, 0x094D, 0x0926, 0x0940))  # Hindi


def _turn(**over: Any) -> Any:
    return make3("Turn", **over)


def _timed(**over: Any) -> dict[str, Any]:
    return {"start_ms": 1000, "end_ms": 2000, "timing_source": "audio_alignment", **over}


def _check(transcript: Any, turns: list[Any], exchanges: list[Any] | None = None) -> None:
    api("transcript").check_transcript(transcript, turns, exchanges)


def _artifact_origin() -> dict[str, Any]:
    return {"kind": "artifact", "ref": new_id("art")}


# --------------------------------------------------------------------------------------- Turn


def test_turn_seq_required_timing_optional() -> None:
    """ADR 0011 I1: ``seq`` is required (order is always known) and timing is optional: a turn
    with no start, no end and no wall time is the normal shape of a transcript-only import. The
    fields that carry meaning have no default either: ``role``, ``source``, ``text``,
    ``transcript_id``. A bare turn keeps absent optional fields as ``None``."""
    turn_model = entity_class3("Turn")
    bare = _turn()
    assert (bare.start_ms, bare.end_ms, bare.wall_time) == (None, None, None)
    assert (bare.timing_source, bare.language, bare.language_raw) == ("none", None, None)
    assert not bare.segments  # absent (None) or empty: both mean no sub-structure
    assert bare.interrupted is None
    assert bare.exchange_index is None
    assert bare.contains_bidi_controls is False
    doc = turn_doc()
    for missing in ("seq", "role", "source", "text", "transcript_id"):
        assert_rejected3(turn_model, {k: v for k, v in doc.items() if k != missing}, field=missing)
    for bad in (-1, 1.5, "0", True, None, 1.0):
        assert_rejected3(turn_model, {**doc, "seq": bad}, field="seq")
    for good in (0, 1, 41, 100_000):
        assert _turn(seq=good).seq == good
    assert {"seq", "transcript_id"} <= set(schema_of(turn_model)["required"])


def test_turn_seq_unique_within_transcript() -> None:
    """ADR 0011 I1: ``seq`` is unique within its transcript (``check_transcript``). The same ``seq``
    in another transcript is fine, and a turn that belongs to another transcript is rejected."""
    transcript = make3("Transcript")
    other = make3("Transcript")
    turns = [_turn(transcript_id=str(transcript.id), seq=seq) for seq in (0, 1, 2)]
    _check(transcript, turns)
    _check(transcript, [])
    twin = _turn(transcript_id=str(transcript.id), seq=1)
    with pytest.raises(ValueError, match=r"\b1\b"):
        _check(transcript, [*turns, twin])
    elsewhere = [_turn(transcript_id=str(other.id), seq=seq) for seq in (0, 1, 2)]
    _check(other, elsewhere)
    with pytest.raises(ValueError, match=str(other.id)):
        _check(transcript, [*turns, elsewhere[0]])
    gap = [_turn(transcript_id=str(transcript.id), seq=seq) for seq in (0, 5, 9)]
    _check(transcript, gap)  # seq is an order, not a contiguous index


def test_turn_end_ge_start() -> None:
    """ADR 0002 I6, ADR 0011 I2: ``end_ms >= start_ms`` when both are present. Equal is fine, a
    single bound is fine, and the offsets are strict non-negative integers."""
    turn_model = entity_class3("Turn")
    assert _turn(**_timed(start_ms=500, end_ms=500)).end_ms == 500
    assert _turn(**_timed(start_ms=0, end_ms=1)).start_ms == 0
    assert _turn(timing_source="telemetry", start_ms=10).end_ms is None
    assert _turn(timing_source="telemetry", end_ms=10).start_ms is None
    assert_rejected3(turn_model, turn_doc(**_timed(start_ms=2000, end_ms=1999)))
    for bad in (-1, 1.5, "1", True):
        assert_rejected3(turn_model, turn_doc(**_timed(start_ms=bad)), field="start_ms")
        assert_rejected3(turn_model, turn_doc(**_timed(end_ms=bad)), field="end_ms")
    schema = schema_of(turn_model)
    for name in ("start_ms", "end_ms"):
        assert json.dumps(schema["properties"][name]).count('"minimum": 0') == 1


def test_segment_end_ge_start() -> None:
    """ADR 0011 I2: the same rule on ``Segment``."""
    segment = registered_class("Segment")
    assert segment.model_validate(segment_doc(start_ms=5, end_ms=5)).end_ms == 5
    assert segment.model_validate(segment_doc(start_ms=5)).end_ms is None
    assert segment.model_validate(segment_doc(end_ms=5)).start_ms is None
    with pytest.raises(ValidationError):
        segment.model_validate(segment_doc(start_ms=6, end_ms=5))
    for bad in (-1, 0.5, "1", True):
        with pytest.raises(ValidationError):
            segment.model_validate(segment_doc(start_ms=bad))


def test_segment_times_within_turn() -> None:
    """P-28 (ADR 0011 Decision 2): segments live in ``Turn.segments``; when both a segment and its
    turn carry times, the segment's range lies inside the turn's range. A segment that starts
    before the turn or ends after it is rejected; touching the turn's bounds is fine."""
    turn_model = entity_class3("Turn")
    inside = [
        segment_doc(start_ms=1000, end_ms=1500, text="one"),
        segment_doc(start_ms=1500, end_ms=2000, text="two"),
    ]
    loaded = _turn(**_timed(segments=inside))
    assert [s.text for s in loaded.segments or []] == ["one", "two"]
    for bad in (
        segment_doc(start_ms=999, end_ms=1500),
        segment_doc(start_ms=1000, end_ms=2001),
        segment_doc(start_ms=0, end_ms=5000),
    ):
        assert_rejected3(turn_model, turn_doc(**_timed(segments=[inside[0], bad])))
    assert _turn(segments=[]).segments == []


def test_segment_granularity_phrase() -> None:
    """ADR 0011 I6, ADR 0003 I8: granularity is word, phrase or utterance (a Sarvam phrase
    timestamp is a ``phrase``); anything else is rejected. ``Segment`` is a value object (no
    identity fields) in ``Turn.segments`` and its text is stored verbatim."""
    segment = registered_class("Segment")
    for granularity in ("word", "phrase", "utterance"):
        assert (
            segment.model_validate(segment_doc(granularity=granularity)).granularity == granularity
        )
    for bad in ("sentence", "Phrase", "", None, 1):
        with pytest.raises(ValidationError):
            segment.model_validate(segment_doc(granularity=bad))
    assert set(property_enum(segment, "granularity")) == {"word", "phrase", "utterance"}
    assert not {"id", "schema_version", "created_at", "metadata"} & set(segment.model_fields)
    text = NON_ASCII + " " + chr(0x200D)
    kept = _turn(segments=[segment_doc(text=text, granularity="phrase")])
    assert (kept.segments or [])[0].text == text
    for missing in ("granularity", "text"):
        with pytest.raises(ValidationError):
            segment.model_validate({k: v for k, v in segment_doc().items() if k != missing})


def test_confidence_in_0_1() -> None:
    """ADR 0011 I6, P-29: a reported confidence lies in [0,1] (``Segment.confidence``,
    ``Transcript.language_confidence``) and is absent when the source reported none; ``Turn`` has
    no confidence field of its own."""
    segment = registered_class("Segment")
    for good in (0.0, 0.5, 1.0, 0.123456789):
        assert segment.model_validate(segment_doc(confidence=good)).confidence == good
        assert make3("Transcript", language_confidence=good).language_confidence == good
    for bad in (-0.01, 1.01, float("nan"), float("inf"), "0.5", True, -1):
        with pytest.raises(ValidationError):
            segment.model_validate(segment_doc(confidence=bad))
        with pytest.raises(ValidationError):
            entity_class3("Transcript").model_validate(transcript_doc(language_confidence=bad))
    assert segment.model_validate(segment_doc()).confidence is None
    assert make3("Transcript").language_confidence is None
    turn_model = entity_class3("Turn")
    assert "confidence" not in turn_model.model_fields
    assert_rejected3(turn_model, turn_doc(confidence=0.9))
    for model in (segment, entity_class3("Transcript")):
        props = schema_of(model)["properties"]
        name = "confidence" if model is segment else "language_confidence"
        text = json.dumps(props[name])
        assert '"minimum": 0' in text
        assert '"maximum": 1' in text


def test_timing_source_none_iff_no_timing() -> None:
    """ADR 0011 I3: ``timing_source`` is ``none`` if and only if the turn has no ``start_ms`` and no
    ``end_ms``. A ``wall_time`` is an annotation and never counts as timing, so a turn that has
    only a wall time has ``timing_source=none``, and a source other than none without offsets is
    rejected."""
    turn_model = entity_class3("Turn")
    wall = {"at": "2026-10-22T10:00:00.250Z", "meaning": "unknown"}
    assert _turn(wall_time=wall).timing_source == "none"
    for source in ("audio_alignment", "telemetry", "observer"):
        assert _turn(**_timed(timing_source=source)).timing_source == source
        assert _turn(timing_source=source, start_ms=1).timing_source == source
        assert_rejected3(turn_model, turn_doc(timing_source=source), field="timing_source")
        assert_rejected3(turn_model, turn_doc(timing_source=source, wall_time=wall))
    assert_rejected3(turn_model, turn_doc(**_timed(timing_source="none")), field="timing_source")
    assert_rejected3(turn_model, turn_doc(start_ms=0, timing_source="none"))
    assert_rejected3(turn_model, turn_doc(end_ms=0, timing_source="none"))
    assert set(property_enum(turn_model, "timing_source")) == {
        "audio_alignment",
        "telemetry",
        "observer",
        "none",
    }
    with pytest.raises(ValidationError):
        turn_model.model_validate(turn_doc(timing_source="guess"))


def test_turn_has_no_started_at_field() -> None:
    """ADR 0002 I13, ADR 0011 I12: ``Turn.started_at`` is gone; a wall-clock instant is
    ``wall_time{at, meaning}`` with meaning start, end, logged or unknown. F8 maps to
    ``meaning=unknown`` without inventing a start."""
    turn_model = entity_class3("Turn")
    assert "started_at" not in turn_model.model_fields
    assert "started_at" not in schema_of(turn_model)["properties"]
    assert_rejected3(turn_model, turn_doc(started_at="2026-10-22T10:00:00Z"))
    wall_time = registered_class("WallTime")
    assert set(property_enum(wall_time, "meaning")) == {"start", "end", "logged", "unknown"}
    for meaning in ("start", "end", "logged", "unknown"):
        loaded = _turn(wall_time={"at": "2026-10-22T15:30:00+05:30", "meaning": meaning})
        assert loaded.wall_time.meaning == meaning
        dumped = json.loads(loaded.model_dump_json())
        assert dumped["wall_time"]["at"] == "2026-10-22T10:00:00.000000Z"
    for bad in (
        {"at": "2026-10-22T10:00:00", "meaning": "unknown"},
        {"at": "2026-10-22T10:00:00Z", "meaning": "begin"},
        {"at": "2026-10-22T10:00:00Z"},
        {"meaning": "unknown"},
        {"at": 1_790_000_000, "meaning": "unknown"},
        {"at": "2026-10-22T10:00:00Z", "meaning": "unknown", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            wall_time.model_validate(bad)
    assert not {"id", "schema_version", "created_at", "metadata"} & set(wall_time.model_fields)


def test_turn_source_enum_exact() -> None:
    """ADR 0011 I4, ADR 0003: ``Turn.source`` is exactly our_stt, agent_stt, agent_llm_output,
    agent_tts_input, agent_context, agent_log, script, human_reference (an ``enum``, never
    ``const``). The stage is never collapsed or guessed, so other spellings are rejected."""
    turn_model = entity_class3("Turn")
    members = {
        "our_stt",
        "agent_stt",
        "agent_llm_output",
        "agent_tts_input",
        "agent_context",
        "agent_log",
        "script",
        "human_reference",
    }
    assert set(property_enum(turn_model, "source")) == members
    for member in members:
        assert _turn(source=member).source == member
    for bad in ("agent", "stt", "tts", "llm", "agent_export", "observer", "AGENT_LOG", "", None):
        assert_rejected3(turn_model, turn_doc(source=bad), field="source")
    assert not contains_key(schema_of(turn_model), "const")


def test_transcript_source_names_producer() -> None:
    """P-27 (ADR 0011 Decision 1): ``Transcript.source`` names the producer (our_stt, agent_export,
    agent_telemetry, observer, script, human_reference) while ``Turn.source`` names the pipeline
    stage of each turn, so one transcript can mix stages (F4: user lines agent_stt, assistant
    lines agent_context). ``agent_log`` is a stage, not a producer, and is rejected on a
    transcript."""
    transcript_model = entity_class3("Transcript")
    producers = {
        "our_stt",
        "agent_export",
        "agent_telemetry",
        "observer",
        "script",
        "human_reference",
    }
    assert set(property_enum(transcript_model, "source")) == producers
    for producer in producers:
        assert make3("Transcript", source=producer).source == producer
    for bad in ("agent_log", "agent_stt", "agent", "stt", "OUR_STT", "", None):
        assert_rejected3(transcript_model, transcript_doc(source=bad), field="source")
    transcript = make3("Transcript", source="agent_telemetry")
    turns = [
        _turn(transcript_id=str(transcript.id), seq=0, role="caller", source="agent_stt"),
        _turn(transcript_id=str(transcript.id), seq=1, role="agent", source="agent_context"),
    ]
    _check(transcript, turns)  # no equality between the producer and the stage is required
    assert {t.source for t in turns} == {"agent_stt", "agent_context"}


def test_interrupted_none_distinct_from_false() -> None:
    """ADR 0011 I5: ``interrupted`` (and ``Exchange.was_interrupted``) is true, false or ``None``
    (unknown). ``None`` stays distinct from ``False`` through the default, a JSON round trip and
    the JSON Schema; absent means unknown, never 'not interrupted'."""
    turn_model = entity_class3("Turn")
    for value in (None, False, True):
        loaded = _turn(interrupted=value)
        assert loaded.interrupted is value
        again = turn_model.model_validate_json(loaded.model_dump_json())
        assert again.interrupted is value
        assert json.loads(loaded.model_dump_json())["interrupted"] is value
    assert _turn().interrupted is None
    assert turn_model.model_validate(turn_doc()).interrupted is None
    bad_flags: list[Any] = [0, 1, "false", "true", "yes", []]
    for bad_flag in bad_flags:
        assert_rejected3(turn_model, turn_doc(interrupted=bad_flag), field="interrupted")
    exchange = entity_class3("Exchange")
    for value in (None, False, True):
        loaded = make3("Exchange", was_interrupted=value)
        assert exchange.model_validate_json(loaded.model_dump_json()).was_interrupted is value
    assert make3("Exchange").was_interrupted is None
    assert schema_accepts(turn_model, doc3("Turn", interrupted=None))
    assert not schema_accepts(turn_model, doc3("Turn", interrupted="no"))


def test_role_enum_exact() -> None:
    """ADR 0016 I1, ADR 0003 I2: ``Turn.role`` is exactly caller, agent, unknown. The roles of the
    removed concepts (ivr, human_agent, transfer) and the common provider names (user, assistant,
    bot) are rejected; the raw provider label goes through an importer mapping, never the type."""
    turn_model = entity_class3("Turn")
    assert set(property_enum(turn_model, "role")) == {"caller", "agent", "unknown"}
    for role in ("caller", "agent", "unknown"):
        assert _turn(role=role).role == role
    for bad in (
        "ivr",
        "human_agent",
        "user",
        "assistant",
        "bot",
        "system",
        "mixed",
        "Caller",
        "",
        None,
        "SPEAKER_01",
    ):
        assert_rejected3(turn_model, turn_doc(role=bad), field="role")


def test_turn_language_observed_any() -> None:
    """ADR 0011 I8, ADR 0005 I20: ``Turn.language`` accepts any well-formed observed tag and
    never checks support: an excluded language, a romanized tag, ``und``, ``mul``, ``zxx``, an
    unregistered tag, a private-use tag. Letter case is canonicalized. A malformed tag, a
    language name or a padded label is rejected by the type (the importer keeps such a label in
    ``language_raw``)."""
    turn_model = entity_class3("Turn")
    excluded = EXCLUDED_LANGUAGE_TAGS[0] + "-IN"
    for tag in (excluded, "hi-Latn", "und", "mul", "zxx", "xq-IN", "hi-x-foo", "en-AU", "ta-LK"):
        assert _turn(language=tag).language == tag
    assert _turn(language="HI-in").language == "hi-IN"
    assert _turn().language is None
    for bad in (
        "hi_IN",
        "hindi",
        "",
        " hi-IN",
        "hi-IN ",
        "hi--IN",
        "-hi",
        "hi-IN-",
        "i-klingon",
        7,
    ):
        assert_rejected3(turn_model, turn_doc(language=bad), field="language")


def test_turn_language_raw_verbatim() -> None:
    """P-13 (ADR 0005 Decision 3, I12d): ``Turn.language_raw`` keeps the provider's per-turn
    label code point for code point, whether or not it parsed: underscores, padding, case,
    non-ASCII, NFC versus NFD, an empty string. It is independent of ``language`` (a turn can
    carry a raw label and no tag), and a lone surrogate is rejected. The pure helper builds the
    pair."""
    language = api("language")
    nfd = "e" + chr(0x0301)
    nfc = chr(0x00E9)
    for raw in (" hi_IN\n", "HI_in", "hindi", NON_ASCII, nfd, nfc, "", "bn\tIN"):
        assert _turn(language_raw=raw).language_raw == raw
        dumped = json.loads(_turn(language_raw=raw).model_dump_json())
        assert dumped["language_raw"] == raw
    assert _turn(language_raw=nfd).language_raw != _turn(language_raw=nfc).language_raw
    assert _turn(language_raw="hindi").language is None
    observed = language.observe_language_label("hi_IN")
    turn = _turn(language=observed.tag, language_raw=observed.raw)
    assert (turn.language, turn.language_raw) == ("hi-IN", "hi_IN")
    unparseable = language.observe_language_label("hindi")
    turn = _turn(language=unparseable.tag, language_raw=unparseable.raw)
    assert (turn.language, turn.language_raw) == (None, "hindi")
    with pytest.raises(ValidationError):
        entity_class3("Turn").model_validate(turn_doc(language_raw=chr(0xD800)))


def test_turn_and_segment_bidi_flag_computed_and_verified() -> None:
    """Proposed name (ADR 0017 Decision 2, I4; the Turn half of ``test_bidi_flag_computed`` and
    ``test_bidi_flag_forgery_rejected``): ``contains_bidi_controls`` is computed from ``text`` when
    omitted, verified when supplied (a forged flag is rejected both ways), always serialized, and
    set only by U+202A-202E and U+2066-2069 (LRM, RLM and ALM do not set it). The text is stored
    code point for code point."""
    segment = registered_class("Segment")
    turn_model = entity_class3("Turn")
    spoof = "1234" + chr(0x202E) + "9876"
    plain = "1234 " + chr(0x200F) + chr(0x061C) + " 9876"
    assert _turn(text=spoof).contains_bidi_controls is True
    assert _turn(text=plain).contains_bidi_controls is False
    assert _turn(text=spoof).text == spoof
    assert json.loads(_turn(text=spoof).model_dump_json())["contains_bidi_controls"] is True
    assert json.loads(_turn(text=plain).model_dump_json())["contains_bidi_controls"] is False
    assert_rejected3(turn_model, turn_doc(text=spoof, contains_bidi_controls=False))
    assert_rejected3(turn_model, turn_doc(text=plain, contains_bidi_controls=True))
    assert _turn(text=spoof, contains_bidi_controls=True).contains_bidi_controls is True
    for char in "".join(chr(cp) for cp in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))):
        assert _turn(text="a" + char + "b").contains_bidi_controls is True
    assert segment.model_validate(segment_doc(text=spoof)).contains_bidi_controls is True
    assert segment.model_validate(segment_doc(text=plain)).contains_bidi_controls is False
    with pytest.raises(ValidationError):
        segment.model_validate(segment_doc(text=spoof, contains_bidi_controls=False))
    with pytest.raises(ValidationError):
        segment.model_validate(segment_doc(text=plain, contains_bidi_controls=True))
    nfd = "e" + chr(0x0301)
    assert _turn(text=nfd).text == nfd  # no normalization


# ------------------------------------------------------------------------------------ Exchange


def test_exchange_shape_and_sources() -> None:
    """Proposed name (ADR 0011 Decision 4, P-06): ``Exchange{index, started_at?, duration_ms?,
    was_interrupted?, source}``: ``index`` is a non-negative integer, ``source`` is exactly
    audio_alignment, telemetry, observer, inferred_from_turns, ``started_at`` is a UTC instant
    and ``duration_ms`` an ``Ms``. Pipecat's ``turn_number`` maps to ``Exchange.index``, never to
    ``Turn.seq``."""
    exchange = entity_class3("Exchange")
    assert set(property_enum(exchange, "source")) == {
        "audio_alignment",
        "telemetry",
        "observer",
        "inferred_from_turns",
    }
    for source in ("audio_alignment", "telemetry", "observer", "inferred_from_turns"):
        assert make3("Exchange", source=source).source == source
    loaded = make3("Exchange", started_at="2026-10-22T15:30:00+05:30", duration_ms=4200)
    assert json.loads(loaded.model_dump_json())["started_at"] == "2026-10-22T10:00:00.000000Z"
    for bad in ("turn", "inferred", "", None):
        assert_rejected3(exchange, doc3("Exchange", source=bad), field="source")
    bad_numbers: list[Any] = [-1, 1.5, "0", True, None]
    for bad_index in bad_numbers:
        assert_rejected3(exchange, doc3("Exchange", index=bad_index), field="index")
    for bad_duration in (-1, 1.5, "5", True):
        assert_rejected3(exchange, doc3("Exchange", duration_ms=bad_duration), field="duration_ms")
    assert_rejected3(
        exchange, doc3("Exchange", started_at="2026-10-22T10:00:00"), field="started_at"
    )
    assert {"index", "source"} <= set(schema_of(exchange)["required"])
    bare = make3("Exchange")
    assert (bare.started_at, bare.duration_ms, bare.was_interrupted) == (None, None, None)


def test_exchange_index_references_existing_exchange() -> None:
    """ADR 0011 I11: a turn's ``exchange_index`` must name an existing exchange of the run
    (``check_transcript`` with the run's exchanges); a turn without one is fine, and the exchange
    indexes are unique. Without the exchanges argument the references are not checked."""
    transcript = make3("Transcript")
    exchanges = [make3("Exchange", index=index) for index in (0, 1, 2)]
    ok = [
        _turn(transcript_id=str(transcript.id), seq=0, exchange_index=0),
        _turn(transcript_id=str(transcript.id), seq=1, exchange_index=2),
        _turn(transcript_id=str(transcript.id), seq=2),
    ]
    _check(transcript, ok, exchanges)
    dangling = _turn(transcript_id=str(transcript.id), seq=3, exchange_index=3)
    with pytest.raises(ValueError, match=r"\b3\b"):
        _check(transcript, [*ok, dangling], exchanges)
    with pytest.raises(ValueError, match=r"\b0\b"):
        _check(transcript, ok[:1], [])
    _check(transcript, [*ok, dangling])  # exchanges not given: not checked here
    with pytest.raises(ValueError, match=r"\b1\b"):
        _check(transcript, ok, [*exchanges, make3("Exchange", index=1)])
    with pytest.raises(ValidationError):
        entity_class3("Turn").model_validate(turn_doc(exchange_index=-1))
    with pytest.raises(ValidationError):
        entity_class3("Turn").model_validate(turn_doc(exchange_index="0"))


# ------------------------------------------------------------------------------ timeline origin


def test_timeline_origin_artifact_requires_artifact_ref() -> None:
    """ADR 0002 Decision 3, I12: ``TimelineOrigin{kind, ref?, started_at?}`` with kind artifact,
    telemetry or observer. ``kind=artifact`` needs ``ref`` to be an ``art_`` ID (sample 0 of that
    artifact); telemetry and observer carry the source's trace or session reference as an
    ``ExternalRef`` (or none) and never an ``art_`` ID. ``started_at`` is a UTC instant."""
    origin = registered_class("TimelineOrigin")
    art = new_id("art")
    loaded = origin.model_validate({"kind": "artifact", "ref": art})
    assert str(loaded.ref) == art
    assert json.loads(loaded.model_dump_json())["ref"] == art
    external = {
        "system": "pipecat",
        "kind": "trace_id",
        "value": "4bf92f3577b34da6a3ce929d0e0e4736",
    }
    bad_refs: list[Any] = [None, "recording.wav", new_id("run"), new_id("tsc"), 7, external]
    for bad in bad_refs:
        with pytest.raises(ValidationError):
            origin.model_validate({"kind": "artifact", "ref": bad})
    with pytest.raises(ValidationError):
        origin.model_validate({"kind": "artifact"})
    for kind in ("telemetry", "observer"):
        assert origin.model_validate({"kind": kind}).ref is None
        with_ref = origin.model_validate({"kind": kind, "ref": external})
        assert with_ref.ref.value == external["value"]
        assert json.loads(with_ref.model_dump_json())["ref"] == external
        with pytest.raises(ValidationError):
            origin.model_validate({"kind": kind, "ref": art})
    started = origin.model_validate(
        {"kind": "telemetry", "started_at": "2026-10-22T15:30:00+05:30"}
    )
    assert json.loads(started.model_dump_json())["started_at"] == "2026-10-22T10:00:00.000000Z"
    for bad_time in ("2026-10-22T10:00:00", 1_790_000_000, "now"):
        with pytest.raises(ValidationError):
            origin.model_validate({"kind": "telemetry", "started_at": bad_time})
    assert set(property_enum(origin, "kind")) == {"artifact", "telemetry", "observer"}
    for bad_kind in ("file", "Artifact", "", None):
        with pytest.raises(ValidationError):
            origin.model_validate({"kind": bad_kind})
    with pytest.raises(ValidationError):
        origin.model_validate({"kind": "artifact", "ref": art, "path": "recordings/x.wav"})


def test_timeline_origin_required_iff_offsets_present() -> None:
    """ADR 0002 I11: a transcript's ``timeline_origin`` is required if and only if any of its turns
    or segments has ``start_ms`` or ``end_ms``, and a ``ToolCallEvent``'s ``timeline_origin`` is
    required if and only if it has ``offset_ms``. A wall time is not an offset."""
    origin = _artifact_origin()
    timed = make3("Transcript", timeline_origin=origin)
    untimed = make3("Transcript")
    timed_turn = _turn(transcript_id=str(timed.id), seq=0, **_timed())
    plain_turn = _turn(transcript_id=str(untimed.id), seq=0)
    _check(timed, [timed_turn])
    _check(untimed, [plain_turn])
    _check(untimed, [])
    with pytest.raises(ValueError, match="timeline_origin"):
        _check(untimed, [_turn(transcript_id=str(untimed.id), seq=0, **_timed())])
    with pytest.raises(ValueError, match="timeline_origin"):
        _check(timed, [_turn(transcript_id=str(timed.id), seq=0)])
    with pytest.raises(ValueError, match="timeline_origin"):
        _check(timed, [])
    # a single bound is an offset
    with pytest.raises(ValueError, match="timeline_origin"):
        _check(
            untimed,
            [_turn(transcript_id=str(untimed.id), seq=0, timing_source="observer", end_ms=9)],
        )
    # segment offsets count even when the turn itself carries none
    seg_turn = _turn(
        transcript_id=str(untimed.id), seq=0, segments=[segment_doc(start_ms=1, end_ms=2)]
    )
    with pytest.raises(ValueError, match="timeline_origin"):
        _check(untimed, [seg_turn])
    seg_turn_ok = _turn(
        transcript_id=str(timed.id), seq=0, segments=[segment_doc(start_ms=1, end_ms=2)]
    )
    _check(timed, [seg_turn_ok])
    # a wall time is not an offset
    wall = {"at": "2026-10-22T10:00:00Z", "meaning": "logged"}
    _check(untimed, [_turn(transcript_id=str(untimed.id), seq=0, wall_time=wall)])
    # tool calls: offset_ms and timeline_origin come together
    tool = make3("ToolCallEvent", offset_ms=1500, timeline_origin=origin)
    assert tool.offset_ms == 1500
    assert make3("ToolCallEvent").timeline_origin is None
    event = entity_class3("ToolCallEvent")
    assert_rejected3(event, doc3("ToolCallEvent", offset_ms=1500), field="timeline_origin")
    assert_rejected3(event, doc3("ToolCallEvent", timeline_origin=origin), field="timeline_origin")


# -------------------------------------------------------------------------- Transcript records


def test_transcript_shape_and_optional_fields() -> None:
    """Proposed name (ADR 0011 Decision 1, ADR 0005 Decisions 2 and 3): the observed language of a
    transcript is ``detected_language`` (any well-formed tag) with the provider's label kept in
    ``detected_language_raw`` (verbatim, also when no tag parsed); ``provider`` and ``model`` are
    open ``MachineKey`` values; ``privacy`` is required; absent optional fields are ``None``; the
    record round-trips through JSON and its JSON Schema."""
    transcript_model = entity_class3("Transcript")
    bare = make3("Transcript")
    for name in (
        "provider",
        "model",
        "detected_language",
        "detected_language_raw",
        "language_confidence",
        "declared_language",
        "timeline_origin",
    ):
        assert getattr(bare, name) is None, name
    full = make3(
        "Transcript",
        source="our_stt",
        provider="some-new-vendor",
        model="org/Model-Name:v2",
        detected_language=OTHER_TAG,
        detected_language_raw=OTHER_LABEL_RAW,
        language_confidence=0.97,
        timeline_origin=_artifact_origin(),
        privacy=privacy_human(),
    )
    assert (full.provider, full.model) == ("some-new-vendor", "org/Model-Name:v2")
    assert (full.detected_language, full.detected_language_raw) == (OTHER_TAG, OTHER_LABEL_RAW)
    again = transcript_model.model_validate_json(full.model_dump_json())
    assert again == full
    assert schema_accepts(transcript_model, json.loads(full.model_dump_json()))
    unparsed = make3("Transcript", detected_language=None, detected_language_raw=" hindi ")
    assert unparsed.detected_language is None
    assert unparsed.detected_language_raw == " hindi "
    for bad in ("hi_IN", "hindi", "", " hi-IN"):
        assert_rejected3(
            transcript_model, transcript_doc(detected_language=bad), field="detected_language"
        )
    for bad in ("two words", "", DEVANAGARI):
        assert_rejected3(transcript_model, transcript_doc(provider=bad), field="provider")
    # the transcript holds no turns: they are records of their own that point to it
    assert "turns" not in transcript_model.model_fields


def test_transcript_provider_call_ref_is_external_ref() -> None:
    """N28 (resolved in revision 11; ADR 0012 Decision 6; api-surface 22.9 item 6):
    ``Transcript.provider_call_ref`` is the provider's own request identifier, a foreign
    identifier, so it is an optional ``ExternalRef{system, kind, value}``. It is absent by
    default, accepts the object, keeps it exactly (also with spaces and with text that looks like
    one of our IDs), round-trips as an object and not as text, and rejects a bare string, a typed
    ID of ours and a non-object. Proposed name (SCOPE section 5, P1-10b)."""
    model = entity_class3("Transcript")
    assert "provider_call_ref" in model.model_fields
    assert make3("Transcript").provider_call_ref is None
    assert make3("Transcript", provider_call_ref=None).provider_call_ref is None
    for value in ("req_8f3a2c", "  spaced  ", "run_" + new_id("run")[4:], "0188-uuid-like"):
        reference = {"system": "sarvam", "kind": "request_id", "value": value}
        loaded = make3("Transcript", provider_call_ref=reference)
        assert loaded.provider_call_ref.value == value
        assert (loaded.provider_call_ref.system, loaded.provider_call_ref.kind) == (
            "sarvam",
            "request_id",
        )
        dumped = json.loads(loaded.model_dump_json())
        assert dumped["provider_call_ref"] == reference  # an object, never a string
        assert model.model_validate_json(loaded.model_dump_json()) == loaded
        assert schema_accepts(model, dumped)
    for bad in (
        "req_8f3a2c",
        "",
        new_id("run"),
        new_id("tsc"),
        7,
        ["sarvam", "request_id", "x"],
        {"system": "sarvam", "kind": "request_id"},
        {"system": "sarvam", "kind": "request_id", "value": "x", "extra": 1},
    ):
        assert_rejected3(model, transcript_doc(provider_call_ref=bad), field="provider_call_ref")
        assert not schema_accepts(model, transcript_doc(provider_call_ref=bad)), bad


def test_transcript_declared_language_shape_and_provenance() -> None:
    """N14 (ADR 0004 Decision 6g, I27): ``Transcript.declared_language`` is a
    ``LanguageDeclaration{language, source, declared_by, declared_at}``. ``language`` is a
    SupportedLanguage without a script subtag (``mr-IN`` and ``mr`` are valid, ``mr-Deva-IN`` is
    rejected, as is any language outside the nine); ``source`` is exactly ``user_declared``
    (an ``enum``); ``declared_by`` is a MachineKey without ``@``; ``declared_at`` is a required
    UTC instant. All four are required."""
    declaration = registered_class("LanguageDeclaration")
    assert set(declaration.model_fields) == {"language", "source", "declared_by", "declared_at"}
    assert not {"id", "schema_version", "created_at", "metadata"} & set(declaration.model_fields)
    for language in ("mr-IN", "mr", "hi-IN", "en-GB", "ta", "pa-IN"):
        assert (
            declaration.model_validate(language_declaration(language=language)).language == language
        )
    assert declaration.model_validate(language_declaration(language="MR-in")).language == "mr-IN"
    for bad in (
        "mr-Deva-IN",
        "mr-Deva",
        "pa-Guru",
        "hi-Latn",
        EXCLUDED_LANGUAGE_TAGS[0],
        EXCLUDED_LANGUAGE_TAGS[0] + "-IN",
        "und",
        "mul",
        "hindi",
        "mr_IN",
        "mr-AU",
        "",
        None,
    ):
        with pytest.raises(ValidationError):
            declaration.model_validate(language_declaration(language=bad))
    assert property_enum(declaration, "source") == ["user_declared"]
    assert not contains_key(schema_of(declaration), "const")
    for bad in ("observed", "User_Declared", "user", "", None):
        with pytest.raises(ValidationError):
            declaration.model_validate(language_declaration(source=bad))
    for good in ("cli", "tracer-cli:1.0", "acct_1234", "ops/oncall"):
        assert (
            declaration.model_validate(language_declaration(declared_by=good)).declared_by == good
        )
    for bad in ("a@b", "user@example.com", "@", "", "two words", DEVANAGARI, None):
        with pytest.raises(ValidationError):
            declaration.model_validate(language_declaration(declared_by=bad))
    loaded = declaration.model_validate(
        language_declaration(declared_at="2026-10-23T13:30:00+05:30")
    )
    assert json.loads(loaded.model_dump_json())["declared_at"] == "2026-10-23T08:00:00.000000Z"
    bad_times: list[Any] = ["2026-10-23T08:00:00", 1_790_000_000, "now", None]
    for bad_time in bad_times:
        with pytest.raises(ValidationError):
            declaration.model_validate(language_declaration(declared_at=bad_time))
    for missing in ("language", "source", "declared_by", "declared_at"):
        doc = {k: v for k, v in language_declaration().items() if k != missing}
        with pytest.raises(ValidationError):
            declaration.model_validate(doc)
    with pytest.raises(ValidationError):
        declaration.model_validate(language_declaration(confidence=1.0))
    transcript = make3(
        "Transcript", source="agent_export", declared_language=language_declaration()
    )
    assert transcript.declared_language.language == "mr-IN"
    again = entity_class3("Transcript").model_validate_json(transcript.model_dump_json())
    assert again.declared_language == transcript.declared_language


def test_declared_language_only_on_untagged_imported_transcript() -> None:
    """N14 (ADR 0004 Decision 6g, I28): a declaration is allowed only on an imported transcript
    (``agent_export``, ``agent_telemetry``, ``observer``, ``human_reference``) that has no
    transcript-level label: ``detected_language`` and ``detected_language_raw`` are both absent.
    It is rejected on ``our_stt`` and ``script`` transcripts (our own producer records the tag),
    and when either label is present, including ``und`` and a raw label that did not parse. It is
    never an observation: no observed field is filled from it."""
    transcript_model = entity_class3("Transcript")
    declared = language_declaration()
    for source in ("agent_export", "agent_telemetry", "observer", "human_reference"):
        loaded = make3("Transcript", source=source, declared_language=declared)
        assert loaded.declared_language.language == "mr-IN"
        assert loaded.detected_language is None  # never copied into an observed field
        assert loaded.detected_language_raw is None
        assert json.loads(loaded.model_dump_json())["detected_language"] is None
    for source in ("our_stt", "script"):
        error = assert_rejected3(
            transcript_model, transcript_doc(source=source, declared_language=declared)
        )
        assert "declared_language" in str(error) or "declar" in str(error).lower()
    base = transcript_doc(source="agent_export", declared_language=declared)
    for label in (
        {"detected_language": "mr-IN"},
        {"detected_language": "und"},
        {"detected_language_raw": "mr_IN"},
        {"detected_language_raw": "marathi"},
        {"detected_language_raw": ""},
        {"detected_language": "hi-IN", "detected_language_raw": "hi_IN"},
    ):
        assert_rejected3(transcript_model, {**base, **label})
    # a labelled imported transcript without a declaration is fine, and so is an untagged one
    assert (
        make3("Transcript", source="agent_export", detected_language="mr-IN").declared_language
        is None
    )
    assert make3("Transcript", source="agent_export").declared_language is None
    # the declaration does not touch the turns of the transcript
    transcript = make3("Transcript", source="agent_export", declared_language=declared)
    turn = _turn(transcript_id=str(transcript.id))
    _check(transcript, [turn])
    assert (turn.language, turn.language_raw) == (None, None)


# --------------------------------------------------------------------------- A-05 and A-02


CALIBRATION_REFERENCE_SECONDS = 0.05  # the calibration loop below on the development machine


def _calibrate() -> float:
    """Seconds for a fixed pure-Python workload that resembles validation (ADR 0011 Decision 6,
    P-30). It does not use the package, so a slower validator cannot hide behind it."""
    import re
    import uuid

    pattern = re.compile(
        r"turn_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    )
    sample = "turn_" + str(uuid.UUID(int=(1 << 127) | (7 << 76) | (2 << 62) | 12345))
    start = time.perf_counter()
    total = 0
    for index in range(60_000):
        if pattern.fullmatch(sample):
            total += uuid.UUID(sample[5:]).int & 1
        record = {"id": sample, "seq": index, "text": "x" * 24, "role": "agent"}
        total += len(record)
    assert total > 0
    return time.perf_counter() - start


def test_long_transcript_50k_turns_under_2s() -> None:
    """A-05, ADR 0011 Decision 6, P-30 (accepted): a 4 hour transcript of 50,000 timed turns
    validates, together with the transcript rules (unique ``seq``, the timeline origin), in under
    2 seconds. The limit is scaled by a calibration loop measured in the same test (floor 2 s), so
    a slow runner does not fail the test intermittently."""
    transcript = make3("Transcript", timeline_origin=_artifact_origin())
    count = 50_000
    docs = [
        turn_doc(
            str(transcript.id),
            seq,
            role="agent" if seq % 2 else "caller",
            text=NON_ASCII + " " + str(seq),
            timing_source="audio_alignment",
            start_ms=seq * 288,
            end_ms=seq * 288 + 250,
            language="hi-IN",
        )
        for seq in range(count)
    ]
    ratio = max(1.0, _calibrate() / CALIBRATION_REFERENCE_SECONDS)
    limit = 2.0 * ratio
    turn_class: Any = entity_class3("Turn")
    adapter: TypeAdapter[list[Any]] = TypeAdapter(list[turn_class])
    start = time.perf_counter()
    turns = adapter.validate_python(docs)
    _check(transcript, turns)
    elapsed = time.perf_counter() - start
    assert len(turns) == count
    assert turns[-1].end_ms == (count - 1) * 288 + 250
    assert elapsed < limit, (
        f"{elapsed:.2f}s for {count} turns (limit {limit:.2f}s, ratio {ratio:.2f})"
    )


def test_transcript_turn_and_tool_call_errors_do_not_echo_text() -> None:
    """N31 (resolved in revision 11; ADR 0007 Decision 8 (b); api-surface 22.9 item 8):
    ``Transcript``, ``Turn``, ``Segment``, ``ToolCallEvent`` and ``IngestWarning`` hold the text and
    raw payloads that are personal data on a human call, so they hide their input in validation
    errors. A rejected document whose text holds a marker string does not show it in ``str``,
    ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``, through
    ``model_validate`` and ``model_validate_json``, while the error still names the field.
    Seeds: a rule across fields that the document breaks (offsets in the wrong order, a truncated
    payload claimed to be complete), a missing required field (pydantic then shows the whole
    object as the input), and an unknown field that holds the marker. Proposed name (SCOPE
    section 5, P1-10b and P1-10c)."""
    mark = QUOTE_MARK
    transcript_id = new_id("tsc")
    models = {
        "Transcript": entity_class3("Transcript"),
        "Turn": entity_class3("Turn"),
        "Segment": registered_class("Segment"),
        "ToolCallEvent": entity_class3("ToolCallEvent"),
        "IngestWarning": registered_class("IngestWarning"),
    }
    for name, model in models.items():
        assert model.model_config.get("hide_input_in_errors") is True, name
    turn = turn_doc(transcript_id, text=mark)
    ordered = {"start_ms": 2000, "end_ms": 1000, "timing_source": "audio_alignment"}
    call = tool_call_doc(arguments_raw=mark, arguments=None, arguments_complete=True)
    seeds: list[tuple[str, dict[str, Any], str]] = [
        ("Transcript", {**transcript_doc(), "unexpected_note": mark}, "unexpected_note"),
        ("Turn", {**turn, **ordered}, "end_ms"),
        ("Turn", {k: v for k, v in turn.items() if k != "seq"}, "seq"),
        ("Turn", {**turn, "unexpected_note": mark}, "unexpected_note"),
        ("Segment", segment_doc(text=mark, start_ms=2000, end_ms=1000), "end_ms"),
        ("Segment", {**segment_doc(text=mark), "unexpected_note": mark}, "unexpected_note"),
        ("ToolCallEvent", call, "arguments_complete"),
        (
            "ToolCallEvent",
            {**tool_call_doc(arguments_raw=mark), "unexpected_note": mark},
            "unexpected_note",
        ),
        (
            "ToolCallEvent",
            {k: v for k, v in tool_call_doc(result_raw=mark).items() if k != "seq"},
            "seq",
        ),
        (
            "IngestWarning",
            {**ingest_warning_doc(raw=mark), "unexpected_note": mark},
            "unexpected_note",
        ),
        (
            "IngestWarning",
            {k: v for k, v in ingest_warning_doc(raw=mark).items() if k != "message"},
            "message",
        ),
    ]
    for name, doc, field in seeds:
        assert_input_hidden(models[name], doc, mark, field=field, label=field)


def test_transcript_turn_and_segment_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed
    ID, closed-set member, MachineKey and language tag on ``Transcript``, ``Turn`` and ``Segment``
    do not appear in ``str``, ``repr``, ``errors(include_input=False)`` or
    ``json(include_input=False)``, through ``model_validate`` and ``model_validate_json``, and the
    error is still located at the field. Proposed name (SCOPE section 5, P1-10b)."""
    transcript_id = new_id("tsc")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("typed ID", entity_class3("Transcript"), transcript_doc(id=marked_id("tsc")), "id"),
        ("ClosedSet", entity_class3("Transcript"), transcript_doc(source=marked_set()), "source"),
        (
            "MachineKey",
            entity_class3("Transcript"),
            transcript_doc(provider=marked_key()),
            "provider",
        ),
        (
            "BCP-47",
            entity_class3("Transcript"),
            transcript_doc(detected_language=marked_tag()),
            "detected_language",
        ),
        (
            "typed ID",
            entity_class3("Turn"),
            turn_doc(marked_id("tsc")),
            "transcript_id",
        ),
        (
            "ClosedSet",
            entity_class3("Transcript"),
            transcript_doc(timeline_origin={"kind": marked_set()}),
            "kind",
        ),
        (
            "typed ID",
            entity_class3("Transcript"),
            transcript_doc(timeline_origin={"kind": "artifact", "ref": marked_id("art")}),
            "ref",
        ),
        (
            "BCP-47",
            entity_class3("Transcript"),
            transcript_doc(
                source="agent_export", declared_language=language_declaration(language=marked_tag())
            ),
            "language",
        ),
        (
            "MachineKey",
            entity_class3("Transcript"),
            transcript_doc(
                source="agent_export",
                declared_language=language_declaration(declared_by=marked_key()),
            ),
            "declared_by",
        ),
        ("ClosedSet", entity_class3("Turn"), turn_doc(transcript_id, role=marked_set()), "role"),
        (
            "ClosedSet",
            entity_class3("Turn"),
            turn_doc(transcript_id, source=marked_set()),
            "source",
        ),
        (
            "ClosedSet",
            entity_class3("Turn"),
            turn_doc(transcript_id, timing_source=marked_set()),
            "timing_source",
        ),
        (
            "BCP-47",
            entity_class3("Turn"),
            turn_doc(transcript_id, language=marked_tag()),
            "language",
        ),
        (
            "ClosedSet",
            registered_class("Segment"),
            segment_doc(granularity=marked_set()),
            "granularity",
        ),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, QUOTE_MARK, field=field, label=label)
