# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-26: the offline end to end run of ``tracer score`` over the synthetic Marathi pair.

A generated 68 s recording (tones and exact silences), a 22-turn VoicEra-format export with
planted differences, and scripted Sarvam bodies replayed through ``--stt fake:<dir>`` (and, for
one test, through the Sarvam engine over an ``httpx.MockTransport``). The expected numbers are
exact and written here as literals: 109 hits, 9 substitutions, 2 deletions and 2 insertions, a
match error rate of 13/122, 10 spans of which 8 are flagged, 9 flagged tokens of 120 (3/40). The
span table comes from ``expected_results.json``, which an independent alignment reproduces
(``test_synthetic_pair.py``). The data are invented and not natively reviewed (BL-169): this proves
plumbing, not Marathi quality, and says nothing about Sarvam. Contract:
docs/phase3/api-surface-batch-f.md section 3 and 4.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    KEY_CANARY,
    REPORT_NAME,
    CliRun,
    data_support,
    flags_document,
    git,
    guard_repo,
    load_stored_run,
    parse_html,
    report_mod,
    results_by_key,
    run_cli,
    run_guard,
    score_argv,
    tree_bytes,
    unwrap,
)

MER_KEY = "stt_export_agreement_mer"
COUNT_KEY = "stt_export_flag_count"
SHARE_KEY = "stt_export_flagged_token_share"

# Marathi words as escapes (this file is ASCII). Each pair is (export text, STT text) of a planted
# difference of the data README.
PACHSHE = "\u092a\u093e\u091a\u0936\u0947"
JAST = "\u091c\u093e\u0938\u094d\u0924"
KHUP = "\u0916\u0942\u092a"
EK = "\u090f\u0915"
TEEN_KAMKAJ_DIVAS = (
    "\u0924\u0940\u0928 \u0915\u093e\u092e\u0915\u093e\u091c\u093e\u091a\u094d\u092f\u093e "
    "\u0926\u093f\u0935\u0938\u093e\u0902\u0924"
)
BHARPUR_THODE_ROKH = "\u092d\u0930\u092a\u0942\u0930 \u0925\u094b\u0921\u0947 \u0930\u094b\u0916"
OTPI = "\u0913\u091f\u0940\u092a\u0940"
TAL = "\u0924\u0933"
TAL_ASCII_L = "\u0924\u0932"
KA = "\u0915\u093e"
NAKKI = "\u0928\u0915\u094d\u0915\u0940"
THAMB = "\u0925\u093e\u0902\u092c"

#: The eight flagged spans of the base variant: (turn, start, end, export text, heard text).
BASE_FLAGGED = (
    (4, 13, 18, PACHSHE, "500"),
    (5, 11, 16, JAST, KHUP),
    (6, 17, 19, EK, None),
    (10, 0, 23, TEEN_KAMKAJ_DIVAS, BHARPUR_THODE_ROKH),
    (12, 12, 15, "OTP", OTPI),
    (14, 17, 19, TAL, TAL_ASCII_L),
    (14, 35, 37, KA, None),
    (16, 21, 21, None, NAKKI),
)
#: The two recorded spans under the threshold (histogram only, never highlighted).
BASE_UNFLAGGED = ((7, 12, 18), (17, 8, 11))
FULL_INPUTS = ["audio", "transcript", "agent_transcript"]


@dataclass
class World:
    root: Path
    wav: Path
    export: Path
    replay: dict[str, Path]
    runs: dict[str, CliRun] = field(default_factory=dict)

    def run(self, variant: str, tag: str | None = None) -> CliRun:
        """``tracer score`` over a variant, once per (variant, tag); the guard of the calling
        test is active (the run is lazy, never made in a fixture)."""
        key = tag or variant
        if key not in self.runs:
            out = self.root / f"out-{key}"
            argv = score_argv(self.wav, self.export, out, stt=f"fake:{self.replay[variant]}")
            self.runs[key] = run_cli(argv, out_dir=out, wav=self.wav)
        return self.runs[key]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    support = data_support()
    root = tmp_path_factory.mktemp("e2e")
    inputs = root / "in"
    inputs.mkdir()
    wav = support.make_wav(support.recipe(), inputs / "synthetic_mr.wav")
    export = inputs / "export.txt"
    export.write_bytes(support.data_path("export_synthetic_mr.txt").read_bytes())
    replay = {
        variant: support.write_replay_dir(variant, root / f"replay-{variant}")
        for variant in support.VARIANT_DIRS
    }
    return World(root, wav, export, replay)


