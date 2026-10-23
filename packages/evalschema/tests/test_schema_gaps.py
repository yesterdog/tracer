# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-15, part 4: the register of rules JSON Schema cannot state (N12, N38) and the self-tests of
the differential engine.

Gate item 2 lets ``KNOWN_SCHEMA_GAPS`` hold only non-blocking mapping gaps (the register of
``tests/support/mappings/gap_register.py``). The code-only rules that api-surface 19.6 N12 and 19.7
N38 describe are a different thing and have their own register, ``CODE_ONLY_RULES`` in
``_support_b8.py`` (P, contract 8: the separately named list N12 recommends). The register is data
with an owner per row; these tests keep it honest in both directions:

* every difference between a model and its exported schema that the mutation engine finds is
  claimed by a row (nothing undocumented), and every claim still matches a difference (a rule that
  gets emitted leaves the register: no stale rows);
* each row that has a witness document is a real gap (the model rejects, the schema accepts);
* each row cites documentation that still exists, and every ADR or api-surface file that says
  "code-only" is cited by some row (a rule documented in prose cannot be missing here).

Conditional presence rules that JSON Schema can state are not gaps: they are emitted as
``if``/``then`` (``test_documented_presence_rules_are_emitted_as_if_then``). Rows with
``emittable=True`` are rules that could be emitted and are not yet; each carries the
recommendation to emit it.
"""

from __future__ import annotations

import copy
import fnmatch
import functools
import json
import re
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ._support import ROOT, api
from ._support_b2 import (
    JUDGE_TEMPLATE,
    error_texts,
    jsonschema_module,
    judge_definition,
    latency_definition,
    make,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    assert_rejected3,
    entity_class3,
    snapshot_doc,
    snapshot_parts,
)
from ._support_b8 import (
    CODE_ONLY_RULES,
    NATURES,
    OWNERS,
    CodeOnlyRule,
    canonical_dumps,
    claims_match,
    disagreements,
    disagreements_or_none,
    doc_files_mentioning_code_only,
    doc_text,
    ecma_validator_class,
    entity_class_by_name,
    model_accepts,
    mutations,
    path_text,
    rendered_schema,
    schema_validator,
    variant_ids,
)

# Documents that say "code-only" without stating a rule: the architecture index, and the contract
# of this very register. Every other ADR or api-surface file that says it must be cited by a row.
NOT_RULE_BEARING: dict[str, str] = {
    "docs/adr/README.md": "index line that points at api-surface 19.6",
    "docs/phase1/api-surface-schema-export.md": "the contract of this register",
}

VARIANTS = variant_ids()


@functools.cache
def all_differences() -> dict[str, list[str]]:
    """signature -> variants where it was found, over every sample, in both directions:
    ``looser/<sig>`` and ``stricter/<sig>``. Raises ``AssertionError`` when a sample could not be
    compared (the module is not written, a sample does not build): a ratchet that compared
    nothing must fail, not pass."""
    found: dict[str, list[str]] = {}
    skipped: list[str] = []
    for entity, label in VARIANTS:
        result = disagreements_or_none(entity, label)
        if result is None:
            skipped.append(f"{entity}/{label}")
            continue
        looser, stricter = result
        for signature in looser:
            found.setdefault("looser/" + signature, []).append(label)
        for signature in stricter:
            found.setdefault("stricter/" + signature, []).append(label)
    assert not skipped, f"samples that could not be compared against their exported file: {skipped}"
    return found


def looser_signatures() -> list[str]:
    return [k.removeprefix("looser/") for k in all_differences() if k.startswith("looser/")]


def _claim_matches(claim: tuple[str, str, str], signature: str) -> bool:
    parts = signature.split("|", 2)
    return all(fnmatch.fnmatchcase(part, glob) for part, glob in zip(parts, claim, strict=True))


# -------------------------------------------------------------------------- the register


def test_register_rows_are_complete_and_unique() -> None:
    """Each row has a unique ``CO-...`` id, an owner from the project agents, a known nature, a
    summary that says what the rule is and why a schema cannot state it (at least 40
    characters), at least one documentation anchor, and claims that are three non-empty ASCII
    globs. A ``mutation`` row has a claim or a witness (otherwise nothing proves it exists)."""
    ids = [rule.rule_id for rule in CODE_ONLY_RULES]
    assert len(ids) == len(set(ids)), ids
    assert ids, ids
    for rule in CODE_ONLY_RULES:
        assert re.fullmatch(r"CO-[A-Z0-9]+(?:-[A-Z0-9]+)*", rule.rule_id), rule.rule_id
        assert rule.owner in OWNERS, (rule.rule_id, rule.owner)
        assert rule.nature in NATURES, (rule.rule_id, rule.nature)
        assert len(rule.summary) >= 40, rule.rule_id
        assert rule.summary.isascii(), rule.rule_id
        assert rule.docs, rule.rule_id
        for claim in rule.claims:
            assert len(claim) == 3, (rule.rule_id, claim)
            assert all(part and part.isascii() for part in claim), (rule.rule_id, claim)
        if rule.nature == "mutation":
            assert rule.claims or rule.witness is not None, rule.rule_id


def test_every_row_cites_documentation_that_still_says_it() -> None:
    """The anchor text of every row occurs in the document it names, so renaming or deleting the
    sentence that documents a code-only rule fails here and someone decides what happened to the
    rule. Paths are relative to the repository root and must be ADRs or api-surface files."""
    for rule in CODE_ONLY_RULES:
        for path, anchor in rule.docs:
            assert re.fullmatch(
                r"docs/(adr/[0-9]{4}-[a-z0-9-]+|phase1/api-surface[a-z-]*)\.md", path
            ), path
            assert anchor in doc_text(path), f"{rule.rule_id}: {path} no longer says {anchor!r}"


def test_every_document_that_says_code_only_is_cited_by_a_row() -> None:
    """The documentation ratchet: each ADR or api-surface file whose text says "code-only" is cited
    by at least one row of the register, or listed in ``NOT_RULE_BEARING`` with the reason it
    states no rule. A new document with a code-only list therefore cannot land unregistered. (A
    sentence-level check is not possible without parsing prose; the anchors of the previous test
    pin the sentences that are known.)"""
    cited = {path for rule in CODE_ONLY_RULES for path, _ in rule.docs}
    said = set(doc_files_mentioning_code_only())
    assert said - cited - set(NOT_RULE_BEARING) == set(), sorted(
        said - cited - set(NOT_RULE_BEARING)
    )
    stale = {path for path in NOT_RULE_BEARING if path not in said}
    assert not stale, f"listed as not rule-bearing but no longer says code-only: {sorted(stale)}"
    for path in NOT_RULE_BEARING:
        assert (ROOT / path).is_file(), path


def test_register_is_separate_from_the_mapping_known_schema_gaps() -> None:
    """Gate item 2 (and ADR 0008 Decision 8): ``KNOWN_SCHEMA_GAPS`` is the non-blocking mapping
    gap list of ``gap_register.py``. The code-only register is a different object under a
    different name; no mapping row carries a ``CO-`` id and no code-only row is a mapping row."""
    from .support.mappings import gap_register

    mapping_ids = {
        str(getattr(row, "gap_id", getattr(row, "source_key", "")))
        for row in gap_register.KNOWN_SCHEMA_GAPS
    }
    assert id(CODE_ONLY_RULES) != id(gap_register.KNOWN_SCHEMA_GAPS)
    assert not any(i.startswith("CO-") for i in mapping_ids)
    assert not {rule.rule_id for rule in CODE_ONLY_RULES} & mapping_ids


def test_emittable_rows_carry_the_recommendation_to_emit() -> None:
    """A row marked ``emittable`` says so in words ("Recommendation: emit" or "emit"), so the
    reader of the register knows it is a decision for the architect and not a limit of JSON
    Schema. Rows that are not emittable never say it."""
    for rule in CODE_ONLY_RULES:
        says = re.search(r"\bemit", rule.summary, re.IGNORECASE) is not None
        if rule.emittable:
            assert says, rule.rule_id
            assert "ecommend" in rule.summary, rule.rule_id
        else:
            assert rule.nature != "mutation" or not rule.summary.lower().startswith(
                "recommendation"
            ), rule.rule_id


# ------------------------------------------------------------------------------ the ratchet


def test_every_difference_between_a_model_and_its_schema_is_claimed_by_a_rule() -> None:
    """The register covers everything the engine finds (N12, N38, gate item 2 spirit): each
    mutation of a sample that the model rejects and the schema accepts matches the claims of at
    least one row. A model that grows a rule the schema does not state fails here until the rule
    is emitted or a row with an owner is added."""
    unclaimed = [
        s
        for s in looser_signatures()
        if not any(_claim_matches(c, s) for r in CODE_ONLY_RULES for c in r.claims)
    ]
    assert not unclaimed, "unclaimed differences:\n  " + "\n  ".join(sorted(unclaimed))


def test_every_claim_still_matches_a_difference() -> None:
    """No stale rows: each claim of the register (except the ``hyp_*`` kinds that only the
    hypothesis tests can reach) matches at least one difference found now. A claim written for a
    path "anywhere" comes as two globs, the path and ``*.path`` (the same rule appears at
    ``evaluator.name`` in a snapshot and at ``snapshot.evaluator.name`` in a bundle); the two form
    one claim, which is live when either matches. When a rule is finally emitted in the schema its
    claim stops matching and this test names the row to delete."""
    observed = looser_signatures()
    groups: dict[tuple[str, str, str, str], bool] = {}
    for rule in CODE_ONLY_RULES:
        for claim in rule.claims:
            if claim[0].startswith("hyp_"):
                continue
            kind, entity, path = claim
            key = (rule.rule_id, kind, entity, path.removeprefix("*."))
            groups[key] = groups.get(key, False) or any(_claim_matches(claim, s) for s in observed)
    stale = sorted(key for key, live in groups.items() if not live)
    assert not stale, f"claims that match nothing (emitted now? delete the row or claim): {stale}"


def test_the_schema_is_never_stricter_anywhere() -> None:
    """The other direction has no register: over every sample no mutation is accepted by the
    model and rejected by the schema."""
    stricter = [k for k in all_differences() if k.startswith("stricter/")]
    assert not stricter, stricter


def test_witnesses_are_real_gaps() -> None:
    """A row with a witness document is proved: the model rejects the document and the exported
    schema of that entity accepts it. (A witness that the schema now rejects means the rule is
    emitted: delete the row.) Digest-bound witnesses are validated with the digest check on."""
    witnessed = [
        (rule, witness)
        for rule in CODE_ONLY_RULES
        for witness in (rule.witness, *rule.more_witnesses)
        if witness is not None
    ]
    assert witnessed, "no witness rows"
    for rule, witness in witnessed:
        entity, document, digest_on = witness()
        model = entity_class_by_name()[entity]
        if digest_on:
            errors = api("errors")
            from pydantic import ValidationError

            with pytest.raises((ValidationError, errors.TracerSchemaError)):
                model.model_validate(document)
        else:
            assert not model_accepts(model, document), (
                f"{rule.rule_id}: the model accepts the witness"
            )
        assert schema_validator(rendered_schema(entity)).is_valid(document), (
            f"{rule.rule_id}: the schema rejects the witness, so the rule is emitted now"
        )


def test_at_least_one_row_per_documented_family() -> None:
    """The families N12 and N38 name are all present as rows: template variables, one_of subset,
    keyed uniqueness, placeholder names, URL rules, config_ref credential patterns, cross-record
    checks, digest verification, PerTurnSummary, Turn timing, RunVerdict counts. (The MetricResult
    presence rules left the register in revision 23: they are emitted as ``if``/``then``.)"""
    text = " ".join(rule.rule_id for rule in CODE_ONLY_RULES)
    for token in (
        "PROMPT-TEMPLATE",
        "ONE-OF",
        "UNIQUE",
        "PLACEHOLDERS",
        "URL",
        "CONFIG-REF",
        "CROSS-RECORD",
        "DIGEST",
        "PER-TURN",
        "TURN-TIMING",
        "VERDICT",
    ):
        assert token in text, token


# ------------------------------------------------------- A21: repeats inside a ConfigSnapshot


def _reseal_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    digest = api("snapshot").compute_snapshot_digest(body)
    return {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def _snapshot_with_repeat(field: str, index: int) -> dict[str, Any]:
    """A valid snapshot whose ``field`` list holds element ``index`` a second time (the digest of
    the snapshot is recomputed, so only the repeat can be the reason to reject). Definition 0
    carries ``QUOTE_MARK`` in its template: it must never be echoed."""
    parts = snapshot_parts(judge_over={"prompt_template": JUDGE_TEMPLATE + " " + QUOTE_MARK})
    doc = snapshot_doc(parts)
    doc[field].append(copy.deepcopy(doc[field][index]))
    return _reseal_snapshot(doc)


def test_config_snapshot_accepts_distinct_definitions_and_bindings() -> None:
    """A21, the control: distinct definitions and bindings (two, and three with an extra
    definition) are accepted, so the rejections below are about the repeat and nothing else."""
    snapshot = entity_class3("ConfigSnapshot")
    two = snapshot.model_validate(snapshot_doc(snapshot_parts()))
    assert len(two.metric_definitions) == len(two.metric_bindings) == 2
    extra = judge_definition(key="outcome_confirmed")
    three = snapshot.model_validate(snapshot_doc(snapshot_parts(extra_definitions=[extra])))
    assert len(three.metric_definitions) == len(three.metric_bindings) == 3


@pytest.mark.parametrize("field", ["metric_definitions", "metric_bindings"])
@pytest.mark.parametrize("index", [0, 1])
def test_config_snapshot_rejects_the_same_definition_or_binding_twice(
    field: str, index: int
) -> None:
    """A21 (auto-approved; model change, test first): a ConfigSnapshot whose ``metric_definitions``
    holds one definition twice (same ``id`` and ``version``), or whose ``metric_bindings`` holds
    one binding twice, is rejected with a ``ValidationError`` located at that field, through
    ``model_validate`` and ``model_validate_json``. The references resolve through a mapping, so
    today the repeat is accepted silently (red until the architect adds the check). The message
    says what is wrong (it contains ``duplicate``, as the bundle's ``[duplicate]`` does) and does
    not show the rejected record: neither the text of the repeated definition nor its digest value
    appears in ``str``, ``repr``, ``errors(include_input=False)`` or
    ``json(include_input=False)``."""
    snapshot = entity_class3("ConfigSnapshot")
    doc = _snapshot_with_repeat(field, index)
    error = assert_rejected3(snapshot, doc, field=field)
    texts = error_texts(error)
    assert any("duplicate" in text.lower() for text in texts), texts
    repeated_digest = doc[field][index]["content_digest"]["value"]
    for text in texts:
        assert repeated_digest not in text, text
        assert QUOTE_MARK not in text, text
    assert_input_hidden(snapshot, doc, QUOTE_MARK, field=field, label=f"{field}[{index}]")


def _snapshot_with_keyed_repeat(field: str, index: int, insert_at: int) -> dict[str, Any]:
    """A valid snapshot whose ``field`` list holds, at position ``insert_at``, a second record
    with the ``id`` and ``version`` of element ``index`` but DIFFERENT content (so a different,
    correct ``content_digest``). The snapshot digest is recomputed, so only the keyed repeat can
    be the reason to reject. The second record, and only it, carries ``QUOTE_MARK``."""
    parts = snapshot_parts()
    doc = snapshot_doc(parts)
    if field == "metric_definitions":
        original = parts["definitions"][index]
        if index == 0:
            changed = judge_definition(prompt_template=JUDGE_TEMPLATE + " " + QUOTE_MARK)
        else:
            changed = latency_definition(
                languages=["en"], implementation_ref="builtin:p95_latency_2"
            )
        other = make("MetricDefinition", **{**changed, "id": str(original.id), "version": 1})
    else:
        original = parts["bindings"][index]
        other = make(
            "MetricBinding",
            id=str(original.id),
            evaluator_id=str(original.evaluator_id),
            metric={"id": str(original.metric.id), "version": original.metric.version},
            enabled=original.enabled,
            params={"note": QUOTE_MARK},
        )
    assert (other.id, other.version) == (original.id, original.version)
    assert other.content_digest != original.content_digest
    record = other.model_dump(mode="json", by_alias=True)
    doc[field].insert(insert_at, record)
    return _reseal_snapshot(doc)


@pytest.mark.parametrize("field", ["metric_definitions", "metric_bindings"])
@pytest.mark.parametrize(("index", "insert_at"), [(0, 1), (0, 2), (1, 2)])
def test_config_snapshot_rejects_two_different_records_under_one_id_and_version(
    field: str, index: int, insert_at: int
) -> None:
    """A28 (second A21 test; the keyed half): two entries of ``metric_definitions`` or
    ``metric_bindings`` that share ``(id, version)`` but differ in content (each with its own
    valid ``content_digest``) are rejected, through ``model_validate`` (digest check on) and
    ``model_validate_json``, with an error of type ``duplicate`` located at ``(field, position of
    the repeat)``, the later of the two (the first occurrence is never reported). The message does
    not show the record: neither the marker held by the second record, nor either digest value.
    This is the keyed rule (references resolve by ``(id, version)``); the exact-repeat test above
    is the other half."""
    snapshot = entity_class3("ConfigSnapshot")
    doc = _snapshot_with_keyed_repeat(field, index, insert_at)
    digests = {e["content_digest"]["value"] for e in doc[field]}
    paths = ((snapshot.model_validate, doc), (snapshot.model_validate_json, json.dumps(doc)))
    for load, payload in paths:
        with pytest.raises(ValidationError) as caught:
            load(payload)
        error = caught.value
        found = [(e["type"], tuple(e["loc"])) for e in error.errors()]
        assert ("duplicate", (field, insert_at)) in found, found
        assert [loc for kind, loc in found if kind == "duplicate"] == [(field, insert_at)], found
        for text in error_texts(error):
            assert "duplicate" in text.lower(), text
            assert QUOTE_MARK not in text, text
            for digest in digests:
                assert digest not in text, text
    assert_input_hidden(snapshot, doc, QUOTE_MARK, field=field, label=f"{field}[{insert_at}]")


def test_keyed_repeat_in_a_snapshot_is_a_model_rule_the_schema_cannot_state() -> None:
    """A28 decision: a keyed repeat inside a ConfigSnapshot list is accepted by the exported
    schema (it has neither ``uniqueItems`` there, A21/A24, nor a keyword for uniqueness by ``id``
    and ``version``) and rejected by the model, so it is the register's
    ``CO-KEYED-UNIQUENESS`` territory. The register's mutation claims cannot list it (no mutation
    produces two different records under one key, the row has no claims), so this test is its
    witness for the ConfigSnapshot lists: the exported ``ConfigSnapshot`` schema accepts the
    document of each list and the model rejects it. If the schema ever starts rejecting one, the
    rule is emitted and the ADR 0008 Decision 8 sentence must change."""
    row = next(r for r in CODE_ONLY_RULES if r.rule_id == "CO-KEYED-UNIQUENESS")
    assert row.emittable is False
    assert row.nature == "mutation"
    validator = schema_validator(rendered_schema("ConfigSnapshot"))
    snapshot = entity_class3("ConfigSnapshot")
    control = snapshot_doc(snapshot_parts())
    assert validator.is_valid(control)
    snapshot.model_validate(control)
    for field in ("metric_definitions", "metric_bindings"):
        doc = _snapshot_with_keyed_repeat(field, 0, 1)
        assert validator.is_valid(doc), field
        with pytest.raises(ValidationError):
            snapshot.model_validate(doc)
        schema_field = rendered_schema("ConfigSnapshot")["properties"][field]
        assert "uniqueItems" not in schema_field, field
        assert "maxItems" not in schema_field, field


# ----------------------------------------------------------- self-tests of the engine (green)


class _Probe(BaseModel):
    """A tiny strict model used to prove that the engine sees both kinds of difference."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(strict=True, ge=0, le=9)
    name: str = Field(strict=True, min_length=1)


