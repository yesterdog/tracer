# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-15, part 3: the exported JSON Schema and the pydantic models accept and reject the same
documents (ADR 0008 Decision 8 and invariants I13, I14).

Contract: docs/phase1/api-surface-schema-export.md section 7. Every registered entity is checked
on the sample documents of ``_support_b8.sample_variants()``: several per entity, one per branch of
a conditional rule that the batch 2 to 4 helpers can reach, and the batch 7 bundle world for the
two bundles. The checks:

* the model's own dump (nulls kept, and with ``exclude_none``) validates under the exported file;
* the schema is never stricter than the model (no valid document is refused by the file);
* a mutation of a valid document (a member removed, nulled, retyped, an unknown member, a string
  outside its closed set or one character off, an integer out of range, a list emptied or given a
  repeat) is rejected by the model and by the schema alike, except the documented code-only rules
  of ``CODE_ONLY_RULES`` (``test_schema_gaps.py`` keeps that register honest);
* the same on text and IDs drawn by ``hypothesis`` (derandomized, so a failure reproduces).

Patterns are evaluated with ECMA-262 semantics for ``$`` (S-N90); ``1.0`` as an integer is a JSON
Schema rule, not a gap of the schema (``CO-INTEGRAL-FLOAT``).
"""

from __future__ import annotations

import fnmatch
import functools
import json
import re
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ._support import api
from ._support_b5 import PAYLOAD_TYPES, payload_for, redaction_class, redaction_doc, sealed_class
from ._support_b8 import (
    CODE_ONLY_RULES,
    definition_validator,
    dump_json_mode,
    ecma_validator_class,
    entity_class_by_name,
    entity_class_names,
    find_definition,
    model_accepts,
    mutations,
    path_text,
    rendered_schema,
    replace_at,
    sample_document,
    sample_or_none,
    sample_variants,
    schema_validator,
    string_leaf_paths,
    variant_disagreements,
    variant_ids,
)

# ------------------------------------------------------------------------------- helpers

VARIANTS = variant_ids()
IDS = [f"{entity}-{label}" for entity, label in VARIANTS]


def claimed(signature: str) -> list[str]:
    """Rule ids whose claims match ``kind|Entity|path``."""
    kind, entity, path = signature.split("|", 2)
    return [
        rule.rule_id
        for rule in CODE_ONLY_RULES
        for claim in rule.claims
        if all(
            fnmatch.fnmatchcase(part, glob)
            for part, glob in zip((kind, entity, path), claim, strict=True)
        )
    ]


# ---------------------------------------------------------------------------- the samples


def test_every_registered_entity_has_sample_documents() -> None:
    """No entity can go untested: the registry walk and the sample table agree. A new entity
    needs a sample here (and a file in the export) before this passes; the bundles are part of
    the table already."""
    variants = sample_variants()
    missing = sorted(set(entity_class_names()) - set(variants))
    assert not missing, f"entities without sample documents: {missing}"
    stale = sorted(set(variants) - set(entity_class_names()))
    assert not stale, f"sample documents of entities that are not registered: {stale}"
    assert all(variants[name] for name in variants)


@pytest.mark.parametrize(("entity", "label"), VARIANTS, ids=IDS)
def test_sample_documents_are_valid_for_the_model(entity: str, label: str) -> None:
    """The sample documents are real: the model accepts each one with digests verified for real,
    so a failure of the agreement tests below is never a bad sample."""
    model = entity_class_by_name()[entity]
    model.model_validate(sample_document(entity, label))


@pytest.mark.parametrize(("entity", "label"), VARIANTS, ids=IDS)
def test_roundtrip_valid(entity: str, label: str) -> None:
    """ADR 0008 I14 (SCOPE name): model -> JSON text -> model is the identity and a second dump is
    byte-identical; the exported file accepts the dump with nulls kept and the original document,
    and judges the ``exclude_none`` dump as the model does."""
    model = entity_class_by_name()[entity]
    doc = sample_document(entity, label)
    loaded = model.model_validate(doc)
    text = loaded.model_dump_json(by_alias=True)
    again = model.model_validate_json(text)
    assert again == loaded
    assert again.model_dump_json(by_alias=True) == text
    validator = schema_validator(rendered_schema(entity))
    for candidate in (json.loads(text), doc):
        errors = sorted(validator.iter_errors(candidate), key=lambda e: list(e.path))
        assert not errors, [(list(e.path), e.message[:120]) for e in errors[:3]]
    # Without the nulls the document is valid for the model exactly when it is for the schema (a
    # required-but-nullable member such as MetricResult.value cannot be left out of either).
    bare = json.loads(loaded.model_dump_json(by_alias=True, exclude_none=True))
    assert bool(validator.is_valid(bare)) is model_accepts(model, bare)


@pytest.mark.parametrize(("entity", "label"), VARIANTS, ids=IDS)
def test_serialization_schema_accepts_the_dump_as_well(entity: str, label: str) -> None:
    """ADR 0008 I13 in use: the serialization-mode schema of pydantic accepts the model's dump
    (it equals the validation schema, ``test_validation_eq_serialization_schema``)."""
    model = entity_class_by_name()[entity]
    schema = model.model_json_schema(mode="serialization")
    dumped = dump_json_mode(model, sample_document(entity, label))
    assert ecma_validator_class()(schema).is_valid(dumped)


# -------------------------------------------------------------- mutations: model versus schema


@pytest.mark.parametrize(("entity", "label"), VARIANTS, ids=IDS)
def test_schema_is_never_stricter_than_the_model(entity: str, label: str) -> None:
    """ADR 0008 Decision 8: a document the schema rejects and the model accepts is a bug in the
    schema (a valid file would be refused). There is no register for this direction."""
    _looser, stricter = variant_disagreements(entity, label)
    assert not stricter, f"schema stricter than the model: {stricter}"


@pytest.mark.parametrize(("entity", "label"), VARIANTS, ids=IDS)
def test_invalid_rejected_by_both(entity: str, label: str) -> None:
    """ADR 0008 I14 (SCOPE name ``test_invalid_rejected_by_both``): every mutation of a valid
    document that the model rejects is also rejected by the exported schema, except a rule listed
    in ``CODE_ONLY_RULES`` (N12, N38). Mutations: member removed or nulled, wrong JSON type, an
    unknown member, a string that is not a member of its closed set, near misses of every string
    (trailing newline, leading space, swapped case, one character short or long), integers
    negative, above 2**53, fractional or written as 1.0, lists emptied or given a repeat. An
    unclaimed difference lists the rule it needs: emit the keyword, or add a row with an owner."""
    looser, _stricter = variant_disagreements(entity, label)
    unclaimed = [s for s in looser if not claimed(s)]
    assert not unclaimed, (
        "the model rejects, the schema accepts, and no CODE_ONLY_RULES row claims it:\n  "
        + "\n  ".join(unclaimed)
    )


def test_required_fields_are_required_in_the_schema() -> None:
    """Required fields, directly: every field a model declares without a default is in the
    exported ``required`` list under its wire name, ``required`` names only declared properties,
    and a document without a required member is rejected by the schema. (Fields that are required
    only under a condition, ``CostRecord.amount`` when available, are checked by the mutation
    tests through their ``if``/``then``.)"""
    for entity, model in sorted(entity_class_by_name().items()):
        schema = rendered_schema(entity)
        required = {f.alias or name for name, f in model.model_fields.items() if f.is_required()}
        assert required <= set(schema["required"]), (
            entity,
            sorted(required - set(schema["required"])),
        )
        assert set(schema["required"]) <= set(schema["properties"]), entity
        label = sample_variants()[entity][0][0]
        doc = sample_document(entity, label)
        validator = schema_validator(schema)
        for name in sorted(required & set(doc)):
            assert not validator.is_valid({k: v for k, v in doc.items() if k != name}), (
                entity,
                name,
            )


def test_unknown_members_are_rejected_at_every_object_level() -> None:
    """``extra="forbid"`` (ADR 0008 I2): an unknown member is rejected by the model and by the
    schema (``additionalProperties: false``) at the top level and in every nested object of every
    sample, except the objects documented as open maps (``metadata``, ``params``, judge params);
    those the model accepts and the schema accepts, so there is nothing to claim."""
    for entity, label in VARIANTS:
        model = entity_class_by_name()[entity]
        validator = schema_validator(rendered_schema(entity))
        doc = sample_document(entity, label)
        for kind, path, mutated in mutations(doc):
            if kind != "unknown":
                continue
            ok_model = model_accepts(model, mutated)
            ok_schema = validator.is_valid(mutated)
            signature = f"unknown|{entity}|{path_text(path)}"
            assert ok_schema or not ok_model, signature
            if ok_model != ok_schema:
                assert claimed(signature), signature


# --------------------------------------------------------- value objects of the sealing layer


@pytest.mark.parametrize("payload_type", PAYLOAD_TYPES)
def test_sealed_payload_definition_agrees_with_the_model(payload_type: str) -> None:
    """The sealing value objects are reached through ``ExportBundle`` only. The exported
    ``SealedPayload`` definition accepts a really sealed payload of each of the five types and
    agrees with the model on every mutation (nonce and ciphertext alphabets and lengths, the
    closed sets, the per-type subject ID, ``subject_version``). Red until P1-14 exports the
    bundle."""
    found = find_definition("SealedPayload")
    assert found is not None, "SealedPayload is in no exported schema file"
    _file, schema = found
    validator = definition_validator(schema, "SealedPayload")
    model = sealed_class()
    doc = payload_for(payload_type)
    model.model_validate(doc)
    assert validator.is_valid(doc)
    for kind, path, mutated in mutations(doc):
        ok_model = model_accepts(model, mutated)
        ok_schema = bool(validator.is_valid(mutated))
        signature = f"{kind}|SealedPayload|{path_text(path)}"
        assert not (ok_model and not ok_schema), f"schema stricter: {signature}"
        if not ok_model and ok_schema:
            assert claimed(signature), f"unclaimed: {signature}"


def test_redaction_record_definition_agrees_with_the_model() -> None:
    """Same for ``RedactionRecord`` (four fields; ``covers`` a non-empty set of four members)."""
    found = find_definition("RedactionRecord")
    assert found is not None, "RedactionRecord is in no exported schema file"
    _file, schema = found
    validator = definition_validator(schema, "RedactionRecord")
    model = redaction_class()
    doc = redaction_doc()
    model.model_validate(doc)
    assert validator.is_valid(doc)
    for kind, path, mutated in mutations(doc):
        ok_model = model_accepts(model, mutated)
        ok_schema = bool(validator.is_valid(mutated))
        signature = f"{kind}|RedactionRecord|{path_text(path)}"
        assert not (ok_model and not ok_schema), f"schema stricter: {signature}"
        if not ok_model and ok_schema:
            assert claimed(signature), f"unclaimed: {signature}"


# ------------------------------------------------------------------------- hypothesis


HYPOTHESIS = settings(
    max_examples=300,
    derandomize=True,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
ID_ALPHABET = "0123456789abcdefABCDEF-_ \n\t\u00e9\u0968"


def _edits(wire: str) -> st.SearchStrategy[str]:
    """A valid wire ID with a few random edits: a character replaced, one inserted or removed, a
    prefix swap, a suffix."""
    return st.builds(
        lambda text, index, char, op: _apply_edit(text, index, char, op),
        st.just(wire),
        st.integers(min_value=0, max_value=len(wire)),
        st.sampled_from(sorted(set(ID_ALPHABET))),
        st.sampled_from(["replace", "insert", "delete", "suffix", "prefix"]),
    )


def _apply_edit(text: str, index: int, char: str, op: str) -> str:
    index = min(index, max(len(text) - 1, 0))
    if op == "replace":
        return text[:index] + char + text[index + 1 :]
    if op == "insert":
        return text[:index] + char + text[index:]
    if op == "delete":
        return text[:index] + text[index + 1 :]
    if op == "suffix":
        return text + char
    return char + text


@functools.cache
def _id_patterns() -> dict[str, str]:
    """Entity prefix -> the exported pattern of that entity's own ``id``."""
    patterns: dict[str, str] = {}
    for entity, cls in entity_class_by_name().items():
        patterns[cls.id_prefix] = rendered_schema(entity)["properties"]["id"]["pattern"]
    return patterns


