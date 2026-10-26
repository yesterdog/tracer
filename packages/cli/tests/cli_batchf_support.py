# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch F tests of ``tracer-cli`` (P3-26, P3-34).

Contract: docs/phase3/api-surface-batch-f.md. The synthetic Marathi pair and the independent
alignment live in ``packages/evalcore/tests/evalcore_batchf_support.py`` and are loaded here by
file path (the batch D precedent), so the two directories share one reader of the data. The
command is run in-process through ``tracer_cli.cli.run`` with an injected ``Environment`` (batch E
contract): no process, no real clock, no socket. Nothing of ``tracer_cli`` is imported at module
level, so before the code exists each test fails on its own and the directory still collects.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
EVALCORE_SUPPORT = ROOT / "packages" / "evalcore" / "tests" / "evalcore_batchf_support.py"
INSTANT = datetime(2026, 10, 26, 9, 0, 0, tzinfo=UTC)
REPORT_NAME = "report-20261026T090000Z.html"
FSI = "\u2068"
PDI = "\u2069"
#: A canary standing for a key; never a real key.
KEY_CANARY = "-".join(["CANARYKEY", "".join(["f1", "7c", "e2"]), "".join(["0b", "93", "d4"])])


def data_support() -> ModuleType:
    """The data helpers of the evalcore batch F tests, loaded once by file path."""
    name = "evalcore_batchf_support"
    found = sys.modules.get(name)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(name, EVALCORE_SUPPORT)
    assert spec is not None, EVALCORE_SUPPORT
    assert spec.loader is not None, EVALCORE_SUPPORT
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cli_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.cli")


def report_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.report")


def store_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.store")


# ------------------------------------------------------------------------------ running it


@dataclass
class CliRun:
    code: int
    out: str
    err: str
    out_dir: Path
    wav: Path
    sleeps: list[float] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()

    def value(self, label: str) -> str:
        """The text after ``label: `` on the one stdout line that starts with it."""
        found = [line for line in self.lines if line.startswith(f"{label}: ")]
        assert len(found) == 1, (label, self.lines)
        return found[0][len(label) + 2 :]

    @property
    def report_path(self) -> Path:
        return self.out_dir / REPORT_NAME


def score_argv(
    wav: Path, export: Path, out_dir: Path, *, stt: str, extra: Sequence[str] = ()
) -> list[str]:
    """``tracer score`` with every required input explicit and the product's example rubric file.

    No chunking option is given: the documented defaults (25,000 ms window, 5,000 ms search)
    produce the three chunks of ``chunk_plan.json``.
    """
    support = data_support()
    return [
        "score",
        str(wav),
        "--transcript",
        str(export),
        "--rubric",
        str(support.RUBRIC_EXAMPLE),
        "--declared-language",
        "mr",
        "--caller-origin",
        "human",
        *(
            [
                "--consent",
                "obtained",
                "--consent-reference",
                "owner statement 2026-10-25",
                "--consent-recorded-at",
                "2026-10-01T00:00:00Z",
                "--max-chunks",
                "3",
            ]
            if stt == "sarvam"
            else ["--consent", "unknown"]
        ),
        "--retention",
        "keep",
        "--stt",
        stt,
        "--out",
        str(out_dir),
        *extra,
    ]


def run_cli(
    argv: Sequence[str],
    *,
    out_dir: Path,
    wav: Path,
    environ: dict[str, str] | None = None,
    transport: Any = None,
) -> CliRun:
    """Run the command with the fixed clock and no engine factory (the command builds its own)."""
    cli = cli_mod()
    stdout, stderr = io.StringIO(), io.StringIO()
    sleeps: list[float] = []
    environment = cli.Environment(
        stdout=stdout,
        stderr=stderr,
        environ=dict(environ or {}),
        clock=lambda: INSTANT,
        sleep=sleeps.append,
        transport=transport,
        engine_factory=None,
    )
    code = cli.run(list(argv), environment)
    return CliRun(code, stdout.getvalue(), stderr.getvalue(), out_dir, wav, sleeps)


def tree_bytes(folder: Path) -> dict[str, bytes]:
    """Every file under ``folder`` by relative name."""
    return {
        str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
    }


def load_stored_run(out_dir: Path, bundle_id_text: str) -> Any:
    """The bundle the command stored, read back through ``tracer_cli.store``."""
    from tracer_evalschema.ids import RunBundleId

    store = store_mod()
    engine = store.open_sqlite_store(out_dir)
    try:
        return store.load_run(engine, RunBundleId.parse(bundle_id_text))
    finally:
        engine.dispose()


def results_by_key(bundle: Any) -> dict[str, Any]:
    keys = {d.id: d.key for d in bundle.snapshot.metric_definitions}
    return {keys[r.metric.id]: r for r in bundle.results}


def flags_document(bundle: Any) -> dict[str, Any]:
    document = results_by_key(bundle)["stt_export_agreement_mer"].metadata["tracer.flags"]
    assert isinstance(document, dict)
    return document


# ------------------------------------------------------------------------------ a tiny HTML tree

_VOID = frozenset({"meta", "br", "hr", "wbr", "col", "area", "base", "link", "img", "input"})


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self) -> list[Node]:
        found: list[Node] = []
        for child in self.children:
            if isinstance(child, Node):
                found.append(child)
                found.extend(child.walk())
        return found

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        return [
            n
            for n in self.walk()
            if (tag is None or n.tag == tag) and (cls is None or cls in n.classes)
        ]

    def by_id(self, ident: str) -> Node:
        found = [n for n in self.walk() if n.attrs.get("id") == ident]
        assert len(found) == 1, (ident, len(found))
        return found[0]

    def has_id(self, ident: str) -> bool:
        return any(n.attrs.get("id") == ident for n in self.walk())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        self.current.children.append(data)


def parse_html(text: str) -> Node:
    builder = _Builder()
    builder.feed(text)
    builder.close()
    return builder.root


def unwrap(text: str) -> str:
    """The text of a ``ut`` element without its FSI ... PDI wrapper (contract of batch E 5.4)."""
    if text.startswith(FSI) and text.endswith(PDI):
        return text[1:-1]
    return text


# ------------------------------------------------------------------------------ the guard repo

GUARD_SCRIPT = ROOT / "scripts" / "guard_private_fixtures.py"
GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [
            GIT,
            "-c",
            "user.name=guard-test",
            "-c",
            "user.email=guard-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def guard_repo(tmp_path: Path) -> Path:
    """A throwaway repository holding a copy of the real guard script (the project is untouched)."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GUARD_SCRIPT, repo / "scripts" / "guard_private_fixtures.py")
    git(repo, "init", "-q")
    return repo


def run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(repo / "scripts" / "guard_private_fixtures.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
