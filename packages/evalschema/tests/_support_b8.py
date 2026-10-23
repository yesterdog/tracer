# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 11 (P1-15), shared helpers: the JSON Schema export, ``schema.lock`` and the register of
code-only rules. No tests here except the helper self-tests in ``test_schema_gaps.py``.

Contract: docs/phase1/api-surface-schema-export.md. ADRs 0002, 0003 (Decision 6), 0008
(Decisions 7 to 9), 0009. The implementation module is ``tracer_evalschema.schema_export``; it is
imported lazily through ``api()`` so a module that is not written yet fails the test that needs it
and never breaks collection.

Three groups of helpers:

* the export (``rendered_files``, ``canonical_dumps``, schema walks, a validator with ECMA-262
  ``$`` semantics for patterns);
* sample documents for every registered entity, built from the batch 2 to 4 helpers and the
  batch 7 bundle world, and the mutations the agreement tests apply to them;
* ``CODE_ONLY_RULES``: every rule that JSON Schema cannot state, with an owner and the documents
  that name it (N12, N38). It is a different register from the mapping ``KNOWN_SCHEMA_GAPS`` of
  ``tests/support/mappings/gap_register.py`` (gate item 2 lets only non-blocking mapping gaps
  into that one).
"""

from __future__ import annotations

import collections
import contextlib
import copy
import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import _support_b2 as b2
from . import _support_b3 as b3
from . import _support_b4 as b4
from ._support import CURRENT_SCHEMA_VERSION, PACKAGE_DIR, ROOT, api

# ------------------------------------------------------------------------------ the contract

SCHEMA_SUFFIX = ".schema.json"
LOCK_NAME = "schema.lock"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
SHIPPED_DIR = PACKAGE_DIR / "src" / "tracer_evalschema" / "schemas"
URN_FORM = re.compile(
    r"^urn:tracer:evalschema:(?P<version>[0-9]+\.[0-9]+\.[0-9]+):(?P<entity>[A-Z][A-Za-z0-9]*)$"
)
FILE_NAME_FORM = re.compile(r"^(?P<entity>[A-Z][A-Za-z0-9]*)\.schema\.json$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Keywords of JSON Schema 2020-12 (core, applicator, unevaluated, validation, format-annotation,
# content, meta-data) and the extensions this package emits on purpose. ``const`` is absent on
# purpose (ADR 0003 Decision 6). ``discriminator`` is an OpenAPI keyword and is not allowed
# either: a closed set of variants is an ``anyOf``/``oneOf`` of objects with an ``enum`` tag.
KNOWN_KEYWORDS = frozenset(
    {
        "$schema", "$id", "$ref", "$defs", "$comment",
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
        "properties", "patternProperties", "additionalProperties", "propertyNames",
        "items", "prefixItems", "contains", "dependentSchemas",
        "type", "enum", "multipleOf", "maximum", "exclusiveMaximum", "minimum",
        "exclusiveMinimum", "maxLength", "minLength", "pattern", "maxItems", "minItems",
        "uniqueItems", "maxContains", "minContains", "maxProperties", "minProperties",
        "required", "dependentRequired",
        "format", "title", "description", "default", "deprecated", "readOnly", "writeOnly",
        "x-pii",
    }
)  # fmt: skip
EXTENSION_KEYWORDS = frozenset({"x-pii"})

# Words in a generated schema that would be environment-dependent or an accident of a repr.
REPR_LEAKS = (
    re.compile(r"<[A-Za-z_.]+ object at 0x"),
    re.compile(r"<class '"),
    re.compile(r"0x[0-9a-f]{6,}"),
)

# The owners a code-only rule may name (the project agents).
OWNERS = frozenset(
    {"schema-architect", "staff-reviewer", "security-licensing", "indic-linguistics", "dev", "qa"}
)


# ------------------------------------------------------------------------- module and bytes


def export_module() -> Any:
    return api("schema_export")


@functools.cache
def rendered_files() -> dict[str, bytes]:
    """``render_schemas()`` of the module under test (cached: it is pure)."""
    files: dict[str, bytes] = export_module().render_schemas()
    return files


def rendered_schema(entity: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(rendered_files()[entity + SCHEMA_SUFFIX])
    return parsed


def canonical_dumps(value: Any) -> bytes:
    """The byte form the contract pins for every generated JSON file: UTF-8 (ASCII only, every
    other code point is a ``\\u`` escape), object keys sorted, two-space indent, one trailing LF."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True).encode("ascii") + b"\n"


def sha256_hex(data: bytes) -> str:
    """Independent check value (the test never asks the module under test for its own digest)."""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def entity_class_names() -> list[str]:
    """Sorted class names of every registered entity (the registry walk, no hard-coded count)."""
    return sorted(cls.__name__ for cls in api("registry").entity_classes().values())


def entity_class_by_name() -> dict[str, Any]:
    return {cls.__name__: cls for cls in api("registry").entity_classes().values()}


# --------------------------------------------------------------------------- schema walks


def schema_nodes(node: Any, *, skip_data: bool = True) -> Iterator[dict[str, Any]]:
    """Every schema object nested in a JSON Schema document, root included. Names under
    ``properties``, ``patternProperties`` and ``$defs`` are not schemas; their values are.
    ``enum``, ``required``, ``default``, ``examples`` hold data, never schemas."""
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key in ("properties", "patternProperties", "$defs"):
                for sub in value.values():
                    yield from schema_nodes(sub, skip_data=skip_data)
            elif skip_data and key in ("enum", "required", "default", "examples", "const"):
                continue
            else:
                yield from schema_nodes(value, skip_data=skip_data)
    elif isinstance(node, list):
        for item in node:
            yield from schema_nodes(item, skip_data=skip_data)


def keywords_used(schema: Mapping[str, Any]) -> set[str]:
    """Every keyword that appears on a schema object of ``schema`` (property names excluded)."""
    return {key for node in schema_nodes(schema) for key in node}


def property_names_anywhere(schema: Mapping[str, Any]) -> set[str]:
    """Property names (``properties`` and ``patternProperties`` keys) of every nested schema."""
    names: set[str] = set()
    for node in schema_nodes(schema):
        names.update(node.get("properties", {}))
    return names


