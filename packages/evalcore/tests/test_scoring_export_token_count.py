# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch G (security audit F2): the export token count the command checks before a paid send.

``tracer_evalcore.scoring.export_token_count(export_turns, normaliser)`` is the number of tokens the
aligner will see for the export side: the tokens of every export turn (both roles), in ``seq``
order, with the normaliser the snapshot names. The command refuses ``--stt sarvam`` before the send
when it is above ``ALIGN_MAX_TOKENS``, so a paid transcription is never thrown away by
``alignment_too_large`` on the export side. Contract: docs/phase3/api-surface-batch-g.md section 5.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    BASE_EXPORT,
    CREATED_AT,
    consent,
    export_text,
    retention,
    run_pipeline,
)

NORMALISER = "mr-Deva/1"


def _turns(text: str) -> list[Any]:
    from tracer_evalcore.importers.voicera import read_voicera_export
    from tracer_evalschema.privacy import PrivacyMarker
    from tracer_evalschema.transcript import LanguageDeclaration

    read = read_voicera_export(
        text,
        created_at=CREATED_AT,
        privacy=PrivacyMarker(human_subject=True, consent=consent(), retention=retention()),
        declared_language=LanguageDeclaration(
            language="mr", source="user_declared", declared_at=CREATED_AT
        ),
        accept_warnings=False,
    )
    return list(read.turns)


def _count(turns: Any) -> int:
    from tracer_evalcore.scoring import export_token_count

    return int(export_token_count(turns, NORMALISER))


def test_export_token_count_is_public_with_the_documented_signature() -> None:
    import tracer_evalcore.scoring as scoring

    assert "export_token_count" in scoring.__all__
    parameters = list(inspect.signature(scoring.export_token_count).parameters)
    assert parameters == ["export_turns", "normaliser"]


def test_export_token_count_equals_the_token_count_the_aligner_sees() -> None:
    # the number in the stored flags document is the number the aligner used (the oracle is the
    # pipeline itself: a count of whitespace words or of characters cannot match it)
    run = run_pipeline_in_memory()
    scored = next(r for r in run.bundle.results if r.status == "scored")
    document = scored.metadata["tracer.flags"]
    export = next(t for t in run.bundle.transcripts if t.source == "agent_export")
    turns = [t for t in run.bundle.turns if t.transcript_id == export.id]
    assert document["tokens"]["export"] == 11
    assert _count(turns) == document["tokens"]["export"]
    assert _count(_turns(BASE_EXPORT)) == 11


def run_pipeline_in_memory() -> Any:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        return run_pipeline(Path(folder))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([("assistant", "w0 w1 w2")], 3),
        # both roles are export turns: the aligner sees the caller lines too
        ([("assistant", "a b"), ("user", "c d e"), ("assistant", "f")], 6),
        # punctuation and the danda separate tokens; digits and letters of one word do not
        ([("assistant", "w0,w1,w2")], 3),
        ([("assistant", "a\u0964b"), ("user", "c-d/e")], 5),
        # zero width joiners are dropped inside a word (ADR 0021): one token, not two
        ([("assistant", "x\u200dy x\u200cy")], 2),
        # nothing to align
        ([("assistant", "...")], 0),
    ],
)
def test_export_token_count_by_hand(lines: list[tuple[str, str]], expected: int) -> None:
    assert _count(_turns(export_text(lines))) == expected


def test_export_token_count_is_independent_of_the_order_the_turns_are_given() -> None:
    turns = _turns(export_text([("assistant", "a b c"), ("user", "d"), ("assistant", "e f")]))
    assert _count(turns) == 6
    assert _count(list(reversed(turns))) == 6


def test_export_token_count_of_no_turn_is_zero() -> None:
    assert _count([]) == 0


def test_export_token_count_refuses_an_unknown_normaliser_and_has_no_default() -> None:
    from tracer_evalcore.scoring import export_token_count

    turns = _turns(BASE_EXPORT)
    with pytest.raises(ValueError):  # noqa: PT011 (message deliberately not pinned)
        export_token_count(turns, "nope/1")
    with pytest.raises(TypeError):
        export_token_count(turns)  # type: ignore[call-arg]


def test_the_alignment_cap_the_command_checks_against_is_ten_thousand_tokens() -> None:
    from tracer_evalcore.align import ALIGN_MAX_TOKENS, AlignmentTooLargeError, align_keys

    assert ALIGN_MAX_TOKENS == 10_000
    align_keys(["k"] * 10_000, ["k"], max_tokens=ALIGN_MAX_TOKENS)
    with pytest.raises(AlignmentTooLargeError):
        align_keys(["k"] * 10_001, ["k"], max_tokens=ALIGN_MAX_TOKENS)
