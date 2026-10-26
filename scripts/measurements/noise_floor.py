# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Counts-only measurements over stored Tracer runs (P3-31 noise floor; gate condition C4).

GUARANTEE: COUNTS ONLY. The script opens each store read-only (``<dir>/tracer.sqlite`` through a
``file:...?mode=ro`` URI), reads the call text into memory to rebuild the comparison tokens, and
prints labels, integers and ``N of D`` cells. It never prints, logs or writes any text, token,
identifier or path (the only names printed are the ``LABEL`` strings the caller chose). An error
message holds a reason code and never a value. It sends nothing anywhere and needs no key.

Usage (stdlib and ``tracer_evalcore`` only)::

    uv run python scripts/measurements/noise_floor.py report \\
        R1=DIR1 R2=DIR2 R3=DIR3 \\
        --pair R1,R2 --pair R1,R3 \\
        --cuts R1,R3,WAV,25000,5000,20000,5000

    uv run python scripts/measurements/noise_floor.py ablate R1=DIR1 [R2=DIR2 ...]

``report`` prints one table, a row per measure and a column per run:

* export tokens, STT tokens, MER as ``(S+D+I) of (H+S+D+I)``, spans, flagged spans, flagged
  export tokens (export tokens wholly inside a flagged non-empty span), export turns with a flag;
* ``flagged zero-width spans``: flagged insertion spans (``start == end`` in the export turn).
  They hold no export token, so the token Jaccard cannot see them;
* ``flagged near_chunk_boundary`` as stored (ADR 0023 rule 10: true for a span that holds the
  first or last token of an STT turn, the first token of the first turn and the last token of the
  last turn included);
* RECORDING EDGE, defined exactly from the flags document: a span is a recording edge iff its
  ``near_chunk_boundary`` is true, it holds STT tokens (``stt_token_end > stt_token_start``) and
  ``stt_token_start == 0`` (it holds the first STT token of the recording) or ``stt_token_end ==
  tokens.stt`` (it holds the last). The row ``near cut excl. recording edges (doc)`` counts the
  flagged spans that are near a boundary and not a recording edge. A span that holds a recording
  edge and a real cut together counts as an edge in this definition;
* ``near cut excl. recording edges (strict)`` recomputes rule 10 on the rebuilt STT tokens while
  ignoring the first token of the recording and the last token of the recording, so such a span
  still counts when it holds a real cut. The unrestricted recomputation is checked against the
  stored ``near_chunk_boundary`` of every span and the script aborts on any difference;
* base rate: ``flagged among near-cut spans`` against ``flagged among other spans``, both as
  ``N of D``, for both definitions above (other spans include the recording-edge spans).

``--pair A,B`` (repeatable) prints ``both of either`` of the flagged export-token sets and, beside
it, the flagged zero-width span counts of A and B. ``--cuts A,B,WAV,MAX_A,SEARCH_A,MAX_B,SEARCH_B``
(repeatable; add ``--accept-provider-downmix`` for a two-channel file) runs
``tracer_evalcore.audio.chunker.chunk_pcm`` on the WAV (read-only) with each parameter pair and
prints how many cut positions of A coincide to the frame with a cut of B (``N of D``, D the cuts
of A) and the number of cuts of B. A cut position is the start frame of every chunk but the first.

``ablate`` re-scores the stored export and STT turns offline with the stored normaliser (ON) and
with a measurement-only identity key (OFF: no dropped character, no fold, no casefold, no NFC).
The identity key is registered in this process only and is never added to the product registry
file. Self-check: the ON recomputation must give the stored H, S, D, I, flag count and flagged
export-token count of the flags document; otherwise the script exits with an error.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import wave
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from tracer_evalcore import normalise
from tracer_evalcore.align import Alignment, align
from tracer_evalcore.audio.chunker import chunk_pcm
from tracer_evalcore.flags import STT_DISAGREEMENT, flag_spans
from tracer_evalcore.spans import extract_spans
from tracer_evalcore.tokens import TextTurn, TokenSequence, tokenise_turns

STORE_NAME = "tracer.sqlite"
FLAGS_KEY = "tracer.flags"
EXPORT_SOURCE = "agent_export"
STT_SOURCE = "our_stt"
IDENTITY_NORMALISER = "raw-measurement-only"


class MeasurementError(Exception):
    """A refusal; the message is a reason code and never holds call content."""


