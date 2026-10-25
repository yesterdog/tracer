# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0023 machine-read checks, in the format of ``test_evaldb_adr0021.py`` and
``test_evaldb_adr0022.py``.

``test_adr0023_rules_are_tested``: every numbered rule of "## Decision" and every invariant cites at
least one test name in backticks, and every cited name exists in a ``packages/*/tests`` directory,
unless it is on ``PENDING``. ``PENDING`` is exactly the cited names that docs/phase3/SCOPE.md
section A assigns to tasks of batches B, D, E and F (P3-06 to P3-11 and P3-18 onwards) and that no
test defines yet. It only shrinks; the gate (P3-G) empties it. A name of batch A or batch C is never
pending: those tests exist.

``test_adr0023_flags_format_matches_json_schema``: the JSON Schema block of the section "The
``tracer.flags/1`` format" equals ``tracer_evalcore.flags.FLAGS_JSON_SCHEMA``, and the schema
accepts and refuses what the ADR says.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

from evalcore_batchc_support import ADR23, ROOT, SCOPE, flags_mod  # type: ignore[import-not-found,unused-ignore]

PACKAGES = ROOT / "packages"

# Cited by ADR 0023 and defined by no test yet. Each is named in a row of SCOPE section A for a
# task of batch B, D, E or F. Remove a name here in the commit that adds its test.
PENDING: frozenset[str] = frozenset(
    {
        "test_agreement_unit_is_ratio_never_wer_or_cer",
        "test_cli_output_never_claims_accuracy",
        "test_cli_prints_counts_with_denominators",
        "test_cli_requires_every_privacy_and_language_input",
        "test_concept_page_states_no_accuracy_claim",
        "test_definitions_requires_transcript_and_agent_transcript",
        "test_e2e_synthetic_mr_exact_outputs",
        "test_empty_export_is_error_no_tokens_not_zero",
        "test_empty_stt_is_error_no_tokens_not_zero",
        "test_evidence_refs_resolve_in_bundle",
        "test_flags_metadata_has_no_text_and_no_metadata_flags",
        "test_flags_metadata_validates_against_tracer_flags_1",
        "test_language_source_declared_from_export",
        "test_measurement_report_has_counts_and_denominators",
        "test_metrics_evaluate_agent_outputs_only",
        "test_missing_binding_param_refuses_before_any_send",
        "test_pipeline_bundle_passes_check_run_bundle",
        "test_pipeline_is_deterministic_given_same_stt",
        "test_pipeline_observed_tier_is_t0",
        "test_pipeline_started_at_absent_never_ingest_time",
        "test_report_never_claims_accuracy",
        "test_report_shows_language_declared_by_user",
        "test_results_pass_check_result_against_definition",
        "test_rubric_file_without_required_pass_rule_refused",
        "test_run_round_trips_through_store_byte_identical",
        "test_shipped_definitions_have_no_pass_rule",
        "test_stt_failure_is_missing_input_with_warning_and_nonzero_exit",
        "test_tdeg_audio_and_export_scored",
        "test_tdeg_export_without_agent_turn_missing_input_agent_transcript",
        "test_tdeg_export_without_declaration_language_not_observed",
        "test_tdeg_no_audio_missing_input_transcript",
        "test_tdeg_no_export_missing_input_agent_transcript",
        "test_tdeg_non_mr_chunk_is_not_scored",
        "test_tdeg_schema_identical_across_cases",
        "test_transcript_ids_required_with_two_transcripts",
        "test_unknown_scorer_refuses_before_run",
    }
)

BATCH_C_TASKS = ("P3-13", "P3-14", "P3-15", "P3-16", "P3-17")
BATCH_C_ADR_TESTS = (
    "test_adr0025_rule_table_matches_normaliser",
    "test_adr0023_rules_are_tested",
    "test_adr0023_flags_format_matches_json_schema",
)
BATCH_AB_TASK_NUMBERS = range(1, 12)  # P3-01 to P3-11 are batches A and B


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted(PACKAGES.glob("*/tests/test_*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        }
    return names


def _cited(text: str) -> set[str]:
    return set(re.findall(r"`(test_\w+)`", text))


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = re.search(r"\n## ", rest)
    return rest[: end.start()] if end else rest


