# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate condition C7 (decision log "PHASE 3 GATE", BL-255): the flags table of the report has a
bare yes/no column "chunk edge". A flag at a chunk edge may come from where the audio was cut, and
the first and last tokens of the whole recording count as edges too (ADR 0023 rule 10,
``test_near_chunk_boundary_marked``, which this file does not touch). The report therefore carries
one plain sentence under the flags table. Contract: docs/phase3/api-surface-batch-j.md section 3.

The reports come from the replay engine (``--stt fake:<dir>`` over the synthetic Marathi pair of
batch F) and from the fake engine of batch D. All text is invented; the sentence is static.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    DENY_LIST,
    Doc,
    csp_directives,
    element_text,
    es,
    external_request_violations,
    no_price_cost_builder,
    parse,
    report_mod,
    sha256_source,
    structure_violations,
)
from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    CliRun,
    data_support,
    flags_document,
    load_stored_run,
    run_cli,
    score_argv,
)

#: The wording is the qa's choice (the lead auto-approves): exactly this text, one paragraph.
NOTE_ID = "chunk-edge-note"
NOTE_TEXT = (
    "A flag marked yes under chunk edge may come from where the audio was cut for transcription "
    "and not from the two texts. The first and last tokens of the whole recording also count as "
    "chunk edges."
)
RELATIVE = "../in/call.wav"


def _note(doc: Doc) -> Any:
    return doc.by_id(NOTE_ID)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------------ the replay reports


@dataclass
class World:
    root: Path
    wav: Path
    export: Path
    replay: Path
    runs: dict[str, CliRun]

    def report(self) -> tuple[CliRun, str]:
        """``tracer score`` over the base variant, once; the guards of the calling test hold."""
        if "base" not in self.runs:
            out = self.root / "out-base"
            argv = score_argv(self.wav, self.export, out, stt=f"fake:{self.replay}")
            self.runs["base"] = run_cli(argv, out_dir=out, wav=self.wav)
        run = self.runs["base"]
        assert run.code == 0, run.err
        return run, run.report_path.read_text("utf-8")


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    support = data_support()
    root = tmp_path_factory.mktemp("chunk-edge-note")
    inputs = root / "in"
    inputs.mkdir()
    wav = support.make_wav(support.recipe(), inputs / "synthetic_mr.wav")
    export = inputs / "export.txt"
    export.write_bytes(support.data_path("export_synthetic_mr.txt").read_bytes())
    replay = support.write_replay_dir("base", root / "replay-base")
    return World(root, wav, export, replay, {})


# ------------------------------------------------------------------------ fake engine reports


def _fake_run(tmp_path: Path, **options: Any) -> Any:
    support = es()
    options.setdefault("cost_builder", no_price_cost_builder)
    options["config"] = support.configuration(
        threshold=options.pop("threshold", support.TEST_ONLY_FLAG_MIN_CONFIDENCE),
        budget=options.pop("budget", support.TEST_ONLY_REVIEW_BUDGET),
    )
    return support.run_pipeline(tmp_path, **options)


def _fake_report(run: Any, *, embedded: bool = False) -> str:
    report = report_mod()
    audio = report.EmbeddedAudio(b"RIFF") if embedded else report.RelativeAudio(RELATIVE)
    return str(report.render_report(run.bundle, audio=audio))


def _no_flag_run(tmp_path: Path) -> Any:
    support = es()
    lines = list(support.EXPORT_LINES)
    half = max(1, len(lines) - 1)
    texts = [
        " ".join(text for _, text in lines[:half]),
        " ".join(text for _, text in lines[half:]) or "x",
    ]
    engine = support.FakeEngine(chunk_scripts=support.scripts(texts, phrases=None))
    return _fake_run(tmp_path, engine=engine)


# ------------------------------------------------------------------------ the tests


