# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-19: the ``tracer.flags/1`` document builder and its validator (ADR 0023 rule 12 and the
cross-field rules under "The tracer.flags/1 format").

The structural part is the JSON Schema of ``tracer_evalcore.flags.FLAGS_JSON_SCHEMA`` (batch C);
the cross-field part is ``validate_flags_document``. For each mutation of a good document the
tests also ask ``jsonschema`` (a test dependency, not a runtime one): a structural fault is
refused by both, a cross-field fault only by the validator, which is the point of the validator.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import jsonschema  # type: ignore[import-untyped,unused-ignore]
import pytest

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    EXPORT_LINES,
    NORMALISER,
    STT_PHRASES,
    STT_TEXTS,
    base_document,
    mod,
)

THRESHOLD = Decimal("0.3")
MODEL = "fake-model-1"


def _validate(document: Any, **override: Any) -> None:
    flagsdoc = mod("flagsdoc")
    arguments: dict[str, Any] = {"normaliser": NORMALISER, "threshold": THRESHOLD, "model": MODEL}
    arguments.update(override)
    flagsdoc.validate_flags_document(document, **arguments)


def _sequences() -> tuple[Any, Any]:
    tokens = mod("tokens")
    export = tokens.tokenise_turns(
        [tokens.TextTurn(seq, text) for seq, (_speaker, text) in enumerate(EXPORT_LINES)],
        NORMALISER,
    )
    stt = tokens.tokenise_turns(
        [
            tokens.TextTurn(
                seq,
                text,
                tuple(tokens.TimedSegment(t, a, b) for t, a, b in STT_PHRASES[seq]),
            )
            for seq, text in enumerate(STT_TEXTS)
        ],
        NORMALISER,
    )
    return export, stt


def test_flags_document_builder_gives_the_hand_computed_document() -> None:
    flagsdoc, flags, align, spans = mod("flagsdoc"), mod("flags"), mod("align"), mod("spans")
    export, stt = _sequences()
    alignment = align.align(export, stt)
    found = spans.extract_spans(alignment, export, stt)
    marked = flags.flag_spans(
        found, export, stt, signal=flags.STT_DISAGREEMENT, flag_min_confidence=0.3
    )
    document = flagsdoc.build_flags_document(
        normaliser=NORMALISER,
        provider="fake",
        model=MODEL,
        threshold=THRESHOLD,
        alignment=alignment,
        export=export,
        stt=stt,
        flags=marked,
    )
    assert document == base_document()
    _validate(document, export=export)
    # the builder does not alias what it was given and returns plain JSON types
    assert document is not base_document()
    import json

    assert json.loads(json.dumps(document)) == document
    assert document["flag_min_confidence"] == flags.format_threshold(THRESHOLD)


def test_flags_document_good_documents_validate_with_and_without_the_export() -> None:
    export, _stt = _sequences()
    _validate(base_document(), export=export)
    _validate(base_document())
    zero = copy.deepcopy(base_document("0"))
    for span in zero["spans"]:
        span["flagged"] = True
    zero["tokens"]["flagged_export"] = 2
    _validate(zero, threshold=Decimal("0"), export=export)
    one = copy.deepcopy(base_document("1"))
    one["spans"][0]["flagged"] = False
    _validate(one, threshold=Decimal(1), export=export)


