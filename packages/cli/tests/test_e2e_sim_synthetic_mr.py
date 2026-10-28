# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-28: the offline end to end run of ``tracer simulate`` over a synthetic Marathi call.

A four-line scripted call (the first four lines of scenario ``sim-mr-s1`` of the committed synthetic
fixture ``packages/evalcore/tests/fixtures/sim_mr.json``: invented, hand written, not natively
reviewed, BL-169; this proves plumbing, not Marathi quality, and says nothing about Sarvam), a fake
TTS tone, an in-process fake agent that speaks the fixture's four replies, the fake speech-to-text
of the scenario's words and an agent export with one planted substitution. Everything runs on the
virtual clock through the command, so every number is exact and written here as a literal; the
timeline is derived by hand below and the expected WAV is built here by an independent writer.

Timeline (ms; a line of N characters is 2 N frames of 20 ms; the agent is silent for 300 ms before
the caller starts the next line; the agent's last active frame is at its start + 20 (frames - 1)):

* line 0 (40 characters, 80 frames) is sent 0 .. 1580, its edge is 1600; reply 0 (20 frames) is
  1900 .. 2280, gap 300 and first agent audio 1900;
* line 1 (51 characters, 102 frames) starts at 2280 + 300 = 2580, edge 4620; reply 1 (15 frames)
  starts at 4900, last frame 5180, gap 280;
* line 2 (29 characters, 58 frames) starts at 5480, edge 6640; reply 2 (25 frames) starts at 6900,
  last frame 7380, gap 260;
* line 3 (18 characters, 36 frames) starts at 7680, edge 8400; reply 3 (10 frames) starts at 8700,
  last frame 8880, gap 300; the recording ends at 8900.

The gaps are [300, 280, 260, 300]: the nearest-rank median (rank 2 of 4 sorted) is 280. Nothing
overlaps. Contract: docs/phase4/api-surface-batch-f.md section 4. ADR 0027 rules 1 to 5, 10, 17.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import stat
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import Node, parse_html  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    NOW,
    STAMP,
    SimRun,
    base_args,
    evaluator_doc,
    run_sim,
    scenario_doc,
    write_inputs,
)
from cli_p4f_support import (  # type: ignore[import-not-found,unused-ignore]
    AGREEMENT_KEYS,
    RESULT_KEYS,
    TIMING_KEYS,
    mask,
    results_by_key,
    sim_mr_scenario,
)

#: reply k starts here (ms after the connection) and lasts FRAMES[k] frames of 20 ms
REPLY_START = (1900, 4900, 6900, 8700)
REPLY_FRAMES = (20, 15, 25, 10)
#: the caller lines as sent: first frame instant and edge (last frame + 20), by the derivation above
LINE_SPAN = ((0, 1600), (2580, 4620), (5480, 6640), (7680, 8400))
LEVEL = 1000
RATE = 16000
TOTAL_MS = 8900
#: sha256 of the recording and of the masked report / masked stored bundle (pinned; a change of any
#: stored byte is a named spec change)
WAV_SHA256 = "3b79ce5d8aa458d2a6e0d2198c5b450b9c6f41ae5423fa42e72c27cc729a3212"
WAV_SIZE = 44 + TOTAL_MS * 16 * 4  # a canonical header and 16-bit stereo samples

#: sha256 of the report and of the stored bundle with every identifier, instant, digest and directory
#: masked (``mask`` of ``cli_p4f_support``); identical on python 3.11 to 3.14 when pinned
REPORT_MASKED_SIZE = 11327
REPORT_MASKED_SHA256 = "63019eb180954d2194701907fd780ad629729fa008ea2791425d92d5111c7b85"
BUNDLE_MASKED_SHA256 = "f7b8d97998b1aa3fa998b135ad94a339822ccada3af64c477c820b266fedb2f3"