def test_report_flags_table_is_followed_by_the_chunk_edge_note(world: World) -> None:
    run, raw = world.report()
    doc = parse(raw)
    section = doc.by_id("flags")
    table = doc.by_id("flags-table")
    assert "chunk edge" in [th.text() for th in table.all("th")], "the column keeps its label"
    # the table and the sentence are the children of the section, in this order
    assert [(c.tag, c.attrs.get("id", "")) for c in section.elements()] == [
        ("h2", ""),
        ("table", "flags-table"),
        ("p", NOTE_ID),
    ]
    note = _note(doc)
    assert note.tag == "p"
    assert note.attrs["class"] == NOTE_ID
    assert set(note.attrs) == {"id", "class"}, "no data attribute, no handler, no style"
    assert note.elements() == [], "plain text: no markup inside the sentence"
    assert _one_line(note.text()) == NOTE_TEXT
    # exactly one such sentence anywhere in the page
    assert len([n for n in doc.all() if n.attrs.get("id") == NOTE_ID]) == 1
    assert doc.raw.count(NOTE_TEXT) == 1
    # the table itself still has one row per flag and the column cells are unchanged
    rows = table.all("tr")
    assert len(rows) == 1 + len(doc.with_class("flag-row", "tr"))
    assert run.code == 0


def test_report_chunk_edge_note_is_absent_when_there_are_no_flag_rows(tmp_path: Path) -> None:
    # Decision (contract section 3.2): with no flag row there is no column to explain, so the
    # sentence is not written; the section keeps its one line "no span reached the threshold".
    run = _no_flag_run(tmp_path)
    doc = parse(_fake_report(run))
    assert not doc.with_class("flag-row", "tr")
    assert not doc.has_id("flags-table")
    assert not doc.has_id(NOTE_ID)
    assert "chunk edge" not in doc.raw
    assert doc.raw.count("count as chunk edges") == 0
    section = doc.by_id("flags")
    assert [c.tag for c in section.elements()] == ["h2", "p"]
    assert section.all("p")[0].text() == "no span reached the threshold"


def test_report_chunk_edge_note_follows_the_table_in_every_scored_report(tmp_path: Path) -> None:
    seen = 0
    for name, options in (
        ("scored", {}),
        ("more flags", {"threshold": 0.2}),
        ("over budget", {"budget": 0.05}),
    ):
        folder = tmp_path / name.replace(" ", "-")
        folder.mkdir()
        run = _fake_run(folder, **options)
        for embedded in (False, True):
            doc = parse(_fake_report(run, embedded=embedded))
            assert doc.with_class("flag-row", "tr"), name
            section = doc.by_id("flags")
            assert [c.tag for c in section.elements()] == ["h2", "table", "p"], name
            assert _one_line(_note(doc).text()) == NOTE_TEXT, name
            seen += 1
    assert seen == 6


def test_report_chunk_edge_note_holds_no_call_text_and_no_number(tmp_path: Path) -> None:
    support = es()
    texts: set[str] = set()
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        run = _fake_run(
            folder, threshold=0.2 if name == "b" else support.TEST_ONLY_FLAG_MIN_CONFIDENCE
        )
        doc = parse(_fake_report(run))
        note = _note(doc)
        texts.add(_one_line(note.text()))
        assert "ut" not in note.classes
        assert not [n for n in note.walk() if "ut" in n.classes]
        assert not re.search(r"[0-9]", note.text()), "no count, offset or time"
        for turn in run.bundle.turns:
            assert turn.text not in note.text()
        document = flags_document(run.bundle)
        assert document["spans"], "the run has spans, so the sentence could have named one"
    # the same bytes whatever the call: it is a fixed sentence, not a function of the data
    assert texts == {NOTE_TEXT}


def test_report_chunk_edge_note_of_a_replay_run_is_the_same_fixed_sentence(world: World) -> None:
    run, raw = world.report()
    texts = _note(parse(raw)).text()
    assert _one_line(texts) == NOTE_TEXT
    bundle = load_stored_run(run.out_dir, run.value("bundle"))
    for turn in bundle.turns:
        assert turn.text not in texts
        # no word of any call line leaks into it (the words of the sentence are not call words)
        for word in turn.text.split():
            if len(word) > 3:
                assert word not in NOTE_TEXT.split(), word