@HYPOTHESIS
@given(data=st.data())
def test_exported_id_pattern_agrees_with_the_id_class_on_edited_and_random_ids(
    data: st.DataObject,
) -> None:
    """For every entity, a typed ID that is valid, one edit away from valid (a character replaced,
    inserted, removed, a newline or space added, a Devanagari digit, a swapped prefix) or random
    text is accepted by the exported ``id`` pattern exactly when the ID class accepts it. Both
    directions, so a pattern looser or stricter than the class fails. (ECMA-262 ``$``.)"""
    ids = api("ids")
    patterns = _id_patterns()
    prefix = data.draw(st.sampled_from(sorted(patterns)))
    other = data.draw(st.sampled_from(sorted(ids.ID_PREFIXES)))
    wire = str(ids.id_type(other).new())
    candidate = data.draw(
        st.one_of(
            st.just(wire),
            _edits(wire),
            _edits(str(ids.id_type(prefix).new())),
            st.text(alphabet=ID_ALPHABET + "abcdefghijklmnopqrstuvwxyz", max_size=60),
        )
    )
    try:
        ids.id_type(prefix).parse(candidate)
        accepted_by_class = True
    except ValueError:
        accepted_by_class = False
    validator = ecma_validator_class()({"type": "string", "pattern": patterns[prefix]})
    assert validator.is_valid(candidate) is accepted_by_class, (prefix, candidate)


