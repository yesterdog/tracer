# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-01: the repository-level scaffold rules of Phase 4 (contract
docs/phase4/api-surface-batch-0b.md sections 2, 4 and 7): the pure core stays free of the asyncio
network API, tracer-evalschema is byte-unchanged against the Phase 4 baseline, and
``tools/samplebot`` is an isolated tool project.

The per-package tests of ``tracer-evalsim`` are in ``packages/evalsim/tests``. These tests read
repository files and start fresh interpreters; they open no socket.
"""

from __future__ import annotations

import ast
import importlib.metadata as md
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
PACKAGES = ROOT / "packages"
EVALCORE_SRC = PACKAGES / "evalcore" / "src" / "tracer_evalcore"
EVALSCHEMA = PACKAGES / "evalschema"
EVALSCHEMA_SRC = EVALSCHEMA / "src" / "tracer_evalschema"
BASELINE = ROOT / "docs" / "phase4" / "evidence" / "evalschema-baseline.json"
BASELINE_PHASE3 = ROOT / "docs" / "phase3" / "evidence" / "evalschema-baseline.json"
BASELINE_TOOL = PACKAGES / "evaldb" / "tests" / "evalschema_baseline.py"
#: The Phase 3 gate commit, the Phase 4 baseline (docs/phase4/SCOPE.md Decisions D4).
BASELINE_COMMIT = "a1f01617116cb44f749f4e642d4fcb5f165f8e4a"
SAMPLEBOT = ROOT / "tools" / "samplebot"
POLICY = ROOT / "docs" / "security" / "license-policy.toml"

#: The P4-03 modules: the scan below must have read them (a scan of nothing proves nothing).
P4_03_MODULES = ("simtypes.py", "tts.py", "llm.py", "brain.py")

#: Attribute or name tokens of the asyncio network API (ADR 0026 rule 2): connections, servers,
#: datagram endpoints, socket helpers of the event loop, streams, TLS upgrade, name resolution
#: and subprocess pipes.
ASYNCIO_NETWORK_NAMES = frozenset(
    {
        "open_connection",
        "start_server",
        "open_unix_connection",
        "start_unix_server",
        "create_connection",
        "create_server",
        "create_datagram_endpoint",
        "create_unix_connection",
        "create_unix_server",
        "connect_accepted_socket",
        "connect_read_pipe",
        "connect_write_pipe",
        "sock_connect",
        "sock_accept",
        "sock_recv",
        "sock_recv_into",
        "sock_recvfrom",
        "sock_recvfrom_into",
        "sock_sendall",
        "sock_sendto",
        "sock_sendfile",
        "sendfile",
        "start_tls",
        "getaddrinfo",
        "getnameinfo",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "subprocess_exec",
        "subprocess_shell",
        "StreamReader",
        "StreamWriter",
        "StreamReaderProtocol",
        "DatagramProtocol",
        "SelectorEventLoop",
        "ProactorEventLoop",
    }
)
#: Modules whose import gives code a way to open a socket or to wait on one, beyond the ones
#: ``test_evalcore_imports_no_network_module`` lists.
EVENT_LOOP_MODULES = frozenset(
    {
        "selectors",
        "select",
        "_socket",
        "_ssl",
        "_asyncio",
        "asyncore",
        "asynchat",
        "uvloop",
        "trio",
        "anyio",
        "curio",
        "gevent",
        "eventlet",
        "twisted",
        "tornado",
        "websockets",
        "aiortc",
        "av",
        "wsproto",
        "h2",
    }
)
NETWORK_LOADED = frozenset(
    {
        "asyncio",
        "asyncio.streams",
        "asyncio.base_events",
        "asyncio.selector_events",
        "selectors",
        "select",
        "socket",
        "_socket",
        "ssl",
        "_ssl",
    }
)


def asyncio_network_hits(source: str) -> list[str]:
    """Every use of the asyncio network API, an event-loop module or an asyncio submodule.

    Plain ``import asyncio`` is not a hit here (``test_evalcore_imports_no_network_module``
    refuses it separately); using a network name through any alias, importing it from asyncio
    or from an asyncio submodule, naming it in a string (``getattr``) or importing a loop library
    is.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in EVENT_LOOP_MODULES or (top == "asyncio" and alias.name != "asyncio"):
                    hits.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.level == 0 and (
                top in EVENT_LOOP_MODULES or (top == "asyncio" and "." in (node.module or ""))
            ):
                hits.append(f"line {node.lineno}: from {node.module} import")
            if node.level == 0 and top == "asyncio":
                for alias in node.names:
                    if alias.name in ASYNCIO_NETWORK_NAMES or alias.name in {
                        "streams",
                        "base_events",
                    }:
                        hits.append(f"line {node.lineno}: from asyncio import {alias.name}")
        elif isinstance(node, ast.Attribute) and node.attr in ASYNCIO_NETWORK_NAMES:
            hits.append(f"line {node.lineno}: attribute {node.attr}")
        elif isinstance(node, ast.Name) and node.id in ASYNCIO_NETWORK_NAMES:
            hits.append(f"line {node.lineno}: name {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if (
                text in ASYNCIO_NETWORK_NAMES
                or ("." in text and text.split(".")[0] in EVENT_LOOP_MODULES)
                or (text.startswith("asyncio.") and text != "asyncio.")
            ):
                hits.append(f"line {node.lineno}: string {text!r}")
    return hits


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _run_isolated(code: str) -> str:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


