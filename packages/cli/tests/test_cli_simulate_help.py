# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-01: the ``tracer simulate`` subcommand exists and prints its help (contract
docs/phase4/api-surface-batch-0b.md section 7.1).

The options of the command arrive with P4-23. At P4-01 the subcommand is registered, appears in the
top-level help next to ``score``, and ``tracer simulate --help`` exits 0 with the usage on stdout,
nothing on stderr, no traceback and no claim word. ``tracer-cli`` does not import
``tracer_evalsim`` (or websockets) at this point: the first import comes with P4-23, inside the
command, so importing the command line stays free of the WebSocket stack.
"""

from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
DENY_LIST = re.compile(r"(?i)\b(?:accuracy|wer|cer|error rate|correct|recall|precision)\b")


def _tracer(*args: str) -> subprocess.CompletedProcess[str]:
    script = shutil.which("tracer", path=str(Path(sys.executable).parent))
    assert script is not None, "the console script `tracer` is not installed in this environment"
    return subprocess.run(  # noqa: S603
        [script, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_tracer_simulate_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    done = _tracer("simulate", "--help")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("usage: tracer simulate"), done.stdout[:200]
    assert done.stderr == ""
    assert "Traceback" not in done.stdout
    assert done.stdout.isascii()
    # Tracer-authored help never claims accuracy (ADR 0023 rule 1, ADR 0026 rule 24)
    assert not DENY_LIST.search(done.stdout), DENY_LIST.findall(done.stdout)

    # the same through the entry point function, with the streams injected
    cli = importlib.import_module("tracer_cli.cli")
    with pytest.raises(SystemExit) as caught:
        cli.main(["simulate", "--help"])
    assert caught.value.code == 0
    printed = capsys.readouterr()
    assert printed.out.startswith("usage: tracer simulate")
    assert printed.err == ""

    # the top-level help lists both commands, and `score` is unchanged
    top = _tracer("--help")
    assert top.returncode == 0, top.stdout + top.stderr
    assert top.stdout.startswith("usage: tracer")
    assert "score" in top.stdout
    assert "simulate" in top.stdout
    assert not DENY_LIST.search(top.stdout), DENY_LIST.findall(top.stdout)
    score = _tracer("score", "--help")
    assert score.returncode == 0, score.stdout + score.stderr
    assert score.stdout.startswith("usage: tracer score")

    # importing the command line loads neither the simulator nor its WebSocket stack
    code = (
        "import json, sys\n"
        "import tracer_cli.cli\n"
        "print(json.dumps(sorted(n for n in ('tracer_evalsim', 'websockets', 'aiortc', 'av')"
        " if n in sys.modules)))\n"
    )
    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == "[]"