FLAGS_SPAN = {
    "turn_seq": 2,
    "start": 27,
    "end": 32,
    "export_tokens": 1,
    "stt_token_start": 10,
    "stt_token_end": 11,
    "substitutions": 1,
    "deletions": 0,
    "insertions": 0,
    "edit_distance": 1,
    "key_length": 5,
    "confidence": "0.2",
    "flagged": False,
    "near_chunk_boundary": True,
}

NOTE = "observed at the simulator over pipecat-protobuf/1, quantum 20 ms"
EXPECTED_STDOUT = [
    "result: call established",
    "end reason: script_complete",
    "outcome: not evaluated",
    "dialect: pipecat-protobuf/1",
    "caller audio: synthetic (generated speech, not a person)",
    "caller turns: 4 started, 4 completed",
    "agent segments: 4",
    f"first agent audio: 1900 ms ({NOTE})",
    f"gap after the caller's line (median): 280 ms ({NOTE}, N 4)",
    f"overlap: 0 ms ({NOTE})",
    f"answered caller turns: 4 ({NOTE})",
    "agreement distance (MER): 0.05 (1 of 20; H 19, S 1, D 0, I 0) between the agent's own export and our speech-to-text of the agent",
    "flags: 0",
    "tts usage: none",
    "llm usage: none",
    "stt usage: none",
    "cost: not available (no_usage)",
    "ingest warnings: 0",
]

CALL_LOG = [
    '{"at":"2026-10-27T12:00:00.000000Z","event":"connect","stage":"call","dialect":"pipecat-protobuf/1"}',
    '{"at":"2026-10-27T12:00:00.000000Z","event":"end","stage":"call","end_reason":"script_complete","counts":{"caller_turns_started":4,"caller_turns_completed":4,"agent_segments":4,"barge_ins_fired":0,"brain_requests":0,"brain_invalid_outputs":0,"brain_regenerations":0,"tts_requests":0,"tts_cache_hits":0,"llm_requests":0,"stt_requests":0,"frames_sent":276,"frames_received":70,"inbound_messages_ignored":0,"signals_received":0}}',
]

TIMING_DOCUMENT = {
    "format": "tracer.timing/1",
    "dialect": "pipecat-protobuf/1",
    "clock": "simulator_monotonic",
    "quantum_ms": 20,
    "caller_frame_ms": 20,
    "setup_ms": 0,
    "first_agent_audio_ms": 1900,
    "overlap_ms": 0,
    "answered_caller_turns": 4,
    "gap_median_ms": 280,
    "gap_n": 4,
    "barge_in_stop_median_ms": None,
    "barge_in_n": 0,
    "turns": [
        {
            "turn_seq": seq,
            "gap_ms": gap,
            "overlap_ms": 0,
            "barge_in_after_ms": None,
            "barge_in_stop_ms": None,
            "self": {
                "reply_ms": reply,
                "wait_ms": wait,
                "stt_ms": None,
                "brain_ms": None,
                "tts_ms": None,
            },
        }
        for seq, gap, reply, wait in (
            (0, 300, None, None),
            (1, 280, 300, 300),
            (2, 260, 300, 300),
            (3, 300, 300, 300),
        )
    ],
}


@dataclass
class Case:
    root: Path
    run: SimRun
    replies: list[str]
    lines: list[str]
    export_text: str

    @property
    def out(self) -> Path:
        return self.root / "out"


def tokens(text: str) -> list[str]:
    """The words of an invented sentence: whitespace separated, the punctuation of the fixture
    (full stop, comma, question mark) removed. An independent reader of the test, not the product's
    normaliser (the Marathi normaliser has its own tests)."""
    return re.sub(r"[.,?]", "", text).split()