@functools.cache
def _machine_key_pattern() -> str:
    """The exported pattern of ``MachineKey`` (the most used identifier type)."""
    text = api("text")
    expected = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
    from pydantic import TypeAdapter

    schema = TypeAdapter(text.MachineKey).json_schema()
    assert schema["pattern"] == expected
    for entity in entity_class_names():
        if expected in json.dumps(rendered_schema(entity)):
            return expected
    pytest.fail("no exported file uses the MachineKey pattern")


@HYPOTHESIS
@given(
    st.text(
        alphabet=st.one_of(
            st.sampled_from(list("abzAZ09._:/@+- \n\t")),
            st.sampled_from(["\u00e9", "\u0968", "\u200d", "\u202e", "\uff21", "\u2028"]),
        ),
        max_size=300,
    )
)
def test_exported_machine_key_pattern_agrees_with_the_type(candidate: str) -> None:
    """``MachineKey`` text drawn from the pattern's own alphabet plus the characters that confuse
    engines (newline, tab, no-break and line separators, a fullwidth letter, a Devanagari digit,
    ZWJ, a bidi override): the exported pattern and the pydantic type accept the same strings."""
    from pydantic import TypeAdapter, ValidationError

    text = api("text")
    pattern = _machine_key_pattern()
    adapter = TypeAdapter(text.MachineKey)
    try:
        adapter.validate_python(candidate)
        accepted = True
    except (ValidationError, ValueError):
        accepted = False
    assert (
        ecma_validator_class()({"type": "string", "pattern": pattern}).is_valid(candidate)
        is accepted
    )