_GOOD = {"count": 3, "name": "x"}


def _probe_schema() -> dict[str, Any]:
    return _Probe.model_json_schema()


def test_engine_finds_a_schema_that_is_looser_than_its_model() -> None:
    """Planted fault 1: delete ``maximum`` from the schema. Mutations with a count above the
    model's maximum are rejected by the model and accepted by the schema, so the engine reports
    ``looser`` signatures for ``count`` and none stricter. (``int_too_big`` 2**53 exceeds 9.)"""
    schema = _probe_schema()
    del schema["properties"]["count"]["maximum"]
    looser, stricter = disagreements(_Probe, schema, _GOOD)
    assert "int_too_big|_Probe|count" in looser
    assert stricter == []


def test_engine_finds_a_schema_that_is_stricter_than_its_model() -> None:
    """Planted fault 2: add ``maxLength: 0`` to ``name``. The valid sample is refused by the
    schema, so the engine reports ``stricter`` signatures (every mutation that keeps the name)."""
    schema = _probe_schema()
    schema["properties"]["name"]["maxLength"] = 0
    _looser, stricter = disagreements(_Probe, schema, _GOOD)
    assert stricter, "a stricter schema must be reported"


def test_engine_reports_nothing_for_an_agreeing_pair() -> None:
    """The unmodified schema of the probe model agrees on every mutation except the one JSON
    Schema cannot express: ``1.0`` is an integer to the schema and an error to a strict model."""
    looser, stricter = disagreements(_Probe, _probe_schema(), _GOOD)
    assert stricter == []
    assert sorted(looser) == ["int_integral_float|_Probe|count"]


