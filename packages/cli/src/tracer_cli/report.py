# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The single self-contained HTML report of a scored (or not scored) recorded call.

The document is built by hand from the standard library. It holds one hashed ``<style>`` and one
hashed ``<script>`` (the audio seek), a Content-Security-Policy that allows nothing else, no
external reference and no element that loads anything but the one ``<audio>``. Every piece of
untrusted text is written by ``safe_html``: ``display_safe`` (ADR 0017), a visible marker for the
remaining control characters, then HTML escaping, per piece, so that a piece cannot reorder the
text around it or end the element that holds it.

``render_report`` reads the bundle and the audio argument alone: no clock, no file, no
environment, no identifier. The same bundle gives the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import tracer_evalcore
from tracer_cli.summary import (
    BudgetFacts,
    Facts,
    budget_facts,
    cost_text,
    export_transcript_of,
    export_turns_of,
    facts_of,
    not_scored_reasons,
    stt_failure_detail,
    summary_lines,
    usage_counts,
)
from tracer_evalcore.registry import COUNT_KEY, MER_KEY
from tracer_evalcore.tokens import TextTurn, TurnToken, tokenise_turns
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.text import display_safe
from tracer_evalschema.transcript import Turn

__all__ = [
    "SEEK_SCRIPT",
    "STYLE",
    "EmbeddedAudio",
    "RelativeAudio",
    "content_security_policy",
    "csp_hash_source",
    "outcome_of",
    "relative_audio_src",
    "render_report",
    "safe_html",
]

SEEK_SCRIPT: Final = (
    "(function () {\n"
    '  var player = document.getElementById("player");\n'
    "  if (!player) { return; }\n"
    '  document.addEventListener("click", function (event) {\n'
    "    var node = event.target;\n"
    "    while (node && node.nodeType === 1) {\n"
    '      if (node.hasAttribute("data-seek-start-ms")) {\n'
    '        var start = Number(node.getAttribute("data-seek-start-ms"));\n'
    "        if (isFinite(start)) { player.currentTime = start / 1000; player.play(); }\n"
    "        return;\n"
    "      }\n"
    "      node = node.parentNode;\n"
    "    }\n"
    "  });\n"
    "})();\n"
)

STYLE: Final = (
    "body { font-family: sans-serif; margin: 1.5rem; max-width: 60rem; }\n"
    "table { border-collapse: collapse; }\n"
    "td, th { border: 1px solid #888; padding: 0.2rem 0.5rem; text-align: left; }\n"
    "mark.flag { background: #ffe066; }\n"
    "mark.gap { background: #ffb3b3; }\n"
    ".role { font-weight: bold; margin-right: 0.5rem; }\n"
    ".warning { border: 2px solid #b00; padding: 0.5rem; }\n"
    "svg rect { fill: #446; }\n"
)

_RELATIVE_SRC: Final = re.compile(r"[A-Za-z0-9._~%/-]+")
_BINS: Final = 10
_LINE_BREAKS: Final = chr(0x2028) + chr(0x2029)


@dataclass(frozen=True)
class RelativeAudio:
    """A relative, URL-encoded reference to the audio file, from the report's directory."""

    src: str

    def __post_init__(self) -> None:
        value = self.src
        if (
            not isinstance(value, str)
            or _RELATIVE_SRC.fullmatch(value) is None
            or value.startswith("/")
            or "//" in value
        ):
            raise ValueError("the audio reference must be a relative, URL-encoded path")


@dataclass(frozen=True)
class EmbeddedAudio:
    """The exact bytes of the WAV file, embedded as a data URI (opt-in: personal data)."""

    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("the audio data must be bytes")
        if not self.data:
            raise ValueError("the audio data is empty")


def relative_audio_src(
    audio_path: str | os.PathLike[str], report_dir: str | os.PathLike[str]
) -> str:
    relative = os.path.relpath(os.fspath(audio_path), os.fspath(report_dir))
    return urllib.parse.quote(relative.replace(os.sep, "/"), safe="/")


def csp_hash_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def content_security_policy(style_text: str, script_text: str, *, embedded: bool) -> str:
    media = "data:" if embedded else "'self'"
    return (
        f"default-src 'none'; style-src {csp_hash_source(style_text)}; "
        f"script-src {csp_hash_source(script_text)}; media-src {media}; "
        "base-uri 'none'; form-action 'none'"
    )


def safe_html(text: str) -> str:
    """``display_safe``, a ``[U+XXXX]`` marker for every other control character, then escaping."""
    if not isinstance(text, str):
        raise TypeError("safe_html takes text")
    marked = "".join(
        f"[U+{ord(c):04X}]" if unicodedata.category(c) == "Cc" or c in _LINE_BREAKS else c
        for c in display_safe(text)
    )
    return html.escape(marked, quote=True)


