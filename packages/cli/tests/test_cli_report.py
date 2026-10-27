# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-23: the single self-contained HTML report (contract docs/phase3/api-surface-batch-e.md
section 5).

Most tests render a bundle that the batch D pipeline produced for the hand-computed base scenario
(``render_report(bundle, audio=...)``); the ones that need the command's own choices (the relative
audio reference, the files written, the key channels) run ``tracer_cli.cli.run`` in-process. The
HTML is parsed with the standard library (``html.parser``); nothing is rendered by a browser, so the
behaviour of the seek script and of a relative ``<audio src>`` under ``file://`` is not tested
(manual check, contract section 5.9).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    ABSOLUTE_PATH,
    DATA_AUDIO,
    DENY_LIST,
    FSI,
    PDI,
    SarvamScript,
    csp_directives,
    element_text,
    es,
    export_turns,
    external_request_violations,
    flag_document,
    fresh,
    load_stored_run,
    markerize,
    no_price_cost_builder,
    parse,
    price_file_text,
    report_files,
    report_mod,
    run_cli,
    score_args,
    sha256_source,
    shown_html,
    structure_violations,
    summary_mod,
    the_report,
    tree_bytes,
    unwrap,
    write_inputs,
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


RELATIVE = "../in/call.wav"


def _run(tmp_path: Path, **options: Any) -> Any:
    """The batch D pipeline on the base scenario (test threshold 0.3, test budget 0.25)."""
    support = es()
    options.setdefault("cost_builder", no_price_cost_builder)
    if "config" not in options:
        options["config"] = support.configuration(
            threshold=options.pop("threshold", support.TEST_ONLY_FLAG_MIN_CONFIDENCE),
            budget=options.pop("budget", support.TEST_ONLY_REVIEW_BUDGET),
        )
    return support.run_pipeline(tmp_path, **options)


def _render(run: Any, *, src: str = RELATIVE) -> str:
    report = report_mod()
    return str(report.render_report(run.bundle, audio=report.RelativeAudio(src)))


def _agreeing(lines: list[tuple[str, str]]) -> Any:
    """An engine whose two chunks say exactly what the export lines say (no span at all)."""
    support = es()
    half = max(1, len(lines) - 1)
    texts = [
        " ".join(text for _, text in lines[:half]),
        " ".join(text for _, text in lines[half:]) or "x",
    ]
    return support.FakeEngine(chunk_scripts=support.scripts(texts, phrases=None))


def _scenarios(tmp_path: Path) -> dict[str, Any]:
    """Runs of every outcome the report has to cope with."""
    support = es()
    made: dict[str, Any] = {}

    def folder(name: str) -> Path:
        path = tmp_path / name
        path.mkdir()
        return path

    made["scored"] = _run(folder("scored"))
    made["more flags"] = _run(folder("more"), threshold=0.2)
    made["over budget"] = _run(folder("over"), budget=0.05)
    made["placeholder"] = _run(folder("placeholder"), threshold=0.5, budget=1.0)
    made["language"] = _run(
        folder("language"),
        engine=support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", "hi"])),
    )
    made["no tokens"] = _run(
        folder("tokens"), engine=support.FakeEngine(chunk_scripts=support.scripts(["", ""], None))
    )
    made["stt failed"] = _run(
        folder("failed"),
        engine=support.FakeEngine(
            fail_at=1,
            failure=__import__("tracer_evalcore.stt", fromlist=["x"]).SttServerError(
                chunk_index=1, status=503, attempts=4
            ),
        ),
    )
    made["no agent line"] = _run(
        folder("noagent"), export=support.export_text([("user", support.KAAM)])
    )
    lines = list(support.EXPORT_LINES)
    made["no flags"] = _run(folder("noflags"), engine=_agreeing(lines))
    return made


# ------------------------------------------------------------------ P3-23 required names


def test_report_has_no_external_requests(tmp_path: Path) -> None:
    report = report_mod()
    hostile = es().export_text(
        [
            ("assistant", '<img src="http://evil.example/p.png"> url(http://evil.example/a)'),
            ("user", "@import 'http://evil.example/x.css'; //cdn.example/lib.js https://x.test"),
            ("assistant", "javascript:alert(1) ftp://host/file <link rel=stylesheet href=//e/x>"),
        ]
    )
    runs = dict(_scenarios(tmp_path))
    runs["hostile"] = _run(fresh(tmp_path, "hostile"), export=hostile)
    assert runs
    for name, run in runs.items():
        for embedded in (False, True):
            audio = (
                report.EmbeddedAudio(b"RIFF....WAVEdata")
                if embedded
                else report.RelativeAudio(RELATIVE)
            )
            raw = report.render_report(run.bundle, audio=audio)
            doc = parse(raw)
            assert structure_violations(doc) == [], (name, embedded)
            assert external_request_violations(doc, embedded=embedded) == [], (name, embedded)
            assert not re.search(r"(?i)<a[\s>]|<link|<img|<iframe|<object|<embed|<form", raw)
            assert not [n for n in doc.all() if "href" in n.attrs or "srcset" in n.attrs]
    # the one script has no source and nothing in the report loads anything
    raw = report.render_report(runs["scored"].bundle, audio=report.RelativeAudio(RELATIVE))
    assert len(element_text(raw, "script")) == 1
    assert "<script src" not in raw.lower()
    assert "http:" not in parse(raw).tracer_text()


def test_report_csp_present_and_script_hash_matches(tmp_path: Path) -> None:
    report = report_mod()
    run = _run(tmp_path)
    for embedded in (False, True):
        audio = report.EmbeddedAudio(b"RIFF") if embedded else report.RelativeAudio(RELATIVE)
        raw = report.render_report(run.bundle, audio=audio)
        doc = parse(raw)
        metas = [
            m
            for m in doc.all("meta")
            if m.attrs.get("http-equiv", "").lower() == ("content-security-policy")
        ]
        assert len(metas) == 1
        content = metas[0].attrs["content"]
        (script,) = element_text(raw, "script")
        (style,) = element_text(raw, "style")
        media = "data:" if embedded else "'self'"
        expected = (
            f"default-src 'none'; style-src {sha256_source(style)}; "
            f"script-src {sha256_source(script)}; media-src {media}; "
            "base-uri 'none'; form-action 'none'"
        )
        assert content == expected
        directives = csp_directives(content)
        assert directives["default-src"] == ["'none'"]
        assert not re.search(r"unsafe|\*|https?:|blob:|filesystem:", content)
        # the policy is the first thing in the head, before anything it could govern
        policy_at = raw.index("Content-Security-Policy")
        for later in ("<style", "<script", "<audio", "<body"):
            assert policy_at < raw.index(later), later
        # the module functions give the same strings
        assert report.csp_hash_source(script) == sha256_source(script)
        assert report.content_security_policy(style, script, embedded=embedded) == expected
        assert script == report.SEEK_SCRIPT
        assert style == report.STYLE
    # a changed script has another hash: the policy is not a constant
    assert sha256_source(report.SEEK_SCRIPT) != sha256_source(report.SEEK_SCRIPT + " ")
    # the marker that the repository guard looks for (P3-34)
    assert '<meta name="tracer-report" content="1">' in raw