def _path_line(run: CliRun, name: str) -> str:
    """The ``store:`` or ``report:`` line, ``<out>/<file>`` as typed (batch E contract 3.1)."""
    return f"{name}: {run.out_dir}/{REPORT_NAME if name == 'report' else 'tracer.sqlite'}"


def _bundle(run: CliRun) -> Any:
    return load_stored_run(run.out_dir, run.value("bundle"))


def _lines_without_bundle(run: CliRun) -> list[str]:
    return [line for line in run.lines if not line.startswith("bundle: ")]


# ------------------------------------------------------------------------------ the base variant


def test_e2e_synthetic_mr_exact_outputs(world: World) -> None:
    support = data_support()
    run = world.run("base")
    assert run.code == 0, run.err
    assert run.err == ""
    assert _lines_without_bundle(run) == [
        "result: scored",
        "language declared by user: mr",
        "stt provider: replay",
        "stt model: recorded-responses (as requested)",
        "normaliser: mr-Deva/1",
        f"agreement distance (MER): {13 / 122!r} (13 of 122; H 109, S 9, D 2, I 2)",
        "tokens: 120 in the export, 120 in the stt transcript",
        "flags: 8 (confidence at or above 0.5)",
        f"flagged share of export tokens: 9 of 120 ({9 / 120!r})",
        "export turns with a flag: 7 of 22",
        "review budget: 1.0 (no limit)",
        "stt usage: none",
        "cost: not available (no_usage)",
        "ingest warnings: 0",
        _path_line(run, "store"),
        _path_line(run, "report"),
    ]
    assert repr(13 / 122) == "0.10655737704918032"
    assert repr(9 / 120) == "0.075"
    # a replay is offline and writes exactly the store and the report (no call log, no key)
    assert sorted(tree_bytes(run.out_dir)) == [REPORT_NAME, "tracer.sqlite"]
    assert (run.out_dir / "tracer.sqlite").stat().st_mode & 0o777 == 0o600
    assert run.report_path.stat().st_mode & 0o777 == 0o600

    bundle = _bundle(run)
    # the Run: recorded, T0, the inputs that exist, no start time, no external reference
    assert bundle.run.mode == "recorded"
    assert bundle.run.caller_origin == "human"
    assert bundle.run.observed_tier == "T0"
    assert bundle.run.available_inputs == FULL_INPUTS
    assert bundle.run.started_at is None
    assert bundle.run.external_refs == []
    assert bundle.run.ingest_warnings == []
    assert bundle.verdict is None
    # the transcripts: the export with its declaration and call id, the replayed STT
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    assert export.provider_call_ref.value == support.CALL_ID
    assert (export.declared_language.language, export.declared_language.source) == (
        "mr",
        "user_declared",
    )
    assert (stt.provider, stt.model) == ("replay", "recorded-responses")
    assert stt.metadata["tracer.chunk_plan"] == support.chunk_plan()["as_metadata"]
    assert stt.metadata["tracer.empty_chunks"] == {"count": 0, "indexes": []}
    export_turns = [t for t in bundle.turns if t.transcript_id == export.id]
    stt_turns = [t for t in bundle.turns if t.transcript_id == stt.id]
    assert len(export_turns) == 22
    assert [t.role for t in export_turns] == ["agent", "caller"] * 11
    assert [t.seq for t in export_turns] == list(range(22))
    assert len(stt_turns) == 3
    assert all(t.language is not None and t.language.split("-")[0] == "mr" for t in stt_turns)
    assert bundle.usage_records == []  # a replay makes no request and claims no usage

    # the three results: exact values, one shared flags document
    results = results_by_key(bundle)
    assert list(results) == [MER_KEY, COUNT_KEY, SHARE_KEY]
    assert results[MER_KEY].value == 13 / 122
    assert results[COUNT_KEY].value == 8
    assert results[SHARE_KEY].value == 0.075
    assert results[SHARE_KEY].passed is True  # the placeholder budget 1.0 cannot fail
    assert results[MER_KEY].passed is None
    assert results[COUNT_KEY].passed is None
    for result in results.values():
        assert result.status == "scored"
        assert result.language_source == "declared"
        assert result.declared_language_transcript_ids == [export.id]
        assert result.transcript_ids == [export.id, stt.id]
        assert result.metadata["tracer.flags"] == flags_document(bundle)
    document = flags_document(bundle)
    assert document["format"] == "tracer.flags/1"
    assert document["signal"] == "stt_disagreement"
    assert document["normaliser"] == "mr-Deva/1"
    assert document["flag_min_confidence"] == "0.5"
    assert document["stt"] == {
        "provider": "replay",
        "model": "recorded-responses",
        "model_source": "requested",
    }
    assert document["alignment"] == {
        "unit": "word",
        "hits": 109,
        "substitutions": 9,
        "deletions": 2,
        "insertions": 2,
    }
    assert document["tokens"] == {"export": 120, "stt": 120, "flagged_export": 9}
    spans = document["spans"]
    assert len(spans) == 10
    assert [(s["turn_seq"], s["start"], s["end"]) for s in spans if s["flagged"]] == [
        (turn, start, end) for turn, start, end, _e, _h in BASE_FLAGGED
    ]
    assert [(s["turn_seq"], s["start"], s["end"]) for s in spans if not s["flagged"]] == list(
        BASE_UNFLAGGED
    )
    assert spans == [support.span_document(s) for s in support.expected_spans("base")]
    # the planted near-boundary spans: the split word (not flagged) and the cut word (flagged)
    near = [(s["turn_seq"], s["flagged"]) for s in spans if s["near_chunk_boundary"]]
    assert near == [(7, False), (14, True)]
    # evidence: the distinct flagged export turns by seq, then the distinct audio ranges
    seq_of = {t.id: t.seq for t in bundle.turns}
    for result in results.values():
        assert [seq_of[r.ref_id] for r in result.evidence_refs if r.kind == "turn"] == [
            4, 5, 6, 10, 12, 14, 16
        ]  # fmt: skip
        assert [
            (r.start_ms, r.end_ms) for r in result.evidence_refs if r.kind == "artifact_range"
        ] == [
            (11300, 13800), (14100, 16600), (29590, 32290), (35590, 38290), (41590, 44290),
            (49180, 51880),
        ]  # fmt: skip