def edit_counts(ref: list[str], hyp: list[str]) -> tuple[int, int, int, int]:
    """(hits, substitutions, deletions, insertions) of a minimal word alignment, found by a plain
    dynamic programme (unit cost). For the one planted substitution it is unique."""
    rows = len(ref) + 1
    cols = len(hyp) + 1
    dist = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dist[i][0] = i
    for j in range(cols):
        dist[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dist[i][j] = min(dist[i - 1][j] + 1, dist[i][j - 1] + 1, dist[i - 1][j - 1] + cost)
    i, j = len(ref), len(hyp)
    hits = subs = dels = ins = 0
    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and dist[i][j] == dist[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1)
        ):
            if ref[i - 1] == hyp[j - 1]:
                hits += 1
            else:
                subs += 1
            i, j = i - 1, j - 1
        elif i > 0 and dist[i][j] == dist[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return hits, subs, dels, ins


def build_case(root: Path, monkeypatch: pytest.MonkeyPatch) -> Case:
    """The inputs under ``root/in`` and a run with the output directory ``out`` relative to the
    working directory (so the printed paths are the same on every machine)."""
    scenario = sim_mr_scenario(0)
    lines = [t["text"] for t in scenario["scripted_turns"][:4]]
    replies = [r["text"] for r in scenario["agent_replies"][:4]]
    steps: list[Any] = []
    previous_end = 0
    for start, frames, text in zip(REPLY_START, REPLY_FRAMES, replies, strict=True):
        steps += [
            {"wait_ms": start - previous_end},
            {"speak": {"frames": frames, "level": LEVEL, "text": text}},
        ]
        previous_end = start + frames * 20
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    files = write_inputs(
        Path("in"),
        evaluator=evaluator_doc(scripted_turns=scenario["scripted_turns"][:4]),
        scenario=scenario_doc(steps),
    )
    files.out = Path("out")
    # the agent's own export: the four replies and the four lines, one word of reply 1 replaced
    heard = [replies[0], replies[1].rsplit(" ", 1)[0] + " सांगू.", replies[2], replies[3]]
    said = [heard[0], lines[0], heard[1], lines[1], heard[2], lines[2], heard[3], lines[3]]
    export = "call_id=5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11\n---\n" + "".join(
        f"[2026-10-25T06:00:{i:02d}.000+00:00] {'assistant' if i % 2 == 0 else 'user'}: {text}\n"
        for i, text in enumerate(said)
    )
    files.export.write_text(export, "utf-8")
    args = [("out" if a == str(Path("in") / "out") else a) for a in base_args(files)]
    args += ["--agent-transcript", str(files.export), "--declared-language", "mr"]
    run = run_sim(files, args=args)
    files.out = root.resolve() / "out"  # later reads do not depend on the working directory
    return Case(root, run, replies, lines, export)


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Case:
    return build_case(tmp_path, monkeypatch)


def expected_wav() -> bytes:
    """The recording as the specification says it is: stereo, 16 kHz, 16-bit, the caller's tone
    on the left over the four line spans, the agent's tone on the right over the four replies."""
    samples = TOTAL_MS * 16
    left = [0] * samples
    right = [0] * samples
    for start, edge in LINE_SPAN:
        left[start * 16 : edge * 16] = [LEVEL] * ((edge - start) * 16)
    for start, frames in zip(REPLY_START, REPLY_FRAMES, strict=True):
        right[start * 16 : (start + frames * 20) * 16] = [LEVEL] * (frames * 20 * 16)
    body = b"".join(struct.pack("<hh", a, b) for a, b in zip(left, right, strict=True))
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(body), b"WAVE", b"fmt ", 16, 1, 2, RATE,
        RATE * 4, 4, 16, b"data", len(body),
    )  # fmt: skip
    return header + body