def test_report_escapes_html_and_script_in_transcript(tmp_path: Path) -> None:
    support = es()
    report = report_mod()
    hostile_lines = [
        ("assistant", "<script>alert(1)</script> and </script><script>evil()</script>"),
        ("user", '"><img src=x onerror=alert(1)> &amp; &#x3c;b&#x3e; \' " <b>bold</b>'),
        ("assistant", "</style><style>*{display:none}</style><meta http-equiv=refresh content=0>"),
        ("user", "<!-- comment --> ]]> <![CDATA[ x ]]> {{7*7}} ${x} %s %(x)s <svg onload=1>"),
    ]
    export = support.export_text(hostile_lines)
    run = _run(tmp_path, export=export, engine=_agreeing(hostile_lines))
    raw = _render(run)
    doc = parse(raw)
    assert structure_violations(doc) == []
    assert len(element_text(raw, "script")) == 1
    assert element_text(raw, "script")[0] == report.SEEK_SCRIPT
    assert raw.count("</script>") == 1
    assert raw.count("</style>") == 1
    assert [n.tag for n in doc.all() if n.tag in ("img", "svg", "b", "meta")].count("meta") == 3
    assert not any(n.tag in ("img", "b", "svg") for n in doc.all() if "ut" not in n.classes)
    assert not [
        n for n in doc.all() if n.tag == "img" or "onerror" in n.attrs or "onload" in n.attrs
    ]
    # every character that matters is escaped, and what the reader sees is the text as received
    first = hostile_lines[1][1]
    assert shown_html(first) in raw
    assert "&lt;img src=x onerror=alert(1)&gt;" in raw
    assert "&quot;&gt;&lt;img" in raw
    assert "&amp;amp;" in raw
    assert "&#x27;" in raw
    turns = export_turns(run.bundle)
    shown = [n for n in doc.by_id("transcript").walk() if "ut" in n.classes]
    joined = "".join(unwrap(n.text()) for n in shown)
    assert joined == "".join(markerize(t.text) for t in turns)
    # the raw document holds no unescaped angle bracket from a transcript
    for needle in ("<script>alert", "<img src=x", "<b>bold", "<!-- comment", "<![CDATA["):
        assert needle not in raw, needle


def test_report_shows_bidi_controls_as_markers(tmp_path: Path) -> None:
    support = es()
    controls = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    lines = [
        ("assistant", f"{support.NAMASKAR} {controls} 1234\u202e9876 \u2069\u202e{support.MALA}"),
        ("user", f"x\u200e y\u200f z\u061c {support.KAAM}"),
        ("assistant", f"{support.DON} {support.TEEN}"),
    ]
    run = _run(tmp_path, export=support.export_text(lines), engine=_agreeing(lines))
    raw = _render(run)
    doc = parse(raw)
    # the embedding, override and isolate controls never appear raw, except FSI/PDI as wrappers
    for char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067":
        assert char not in raw, hex(ord(char))
    uts = doc.ut_nodes()
    assert uts
    for node in uts:
        text = node.text()
        assert text.startswith(FSI)
        assert text.endswith(PDI)
        inner = text[1:-1]
        assert FSI not in inner
        assert PDI not in inner
    assert raw.count(FSI) == len(uts) == raw.count(PDI)
    # ... and each one is shown as its marker
    text = " ".join(unwrap(n.text()) for n in uts)
    for marker in ("[LRE]", "[RLE]", "[PDF]", "[LRO]", "[RLO]", "[LRI]", "[RLI]", "[FSI]", "[PDI]"):
        assert marker in text, marker
    assert "1234[RLO]9876" in text
    # LRM, RLM and ALM are left alone (ADR 0017 I8)
    assert "x\u200e y\u200f z\u061c" in text
    # the markers are what display_safe says, segment by segment, in the turn text
    turns = export_turns(run.bundle)
    shown = "".join(unwrap(n.text()) for n in doc.by_id("transcript").walk() if "ut" in n.classes)
    assert shown == "".join(markerize(t.text) for t in turns)


def test_report_highlights_exact_flag_spans(tmp_path: Path) -> None:
    support = es()
    for threshold, flagged in ((0.3, (False, True, True)), (0.2, (True, True, True))):
        folder = tmp_path / f"t{threshold}"
        folder.mkdir()
        run = _run(folder, threshold=threshold)
        document = flag_document(run.bundle)
        assert [s["flagged"] for s in document["spans"]] == list(flagged)
        assert document == support.base_document(str(threshold), flagged)
        doc = parse(_render(run))
        turns = export_turns(run.bundle)
        # per turn: the segments, in order, are the text of the turn and the marks are the spans
        for turn in turns:
            item = doc.by_id(f"turn-{turn.seq}")
            assert item.attrs["data-seq"] == str(turn.seq)
            box = [n for n in item.walk() if "text" in n.classes and n.tag == "div"]
            assert len(box) == 1
            parts = list(box[0].elements())
            rebuilt = "".join(unwrap(p.text()) for p in parts if "ut" in p.classes)
            assert rebuilt == markerize(turn.text), turn.seq
            wanted = [
                (i, s)
                for i, s in enumerate(document["spans"])
                if s["turn_seq"] == turn.seq and s["flagged"]
            ]
            marks = [p for p in parts if "flag" in p.classes]
            assert len(marks) == len(wanted), turn.seq
            for mark, (index, span) in zip(marks, wanted, strict=True):
                assert mark.tag == "mark"
                assert mark.attrs["data-span"] == str(index)
                assert mark.attrs["data-turn"] == str(turn.seq)
                assert mark.attrs["data-start"] == str(span["start"])
                assert mark.attrs["data-end"] == str(span["end"])
                if span["start"] == span["end"]:
                    assert "gap" in mark.classes
                    assert "ut" not in mark.classes
                    assert mark.text() == "[+]"
                else:
                    assert "gap" not in mark.classes
                    assert "ut" in mark.classes
                    assert unwrap(mark.text()) == markerize(turn.text[span["start"] : span["end"]])
            # a zero-width mark sits at its offset: the text before it is what comes before
            before = ""
            for part in parts:
                if "flag" in part.classes and "gap" in part.classes:
                    offset = int(part.attrs["data-start"])
                    assert before == markerize(turn.text[:offset])
                if "ut" in part.classes:
                    before += unwrap(part.text())
            assert item.attrs["data-flags"] == str(len(wanted))
        # no segment is empty: an empty piece of text is never written as an element
        assert all(unwrap(n.text()) != "" for n in doc.ut_nodes())
        # exactly the flagged spans are listed in the table, in span order
        rows = doc.with_class("flag-row", "tr")
        assert [r.attrs["data-span"] for r in rows] == [
            str(i) for i, s in enumerate(document["spans"]) if s["flagged"]
        ]
        for row in rows:
            span = document["spans"][int(row.attrs["data-span"])]
            cells = {c.attrs["class"]: c.text() for c in row.elements()}
            assert cells["turn"] == f"turn {span['turn_seq']}"
            assert cells["confidence"] == span["confidence"]
            assert cells["offsets"] == f"{span['start']}-{span['end']}"
            assert cells["boundary"] == ("yes" if span["near_chunk_boundary"] else "no")
            role = {0: "agent", 1: "caller", 2: "agent"}[span["turn_seq"]]
            assert cells["role"] == role
    # nothing is marked that the document did not flag
    assert len(doc.with_class("flag", "mark")) == sum(flagged)