def _set(document: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    changed = copy.deepcopy(document)
    node: Any = changed
    for step in path[:-1]:
        node = node[step]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return changed


_DELETE = object()
BIG = 2**53

STRUCTURE = [
    (("extra",), 1),
    (("format",), _DELETE),
    (("signal",), _DELETE),
    (("spans",), _DELETE),
    (("format",), "tracer.flags/2"),
    (("signal",), "second_signal"),
    (("normaliser",), "Mr-Deva/1"),
    (("normaliser",), "mr-Deva/0"),
    (("normaliser",), "mr-Deva/01"),
    (("normaliser",), 1),
    (("stt", "extra"), 1),
    (("stt", "model_source"), "reported"),
    (("stt", "provider"), "has space"),
    (("stt", "provider"), ""),
    (("stt", "model"), "m" * 65),
    (("stt", "model"), _DELETE),
    (("flag_min_confidence",), "0.30"),
    (("flag_min_confidence",), 0.3),
    (("flag_min_confidence",), "1.5"),
    (("flag_min_confidence",), "-0.1"),
    (("flag_min_confidence",), ""),
    (("alignment", "unit"), "character"),
    (("alignment", "hits"), -1),
    (("alignment", "hits"), True),
    (("alignment", "hits"), BIG),
    (("alignment", "extra"), 0),
    (("alignment", "insertions"), _DELETE),
    (("tokens", "export"), 0),
    (("tokens", "stt"), 0),
    (("tokens", "flagged_export"), -1),
    (("tokens", "extra"), 0),
    (("spans", 0, "extra"), 1),
    (("spans", 0, "edit_distance"), 0),
    (("spans", 0, "key_length"), 0),
    (("spans", 0, "confidence"), "0.2500"),
    (("spans", 0, "confidence"), 0.25),
    (("spans", 0, "confidence"), "1.0"),
    (("spans", 0, "flagged"), "false"),
    (("spans", 0, "flagged"), 0),
    (("spans", 0, "near_chunk_boundary"), None),
    (("spans", 0, "turn_seq"), -1),
    (("spans", 0, "start"), 1.5),
    (("spans", 1, "audio_start_ms"), 5),
    (("spans", 0, "audio_end_ms"), _DELETE),
    (("spans", 0, "substitutions"), _DELETE),
    (("spans",), {}),
]


@pytest.mark.parametrize(("path", "value"), STRUCTURE)
def test_flags_document_structural_faults_are_refused_by_schema_and_validator(
    path: tuple[Any, ...], value: Any
) -> None:
    flagsdoc, flags = mod("flagsdoc"), mod("flags")
    document = _set(base_document(), path, value)
    assert list(jsonschema.Draft202012Validator(flags.FLAGS_JSON_SCHEMA).iter_errors(document))
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(document)
    assert caught.value.code == "flags_shape"
    assert isinstance(caught.value, ValueError)


CROSS = [
    # (path, value, code): the schema accepts each document, the validator does not
    (("alignment", "hits"), 8, "flags_counts"),
    (("alignment", "insertions"), 0, "flags_counts"),
    (("alignment", "deletions"), 2, "flags_counts"),
    (("tokens", "flagged_export"), 2, "flags_counts"),
    (("tokens", "flagged_export"), 0, "flags_counts"),
    (("spans", 0, "substitutions"), 0, "flags_span"),
    (("spans", 1, "deletions"), 0, "flags_span"),
    (("spans", 2, "insertions"), 2, "flags_span"),
    (("spans", 0, "export_tokens"), 2, "flags_span"),
    (("spans", 2, "export_tokens"), 1, "flags_span"),
    (("spans", 0, "end"), 1, "flags_span"),
    (("spans", 0, "stt_token_end"), 4, "flags_span"),
    (("spans", 2, "end"), 5, "flags_span"),
    (("spans", 0, "edit_distance"), 5, "flags_span"),
    (("spans", 0, "confidence"), "0.5", "flags_span"),
    (("spans", 1, "confidence"), "0.9999", "flags_span"),
    (("spans", 0, "flagged"), True, "flags_span"),
    (("spans", 1, "flagged"), False, "flags_span"),
    (("spans", 0, "audio_start_ms"), 3000, "flags_span"),
    (("spans", 2, "audio_end_ms"), 100, "flags_span"),
    (("normaliser",), "mr-Deva/2", "flags_binding_mismatch"),
    (("flag_min_confidence",), "0.4", "flags_binding_mismatch"),
    (("stt", "model"), "other-model", "flags_binding_mismatch"),
]


@pytest.mark.parametrize(("path", "value", "code"), CROSS)
def test_flags_document_cross_field_faults_are_refused_by_the_validator_only(
    path: tuple[Any, ...], value: Any, code: str
) -> None:
    flagsdoc, flags = mod("flagsdoc"), mod("flags")
    document = _set(base_document(), path, value)
    schema_errors = list(
        jsonschema.Draft202012Validator(flags.FLAGS_JSON_SCHEMA).iter_errors(document)
    )
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(document)
    assert caught.value.code == code
    if code != "flags_shape":
        assert not schema_errors, schema_errors


def test_flags_document_counts_are_python_integers_not_floats_or_booleans() -> None:
    flagsdoc = mod("flagsdoc")
    for path, value in (
        (("alignment", "hits"), 9.0),
        (("tokens", "export"), 11.0),
        (("spans", 0, "start"), 12.0),
    ):
        with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
            _validate(_set(base_document(), path, value))
        assert caught.value.code == "flags_shape"


def test_flags_document_order_and_overlap_are_refused() -> None:
    flagsdoc = mod("flagsdoc")
    document = base_document()
    swapped = copy.deepcopy(document)
    swapped["spans"][1], swapped["spans"][2] = swapped["spans"][2], swapped["spans"][1]
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(swapped)
    assert caught.value.code == "flags_order"
    # two spans of one turn that overlap
    overlap = copy.deepcopy(document)
    overlap["spans"][1]["start"] = overlap["spans"][1]["start"]
    overlap["spans"].insert(2, copy.deepcopy(overlap["spans"][1]))
    overlap["spans"][2]["stt_token_start"] = 8
    overlap["spans"][2]["stt_token_end"] = 8
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(overlap)
    assert caught.value.code in {"flags_order", "flags_counts"}
    # STT ranges that run past the STT tokens or back over an earlier span
    beyond = _set(document, ("spans", 2, "stt_token_end"), 12)
    beyond = _set(beyond, ("spans", 2, "stt_token_start"), 11)
    with pytest.raises(flagsdoc.FlagsDocumentError):
        _validate(beyond)
    back = _set(document, ("spans", 1, "stt_token_start"), 1)
    back = _set(back, ("spans", 1, "stt_token_end"), 1)
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(back)
    assert caught.value.code == "flags_order"


def test_flags_document_is_checked_against_the_export_it_was_made_from() -> None:
    flagsdoc = mod("flagsdoc")
    export, _stt = _sequences()
    for path, value in (
        (("spans", 0, "turn_seq"), 9),
        (("spans", 0, "end"), 400),
        (("spans", 0, "start"), 1),  # inside a word: not on a token edge
        (("spans", 1, "end"), 25),  # a token edge that does not exist
    ):
        document = _set(base_document(), path, value)
        with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
            _validate(document, export=export)
        assert caught.value.code in {"flags_export_mismatch", "flags_span", "flags_order"}, path
    # without the export those three are not judged (the validator reads only the document)
    _validate(_set(base_document(), ("spans", 0, "start"), 1))


def test_flags_document_errors_carry_a_code_and_no_value() -> None:
    flagsdoc = mod("flagsdoc")
    marker = "ZZ VALUE MARKER"
    document = _set(base_document(), ("stt", "provider"), marker)
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(document)
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)
    assert str(caught.value).strip() != ""


def test_flags_document_validator_is_pure() -> None:
    document = base_document()
    before = copy.deepcopy(document)
    _validate(document)
    assert document == before
    with pytest.raises(TypeError):
        _validate(None)


def test_flags_document_totals_and_zero_width_rules_are_checked_apart() -> None:
    flagsdoc = mod("flagsdoc")
    # per-span counts stay consistent but the totals no longer match the alignment
    shifted = _set(
        _set(base_document(), ("alignment", "hits"), 8), ("alignment", "substitutions"), 2
    )
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(shifted)
    assert caught.value.code == "flags_counts"
    # a span with tokens but no width, and a span with width but no token
    start = base_document()["spans"][0]["start"]
    flat = _set(base_document(), ("spans", 0, "end"), start)
    with pytest.raises(flagsdoc.FlagsDocumentError) as caught:
        _validate(flat)
    assert caught.value.code == "flags_span"
    not_mappings: list[Any] = [[], "x", 5, ()]
    for not_a_mapping in not_mappings:
        with pytest.raises(TypeError):
            _validate(not_a_mapping)
