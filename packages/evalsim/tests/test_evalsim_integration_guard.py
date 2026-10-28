# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT018
"""P4-30: the ``integration`` marker is loopback only, deselected unless asked for, and never a
silent skip (ADR 0026 rule 23; contract docs/phase4/api-surface-batch-f.md section 3).

The interop tests of P4-27 run a real Pipecat bot on this machine, so they need the network paths
the normal tests are denied. This file proves the exemption is narrow: a test with the marker, and
only such a test, may reach ``localhost`` or a literal loopback address over TCP, and may use
datagrams only between addresses this machine owns (WebRTC media; ICE gathers on every interface,
BL-310); a bind to a wildcard, any other destination, any name and any Unix socket is still an
``AssertionError``; the marker is registered, the tests that carry it are deselected unless the run
selects it with ``-m integration`` (the ``interop`` CI job does), and no integration module can
skip itself: a missing sample bot is a FAILURE with a message, never a skip, never an xfail and
never an import guard. The real ``conftest.py`` and the guard modules run in a fresh pytest process,
with ``--strict-markers``, as in ``test_evalsim_loopback_guard.py``; the forbidden destinations are
documentation addresses (RFC 5737) and every real socket function is replaced by a spy, so no
packet is ever sent.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from evalsim_support_inner import INNER  # type: ignore[import-not-found,unused-ignore]

HERE = Path(__file__).resolve().parent
#: the only test modules that may carry the marker (a new one is a decision, not an accident)
INTEGRATION_MODULES = {
    "test_integration_calibration.py",
    "test_integration_pipecat_protobuf.py",
    "test_integration_pipecat_twilio.py",
    "test_integration_pipecat_webrtc.py",
}
SUPPORT_MODULES = (
    "conftest.py",
    "evalsim_loopback_guard.py",
    "evalsim_integration_guard.py",
)
SKIP_WORDS = re.compile(
    r"importorskip|pytest\.skip|\.skip\(|skipif|xfail|find_spec|SkipTest|allow_module_level"
)


def _run_inner(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name in SUPPORT_MODULES:
        (tmp_path / name).write_bytes((HERE / name).read_bytes())
    (tmp_path / "test_inner.py").write_text(textwrap.dedent(INNER), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    environment = {
        k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "SARVAM_API_KEY")
    }
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--strict-markers",
            "-q",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            *arguments,
            str(tmp_path / "test_inner.py"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
        env=environment,
    )


def test_integration_tests_loopback_only(tmp_path: Path) -> None:
    """The real conftest and guard modules, in a fresh process with ``--strict-markers`` (so the
    marker must be registered): with ``-m integration`` the four marked guard tests run and pass
    (a TCP server and a UDP pair of this machine are reachable; every forbidden destination, the
    wildcards, the multicast and broadcast addresses, the names and a Unix socket are refused with
    an ``AssertionError`` before any real socket function is called); the unmarked test keeps the
    full guard; the ``loopback`` marker keeps its own narrower guard."""
    done = _run_inner(tmp_path, "-m", "integration")
    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"\b4 passed\b", done.stdout), done.stdout
    assert "deselected" in done.stdout, done.stdout


def test_the_integration_tests_are_deselected_unless_the_run_selects_them(tmp_path: Path) -> None:
    """With no ``-m`` (the default run of every normal leg) the marked tests are DESELECTED, not
    skipped and not run; ``-m "not integration"`` and ``-m loopback`` keep them out; an expression
    that names the marker lets pytest's own logic decide. Nothing is reported as skipped."""
    default = _run_inner(tmp_path / "a")
    assert default.returncode == 0, default.stdout + default.stderr
    assert re.search(r"\b2 passed\b", default.stdout), default.stdout  # the unmarked + loopback
    assert re.search(r"\b4 deselected\b", default.stdout), default.stdout
    assert "skipped" not in default.stdout
    excluded = _run_inner(tmp_path / "b", "-m", "not integration")
    assert excluded.returncode == 0, excluded.stdout + excluded.stderr
    assert re.search(r"\b2 passed\b", excluded.stdout), excluded.stdout
    both = _run_inner(tmp_path / "c", "-m", "integration or loopback")
    assert both.returncode == 0, both.stdout + both.stderr
    assert re.search(r"\b5 passed\b", both.stdout), both.stdout


def test_the_conftest_registers_the_marker_and_keeps_the_older_guards() -> None:
    text = (HERE / "conftest.py").read_text(encoding="utf-8")
    assert "install_integration_guard" in text
    assert "install_loopback_only" in text
    assert 'delenv("SARVAM_API_KEY"' in text
    assert "addinivalue_line" in text
    assert "pytest_collection_modifyitems" in text
    assert "markexpr" in text