def _numbered(section: str, pattern: str) -> list[tuple[str, str]]:
    starts = list(re.finditer(rf"^{pattern}", section, re.MULTILINE))
    items = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(section)
        items.append((match.group(1), section[match.start() : end]))
    return items


def _scope_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in SCOPE.read_text("utf-8").splitlines():
        match = re.match(r"\| (P3-[\w]+) \|", line)
        if match:
            rows[match.group(1)] = line
    return rows


def test_adr0023_rules_are_tested() -> None:
    text = ADR23.read_text("utf-8")
    defined = _defined_tests()
    rows = _scope_rows()
    named_by_scope = set().union(*(_cited(row) for row in rows.values()))
    rules = _numbered(_section(text, "## Decision"), r"(\d+)\. \*\*")
    assert [int(label) for label, _ in rules] == list(range(1, 20)), "ADR 0023 has 19 rules"
    for label, body in rules:
        cited = _cited(body)
        assert cited, f"rule {label} of ADR 0023 cites no test"
        unknown = cited - defined - PENDING
        assert not unknown, f"rule {label} cites tests that do not exist: {sorted(unknown)}"
    invariants = _numbered(_section(text, "## Invariants (testable)"), r"- (I\d+)\. ")
    assert [label for label, _ in invariants] == [f"I{i}" for i in range(1, 11)]
    for label, body in invariants:
        cited = _cited(body)
        assert cited, f"invariant {label} of ADR 0023 cites no test"
        unknown = cited - defined - PENDING
        assert not unknown, f"invariant {label} cites tests that do not exist: {sorted(unknown)}"
    # the pending set is exactly SCOPE section A names: never a misspelt or invented name
    assert named_by_scope >= PENDING, sorted(PENDING - named_by_scope)
    # ... and never a name of a test that must already exist (batch A or C, or the ADR checks)
    for task, row in rows.items():
        number = re.match(r"P3-(\d+)", task)
        early = task in BATCH_C_TASKS or (
            number is not None and int(number.group(1)) in range(1, 6)
        )
        if early:
            assert not (_cited(row) & PENDING), f"{task} names tests that exist, not pending"
    assert not set(BATCH_C_ADR_TESTS) & PENDING
    # the check checks itself
    assert "test_adr0023_rules_are_tested" in defined
    # exactly: a name whose test exists is removed from PENDING in the commit that adds the test
    assert not PENDING & defined, f"remove from PENDING: {sorted(PENDING & defined)}"
    # the pending names are real, cited names, so the set cannot hide a stale entry
    cited_anywhere = _cited(text)
    assert cited_anywhere >= PENDING, sorted(PENDING - cited_anywhere)


@pytest.mark.parametrize("task", BATCH_C_TASKS)
def test_every_test_named_by_the_scope_rows_of_batch_c_exists(task: str) -> None:
    row = _scope_rows()[task]
    named = _cited(row)
    assert named, task
    missing = named - _defined_tests()
    assert not missing, f"{task}: {sorted(missing)}"


def test_the_batch_c_rows_name_the_exact_tests_of_this_batch() -> None:
    rows = _scope_rows()
    expected = {
        "P3-13": 9,
        "P3-14": 5,
        "P3-15": 10,
        "P3-16": 6,
        "P3-17": 6,
    }
    for task, count in expected.items():
        assert len(_cited(rows[task])) == count, task
    for name in BATCH_C_ADR_TESTS:
        assert name in _defined_tests(), name


# ------------------------------------------------------------------ tracer.flags/1


def _adr_schema() -> dict[str, Any]:
    section = _section(ADR23.read_text("utf-8"), "## The `tracer.flags/1` format")
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1, "one JSON Schema block in the section"
    schema = json.loads(blocks[0])
    assert isinstance(schema, dict)
    return schema


