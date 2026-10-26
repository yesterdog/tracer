# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-24: the adversarial report and input suite (contract docs/phase3/api-surface-batch-e.md
section 7).

Hostile export lines, hostile Sarvam bodies, huge lines, NUL and other control characters, bad
files and bad paths go through the whole command (read, score, store, report). For every one the
command either refuses cleanly (exit 4, one ``error:`` line, nothing created) or completes (exit
0, 2 or 3) with a report that still obeys every structural rule, a store that reads back, and
streams that echo none of the hostile text. The text is invented. No test opens a socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    FSI,
    PDI,
    REPORT_NAME,
    base_bodies,
    csp_directives,
    element_text,
    es,
    external_request_violations,
    fresh,
    load_stored_run,
    markerize,
    parse,
    report_files,
    report_mod,
    run_cli,
    sarvam_body,
    score_args,
    sha256_source,
    structure_violations,
    tree_bytes,
    unwrap,
    write_inputs,
    write_replay,
)


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


async def _refuse_async(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _refuse_async)
    monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _refuse_async)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


HOSTILE = '<script>alert(1)</script>"><img src=x onerror=alert(1)></style><svg onload=1>'
C0_AND_C1 = "".join(chr(c) for c in [*range(1, 9), 11, 12, *range(14, 32), 127, *range(128, 160)])


def _line(text: str, speaker: str = "assistant") -> tuple[str, str]:
    return (speaker, text)


def _export(*texts: str) -> str:
    support = es()
    lines = [("assistant" if i % 2 == 0 else "user", t) for i, t in enumerate(texts)]
    return str(support.export_text(lines))


def _words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


#: name -> (export text or bytes, expected exit code or codes, extra keyword arguments)
CASES: dict[str, tuple[Any, tuple[int, ...], dict[str, Any]]] = {
    "script and attribute breakouts": (
        _export(HOSTILE, HOSTILE, "plain words here"),
        (0, 2),
        {},
    ),
    "closing tags of the report itself": (
        _export("</script></style></body></html><script>1</script>", "</main></audio>", "x"),
        (0, 2),
        {},
    ),
    "url forms in text": (
        _export("url(http://e.test/a) @import 'x'; //e.test/y http://e.test javascript:1", "z"),
        (0, 2),
        {},
    ),
    "an override storm": (_export("\u202e" * 5000 + " word", "\u2066" * 5000, "w"), (0, 2), {}),
    "pdi breakout": (
        _export("x\u2069\u202ey \u2069\u2069\u2069 z", "\u2068\u2068", "w"),
        (0, 2),
        {},
    ),
    "every bidi control": (
        _export("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069 a", "b"),
        (0, 2),
        {},
    ),
    "nul": (_export("a\x00b \x00 \x00\x00", "c\x00", "d"), (0, 2, 4), {}),
    "c0 and c1 controls": (_export(f"a{C0_AND_C1}b word", f"x{C0_AND_C1}", "y"), (0, 2, 4), {}),
    "line and paragraph separators": (_export("a\u2028b\u2029c", "d\u0085e", "f"), (0, 2, 4), {}),
    "zero width and joiners": (
        _export("a\u200bb\u200c\u200d\u2060\ufeffc \ufffe\uffff", "d"),
        (0, 2),
        {},
    ),
    "astral and combining": (
        _export("\U0001f600\U0001f600 e\u0301\u0301\u0301 \U00010400", "x"),
        (0, 2),
        {},
    ),
    "format strings and templates": (
        _export("{{7*7}} ${x} %s %(x)s {0} {x.__class__}", "'; DROP TABLE runs;--", "y"),
        (0, 2),
        {},
    ),
    "a huge single token": (_export("A" * 1_000_000, "short", "tail"), (2,), {}),
    "more tokens than the aligner takes": (_export(_words(12_000), "x", "y"), (4,), {}),
    "many turns": (_export(*[f"turn{i} word{i}" for i in range(1200)]), (0, 2), {}),
    "a huge line with a hostile tail": (_export("w " * 200_000 + HOSTILE, "x"), (4,), {}),
    "an empty file": ("", (4,), {}),
    "only a header": ("call_id=5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11\n---\n", (2, 4), {}),
    "only caller lines": (es().export_text([("user", "a b c")]), (2,), {}),
    "a call id with a bidi control": (
        "call_id=5c0a7e1d-\u202e-4f6e-9a18-2d7e4c9b0a11\n---\n"
        "[2026-10-25T06:00:00.000+00:00] assistant: x\n",
        (4,),
        {},
    ),
    "an unknown speaker": (
        es().BASE_EXPORT + "[2026-10-25T06:00:09.000+00:00] <script>: hi\n",
        (4,),
        {},
    ),
    "an export that is not utf-8": (b"call_id=x\n---\n\xed\xa0\x80 \xff\xfe", (4,), {}),
    "a byte order mark and carriage returns": (
        "\ufeff" + es().BASE_EXPORT.replace("\n", "\r\n"),
        (0, 2, 4),
        {},
    ),
    "text with a lone replacement": (_export("a\ufffdb", "c"), (0, 2), {}),
}