@dataclass(frozen=True)
class RunData:
    label: str
    export: TokenSequence
    stt: TokenSequence
    doc: Mapping[str, Any]
    export_turns: tuple[TextTurn, ...]
    stt_turns: tuple[TextTurn, ...]


@dataclass(frozen=True)
class RunMeasures:
    export_tokens: int
    stt_tokens: int
    mer: tuple[int, int]
    spans: int
    flagged_spans: int
    flagged_tokens: frozenset[int]
    turns_with_flag: tuple[int, int]
    zero_width_flagged: int
    near_boundary_flagged: int
    near_cut_doc: tuple[int, int]
    near_cut_doc_other: tuple[int, int]
    near_cut_strict: tuple[int, int]
    near_cut_strict_other: tuple[int, int]
    near_total: int
    near_stored: tuple[int, int]
    near_stored_other: tuple[int, int]
    edge_spans_doc: int


def _json(value: object) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, bytes):
        return json.loads(value.decode())
    raise MeasurementError("store_document_not_json")


def _turns(conn: sqlite3.Connection, source: str) -> tuple[TextTurn, ...]:
    ids = [row[0] for row in conn.execute("SELECT id FROM transcript WHERE source = ?", (source,))]
    if len(ids) != 1:
        raise MeasurementError(f"transcript_count_not_one:{source}:{len(ids)}")
    out: list[TextTurn] = []
    for (doc,) in conn.execute(
        "SELECT doc FROM turn WHERE transcript_id = ? ORDER BY seq", (ids[0],)
    ):
        parsed = _json(doc)
        out.append(TextTurn(parsed["seq"], parsed["text"]))
    return tuple(out)


def _flags_document(conn: sqlite3.Connection) -> Mapping[str, Any]:
    found: list[Mapping[str, Any]] = []
    for (doc,) in conn.execute("SELECT doc FROM metric_result"):
        metadata = _json(doc).get("metadata") or {}
        if FLAGS_KEY in metadata:
            found.append(
                _json(metadata[FLAGS_KEY])
                if isinstance(metadata[FLAGS_KEY], str | bytes)
                else metadata[FLAGS_KEY]
            )
    if not found:
        raise MeasurementError("no_flags_document")
    if any(item != found[0] for item in found[1:]):
        raise MeasurementError("flags_documents_differ")
    return found[0]


def load_run(label: str, directory: str) -> RunData:
    store = Path(directory) / STORE_NAME
    if not store.is_file():
        raise MeasurementError(f"no_store_in_directory:{label}")
    uri = f"file:{store.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        export_turns = _turns(conn, EXPORT_SOURCE)
        stt_turns = _turns(conn, STT_SOURCE)
        doc = _flags_document(conn)
    normaliser = doc["normaliser"]
    export = tokenise_turns(export_turns, normaliser)
    stt = tokenise_turns(stt_turns, normaliser)
    if (len(export.tokens), len(stt.tokens)) != (doc["tokens"]["export"], doc["tokens"]["stt"]):
        raise MeasurementError(f"token_counts_differ_from_flags_document:{label}")
    return RunData(label, export, stt, doc, export_turns, stt_turns)


def flagged_export_tokens(
    export: TokenSequence, spans: Sequence[tuple[int, int, int]]
) -> frozenset[int]:
    """Indices of export tokens wholly inside one of the (turn_seq, start, end) spans."""
    found: set[int] = set()
    for turn_seq, start, end in spans:
        if end <= start:
            continue
        for index, token in enumerate(export.tokens):
            if token.turn_seq == turn_seq and token.start >= start and token.end <= end:
                found.add(index)
    return frozenset(found)


def _near(stt: TokenSequence, start: int, end: int, *, ignore_recording_edges: bool) -> bool:
    """Rule 10 on token positions; optionally blind to the first and last token of the recording."""
    tokens = stt.tokens
    count = len(tokens)
    if start == end:
        return 0 < start < count and tokens[start - 1].turn_index != tokens[start].turn_index
    for index in range(start, end):
        turn = tokens[index].turn_index
        first = index != 0 if ignore_recording_edges else True
        last = index != count - 1 if ignore_recording_edges else True
        if first and (index == 0 or tokens[index - 1].turn_index != turn):
            return True
        if last and (index == count - 1 or tokens[index + 1].turn_index != turn):
            return True
    return False