def outcome_of(bundle: RunBundle) -> str:
    if stt_failure_detail(bundle) is not None:
        return "stt_failed"
    if bundle.results and all(r.status == "scored" for r in bundle.results):
        return "scored"
    return "not_scored"


# ------------------------------------------------------------------------------ small builders


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _attrs(attributes: Mapping[str, object]) -> str:
    return "".join(f' {name}="{_e(value)}"' for name, value in attributes.items())


def _el(tag: str, inner: str = "", **attributes: object) -> str:
    named = {name.replace("_", "-"): value for name, value in attributes.items()}
    return f"<{tag}{_attrs(named)}>{inner}</{tag}>"


def _ut(tag: str, classes: str, text: str, **attributes: object) -> str:
    """An element that holds one piece of untrusted text and nothing else."""
    return _el(tag, safe_html(text), **{"class": f"{classes} ut".strip(), **attributes})


def _none(text: str) -> str:
    return _el("span", _e(text), **{"class": "none"})


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------- the sections


def _header(outcome: str, declared: str | None) -> str:
    scored = outcome == "scored"
    parts = [
        _el(
            "header",
            _el("h1", "Tracer: agreement between two transcripts of one recorded call")
            + _el(
                "p",
                "result: scored" if scored else "result: not scored",
                id="status",
                data_outcome=outcome,
            ),
        ),
        _el(
            "section",
            _el(
                "p",
                "This page compares the transcript export of an agent for one recorded call "
                "with a speech-to-text transcript of the same audio. Neither text is treated "
                "as the reference, and nothing here says which one is right. No call was "
                "simulated: this is a recorded call. The page points at the spans of the export "
                "where the two disagree, so that a person reads less.",
            ),
            id="what-this-is",
        ),
    ]
    if declared is not None:
        parts.append(
            _el(
                "p",
                f"language declared by user: {_e(declared)}",
                id="declared-language",
                data_language=declared,
            )
        )
    return "".join(parts)


def _audio_section(bundle: RunBundle, audio: RelativeAudio | EmbeddedAudio) -> str:
    if not bundle.artifacts:
        return _el("section", "", id="audio")
    artifact = bundle.artifacts[0]
    fmt = artifact.audio_format
    facts = (
        f"audio: {fmt.container}, {fmt.sample_rate_hz} Hz, {fmt.channels} channel(s), "
        if fmt is not None
        else "audio: format not recorded, "
    )
    parts = [
        _el(
            "p",
            _e(f"{facts}{artifact.size} bytes, sha256 {artifact.sha256}"),
            id="audio-facts",
            data_sha256=artifact.sha256,
            data_bytes=artifact.size,
        )
    ]
    if isinstance(audio, EmbeddedAudio):
        parts.append(
            _el(
                "p",
                "This file contains the call audio and personal data. Treat it like the "
                "recording: do not share it.",
                id="embed-warning",
                **{"class": "warning"},
            )
        )
        source = "data:audio/wav;base64," + base64.b64encode(audio.data).decode("ascii")
    else:
        source = audio.src
    parts.append(_el("audio", "", id="player", controls="", preload="none", src=source))
    return _el("section", "".join(parts), id="audio")


def _privacy(bundle: RunBundle) -> str:
    privacy = export_transcript_of(bundle).privacy
    if privacy is None or not privacy.human_subject:
        return ""
    consent = privacy.consent.status if privacy.consent is not None else "not recorded"
    retention = privacy.retention
    if retention is None:
        tail = "delete on expiry: not recorded. retain until: not set."
    else:
        until = _utc(retention.retain_until) if retention.retain_until else "not set"
        expiry = "yes" if retention.delete_on_expiry else "no"
        tail = f"delete on expiry: {expiry}. retain until: {until}."
    return _el("p", _e(f"personal data: yes. consent: {consent}. {tail}"), id="privacy")


def _provenance(facts: Facts) -> str:
    mer = facts.results.get(MER_KEY)
    version = (
        mer.scorer.version
        if mer is not None and mer.scorer is not None
        else tracer_evalcore.__version__
    )
    rows = [("scorer version", _el("dd", _e(version), id="scorer-version"))]
    document = facts.document
    if document is not None:
        stt = document["stt"]
        rows += [
            ("signal", _el("dd", _e(document["signal"]), id="signal")),
            ("stt provider", _ut("dd", "", stt["provider"], id="stt-provider")),
            ("stt model", _ut("dd", "", stt["model"], id="stt-model")),
            ("model source", _el("dd", _e(stt["model_source"]), id="stt-model-source")),
            ("normaliser", _el("dd", _e(document["normaliser"]), id="normaliser")),
            ("threshold", _el("dd", _e(document["flag_min_confidence"]), id="threshold")),
        ]
    body = "".join(_el("dt", label) + value for label, value in rows)
    return _el("section", _el("h2", "How this was computed") + _el("dl", body, id="provenance"))


