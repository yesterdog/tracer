# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch E tests (score command, report, adversarial suite).

Contract: docs/phase3/api-surface-batch-e.md. Nothing of the batch E modules (``tracer_cli.cli``,
``.report``, ``.summary``, ``.replay``) is imported at module level: each test loads the module
lazily, so that before the code exists every test fails on its own with ``ModuleNotFoundError`` and
the directory still collects.

The scored call is the hand-computed base scenario of the batch D tests (the invented Marathi-like
words, the fake engine, the two-chunk plan); its helpers are loaded by file path through
``cli_batchd_support``. All text is invented. The key is the canary of the batch D support file,
never a real key. The command is run in-process through ``tracer_cli.cli.run`` with an injected
``Environment`` (streams, environment, clock, sleep, transport, engine factory): no process is
started, no clock is read for a result and no socket is opened.
"""

from __future__ import annotations

import base64
import hashlib
import html
import importlib
import io
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from cli_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    CLI_SRC,
    ROOT,
    evalcore_support,
)

DENY_LIST = re.compile(r"(?i)\b(?:accuracy|wer|cer|error rate|correct|recall|precision)\b")
REPORT_NAME = re.compile(r"report-\d{8}T\d{6}Z\.html")
FSI = "\u2068"
PDI = "\u2069"
#: The tags the report may never contain (contract section 5.2).
FORBIDDEN_TAGS = frozenset(
    {
        "a", "img", "iframe", "frame", "frameset", "object", "embed", "applet", "link", "base",
        "form", "input", "textarea", "select", "option", "video", "source", "track", "picture",
        "canvas", "template", "noscript", "portal", "slot", "font", "center", "marquee",
    }
)  # fmt: skip
#: The attribute names the report may use besides ``data-*`` (contract section 5.2).
ALLOWED_ATTRIBUTES = frozenset(
    {
        "class", "id", "lang", "charset", "http-equiv", "content", "name", "controls", "preload",
        "src", "type", "width", "height", "x", "y", "viewbox", "role", "aria-label", "colspan",
        "rowspan", "scope", "dir", "title", "open", "headers", "tabindex", "aria-hidden",
        "aria-labelledby", "aria-describedby",
    }
)  # fmt: skip
VOID_TAGS = frozenset({"meta", "br", "hr", "wbr", "col", "area", "base", "link", "img", "input"})


def es() -> ModuleType:
    """The helpers of the evalcore batch D tests (fake engine, base scenario, rubric files)."""
    module: ModuleType = evalcore_support()
    return module


def cli_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.cli")


def report_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.report")


def summary_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.summary")


def replay_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.replay")


def store_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.store")


def noop_sleep(seconds: float) -> None:
    return None


def no_price_cost_builder(usages: Sequence[Any]) -> Any:
    """The cost builder the command composes when the user gave no price file (batch D
    ``CostBuilder``): every record is ``not_available``."""
    from tracer_evalcore.pipeline import CostParts
    from tracer_evalproviders.cost import compute_stt_cost

    cost = compute_stt_cost(usages, catalog=None, created_at=es().CREATED_AT)
    return CostParts((), cost.records, (cost.summary,))


def fresh(tmp_path: Path, name: str) -> Path:
    """A new empty folder ``tmp_path/name``."""
    folder = tmp_path / name
    folder.mkdir()
    return folder


# ------------------------------------------------------------------------------ input files


@dataclass(frozen=True)
class Inputs:
    root: Path
    wav: Path
    export: Path
    rubric: Path
    out: Path


def write_inputs(
    tmp_path: Path,
    *,
    export: str | bytes | None = None,
    rubric: str | bytes | None = None,
    frames: int = 24000,
    rate: int = 8000,
    channels: int = 1,
    wav_name: str = "call.wav",
) -> Inputs:
    """The three input files under ``tmp_path/in``; ``tmp_path/out`` does not exist yet."""
    support = es()
    folder = tmp_path / "in"
    folder.mkdir()
    wav = support.write_wav(folder / wav_name, frames=frames, rate=rate, channels=channels)
    export_file = folder / "export.txt"
    export_file.write_bytes(
        (support.BASE_EXPORT if export is None else export).encode("utf-8")
        if not isinstance(export, bytes)
        else export
    )
    rubric_file = folder / "rubric.json"
    rubric_file.write_bytes(
        (support.rubric_text() if rubric is None else rubric).encode("utf-8")
        if not isinstance(rubric, bytes)
        else rubric
    )
    return Inputs(tmp_path, wav, export_file, rubric_file, tmp_path / "out")


def score_args(
    inputs: Inputs,
    *,
    options: Mapping[str, str | None] | None = None,
    extra: Sequence[str] = (),
    chunking: bool = True,
) -> list[str]:
    """``["score", <audio>, <options>...]``. ``options`` overrides an option's value; ``None``
    removes it. The chunking values (2,000 ms windows, 1,000 ms search) make the 3 s recording
    two chunks, as in the batch D base scenario."""
    values: dict[str, str | None] = {
        "--transcript": str(inputs.export),
        "--rubric": str(inputs.rubric),
        "--declared-language": "mr",
        "--caller-origin": "human",
        "--consent": "obtained",
        "--consent-reference": "owner statement 2026-10-25",
        "--consent-recorded-at": "2026-01-01T00:00:00Z",
        "--retention": "keep",
        "--stt": "sarvam",
        "--max-chunks": "50",
        "--out": str(inputs.out),
    }
    if chunking:
        values["--max-chunk-ms"] = "2000"
        values["--search-ms"] = "1000"
    values.update(options or {})
    flat: list[str] = []
    for name, value in values.items():
        if value is not None:
            flat += [name, value]
    return ["score", str(inputs.wav), *flat, *extra]


# ------------------------------------------------------------------------------ running it


@dataclass
class CliRun:
    code: int
    out: str
    err: str
    engine: Any
    requests: list[Any]
    clock_calls: list[datetime]
    factory_calls: int
    sleeps: list[float]

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()

    def value(self, label: str) -> str:
        """The text after ``label: `` on the one stdout line that starts with it."""
        found = [line for line in self.lines if line.startswith(f"{label}: ")]
        assert len(found) == 1, (label, self.lines)
        return found[0][len(label) + 2 :]


class _Clock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant
        self.calls: list[datetime] = []

    def __call__(self) -> datetime:
        self.calls.append(self.instant)
        return self.instant


def run_cli(
    args: Sequence[str],
    *,
    engine: Any = "default",
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    production: bool = False,
    instant: datetime | None = None,
) -> CliRun:
    """Run ``tracer_cli.cli.run`` in-process.

    ``engine`` is what the injected factory returns (default: a fresh base-scenario ``FakeEngine``);
    ``production=True`` injects no factory, so the command builds its own engine (the Sarvam
    engine over ``transport``, or the replay engine for ``--stt fake:<dir>``).
    """
    support = es()
    cli = cli_mod()
    stdout, stderr = io.StringIO(), io.StringIO()
    clock = _Clock(support.CREATED_AT if instant is None else instant)
    chosen = support.FakeEngine() if engine == "default" else engine
    requests: list[Any] = []
    sleeps: list[float] = []

    def factory(request: Any) -> Any:
        requests.append(request)
        if isinstance(chosen, BaseException):
            raise chosen
        return chosen

    environment = cli.Environment(
        stdout=stdout,
        stderr=stderr,
        environ=dict(environ or {}),
        clock=clock,
        sleep=sleeps.append,
        transport=transport,
        engine_factory=None if production else factory,
    )
    code = cli.run(list(args), environment)
    return CliRun(
        code,
        stdout.getvalue(),
        stderr.getvalue(),
        chosen,
        requests,
        clock.calls,
        len(requests),
        sleeps,
    )


def report_files(out: Path) -> list[Path]:
    return sorted(p for p in out.glob("report-*.html") if REPORT_NAME.fullmatch(p.name))


def the_report(out: Path) -> str:
    files = report_files(out)
    assert len(files) == 1, sorted(p.name for p in out.iterdir()) if out.is_dir() else out
    return files[0].read_text("utf-8")


def tree_bytes(out: Path) -> dict[str, bytes]:
    """Every file under ``out`` by relative name."""
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def load_stored_run(out: Path, bundle_id_text: str) -> Any:
    """The bundle the command stored, read back through ``tracer_cli.store``."""
    from tracer_evalschema.ids import RunBundleId

    store = store_mod()
    engine = store.open_sqlite_store(out)
    try:
        return store.load_run(engine, RunBundleId.parse(bundle_id_text))
    finally:
        engine.dispose()


def load_stored_project(out: Path, project_id: Any) -> Any:
    """The project record the command stored."""
    import tracer_evaldb

    store = store_mod()
    engine = store.open_sqlite_store(out)
    try:
        return tracer_evaldb.load_project(engine, project_id)
    finally:
        engine.dispose()


# ------------------------------------------------------------------------------ the replay dir


def sarvam_body(
    text: str,
    *,
    request_id: str,
    language: str | None = "mr-IN",
    probability: float | None = 1.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"request_id": request_id, "transcript": text}
    body["language_code"] = language
    if probability is not None:
        body["language_probability"] = probability
    return body


def base_bodies(languages: Sequence[str | None] = ("mr-IN", "mr-IN")) -> list[dict[str, Any]]:
    support = es()
    return [
        sarvam_body(text, request_id=f"20261025_replay-{index:04d}", language=languages[index])
        for index, text in enumerate(support.STT_TEXTS)
    ]


def write_replay(folder: Path, bodies: Sequence[Any]) -> Path:
    """``chunk-0000.json`` ... one Sarvam 200 body per chunk (a dict, or raw bytes)."""
    folder.mkdir(parents=True, exist_ok=True)
    for index, body in enumerate(bodies):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=True).encode()
        (folder / f"chunk-{index:04d}.json").write_bytes(data)
    return folder


class SarvamScript:
    """An ``httpx.MockTransport`` that answers the chunk requests in order.

    A body is a dict (HTTP 200), or an int (that HTTP status with a Sarvam style error body).
    ``requests`` keeps every request, so a test can read the key header the command sent.
    """

    def __init__(
        self,
        bodies: Sequence[Any] | None = None,
        *,
        date: str | None = "Sun, 25 Oct 2026 09:30:00 GMT",
        dates: Sequence[str | None] | None = None,
    ) -> None:
        self.bodies = list(base_bodies() if bodies is None else bodies)
        self.date = date
        self.dates = None if dates is None else list(dates)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index = len(self.requests)
        self.requests.append(request)
        body = self.bodies[index]
        date = self.date if self.dates is None else self.dates[index]
        headers = {} if date is None else {"date": date}
        if isinstance(body, int):
            error = {
                "error": {"message": "bad", "code": "invalid_request_error", "request_id": "r"}
            }
            return httpx.Response(body, json=error, headers=headers)
        return httpx.Response(200, json=body, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


# ------------------------------------------------------------------------------ price files


def price_file_text(
    *, with_requests: bool = True, effective_from: str = "2026-01-01T00:00:00.000000Z"
) -> str:
    """A user USD price file for sarvam / saaras:v4: 0.006 USD per 60 audio seconds (so 0.0001 per
    second) and, if asked, 0 per request. The numbers are invented test values."""

    def entry(unit: str, quantity: str, amount: str) -> dict[str, Any]:
        return {
            "entry_key": f"sarvam-{unit}",
            "provider": "sarvam",
            "model": "saaras:v4",
            "aliases": [],
            "unit": unit,
            "unit_quantity": quantity,
            "price": {"amount": amount, "currency": "USD"},
            "billing": {"increment": "none", "minimum": "none"},
            "effective_from": effective_from,
            "price_source": "user_supplied",
            "provenance": {
                "kind": "user_note",
                "retrieved_at": "2026-10-25T00:00:00.000000Z",
                "note": "invented test price",
            },
        }

    entries = [entry("audio_input_seconds", "60", "0.006")]
    if with_requests:
        entries.append(entry("requests", "1", "0"))
    return json.dumps({"published_at": "2026-10-01T00:00:00.000000Z", "entries": entries})


# ------------------------------------------------------------------------------ the report DOM


class Node:
    """One element of a parsed report."""

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: Node | None) -> None:
        self.tag = tag
        self.attr_list = attrs
        self.attrs: dict[str, str] = {k: (v if v is not None else "") for k, v in attrs}
        self.parent = parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self) -> Any:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def all(self, tag: str | None = None) -> list[Node]:
        """Every descendant element (not this one), optionally of one tag."""
        return [n for n in self.walk() if n is not self and (tag is None or n.tag == tag)]

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def tracer_text(self) -> str:
        """The text of this element without the text of any ``ut`` (untrusted text) element."""
        if "ut" in self.classes:
            return ""
        return "".join(c if isinstance(c, str) else c.tracer_text() for c in self.children)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.current = self.root
        self.script_texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        self.current.children.append(data)


class Doc:
    """A parsed report, with the helpers the tests use."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        builder = _Builder()
        builder.feed(raw)
        builder.close()
        self.root = builder.root

    def all(self, tag: str | None = None) -> list[Node]:
        return [n for n in self.root.walk() if n.tag != "#root" and (tag is None or n.tag == tag)]

    def by_id(self, element_id: str) -> Node:
        found = [n for n in self.all() if n.attrs.get("id") == element_id]
        assert len(found) == 1, (element_id, len(found))
        return found[0]

    def has_id(self, element_id: str) -> bool:
        return any(n.attrs.get("id") == element_id for n in self.all())

    def with_class(self, name: str, tag: str | None = None) -> list[Node]:
        return [n for n in self.all(tag) if name in n.classes]

    def tracer_text(self) -> str:
        return self.root.tracer_text()

    def ut_nodes(self) -> list[Node]:
        return [n for n in self.all() if "ut" in n.classes]