def test_mutations_are_deterministic_and_cover_every_family() -> None:
    """The same document yields the same mutations in the same order, and the set of kinds covers
    required members, nulls, retyping, unknown members, closed-set and near-miss strings, integer
    ranges, and list shapes."""
    doc = {
        "a": "text",
        "b": 3,
        "c": True,
        "d": ["x", "y"],
        "e": {"f": 1.5},
    }
    first = [(k, path_text(p), json.dumps(m, sort_keys=True)) for k, p, m in mutations(doc)]
    second = [(k, path_text(p), json.dumps(m, sort_keys=True)) for k, p, m in mutations(doc)]
    assert first == second
    kinds = {k for k, _p, _m in first}
    assert kinds >= {
        "remove",
        "to_null",
        "unknown",
        "str_bad",
        "str_empty",
        "near_trailing_newline",
        "near_leading_space",
        "near_swapcase",
        "near_truncated",
        "near_extended",
        "str_to_int",
        "bool_to_str",
        "int_negative",
        "int_too_big",
        "int_fraction",
        "int_integral_float",
        "int_to_str",
        "list_empty",
        "list_repeat",
        "to_object",
        "num_to_str",
    }
    original = json.dumps(doc, sort_keys=True)
    list(mutations(doc))
    assert json.dumps(doc, sort_keys=True) == original, "the engine must not edit its input"