def test_e2e_synthetic_mr_report_shows_the_exact_flags(world: World) -> None:
    run = world.run("base")
    text = run.report_path.read_text("utf-8")
    # the marker the repository guard reads (P3-34), once, in the head before style and script
    marker = '<meta name="tracer-report" content="1">'
    assert text.count(marker) == 1
    assert text.index(marker) < text.index("<style>") < text.index("<script>")
    page = parse_html(text)
    assert page.by_id("status").attrs["data-outcome"] == "scored"
    agreement = page.by_id("agreement")
    assert float(agreement.attrs["data-value"]) == 13 / 122
    counts = ("hits", "substitutions", "deletions", "insertions")
    assert [agreement.attrs[f"data-{k}"] for k in counts] == [
        "109", "9", "2", "2"
    ]  # fmt: skip
    assert page.by_id("flag-count").attrs["data-value"] == "8"
    share = page.by_id("flagged-share")
    assert (share.attrs["data-numerator"], share.attrs["data-denominator"]) == ("9", "120")
    assert page.by_id("review-budget").attrs["data-no-limit"] == "true"
    # one row per flagged span, in order, with what the export says and what the STT heard
    rows = page.by_id("flags-table").find_all("tr", "flag-row")
    assert len(rows) == 8
    for row, (turn, start, end, export_text, heard) in zip(rows, BASE_FLAGGED, strict=True):
        assert row.attrs["data-turn-seq"] == str(turn)
        cells = {c.classes[0]: c for c in row.find_all("td")}
        assert cells["turn"].text() == f"turn {turn}"
        assert cells["role"].text() == ("agent" if turn % 2 == 0 else "caller")
        assert cells["offsets"].text() == f"{start}-{end}"
        if export_text is None:
            assert cells["exportside"].find_all("span", "none")[0].text() == "not in export"
        else:
            pieces = cells["exportside"].find_all("span", "export-piece")
            assert [unwrap(p.text()) for p in pieces] == [export_text]
        if heard is None:
            assert cells["heard"].find_all("span", "none")[0].text() == "nothing heard"
        else:
            pieces = cells["heard"].find_all("span", "heard-piece")
            assert [unwrap(p.text()) for p in pieces] == [heard]
    # the two spans under the threshold are never highlighted, only counted in the histogram
    marks = page.find_all("mark", "flag")
    assert len(marks) == 8
    assert [(m.attrs["data-turn"], m.attrs["data-start"], m.attrs["data-end"]) for m in marks] == [
        (str(t), str(s), str(e)) for t, s, e, _x, _h in BASE_FLAGGED
    ]
    assert [m.text() for m in marks if "gap" in m.classes] == ["[+]"]
    # the histogram: bin = min(9, 10 * edit_distance // key_length) of all 10 spans
    bins = page.by_id("histogram").find_all("tr", "bin")
    assert [b.attrs["data-spans"] for b in bins] == [
        "0",
        "1",
        "0",
        "1",
        "0",
        "1",
        "0",
        "0",
        "0",
        "7",
    ]
    assert [b.attrs["data-flagged"] for b in bins] == [
        "0",
        "0",
        "0",
        "0",
        "0",
        "1",
        "0",
        "0",
        "0",
        "7",
    ]
    assert page.by_id("histogram-total").text() == "spans: 10"