@functools.cache
def _string_leaves() -> list[tuple[str, str, tuple[Any, ...]]]:
    """(entity, label, path) of every string leaf of every sample. A sample that does not build
    fails here too (a property test that drew from fewer samples than the table lists would pass
    for the wrong reason)."""
    leaves: list[tuple[str, str, tuple[Any, ...]]] = []
    unbuilt: list[str] = []
    for entity, label in VARIANTS:
        doc = sample_or_none(entity, label)
        if doc is None:
            unbuilt.append(f"{entity}/{label}")
            continue
        leaves.extend((entity, label, path) for path in string_leaf_paths(doc))
    assert not unbuilt, f"samples that do not build: {unbuilt}"
    return leaves


@HYPOTHESIS
@given(data=st.data())
def test_arbitrary_text_at_a_string_leaf_is_judged_alike_by_model_and_schema(
    data: st.DataObject,
) -> None:
    """Hypothesis negative cases for closed sets, patterns and free text at once: pick a string
    leaf of a sample document (an enum member, an ID, a digest, a timestamp, a key, a name, free
    text), replace it by arbitrary text (any Unicode except surrogates, up to 40 characters), and
    compare. The schema must never reject what the model accepts; it may accept what the model
    rejects only where a ``CODE_ONLY_RULES`` row claims that leaf for kind ``hyp_text`` or
    ``str_bad`` (label text rules, URL rules, content checks)."""
    leaves = _string_leaves()
    assert leaves, "no sample document builds"
    entity, label, path = data.draw(st.sampled_from(leaves))
    text = data.draw(st.text(max_size=40))
    model = entity_class_by_name()[entity]
    mutated = replace_at(sample_document(entity, label), path, text)
    ok_model = model_accepts(model, mutated)
    ok_schema = bool(schema_validator(rendered_schema(entity)).is_valid(mutated))
    signature = f"{entity}|{path_text(path)}"
    assert not (ok_model and not ok_schema), f"schema stricter at {signature}: {text!r}"
    if not ok_model and ok_schema:
        kinds = ("hyp_text", "str_bad")
        assert any(claimed(f"{kind}|{entity}|{path_text(path)}") for kind in kinds), (
            f"unclaimed difference at {signature}: {text!r}"
        )


def test_hypothesis_settings_are_deterministic() -> None:
    """The property tests above are derandomized and have no example database, so a CI failure
    reproduces locally and no run depends on a cached example (no flake)."""
    assert HYPOTHESIS.derandomize is True
    assert HYPOTHESIS.database is None
    assert re.fullmatch(r"\d+", str(HYPOTHESIS.max_examples))