_WALK = """
import importlib, json, pkgutil, sys
before = set(sys.modules)
for base in __BASES__:
    pkg = importlib.import_module(base)
    for m in pkgutil.walk_packages(pkg.__path__, base + "."):
        if m.name.rsplit(".", 1)[-1] == "__main__":
            continue
        importlib.import_module(m.name)
print(json.dumps(sorted(n for n in sys.modules if n not in before)))
"""


def _modules_loaded_by(*bases: str) -> set[str]:
    return set(json.loads(_run_isolated(_WALK.replace("__BASES__", repr(list(bases))))))


# ---------------------------------------------------------------- the SCOPE tests (P4-01)


def test_evalcore_imports_no_asyncio_network() -> None:
    scanned = []
    offences = []
    for path in sorted(EVALCORE_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned.append(path.relative_to(EVALCORE_SRC).as_posix())
        offences += [
            f"{scanned[-1]}: {hit}"
            for hit in asyncio_network_hits(path.read_text(encoding="utf-8"))
        ]
    assert not offences, offences
    # the scan reached the modules P4-03 adds to the pure core (the engine boundaries)
    missing = [m for m in P4_03_MODULES if m not in scanned]
    assert not missing, f"the pure core lacks {missing}"

    # nothing loop- or socket-capable is loaded because evalcore was imported (the evalschema
    # modules it builds on are measured separately and subtracted)
    base = _modules_loaded_by("tracer_evalschema")
    both = _modules_loaded_by("tracer_evalschema", "tracer_evalcore")
    added = both - base
    assert any(n.startswith("tracer_evalcore.tts") for n in added)
    hit = {n for n in added if n in NETWORK_LOADED or n.split(".")[0] in EVENT_LOOP_MODULES}
    assert not hit, sorted(hit)

    # the checker has teeth: each snippet is a violation, and ordinary code is not
    violations = [
        "import asyncio\nasync def f():\n    return await asyncio.open_connection('h', 1)",
        "import asyncio as aio\nr = aio.start_server",
        "from asyncio import open_connection",
        "from asyncio import streams",
        "from asyncio.streams import StreamReader",
        "import asyncio.base_events",
        "import asyncio\nloop = asyncio.get_running_loop()\nloop.create_connection(None, 'h')",
        "loop.sock_connect(s, a)",
        "getattr(loop, 'create_datagram_endpoint')",
        "import selectors",
        "import select",
        "from selectors import DefaultSelector",
        "import uvloop",
        "import trio",
        "import websockets.asyncio.client",
        "import aiortc",
        "import importlib\nimportlib.import_module('asyncio.streams')",
        "x = 'asyncio.streams'",
        "import asyncio\nasyncio.create_subprocess_exec('ls')",
        "from asyncio import start_tls as t",
    ]
    for source in violations:
        assert asyncio_network_hits(source), source
    clean = [
        "import dataclasses\n\n@dataclasses.dataclass\nclass A:\n    x: int",
        "import asyncio\nasyncio.sleep",
        "import re\nre.compile('connection')",
        "def create(x):\n    return x",
        "text = 'open the connection'",
        "from collections.abc import Callable",
    ]
    for source in clean:
        assert not asyncio_network_hits(source), source


def test_evalschema_unchanged_since_phase4_baseline(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("evalschema_baseline", BASELINE_TOOL)
    assert spec is not None
    assert spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(recorded) == {
        "commit",
        "schema_lock_sha256",
        "source_tree_sha256",
        "pyproject_sha256",
    }
    assert recorded["commit"] == BASELINE_COMMIT
    current = tool.current_values()
    for key, value in current.items():
        assert re.fullmatch(r"[0-9a-f]{64}", recorded[key]), key
        assert recorded[key] == value, f"tracer-evalschema changed since the baseline: {key}"
    # Phase 4 starts from the tree that Phase 3 recorded: the values did not move in between
    phase3 = json.loads(BASELINE_PHASE3.read_text(encoding="utf-8"))
    for key in ("schema_lock_sha256", "source_tree_sha256", "pyproject_sha256"):
        assert recorded[key] == phase3[key], key
    # the hash follows content: a copy with one byte more hashes differently
    copy = tmp_path / "probe"
    shutil.copytree(EVALSCHEMA_SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert tool.source_tree_sha256(copy) == recorded["source_tree_sha256"]
    target = copy / "ids.py"
    target.write_bytes(target.read_bytes() + b"\n")
    assert tool.source_tree_sha256(copy) != recorded["source_tree_sha256"]
    # where the baseline commit is in the clone (a shallow checkout lacks it), the recorded values
    # are the ones of that commit's tree, not only of the current one
    git = shutil.which("git")
    if git is not None:
        known = subprocess.run(  # noqa: S603
            [git, "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if known.returncode == 0:
            archive = tmp_path / "at-baseline"
            archive.mkdir()
            packed = subprocess.run(  # noqa: S603
                [git, "archive", BASELINE_COMMIT, "packages/evalschema/src",
                 "packages/evalschema/pyproject.toml"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )  # fmt: skip
            tar = shutil.which("tar")
            assert tar is not None
            subprocess.run(  # noqa: S603
                [tar, "-x", "-C", str(archive)], input=packed.stdout, check=True
            )
            source = archive / "packages" / "evalschema" / "src" / "tracer_evalschema"
            assert tool.source_tree_sha256(source) == recorded["source_tree_sha256"]
            assert (
                tool._sha256(source / "schemas" / "schema.lock") == recorded["schema_lock_sha256"]
            )
            pyproject = archive / "packages" / "evalschema" / "pyproject.toml"
            assert tool._sha256(pyproject) == recorded["pyproject_sha256"]


def test_samplebot_not_a_workspace_member_and_not_in_uv_lock() -> None:
    data = _toml(SAMPLEBOT / "pyproject.toml")
    project = data["project"]
    assert project["name"] == "tracer-samplebot"
    assert project["license"] == "Apache-2.0"
    assert project["requires-python"].startswith(">=3.11")
    assert data["tool"]["uv"]["package"] is False, "an application, never built into a wheel"
    assert "workspace" not in data["tool"]["uv"], "its own project, not a workspace root"
    assert "sources" not in data["tool"]["uv"], "no workspace source: it uses no tracer package"
    assert (SAMPLEBOT / "README.md").read_text(encoding="utf-8").strip()
    # Pipecat is pinned exactly (extras allowed), and no tracer package is a dependency
    names = [canonicalize_name(Requirement(d).name) for d in project["dependencies"]]
    assert "pipecat-ai" in names
    assert not [n for n in names if n.startswith("tracer-")]
    (pipecat,) = [
        Requirement(d) for d in project["dependencies"] if d.lower().startswith("pipecat-ai")
    ]
    assert pipecat.specifier == SpecifierSet("==1.14.0")
    assert len(list(pipecat.specifier)) == 1
    policy = _toml(POLICY)["tools"]["samplebot"]
    assert policy["pin"] == "pipecat-ai==1.14.0"

    # not a member of the root workspace: no member pattern matches it, and it is not a member
    root = _toml(ROOT / "pyproject.toml")
    members = root["tool"]["uv"]["workspace"]["members"]
    expanded = {p.resolve() for pattern in members for p in ROOT.glob(pattern)}
    assert SAMPLEBOT.resolve() not in expanded
    assert not any(SAMPLEBOT.resolve() in p.parents for p in expanded)
    mentioned = json.dumps([root["dependency-groups"], root["tool"]["uv"]["sources"], members])
    assert "samplebot" not in mentioned, "the root project neither depends on nor sources it"

    # not in the root lock: no Pipecat, no sample-bot package, no source line that names it
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    lock = _toml(ROOT / "uv.lock")
    locked = {canonicalize_name(p["name"]) for p in lock["package"]}
    assert "pipecat-ai" not in locked
    assert "tracer-samplebot" not in locked
    assert "samplebot" not in lock_text
    assert "pipecat" not in lock_text.lower()
    # its own lock, once it exists (P4-26 at the latest), is a separate file at a separate path
    own = SAMPLEBOT / "uv.lock"
    if own.exists():
        own_lock = _toml(own)
        own_names: dict[str, dict[str, Any]] = {
            str(canonicalize_name(p["name"])): p for p in own_lock["package"]
        }
        assert own_names["pipecat-ai"]["version"] == "1.14.0"
        assert not [n for n in own_names if n.startswith("tracer-") and n != "tracer-samplebot"]
        assert own_names["tracer-samplebot"]["source"] == {"virtual": "."}


@pytest.mark.parametrize("name", ["pipecat-ai", "tracer-samplebot"])
def test_samplebot_and_pipecat_are_not_installed_in_the_tracer_environment(name: str) -> None:
    """The root environment installs neither the tool nor Pipecat (added by qa)."""
    with pytest.raises(md.PackageNotFoundError):
        md.version(name)