def test_e2e_report_and_store_consistent(world: World) -> None:
    support = data_support()
    run = world.run("base")
    bundle = _bundle(run)
    report_text = run.report_path.read_text("utf-8")
    page = parse_html(report_text)
    # 1. the report on disk is the report of the stored run, byte for byte
    render = report_mod()
    src = render.relative_audio_src(run.wav.resolve(), run.out_dir.resolve())
    assert src == "../in/synthetic_mr.wav"
    again = render.render_report(bundle, audio=render.RelativeAudio(src=src))
    assert again.encode("utf-8") == run.report_path.read_bytes()
    assert page.by_id("player").attrs["src"] == src
    # 2. stdout, report and store carry the same numbers
    results = results_by_key(bundle)
    document = flags_document(bundle)
    mer = results[MER_KEY]
    alignment = document["alignment"]
    edits = alignment["substitutions"] + alignment["deletions"] + alignment["insertions"]
    total = alignment["hits"] + edits
    assert run.value("agreement distance (MER)") == (
        f"{mer.value!r} ({edits} of {total}; H {alignment['hits']}, "
        f"S {alignment['substitutions']}, "
        f"D {alignment['deletions']}, I {alignment['insertions']})"
    )
    agreement = page.by_id("agreement")
    assert float(agreement.attrs["data-value"]) == mer.value
    assert agreement.attrs["data-hits"] == str(alignment["hits"])
    assert page.by_id("flag-count").attrs["data-value"] == str(results[COUNT_KEY].value)
    flagged_tokens = document["tokens"]["flagged_export"]
    share = page.by_id("flagged-share")
    assert share.attrs["data-numerator"] == str(flagged_tokens)
    assert share.attrs["data-denominator"] == str(document["tokens"]["export"])
    assert run.value("flagged share of export tokens") == (
        f"{flagged_tokens} of {document['tokens']['export']} ({results[SHARE_KEY].value!r})"
    )
    assert page.by_id("scorer-version").text()
    assert page.by_id("normaliser").text() == document["normaliser"]
    assert page.by_id("threshold").text() == document["flag_min_confidence"]
    # 3. every flagged row of the report is a flagged span of the stored document, in order
    flagged = [s for s in document["spans"] if s["flagged"]]
    rows = page.by_id("flags-table").find_all("tr", "flag-row")
    assert [r.attrs["data-turn-seq"] for r in rows] == [str(s["turn_seq"]) for s in flagged]
    assert [(r.attrs["data-stt-token-start"], r.attrs["data-stt-token-end"]) for r in rows] == [
        (str(s["stt_token_start"]), str(s["stt_token_end"])) for s in flagged
    ]
    # 4. the text shown for a span is the stored turn text at the span's offsets, and the STT text
    #    is the stored STT turn text at the span's token range (a second route to the same text)
    from tracer_evalcore.tokens import TextTurn, tokenise_turns

    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    export_turns = {t.seq: t.text for t in bundle.turns if t.transcript_id == export.id}
    stt_turns = [t for t in bundle.turns if t.transcript_id == stt.id]
    stt_tokens = tokenise_turns(
        [TextTurn(seq=t.seq, text=t.text) for t in stt_turns], document["normaliser"]
    ).tokens
    for row, span in zip(rows, flagged, strict=True):
        cells = {c.classes[0]: c for c in row.find_all("td")}
        if span["export_tokens"]:
            shown = [unwrap(p.text()) for p in cells["exportside"].find_all("span", "export-piece")]
            assert shown == [export_turns[span["turn_seq"]][span["start"] : span["end"]]]
        mine = stt_tokens[span["stt_token_start"] : span["stt_token_end"]]
        shown_heard = [unwrap(p.text()) for p in cells["heard"].find_all("span", "heard-piece")]
        if not mine:
            assert shown_heard == []
            continue
        by_turn: dict[int, list[Any]] = {}
        for token in mine:
            by_turn.setdefault(token.turn_seq, []).append(token)
        wanted = [
            next(t.text for t in stt_turns if t.seq == seq)[tokens[0].start : tokens[-1].end]
            for seq, tokens in by_turn.items()
        ]
        assert shown_heard == wanted
    # 5. the export turns are all in the report, in order, with the stored text
    items = page.by_id("transcript").find_all("li", "turn")
    assert [li.attrs["data-seq"] for li in items] == [str(i) for i in range(22)]
    for item in items:
        shown_text = "".join(unwrap(n.text()) for n in item.find_all(None, "ut"))
        assert shown_text == export_turns[int(item.attrs["data-seq"])]
    # 6. the report holds no record id, no call id, no absolute path and no text of the other side
    assert not re.search(r"\b[a-z]{2,6}_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-", report_text)
    assert support.CALL_ID not in report_text
    assert str(run.out_dir) not in report_text
    assert str(run.wav.parent) not in report_text
    assert "file:" not in report_text
    # 7. the store holds the rest (the artifact keeps the absolute file URI; the report does not)
    artifact = bundle.artifacts[0]
    assert artifact.uri.startswith("file:///")
    assert artifact.sha256 == page.by_id("audio-facts").attrs["data-sha256"]