def _check_clean_streams(result: Any, hostile: tuple[str, ...]) -> None:
    streams = result.out + result.err
    assert "Traceback" not in streams
    for char in streams:
        assert char == "\n" or unicodedata.category(char) not in ("Cc", "Cf"), hex(ord(char))
    for text in hostile:
        assert text not in streams


def _check_report(out: Path, bundle_id: str) -> None:
    report = report_mod()
    raw = report_files(out)[0].read_text("utf-8")
    doc = parse(raw)
    assert structure_violations(doc) == []
    assert external_request_violations(doc, embedded=False) == []
    (script,) = element_text(raw, "script")
    (style,) = element_text(raw, "style")
    content = next(m.attrs["content"] for m in doc.all("meta") if "http-equiv" in m.attrs)
    directives = csp_directives(content)
    assert directives["script-src"] == [sha256_source(script)]
    assert directives["style-src"] == [sha256_source(style)]
    assert script == report.SEEK_SCRIPT
    for char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067":
        assert char not in raw
    for node in doc.ut_nodes():
        text = node.text()
        assert text.startswith(FSI)
        assert text.endswith(PDI)
        assert FSI not in text[1:-1]
        assert PDI not in text[1:-1]
    assert not [c for c in raw if unicodedata.category(c) == "Cc" and c not in "\n\t"]
    assert "\x00" not in raw
    # the report of the stored run is the report that was written
    stored = load_stored_run(out, bundle_id)
    assert report.render_report(stored, audio=report.RelativeAudio(_audio_src(raw))) == raw
    # what is shown of every export turn is that turn, marked and never changed
    export = next(t for t in stored.transcripts if t.source == "agent_export")
    turns = sorted((t for t in stored.turns if t.transcript_id == export.id), key=lambda t: t.seq)
    shown = [n for n in doc.by_id("transcript").walk() if "ut" in n.classes]
    assert "".join(unwrap(n.text()) for n in shown) == "".join(markerize(t.text) for t in turns)


def _audio_src(raw: str) -> str:
    doc = parse(raw)
    return str(doc.all("audio")[0].attrs["src"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_adversarial_inputs_never_break_report_or_store(tmp_path: Path, name: str) -> None:
    export, allowed, extra = CASES[name]
    inputs = write_inputs(tmp_path, export=export)
    result = run_cli(score_args(inputs, **extra))
    assert result.code in allowed, (name, result.code, result.err[:300])
    _check_clean_streams(result, ("<script>", "onerror", "DROP TABLE"))
    if result.code == 4:
        assert result.err.startswith("error: "), result.err[:300]
        assert result.err.count("\n") == 1, result.err[:300]
        assert result.out == ""
        assert not inputs.out.exists()
        assert result.factory_calls == 0
        return
    assert result.factory_calls == 1
    assert sorted(tree_bytes(inputs.out)) == sorted(
        ["tracer.sqlite", report_files(inputs.out)[0].name]
    )
    assert REPORT_NAME.fullmatch(report_files(inputs.out)[0].name)
    _check_report(inputs.out, result.value("bundle"))
    if name in ("a huge single token", "a huge line with a hostile tail"):
        assert result.value("not scored") == "input too large to compare"
    if name == "more tokens than the aligner takes":
        assert result.value("not scored") == "input too large to compare"
        assert "agreement distance" not in result.out
    if name == "only caller lines":
        assert result.value("not scored") == "missing input agent_transcript"


def test_adversarial_sarvam_body_with_markup_bidi_and_controls(tmp_path: Path) -> None:
    support = es()
    hostile_text = (
        f"{HOSTILE} \u202e9876 \u2066x\u2069 nul\x00end {support.TEXT_CANARY} "
        + support.STT_TEXTS[0]
    )
    bodies = [
        sarvam_body(hostile_text, request_id="20261025_h-0"),
        sarvam_body(support.STT_TEXTS[1], request_id="20261025_h-1"),
    ]
    folder = write_replay(tmp_path / "replay", bodies)
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs, options={"--stt": f"fake:{folder}"}), production=True)
    assert result.code == 0, result.err
    _check_clean_streams(result, ("<script>", support.TEXT_CANARY))
    _check_report(inputs.out, result.value("bundle"))
    # the provider text of a flagged span is shown beside it, escaped and marked, never as markup
    raw = report_files(inputs.out)[0].read_text("utf-8")
    assert support.TEXT_CANARY in raw
    assert "script&gt;alert(1)&lt;/script&gt;" in raw
    assert "<script>alert" not in raw
    assert "[RLO]" in raw
    assert "[U+0000]" in raw
    stored = load_stored_run(inputs.out, result.value("bundle"))
    stt_text = "".join(t.text for t in stored.turns if t.source == "our_stt")
    assert support.TEXT_CANARY in stt_text
    assert "\x00" in stt_text