def test_the_integration_marker_is_confined_to_the_declared_modules() -> None:
    using = set()
    for path in HERE.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"pytest\.mark\.integration|pytestmark\s*=.*integration", text):
            using.add(path.name)
    # this file mentions the marker inside a quoted inner suite; it is not a user of it
    using.discard(Path(__file__).name)
    assert using == INTEGRATION_MODULES, (
        sorted(using - INTEGRATION_MODULES),
        sorted(INTEGRATION_MODULES - using),
    )
    for name in INTEGRATION_MODULES:
        text = (HERE / name).read_text(encoding="utf-8")
        assert "mark.loopback" not in text, name  # one marker per module: one guard per module
        assert re.search(r"^pytestmark\s*=\s*pytest\.mark\.integration\s*$", text, re.MULTILINE), (
            name
        )


def test_no_integration_module_can_skip_itself() -> None:
    """The requirement: a test of the interop job is never skipped on an import error, a missing
    tool or an absent environment. The only gate is the marker (deselect) and the job (select);
    inside a selected test every failure is a failure. No skip, no xfail, no import guard, no
    ``try``/``except ImportError`` and no ``find_spec`` in the modules or in the harness."""
    names = [*sorted(INTEGRATION_MODULES), "evalsim_support_samplebot.py"]
    for name in names:
        source = (HERE / name).read_text(encoding="utf-8")
        assert not SKIP_WORDS.search(source), (name, SKIP_WORDS.search(source))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    caught = (
                        ast.unparse(handler.type) if handler.type is not None else "BaseException"
                    )
                    assert "ImportError" not in caught and "ModuleNotFoundError" not in caught, name


def test_a_missing_sample_bot_is_a_failure_with_a_message_never_a_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness raises ``SamplebotUnavailable`` (an ``AssertionError``) when the command cannot
    start, exits before it is ready or is silent past the bound; the message names the tool
    project and the command to run. ``pytest.skip.Exception`` is not what it raises."""
    import evalsim_support_samplebot as harness  # type: ignore[import-not-found,unused-ignore]

    monkeypatch.setenv(harness.COMMAND_ENV, '["/nonexistent/tracer-samplebot-command"]')
    scenario = tmp_path / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    with pytest.raises(harness.SamplebotUnavailable) as raised:
        harness.SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=16000)
    assert isinstance(raised.value, AssertionError)
    assert not isinstance(raised.value, pytest.skip.Exception)
    assert "tools/samplebot" in str(raised.value)
    assert "uv sync --locked --project tools/samplebot" in str(raised.value)
    monkeypatch.setenv(harness.COMMAND_ENV, json_command("import sys; sys.exit(3)"))
    with pytest.raises(harness.SamplebotUnavailable) as exited:
        harness.SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=16000)
    assert "exit code 3" in str(exited.value)
    monkeypatch.setenv(harness.COMMAND_ENV, json_command("import time; time.sleep(60)"))
    monkeypatch.setattr(harness, "READY_TIMEOUT_S", 1.0)
    with pytest.raises(harness.SamplebotUnavailable) as silent:
        harness.SampleBot.start("pipecat-protobuf/1", scenario, sample_rate_hz=16000)
    assert "not ready" in str(silent.value)


def json_command(code: str) -> str:
    import json

    return json.dumps([sys.executable, "-I", "-c", code])


def test_the_default_command_runs_the_tool_in_its_own_locked_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evalsim_support_samplebot as harness  # type: ignore[import-not-found,unused-ignore]

    monkeypatch.delenv(harness.COMMAND_ENV, raising=False)
    command = harness.bot_command("pipecat-protobuf/1", Path("/x/scenario.json"), 16000, port=0)
    assert Path(command[0]).name == "uv"
    assert command[1:8] == [
        "run", "--project", str(harness.SAMPLEBOT), "--locked", "python", "-m",
        "samplebot.scripted_bot",
    ]  # fmt: skip
    assert command[8:] == [
        "--dialect", "pipecat-protobuf/1", "--scenario", "/x/scenario.json",
        "--sample-rate", "16000", "--host", "127.0.0.1", "--port", "0",
    ]  # fmt: skip
    assert harness.SAMPLEBOT == harness.ROOT / "tools" / "samplebot"
    assert harness.BOT_CWD == harness.SAMPLEBOT  # `python -m samplebot...` finds the package there