def test_report_chunk_edge_note_is_escaped_text_and_passes_the_deny_list(tmp_path: Path) -> None:
    run = _fake_run(tmp_path)
    raw = _fake_report(run)
    doc = parse(raw)
    note_markup = re.search(rf'<p[^>]*id="{NOTE_ID}"[^>]*>(.*?)</p>', raw, flags=re.DOTALL)
    assert note_markup is not None
    inner = note_markup.group(1)
    # the sentence is written as escaped text, the way every text of the report is: unescaping
    # gives the sentence back, and nothing in it is markup
    assert html.unescape(inner) == NOTE_TEXT
    assert "<" not in inner
    assert not re.search(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)", inner)
    # the page-wide rules: no word of the deny list, no recommendation, no claim of accuracy
    assert not DENY_LIST.findall(NOTE_TEXT)
    lowered = _note(doc).text().lower()
    for phrase in ("accuracy", "error rate", "recommend", "suggest", "correct", "wer", "cer"):
        assert phrase not in re.findall(r"[a-z]+", lowered), phrase
    assert not DENY_LIST.findall(doc.tracer_text())
    # the new element did not add a tag or an attribute the structure rules refuse
    assert structure_violations(doc) == []
    assert external_request_violations(doc, embedded=False) == []


def test_report_chunk_edge_note_adds_no_script_style_or_policy_change(tmp_path: Path) -> None:
    report = report_mod()
    run = _fake_run(tmp_path)
    for embedded in (False, True):
        raw = _fake_report(run, embedded=embedded)
        doc = parse(raw)
        assert NOTE_TEXT in doc.raw
        # still one script and one style, the exact constants the policy hashes
        (script,) = element_text(raw, "script")
        (style,) = element_text(raw, "style")
        assert script == report.SEEK_SCRIPT
        assert style == report.STYLE
        assert len(doc.all("script")) == 1
        assert len(doc.all("style")) == 1
        # the sentence sits in the body, outside the hashed script and style text
        assert NOTE_TEXT not in script
        assert NOTE_TEXT not in style
        metas = [
            m
            for m in doc.all("meta")
            if m.attrs.get("http-equiv", "").lower() == "content-security-policy"
        ]
        assert len(metas) == 1
        media = "data:" if embedded else "'self'"
        assert metas[0].attrs["content"] == (
            f"default-src 'none'; style-src {sha256_source(style)}; "
            f"script-src {sha256_source(script)}; media-src {media}; "
            "base-uri 'none'; form-action 'none'"
        )
        assert csp_directives(metas[0].attrs["content"])["default-src"] == ["'none'"]
        # no style attribute and no handler on the new element
        note = _note(doc)
        assert "style" not in note.attrs
        assert not [k for k in note.attrs if k.startswith("on")]


def test_report_chunk_edge_note_names_the_recording_edge_semantics(
    tmp_path: Path, world: World
) -> None:
    """C7b. The scorer keeps ``near_chunk_boundary`` true for a span that holds the first or the
    last token of the recording (pinned by ``test_near_chunk_boundary_marked``, unchanged); the
    sentence says so, and a real report row shows the case."""
    run = _fake_run(tmp_path)
    document = flags_document(run.bundle)
    doc = parse(_fake_report(run))
    sentence = _one_line(_note(doc).text())
    assert "first and last tokens of the whole recording also count as chunk edges" in sentence
    assert "may come from where the audio was cut" in sentence
    stt_total = document["tokens"]["stt"]
    rows = {r.attrs["data-span"]: r for r in doc.with_class("flag-row", "tr")}
    at_recording_end = [
        (index, span)
        for index, span in enumerate(document["spans"])
        if span["flagged"] and span["stt_token_end"] == stt_total > span["stt_token_start"]
    ]
    assert at_recording_end, "a flagged span holds the last token of the recording"
    for index, span in at_recording_end:
        assert span["near_chunk_boundary"] is True
        cells = {c.attrs["class"]: c.text() for c in rows[str(index)].elements()}
        assert cells["boundary"] == "yes"
    # the replay report: every row's cell agrees with the stored document
    replay_run, raw = world.report()
    replay_doc = parse(raw)
    bundle = load_stored_run(replay_run.out_dir, replay_run.value("bundle"))
    spans = flags_document(bundle)["spans"]
    for row in replay_doc.with_class("flag-row", "tr"):
        cell = next(c for c in row.elements() if c.attrs["class"] == "boundary")
        assert cell.text() == (
            "yes" if spans[int(row.attrs["data-span"])]["near_chunk_boundary"] else "no"
        )