def test_adversarial_sarvam_body_with_a_markup_language_label_and_request_id(
    tmp_path: Path,
) -> None:
    support = es()
    bodies = [
        sarvam_body(
            support.STT_TEXTS[0], request_id="20261025_ok-0", language="<img src=x onerror=1>"
        ),
        sarvam_body(support.STT_TEXTS[1], request_id="20261025_ok-1"),
    ]
    folder = write_replay(tmp_path / "replay", bodies)
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs, options={"--stt": f"fake:{folder}"}), production=True)
    assert result.code == 2, result.err
    assert result.value("not scored") == "language not observed in the stt transcript"
    _check_clean_streams(result, ("<img", "onerror"))
    _check_report(inputs.out, result.value("bundle"))
    raw = report_files(inputs.out)[0].read_text("utf-8")
    assert "<img" not in raw
    assert "onerror=1>" not in raw


def test_adversarial_replay_bodies_over_the_cap_or_malformed_fail_the_transcription(
    tmp_path: Path,
) -> None:
    support = es()
    big = json.dumps(sarvam_body("x" * 1_100_000, request_id="20261025_big")).encode()
    shapes = {
        "body_too_large": big,
        "not_json": b"\xff\xfe\x00{",
        "not_object": b"[1, 2]",
        "transcript_invalid": json.dumps({"request_id": "r", "transcript": 5}).encode(),
        "nan": b'{"request_id": "r", "transcript": "x", "language_probability": NaN}',
        "deep": b"[" * 5000 + b"]" * 5000,
    }
    for index, (reason, body) in enumerate(shapes.items()):
        folder = tmp_path / f"c{index}"
        folder.mkdir()
        replay = write_replay(folder / "replay", [base_bodies()[0], body])
        inputs = write_inputs(folder)
        result = run_cli(score_args(inputs, options={"--stt": f"fake:{replay}"}), production=True)
        assert result.code == 3, (reason, result.err)
        assert result.value("not scored") == "speech-to-text failed"
        _check_clean_streams(result, (support.TEXT_CANARY,))
        if reason in ("body_too_large", "not_json", "not_object"):
            assert reason in result.value("detail"), (reason, result.value("detail"))
        _check_report(inputs.out, result.value("bundle"))


def test_adversarial_lone_surrogate_in_text_is_replaced_and_never_reaches_the_report(
    tmp_path: Path,
) -> None:
    # a file cannot hold one (strict UTF-8), but the library path can: the reader replaces it with
    # U+FFFD and warns, and the pipeline's bundle renders and stores like any other
    support = es()
    report = report_mod()
    export = support.export_text([("assistant", "a\ud800b word"), ("user", "c")])
    run = support.run_pipeline(tmp_path, export=export, accept_export_warnings=True)
    codes = [w.code for w in run.bundle.run.ingest_warnings]
    assert "text_not_valid_unicode" in codes
    raw = report.render_report(run.bundle, audio=report.RelativeAudio("../in/call.wav"))
    assert raw.encode("utf-8")  # encodable: no lone surrogate survives
    doc = parse(raw)
    assert "\ufffd" in "".join(unwrap(n.text()) for n in doc.ut_nodes())
    assert "text_not_valid_unicode" in doc.by_id("warnings").text()
    assert structure_violations(doc) == []


