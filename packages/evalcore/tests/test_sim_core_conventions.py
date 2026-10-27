# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Conventions of the Phase 4 batch A modules of tracer-evalcore (P4-04 to P4-07; contract
docs/phase4/api-surface-batch-a.md sections 1 and 7): pure code with its clock and randomness
injected (ADR 0026 rule 2), the package's file conventions, the shipped ``tracer.timing/1`` schema
equal to the ADR's block, and the SCOPE test names of the four tasks defined exactly once.

These tests read source files and documents; they open no socket and import the modules only to
read their constants.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    ADR27,
    BATCH_A_MODULES,
    EVALCORE_SRC,
    PACKAGES,
    SCOPE4,
    simtiming_mod,
    simtypes_mod,
)

#: Modules whose import gives code a clock, randomness, the environment, a thread, a process, a
#: socket or a file system handle. The batch A modules are pure: they compute from their arguments.
FORBIDDEN_IMPORTS = frozenset(
    {
        "time",
        "datetime",
        "random",
        "secrets",
        "uuid",
        "os",
        "sys",
        "signal",
        "threading",
        "multiprocessing",
        "subprocess",
        "asyncio",
        "socket",
        "ssl",
        "select",
        "selectors",
        "tempfile",
        "shutil",
        "logging",
        "pathlib",
        "io",
        "ctypes",
        "inspect",
        "importlib",
    }
)
#: Attribute or name tokens that read a clock or an entropy source, whatever the import spelling.
FORBIDDEN_TOKENS = frozenset(
    {
        "monotonic",
        "monotonic_ns",
        "time_ns",
        "perf_counter",
        "perf_counter_ns",
        "process_time",
        "utcnow",
        "urandom",
        "getrandbits",
        "token_bytes",
        "token_hex",
        "uuid4",
        "uuid7",
        "sleep",
        "environ",
        "getenv",
    }
)
SCOPE_ROWS = ("P4-04", "P4-05", "P4-06", "P4-07")


def impurity(source: str, name: str) -> list[str]:
    """Every clock, randomness or ambient-state use in ``source`` (empty means pure)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    found.append(f"{name}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                found.append(f"{name}:{node.lineno}: from {node.module} import ...")
            for alias in node.names:
                if alias.name in FORBIDDEN_TOKENS:
                    found.append(f"{name}:{node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called in {"import_module", "__import__"}:
                found.append(f"{name}:{node.lineno}: dynamic import")
            if called in {"now", "today"} and isinstance(fn, ast.Attribute):
                found.append(f"{name}:{node.lineno}: reads the wall clock ({called})")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_TOKENS:
            found.append(f"{name}:{node.lineno}: {node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_TOKENS:
            found.append(f"{name}:{node.lineno}: {node.id}")
    return found


def test_sim_core_modules_import_no_clock_or_randomness() -> None:
    """ADR 0026 rule 2: the five modules compute from their arguments. No clock, no entropy, no
    environment, no thread, no file, no socket, by import or by name; and the scan has teeth."""
    read = 0
    offences: list[str] = []
    for module in BATCH_A_MODULES:
        path = EVALCORE_SRC / f"{module}.py"
        assert path.is_file(), f"{path.name} does not exist (a scan of nothing proves nothing)"
        offences += impurity(path.read_text("utf-8"), path.name)
        read += 1
    assert read == 5
    assert not offences, offences
    # the scan has teeth
    bad = [
        "import time",
        "from time import monotonic",
        "import random\nx = random.random()",
        "import datetime\nx = datetime.datetime.now()",
        "import os\nx = os.urandom(4)",
        "import secrets",
        "x = time.monotonic_ns()",
        "import uuid",
        "y = __import__('time')",
        "from datetime import datetime\nz = datetime.utcnow()",
        "import asyncio",
        "import pathlib",
        "x = os.environ['KEY']",
    ]
    for snippet in bad:
        assert impurity(snippet, "snippet"), snippet
    good = [
        "import struct\nfrom dataclasses import dataclass\nx = struct.pack('<h', 1)",
        "def f(now_ns: int) -> int:\n    return now_ns + 1",
        "from tracer_evalcore.simtypes import FRAME_MS",
    ]
    for snippet in good:
        assert not impurity(snippet, "snippet"), snippet


def _exports(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            assert isinstance(node.value, ast.List | ast.Tuple)
            return [ast.literal_eval(e) for e in node.value.elts]
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and node.value is not None
        ):
            return [ast.literal_eval(e) for e in node.value.elts]  # type: ignore[attr-defined]
    raise AssertionError("no __all__")


def isort_style_order(names: list[str]) -> list[str]:
    """The order ruff's RUF022 keeps: SCREAMING_CASE, then CamelCase, then the rest."""

    def group(name: str) -> int:
        if name.isupper() or re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            return 0
        return 1 if name[:1].isupper() else 2

    return sorted(names, key=lambda n: (group(n), n))


def _public_top_level(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not n.startswith("_")}