def test_report_relative_audio_by_default_embed_opt_in(tmp_path: Path) -> None:
    report = report_mod()
    run = _run(tmp_path)
    raw = report.render_report(run.bundle, audio=report.RelativeAudio("../in/call.wav"))
    doc = parse(raw)
    (player,) = doc.all("audio")
    assert player.attrs["id"] == "player"
    assert "controls" in player.attrs
    assert player.attrs["src"] == "../in/call.wav"
    assert "data:audio" not in raw
    assert not doc.has_id("embed-warning")
    assert csp_directives(doc.all("meta")[1].attrs["content"])["media-src"] == ["'self'"]
    # the opt-in embeds the exact bytes and says what that means
    wav = (tmp_path / "call.wav").read_bytes()
    embedded = report.render_report(run.bundle, audio=report.EmbeddedAudio(wav))
    edoc = parse(embedded)
    (eplayer,) = edoc.all("audio")
    match = DATA_AUDIO.fullmatch(eplayer.attrs["src"])
    assert match is not None
    import base64

    assert base64.b64decode(match.group(1), validate=True) == wav
    assert csp_directives(edoc.all("meta")[1].attrs["content"])["media-src"] == ["data:"]
    warning = edoc.by_id("embed-warning").text()
    assert "personal data" in warning
    assert "do not share" in warning
    assert structure_violations(edoc) == []
    # the two arguments are checked
    with pytest.raises(TypeError):
        report.render_report(run.bundle, audio="../in/call.wav")
    with pytest.raises(TypeError):
        report.EmbeddedAudio("text")
    with pytest.raises(ValueError, match="empty"):
        report.EmbeddedAudio(b"")
    for bad in (
        "", "http://x/a.wav", "//host/a.wav", "/etc/passwd", "a b.wav", 'a"b.wav', "a:b.wav",
        "a\nb.wav", "a?b.wav", "a#b.wav", "a\\b.wav", "a<b.wav", "..//a.wav", "a\x00b.wav",
    ):  # fmt: skip
        with pytest.raises(ValueError, match="relative"):
            report.RelativeAudio(bad)
    for good in ("a.wav", "../a.wav", "../in/call%20one.wav", "x/y/z.wav", "a%C3%A9.wav"):
        assert report.RelativeAudio(good).src == good


def test_report_relative_audio_src_is_url_encoded(tmp_path: Path) -> None:
    report = report_mod()
    out = tmp_path / "out"
    cases = {
        "call.wav": "../in/call.wav",
        "my call #1 %20 ?.wav": "../in/my%20call%20%231%20%2520%20%3F.wav",
        "caf\u00e9 \u092e\u0930\u093e\u0920\u0940.wav": (
            "../in/caf%C3%A9%20%E0%A4%AE%E0%A4%B0%E0%A4%BE%E0%A4%A0%E0%A5%80.wav"
        ),
        "q\"uote's&<>.wav": "../in/q%22uote%27s%26%3C%3E.wav",
        "a:b.wav": "../in/a%3Ab.wav",
    }
    for name, expected in cases.items():
        got = report.relative_audio_src(tmp_path / "in" / name, out)
        assert got == expected, name
        assert re.fullmatch(r"[A-Za-z0-9._~%/-]+", got)
        report.RelativeAudio(got)
    # the same folder, a child folder and a far folder
    assert report.relative_audio_src(out / "a.wav", out) == "a.wav"
    assert report.relative_audio_src(out / "sub" / "a.wav", out) == "sub/a.wav"
    assert report.relative_audio_src(tmp_path / "x" / "y" / "a.wav", out) == "../x/y/a.wav"


def test_report_never_claims_accuracy(tmp_path: Path) -> None:
    report = report_mod()
    seen = 0
    for name, run in _scenarios(tmp_path).items():
        for audio in (report.RelativeAudio(RELATIVE), report.EmbeddedAudio(b"RIFF")):
            doc = parse(report.render_report(run.bundle, audio=audio))
            hits = DENY_LIST.findall(doc.tracer_text())
            assert not hits, (name, hits)
            for node in doc.all():
                for key, value in node.attrs.items():
                    if key not in ("src", "content") and "ut" not in node.classes:
                        assert not DENY_LIST.search(value), (name, key, value)
            lowered = doc.tracer_text().lower()
            for phrase in ("accuracy", "error rate", "word error", "character error"):
                assert phrase not in lowered, (name, phrase)
            seen += 1
    assert seen >= 18
    # the value is named by what it is, and the page says neither text is a reference
    doc = parse(_render(_run(fresh(tmp_path, "again"))))
    assert doc.by_id("agreement").text().startswith("agreement distance (MER): ")
    note = doc.by_id("what-this-is").text().lower()
    assert "neither" in note
    assert "reference" in note
    assert "recommend" not in doc.tracer_text().lower()
    assert "suggest" not in doc.tracer_text().lower()