def _good_document() -> dict[str, Any]:
    return {
        "format": "tracer.flags/1",
        "signal": "stt_disagreement",
        "normaliser": "mr-Deva/1",
        "stt": {"provider": "sarvam", "model": "saarika:v2.5", "model_source": "requested"},
        "flag_min_confidence": "0.5",
        "alignment": {"unit": "word", "hits": 3, "substitutions": 1,
                      "deletions": 0, "insertions": 1},
        "tokens": {"export": 4, "stt": 5, "flagged_export": 1},
        "spans": [
            {
                "turn_seq": 0, "start": 3, "end": 5, "export_tokens": 1, "stt_token_start": 1,
                "stt_token_end": 2, "substitutions": 1, "deletions": 0, "insertions": 0,
                "edit_distance": 2, "key_length": 2, "confidence": "1", "flagged": True,
                "near_chunk_boundary": False, "audio_start_ms": 0, "audio_end_ms": 1000,
            },
            {
                "turn_seq": 0, "start": 8, "end": 8, "export_tokens": 0, "stt_token_start": 3,
                "stt_token_end": 4, "substitutions": 0, "deletions": 0, "insertions": 1,
                "edit_distance": 3, "key_length": 3, "confidence": "0.0001", "flagged": False,
                "near_chunk_boundary": True,
            },
        ],
    }  # fmt: skip


def test_adr0023_flags_format_matches_json_schema() -> None:
    fl = flags_mod()
    adr = _adr_schema()
    shipped = fl.FLAGS_JSON_SCHEMA
    assert isinstance(shipped, dict)
    assert shipped == adr, "tracer-evalcore ships the schema of ADR 0023 unchanged"
    assert json.dumps(shipped, sort_keys=True) == json.dumps(adr, sort_keys=True)
    assert fl.FLAGS_FORMAT == "tracer.flags/1" == adr["title"]
    assert adr["properties"]["format"]["enum"] == [fl.FLAGS_FORMAT]
    assert adr["properties"]["signal"]["enum"] == [fl.SIGNAL_NAME]
    assert adr["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    # the shipped object is a copy a caller cannot use to change the next caller's schema
    again = fl.FLAGS_JSON_SCHEMA
    assert again == adr


def test_flags_schema_accepts_the_documented_document_and_refuses_each_violation() -> None:
    schema = _adr_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    good = _good_document()
    assert list(validator.iter_errors(good)) == []

    def refused(change: Any) -> bool:
        document = copy.deepcopy(good)
        change(document)
        return bool(list(validator.iter_errors(document)))

    mutations = {
        "unknown top-level key": lambda d: d.update(extra=1),
        "missing spans": lambda d: d.pop("spans"),
        "wrong format": lambda d: d.update(format="tracer.flags/2"),
        "wrong signal": lambda d: d.update(signal="other"),
        "normaliser pattern": lambda d: d.update(normaliser="mr-Deva/0"),
        "normaliser lower case script": lambda d: d.update(normaliser="mr-deva/1"),
        "model source": lambda d: d["stt"].update(model_source="observed"),
        "provider pattern": lambda d: d["stt"].update(provider="has space"),
        "threshold trailing zero": lambda d: d.update(flag_min_confidence="0.50"),
        "threshold is a number": lambda d: d.update(flag_min_confidence=0.5),
        "threshold above one": lambda d: d.update(flag_min_confidence="1.5"),
        "alignment unit": lambda d: d["alignment"].update(unit="char"),
        "negative count": lambda d: d["alignment"].update(hits=-1),
        "float count": lambda d: d["alignment"].update(hits=3.5),
        "count above 2**53-1": lambda d: d["alignment"].update(hits=2**53),
        "zero export tokens": lambda d: d["tokens"].update(export=0),
        "span extra key": lambda d: d["spans"][0].update(text="x"),
        "span edit distance zero": lambda d: d["spans"][0].update(edit_distance=0),
        "span key length zero": lambda d: d["spans"][0].update(key_length=0),
        "confidence five digits": lambda d: d["spans"][0].update(confidence="0.12345"),
        "confidence trailing zero": lambda d: d["spans"][0].update(confidence="0.50"),
        "confidence is a number": lambda d: d["spans"][0].update(confidence=0.5),
        "flagged is a string": lambda d: d["spans"][0].update(flagged="true"),
        "audio start alone": lambda d: d["spans"][0].pop("audio_end_ms"),
        "audio end alone": lambda d: d["spans"][1].update(audio_end_ms=5),
        "missing required span key": lambda d: d["spans"][0].pop("near_chunk_boundary"),
    }
    for name, change in mutations.items():
        assert refused(change), name
    # the optional audio fields may both be absent
    assert not refused(
        lambda d: [d["spans"][0].pop("audio_start_ms"), d["spans"][0].pop("audio_end_ms")]
    )