def all_strings(node: Any) -> Iterator[str]:
    """Every string value and every object key in a JSON document (including enum members)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from all_strings(item)


def pattern_values(schema: Mapping[str, Any]) -> list[str]:
    values = [n["pattern"] for n in schema_nodes(schema) if isinstance(n.get("pattern"), str)]
    for node in schema_nodes(schema):
        values.extend(node.get("patternProperties", {}))
    return values


def refs_of(schema: Mapping[str, Any]) -> list[str]:
    return [n["$ref"] for n in schema_nodes(schema) if isinstance(n.get("$ref"), str)]


def find_definition(name: str) -> tuple[str, dict[str, Any]] | None:
    """An entity name finds its own file; any other name finds the first exported file (in file
    name order) whose ``$defs`` holds it. ``None`` when no file defines ``name``."""
    files = rendered_files()
    own = name + SCHEMA_SUFFIX
    if own in files:
        return own, json.loads(files[own])
    for file_name, data in sorted(files.items()):
        schema = json.loads(data)
        if name in schema.get("$defs", {}):
            return file_name, schema
    return None


def definition_node(schema: Mapping[str, Any], name: str) -> dict[str, Any]:
    """The schema object of ``name``: the root of an entity file, else its ``$defs`` entry."""
    if schema.get("title") == name and name not in schema.get("$defs", {}):
        return dict(schema)
    node: dict[str, Any] = schema["$defs"][name]
    return node


def if_then_count(node: Mapping[str, Any]) -> int:
    """Number of ``if``/``then`` rules in ``node['allOf']`` (the form pydantic emits them in)."""
    return sum(1 for sub in node.get("allOf", []) if "if" in sub and "then" in sub)


def resolve(root: Mapping[str, Any], node: Any) -> Any:
    """Follow ``#/$defs/...`` references."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 50:
        seen += 1
        target: Any = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


# ------------------------------------------------------------------------------ validators


def ecma_validator_class() -> Any:
    """``Draft202012Validator`` with ``pattern`` evaluated like ECMA-262 and every non-Python
    JSON Schema validator does: ``$`` at the end of a pattern matches only at the end of the
    text, never before a trailing newline (Python's ``re`` and so ``jsonschema`` do the latter;
    S-N90). Anchored patterns are checked by ``test_patterns_are_anchored_and_portable``."""
    jsonschema = b2.jsonschema_module()
    validators = jsonschema.validators

    def pattern(validator: Any, value: str, instance: Any, schema: Any) -> Iterator[Any]:
        if not validator.is_type(instance, "string"):
            return
        text = value[:-1] + r"\Z" if value.endswith("$") and not value.endswith(r"\$") else value
        if re.search(text, instance) is None:
            yield jsonschema.ValidationError(f"{instance!r} does not match {value!r}")

    cls: Any = validators.extend(jsonschema.Draft202012Validator, {"pattern": pattern})
    return cls


def schema_validator(schema: Mapping[str, Any]) -> Any:
    return ecma_validator_class()(dict(schema))


