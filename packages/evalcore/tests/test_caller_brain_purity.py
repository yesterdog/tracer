# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-13 to P4-15: the brain modules are pure (ADR 0026 rule 2, ADR 0028 rules 8 and 12;
contract docs/phase4/api-surface-batch-c.md section 9).

``callerline``, ``prompt`` and ``brains`` read no clock, no randomness, no environment variable,
no key and no network, print nothing, log nothing and run no program. The tests scan the sources
and then play every brain with the clock, the random sources and the environment rigged to fail.
Scripted and replay calls, and LLM calls over a fake engine, are reproducible from their inputs.
"""

from __future__ import annotations

import json
import logging
import os
import random
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

import evalcore_phase4_batchc_support as sup  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema.evaluator import ScriptedTurn

MODULES = ("callerline", "prompt", "brains")
ALLOWED_IMPORTS = {
    "__future__",
    "ast",
    "collections",
    "dataclasses",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "stat",
    "typing",
    "unicodedata",
    "tracer_evalcore",
    "tracer_evalschema",
}
BANNED_NAMES = (
    "socket",
    "ssl",
    "select",
    "selectors",
    "asyncio",
    "http",
    "urllib",
    "subprocess",
    "multiprocessing",
    "threading",
    "ctypes",
    "pickle",
    "marshal",
    "shelve",
    "time",
    "datetime",
    "random",
    "secrets",
    "uuid",
    "logging",
    "warnings",
    "sys",
    "environ",
    "getenv",
    "putenv",
    "system",
    "popen",
    "print",
    "input",
    "eval",
    "exec",
    "compile",
    "__import__",
    "getpass",
)


def test_brain_modules_import_only_the_allowed_and_name_nothing_banned() -> None:
    """ADR 0026 rule 2 and ADR 0028 rules 8 and 12: the brain modules reach no network, clock,
    random source, environment, log or program, and carry the SPDX header; read from the AST so
    that no path can hide it."""
    for name in MODULES:
        source = sup.module_source(name)
        assert source.startswith(
            "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 The Tracer contributors\n"
        ), name
        extra = sup.imports_of(source) - ALLOWED_IMPORTS
        assert extra == set(), (name, extra)
        assert sup.forbidden_in_source(source, BANNED_NAMES) == [], name
        assert sup.mod(name).__all__ == sorted(sup.mod(name).__all__, key=sup.all_order), name


def test_the_brains_do_not_import_a_provider_or_a_network_package() -> None:
    """ADR 0026 rule 2: the pure core never names a provider, the simulator, the CLI, the store or
    an HTTP client."""
    for name in MODULES:
        source = sup.module_source(name)
        for word in (
            "tracer_evalproviders",
            "tracer_evalsim",
            "tracer_cli",
            "tracer_evaldb",
            "httpx",
        ):
            assert word not in source, (name, word)


def rig(patch: pytest.MonkeyPatch) -> sup.EnvSpy:
    """Make every clock and random source of the standard library fail the test, and record every
    read of the environment. Not rigged: ``time.time_ns`` and ``secrets.randbits``, which the
    schema's id generator needs to build the fakes' records."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a clock or a random source was read")

    for module, names in (
        (time, ("time", "monotonic", "perf_counter", "process_time", "monotonic_ns", "sleep")),
        (random, ("random", "randint", "choice", "shuffle", "seed", "getrandbits", "sample")),
        (secrets, ("token_bytes", "token_hex", "randbelow", "choice")),
        (uuid, ("uuid1", "uuid4")),
        (os, ("getenv", "getpid")),
    ):
        for attribute in names:
            patch.setattr(module, attribute, refuse)
    spy = sup.EnvSpy(os.environ)
    patch.setattr(os, "environ", spy)
    spy.active = True
    return spy


def play_everything(tmp_path: Path) -> list[Any]:
    """Every brain, whole fixture scenarios, then a hostile LLM call; returns what they decided."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    brains = sup.mod("brains")
    out: list[Any] = []
    for number, scenario in enumerate(sup.sim_mr()["scenarios"]):
        lines = [t["text"] for t in scenario["scripted_turns"]]
        turns = [ScriptedTurn.model_validate(t) for t in scenario["scripted_turns"]]
        out += sup.drive(brains.ScriptedBrain(turns), [None] * (len(lines) + 1), sup.RecordingTts())
        directory = tmp_path / f"replay{number}"
        directory.mkdir()
        for index, line in enumerate(lines):
            (directory / f"turn-{index + 1:04d}.json").write_text(
                json.dumps({"line": line}), "utf-8"
            )
        replay = brains.ReplayBrain(
            directory, language="mr", max_turns=len(lines), max_line_chars=300
        )
        out += sup.drive(replay, [None] * (len(lines) + 1), sup.RecordingTts())
        replies = [*lines, "END_CALL"]
        engine = sup.FakeLlm(replies)
        agents = [r["text"] for r in scenario["agent_replies"]]
        llm = sup.make_llm_brain(engine, max_turns=len(lines) + 2)
        out += sup.drive(llm, [*agents, None], sup.RecordingTts())
    texts = list(sup.HOSTILE_AGENT_TEXTS.values())
    engine = sup.FakeLlm(["latin", sup.LINE_A] * len(texts))
    out += sup.drive(
        sup.make_llm_brain(engine, max_turns=len(texts) + 1), texts, sup.RecordingTts()
    )
    return out


def test_brains_read_no_clock_no_random_source_and_no_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Determinism of scripted and replay with an injected clock and seed: there is nothing to
    inject, because nothing is read. The only seed is the one recorded in the profile and sent
    unchanged to the engine."""
    decisions_plain = play_everything(tmp_path / "plain")
    with monkeypatch.context() as patch:
        spy = rig(patch)
        decisions_rigged = play_everything(tmp_path / "rigged")
        spy.active = False
    assert decisions_plain == decisions_rigged
    assert len(decisions_plain) > 100
    assert spy.reads == [], "the environment was read"


def test_two_plays_of_every_brain_are_identical(tmp_path: Path) -> None:
    """Rule 4 and 12: the same inputs give the same decisions in two plays, so a stored run can be
    rebuilt."""
    first = play_everything(tmp_path / "one")
    second = play_everything(tmp_path / "two")
    assert first == second


def test_llm_runs_with_one_seed_send_the_same_bodies_and_the_seed_is_never_claimed() -> None:
    """ADR 0028 rule 5: the seed is sent as given on every request and recorded; the brain says
    nothing about determinism (the provider does not promise any)."""
    bodies = []
    for _ in range(2):
        engine = sup.FakeLlm(["latin", sup.LINE_A, sup.LINE_B])
        brain = sup.make_llm_brain(engine, seed=1234)
        sup.drive(brain, [sup.AGENT_A, "x"], sup.RecordingTts())
        assert {r.seed for r in engine.requests} == {1234}
        bodies.append([sup.request_body(r) for r in engine.requests])
        assert brain.profile_block()["seed"] == 1234
    assert bodies[0] == bodies[1]
    module = sup.module_source("brains").lower()
    for claim in ("deterministic", "reproducible", "guarantee"):
        assert claim not in module.replace("not a determinism guarantee", "")


def test_the_brains_write_nothing_and_log_nothing(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """ADR 0028 rules 6 and 12: no stream, no log record and no file comes from a brain; only counts
    are kept."""
    with caplog.at_level(logging.DEBUG):
        play_everything(tmp_path)
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["replay0", "replay1", "replay2"]