def test_pattern_semantics_are_ecma_for_the_end_anchor() -> None:
    """S-N90: Python's ``re.search`` lets ``$`` match before a trailing newline, ECMA-262 does
    not. The validator of this batch rejects ``"abc\\n"`` for ``^[a-z]+$`` (like every non-Python
    validator) and still accepts ``"abc"``; an escaped ``\\$`` at the end is left alone."""
    jsonschema = jsonschema_module()

    plain = jsonschema.Draft202012Validator({"type": "string", "pattern": "^[a-z]+$"})
    assert plain.is_valid("abc\n"), "the stock jsonschema package has the quirk this batch removes"
    ecma = ecma_validator_class()({"type": "string", "pattern": "^[a-z]+$"})
    assert ecma.is_valid("abc")
    assert not ecma.is_valid("abc\n")
    assert not ecma.is_valid("abc\n\n")
    dollar = ecma_validator_class()({"type": "string", "pattern": r"^a\$"})
    assert dollar.is_valid("a$")
    assert not dollar.is_valid("a")


def test_canonical_dumps_is_stable_and_ascii() -> None:
    """The reference serializer of this batch: keys sorted, two-space indent, ASCII escapes, one
    trailing LF, equal for equal data whatever the insertion order."""
    left = canonical_dumps({"b": [1, {"y": 1, "x": "\u0928"}], "a": None})
    right = canonical_dumps({"a": None, "b": [1, {"x": "\u0928", "y": 1}]})
    assert left == right
    expected = (
        b'{\n  "a": null,\n  "b": [\n    1,\n    {\n      "x": "\\u0928",\n'
        b'      "y": 1\n    }\n  ]\n}\n'
    )
    assert left == expected


def test_claims_match_by_component_globs() -> None:
    """A claim is three globs (kind, entity, path) matched one by one, so ``*`` in the entity
    never swallows the path and ``*`` in the path matches list indexes."""
    assert _claim_matches(("str_bad", "*", "name"), "str_bad|Project|name")
    assert not _claim_matches(("str_bad", "*", "name"), "str_bad|Project|evaluator.name")
    assert _claim_matches(("list_repeat", "*", "*"), "list_repeat|Run|channel_maps.*.channels")
    assert not _claim_matches(("str_bad", "Run", "*"), "str_bad|Turn|text")
    assert claims_match("a*", "abc")
    assert not claims_match("a*", "bca")


def test_a_row_can_be_built_and_is_immutable() -> None:
    """The row type is a frozen dataclass: a test cannot edit the register at run time."""
    row = CodeOnlyRule(
        "CO-X",
        "qa",
        "validator",
        "a" * 40,
        (("docs/adr/0008-schema-versioning-and-compatibility.md", "x"),),
    )
    with pytest.raises(AttributeError):
        row.owner = "dev"  # type: ignore[misc]