def definition_validator(schema: Mapping[str, Any], name: str) -> Any:
    """A validator for ``$defs/name`` of an exported file, with the file's definitions around it."""
    wrapper = {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
    return ecma_validator_class()(wrapper)


# ---------------------------------------------------------------------------- sample documents

_B2_NAMES = frozenset(
    {
        "Project",
        "AgentTarget",
        "Persona",
        "TestProfile",
        "MetricDefinition",
        "MetricBinding",
        "Rubric",
        "Evaluator",
    }
)
_B3_NAMES = frozenset({spec[1] for spec in b3.ENTITY_SPECS3})
_B4_NAMES = frozenset({spec[1] for spec in b4.ENTITY_SPECS4})
_BUNDLE_NAMES = frozenset({"RunBundle", "ExportBundle"})

SampleBuilder = Callable[[], dict[str, Any]]


def _b2(name: str, **over: Any) -> SampleBuilder:
    return lambda: b2.build(name, **over)


def _b3(name: str, **over: Any) -> SampleBuilder:
    def build() -> dict[str, Any]:
        model = b3.entity_class3(name)
        doc = b3.doc3(name, **over)
        return b2.seal(model, doc) if name in b3.VERSIONED_NAMES3 else doc

    return build


def _b4(
    name: str, doc_fn: Callable[..., dict[str, Any]] | None = None, **over: Any
) -> SampleBuilder:
    def build() -> dict[str, Any]:
        model = b4.entity_class4(name)
        doc = doc_fn(**over) if doc_fn is not None else b4.doc4(name, **over)
        return b2.seal(model, doc) if name in b4.VERSIONED_NAMES4 else doc

    return build


def _definition_variants() -> list[tuple[str, SampleBuilder]]:
    rating = b2.judge_definition(
        key="politeness",
        value_type="rating",
        scale={"min": 1, "max": 5},
        pass_rule={"kind": "range", "min": 4, "inclusive": True},
    )
    enum = b2.judge_definition(
        key="outcome_class",
        value_type="enum",
        scale={"options": ["resolved", "escalated", "abandoned"]},
        pass_rule=b2.one_of_rule(["resolved"]),
    )
    share = b2.judge_definition(
        key="polite_turn_share",
        aggregation={"kind": "per_turn_share", "turn_rule": b2.equals_true_rule()},
        pass_rule={"kind": "range", "min": 0.8, "inclusive": True},
    )
    return [
        ("numeric", _b2("MetricDefinition")),
        ("judge_boolean", _b2("MetricDefinition", **b2.judge_definition())),
        ("judge_rating", _b2("MetricDefinition", **rating)),
        ("judge_enum", _b2("MetricDefinition", **enum)),
        ("judge_turn_share", _b2("MetricDefinition", **share)),
    ]


def _result_variants() -> list[tuple[str, SampleBuilder]]:
    na_base: dict[str, Any] = {
        "status": "not_applicable",
        "value": None,
        "passed": None,
        "judge": None,
        "language_source": None,
    }
    return [
        ("scored", _b3("MetricResult")),
        (
            "na_language_not_observed",
            _b3("MetricResult", **na_base, na_reason="language_not_observed"),
        ),
        (
            "na_missing_input",
            _b3(
                "MetricResult",
                **na_base,
                na_reason="missing_input",
                missing_kinds=["reference_text"],
            ),
        ),
        (
            "error",
            _b3(
                "MetricResult",
                status="error",
                value=None,
                passed=None,
                judge=None,
                language_source="observed",
                error={
                    "code": "judge_call_failed",
                    "message": "the judge provider returned HTTP 503",
                },
            ),
        ),
    ]


def _cost_variants() -> list[tuple[str, SampleBuilder]]:
    variants: list[tuple[str, SampleBuilder]] = [
        ("reported", _b4("CostRecord")),
        ("computed", _b4("CostRecord", b4.record_computed_doc)),
    ]
    for reason in sorted(set(b2.property_enum(b4.entity_class4("CostRecord"), "na_reason"))):
        variants.append((f"na_{reason}", _b4("CostRecord", lambda r=reason: b4.record_na_doc(r))))
    return variants


def sample_variants() -> dict[str, list[tuple[str, SampleBuilder]]]:
    """Entity class name -> labelled builders of valid JSON-mode documents, one per branch of a
    conditional rule that the examples can reach. The bundles come from the batch 7 world."""
    variants: dict[str, list[tuple[str, SampleBuilder]]] = {
        "Project": [("base", _b2("Project"))],
        "AgentTarget": [("base", _b2("AgentTarget"))],
        "Persona": [("base", _b2("Persona"))],
        "TestProfile": [("base", _b2("TestProfile"))],
        "MetricDefinition": _definition_variants(),
        "MetricBinding": [("base", _b2("MetricBinding"))],
        "Rubric": [("base", _b2("Rubric"))],
        "Evaluator": [("base", _b2("Evaluator"))],
        "Artifact": [
            ("human", _b3("Artifact")),
            ("synthetic", _b3("Artifact", privacy=b3.privacy_synthetic())),
        ],
        "Transcript": [
            ("our_stt", _b3("Transcript")),
            (
                "declared",
                _b3(
                    "Transcript", source="agent_export", declared_language=b3.language_declaration()
                ),
            ),
        ],
        "Turn": [
            ("untimed", _b3("Turn")),
            (
                "timed",
                _b3("Turn", timing_source="audio_alignment", start_ms=1000, end_ms=2000),
            ),
        ],
        "Exchange": [("base", _b3("Exchange"))],
        "ToolCallEvent": [
            ("complete", _b3("ToolCallEvent")),
            (
                "truncated_arguments",
                _b3(
                    "ToolCallEvent",
                    arguments_complete=False,
                    arguments=None,
                    arguments_raw='{"city": "Pu',
                ),
            ),
        ],
        "StageSpan": [
            ("stt", _b3("StageSpan")),
            ("other", _b3("StageSpan", stage="other", stage_raw="vad")),
        ],
        "MetricResult": _result_variants(),
        "RunVerdict": [("base", _b3("RunVerdict"))],
        "ConfigSnapshot": [("base", _b3("ConfigSnapshot"))],
        "Run": [
            ("declared_map", _b3("Run")),
            (
                "inferred_map",
                _b3("Run", channel_maps=[b3.inferred_map_doc()]),
            ),
        ],
        "Dataset": [("base", _b3("Dataset"))],
        "DatasetItem": [("base", _b3("DatasetItem"))],
        "UsageRecord": [
            ("tokens", _b4("UsageRecord")),
            (
                "seconds",
                _b4(
                    "UsageRecord",
                    stage="stt",
                    provider="sarvam",
                    model="saaras:v4",
                    quantities=[b4.qty("audio_input_seconds", "12.5")],
                ),
            ),
        ],
        "PricingCatalog": [
            ("catalog_entry", _b4("PricingCatalog")),
            (
                "conditional_user_entry",
                _b4(
                    "PricingCatalog",
                    lambda: b4.catalog_doc(
                        [
                            b4.entry_doc(
                                price_source="user_supplied",
                                applies_when=[
                                    {"kind": "context_tokens_gt", "value": 1000},
                                    {"kind": "region", "value": "us-east-1"},
                                    {"kind": "batch", "value": True},
                                ],
                            )
                        ]
                    ),
                ),
            ),
        ],
        "CostRecord": _cost_variants(),
        "CostSummary": [("base", _b4("CostSummary"))],
        "RunBundle": [
            ("t1_human", lambda: _b7().run_bundle_doc()),
            ("t0_simulated", lambda: _b7().run_bundle_doc(tier="T0", origin="simulated")),
        ],
        "ExportBundle": [
            ("internal", lambda: _b7().export_doc("internal")),
            ("shared", lambda: _b7().shared_ready("shared_export")),
        ],
    }
    return variants


def _b7() -> Any:
    from . import _support_b7

    return _support_b7


@functools.cache
def sample_document(name: str, label: str) -> dict[str, Any]:
    """The cached valid document of one variant (random IDs are drawn once per session)."""
    for variant_label, builder in sample_variants()[name]:
        if variant_label == label:
            return builder()
    raise KeyError((name, label))


def variant_ids() -> list[tuple[str, str]]:
    """(entity, label) of every variant, for parametrization. Entities of the registry without a
    variant are listed with the label ``"missing"`` so that a new entity cannot go untested."""
    names = sample_variants()
    pairs: list[tuple[str, str]] = []
    for entity in entity_class_names_static():
        pairs.extend((entity, label) for label, _ in names.get(entity, [("missing", lambda: {})]))
    return pairs


def entity_class_names_static() -> list[str]:
    """Registered entity names (the registry walk) merged with the bundle names, so that the
    bundles are parametrized (and red) while ``tracer_evalschema.bundle`` is not written. The
    union never hides an entity: a registry entity without variants shows up as ``missing``."""
    try:
        names = set(entity_class_names())
    except Exception:
        names = set()
    return sorted(names | _BUNDLE_NAMES)


def is_versioned(model: Any) -> bool:
    return bool(issubclass(model, api("base").VersionedEntity))


def model_accepts(model: Any, doc: Any) -> bool:
    """Whether the model validates ``doc``; content-digest checks are off (a mutation changes the
    digest domain, so every mutation of a versioned document would fail for that reason alone)."""
    errors = api("errors")
    try:
        with b2.digest_check_off():
            model.model_validate(doc)
    except (ValidationError, errors.TracerSchemaError, ValueError):
        return False
    return True


def dump_json_mode(model: Any, doc: Mapping[str, Any]) -> dict[str, Any]:
    """The model's own JSON-mode dump of ``doc`` (nulls kept: the schema must accept them)."""
    loaded = model.model_validate(doc)
    dumped: dict[str, Any] = json.loads(loaded.model_dump_json(by_alias=True))
    return dumped


# --------------------------------------------------------------------------------- mutations


def _paths(node: Any, prefix: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    yield prefix, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _paths(value, (*prefix, index))


def _with(doc: Any, path: tuple[Any, ...], value: Any, *, delete: bool = False) -> Any:
    copied = copy.deepcopy(doc)
    cursor = copied
    for part in path[:-1]:
        cursor = cursor[part]
    if delete:
        del cursor[path[-1]]
    else:
        cursor[path[-1]] = value
    return copied


def path_text(path: tuple[Any, ...]) -> str:
    return ".".join("*" if isinstance(part, int) else str(part) for part in path)


NEAR_MISSES: tuple[tuple[str, Callable[[str], str | None]], ...] = (
    ("trailing_newline", lambda s: s + "\n"),
    ("leading_space", lambda s: " " + s),
    ("swapcase", lambda s: s.swapcase() if s.swapcase() != s else None),
    ("truncated", lambda s: s[:-1] if len(s) > 1 else None),
    ("extended", lambda s: s + "x"),
)
# JSON values of other types than the leaf's own.
TYPE_SWAPS: Sequence[tuple[str, Any]] = (("int", 7), ("object", {}))


MAX_PER_SHAPE = 2  # occurrences of one kind at one shape path (list indexes folded) to mutate


def mutations(doc: Any) -> Iterator[tuple[str, tuple[Any, ...], Any]]:
    """(kind, path, mutated document). Every mutation changes one place of a valid document: a
    member removed, set to null or to another JSON type, an unknown member, a string that is not a
    member of its set or a near miss of its value, an integer out of range or not integral, a list
    emptied or given a repeat. Deterministic: same document, same sequence. Because a signature
    names the shape of the path (list indexes are ``*``), a kind is applied at most
    ``MAX_PER_SHAPE`` times at one shape: the first elements of a list stand for all of them,
    which keeps the bundle documents (thousands of leaves) affordable."""
    seen: collections.Counter[tuple[str, str]] = collections.Counter()

    def take(kind: str, path: tuple[Any, ...]) -> bool:
        key = (kind, path_text(path))
        if seen[key] >= MAX_PER_SHAPE:
            return False
        seen[key] += 1
        return True

    for path, node in _paths(doc):
        if not path:
            if take("unknown", path):
                yield "unknown", path, {**doc, "zz_unknown": 1}
            continue
        parent = doc
        for part in path[:-1]:
            parent = parent[part]
        if isinstance(parent, dict) and take("remove", path):
            yield "remove", path, _with(doc, path, None, delete=True)
        if node is not None and take("to_null", path):
            yield "to_null", path, _with(doc, path, None)
        if isinstance(node, dict):
            if take("unknown", path):
                yield "unknown", path, _with(doc, (*path, "zz_unknown"), 1)
            if take("to_list", path):
                yield "to_list", path, _with(doc, path, [])
        elif isinstance(node, list):
            if take("list_empty", path):
                yield "list_empty", path, _with(doc, path, [])
            if node and take("list_repeat", path):
                yield "list_repeat", path, _with(doc, path, [*node, copy.deepcopy(node[0])])
            if take("to_object", path):
                yield "to_object", path, _with(doc, path, {})
        elif isinstance(node, str):
            if take("str_bad", path):
                yield "str_bad", path, _with(doc, path, "zz\nnot a member")
            if take("str_empty", path):
                yield "str_empty", path, _with(doc, path, "")
            for label, fn in NEAR_MISSES:
                near = fn(node)
                if near is not None and take(f"near_{label}", path):
                    yield f"near_{label}", path, _with(doc, path, near)
            for label, value in TYPE_SWAPS:
                if take(f"str_to_{label}", path):
                    yield f"str_to_{label}", path, _with(doc, path, value)
        elif isinstance(node, bool):
            if take("bool_to_str", path):
                yield "bool_to_str", path, _with(doc, path, "true")
            if take("bool_to_int", path):
                yield "bool_to_int", path, _with(doc, path, 1)
        elif isinstance(node, int):
            for kind, value in (
                ("int_negative", -1),
                ("int_too_big", 2**53),
                ("int_fraction", 1.5),
                ("int_integral_float", float(node)),
                ("int_to_str", str(node)),
            ):
                if take(kind, path):
                    yield kind, path, _with(doc, path, value)
        elif isinstance(node, float) and take("num_to_str", path):
            yield "num_to_str", path, _with(doc, path, str(node))


def disagreements(
    model: Any, schema: Mapping[str, Any], doc: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """(looser, stricter) signatures ``kind|Entity|path`` over every mutation of ``doc``. *looser*:
    the model rejects and the schema accepts; *stricter*: the schema rejects and the model
    accepts. A signature is listed once however often it occurs."""
    validator = schema_validator(schema)
    looser: dict[str, None] = {}
    stricter: dict[str, None] = {}
    for kind, path, mutated in mutations(doc):
        ok_model = model_accepts(model, mutated)
        ok_schema = bool(validator.is_valid(mutated))
        if ok_model == ok_schema:
            continue
        signature = f"{kind}|{model.__name__}|{path_text(path)}"
        (stricter if ok_model else looser)[signature] = None
    return list(looser), list(stricter)


@functools.cache
def variant_disagreements(entity: str, label: str) -> tuple[list[str], list[str]]:
    """(looser, stricter) signatures of one sample against its exported file. Cached for the
    session: every test of the batch that needs it (the agreement tests and the register ratchet)
    shares one run, which matters for the bundle documents (thousands of mutations each)."""
    model = entity_class_by_name()[entity]
    return disagreements(model, rendered_schema(entity), sample_document(entity, label))


def sample_or_none(entity: str, label: str) -> dict[str, Any] | None:
    """The sample, or ``None`` when it does not build (it fails its own test, so the tests that
    only need the leaves or the differences leave it out instead of repeating the failure)."""
    try:
        return sample_document(entity, label)
    except Exception:
        return None


def disagreements_or_none(entity: str, label: str) -> tuple[list[str], list[str]] | None:
    try:
        return variant_disagreements(entity, label)
    except Exception:
        return None


def string_leaf_paths(doc: Any) -> list[tuple[Any, ...]]:
    """Paths of every string leaf of a JSON document (cheap: no mutation is built)."""
    return [path for path, node in _paths(doc) if path and isinstance(node, str)]


def replace_at(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    """A deep copy of ``doc`` with ``value`` written at ``path``."""
    return _with(doc, path, value)


# -------------------------------------------------------------------------- subprocess runs


def run_python(code: str, *, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
    """Run ``code`` in a fresh interpreter that sees the same ``sys.path`` as this process and
    return its stdout. ``env`` entries are added to a minimal environment (no secrets, no
    ``PYTHONPATH`` of the caller: the import path is passed explicitly)."""
    program = "import json, sys\nsys.path[:] = json.loads(sys.argv[1])\n" + textwrap.dedent(code)
    base_env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "HOME")
        if key in os.environ
    }
    base_env.update(env or {})
    result = subprocess.run(  # noqa: S603 - fixed interpreter, code under test, no shell
        [sys.executable, "-c", program, json.dumps([p for p in sys.path if p])],
        check=False,
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def run_cli(
    args: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """``python -m tracer_evalschema.schema_export <args>`` with this process's import path."""
    program = (
        "import json, sys\nsys.path[:] = json.loads(sys.argv[1])\n"
        "import runpy\nsys.argv = ['schema_export', *json.loads(sys.argv[2])]\n"
        "runpy.run_module('tracer_evalschema.schema_export', run_name='__main__')\n"
    )
    base_env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "HOME")
        if key in os.environ
    }
    base_env.update(env or {})
    return subprocess.run(  # noqa: S603 - fixed interpreter, code under test, no shell
        [
            sys.executable,
            "-c",
            program,
            json.dumps([p for p in sys.path if p]),
            json.dumps(list(args)),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        timeout=300,
    )


def copy_files(files: Mapping[str, bytes], target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    return target


def read_directory(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


@contextlib.contextmanager
def cwd_of(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ------------------------------------------------------------------------ code-only rules


Claim = tuple[str, str, str]  # (mutation kind, entity, path) globs, matched one by one
Witness = Callable[[], tuple[str, dict[str, Any], bool]]  # (entity, document, digest check on)


@dataclass(frozen=True)
class CodeOnlyRule:
    """A rule that the JSON Schema of an entity cannot state, or that is not stated today.

    ``nature``: ``mutation`` (the differential test finds documents the model rejects and the
    schema accepts; ``claims`` names them), ``cross_record`` / ``cryptographic`` / ``arithmetic``
    (nothing a single-document schema can state), ``validator`` (a property of JSON Schema
    itself). ``emittable`` marks a rule that JSON Schema 2020-12 CAN state and the models do not
    emit yet; each such row carries the recommendation to emit it, and the row disappears when it
    is emitted (the stale-claim test fails until then). ``witness`` builds one document that the
    model rejects and the exported schema accepts. ``docs`` lists (path relative to the
    repository root, anchor text that must occur in it) so a rule cannot lose its documentation
    unnoticed."""

    rule_id: str
    owner: str
    nature: str
    summary: str
    docs: tuple[tuple[str, str], ...]
    claims: tuple[Claim, ...] = ()
    witness: Witness | None = None
    emittable: bool = False
    more_witnesses: tuple[Witness, ...] = ()


NATURES = frozenset({"mutation", "cross_record", "cryptographic", "arithmetic", "validator"})
API = "docs/phase1/api-surface.md"
ADR_0002 = "docs/adr/0002-time-and-units.md"
ADR_0007 = "docs/adr/0007-secrets.md"
ADR_0008 = "docs/adr/0008-schema-versioning-and-compatibility.md"
ADR_0013 = "docs/adr/0013-verdict.md"
ADR_0017 = "docs/adr/0017-bidi-and-display-safe.md"


def _set_at(
    entity: str, label: str, path: tuple[Any, ...], value: Any, *, digest: bool = False
) -> Witness:
    """A witness: the sample ``label`` of ``entity`` with ``value`` written at ``path``."""

    def build() -> tuple[str, dict[str, Any], bool]:
        doc = copy.deepcopy(sample_document(entity, label))
        cursor: Any = doc
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = value
        return entity, doc, digest

    return build


def _claims(kinds: Sequence[str], entity: str, *paths: str) -> tuple[Claim, ...]:
    return tuple((kind, entity, path) for kind in kinds for path in paths)


def _deep(*paths: str) -> tuple[str, ...]:
    """Each path as written and below any container (``*.path``): the same rule of an embedded
    record appears at ``snapshot.evaluator.name`` inside a bundle and at ``evaluator.name`` in a
    snapshot."""
    return tuple(form for path in paths for form in (path, "*." + path))


def _repeat_first_at(entity: str, label: str, path: tuple[Any, ...]) -> Witness:
    """A witness: the sample ``label`` of ``entity`` with the first element of the list at
    ``path`` appended a second time."""

    def build() -> tuple[str, dict[str, Any], bool]:
        doc = copy.deepcopy(sample_document(entity, label))
        cursor: Any = doc
        for part in path[:-1]:
            cursor = cursor[part]
        items = cursor[path[-1]]
        items.append(copy.deepcopy(items[0]))
        return entity, doc, False

    return build


def _snapshot_keyed_repeat(field: str) -> Witness:
    """A witness: a valid ConfigSnapshot whose ``field`` list holds, after element 0, a second
    record with the ``id`` and ``version`` of element 0 but different content (its own valid
    ``content_digest``). The snapshot digest is recomputed, so only the keyed repeat is the reason
    for the model to reject it (A21, A28; same document as
    ``test_schema_gaps._snapshot_with_keyed_repeat(field, 0, 1)``)."""

    def build() -> tuple[str, dict[str, Any], bool]:
        parts = b3.snapshot_parts()
        doc = b3.snapshot_doc(parts)
        if field == "metric_definitions":
            original = parts["definitions"][0]
            changed = b2.judge_definition(prompt_template=b2.JUDGE_TEMPLATE + " repeat")
            other = b2.make("MetricDefinition", **{**changed, "id": str(original.id), "version": 1})
        else:
            original = parts["bindings"][0]
            other = b2.make(
                "MetricBinding",
                id=str(original.id),
                evaluator_id=str(original.evaluator_id),
                metric={"id": str(original.metric.id), "version": original.metric.version},
                enabled=original.enabled,
                params={"note": "repeat"},
            )
        doc[field].insert(1, other.model_dump(mode="json", by_alias=True))
        body = {k: v for k, v in doc.items() if k != "content_digest"}
        digest = api("snapshot").compute_snapshot_digest(body)
        sealed = {**body, "content_digest": digest.model_dump(mode="json", exclude_none=True)}
        return "ConfigSnapshot", sealed, True

    return build


def _unset_at(entity: str, label: str, path: tuple[Any, ...]) -> Witness:
    def build() -> tuple[str, dict[str, Any], bool]:
        doc = copy.deepcopy(sample_document(entity, label))
        cursor: Any = doc
        for part in path[:-1]:
            cursor = cursor[part]
        del cursor[path[-1]]
        return entity, doc, False

    return build


_LABEL_KINDS = ("str_bad", "near_trailing_newline", "near_leading_space")
_ANY_STRING_REWRITE = (
    "str_bad",
    "str_empty",
    "near_trailing_newline",
    "near_leading_space",
    "near_swapcase",
    "near_truncated",
    "near_extended",
)
_BIDI_OVERRIDE = chr(0x202E)

# Properties whose schema carries ``uniqueItems`` (the models reject an exact repeat there and say
# so). Derived from the exported files of revision 23 after A22 (arrays of STRINGS only: a
# validator compares strings cheaply, but compares every pair of objects). The set may only grow
# (``test_unique_items_are_kept_where_the_models_reject_a_repeat``); ``CO-KEYED-UNIQUENESS`` says
# what the keyword cannot state, ``CO-LONG-LIST-UNIQUENESS`` lists the arrays of objects that
# have none on purpose. A property name is a member when ANY entity or value object of the export
# carries the keyword on a property of that name.
UNIQUE_ITEMS_PROPERTIES = frozenset(
    {
        "aliases",  # PriceEntry: model names
        "available_inputs",  # Run: closed-set input kinds
        "covered_by",  # CostExclusion: record ids
        "covers",  # RedactionRecord: closed-set kinds
        "declared_capabilities",  # AgentTarget: closed-set capabilities (new in revision 23)
        "declared_language_transcript_ids",  # MetricResult: transcript ids (new)
        "detected_capabilities",  # Run: closed-set capabilities
        "entry_keys",  # PricingSnapshot: price-entry keys
        "included",  # CostSummary: record ids
        "item_ids",  # Dataset: item ids
        "languages",  # AgentTarget and MetricDefinition: language tags (new)
        "missing_kinds",  # MetricResult: closed-set kinds
        "options",  # EnumScale and OneOfRule: distinct option strings (new)
        "prompt_variables",  # MetricDefinition: distinct variable names (new)
        "requires",  # MetricDefinition: closed-set kinds
        "unpriced_units",  # CostRecord: closed-set units
        "usage_record_ids",  # CostRecord: record ids
    }
)

# A22 (revision 23, security): arrays of objects that grow with a run or an export, where a
# validator that checks ``uniqueItems`` compares every pair (quadratic: 3.63 s against 0.18 s for
# a 4,000-turn bundle with ``jsonschema``). The exported files carry NO ``uniqueItems`` on them;
# the models reject a repeated record in linear time (``CO-LONG-LIST-UNIQUENESS``). Pairs of
# (entity file, property of its top-level object).
A22_NO_UNIQUE_ITEMS: tuple[tuple[str, str], ...] = (
    *(
        ("RunBundle", name)
        for name in (
            "artifacts",
            "transcripts",
            "turns",
            "exchanges",
            "tool_calls",
            "spans",
            "results",
            "usage_records",
            "cost_records",
            "cost_summaries",
            "pricing_catalogs",
            "test_profiles",
            "withheld",
        )
    ),
    *(
        ("ExportBundle", name)
        for name in (
            "run_bundles",
            "datasets",
            "dataset_items",
            "sealed_payloads",
            "provenance_redactions",
        )
    ),
    ("PricingCatalog", "entries"),
)

CODE_ONLY_RULES: tuple[CodeOnlyRule, ...] = (
    CodeOnlyRule(
        "CO-INTEGRAL-FLOAT",
        "schema-architect",
        "validator",
        "JSON Schema 2020-12 counts a number with a zero fraction (1.0) as an integer, so no "
        "keyword of an integer field can reject the float 1.0 that every model rejects. A "
        "consumer must write integers without a fraction; SCHEMA_POLICY.md says so.",
        ((API, "Rejects floats (including `1.0`)"),),
        (("int_integral_float", "*", "*"),),
    ),
    CodeOnlyRule(
        "CO-IJSON-RANGE",
        "schema-architect",
        "mutation",
        "Integers above 2**53 - 1 are rejected by the models where the schema states no maximum: "
        "the `*_ms` base schema is pinned without one (ADR 0002 Decision 2), and the integers "
        "inside a digest domain (ConfigSnapshot, MetricDefinition pass rules and scales, judge "
        "params) must be I-JSON for the RFC 8785 digest.",
        ((ADR_0002, "always carries the `Ms` base schema"), (API, "**Integer bounds.**")),
        (("int_too_big", "*", "*"),),
        _set_at("MetricDefinition", "numeric", ("pass_rule", "max"), 2**53),
    ),
    CodeOnlyRule(
        "CO-LABEL-TEXT",
        "schema-architect",
        "mutation",
        "HumanLabel fields (names, labels, accent) reject control characters, bidi controls, "
        "default-ignorable characters except ZWJ and ZWNJ, and surrounding white space; the "
        "schema states only `type: string` and `minLength`.",
        ((ADR_0017, "**`HumanLabel`** (names, labels, accent, display names) rejects"),),
        _claims(_LABEL_KINDS, "*", *_deep("name", "accent")),
        _set_at("Persona", "base", ("accent",), "Delhi" + _BIDI_OVERRIDE),
    ),
    CodeOnlyRule(
        "CO-KEYED-UNIQUENESS",
        "schema-architect",
        "mutation",
        "Uniqueness by key (A20): one entry per `entry_key`, per provider and model, per unit, "
        "per primary language, per binding, per channel index, per record ID, and a language "
        "list compared by its canonical case. `uniqueItems` states only that two elements are "
        "equal, so a list the model rejects for a keyed repeat (`hi-IN` and `hi-in`) is "
        "accepted by the schema. The exact repeats are emitted (`UNIQUE_ITEMS_PROPERTIES`).",
        (
            (API, "uniqueness by `id` or by primary language"),
            (API, "distinct `applies_when` kinds"),
            (API, "one quantity per unit"),
        ),
        (),
        _set_at("AgentTarget", "base", ("languages",), ["hi-IN", "hi-in"]),
        more_witnesses=(
            # A28 / F5-M12 follow-up: the keyed repeats inside a ConfigSnapshot (same id and
            # version, other content), which no mutation can produce.
            _snapshot_keyed_repeat("metric_definitions"),
            _snapshot_keyed_repeat("metric_bindings"),
        ),
    ),
    CodeOnlyRule(
        "CO-LONG-LIST-UNIQUENESS",
        "schema-architect",
        "mutation",
        "`uniqueItems` is intentionally NOT emitted on the record lists of RunBundle and "
        "ExportBundle (artifacts, transcripts, turns, exchanges, tool_calls, spans, results, "
        "usage_records, cost_records, cost_summaries, pricing_catalogs, test_profiles, withheld, "
        "run_bundles, datasets, dataset_items, sealed_payloads, provenance_redactions) and on "
        "PricingCatalog.entries, nor, as implemented, on any other array of objects "
        "(`A22_NO_UNIQUE_ITEMS`; A22): a validator compares every pair of objects, so the cost "
        "grows with the square of the list length and a hostile bundle can keep a validator "
        "busy; the models reject a repeated record in linear time. A consumer applies the byte "
        "limit first. The keyword stays on arrays of strings (`UNIQUE_ITEMS_PROPERTIES`).",
        (
            (
                API,
                "A22. `uniqueItems` on unbounded arrays of objects costs validators quadratic time",
            ),
            (
                ADR_0008,
                "uniqueness is a code-only rule (register row `CO-LONG-LIST-UNIQUENESS`",
            ),
        ),
        (
            *_claims(
                ("list_repeat",),
                "*Bundle",
                *_deep(
                    "artifacts",
                    "transcripts",
                    "turns",
                    "exchanges",
                    "tool_calls",
                    "spans",
                    "results",
                    "usage_records",
                    "cost_records",
                    "cost_summaries",
                    "pricing_catalogs",
                    "run_bundles",
                    "entries",
                ),
            ),
            ("list_repeat", "PricingCatalog", "entries"),
            # (test_profiles, withheld, datasets, dataset_items, sealed_payloads and
            # provenance_redactions are empty in every sample, so no mutation repeats one: their
            # absence of the keyword is pinned by ``A22_NO_UNIQUE_ITEMS``, not by a claim)
            # the shorter arrays of objects (A22 as implemented: the keyword is for strings only)
            *_claims(
                ("list_repeat",),
                "*",
                *_deep(
                    "metric_definitions",
                    "metric_bindings",
                    "rubric.items",
                    "channel_maps.*.channels",
                    "quantities",
                ),
                *_deep("checks", "applies_when"),
            ),
            ("list_repeat", "Rubric", "items"),
            ("list_repeat", "Project", "provider_configs"),
        ),
        _repeat_first_at("RunBundle", "t1_human", ("turns",)),
        more_witnesses=(
            _repeat_first_at("ExportBundle", "internal", ("run_bundles",)),
            _repeat_first_at("PricingCatalog", "catalog_entry", ("entries",)),
            _repeat_first_at("RunVerdict", "base", ("checks",)),
        ),
    ),
    CodeOnlyRule(
        "CO-PROMPT-TEMPLATE-VARIABLES",
        "schema-architect",
        "mutation",
        "The variables a judge prompt template uses must equal `prompt_variables`.",
        ((API, "template variables equal to `prompt_variables`"),),
        (
            *_claims(
                ("str_bad", "near_swapcase", "near_truncated"), "*", *_deep("prompt_template")
            ),
            *_claims(("list_empty",), "*", *_deep("prompt_variables")),
            *_claims(("near_truncated", "near_extended"), "*", *_deep("prompt_variables.*")),
        ),
        _set_at("MetricDefinition", "judge_boolean", ("prompt_template",), "Judge {{nope}}"),
    ),
    CodeOnlyRule(
        "CO-ONE-OF-SUBSET-OF-SCALE",
        "schema-architect",
        "mutation",
        "The options of a `one_of` pass rule must be a subset of the enum scale.",
        ((API, "`one_of` options a subset of the scale"),),
        (
            *_claims(_ANY_STRING_REWRITE, "MetricDefinition", "pass_rule.options.*"),
            *_claims(("near_truncated", "near_extended"), "MetricDefinition", "scale.options.*"),
        ),
        _set_at(
            "MetricDefinition",
            "judge_enum",
            ("pass_rule", "options"),
            ["resolved", "no_such_option"],
        ),
    ),
    CodeOnlyRule(
        "CO-RANGE-BOUNDS-ORDER",
        "schema-architect",
        "arithmetic",
        "`min <= max` of a range rule and of a rating scale, and a range pass rule whose "
        "bounds lie inside the domain of the unit (ms >= 0) or of the rating scale, compare "
        "numbers of one document.",
        ((API, "`min <= max`"),),
        (
            ("int_negative", "MetricDefinition", "pass_rule.max"),
            ("int_negative", "MetricDefinition", "pass_rule.min"),
            ("int_negative", "MetricDefinition", "scale.max"),
            ("int_negative", "ConfigSnapshot", "*.pass_rule.max"),
        ),
        _set_at(
            "MetricDefinition",
            "numeric",
            ("pass_rule",),
            {"kind": "range", "min": 5, "max": 1, "inclusive": True},
        ),
    ),
    CodeOnlyRule(
        "CO-RUBRIC-ITEM-NEEDS-PASS-RULE",
        "schema-architect",
        "cross_record",
        "A required rubric item needs a metric definition with a pass rule: the rubric and the "
        "definitions of a snapshot are different objects.",
        ((ADR_0013, "Every required metric has a pass rule (ADR 0010)"),),
        _claims(("remove", "to_null"), "*", *_deep("metric_definitions.*.pass_rule")),
    ),
    CodeOnlyRule(
        "CO-BINDING-PARAMS-DECLARED",
        "schema-architect",
        "cross_record",
        "A binding's `params` may set only what its definition's `parameters` declares.",
        ((API, "declares what `MetricBinding.params` may set"),),
        _claims(("unknown",), "*", *_deep("metric_bindings.*.params")),
    ),
    CodeOnlyRule(
        "CO-TOOL-ARGUMENTS-PARSE",
        "schema-architect",
        "mutation",
        "`arguments_complete=true` needs `arguments_raw` to be one whole JSON object: a "
        "property of the string's content that no keyword can state.",
        ((API, "parse failure is the only truncation the schema can detect"),),
        _claims(
            ("str_bad", "str_empty", "near_truncated", "near_extended"),
            "*",
            *_deep("arguments_raw"),
        ),
        _set_at("ToolCallEvent", "complete", ("arguments_raw",), '{"city": "Pu'),
    ),
    CodeOnlyRule(
        "CO-STORED-CURRENCY-LABEL",
        "schema-architect",
        "mutation",
        "A stored currency label must agree with its reason after `normalize_currency_label` "
        "(N51): a property of the string's content.",
        ((API, "a stored currency label agrees with its reason"),),
        _claims(
            ("str_bad", "near_truncated", "near_extended"),
            "*",
            *_deep("reported_raw.currency_raw"),
        ),
        _set_at("CostRecord", "na_currency_conflict", ("reported_raw", "currency_raw"), "EUR"),
    ),
    CodeOnlyRule(
        "CO-URL-RULES",
        "security-licensing",
        "mutation",
        "URL credential parameter names and credential patterns anywhere in a URL, loopback "
        "hosts, fragments and percent-encoding rules (ADR 0007 Decisions 6, 6a, 6b); the "
        "schema states only the character set.",
        ((ADR_0007, "Both are code-only rules"), (API, "Both rules are code-only (19.6 N12)")),
        (),
        _set_at(
            "Artifact", "human", ("uri",), "https://recordings.example.com/calls/a.wav?token=abc123"
        ),
    ),
    CodeOnlyRule(
        "CO-CONFIG-REF-CREDENTIALS",
        "security-licensing",
        "mutation",
        "`config_ref` must name a secret, never hold one: the known credential patterns are a "
        "content check.",
        ((API, "`config_ref` credential patterns"),),
        (),
        _set_at(
            "Project",
            "base",
            ("provider_configs", 0, "config_ref"),
            "env:sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        ),
    ),
    CodeOnlyRule(
        "CO-PROFILE-PLACEHOLDERS",
        "schema-architect",
        "mutation",
        "A profile placeholder in free text needs a test profile on the evaluator, and its field "
        "name must be a profile field.",
        ((API, "A profile placeholder that has no profile"), (API, "placeholder field names")),
        (),
        _set_at("Evaluator", "base", ("instructions",), "Greet {{profile.name}} warmly."),
    ),
    CodeOnlyRule(
        "CO-DIGEST-VERIFICATION",
        "schema-architect",
        "cryptographic",
        "`content_digest` is the SHA-256 or HMAC of the RFC 8785 form of the rest of the "
        "document; a schema cannot compute it.",
        ((API, "snapshot reference resolution and digest verification"),),
        (),
        _set_at("Persona", "base", ("accent",), "Mumbai", digest=True),
    ),
    CodeOnlyRule(
        "CO-TURN-TIMING-ORDER",
        "schema-architect",
        "arithmetic",
        "`end_ms >= start_ms` and the other Turn timing rules compare two numbers.",
        ((API, "the Turn timing rules"),),
        (),
        _set_at("Turn", "timed", ("end_ms",), 500),
    ),
    CodeOnlyRule(
        "CO-VERDICT-COUNTS",
        "schema-architect",
        "arithmetic",
        "RunVerdict counts add up to `required_total` and to the number of checks, and the "
        "status follows from them.",
        ((API, "`RunVerdict` count and status rules"),),
        _claims(("list_empty",), "RunVerdict", "checks"),
        _set_at("RunVerdict", "base", ("required_total",), 2),
    ),
    CodeOnlyRule(
        "CO-PRICE-ARITHMETIC",
        "schema-architect",
        "arithmetic",
        "`effective_to > effective_from`, non-negative `price`, `minimum`, `amount` and `total` "
        "(canonical decimals may be negative), unique `entry_key` and range overlaps.",
        ((API, "non-negative `price`"), (API, "unique `entry_key` and range overlaps")),
        (),
        _set_at(
            "PricingCatalog",
            "catalog_entry",
            ("entries", 0, "price"),
            {"amount": "-1", "currency": "USD"},
        ),
    ),
    CodeOnlyRule(
        "CO-PRICE-NOTE-NOT-BLANK",
        "security-licensing",
        "mutation",
        "A present provenance `note` is not white space only (N66, N71): `minLength` cannot "
        "express Unicode White_Space.",
        ((API, "a blank note"),),
        (),
        _set_at(
            "PricingCatalog",
            "conditional_user_entry",
            ("entries", 0, "provenance", "note"),
            " \t ",
        ),
    ),
    CodeOnlyRule(
        "CO-BUNDLE-CROSS-RECORD",
        "schema-architect",
        "cross_record",
        "RunBundle and ExportBundle rules that read several records: the records a run's "
        "`available_inputs` and tier promise must be present (artifacts, transcripts, turns, "
        "exchanges, tool calls, spans, results, usage and cost records, channel maps), a result's "
        "value, judge, scorer, language source and pass-rule consistency against its definition, "
        "verdict weights against rubric weights, price entry keys against the catalog, and "
        "amounts against the usage; the export's own lists (ADR 0008 Decision 3, ADR 0016). "
        "Section 25 of api-surface.md puts everything beyond field types and closed sets here.",
        ((API, "`KNOWN_SCHEMA_GAPS`-style entries (N12, N38)"),),
        (
            *_claims(
                ("remove", "list_empty"),
                "*Bundle",
                *_deep(
                    "artifacts",
                    "transcripts",
                    "turns",
                    "exchanges",
                    "tool_calls",
                    "spans",
                    "results",
                    "usage_records",
                    "cost_records",
                    "run.channel_maps",
                    "run_bundles",
                    "payload_redactions",
                ),
            ),
            *_claims(
                ("remove", "to_null"),
                "*Bundle",
                *_deep(
                    "artifacts.*.audio_format",
                    "results.*.passed",
                    "results.*.judge",
                    "results.*.language_source",
                    "results.*.scorer",
                    "verdict.weighted_score",
                    "verdict.weight_coverage",
                    "rubric.items.*.weight",
                ),
            ),
            *_claims(
                ("bool_to_str", "bool_to_int", "int_to_str"),
                "*Bundle",
                *_deep("results.*.value"),
            ),
            *_claims(("list_empty",), "*Bundle", *_deep("verdict.checks")),
            *_claims(
                ("near_swapcase", "near_truncated", "near_extended"),
                "*Bundle",
                *_deep(
                    "pricing_catalogs.*.entries.*.entry_key",
                    "pricing_snapshot.entry_keys.*",
                    "scorer.implementation_ref",
                ),
            ),
            *_claims(
                ("near_truncated", "near_extended"),
                "*Bundle",
                *_deep("metric_definitions.*.implementation_ref"),
            ),
            *_claims(("near_truncated",), "*Bundle", *_deep("amount.amount", "total.amount")),
            *_claims(
                ("int_negative", "int_fraction"),
                "*Bundle",
                *_deep("metric_definitions.*.pass_rule.max"),
            ),
        ),
    ),
    CodeOnlyRule(
        "CO-SUMMARY-CONSISTENCY",
        "schema-architect",
        "cross_record",
        "`covered_by` within `included`, `complete` versus `partial`, and the other CostSummary "
        "rules that read several records.",
        ((API, "`covered_by` within `included`"),),
    ),
    CodeOnlyRule(
        "CO-CROSS-RECORD-CHECKS",
        "schema-architect",
        "cross_record",
        "The `check_*` functions (transcript, verdict, observed tier, human-subject marker, "
        "cost summary, bundle consistency), contiguous indexes and ranks, and reference "
        "resolution read more than one document.",
        (
            (API, "the cross-record rules of the `check_*` functions"),
            (API, "contiguous indexes and ranks"),
            (API, "`KNOWN_SCHEMA_GAPS`-style entries (N12, N38)"),
        ),
    ),
    CodeOnlyRule(
        "CO-CHANNEL-AGENT-COUNT",
        "schema-architect",
        "arithmetic",
        "A channel map names at most one agent channel and every channel index is below the "
        "audio's channel count (the confidence-by-determination rule is emitted).",
        ((API, "agent-count rules"),),
    ),
    CodeOnlyRule(
        "CO-PER-TURN-SUMMARY",
        "schema-architect",
        "arithmetic",
        "`PerTurnSummary` shares, counted turns and total turns follow from the counts.",
        ((API, "`PerTurnSummary` consistency"),),
    ),
    CodeOnlyRule(
        "CO-PYTHON-DOLLAR-NEWLINE",
        "qa",
        "validator",
        "Python's `re`, and so the `jsonschema` package, lets `$` match before a trailing "
        "newline, so a Python validator accepts `abc\n` for `^[a-z]+$`; ECMA-262 validators "
        "reject it. The models reject it everywhere. The agreement tests evaluate patterns with "
        "ECMA semantics (S-N90).",
        ((API, "S-N90"),),
    ),
)


def doc_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def doc_files_mentioning_code_only() -> list[str]:
    """ADRs and api-surface files whose text says ``code-only`` (any case)."""
    found: list[str] = []
    for pattern in ("docs/adr/*.md", "docs/phase1/api-surface*.md"):
        for path in sorted(ROOT.glob(pattern)):
            if re.search(r"code-only", path.read_text(encoding="utf-8"), re.IGNORECASE):
                found.append(str(path.relative_to(ROOT)))
    return found


def claims_match(claim: str, signature: str) -> bool:
    return fnmatch.fnmatchcase(signature, claim)


__all__ = [
    "API",
    "CODE_ONLY_RULES",
    "CURRENT_SCHEMA_VERSION",
    "DIALECT",
    "LOCK_NAME",
    "SCHEMA_SUFFIX",
    "SHIPPED_DIR",
    "CodeOnlyRule",
    "canonical_dumps",
    "export_module",
    "mutations",
    "rendered_files",
    "sample_variants",
]