def test_report_shows_language_declared_by_user(tmp_path: Path) -> None:
    support = es()
    run = _run(tmp_path)
    doc = parse(_render(run))
    node = doc.by_id("declared-language")
    assert node.text() == "language declared by user: mr"
    assert node.attrs["data-language"] == "mr"
    assert "detected" not in doc.tracer_text().lower()
    # the declaration is the export's, and the results record that source
    export = next(t for t in run.bundle.transcripts if t.source == "agent_export")
    assert export.declared_language.language == "mr"
    assert {r.language_source for r in run.bundle.results} == {"declared"}
    # the same fact in a call that was not scored
    folder = tmp_path / "ns"
    folder.mkdir()
    other = _run(
        folder, engine=support.FakeEngine(chunk_scripts=support.scripts(languages=["mr", "hi"]))
    )
    assert (
        parse(_render(other)).by_id("declared-language").text() == "language declared by user: mr"
    )


def test_report_has_no_absolute_paths(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs, extra=["--embed-audio"]))
    assert result.code == 0
    embedded = the_report(inputs.out)
    folder = tmp_path / "plain"
    folder.mkdir()
    plain_inputs = write_inputs(folder, wav_name="my call.wav")
    plain = run_cli(score_args(plain_inputs))
    assert plain.code == 0
    raw = the_report(plain_inputs.out)
    assert 'src="../in/my%20call.wav"' in raw
    for text in (embedded, raw):
        searchable = DATA_AUDIO.sub("data:audio/wav;base64,", text)
        for needle in (
            str(tmp_path), str(tmp_path.resolve()), str(Path.cwd()), os.path.expanduser("~"),
            tmp_path.resolve().as_uri(), "file:", "call.wav",
        ):  # fmt: skip
            if needle == "call.wav" and text is raw:
                continue
            assert needle not in searchable, needle
        assert not ABSOLUTE_PATH.search(searchable), ABSOLUTE_PATH.findall(searchable)
    # the artifact record keeps its absolute file URI (a stored fact), the report does not repeat it
    stored = load_stored_run(plain_inputs.out, plain.value("bundle"))
    assert stored.artifacts[0].uri.startswith("file:///")
    assert stored.artifacts[0].uri not in raw


def test_key_never_in_store_and_report_outputs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # the report channel of the key canary (the store channel, without a provider, is in the batch D
    # tests): the key goes to Sarvam through a mock transport, from the environment and from an
    # env file, and no byte of any output holds it
    support = es()
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ("httpx", "httpcore", "tracer_cli", "tracer_evalproviders", "tracer_evalcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    key = support.KEY_CANARY
    export_with_canary = support.export_text(
        [*support.EXPORT_LINES, ("assistant", support.TEXT_CANARY)]
    )
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"DECOY=1\nSARVAM_API_KEY={key}\n", "utf-8")
    streams = []
    for index, via_file in enumerate((False, True)):
        folder = tmp_path / f"run{index}"
        folder.mkdir()
        inputs = write_inputs(folder, export=export_with_canary)
        options = {"--env-file": str(env_file)} if via_file else {}
        script = SarvamScript(
            [
                {
                    "request_id": "20261025_a",
                    "transcript": " ".join(support.STT_TEXTS),
                    "language_code": "mr-IN",
                },
                {
                    "request_id": "20261025_b",
                    "transcript": support.TEXT_CANARY,
                    "language_code": "mr-IN",
                },
            ]
        )
        result = run_cli(
            score_args(inputs, options=options),
            production=True,
            transport=script.transport(),
            environ={} if via_file else {"SARVAM_API_KEY": key},
        )
        assert result.code == 0, result.err
        assert {r.headers["api-subscription-key"] for r in script.requests} == {key}
        files = tree_bytes(inputs.out)
        report = the_report(inputs.out)
        assert support.TEXT_CANARY in report  # the check is not vacuous
        assert support.TEXT_CANARY.encode() in files["tracer.sqlite"]
        for name, data in files.items():
            assert key.encode() not in data, name
            assert b"SARVAM_API_KEY" not in data or name == "tracer.sqlite", name
        assert "SARVAM_API_KEY" not in report
        assert str(env_file) not in report
        assert str(env_file).encode() not in files["tracer.sqlite"]
        assert key not in result.out + result.err
        assert "SARVAM_API_KEY" not in result.out + result.err
        streams.append(result.out + result.err)
    assert key not in caplog.text
    assert key not in "".join(streams)
    # Phase 4 batch E spec change S-E4 (SCOPE P4-24: "extended to three keys"): the same check over
    # a simulated run's three provider roles, a bearer token and every output channel
    from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
        assert_sim_three_keys_never_in_outputs,
    )

    assert_sim_three_keys_never_in_outputs(tmp_path / "simulated", caplog)


# ------------------------------------------------------------------ structure and content


def test_report_structure_ids_and_numbers(tmp_path: Path) -> None:
    run = _run(tmp_path)
    doc = parse(_render(run))
    cli_lines = run_cli_lines(tmp_path)
    assert doc.by_id("report").tag == "main"
    assert doc.root.elements()[0].tag == "html"
    assert doc.all("html")[0].attrs["lang"] == "en"
    assert doc.all("meta")[0].attrs["charset"] == "utf-8"
    status = doc.by_id("status")
    assert status.attrs["data-outcome"] == "scored"
    assert status.text() == "result: scored"
    # the numbers are the numbers of the command's output, with their counts
    for element_id, label in (
        ("agreement", "agreement distance (MER)"),
        ("tokens", "tokens"),
        ("flag-count", "flags"),
        ("flagged-share", "flagged share of export tokens"),
        ("flag-turns", "export turns with a flag"),
        ("review-budget", "review budget"),
    ):
        assert doc.by_id(element_id).text() == f"{label}: {cli_lines[label]}", element_id
    agreement = doc.by_id("agreement")
    assert agreement.attrs["data-value"] == "0.25"
    counts = ("hits", "substitutions", "deletions", "insertions")
    assert [agreement.attrs[f"data-{k}"] for k in counts] == ["9", "1", "1", "1"]
    assert doc.by_id("flag-count").attrs["data-value"] == "2"
    share = doc.by_id("flagged-share")
    assert (share.attrs["data-numerator"], share.attrs["data-denominator"]) == ("1", "11")
    budget = doc.by_id("review-budget")
    assert (budget.attrs["data-max"], budget.attrs["data-no-limit"], budget.attrs["data-met"]) == (
        "0.25", "false", "true",
    )  # fmt: skip
    # provenance
    assert doc.by_id("scorer-version").text() == __import__("tracer_evalcore").__version__
    assert unwrap(doc.by_id("stt-provider").text()) == "fake"
    assert unwrap(doc.by_id("stt-model").text()) == "fake-model-1"
    assert doc.by_id("stt-model-source").text() == "requested"
    assert doc.by_id("normaliser").text() == "mr-Deva/1"
    assert doc.by_id("threshold").text() == "0.3"
    assert doc.by_id("signal").text() == "stt_disagreement"
    # the audio, privacy, snapshot and warning facts
    audio = next(a for a in run.bundle.artifacts)
    facts = doc.by_id("audio-facts")
    assert facts.attrs["data-sha256"] == audio.sha256
    assert facts.attrs["data-bytes"] == str(audio.size)
    privacy = doc.by_id("privacy")
    assert "consent: unknown" in privacy.text()
    assert "delete on expiry: no" in privacy.text()
    assert "retain until: not set" in privacy.text()
    note = doc.by_id("snapshot-note").text()
    assert "recorded_call_no_outcome_check" in note
    assert "never run" in note
    assert doc.by_id("warning-count").text() == "ingest warnings: 0"
    # every export turn is listed once, in order, with its role
    items = doc.by_id("transcript").all("li")
    assert [i.attrs["data-seq"] for i in items] == ["0", "1", "2"]
    assert [i.attrs["data-role"] for i in items] == ["agent", "caller", "agent"]