def _is_edge_doc(span: Mapping[str, Any], stt_total: int) -> bool:
    start, end = span["stt_token_start"], span["stt_token_end"]
    return bool(span["near_chunk_boundary"]) and end > start and (start == 0 or end == stt_total)


def measure(run: RunData) -> RunMeasures:
    doc = run.doc
    spans: Sequence[Mapping[str, Any]] = doc["spans"]
    stt_total = len(run.stt.tokens)
    alignment = doc["alignment"]
    edits = alignment["substitutions"] + alignment["deletions"] + alignment["insertions"]
    flagged = [s for s in spans if s["flagged"]]
    tokens = flagged_export_tokens(
        run.export, [(s["turn_seq"], s["start"], s["end"]) for s in flagged]
    )
    if len(tokens) != doc["tokens"]["flagged_export"]:
        raise MeasurementError(f"flagged_tokens_differ_from_flags_document:{run.label}")
    full_mismatch = sum(
        1
        for s in spans
        if _near(run.stt, s["stt_token_start"], s["stt_token_end"], ignore_recording_edges=False)
        != s["near_chunk_boundary"]
    )
    if full_mismatch:
        raise MeasurementError(f"near_chunk_boundary_not_reproduced:{run.label}")

    def split(near: Sequence[bool]) -> tuple[tuple[int, int], tuple[int, int]]:
        in_cut = [s for s, n in zip(spans, near, strict=True) if n]
        other = [s for s, n in zip(spans, near, strict=True) if not n]
        return (
            (sum(1 for s in in_cut if s["flagged"]), len(in_cut)),
            (sum(1 for s in other if s["flagged"]), len(other)),
        )

    near_doc = [bool(s["near_chunk_boundary"]) and not _is_edge_doc(s, stt_total) for s in spans]
    near_strict = [
        _near(run.stt, s["stt_token_start"], s["stt_token_end"], ignore_recording_edges=True)
        for s in spans
    ]
    stored, stored_other = split([bool(s["near_chunk_boundary"]) for s in spans])
    if any((s["end"] == s["start"]) != (s["export_tokens"] == 0) for s in spans):
        raise MeasurementError(f"zero_width_span_inconsistent:{run.label}")
    cut_doc, other_doc = split(near_doc)
    cut_strict, other_strict = split(near_strict)
    return RunMeasures(
        export_tokens=len(run.export.tokens),
        stt_tokens=stt_total,
        mer=(edits, alignment["hits"] + edits),
        spans=len(spans),
        flagged_spans=len(flagged),
        flagged_tokens=tokens,
        turns_with_flag=(len({s["turn_seq"] for s in flagged}), len(run.export_turns)),
        zero_width_flagged=sum(1 for s in flagged if s["end"] == s["start"]),
        near_boundary_flagged=sum(1 for s in flagged if s["near_chunk_boundary"]),
        near_cut_doc=cut_doc,
        near_cut_doc_other=other_doc,
        near_cut_strict=cut_strict,
        near_cut_strict_other=other_strict,
        near_stored=stored,
        near_stored_other=stored_other,
        near_total=sum(1 for s in spans if s["near_chunk_boundary"]),
        edge_spans_doc=sum(1 for s in spans if _is_edge_doc(s, stt_total)),
    )


def _of(pair: tuple[int, int]) -> str:
    return f"{pair[0]} of {pair[1]}"


def _mer_cell(pair: tuple[int, int]) -> str:
    return f"{_of(pair)} ({Decimal(pair[0]) / Decimal(pair[1]):.4f})" if pair[1] else _of(pair)