def test_sim_core_modules_follow_package_conventions() -> None:
    """The package's file conventions for the new modules: SPDX header, docstring, ASCII, the
    future import, and an ``__all__`` in ruff's order that lists exactly the public names."""
    for module in BATCH_A_MODULES:
        path = EVALCORE_SRC / f"{module}.py"
        text = path.read_text("utf-8")
        lines = text.splitlines()
        assert lines[0] == "# SPDX-License-Identifier: Apache-2.0", path.name
        assert lines[1] == "# Copyright 2026 The Tracer contributors", path.name
        tree = ast.parse(text)
        assert ast.get_docstring(tree), f"{path.name}: no module docstring"
        assert text.isascii(), f"{path.name}: not ASCII"
        assert "from __future__ import annotations" in text
        exports = _exports(tree)
        assert exports == isort_style_order(exports), f"{path.name}: __all__ is not in order"
        assert len(exports) == len(set(exports))
        assert set(exports) == _public_top_level(tree), (
            path.name,
            sorted(set(exports) ^ _public_top_level(tree)),
        )
    # the ordering check has teeth
    assert isort_style_order(["b", "A", "Z", "a", "C_D"]) == ["A", "C_D", "Z", "a", "b"]
    assert isort_style_order(["fn", "Cls", "CONST", "Beta"]) == ["CONST", "Beta", "Cls", "fn"]
    assert _exports(ast.parse('__all__ = ["B", "a"]')) == ["B", "a"]
    # the shared names are the ones P4-03 introduced, not copies
    st = simtypes_mod()
    assert st.FRAME_MS == 20


def test_shipped_timing_schema_equals_adr0027_block() -> None:
    """ADR 0027: ``tracer-evalcore`` ships the same schema as the ADR's block and the documents
    test compares the two. This is that comparison for ``tracer.timing/1``, shipped by P4-07 (the
    two other documents are shipped by the tasks that build them; contract section 6)."""
    section = sup.heading_section(ADR27.read_text("utf-8"), "## The `tracer.timing/1` format")
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    adr_schema: dict[str, Any] = json.loads(blocks[0])
    shipped = simtiming_mod().TIMING_JSON_SCHEMA
    assert json.loads(json.dumps(shipped)) == adr_schema
    assert adr_schema["title"] == simtiming_mod().TIMING_FORMAT == "tracer.timing/1"
    Draft202012Validator.check_schema(dict(shipped))


def _scope_names(rows: tuple[str, ...]) -> dict[str, set[str]]:
    section = sup.scope_section_a()
    out: dict[str, set[str]] = {}
    for row in rows:
        match = re.search(rf"^\| {re.escape(row)} \|.*$", section, re.MULTILINE)
        assert match is not None, f"SCOPE section A has no row {row}"
        cells = match.group(0).split("|")
        # | ID | Owner | Task | Acceptance | Tests | h |  -> the cell before the last
        out[row] = sup.cited(cells[-3])
    return out


def _all_test_definitions() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                counts[node.name] += 1
    return counts


def test_batch_a_defines_exactly_the_scope_names_once() -> None:
    """SCOPE rows P4-04 to P4-07 name 20 tests; each must exist under its exact name, once (the
    contract of this batch and the SCOPE table must not drift apart)."""
    names = _scope_names(SCOPE_ROWS)
    expected = {
        "P4-04": {
            "test_state_machine_transition_table_exact",
            "test_max_duration_is_min_of_evaluator_and_cap",
            "test_kill_switch_reaches_ended_from_every_state",
            "test_no_event_after_ended",
            "test_setup_failure_is_an_end_reason_not_an_exception",
        },
        "P4-05": {
            "test_agent_activity_is_any_nonzero_sample_no_threshold",
            "test_wait_for_silence_parameter_has_no_default",
            "test_barge_in_fires_at_offset_from_agent_onset",
            "test_comfort_noise_agent_never_goes_silent_is_max_silence_not_a_hang",
            "test_simulator_never_speaks_while_brain_pending",
        },
        "P4-06": {
            "test_recorder_is_deterministic_from_frame_log",
            "test_recorder_channel_0_is_exactly_what_was_sent",
            "test_recorder_burst_arrival_uses_playback_model",
            "test_g711_mulaw_alaw_decode_matches_itu_table",
            "test_recorder_refuses_rate_mismatch_never_resamples",
        },
        "P4-07": {
            "test_timing_first_agent_audio_exact_on_synthetic_log",
            "test_timing_gap_measured_from_last_sent_frame",
            "test_simulator_self_latency_excluded_from_agent_gap",
            "test_timing_values_carry_quantum_and_dialect",
            "test_timing_value_without_both_edges_is_error_never_zero",
        },
    }
    assert names == expected, "SCOPE rows P4-04 to P4-07 name other tests than the contract"
    counts = _all_test_definitions()
    for row, row_names in names.items():
        for name in sorted(row_names):
            assert counts[name] == 1, f"{row}: {name} is defined {counts[name]} times"
    # the old name of the renamed P4-07 test is not defined (SCOPE D-a)
    assert counts["test_no_timing_value_without_both_edges_is_not_applicable"] == 0
    assert "test_no_timing_value_without_both_edges_is_not_applicable" in SCOPE4.read_text("utf-8")


def test_batch_a_names_left_the_pending_sets() -> None:
    """The four tasks' names are defined now, so no ADR pending set may still hold one (the
    reader of batch 0 refuses a name that is both defined and pending; this says it by name)."""
    names = set().union(*_scope_names(SCOPE_ROWS).values())
    assert len(names) == 20
    for key, pending in sup.PENDING.items():
        assert not (pending & names), (key, sorted(pending & names))
    assert not (sup.PENDING_OUTSIDE_RULES & names)
    for key in ("0026", "0027", "0028", "0024A"):
        assert sup.check_adr_file(key) == [], key