def test_adversarial_paths_and_options(tmp_path: Path) -> None:
    support = es()
    inputs = write_inputs(tmp_path)
    # --out is a regular file
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", "utf-8")
    refused = run_cli(score_args(inputs, options={"--out": str(blocker)}))
    assert refused.code == 4
    assert blocker.read_text("utf-8") == "not a directory"
    # inputs that are directories, or are missing, or empty files
    for option in ("--rubric", "--transcript"):
        got = run_cli(score_args(inputs, options={option: str(tmp_path)}))
        assert got.code == 4, option
        assert not inputs.out.exists()
    args = score_args(inputs)
    args[1] = str(tmp_path)
    assert run_cli(args).code == 4
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    args[1] = str(empty)
    assert run_cli(args).code == 4
    # an existing report name that is a symlink is never followed or overwritten
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", "utf-8")
    inputs.out.mkdir(mode=0o700)
    link = inputs.out / "report-20261025T090000Z.html"
    os.symlink(victim, link)
    collided = run_cli(score_args(inputs))
    assert collided.code == 4
    assert victim.read_text("utf-8") == "keep me"
    assert collided.factory_calls == 0
    assert support.KEY_CANARY not in collided.err
    # ... not even a dangling one: nothing is written through it
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    target = tmp_path / "does-not-exist-yet.txt"
    os.symlink(target, second / "report-20261025T090000Z.html")
    dangling = run_cli(score_args(inputs, options={"--out": str(second)}))
    assert dangling.code == 4
    assert not target.exists()
    assert dangling.factory_calls == 0
    # an option value that looks like another option is a usage error, never a crash
    odd = run_cli(score_args(inputs, options={"--rubric": "--out"}))
    assert odd.code == 64
    # option abbreviations are not accepted for the privacy inputs (a typo must not pick a value)
    abbreviated = run_cli(
        [
            a if a != "--consent" else "--cons"
            for a in score_args(inputs, options={"--out": str(tmp_path / "o2")})
        ]
    )
    assert abbreviated.code == 64
    assert not (tmp_path / "o2").exists()


def test_adversarial_rubric_note_is_never_stored_or_shown(tmp_path: Path) -> None:
    support = es()
    document = support.rubric_doc()
    document["note"] = HOSTILE + support.TEXT_CANARY
    inputs = write_inputs(tmp_path, rubric=json.dumps(document))
    result = run_cli(score_args(inputs))
    assert result.code == 0, result.err
    assert support.TEXT_CANARY not in result.out + result.err
    for name, data in tree_bytes(inputs.out).items():
        assert support.TEXT_CANARY.encode() not in data, name
        assert b"onerror" not in data, name


def test_adversarial_zero_frame_and_lying_wav_files(tmp_path: Path) -> None:
    support = es()
    inputs = write_inputs(tmp_path, frames=0)
    result = run_cli(score_args(inputs))
    assert result.code == 2, result.err
    assert result.value("not scored") == "no tokens in the stt transcript"
    _check_report(inputs.out, result.value("bundle"))
    folder = tmp_path / "lie"
    folder.mkdir()
    lying = write_inputs(folder)
    data = bytearray(lying.wav.read_bytes())
    data[40:44] = (2**31).to_bytes(4, "little")  # the data chunk claims 2 GiB
    lying.wav.write_bytes(bytes(data))
    refused = run_cli(score_args(lying))
    assert refused.code == 4
    assert not lying.out.exists()
    assert support.TEXT_CANARY not in refused.err


def test_adversarial_replay_body_at_the_cap_is_read_and_one_byte_more_is_not(
    tmp_path: Path,
) -> None:
    cap = 1_048_576
    body = sarvam_body("", request_id="20261025_cap")
    padding = cap - len(json.dumps(body).encode())
    body["transcript"] = "x" * padding
    data = json.dumps(body).encode()
    assert len(data) == cap
    replay = write_replay(tmp_path / "at", [data, base_bodies()[1]])
    inputs = write_inputs(fresh(tmp_path, "at-run"))
    at_cap = run_cli(score_args(inputs, options={"--stt": f"fake:{replay}"}), production=True)
    # read and compared; the single huge token is then too large to compare, which is not a failure
    assert at_cap.code == 2, at_cap.err
    assert at_cap.value("not scored") == "input too large to compare"
    over = write_replay(tmp_path / "over", [data + b" ", base_bodies()[1]])
    over_inputs = write_inputs(fresh(tmp_path, "over-run"))
    refused = run_cli(score_args(over_inputs, options={"--stt": f"fake:{over}"}), production=True)
    assert refused.code == 3, refused.err
    assert "body_too_large" in refused.value("detail")