def parse(raw: str) -> Doc:
    return Doc(raw)


def unwrap(shown: str) -> str:
    """The text of an untrusted-text element without its FSI ... PDI wrapper."""
    assert shown.startswith(FSI), repr(shown[:20])
    assert shown.endswith(PDI), repr(shown[-20:])
    return shown[1:-1]


def markerize(text: str) -> str:
    """What the report shows for untrusted text, wrapper removed: display_safe markers for bidi
    controls and ``[U+XXXX]`` for every other control character (contract section 5.4)."""
    from tracer_evalschema.text import display_safe

    shown = unwrap(display_safe(text))
    return "".join(
        f"[U+{ord(c):04X}]" if unicodedata.category(c) == "Cc" or c in "\u2028\u2029" else c
        for c in shown
    )


def shown_html(text: str) -> str:
    """What ``tracer_cli.report.safe_html`` must return for ``text`` (an independent version)."""
    from tracer_evalschema.text import display_safe

    shown = display_safe(text)
    marked = "".join(
        f"[U+{ord(c):04X}]" if unicodedata.category(c) == "Cc" or c in "\u2028\u2029" else c
        for c in shown
    )
    return html.escape(marked, quote=True)


# ------------------------------------------------------------------------------ report checks


def csp_directives(content: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in content.split(";"):
        tokens = part.split()
        if tokens:
            assert tokens[0] not in directives, f"directive repeated: {tokens[0]}"
            directives[tokens[0]] = tokens[1:]
    return directives


def sha256_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def element_text(raw: str, tag: str) -> list[str]:
    """The exact text between ``<tag>`` and ``</tag>`` (the text the CSP hash is computed over)."""
    return re.findall(rf"<{tag}>(.*?)</{tag}>", raw, flags=re.DOTALL)


DATA_AUDIO = re.compile(r"data:audio/wav;base64,([A-Za-z0-9+/]*={0,2})")


def structure_violations(doc: Doc) -> list[str]:
    """Every breach of the structural rules of contract section 5.2 and 5.3."""
    found: list[str] = []
    tags = [n.tag for n in doc.all()]
    for forbidden in sorted(set(tags) & FORBIDDEN_TAGS):
        found.append(f"forbidden tag <{forbidden}>")
    for tag in ("script", "style", "audio", "main"):
        count = tags.count(tag)
        if tag == "audio" and count not in (0, 1):
            found.append(f"{count} audio elements")
        if tag in ("script", "style", "main") and count != 1:
            found.append(f"{count} <{tag}> elements")
    for node in doc.all():
        names = [name for name, _ in node.attr_list]
        if len(names) != len(set(names)):
            found.append(f"<{node.tag}> repeats an attribute")
        for name in names:
            if name.startswith("data-"):
                continue
            if name not in ALLOWED_ATTRIBUTES:
                found.append(f"<{node.tag}> attribute {name}")
        if node.tag == "script" and node.attr_list:
            found.append("the script element has attributes")
        if node.tag == "style" and node.attr_list:
            found.append("the style element has attributes")
        if "src" in node.attrs and node.tag != "audio":
            found.append(f"src on <{node.tag}>")
        if node.tag == "meta":
            kind = {"charset", "http-equiv", "name"} & set(node.attrs)
            if kind == {"http-equiv"} and node.attrs["http-equiv"].lower() != (
                "content-security-policy"
            ):
                found.append("meta http-equiv other than the CSP")
            if kind not in ({"charset"}, {"http-equiv"}, {"name"}):
                found.append("meta with an unexpected shape")
    return found


def external_request_violations(doc: Doc, *, embedded: bool) -> list[str]:
    """No ``src``/``href``/``url()``/``@import``/``//`` outside the one allowed audio source."""
    found: list[str] = []
    for node in doc.all():
        for name, value in node.attrs.items():
            if name in ("href", "srcset", "action", "formaction", "poster", "background", "ping"):
                found.append(f"{name} attribute")
            if name.startswith("on"):
                found.append(f"event handler {name}")
            if name == "style":
                found.append("style attribute")
            if name == "src":
                if embedded and DATA_AUDIO.fullmatch(value):
                    continue
                if re.fullmatch(r"[A-Za-z0-9._~%/-]+", value) is None or value.startswith(
                    ("/", "//")
                ):
                    found.append(f"src is not a relative reference: {value[:40]}")
                if "//" in value or ":" in value:
                    found.append("src holds // or a scheme")
                continue
            if name in ("content",) and node.tag == "meta":
                continue
            if "//" in value or re.search(r"(?i)\burl\(|@import|https?:|ftp:|javascript:", value):
                found.append(f"attribute {name} holds a url form")
    for tag in ("style", "script"):
        for text in element_text(doc.raw, tag):
            if "//" in text or re.search(r"(?i)\burl\(|@import|https?:", text):
                found.append(f"<{tag}> text holds a url form")
    outside = doc.tracer_text()
    if "//" in outside or re.search(r"(?i)https?:", outside):
        found.append("Tracer text holds a url form")
    raw_without_data = DATA_AUDIO.sub("data:audio/wav;base64,", doc.raw)
    # the raw markup outside the text of untrusted-text elements: no url form either
    stripped = _strip_ut(raw_without_data)
    if re.search(r"(?i)\burl\(|@import", stripped):
        found.append("css url() or @import in the markup")
    if embedded is False and re.search(r"data:[a-z]+/[a-z]", stripped):
        found.append("a data: reference in a report with relative audio")
    return found


def _strip_ut(raw: str) -> str:
    """The raw markup with the content of untrusted-text elements removed (elements are flat:
    a ``ut`` element holds only text)."""
    return re.sub(
        r"(<(span|mark|div|dd|li|td|p|code)\b[^>]*\bclass=\"[^\"]*\but\b[^\"]*\"[^>]*>)(.*?)(</\2>)",
        r"\1\4",
        raw,
        flags=re.DOTALL,
    )


def tracer_text_deny_hits(doc: Doc) -> list[str]:
    return DENY_LIST.findall(doc.tracer_text())


ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.~%-])/(?:Users|home|tmp|private|var|root|mnt|opt|etc|srv|usr|Volumes)/"
    r"|[A-Za-z]:\\|file:|\\\\[A-Za-z]"
)