def _numbers(bundle: RunBundle, facts: Facts, document: dict[str, Any]) -> str:
    lines = dict(summary_lines(bundle, "scored"))
    alignment = document["alignment"]
    tokens = document["tokens"]
    parts = [
        _el("h2", "Numbers"),
        _el(
            "p",
            _e(f"agreement distance (MER): {lines['agreement distance (MER)']}"),
            id="agreement",
            data_value=repr(facts.results[MER_KEY].value),
            data_hits=alignment["hits"],
            data_substitutions=alignment["substitutions"],
            data_deletions=alignment["deletions"],
            data_insertions=alignment["insertions"],
        ),
        _el("p", _e(f"tokens: {lines['tokens']}"), id="tokens"),
        _el(
            "p",
            _e(f"flags: {lines['flags']}"),
            id="flag-count",
            data_value=facts.results[COUNT_KEY].value,
        ),
        _el(
            "p",
            _e(f"flagged share of export tokens: {lines['flagged share of export tokens']}"),
            id="flagged-share",
            data_numerator=tokens["flagged_export"],
            data_denominator=tokens["export"],
        ),
        _el(
            "p",
            _e(f"export turns with a flag: {lines['export turns with a flag']}"),
            id="flag-turns",
        ),
    ]
    budget = budget_facts(bundle, facts)
    if budget is not None:
        parts.append(_budget(budget, lines["review budget"]))
    return _el("section", "".join(parts), id="summary")


def _budget(budget: BudgetFacts, line: str) -> str:
    attributes: dict[str, object] = {
        "id": "review-budget",
        "data-max": budget.maximum,
        "data-no-limit": "true" if budget.no_limit else "false",
    }
    if budget.met is not None:
        attributes["data-met"] = "true" if budget.met else "false"
    return _el("p", _e(f"review budget: {line}"), **attributes)


def _not_scored(bundle: RunBundle) -> str:
    items = [_el("li", _e(f"not scored: {reason}")) for reason in not_scored_reasons(bundle)]
    detail = stt_failure_detail(bundle)
    if detail is not None:
        items.append(_el("li", "detail: " + _ut("span", "", detail)))
    return _el("section", _el("ul", "".join(items)), id="not-scored")


