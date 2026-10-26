# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch F tests: the synthetic Marathi pair (P3-25 data).

Contract: docs/phase3/api-surface-batch-f.md. Nothing of the batch D or E modules is imported at
module level. The data are invented (provenance ``synthetic_hand_written``) and not natively
reviewed (BL-169): they prove plumbing, not Marathi quality. The audio is generated here from
``wav_recipe.json`` and never committed. No file of the private fixture, no key and no network is
used.

``independent_summary`` is a second implementation of the alignment rule of ADR 0023 (cost pair,
tie-break, backtrace from the end), written without ``tracer_evalcore.align`` and
``tracer_evalcore.spans``. It uses only the public tokeniser for the comparison keys, so a bug in
the aligner or in the span extraction cannot hide behind a copy of its own output.
"""

from __future__ import annotations

import json
import math
import re
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
SCOPE = ROOT / "docs" / "phase3" / "SCOPE.md"
#: Where the dev moves the data (contract 1); before the move the tests read the staged copy.
FINAL_DATA = ROOT / "docs" / "examples" / "synthetic-mr"
STAGING_DATA = ROOT / "docs" / "phase3" / "evidence" / "red" / "batch-f" / "staging" / "data"
DATA = FINAL_DATA if FINAL_DATA.is_dir() else STAGING_DATA
RUBRIC_EXAMPLE = ROOT / "docs" / "examples" / "rubric-mr.example.json"

NORMALISER = "mr-Deva/1"
#: variant name -> directory of its three scripted bodies, relative to DATA
VARIANT_DIRS = {"base": "", "nonmr_chunk": "stt_nonmr_chunk", "empty_chunk": "stt_empty_chunk"}
DATA_FILES = (
    "README.md",
    "chunk_plan.json",
    "expected_results.json",
    "export_synthetic_mr.txt",
    "stt_chunk_00.json",
    "stt_chunk_01.json",
    "stt_chunk_02.json",
    "wav_recipe.json",
    "stt_empty_chunk/stt_chunk_00.json",
    "stt_empty_chunk/stt_chunk_01.json",
    "stt_empty_chunk/stt_chunk_02.json",
    "stt_nonmr_chunk/stt_chunk_00.json",
    "stt_nonmr_chunk/stt_chunk_01.json",
    "stt_nonmr_chunk/stt_chunk_02.json",
)
CALL_ID = "e84b1d52-9c3a-47f0-a6d1-3f5b08c7a924"
EXPORT_TURNS = 22
EXPORT_TOKENS = 120
#: The planted differences of the data README (D01 to D19).
PLANTED_IDS = tuple(f"D{n:02d}" for n in range(1, 20))


# ------------------------------------------------------------------------ the data files


def data_path(name: str) -> Path:
    return DATA / name


def read_json(name: str) -> Any:
    return json.loads(data_path(name).read_text("ascii"))


def expected() -> dict[str, Any]:
    value = read_json("expected_results.json")
    assert isinstance(value, dict)
    return value


def recipe() -> dict[str, Any]:
    value = read_json("wav_recipe.json")
    assert isinstance(value, dict)
    return value


def chunk_plan() -> dict[str, Any]:
    value = read_json("chunk_plan.json")
    assert isinstance(value, dict)
    return value


def export_text() -> str:
    return data_path("export_synthetic_mr.txt").read_text("utf-8")


def body_name(variant: str, index: int) -> str:
    folder = VARIANT_DIRS[variant]
    return f"{folder}/stt_chunk_{index:02d}.json" if folder else f"stt_chunk_{index:02d}.json"


def body_bytes(variant: str, index: int) -> bytes:
    return data_path(body_name(variant, index)).read_bytes()


def body(variant: str, index: int) -> dict[str, Any]:
    value = json.loads(body_bytes(variant, index).decode("ascii"))
    assert isinstance(value, dict)
    return value


def write_replay_dir(variant: str, destination: Path) -> Path:
    """The three scripted bodies of ``variant`` as ``chunk-0000.json`` ... (the replay format)."""
    destination.mkdir(parents=True)
    for index in range(3):
        (destination / f"chunk-{index:04d}.json").write_bytes(body_bytes(variant, index))
    return destination


# ------------------------------------------------------------------------ the generated WAV


def make_wav(spec: dict[str, Any], path: Path) -> Path:
    """The recording of ``wav_recipe.json``: mono, 16-bit little-endian, tones and exact zeros."""
    total = spec["total_frames"]
    rate = spec["sample_rate_hz"]
    pcm = array("h", bytes(2 * total))
    for segment in spec["segments"]:
        if segment["kind"] != "tone":
            continue
        frequency, amplitude = segment["frequency_hz"], segment["amplitude"]
        for n in range(segment["start_frame"], segment["end_frame"]):
            pcm[n] = round(amplitude * math.sin(2 * math.pi * frequency * n / rate))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(spec["channels"])
        handle.setsampwidth(spec["sample_width_bytes"])
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


# ------------------------------------------------------------------------ the export and STT texts

_LINE = re.compile(r"^\[(?P<stamp>[^\]]+)\] (?P<speaker>assistant|user): (?P<text>.*)$")


def export_lines(text: str | None = None) -> list[tuple[str, str]]:
    """``(speaker, text)`` of every message line of the export (header and delimiter skipped)."""
    lines = (export_text() if text is None else text).splitlines()
    assert lines[0].startswith("call_id="), lines[:2]
    assert lines[1] == "---", lines[:2]
    found = []
    for line in lines[2:]:
        match = _LINE.match(line)
        assert match is not None, line
        found.append((match["speaker"], match["text"]))
    return found


def stt_texts(variant: str) -> list[str]:
    """The transcript of every non-empty chunk, in chunk order (an empty chunk makes no turn)."""
    texts = [str(body(variant, index)["transcript"]) for index in range(3)]
    return [t for t in texts if t.strip()]


# ------------------------------------------------------------------------ independent alignment


def independent_align(
    export_keys: list[str], stt_keys: list[str]
) -> list[tuple[str, int | None, int | None]]:
    """Ops ``(M|S|D|I, export index, stt index)`` by the rule of ADR 0023, written from scratch.

    The cost is the pair (edits, deletions plus insertions) compared lexicographically: match
    (0, 0), substitution (1, 0), deletion (1, 1), insertion (1, 1). Among alignments of minimal
    cost the backtrace from the end prefers match, then substitution, then deletion, then insertion.
    """
    n, m = len(export_keys), len(stt_keys)
    table: list[list[tuple[int, int]]] = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0] = (i, i)
    for j in range(1, m + 1):
        table[0][j] = (j, j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diagonal = table[i - 1][j - 1]
            if export_keys[i - 1] == stt_keys[j - 1]:
                best = diagonal
            else:
                best = (diagonal[0] + 1, diagonal[1])
            up, left = table[i - 1][j], table[i][j - 1]
            best = min(best, (up[0] + 1, up[1] + 1), (left[0] + 1, left[1] + 1))
            table[i][j] = best
    ops: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        here = table[i][j]
        if i and j and export_keys[i - 1] == stt_keys[j - 1] and table[i - 1][j - 1] == here:
            ops.append(("M", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif (
            i
            and j
            and export_keys[i - 1] != stt_keys[j - 1]
            and (
                table[i - 1][j - 1][0] + 1,
                table[i - 1][j - 1][1],
            )
            == here
        ):
            ops.append(("S", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and (table[i - 1][j][0] + 1, table[i - 1][j][1] + 1) == here:
            ops.append(("D", i - 1, None))
            i -= 1
        else:
            assert j > 0
            assert (table[i][j - 1][0] + 1, table[i][j - 1][1] + 1) == here
            ops.append(("I", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


@dataclass(frozen=True)
class IndependentSpan:
    """A maximal run of non-match ops, cut at export turn boundaries (export tokens only)."""

    turn_seq: int
    start: int
    end: int
    export_tokens: int
    substitutions: int
    deletions: int


@dataclass(frozen=True)
class IndependentSummary:
    export_tokens: int
    stt_tokens: int
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    spans: tuple[IndependentSpan, ...]
    insertion_only_runs: int
    ops: tuple[tuple[str, int | None, int | None], ...]

    @property
    def edits(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def independent_summary(variant: str) -> IndependentSummary:
    """Tokenise with the public tokeniser, align and cut spans with the code of this module."""
    from tracer_evalcore.tokens import TextTurn, tokenise_turns

    export_turns = [TextTurn(seq=i, text=text) for i, (_s, text) in enumerate(export_lines())]
    stt_turns = [TextTurn(seq=i, text=text) for i, text in enumerate(stt_texts(variant))]
    export = tokenise_turns(export_turns, NORMALISER)
    stt = tokenise_turns(stt_turns, NORMALISER)
    ops = independent_align(list(export.keys), list(stt.keys))
    counts = {kind: sum(1 for op in ops if op[0] == kind) for kind in "MSDI"}
    runs: list[list[tuple[str, int | None, int | None]]] = []
    last = -2
    for position, op in enumerate(ops):
        if op[0] == "M":
            continue
        if runs and position == last + 1:
            runs[-1].append(op)
        else:
            runs.append([op])
        last = position
    spans: list[IndependentSpan] = []
    insertion_only = 0
    for run in runs:
        tokens = [export.tokens[op[1]] for op in run if op[1] is not None]
        if not tokens:
            insertion_only += 1
            continue
        for seq in sorted({t.turn_seq for t in tokens}):
            mine = [t for t in tokens if t.turn_seq == seq]
            kinds = [
                op[0] for op in run if op[1] is not None and export.tokens[op[1]].turn_seq == seq
            ]
            spans.append(
                IndependentSpan(
                    turn_seq=seq,
                    start=min(t.start for t in mine),
                    end=max(t.end for t in mine),
                    export_tokens=len(mine),
                    substitutions=kinds.count("S"),
                    deletions=kinds.count("D"),
                )
            )
    return IndependentSummary(
        export_tokens=len(export.keys),
        stt_tokens=len(stt.keys),
        hits=counts["M"],
        substitutions=counts["S"],
        deletions=counts["D"],
        insertions=counts["I"],
        spans=tuple(spans),
        insertion_only_runs=insertion_only,
        ops=tuple(ops),
    )


# ------------------------------------------------------------------------ the expected spans


def expected_variant(name: str) -> dict[str, Any]:
    value = expected()["variants"][name]
    assert isinstance(value, dict)
    return value


def expected_spans(name: str) -> list[dict[str, Any]]:
    value = expected_variant(name)["spans"]
    assert isinstance(value, list)
    return value


SPAN_FIELDS = (
    "turn_seq",
    "start",
    "end",
    "export_tokens",
    "stt_token_start",
    "stt_token_end",
    "substitutions",
    "deletions",
    "insertions",
    "edit_distance",
    "key_length",
    "confidence",
    "flagged",
    "near_chunk_boundary",
)
AUDIO_FIELDS = ("audio_start_ms", "audio_end_ms")


def span_document(span: dict[str, Any]) -> dict[str, Any]:
    """The member set of a ``tracer.flags/1`` span of the expected file (``id`` dropped)."""
    keep = (*SPAN_FIELDS, *(f for f in AUDIO_FIELDS if f in span))
    return {field: span[field] for field in keep}