def test_e2e_report_bytes_are_stable_across_runs(world: World) -> None:
    first = world.run("base")
    second = world.run("base", tag="base-again")
    assert second.code == 0
    assert first.report_path.read_bytes() != b""
    # the report holds no id and no path, so a second run with the same inputs and clock gives
    # the same bytes (the out directory differs, the relative audio reference does not)
    assert first.report_path.read_bytes() == second.report_path.read_bytes()
    assert _lines_without_bundle(first)[:-2] == _lines_without_bundle(second)[:-2]
    # both runs are real runs of the same call: two stores, two bundles with distinct ids
    assert first.value("bundle") != second.value("bundle")


# ------------------------------------------------------------------------------ the variants


def test_e2e_nonmr_chunk_is_not_scored_exit_2_with_three_errors(world: World) -> None:
    support = data_support()
    run = world.run("nonmr_chunk")
    assert run.code == 2, run.err
    assert run.err == ""
    assert _lines_without_bundle(run) == [
        "result: not scored",
        "language declared by user: mr",
        "not scored: unsupported language detected",
        "stt usage: none",
        "cost: not available (no_usage)",
        "ingest warnings: 0",
        _path_line(run, "store"),
        _path_line(run, "report"),
    ]
    bundle = _bundle(run)
    results = results_by_key(bundle)
    want = support.expected_variant("nonmr_chunk")["results"]
    assert list(results) == list(want)
    for key, result in results.items():
        assert result.status == "error"  # not a score and not a zero, ADR 0023 rule 14 (D7)
        assert result.error.code == want[key]["error_code"] == "stt_unsupported_language"
        assert result.error.message == want[key]["error_message"]
        assert result.value is None
        assert result.passed is None
        assert result.metadata == {}
        assert result.evidence_refs == []
        assert result.language_source == "declared"
    assert bundle.run.available_inputs == FULL_INPUTS
    assert bundle.run.ingest_warnings == []
    page = parse_html(run.report_path.read_text("utf-8"))
    assert page.by_id("status").attrs["data-outcome"] == "not_scored"
    reasons = [li.text() for li in page.by_id("not-scored").find_all("li")]
    assert reasons == ["not scored: unsupported language detected"]
    for absent in ("summary", "agreement", "flag-count", "flagged-share", "histogram", "flags"):
        assert not page.has_id(absent), absent
    assert "hi-IN" not in run.report_path.read_text("utf-8")  # the tag is not echoed as a claim


