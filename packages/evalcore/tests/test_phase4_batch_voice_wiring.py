# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch voice inventory (BL-369): every new test name is defined by exactly one test and none is
on a pending set, the contract lists the class to key table the code implements, the named spec
changes reached the tests and the ADR text they are about, and no test module of the batch imports
a Tracer module at module level (so a missing symbol fails one test, not the collection).

Contract: docs/phase4/api-surface-batch-voice.md sections 0, 3, 8, 9 and 10.
"""

from __future__ import annotations

import ast
import importlib
import re

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

CONTRACT = sup.ROOT / "docs" / "phase4" / "api-surface-batch-voice.md"
#: the test names the ADR text cites for BL-369 (rule 9, rule 16 and rule 19 of ADRs 0026, 0027)
NAMES = (
    "test_the_end_reasons_are_the_twelve_of_batch_g_and_voice_failed_last",
    "test_a_failed_synthesis_ends_the_caller_with_voice_failed_and_typed_facts",
    "test_a_failure_of_the_voice_at_the_first_line_is_not_a_successful_run",
    "test_the_exit_code_table_says_what_two_means_with_the_voice",
    "test_tdeg_sim_voice_failed_every_case_is_exactly_what_adr_0027_rule_16_says",
    "test_tdeg_sim_voice_failed_schema_identical_to_the_other_cases",
)
#: the test modules of the batch (names relative to ``packages``)
BATCH_FILES = (
    "cli/tests/cli_p4v_support.py",
    "cli/tests/test_cli_simulate_voice_failed.py",
    "cli/tests/test_tdeg_sim_voice_failed.py",
    "evalcore/tests/test_phase4_batch_voice_wiring.py",
    "evalcore/tests/test_sim_voice_failed_records.py",
    "evalsim/tests/test_evalsim_voice_failed.py",
)
THIRTEEN = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
    "voice_failed",
)


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


def test_every_name_the_adr_text_cites_for_voice_failed_is_defined_by_exactly_one_test() -> None:
    counts = definitions_by_name()
    problems = [f"{n}: {counts.get(n, 0)} definitions" for n in NAMES if counts.get(n, 0) != 1]
    assert problems == [], "\n".join(problems)


def test_every_test_of_the_voice_batch_is_defined_once_in_the_whole_workspace() -> None:
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


def test_no_name_of_the_voice_batch_is_left_on_a_pending_set() -> None:
    pending = set().union(*sup.PENDING.values()) | set(sup.PENDING_OUTSIDE_RULES)
    assert not pending & set(NAMES), sorted(pending & set(NAMES))


def test_the_voice_contract_exists_and_the_sections_the_tests_cite_exist() -> None:
    text = CONTRACT.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    missing = [name for name in NAMES if f"`{name}`" not in text]
    assert missing == [], missing
    headings = {int(m.group(1)) for m in re.finditer(r"(?m)^## (\d+)\. ", text)}
    assert headings == set(range(0, 14)), sorted(headings)
    cited: set[int] = set()
    for relative in BATCH_FILES:
        source = (sup.PACKAGES / relative).read_text("utf-8")
        for match in re.finditer(
            r"api-surface-batch-voice\.md\s+sections?\s+(\d+)(?:(?:,|\s+and|\s+to)\s+(\d+))?"
            r"(?:\s+and\s+(\d+))?",
            source,
        ):
            cited |= {int(g) for g in match.groups() if g}
    assert cited, "no test cites the contract"
    assert cited <= headings, sorted(cited - headings)
    for heading in (
        "## 0. Where the tests are, and how to move them",
        "## 6. Reachability: which callers can lose their voice",
        "## 8. The text the architect applies (proposed ADR and SCOPE amendments)",
        "## 9. Named spec changes",
        "## 12. Ambiguities, with recommendations",
        "## 13. Contradictions and findings",
    ):
        assert heading in text, heading


def test_the_class_to_key_table_of_the_contract_is_the_one_the_code_implements() -> None:
    """Section 3.1 of the contract is the specification of the mapping; this test reads it and
    runs the code against it, so neither can drift without the other."""
    text = CONTRACT.read_text("utf-8")
    start = text.index("### 3.1 ")
    end = text.index("\n## ", start)
    rows = re.findall(r"(?m)^\| `(Tts\w+)` \| `(tts_\w+)` \|", text[start:end])
    assert [r[0] for r in rows] == [
        "TtsAuthError",
        "TtsRequestRejected",
        "TtsRateLimited",
        "TtsServerError",
        "TtsTransportError",
        "TtsResponseInvalid",
        "TtsError",
    ]
    lines = importlib.import_module("tracer_evalsim.lines")
    tts = importlib.import_module("tracer_evalcore.tts")
    assert sorted({code for _, code in rows}) == list(lines.VOICE_FAILURE_CODES)
    for name, code in rows:
        error = getattr(tts, name)(index=0)
        assert lines.voice_failure_code(error) == code, name


def test_no_batch_voice_test_module_imports_a_tracer_module_at_module_level() -> None:
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


def test_the_spec_change_reached_the_tests_that_pin_the_thirteen_end_reasons() -> None:
    """S-V1: every test file that lists the end reasons lists the thirteen, in this order."""
    for relative in (
        "evalcore/tests/evalcore_batcha_support.py",
        "evalcore/tests/test_sim_outcome_doc.py",
        "evalcore/tests/test_adr0027_documents_match_json_schemas.py",
        "evalproviders/tests/test_sarvam_sim_calllog.py",
    ):
        source = (sup.PACKAGES / relative).read_text("utf-8")
        found = re.search(r"END_REASONS[^=]*=\s*[\(\[]\s*([^\)\]]*)[\)\]]", source)
        assert found is not None, relative
        assert tuple(re.findall(r'"([a-z_]+)"', found.group(1))) == THIRTEEN, relative


def test_the_adr_text_says_thirteen_and_defines_the_new_reason() -> None:
    """S-V3 to S-V6: the ADRs agree with the code that the tests pin."""
    adr26 = sup.ADR26.read_text("utf-8")
    rules = dict(sup.numbered(sup.heading_section(adr26, "## Decision"), r"(\d+)\. \*\*"))
    rule9 = rules["9"]
    listed = rule9[rule9.index("exactly the end reasons") : rule9.index("Every call ends")]
    assert tuple(re.findall(r"`([a-z_]+)`", listed)) == THIRTEEN
    assert rule9.count("`voice_failed`") >= 3
    assert "`voice_failed` (amended 2026-10-28, BL-369)" in rule9
    assert "synthesised before the call" in rule9
    assert "`brain_failed` or `voice_failed`" in rules["19"]
    assert "`voice_failed`" in rules["19"]
    assert "thirteen end reasons" in adr26
    assert "twelve end reasons" not in adr26
    adr29 = sup.ADR29.read_text("utf-8")
    assert "thirteen end reasons" in adr29
    assert "twelve end reasons" not in adr29
    adr27 = sup.ADR27.read_text("utf-8")
    assert '"brain_failed", "voice_failed"]' in adr27
    assert "`end_reason` `voice_failed` implies `caller_text_origin` `llm`" in adr27
    rules27 = dict(sup.numbered(sup.heading_section(adr27, "## Decision"), r"(\d+)\. \*\*"))
    assert "`voice_failed`" in rules27["16"]
    scope = sup.SCOPE4.read_text("utf-8")
    assert (
        "`brain_failed` (added 2026-10-28, BL-338), `voice_failed` (added 2026-10-28, BL-369)"
        in scope
    )