def run_cli_lines(tmp_path: Path) -> dict[str, str]:
    folder = tmp_path / "cli"
    folder.mkdir()
    inputs = write_inputs(folder)
    result = run_cli(score_args(inputs))
    assert result.code == 0
    return {line.partition(": ")[0]: line.partition(": ")[2] for line in result.lines}


def test_report_histogram_counts_every_span_with_integer_bins(tmp_path: Path) -> None:
    for threshold in (0.3, 0.2, 0.5):
        folder = tmp_path / f"h{threshold}"
        folder.mkdir()
        run = _run(folder, threshold=threshold)
        document = flag_document(run.bundle)
        doc = parse(_render(run))
        rows = doc.by_id("histogram").all("tr")
        bins = [r for r in rows if "bin" in r.classes]
        assert [r.attrs["data-bin"] for r in bins] == [str(i) for i in range(10)]
        expected_spans = [0] * 10
        expected_flagged = [0] * 10
        for span in document["spans"]:
            index = min(9, span["edit_distance"] * 10 // span["key_length"])
            expected_spans[index] += 1
            expected_flagged[index] += 1 if span["flagged"] else 0
        for row in bins:
            index = int(row.attrs["data-bin"])
            assert int(row.attrs["data-spans"]) == expected_spans[index]
            assert int(row.attrs["data-flagged"]) == expected_flagged[index]
            assert row.attrs["data-low"] == f"0.{index}"
            assert row.attrs["data-high"] == (f"0.{index + 1}" if index < 9 else "1.0")
        assert sum(int(r.attrs["data-spans"]) for r in bins) == len(document["spans"])
        total = doc.by_id("histogram-total")
        assert total.text() == f"spans: {len(document['spans'])}"
    text = parse(_render(_run(fresh(tmp_path, "x")))).tracer_text().lower()
    assert "histogram of the confidence of every span" in text
    assert "uncalibrated" in text
    assert "not a probability" in text


def test_report_review_budget_wording(tmp_path: Path) -> None:
    cases = [
        (
            "within",
            {"budget": 0.25},
            "review budget: 0.25 (flagged share is within it)",
            "false",
            "true",
        ),
        (
            "over",
            {"budget": 0.05},
            "review budget: 0.05 (flagged share is over it)",
            "false",
            "false",
        ),
        (
            "placeholder",
            {"threshold": 0.5, "budget": 1.0},
            "review budget: 1.0 (no limit)",
            "true",
            None,
        ),
    ]
    for name, options, text, no_limit, met in cases:
        folder = tmp_path / name
        folder.mkdir()
        doc = parse(_render(_run(folder, **options)))
        node = doc.by_id("review-budget")
        assert node.text() == text
        assert node.attrs["data-no-limit"] == no_limit
        assert node.attrs.get("data-met") == met


def test_report_usage_table_and_cost(tmp_path: Path) -> None:
    support = es()
    doc = parse(_render(_run(tmp_path)))
    assert doc.by_id("usage-requests").text() == "2"
    assert doc.by_id("usage-audio-seconds").text() == "3"
    assert doc.by_id("usage-cost").text() == "not available (no_price_for_model)"
    note = " ".join(doc.by_id("usage-note").text().split())
    assert "must also price requests" in note
    # priced from a user file: the amount, no note
    folder = tmp_path / "priced"
    folder.mkdir()
    inputs = write_inputs(folder)
    price = folder / "price.json"
    price.write_text(price_file_text(), "utf-8")
    result = run_cli(
        score_args(inputs, options={"--price-file": str(price)}),
        production=True,
        transport=SarvamScript().transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    priced = parse(the_report(inputs.out))
    assert priced.by_id("usage-cost").text() == "0.0003 USD (computed from the supplied USD price)"
    assert not priced.has_id("usage-note")
    assert priced.by_id("usage-requests").text() == "2"
    # nothing printed as zero for want of a price
    assert "0 USD" not in priced.tracer_text()
    assert "0 USD" not in doc.tracer_text()


def test_report_for_calls_that_are_not_scored(tmp_path: Path) -> None:
    summary = summary_mod()
    expected = {
        "language": ("not_scored", ["unsupported language detected"]),
        "no tokens": ("not_scored", ["no tokens in the stt transcript"]),
        "stt failed": ("stt_failed", ["speech-to-text failed"]),
        "no agent line": ("not_scored", ["missing input agent_transcript"]),
    }
    runs = _scenarios(tmp_path)
    for name, (outcome, reasons) in expected.items():
        run = runs[name]
        raw = _render(run)
        doc = parse(raw)
        assert doc.by_id("status").attrs["data-outcome"] == outcome, name
        assert doc.by_id("status").text() == "result: not scored"
        items = [li.text() for li in doc.by_id("not-scored").all("li")]
        assert items[: len(reasons)] == [f"not scored: {r}" for r in reasons], name
        assert list(summary.not_scored_reasons(run.bundle)) == reasons
        for gone in (
            "agreement",
            "tokens",
            "flag-count",
            "flagged-share",
            "review-budget",
            "histogram",
            "flags",
        ):
            assert not doc.has_id(gone), (name, gone)
        for kept in ("usage", "transcript", "declared-language"):
            assert doc.has_id(kept), (name, kept)
        assert structure_violations(doc) == []
        # the export is still shown, with no mark
        assert not doc.with_class("flag", "mark")
        assert "0.0" not in doc.by_id("status").text()
    failed = parse(_render(runs["stt failed"]))
    codes = [c.text() for c in failed.by_id("warnings").all("code")]
    assert codes == ["stt_failed"]
    assert failed.by_id("warning-count").text() == "ingest warnings: 1"
    detail = failed.by_id("not-scored").all("li")[1].text()
    assert detail.startswith("detail: ")
    assert "SttServerError" in detail
    # a scored call has no such list
    assert not parse(_render(runs["scored"])).has_id("not-scored")


def test_report_with_no_flags_says_so(tmp_path: Path) -> None:
    run = _scenarios(tmp_path)["no flags"]
    doc = parse(_render(run))
    assert doc.by_id("flag-count").attrs["data-value"] == "0"
    assert doc.by_id("flagged-share").text().startswith("flagged share of export tokens: 0 of 11")
    assert doc.by_id("agreement").text().startswith("agreement distance (MER): 0.0 (0 of 11; H 11")
    assert not doc.with_class("flag-row", "tr")
    assert not doc.with_class("flag", "mark")
    assert doc.by_id("flags").all("p")[0].text() == "no span reached the threshold"
    assert all(
        r.attrs["data-flagged"] == "0"
        for r in doc.by_id("histogram").all("tr")
        if "bin" in r.classes
    )


def test_report_seek_controls_and_script_are_inert(tmp_path: Path) -> None:
    report = report_mod()
    run = _run(tmp_path, threshold=0.2)
    doc = parse(_render(run))
    buttons = doc.with_class("seek", "button")
    document = flag_document(run.bundle)
    with_range = [s for s in document["spans"] if s["flagged"] and "audio_start_ms" in s]
    assert len(buttons) == len(with_range) == 2
    for button, span in zip(buttons, with_range, strict=True):
        assert button.attrs["type"] == "button"
        assert button.attrs["data-seek-start-ms"] == str(span["audio_start_ms"])
        assert button.attrs["data-seek-end-ms"] == str(span["audio_end_ms"])
    no_range = [r for r in doc.with_class("flag-row", "tr") if not r.elements()[-1].all("button")]
    assert len(no_range) == 1
    assert no_range[0].elements()[-1].text() == "no audio range"
    script = report.SEEK_SCRIPT
    assert script.isascii()
    assert 0 < len(script) <= 2000
    for needed in ("currentTime", "data-seek-start-ms", "player"):
        assert needed in script, needed
    for banned in (
        "eval", "Function", "fetch", "XMLHttpRequest", "import", "document.write", "innerHTML",
        "outerHTML", "location", "open(", "WebSocket", "//", "</", "<!--", "http", "cookie",
        "localStorage", "postMessage", "setTimeout", "setInterval", "src",
    ):  # fmt: skip
        assert banned not in script, banned
    # no handler attribute anywhere: the script attaches its own listener
    assert not [n for n in doc.all() if any(k.startswith("on") for k in n.attrs)]
    assert "addEventListener" in script


def test_report_safe_html_is_display_safe_then_escape_with_control_markers() -> None:
    report = report_mod()
    samples = [
        "", "plain", 'a"b\'c<d>e&f', "\u202e9876", "x\u2069\u202ey", "tab\there", "nul\x00here",
        "c1\x85x", "ls\u2028ps\u2029", "del\x7f", "\u200d\u200c joiners", "\U0001f600 astral",
        "\u092e\u0930\u093e\u0920\u0940", "line\r", "esc\x1b[31m", "\ufffe\uffff",
        "\u200e\u200f\u061c",
    ]  # fmt: skip
    for text in samples:
        got = report.safe_html(text)
        assert got == shown_html(text), repr(text)
        assert got.startswith(FSI)
        assert got.endswith(PDI)
        assert all(unicodedata.category(c) != "Cc" for c in got), repr(text)
        for raw_char in "<>\"'":
            assert raw_char not in got, repr(text)
    assert report.safe_html("nul\x00here") == f"{FSI}nul[U+0000]here{PDI}"
    assert report.safe_html("a\u202eb") == f"{FSI}a[RLO]b{PDI}"
    with pytest.raises(TypeError):
        report.safe_html(b"bytes")


def test_report_is_reproducible_and_holds_no_record_ids(tmp_path: Path) -> None:
    first = _render(_run(fresh(tmp_path, "a")))
    second = _render(_run(fresh(tmp_path, "b")))
    assert first == second
    assert first.isascii() is False  # the Devanagari text is written as it is
    assert not re.search(r"\b[a-z]{2,6}_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-", first)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", first)
    assert es().CALL_ID not in first
    assert first.startswith("<!DOCTYPE html>")


def test_report_outcome_of_and_argument_checks(tmp_path: Path) -> None:
    report = report_mod()
    runs = _scenarios(tmp_path)
    assert report.outcome_of(runs["scored"].bundle) == "scored"
    assert report.outcome_of(runs["language"].bundle) == "not_scored"
    assert report.outcome_of(runs["stt failed"].bundle) == "stt_failed"
    for run in runs.values():
        assert report.outcome_of(run.bundle) == run.result.outcome
    with pytest.raises(TypeError):
        report.render_report(object(), audio=report.RelativeAudio(RELATIVE))
    import inspect

    parameters = inspect.signature(report.render_report).parameters
    assert list(parameters) == ["bundle", "audio"]
    assert parameters["audio"].kind is inspect.Parameter.KEYWORD_ONLY
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())


def test_report_the_command_writes_is_the_report_of_the_stored_run(tmp_path: Path) -> None:
    report = report_mod()
    inputs = write_inputs(tmp_path)
    result = run_cli(score_args(inputs))
    assert result.code == 0
    written = the_report(inputs.out)
    stored = load_stored_run(inputs.out, result.value("bundle"))
    again = report.render_report(stored, audio=report.RelativeAudio("../in/call.wav"))
    assert again == written
    assert result.value("report") == str(report_files(inputs.out)[0])


def test_report_names_the_inert_check_and_the_placeholder_texts(tmp_path: Path) -> None:
    doc = parse(_render(_run(tmp_path)))
    note = " ".join(doc.by_id("snapshot-note").text().split())
    assert "recorded_call_no_outcome_check" in note
    assert "never run" in note
    assert "schema 0.0.1" in note
    assert "not a judgement" in note
    # what the run is: a recorded call, no simulation
    intro = " ".join(doc.by_id("what-this-is").text().lower().split())
    assert "recorded call" in intro
    assert "no call was simulated" in intro


def test_report_flags_at_the_head_of_a_turn_leave_no_empty_piece(tmp_path: Path) -> None:
    support = es()
    export = support.export_text([("assistant", f"{support.DON} {support.TEEN} {support.JEVAN}")])
    cases = {
        # an extra STT word before the first export token: a zero-width flag at offset 0
        "insertion at the head": (
            f"{support.NAMASKAR} {support.DON} {support.TEEN} {support.JEVAN}",
            True,
        ),
        # the first export token replaced: a flagged span that starts at offset 0
        "substitution at the head": (f"{support.MALA} {support.TEEN} {support.JEVAN}", False),
    }
    for index, (name, (text, is_gap)) in enumerate(cases.items()):
        engine = support.FakeEngine(chunk_scripts=support.scripts([text, ""], phrases=None))
        run = _run(fresh(tmp_path, f"h{index}"), export=export, engine=engine, threshold=0.5)
        doc = parse(_render(run))
        box = next(n for n in doc.by_id("turn-0").walk() if n.tag == "div" and "text" in n.classes)
        parts = box.elements()
        first = parts[0]
        assert first.tag == "mark", name
        assert first.attrs["data-start"] == "0", name
        assert ("gap" in first.classes) is is_gap, name
        assert all(unwrap(n.text()) != "" for n in doc.ut_nodes()), name
        rebuilt = "".join(unwrap(p.text()) for p in parts if "ut" in p.classes)
        assert rebuilt == markerize(f"{support.DON} {support.TEEN} {support.JEVAN}")


def test_report_histogram_bins_use_integer_arithmetic_on_varied_confidences(
    tmp_path: Path,
) -> None:
    support = es()
    export = support.export_text([("assistant", "ab kaa abcdefghij kbb abcdefghij kcc xyz kdd")])
    text = "ac kaa abcdefgxyz kbb abcdefghix kcc xyw kdd"
    engine = support.FakeEngine(chunk_scripts=support.scripts([text, ""], phrases=None))
    run = _run(tmp_path, export=export, engine=engine, threshold=0.3)
    document = flag_document(run.bundle)
    pairs = sorted((s["edit_distance"], s["key_length"]) for s in document["spans"])
    assert pairs == [(1, 2), (1, 3), (1, 10), (3, 10)]
    doc = parse(_render(run))
    rows = {int(r.attrs["data-bin"]): r for r in doc.with_class("bin", "tr")}
    spans = {i: int(r.attrs["data-spans"]) for i, r in rows.items() if int(r.attrs["data-spans"])}
    assert spans == {1: 1, 3: 2, 5: 1}
    flagged = {
        i: int(r.attrs["data-flagged"]) for i, r in rows.items() if int(r.attrs["data-flagged"])
    }
    assert flagged == {3: 2, 5: 1}
    # a total disagreement lands in the last bin (confidence 1)
    assert min(9, 3 * 10 // 3) == 9


def test_report_labels_a_flag_in_a_caller_line_with_its_role(tmp_path: Path) -> None:
    support = es()
    caller_changed = support.STT_TEXTS[0].replace(
        support.GHAR, support.GHAR[0] + "\u093e" + support.GHAR[1]
    )
    engine = support.FakeEngine(
        chunk_scripts=support.scripts([caller_changed, support.STT_TEXTS[1]], phrases=None)
    )
    run = _run(tmp_path, engine=engine, threshold=0.3)
    document = flag_document(run.bundle)
    caller_spans = [s for s in document["spans"] if s["turn_seq"] == 1]
    assert [s["flagged"] for s in caller_spans] == [True]
    doc = parse(_render(run))
    rows = {r.attrs["data-turn-seq"]: r for r in doc.with_class("flag-row", "tr")}
    cells = {c.attrs["class"]: c.text() for c in rows["1"].elements()}
    assert cells["role"] == "caller"
    assert {c.attrs["class"]: c.text() for c in rows["2"].elements()}["role"] == "agent"
    assert doc.by_id("turn-1").attrs["data-role"] == "caller"
    assert doc.by_id("turn-1").attrs["data-flags"] == "1"


def test_report_shows_the_retention_the_command_was_given(tmp_path: Path) -> None:
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(
            inputs,
            options={
                "--retention": "delete-after:2027-03-01T12:30:00+02:00",
                "--consent": "obtained",
            },
        )
    )
    assert result.code == 0, result.err
    text = parse(the_report(inputs.out)).by_id("privacy").text()
    assert text == (
        "personal data: yes. consent: obtained. delete on expiry: yes. "
        "retain until: 2027-03-01T10:30:00Z."
    )


def test_report_usage_of_a_failed_transcription_is_what_happened(tmp_path: Path) -> None:
    run = _scenarios(tmp_path)["stt failed"]
    doc = parse(_render(run))
    assert doc.by_id("usage-requests").text() == "1"
    assert doc.by_id("usage-audio-seconds").text() != "3"
    assert doc.by_id("usage-audio-seconds").text() != "none"
    assert doc.by_id("usage-cost").text() == "not available (no_price_for_model)"


def test_report_and_command_make_a_hostile_failure_text_safe(tmp_path: Path) -> None:
    support = es()
    hostile = "x\u202e<b>\x00y"
    failure = __import__("tracer_evalcore.stt", fromlist=["x"]).SttResponseInvalid(
        chunk_index=1, reason=hostile
    )
    engine = support.FakeEngine(fail_at=1, failure=failure)
    inputs = write_inputs(fresh(tmp_path, "cli"))
    result = run_cli(score_args(inputs), engine=engine)
    assert result.code == 3
    detail = result.value("detail")
    assert detail.isascii()
    assert "\x00" not in result.out
    assert "?" in detail
    raw = the_report(inputs.out)
    assert "\u202e" not in raw
    assert "<b>" not in raw
    doc = parse(raw)
    item = doc.by_id("not-scored").all("li")[1]
    shown = [n for n in item.walk() if "ut" in n.classes]
    assert len(shown) == 1
    assert "[RLO]" in unwrap(shown[0].text())
    assert "[U+0000]" in unwrap(shown[0].text())


def _heard_cells(doc: Any) -> dict[str, Any]:
    """The flag rows by span index: {index: (export cell, heard cell)}."""
    rows = {}
    for row in doc.with_class("flag-row", "tr"):
        cells = {c.attrs["class"]: c for c in row.elements()}
        rows[row.attrs["data-span"]] = (row, cells["exportside"], cells["heard"])
    return rows


def test_report_shows_stt_text_beside_each_flag(tmp_path: Path) -> None:
    support = es()
    run = _run(tmp_path, threshold=0.2)
    document = flag_document(run.bundle)
    doc = parse(_render(run))
    heads = [th.text() for th in doc.by_id("flags-table").all("th")]
    assert "agent export" in heads
    assert "STT heard" in heads
    assert heads.index("agent export") + 1 == heads.index("STT heard")
    rows = _heard_cells(doc)
    assert sorted(rows) == ["0", "1", "2"]
    # span 0: PAANI in the export, PANI heard (STT token 2 of the first chunk)
    row, exported, heard = rows["0"]
    assert unwrap(exported.all("span")[0].text()) == markerize(support.PAANI)
    pieces = heard.all("span")
    assert [unwrap(p.text()) for p in pieces] == [markerize(support.PANI)]
    assert "heard-piece" in pieces[0].classes
    assert "ut" in pieces[0].classes
    assert pieces[0].attrs["data-stt-turn"] == "0"
    assert (row.attrs["data-stt-token-start"], row.attrs["data-stt-token-end"]) == ("2", "3")
    # span 1: TEEN was not heard
    _, exported, heard = rows["1"]
    assert unwrap(exported.all("span")[0].text()) == markerize(support.TEEN)
    assert [p.text() for p in heard.all("span")] == ["nothing heard"]
    # span 2: ABHI was heard and is not in the export (the last STT token, in the second chunk)
    row, exported, heard = rows["2"]
    assert [p.text() for p in exported.all("span")] == ["not in export"]
    assert [unwrap(p.text()) for p in heard.all("span")] == [markerize(support.ABHI)]
    assert heard.all("span")[0].attrs["data-stt-turn"] == "1"
    assert (row.attrs["data-stt-token-start"], row.attrs["data-stt-token-end"]) == ("10", "11")
    # the span of the document and its STT tokens agree with what is shown
    assert [(s["stt_token_start"], s["stt_token_end"]) for s in document["spans"]] == [
        (2, 3),
        (8, 8),
        (10, 11),
    ]
    # the audio cell stays last, and the spans under the threshold show no STT text
    for r in doc.with_class("flag-row", "tr"):
        assert r.elements()[-1].attrs["class"] == "audio"
    only_two = parse(_render(_run(fresh(tmp_path, "t3"), threshold=0.3)))
    assert sorted(_heard_cells(only_two)) == ["1", "2"]
    assert support.PANI not in only_two.by_id("flags").text()


def test_report_flag_without_stt_tokens_is_labelled(tmp_path: Path) -> None:
    run = _run(tmp_path, threshold=0.3)
    doc = parse(_render(run))
    rows = _heard_cells(doc)
    _, exported, heard = rows["1"]  # a deletion: an export token with no STT token
    assert heard.all("span")[0].classes == {"none"}
    assert heard.text() == "nothing heard"
    assert not [n for n in heard.walk() if "ut" in n.classes]
    assert "ut" in exported.all("span")[0].classes
    _, exported, heard = rows["2"]  # an insertion: an STT token with no export token
    assert exported.all("span")[0].classes == {"none"}
    assert exported.text() == "not in export"
    assert not [n for n in exported.walk() if "ut" in n.classes]
    assert [n for n in heard.walk() if "ut" in n.classes]


def test_report_stt_text_is_escaped_and_marked(tmp_path: Path) -> None:
    support = es()
    report = report_mod()
    export = support.export_text([("assistant", f"{support.DON} {support.TEEN} {support.JEVAN}")])
    hostile = "aaa<script>alert(1)</script>\x00\u202e&\"' \u2069zzz"
    cases = {
        "markup, bidi and NUL": hostile,
        "a huge body": "q" * 150_000,
    }
    for index, (name, words) in enumerate(cases.items()):
        heard_text = f"{support.DON} {words} {support.JEVAN}"
        engine = support.FakeEngine(chunk_scripts=support.scripts([heard_text, ""], phrases=None))
        run = _run(fresh(tmp_path, f"e{index}"), export=export, engine=engine, threshold=0.5)
        raw = report.render_report(run.bundle, audio=report.RelativeAudio(RELATIVE))
        doc = parse(raw)
        assert structure_violations(doc) == [], name
        assert external_request_violations(doc, embedded=False) == [], name
        (row,) = doc.with_class("flag-row", "tr")
        heard = next(c for c in row.elements() if "heard" in c.classes)
        (piece,) = heard.all("span")
        first, last = ("aaa", "zzz") if index == 0 else ("q", "q")
        start = heard_text.index(first)
        end = heard_text.rindex(last) + len(last)
        assert unwrap(piece.text()) == markerize(heard_text[start:end]), name
        assert piece.text().startswith(FSI)
        assert piece.text().endswith(PDI)
        for char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\x00":
            assert char not in raw, (name, hex(ord(char)))
        assert "<script>alert" not in raw
        if index == 0:
            assert "[RLO]" in raw
            assert "[U+0000]" in raw
            assert "&lt;script&gt;" in raw


def test_stt_text_never_reaches_stdout_the_call_log_the_flags_metadata_or_the_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    support = es()
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    heard = f"{support.STT_TEXTS[0]} {support.TEXT_CANARY}"
    bodies = [
        {"request_id": "20261025_a", "transcript": heard, "language_code": "mr-IN"},
        {"request_id": "20261025_b", "transcript": support.STT_TEXTS[1], "language_code": "mr-IN"},
    ]
    inputs = write_inputs(tmp_path)
    result = run_cli(
        score_args(inputs),
        production=True,
        transport=SarvamScript(bodies).transport(),
        environ={"SARVAM_API_KEY": support.KEY_CANARY},
    )
    assert result.code == 0, result.err
    assert support.TEXT_CANARY in the_report(inputs.out)  # the check is not vacuous
    assert support.TEXT_CANARY not in result.out + result.err
    assert support.TEXT_CANARY not in caplog.text
    log = (inputs.out / "sarvam_call_log.jsonl").read_text("utf-8")
    assert support.TEXT_CANARY not in log
    stored = load_stored_run(inputs.out, result.value("bundle"))
    for item in stored.results:
        assert support.TEXT_CANARY not in item.model_dump_json(), item.metric
        assert support.TEXT_CANARY not in json.dumps(item.metadata)
    assert [u for u in stored.usage_records if support.TEXT_CANARY in u.model_dump_json()] == []
    assert support.TEXT_CANARY not in stored.run.model_dump_json()