def test_e2e_empty_chunk_makes_no_turn_is_counted_and_is_scored(world: World) -> None:
    support = data_support()
    run = world.run("empty_chunk")
    assert run.code == 0, run.err
    assert _lines_without_bundle(run) == [
        "result: scored",
        "language declared by user: mr",
        "stt provider: replay",
        "stt model: recorded-responses (as requested)",
        "normaliser: mr-Deva/1",
        f"agreement distance (MER): {48 / 121!r} (48 of 121; H 73, S 4, D 43, I 1)",
        "tokens: 120 in the export, 78 in the stt transcript",
        "flags: 12 (confidence at or above 0.5)",
        f"flagged share of export tokens: 46 of 120 ({46 / 120!r})",
        "export turns with a flag: 12 of 22",
        "review budget: 1.0 (no limit)",
        "stt usage: none",
        "cost: not available (no_usage)",
        "ingest warnings: 0",
        _path_line(run, "store"),
        _path_line(run, "report"),
    ]
    bundle = _bundle(run)
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    assert len([t for t in bundle.turns if t.transcript_id == stt.id]) == 2
    assert stt.metadata["tracer.empty_chunks"] == {"count": 1, "indexes": [1]}
    assert stt.metadata["tracer.chunk_plan"] == support.chunk_plan()["as_metadata"]
    document = flags_document(bundle)
    assert document["alignment"] == {
        "unit": "word",
        "hits": 73,
        "substitutions": 4,
        "deletions": 43,
        "insertions": 1,
    }
    assert document["tokens"] == {"export": 120, "stt": 78, "flagged_export": 46}
    # the pipeline is the authority for the placement of the tied words; the expectation was
    # regenerated from it and is reproduced by an independent alignment (test_synthetic_pair.py)
    assert document["spans"] == [
        support.span_document(s) for s in support.expected_spans("empty_chunk")
    ]
    page = parse_html(run.report_path.read_text("utf-8"))
    rows = page.by_id("flags-table").find_all("tr", "flag-row")
    assert len(rows) == 12
    last_substitution = rows[-2]  # E14: the one STT word that is substituted, near a chunk edge
    cells = {c.classes[0]: c for c in last_substitution.find_all("td")}
    assert [unwrap(p.text()) for p in cells["heard"].find_all("span", "heard-piece")] == [THAMB]
    assert cells["boundary"].text() == "yes"
    # D06 and E07 to E13 are deletions: nothing heard
    silent = [r for r in rows if r.find_all("td", "heard")[0].find_all("span", "none")]
    assert len(silent) == 8
    assert [r.attrs["data-turn-seq"] for r in silent] == [
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
    ]


# ------------------------------------------------------------------------------ the Sarvam path


def _sarvam_run(world: World, out: Path) -> tuple[CliRun, list[tuple[int, int]]]:
    """The same call through the real Sarvam engine over scripted bodies (no socket)."""
    support = data_support()
    bodies = {i: support.body_bytes("base", i) for i in range(3)}
    seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        content = request.content
        found = re.search(rb'filename="chunk-(\d+)\.wav"', content)
        assert found is not None
        riff = content.index(b"RIFF")
        size = struct.unpack("<I", content[riff + 4 : riff + 8])[0] + 8
        seen.append((int(found[1]), size))
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "date": "Mon, 26 Oct 2026 09:00:00 GMT"},
            content=bodies[int(found[1])],
        )

    argv = score_argv(world.wav, world.export, out, stt="sarvam")
    run = run_cli(
        argv,
        out_dir=out,
        wav=world.wav,
        environ={"SARVAM_API_KEY": KEY_CANARY},
        transport=httpx.MockTransport(handler),
    )
    return run, seen