def flag_document(bundle: Any) -> dict[str, Any]:
    results = [r for r in bundle.results if r.status == "scored"]
    assert results
    document = results[0].metadata["tracer.flags"]
    assert isinstance(document, dict)
    return document


def export_turns(bundle: Any) -> list[Any]:
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    return sorted((t for t in bundle.turns if t.transcript_id == export.id), key=lambda t: t.seq)


@dataclass
class Case:
    """A tiny holder so that parametrised tests can name a scenario."""

    name: str
    export: str
    texts: Sequence[str] | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def callable_default_names(function: Callable[..., Any]) -> list[str]:
    import inspect

    return [
        name
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    ]


__all__ = [
    "ABSOLUTE_PATH",
    "ALLOWED_ATTRIBUTES",
    "CLI_SRC",
    "DENY_LIST",
    "FORBIDDEN_TAGS",
    "FSI",
    "PDI",
    "REPORT_NAME",
    "ROOT",
    "UTC",
    "Case",
    "CliRun",
    "Doc",
    "Inputs",
    "Node",
    "SarvamScript",
    "base_bodies",
    "callable_default_names",
    "cli_mod",
    "csp_directives",
    "element_text",
    "es",
    "export_turns",
    "external_request_violations",
    "flag_document",
    "fresh",
    "load_stored_project",
    "load_stored_run",
    "markerize",
    "no_price_cost_builder",
    "noop_sleep",
    "parse",
    "price_file_text",
    "replay_mod",
    "report_files",
    "report_mod",
    "run_cli",
    "sarvam_body",
    "score_args",
    "sha256_source",
    "shown_html",
    "store_mod",
    "structure_violations",
    "summary_mod",
    "the_report",
    "tracer_text_deny_hits",
    "tree_bytes",
    "unwrap",
    "write_inputs",
    "write_replay",
]