def render_table(labels: Sequence[str], measures: Sequence[RunMeasures]) -> list[str]:
    rows: list[tuple[str, list[str]]] = [
        ("export tokens", [str(m.export_tokens) for m in measures]),
        ("STT tokens", [str(m.stt_tokens) for m in measures]),
        ("MER (S+D+I) of (H+S+D+I)", [_mer_cell(m.mer) for m in measures]),
        ("spans", [str(m.spans) for m in measures]),
        ("flagged spans", [str(m.flagged_spans) for m in measures]),
        ("flagged export tokens", [str(len(m.flagged_tokens)) for m in measures]),
        ("export turns with a flag", [_of(m.turns_with_flag) for m in measures]),
        ("flagged zero-width (insertion) spans", [str(m.zero_width_flagged) for m in measures]),
        ("spans near_chunk_boundary (stored)", [str(m.near_total) for m in measures]),
        ("flagged spans near_chunk_boundary", [str(m.near_boundary_flagged) for m in measures]),
        ("recording-edge spans (doc definition)", [str(m.edge_spans_doc) for m in measures]),
        (
            "flagged near cut excl. recording edges (doc)",
            [str(m.near_cut_doc[0]) for m in measures],
        ),
        (
            "flagged near cut excl. recording edges (strict)",
            [str(m.near_cut_strict[0]) for m in measures],
        ),
        (
            "base rate: flagged among near_chunk_boundary spans (stored, edges included)",
            [_of(m.near_stored) for m in measures],
        ),
        (
            "base rate: flagged among all other spans (stored)",
            [_of(m.near_stored_other) for m in measures],
        ),
        ("base rate: flagged among near-cut spans (doc)", [_of(m.near_cut_doc) for m in measures]),
        (
            "base rate: flagged among other spans (doc)",
            [_of(m.near_cut_doc_other) for m in measures],
        ),
        (
            "base rate: flagged among near-cut spans (strict)",
            [_of(m.near_cut_strict) for m in measures],
        ),
        (
            "base rate: flagged among other spans (strict)",
            [_of(m.near_cut_strict_other) for m in measures],
        ),
    ]
    first = max(len(name) for name, _ in rows)
    widths = [
        max(len(label), *(len(cells[i]) for _, cells in rows)) for i, label in enumerate(labels)
    ]
    lines = [
        "measure".ljust(first)
        + " | "
        + " | ".join(lab.ljust(w) for lab, w in zip(labels, widths, strict=True))
    ]
    lines.append("-" * len(lines[0]))
    for name, cells in rows:
        lines.append(
            name.ljust(first)
            + " | "
            + " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True))
        )
    return lines


def pair_line(a: str, b: str, ma: RunMeasures, mb: RunMeasures) -> str:
    if ma.export_tokens != mb.export_tokens:
        raise MeasurementError("pair_export_token_counts_differ")
    both = len(ma.flagged_tokens & mb.flagged_tokens)
    either = len(ma.flagged_tokens | mb.flagged_tokens)
    return (
        f"jaccard {a} vs {b}: {both} of {either}; flagged tokens {a} {len(ma.flagged_tokens)}"
        f" {b} {len(mb.flagged_tokens)}; flagged zero-width spans {a} {ma.zero_width_flagged}"
        f" {b} {mb.zero_width_flagged}"
    )


def cut_positions(path: str, max_chunk_ms: int, search_ms: int, *, downmix: bool) -> list[int]:
    with wave.open(path, "rb") as handle:
        if handle.getsampwidth() != 2:
            raise MeasurementError("wav_not_16_bit")
        rate, channels = handle.getframerate(), handle.getnchannels()
        pcm = handle.readframes(handle.getnframes())
    plan = chunk_pcm(
        pcm,
        sample_rate_hz=rate,
        channels=channels,
        max_chunk_ms=max_chunk_ms,
        search_ms=search_ms,
        accept_provider_downmix=downmix,
    )
    return [chunk.start_frame for chunk in plan.chunks[1:]]


def cuts_line(spec: str, labels: Sequence[str], *, downmix: bool) -> str:
    parts = spec.split(",")
    if len(parts) != 7:
        raise MeasurementError("cuts_spec_needs_7_fields")
    a, b, wav = parts[0], parts[1], parts[2]
    for label in (a, b):
        if label not in labels:
            raise MeasurementError("cuts_label_not_a_run")
    max_a, search_a, max_b, search_b = (int(x) for x in parts[3:])
    cuts_a = cut_positions(wav, max_a, search_a, downmix=downmix)
    cuts_b = set(cut_positions(wav, max_b, search_b, downmix=downmix))
    same = sum(1 for cut in cuts_a if cut in cuts_b)
    return (
        f"coincident cuts {a} vs {b}: {same} of {len(cuts_a)} cuts of {a}"
        f" (cuts of {b}: {len(cuts_b)})"
    )