def test_e2e_sarvam_path_over_mock_transport_equals_the_replay_run(
    world: World, tmp_path: Path
) -> None:
    """The same call through the real Sarvam engine (scripted bodies, no socket) gives the same
    numbers as the replay, plus measured usage, a call log and no key anywhere."""
    support = data_support()
    plan = support.chunk_plan()
    out = tmp_path / "out-sarvam"
    run, seen = _sarvam_run(world, out)
    assert run.code == 0, run.err
    assert run.err == ""
    assert run.sleeps == []  # no retry, no wait
    # three requests, one per chunk, in order, each holding the WAV of the chunk plan
    assert seen == [(c["index"], c["wav_bytes"]) for c in plan["chunks"]]
    assert sorted(tree_bytes(out)) == [REPORT_NAME, "sarvam_call_log.jsonl", "tracer.sqlite"]
    log = [
        json.loads(line) for line in (out / "sarvam_call_log.jsonl").read_text("ascii").splitlines()
    ]
    assert [(entry["phase"], entry["chunk"]) for entry in log] == [
        (phase, chunk) for chunk in range(3) for phase in ("before", "after")
    ]
    assert [e["request_id"] for e in log if e["phase"] == "after"] == [
        support.body("base", i)["request_id"] for i in range(3)
    ]
    replay = world.run("base")
    skip = ("bundle: ", "store: ", "report: ")
    ours = [line for line in run.lines if not line.startswith(skip)]
    theirs = [line for line in replay.lines if not line.startswith(skip)]
    assert len(ours) == len(theirs)
    differing = [(a, b) for a, b in zip(ours, theirs, strict=True) if a != b]
    assert differing == [
        ("stt provider: sarvam", "stt provider: replay"),
        ("stt model: saaras:v4 (as requested)", "stt model: recorded-responses (as requested)"),
        ("stt usage: 3 requests, 68 audio seconds", "stt usage: none"),
        ("cost: not available (no_price_for_model)", "cost: not available (no_usage)"),
    ]
    bundle = _bundle(run)
    mine = flags_document(bundle)
    other = flags_document(_bundle(replay))
    assert mine["stt"] == {"provider": "sarvam", "model": "saaras:v4", "model_source": "requested"}
    assert {k: v for k, v in mine.items() if k != "stt"} == {
        k: v for k, v in other.items() if k != "stt"
    }
    assert len(bundle.usage_records) == 3
    assert all(KEY_CANARY.encode() not in data for data in tree_bytes(out).values())
    assert KEY_CANARY not in run.out
    assert KEY_CANARY not in run.err
    # the report is the same page apart from the provider, model and usage it states
    page = parse_html((out / REPORT_NAME).read_text("utf-8"))
    assert unwrap(page.by_id("stt-provider").text()) == "sarvam"
    assert page.by_id("usage-requests").text() == "3"
    assert page.by_id("usage-cost").text() == "not available (no_price_for_model)"


def test_e2e_every_output_it_writes_is_refused_by_the_repository_guard(
    world: World, tmp_path: Path
) -> None:
    """P3-34 on real outputs: the store, the report and the call log of a run, staged by force
    into a throwaway repository, make the real guard fail, and the three are named."""
    out = tmp_path / "out-guard"
    run, _seen = _sarvam_run(world, out)
    assert run.code == 0, run.err
    repo = guard_repo(tmp_path / "guard")
    assert run_guard(repo).returncode == 0
    names = sorted(tree_bytes(out))
    assert names == [REPORT_NAME, "sarvam_call_log.jsonl", "tracer.sqlite"]
    for name in names:
        # a different name each time would hide the name rules; the real names are used
        target = repo / "somewhere" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((out / name).read_bytes())
        git(repo, "add", "-f", "--", f"somewhere/{name}")
    result = run_guard(repo)
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert f"tracer report file tracked, staged or in history: somewhere/{REPORT_NAME}" in lines
    assert "tracer store file tracked, staged or in history: somewhere/tracer.sqlite" in lines
    assert "tracer call log tracked, staged or in history: somewhere/sarvam_call_log.jsonl" in lines
    # the same files under innocent names are still caught by what they hold (a renamed copy)
    repo2 = guard_repo(tmp_path / "guard2")
    for name, new in ((REPORT_NAME, "page.html"), ("tracer.sqlite", "data.bin")):
        (repo2 / new).write_bytes((out / name).read_bytes())
        git(repo2, "add", "-f", "--", new)
    (repo2 / "run.jsonl").write_bytes((out / "sarvam_call_log.jsonl").read_bytes())
    git(repo2, "add", "-f", "--", "run.jsonl")
    renamed = run_guard(repo2)
    assert renamed.returncode == 1
    assert len(renamed.stderr.splitlines()) == 3, renamed.stderr