def _histogram(document: dict[str, Any]) -> str:
    spans_in = [0] * _BINS
    flagged_in = [0] * _BINS
    for span in document["spans"]:
        index = min(_BINS - 1, span["edit_distance"] * _BINS // span["key_length"])
        spans_in[index] += 1
        flagged_in[index] += 1 if span["flagged"] else 0
    largest = max(spans_in) or 1
    rows = []
    for index in range(_BINS):
        high = f"0.{index + 1}" if index < _BINS - 1 else "1.0"
        width = max(1, spans_in[index] * 100 // largest)
        bar = (
            _el(
                "svg",
                _el("rect", "", x=0, y=0, width=width, height=10),
                width=width,
                height=10,
                role="img",
                aria_label=f"{spans_in[index]} spans",
            )
            if spans_in[index]
            else ""
        )
        cells = (
            _el("td", f"0.{index} to {high}")
            + _el("td", f"{spans_in[index]} spans")
            + _el("td", f"{flagged_in[index]} flagged")
            + _el("td", bar)
        )
        rows.append(
            _el(
                "tr",
                cells,
                **{
                    "class": "bin",
                    "data-bin": index,
                    "data-low": f"0.{index}",
                    "data-high": high,
                    "data-spans": spans_in[index],
                    "data-flagged": flagged_in[index],
                },
            )
        )
    head = _el(
        "thead",
        _el("tr", "".join(_el("th", label) for label in ("confidence", "spans", "flagged", ""))),
    )
    table = _el("table", head + _el("tbody", "".join(rows)), id="histogram")
    total = _el("p", f"spans: {len(document['spans'])}", id="histogram-total")
    intro = _el(
        "p",
        "Histogram of the confidence of every span found, flagged or not. The confidence is an "
        "uncalibrated disagreement strength and not a probability.",
    )
    return _el("section", _el("h2", "Confidence of the spans") + intro + table + total)


def _stt_turns_and_tokens(
    bundle: RunBundle, document: dict[str, Any]
) -> tuple[list[Turn], tuple[TurnToken, ...]]:
    stt = next((t for t in bundle.transcripts if t.source == "our_stt"), None)
    if stt is None:
        return [], ()
    turns = sorted((t for t in bundle.turns if t.transcript_id == stt.id), key=lambda t: t.seq)
    sequence = tokenise_turns([TextTurn(t.seq, t.text) for t in turns], document["normaliser"])
    return turns, sequence.tokens


def _heard(span: dict[str, Any], turns: Sequence[Turn], tokens: Sequence[TurnToken]) -> str:
    first, last = span["stt_token_start"], span["stt_token_end"]
    if first == last:
        return _none("nothing heard")
    pieces: dict[int, tuple[int, int]] = {}
    for token in tokens[first:last]:
        start, end = pieces.get(token.turn_index, (token.start, token.end))
        pieces[token.turn_index] = (min(start, token.start), max(end, token.end))
    return "".join(
        _ut(
            "span",
            "heard-piece",
            turns[turn_index].text[start:end],
            data_stt_turn=turns[turn_index].seq,
            data_stt_start=start,
            data_stt_end=end,
        )
        for turn_index, (start, end) in pieces.items()
    )


def _seek_cell(span: dict[str, Any]) -> str:
    if "audio_start_ms" not in span:
        return "no audio range"
    start, end = span["audio_start_ms"], span["audio_end_ms"]
    return _el(
        "button",
        f"play {start} to {end} ms",
        type="button",
        data_seek_start_ms=start,
        data_seek_end_ms=end,
        **{"class": "seek"},
    )


def _flag_row(
    index: int,
    span: dict[str, Any],
    roles: Mapping[int, str],
    export_texts: Mapping[int, str],
    stt: tuple[Sequence[Turn], Sequence[TurnToken]],
) -> str:
    if span["start"] == span["end"]:
        exported = _none("not in export")
    else:
        exported = _ut(
            "span", "export-piece", export_texts[span["turn_seq"]][span["start"] : span["end"]]
        )
    cells = {
        "turn": f"turn {span['turn_seq']}",
        "role": _e(roles.get(span["turn_seq"], "unknown")),
        "offsets": f"{span['start']}-{span['end']}",
        "confidence": _e(span["confidence"]),
        "edit": f"edit distance {span['edit_distance']} of {span['key_length']}",
        "boundary": "yes" if span["near_chunk_boundary"] else "no",
        "exportside": exported,
        "heard": _heard(span, *stt),
        "audio": _seek_cell(span),
    }
    return _el(
        "tr",
        "".join(_el("td", inner, **{"class": name}) for name, inner in cells.items()),
        **{
            "class": "flag-row",
            "data-span": index,
            "data-turn-seq": span["turn_seq"],
            "data-stt-token-start": span["stt_token_start"],
            "data-stt-token-end": span["stt_token_end"],
        },
    )


def _flags_section(bundle: RunBundle, document: dict[str, Any], turns: Sequence[Turn]) -> str:
    roles = {t.seq: t.role for t in turns}
    export_texts = {t.seq: t.text for t in turns}
    stt = _stt_turns_and_tokens(bundle, document)
    rows = [
        _flag_row(index, span, roles, export_texts, stt)
        for index, span in enumerate(document["spans"])
        if span["flagged"]
    ]
    if rows:
        labels = (
            "turn", "role", "offsets", "confidence", "edit", "chunk edge",
            "agent export", "STT heard", "audio",
        )  # fmt: skip
        head = _el("thead", _el("tr", "".join(_el("th", label) for label in labels)))
        content = _el("table", head + _el("tbody", "".join(rows)), id="flags-table")
    else:
        content = _el("p", "no span reached the threshold")
    return _el("section", _el("h2", "Flagged spans") + content, id="flags")


def _turn_text(turn: Turn, spans: Sequence[tuple[int, dict[str, Any]]]) -> str:
    text = turn.text
    parts: list[str] = []
    position = 0
    for index, span in spans:
        start, end = span["start"], span["end"]
        if start > position:
            parts.append(_ut("span", "seg", text[position:start]))
        data: dict[str, object] = {
            "data-span": index,
            "data-turn": turn.seq,
            "data-start": start,
            "data-end": end,
            "data-confidence": span["confidence"],
        }
        if start == end:
            parts.append(_el("mark", "[+]", **{"class": "flag gap", **data}))
        else:
            parts.append(_ut("mark", "flag", text[start:end], **data))
        position = max(position, end)
    if position < len(text):
        parts.append(_ut("span", "seg", text[position:]))
    return "".join(parts)


def _transcript(turns: Sequence[Turn], document: dict[str, Any] | None) -> str:
    by_turn: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    if document is not None:
        for index, span in enumerate(document["spans"]):
            if span["flagged"]:
                by_turn.setdefault(span["turn_seq"], []).append((index, span))
    items = []
    for turn in turns:
        spans = sorted(by_turn.get(turn.seq, []), key=lambda p: (p[1]["start"], p[1]["end"]))
        inner = _el("span", _e(turn.role), **{"class": "role"}) + _el(
            "div", _turn_text(turn, spans), **{"class": "text"}
        )
        items.append(
            _el(
                "li",
                inner,
                id=f"turn-{turn.seq}",
                data_seq=turn.seq,
                data_role=turn.role,
                data_flags=len(spans),
                **{"class": "turn"},
            )
        )
    return _el("section", _el("h2", "Export") + _el("ol", "".join(items), id="transcript"))


def _usage_section(bundle: RunBundle) -> str:
    counts = usage_counts(bundle)
    requests, seconds = ("none", "none") if counts is None else (str(counts[0]), counts[1])
    cost = cost_text(bundle)
    table = _el(
        "table",
        _el(
            "tbody",
            _el("tr", _el("th", "requests") + _el("td", requests, id="usage-requests"))
            + _el("tr", _el("th", "audio seconds") + _el("td", seconds, id="usage-audio-seconds"))
            + _el("tr", _el("th", "cost") + _el("td", _e(cost), id="usage-cost")),
        ),
    )
    note = ""
    if cost.startswith("not available"):
        note = _el(
            "p",
            "A user price file must also price requests as well as audio_input_seconds, or no "
            "cost can be computed.",
            id="usage-note",
        )
    return _el("section", _el("h2", "Usage and cost") + table + note, id="usage")


def _warnings_section(bundle: RunBundle) -> str:
    warnings = bundle.run.ingest_warnings
    items = "".join(
        _el(
            "li",
            _el("code", _e(w.code)) + " " + _ut("span", "", f"{w.path or ''} {w.message or ''}"),
        )
        for w in warnings
    )
    return _el(
        "section",
        _el("p", f"ingest warnings: {len(warnings)}", id="warning-count") + _el("ul", items),
        id="warnings",
        data_count=len(warnings),
    )


def _snapshot_note() -> str:
    return _el(
        "section",
        _el(
            "p",
            "The stored configuration of this run holds a check named "
            "recorded_call_no_outcome_check. It is never run and produces no result. It exists "
            "only because schema 0.0.1 requires such a check, and it is not a judgement of the "
            "agent.",
        ),
        id="snapshot-note",
    )


def render_report(bundle: RunBundle, *, audio: RelativeAudio | EmbeddedAudio) -> str:
    if not isinstance(bundle, RunBundle):
        raise TypeError("render_report takes a RunBundle")
    if not isinstance(audio, RelativeAudio | EmbeddedAudio):
        raise TypeError("audio must be a RelativeAudio or an EmbeddedAudio")
    outcome = outcome_of(bundle)
    facts = facts_of(bundle)
    document = facts.document if outcome == "scored" else None
    turns = export_turns_of(bundle)
    declared = export_transcript_of(bundle).declared_language

    body = [
        _header(outcome, declared.language if declared is not None else None),
        _audio_section(bundle, audio),
        _privacy(bundle),
        _provenance(facts),
    ]
    if document is None:
        body.append(_not_scored(bundle))
    else:
        body += [
            _numbers(bundle, facts, document),
            _histogram(document),
            _flags_section(bundle, document, turns),
        ]
    body += [
        _transcript(turns, document),
        _usage_section(bundle),
        _warnings_section(bundle),
        _snapshot_note(),
        _el("footer", _el("p", f"run created {_utc(bundle.run.created_at)}")),
    ]
    policy = content_security_policy(STYLE, SEEK_SCRIPT, embedded=isinstance(audio, EmbeddedAudio))
    head = (
        '<meta charset="utf-8">'
        + f'<meta http-equiv="Content-Security-Policy" content="{_e(policy)}">'
        + '<meta name="tracer-report" content="1">'
        + "<title>Tracer report</title>"
        + f"<style>{STYLE}</style>"
    )
    main = _el("main", "".join(body), id="report")
    return (
        '<!DOCTYPE html>\n<html lang="en"><head>'
        + head
        + "</head><body>"
        + main
        + f"<script>{SEEK_SCRIPT}</script></body></html>\n"
    )