def _register_identity() -> None:
    registry = normalise._REGISTRY
    if IDENTITY_NORMALISER not in registry:
        registry[IDENTITY_NORMALISER] = normalise.NormaliserSpec(
            id=IDENTITY_NORMALISER,
            ignorable=frozenset(),
            joiners=frozenset(),
            key=lambda text: text,
        )


def _rescore(run: RunData, normaliser: str) -> tuple[int, int, Alignment, int, frozenset[int]]:
    export = tokenise_turns(run.export_turns, normaliser)
    stt = tokenise_turns(run.stt_turns, normaliser)
    alignment = align(export, stt)
    threshold = Decimal(run.doc["flag_min_confidence"])
    flags = flag_spans(
        extract_spans(alignment, export, stt),
        export,
        stt,
        signal=STT_DISAGREEMENT,
        flag_min_confidence=threshold,
    )
    flagged = [f for f in flags if f.flagged]
    tokens = flagged_export_tokens(
        export, [(f.span.turn_seq, f.span.start, f.span.end) for f in flagged]
    )
    return len(export.tokens), len(stt.tokens), alignment, len(flagged), tokens


def ablate_line(run: RunData) -> str:
    _register_identity()
    n_on, s_on, al_on, f_on, t_on = _rescore(run, run.doc["normaliser"])
    stored = run.doc["alignment"]
    stored_flags = sum(1 for s in run.doc["spans"] if s["flagged"])
    stored_counts = (
        stored["hits"],
        stored["substitutions"],
        stored["deletions"],
        stored["insertions"],
    )
    on_counts = (al_on.hits, al_on.substitutions, al_on.deletions, al_on.insertions)
    if (
        on_counts != stored_counts
        or f_on != stored_flags
        or len(t_on) != run.doc["tokens"]["flagged_export"]
    ):
        raise MeasurementError(f"ablation_self_check_failed:{run.label}")
    n_off, s_off, al_off, f_off, t_off = _rescore(run, IDENTITY_NORMALISER)
    both, either = len(t_on & t_off), len(t_on | t_off)
    return (
        f"{run.label}: ON tokens {n_on}/{s_on} MER {al_on.edits} of {al_on.hits + al_on.edits}"
        f" flags {f_on} flagged tokens {len(t_on)} | OFF tokens {n_off}/{s_off}"
        f" MER {al_off.edits} of {al_off.hits + al_off.edits} flags {f_off}"
        f" flagged tokens {len(t_off)} | both {both} of either {either}"
        " | self-check ON = stored: ok"
    )


def _parse_runs(items: Sequence[str]) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    for item in items:
        label, sep, directory = item.partition("=")
        if not sep or not label or not directory:
            raise MeasurementError("run_must_be_LABEL=DIR")
        if label in {name for name, _ in runs}:
            raise MeasurementError("duplicate_label")
        runs.append((label, directory))
    return runs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Counts-only measurements over stored runs.")
    sub = parser.add_subparsers(dest="mode", required=True)
    report = sub.add_parser("report", help="counts table, Jaccard pairs, coincident cuts")
    report.add_argument("runs", nargs="+", metavar="LABEL=DIR")
    report.add_argument("--pair", action="append", default=[], metavar="A,B")
    report.add_argument(
        "--cuts", action="append", default=[], metavar="A,B,WAV,MAX_A,SEARCH_A,MAX_B,SEARCH_B"
    )
    report.add_argument("--accept-provider-downmix", action="store_true")
    ablate = sub.add_parser("ablate", help="offline normaliser on/off ablation")
    ablate.add_argument("runs", nargs="+", metavar="LABEL=DIR")
    return parser


def run_report(args: argparse.Namespace) -> list[str]:
    runs = [load_run(label, directory) for label, directory in _parse_runs(args.runs)]
    labels = [r.label for r in runs]
    measures = {r.label: measure(r) for r in runs}
    lines = render_table(labels, [measures[label] for label in labels])
    for spec in args.pair:
        a, _, b = spec.partition(",")
        if a not in measures or b not in measures:
            raise MeasurementError("pair_label_not_a_run")
        lines.append(pair_line(a, b, measures[a], measures[b]))
    for spec in args.cuts:
        lines.append(cuts_line(spec, labels, downmix=args.accept_provider_downmix))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "report":
            lines = run_report(args)
        else:
            lines = [ablate_line(load_run(label, d)) for label, d in _parse_runs(args.runs)]
    except MeasurementError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"error: {type(error).__name__}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
