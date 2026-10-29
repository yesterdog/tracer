# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""Batch activity inventory (BL-313, the optional ``turn_taking.activity_min_peak``): every test
name the ADR text cites is defined by exactly one test and is on no pending set, the contract
exists with the sections the tests cite, the named spec changes reached the tests and the documents
they are about (ADR 0026 rule 10, ADR 0027 rules 10 and 13 and their two JSON blocks, the Phase 4
scope, the concept page, the example profile and the quick-start output), and no test module of the
batch imports a Tracer module at module level (so a missing symbol fails one test, not the
collection).

Contract: docs/phase4/api-surface-batch-activity.md sections 0, 2, 6, 7 and 9.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from typing import Any

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

CONTRACT = sup.ROOT / "docs" / "phase4" / "api-surface-batch-activity.md"
#: the test names the ADR text cites for BL-313 (ADR 0026 rule 10, ADR 0027 rules 10 and 13)
NAMES = (
    "test_a_frame_is_active_iff_its_largest_absolute_sample_reaches_the_threshold",
    "test_with_a_threshold_above_the_noise_the_caller_sees_the_pause_and_the_call_completes",
    "test_a_noise_floor_agent_completes_the_call_with_a_threshold_above_the_noise",
    "test_the_values_are_measured_by_the_threshold_and_not_by_the_noise",
    "test_the_document_echoes_the_member_exactly_when_it_was_given",
    "test_the_stored_profile_records_the_member_exactly_when_the_file_gave_it",
)
#: the test modules of the batch (names relative to ``packages``)
BATCH_FILES = (
    "cli/tests/cli_p4act_support.py",
    "cli/tests/test_cli_simulate_activity_min_peak.py",
    "cli/tests/test_tdeg_sim_activity_min_peak.py",
    "evalcore/tests/test_phase4_batch_activity_wiring.py",
    "evalcore/tests/test_sim_activity_threshold.py",
    "evalcore/tests/test_sim_timing_activity_threshold.py",
    "evalsim/tests/evalsim_support_activity.py",
    "evalsim/tests/test_evalsim_activity_threshold.py",
)
RANGE = {"type": "integer", "minimum": 1, "maximum": 32767}