def masked_bundle_digest(run: SimRun) -> tuple[str, str]:
    """The stored run as canonical JSON with every identifier, instant and digest replaced; the
    digest of the document and the document itself (for a readable failure)."""
    dump = json.dumps(run.bundle().model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    text = re.sub(r"\b[0-9a-f]{64}\b", "<SHA>", mask(dump))
    text = re.sub(r"file://[^\"]*/", "file://<DIR>/", text)  # the temporary directory
    return hashlib.sha256(text.encode("ascii")).hexdigest(), text


# ---------------------------------------------------------------------------- the named tests


def test_e2e_sim_synthetic_mr_exact_outputs(case: Case) -> None:
    """Every byte the command writes or prints for this call, pinned: stdout, the files and their
    modes, the WAV (against an independent writer and a literal digest), the stored transcripts,
    turns, timing document and results, the call log, the masked report and the masked bundle."""
    run = case.run
    assert run.code == 0, run.err
    assert run.err == ""
    assert run.lines[: len(EXPECTED_STDOUT)] == EXPECTED_STDOUT
    tail = run.lines[len(EXPECTED_STDOUT) :]
    assert len(tail) == 4
    assert re.fullmatch(r"bundle: rbnd_[0-9a-f-]{36}", tail[0])
    assert tail[1:] == [
        "store: out/tracer.sqlite",
        f"recording: out/recording-{STAMP}.wav",
        f"report: out/report-{STAMP}.html",
    ]
    assert run.out.isascii()
    # the files, exactly these, private
    assert run.names() == [
        f"recording-{STAMP}.wav",
        f"report-{STAMP}.html",
        "sim_call_log.jsonl",
        "tracer.sqlite",
    ]
    assert stat.S_IMODE(case.out.stat().st_mode) == 0o700
    for path in case.out.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    # the recording
    wav = (case.out / f"recording-{STAMP}.wav").read_bytes()
    assert len(wav) == WAV_SIZE == 569644
    assert wav == expected_wav()
    assert hashlib.sha256(wav).hexdigest() == WAV_SHA256
    with wave.open(str(case.out / f"recording-{STAMP}.wav")) as reader:
        assert (reader.getnchannels(), reader.getframerate(), reader.getsampwidth()) == (
            2,
            16000,
            2,
        )
        assert reader.getnframes() == TOTAL_MS * 16
    # the call log: the connect and the end, with the counts, at the fixed instant
    assert (case.out / "sim_call_log.jsonl").read_text("utf-8").splitlines() == CALL_LOG
    bundle = run.bundle()
    # transcripts and turns, in order
    sources = [t.source for t in bundle.transcripts]
    assert sources == ["script", "our_stt", "agent_export"]
    by_source = {
        t.source: [x for x in bundle.turns if x.transcript_id == t.id] for t in bundle.transcripts
    }
    assert [x.text for x in by_source["script"]] == case.lines
    assert [x.text for x in by_source["our_stt"]] == case.replies
    assert [(x.role, x.seq) for x in by_source["script"]] == [("caller", k) for k in range(4)]
    assert [(x.start_ms, x.end_ms) for x in by_source["script"]] == list(LINE_SPAN)
    assert [x.role for x in by_source["our_stt"]] == ["agent"] * 4
    assert [x.language for x in by_source["our_stt"]] == ["mr-IN"] * 4
    assert [x.role for x in by_source["agent_export"]] == ["agent", "caller"] * 4
    assert all(x.language == "mr-IN" for x in by_source["script"])
    assert bundle.run.started_at == NOW
    assert list(bundle.run.available_inputs) == [
        "audio", "agent_audio", "transcript", "agent_transcript", "turn_timing",
    ]  # fmt: skip
    # the timing document, whole
    assert bundle.run.metadata["tracer.timing"] == TIMING_DOCUMENT
    # the results, in order, exactly
    results = results_by_key(bundle)
    assert tuple(results) == RESULT_KEYS
    assert [results[k].value for k in TIMING_KEYS] == [1900, 280, 0, 4]
    assert [results[k].value for k in AGREEMENT_KEYS] == [0.05, 0, 0.0]
    assert {results[k].status for k in RESULT_KEYS} == {"scored"}
    flags = results["stt_export_agent_agreement_mer"].metadata["tracer.flags"]
    assert flags["alignment"] == {
        "unit": "word",
        "hits": 19,
        "substitutions": 1,
        "deletions": 0,
        "insertions": 0,
    }
    assert flags["tokens"] == {"export": 20, "stt": 20, "flagged_export": 0}
    assert flags["spans"] == [FLAGS_SPAN]
    assert flags["stt"] == {
        "provider": "fake",
        "model": "scenario-text",
        "model_source": "requested",
    }
    # an independent count of the words and of the one substitution
    agent_export = [t for t in by_source["agent_export"] if t.role == "agent"]
    export_words = [w for t in agent_export for w in tokens(t.text)]
    heard_words = [w for t in by_source["our_stt"] for w in tokens(t.text)]
    assert (len(export_words), len(heard_words)) == (20, 20)
    assert edit_counts(export_words, heard_words) == (19, 1, 0, 0)
    assert results["stt_export_agent_agreement_mer"].value == pytest.approx(1 / 20)
    # the artifact is the recording on disk
    (artifact,) = bundle.artifacts
    assert artifact.sha256 == WAV_SHA256
    assert artifact.size == WAV_SIZE
    # the outcome document, whole (the authorisation reference is its hash)
    outcome = bundle.run.metadata["tracer.sim_outcome"]
    assert outcome["end_reason"] == "script_complete"
    assert outcome["call_established"] is True
    # connected from 0 to the end: the last active frame arrived at 8880, the call ends once the
    # agent was silent for 300 ms (script_complete: the brain has no further line)
    assert outcome["connected_ms"] == 9180
    assert outcome["cap_reached"] is None
    assert outcome["counts"]["frames_sent"] == 276 == sum(f for f in (80, 102, 58, 36))
    assert outcome["counts"]["frames_received"] == 70 == sum(REPLY_FRAMES)
    assert outcome["authorisation_reference_sha256"] == (
        hashlib.sha256(b"Approved by the test operator, ticket 17").hexdigest()
    )
    # the report and the stored bundle, with identifiers and instants masked
    report = mask(run.report())
    assert len(report.encode("utf-8")) == REPORT_MASKED_SIZE
    assert hashlib.sha256(report.encode("utf-8")).hexdigest() == REPORT_MASKED_SHA256
    digest, document = masked_bundle_digest(run)
    assert digest == BUNDLE_MASKED_SHA256, document[:4000]


def test_e2e_sim_twice_gives_the_same_bytes_modulo_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same inputs, same virtual clock: the recording, the call log, the masked report, the masked
    bundle and the masked stdout are the same bytes; only the fresh identifiers differ (ADR 0027
    rule 1, ADR 0026 rule 13: the caller side is reproducible; nothing is said about an agent)."""
    one = build_case(tmp_path / "a", monkeypatch)
    two = build_case(tmp_path / "b", monkeypatch)
    assert one.run.code == two.run.code == 0
    name = f"recording-{STAMP}.wav"
    assert (one.out / name).read_bytes() == (two.out / name).read_bytes()
    assert (one.out / "sim_call_log.jsonl").read_bytes() == (
        two.out / "sim_call_log.jsonl"
    ).read_bytes()
    assert mask(one.run.report()) == mask(two.run.report())
    assert masked_bundle_digest(one.run)[0] == masked_bundle_digest(two.run)[0]
    assert mask(one.run.out) == mask(two.run.out)
    ids = [x for x in one.run.lines if x.startswith("bundle: ")] + [
        x for x in two.run.lines if x.startswith("bundle: ")
    ]
    assert len(set(ids)) == 2  # the identifier is the one thing that is fresh


# ------------------------------------------------------------------ report, store, wav and stdout


def interval_rows(root: Node) -> list[tuple[str, int, int]]:
    table = root.by_id("timeline")
    return [
        (r.attrs["data-who"], int(r.attrs["data-start-ms"]), int(r.attrs["data-end-ms"]))
        for r in table.find_all("tr", "interval")
    ]


def test_e2e_sim_report_and_store_consistent(case: Case) -> None:
    """The four views of one call agree: stdout, the stored run, the report and the WAV. Each is
    read by its own reader: the report by an HTML parser, the store through ``load_run``, the WAV
    through the ``wave`` module, stdout by lines. Every timing value, every line of text, every
    interval and the recording's digest is the same in all of them, and the report links the WAV by
    its relative name without embedding it."""
    run = case.run
    bundle = run.bundle()
    root = parse_html(run.report())
    results = results_by_key(bundle)
    timing = bundle.run.metadata["tracer.timing"]
    # the report's summary, outcome and label match the stored outcome
    status = root.by_id("status")
    assert status.attrs["data-established"] == "true"
    assert (
        status.attrs["data-end-reason"] == bundle.run.metadata["tracer.sim_outcome"]["end_reason"]
    )
    assert "synthetic" in root.by_id("synthetic-label").text().lower()
    assert "not evaluated" in root.by_id("outcome-label").text()
    # timing: the table rows carry the stored value, quantum and dialect
    for key in TIMING_KEYS:
        row = root.by_id(f"timing-{key}")
        assert row.attrs["data-status"] == results[key].status == "scored"
        assert int(row.attrs["data-value"]) == results[key].value
        assert row.attrs["data-quantum-ms"] == str(timing["quantum_ms"])
        assert row.attrs["data-dialect"] == timing["dialect"] == "pipecat-protobuf/1"
    assert root.by_id("timing-sim_agent_response_gap_median_ms").attrs["data-n"] == str(
        timing["gap_n"]
    )
    # the same numbers on stdout
    assert run.value("first agent audio").startswith(f"{timing['first_agent_audio_ms']} ms")
    assert run.value("overlap").startswith(f"{timing['overlap_ms']} ms")
    assert run.value("gap after the caller's line (median)").startswith(
        f"{timing['gap_median_ms']} ms"
    )
    # the timeline: the caller's intervals are the stored script turns, the agent's are the replies
    rows = interval_rows(root)
    script = [t for t in bundle.turns if t.source == "script"]
    assert [r for r in rows if r[0] == "caller"] == [
        ("caller", t.start_ms, t.end_ms) for t in script
    ]
    agent = [r for r in rows if r[0] == "agent"]
    assert agent == [
        ("agent", s, s + f * 20) for s, f in zip(REPLY_START, REPLY_FRAMES, strict=True)
    ]
    # the texts, escaped per piece, in the stored order
    caller_items = root.by_id("caller-lines").find_all("li")
    agent_items = root.by_id("agent-lines").find_all("li")
    assert [i.find_all("div", "text")[0].text().strip("⁨⁩") for i in caller_items] == case.lines
    assert [i.find_all("div", "text")[0].text().strip("⁨⁩") for i in agent_items] == case.replies
    # the agreement: the report's section carries the stored status; the numbers are on stdout
    assert root.by_id("agreement").attrs["data-status"] == "scored"
    assert "0.05" in run.value("agreement distance (MER)")
    # the audio: the artifact, the file, the report's facts and the player agree
    (artifact,) = bundle.artifacts
    wav_path = case.out / f"recording-{STAMP}.wav"
    data = wav_path.read_bytes()
    facts = root.by_id("audio-facts")
    assert facts.attrs["data-sha256"] == artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert int(facts.attrs["data-bytes"]) == artifact.size == len(data)
    assert artifact.uri == wav_path.resolve().as_uri()
    assert root.by_id("player").attrs["src"] == wav_path.name  # relative; nothing embedded
    assert "data:audio" not in run.report()
    # the counts on the page are the counts of the outcome
    counts = bundle.run.metadata["tracer.sim_outcome"]["counts"]
    section = root.by_id("counts")
    cells = {r.find_all("th")[0].text(): r.find_all("td")[0].text() for r in section.find_all("tr")}
    assert {k: int(v) for k, v in cells.items()} == counts
    # usage and cost: none (fake engines), as stored
    assert bundle.usage_records == []
    assert root.by_id("usage-tts").text() == "none"
    assert root.by_id("usage-cost").text() == "not available (no_usage)"
    # the sections that must not exist for a clean call
    assert root.by_id("warnings").attrs["data-count"] == "0"
    # the report is exactly what the renderer makes of the stored run and its audio
    from cli_p4e_support import mod  # type: ignore[import-not-found,unused-ignore]

    assert (
        mod("simreport").render_sim_report(bundle, audio=mod("report").RelativeAudio(wav_path.name))
        == run.report()
    )