def definitions_by_name() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(sup.PACKAGES.glob("*/tests/test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                counts[node.name] = counts.get(node.name, 0) + 1
    return counts


def rules_of(adr: Any) -> dict[str, str]:
    text = adr.read_text("utf-8")
    return dict(sup.numbered(sup.heading_section(text, "## Decision"), r"(\d+)\. \*\*"))


def schema_block(heading: str) -> dict[str, Any]:
    section = sup.heading_section(sup.ADR27.read_text("utf-8"), heading)
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    parsed: dict[str, Any] = json.loads(blocks[0])
    return parsed


# ------------------------------------------------------------------------------- inventory


def test_every_name_the_adr_text_cites_for_the_threshold_is_defined_by_exactly_one_test() -> None:
    counts = definitions_by_name()
    problems = [f"{n}: {counts.get(n, 0)} definitions" for n in NAMES if counts.get(n, 0) != 1]
    assert problems == [], "\n".join(problems)


def test_every_test_of_the_batch_is_defined_once_in_the_whole_workspace() -> None:
    counts = definitions_by_name()
    problems: list[str] = []
    for relative in BATCH_FILES:
        tree = ast.parse((sup.PACKAGES / relative).read_text("utf-8"))
        for node in tree.body:
            is_test = isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef
            ) and node.name.startswith("test_")
            if is_test and counts.get(node.name, 0) != 1:
                problems.append(f"{relative}: {node.name} x{counts.get(node.name, 0)}")
    assert problems == [], "\n".join(problems)


def test_no_name_of_the_batch_is_left_on_a_pending_set() -> None:
    pending = set().union(*sup.PENDING.values()) | set(sup.PENDING_OUTSIDE_RULES)
    assert not pending & set(NAMES), sorted(pending & set(NAMES))


def test_the_contract_exists_and_the_sections_the_tests_cite_exist() -> None:
    text = CONTRACT.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    missing = [name for name in NAMES if f"`{name}`" not in text]
    assert missing == [], missing
    headings = {int(m.group(1)) for m in re.finditer(r"(?m)^## (\d+)\. ", text)}
    assert headings == set(range(0, 12)), sorted(headings)
    cited: set[int] = set()
    for relative in BATCH_FILES:
        source = (sup.PACKAGES / relative).read_text("utf-8")
        for match in re.finditer(
            r"api-surface-batch-activity\.md\s+sections?\s+(\d+)(?:(?:,|\s+and|\s+to)\s+(\d+))?"
            r"(?:\s+and\s+(\d+))?",
            source,
        ):
            cited |= {int(g) for g in match.groups() if g}
    assert cited, "no test cites the contract"
    assert cited <= headings, sorted(cited - headings)
    for heading in (
        "## 0. Where the tests are, and how to move them",
        "## 2. The option",
        "## 6. The text the architect applies (proposed ADR, SCOPE and document amendments)",
        "## 7. Named spec changes",
        "## 9. The guarantee for a profile without the member",
        "## 10. The command",
        "## 11. Ambiguities, with recommendations",
    ):
        assert heading in text, heading


def test_no_batch_activity_test_module_imports_a_tracer_module_at_module_level() -> None:
    """A module-level import of a symbol that does not exist yet would fail the collection of the
    whole file; the tests import lazily (``importlib``) instead."""
    for relative in BATCH_FILES:
        tree = ast.parse((sup.PACKAGES / relative).read_text("utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("tracer_"), (relative, name)


# ------------------------------------------------------------------------------- the ADR text


def test_the_adr_text_defines_the_threshold_where_the_rule_is_stated() -> None:
    """S-A1 to S-A4: ADR 0026 rule 10 and ADR 0027 rules 10 and 13 say what the contract says."""
    rule10 = rules_of(sup.ADR26)["10"]
    for fact in (
        "`turn_taking.activity_min_peak`",
        "an optional integer of 1 to 32,767",
        "maximum absolute value",
        "never the wire byte",
        "Absent, the value is 1",
        "any non-zero sample",
        "`first_agent_audio_ms`",
        "`gap_ms`",
        "`overlap_ms`",
        "`barge_in_stop_ms`",
        "the extraction of the agent segments",
        "the recording keeps every sample",
        "the quantum is unchanged",
        "No energy window and no voice-activity model runs in Tracer",
        "counts as silence even when it is speech",
        "never as a statement that the audio is or is not speech",
        "`activity: any non-zero sample`",
        "When the sim profile gives the value, it is recorded",
        "a profile without it has no such record",
    ):
        assert fact in rule10, fact
    rejected = rule10[rule10.index("Rejected:") : rule10.index("Trade-off:")]
    assert "Silero VAD" in rejected and "RMS window" in rejected and "other than 1" in rejected
    tests = rule10[rule10.index("Tests:") :]
    for name in NAMES[:3]:
        assert f"`{name}`" in tests, name
    assert "`test_agent_activity_is_any_nonzero_sample_no_threshold`" in tests  # the default stays
    rules27 = rules_of(sup.ADR27)
    rule27_10 = rules27["10"]
    assert "reach `activity_min_peak` in absolute value" in rule27_10
    assert "`activity: peak >= N`" in rule27_10 and "a reader takes it as 1" in rule27_10
    for name in NAMES[3:5]:
        assert f"`{name}`" in rule27_10, name
    rule27_13 = rules27["13"]
    assert "`activity_min_peak`" in rule27_13
    assert "absent means 1" in rule27_13 and "keeps the bytes it had" in rule27_13
    assert f"`{NAMES[5]}`" in rule27_13


def test_the_two_json_blocks_of_adr_0027_carry_the_optional_member() -> None:
    """S-A5: the member is a property of ``turn_taking`` and of the timing document with the range
    1 to 32767, and it is in neither list of required keys."""
    profile = schema_block("## The `tracer.sim_profile/1` format")
    turn_taking = profile["properties"]["turn_taking"]
    assert turn_taking["properties"]["activity_min_peak"] == RANGE
    assert turn_taking["required"] == [
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
    ]
    timing = schema_block("## The `tracer.timing/1` format")
    assert timing["properties"]["activity_min_peak"] == RANGE
    assert "activity_min_peak" not in timing["required"]
    shipped_profile = importlib.import_module("tracer_evalcore.simprofile")
    shipped_timing = importlib.import_module("tracer_evalcore.simtiming")
    assert json.loads(json.dumps(shipped_profile.SIM_PROFILE_JSON_SCHEMA)) == profile
    assert json.loads(json.dumps(shipped_timing.TIMING_JSON_SCHEMA)) == timing
    text = sup.ADR27.read_text("utf-8")
    assert "the only key of the document that may be absent" in text
    assert "a missing member reads as 1" in text


def test_the_scope_and_the_concept_page_name_the_option() -> None:
    """S-A6, S-A7: one update line in the scope (Q6); one bullet in the concept page, which still
    holds the fixed sentence of what is not claimed exactly once."""
    scope = sup.SCOPE4.read_text("utf-8")
    assert "Update 2026-10-29 (BL-313" in scope and "`turn_taking.activity_min_peak`" in scope
    page = (sup.ROOT / "docs" / "concepts" / "simulated-calls.md").read_text("utf-8")
    measured = sup.heading_section(page, "## What is measured")
    assert "`activity_min_peak`" in measured
    assert "from 1 to 32767" in measured
    assert "every timing label says it" in measured
    assert "When the file gives it, the number is stored with the run" in measured
    assert "`activity: peak >= N`" in measured
    assert (
        page.count("Tracer reports what it observed at the simulator over one wire dialect;") == 1
    )


def test_the_example_profile_gives_the_member_and_the_documents_that_show_its_output_agree() -> (
    None
):
    """S-A8: the example sim profile writes the member as 1 (the rule of any non-zero sample, written
    so that the label shows it); the quick-start output of the cli README carries the label."""
    simprofile = importlib.import_module("tracer_evalcore.simprofile")
    path = sup.ROOT / "docs" / "examples" / "simulated-mr" / "sim-profile.json"
    parsed = simprofile.parse_sim_profile_file(path.read_text("utf-8"))
    assert parsed.turn_taking["activity_min_peak"] == 1
    assert json.loads(path.read_text("utf-8"))["turn_taking"] == {
        "caller_speaks_first": True,
        "wait_for_agent_silence_ms": 300,
        "max_silence_ms": 3000,
        "activity_min_peak": 1,
    }
    readme = (sup.ROOT / "docs" / "examples" / "simulated-mr" / "README.md").read_text("utf-8")
    assert "`activity_min_peak`" in readme
    cli = (sup.ROOT / "packages" / "cli" / "README.md").read_text("utf-8")
    lines = [
        x
        for x in cli.splitlines()
        if re.match(r"(first agent audio|gap after|overlap|answered caller turns)", x)
        and "observed at the simulator over" in x
    ]
    assert len(lines) == 4
    assert all(", activity: any non-zero sample" in x for x in lines), lines


def test_the_spec_change_reached_the_tests_that_pin_the_old_rule() -> None:
    """S-A9, S-A10: the test of the policy fields and the test of the optional keys name the one
    exception, so neither was weakened for anything else."""
    turn = (sup.PACKAGES / "evalcore/tests/test_sim_turntaking.py").read_text("utf-8")
    assert 'assert tuple(fields) == (*POLICY_KEYS, "activity_min_peak")' in turn
    assert 'assert set(schema["properties"]) == {*POLICY_KEYS, "activity_min_peak"}' in turn
    assert 'assert fields["activity_min_peak"].default == 1' in turn
    adr = (sup.PACKAGES / "evalcore/tests/test_adr0027_documents_match_json_schemas.py").read_text(
        "utf-8"
    )
    assert "OPTIONAL_KEYS = {" in adr
    assert adr.count('{"activity_min_peak"}') == 2
    assert "assert seen == set(OPTIONAL_KEYS)" in adr
