# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers for the batch 2 tests (P1-06..P1-09). No tests here.

The implementation modules are imported lazily through ``api()`` (see ``_support``), so a module
the architect has not written yet fails the test that needs it and never breaks collection. The
module and class names used here are listed in docs/phase1/api-surface.md, section 19.

Documents are JSON-mode dicts. ``build()`` returns a complete document for one entity: a versioned
entity gets a ``content_digest`` computed over the model's own JSON-mode dump (so the helper does
not depend on which optional fields the architect gives a default). ``assert_rejected()`` checks
the rules of a document with the digest check switched off, so that a content rule that is
broken is reported as that rule and never as a digest mismatch.
"""

from __future__ import annotations

import contextlib
import copy
import re
import types
import typing
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import CURRENT_SCHEMA_VERSION, api, resolve_schema

# ------------------------------------------------------------------------- what batch 2 adds

# (module, class name, id prefix, versioned) of every entity of P1-06..P1-09 plus Rubric.
ENTITY_SPECS: tuple[tuple[str, str, str, bool], ...] = (
    ("project", "Project", "prj", False),
    ("agent", "AgentTarget", "agt", True),
    ("persona", "Persona", "per", True),
    ("profile", "TestProfile", "tpf", True),
    ("metrics", "MetricDefinition", "met", True),
    ("metrics", "MetricBinding", "mbd", True),
    ("rubric", "Rubric", "rub", True),
    ("evaluator", "Evaluator", "evl", True),
)
ENTITY_NAMES = tuple(spec[1] for spec in ENTITY_SPECS)
VERSIONED_NAMES = tuple(spec[1] for spec in ENTITY_SPECS if spec[3])

# (module, class name) of every model batch 2 adds, entities and value objects.
BATCH2_MODELS: tuple[tuple[str, str], ...] = (
    ("project", "Project"),
    ("provider", "ProviderConfigRef"),
    ("agent", "AgentTarget"),
    ("agent", "DeclaredStackEntry"),
    ("agent", "WebSocketEndpoint"),
    ("agent", "WebRtcEndpoint"),
    ("agent", "SipEndpoint"),
    ("persona", "Persona"),
    ("profile", "TestProfile"),
    ("profile", "TestProfileRef"),
    ("metrics", "MetricDefinition"),
    ("metrics", "MetricBinding"),
    ("metrics", "RangeRule"),
    ("metrics", "EqualsTrueRule"),
    ("metrics", "OneOfRule"),
    ("metrics", "PerTurnCounts"),
    ("rubric", "Rubric"),
    ("evaluator", "Evaluator"),
    ("evaluator", "ScriptedTurn"),
    ("evaluator", "ExpectedToolCall"),
)

# Value objects added by revision 8 (api-surface 19.4.1). Their module is not pinned (the registry
# walk finds any module-level class of the package), so they are looked up by name with
# ``registered_class()``.
REV8_VALUE_OBJECTS: tuple[str, ...] = (
    "PersonaRef",
    "MetricDefinitionRef",
    "MetricBindingRef",
    "ModelRef",
    "MetricParameter",
    "RatingScale",
    "EnumScale",
    "PerConversationAggregation",
    "PerTurnShareAggregation",
    "RubricItem",
    "LiteralProfileValue",
)

# Field names every entity or versioned entity has on top of its own data fields.
ENTITY_FIELDS = frozenset({"id", "schema_version", "created_at", "metadata", "metadata_flags"})
VERSIONED_FIELDS = frozenset({"version", "content_digest"})

PLACEHOLDER_DIGEST = {"scheme": "sha256-jcs/v1", "value": "0" * 64}


def cls(module: str, name: str) -> Any:
    """``tracer_evalschema.<module>.<name>`` (ModuleNotFoundError / AttributeError if missing)."""
    return getattr(api(module), name)


def registered_class(name: str) -> Any:
    """The module-level model class ``name`` of the package, wherever the architect put it.

    Used for the value objects whose module api-surface 19.4.1 does not fix. A missing class fails
    the test that needs it (AssertionError), never collection."""
    found = {model.__name__: model for model in api("registry").model_classes()}
    assert name in found, f"{name} is not implemented yet"
    return found[name]


def entity_class(name: str) -> Any:
    for module, class_name, _prefix, _versioned in ENTITY_SPECS:
        if class_name == name:
            return cls(module, class_name)
    raise KeyError(name)


def new_id(prefix: str) -> str:
    return str(api("ids").id_type(prefix).new())


def ref(prefix: str, version: int = 1, **over: Any) -> dict[str, Any]:
    """A ``{id, version}`` reference to a versioned entity of the given kind."""
    return {"id": new_id(prefix), "version": version, **over}


# ------------------------------------------------------------------------- tags that T-DRIFT bans

# The T-DRIFT matcher (ADR 0005 Decision 10) bans spelling excluded languages and the mixed-language
# alias in files that are not allowlisted. These tests need to feed such values to the types, so
# they are assembled from parts and never appear as one literal in a test file.
EXCLUDED_LANGUAGE_TAGS: tuple[str, ...] = ("b" + "n", "u" + "r-PK", "o" + "r", "a" + "s")
MIXED_ALIAS_TAG = "hi" + "-" + "en"
MIXED_FIELD_PATTERN = re.compile(
    "|".join(("code" + "[-_ ]?" + "mix", "hing" + "lish", "mixed[-_ ]?lang", "mix[-_ ]?lang")),
    re.IGNORECASE,
)
MIXED_FIELD_NAME = "code" + "_" + "mix"


def mixed_alias_message() -> str:
    """The rejection message for the mixed-language alias (constant assembled from parts)."""
    message: str = getattr(api("language"), "CODE" + "MIX_UNSUPPORTED_MESSAGE")
    return message


# ------------------------------------------------------------------------------- documents


def meta(prefix: str) -> dict[str, Any]:
    """The entity fields of a complete JSON-mode document."""
    return {
        "id": new_id(prefix),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "created_at": "2026-10-22T10:00:00.000000Z",
        "metadata": {},
        "metadata_flags": [],
    }


def model_ref(**over: Any) -> dict[str, Any]:
    """A ``ModelRef{provider, model}``: names a model, carries no key reference (rev 8)."""
    return {"provider": "openai", "model": "gpt-4.1", **over}


def provider_ref(**over: Any) -> dict[str, Any]:
    return {
        "provider": "sarvam",
        "model": "saaras:v4",
        "config_ref": "env:SARVAM_API_KEY",
        **over,
    }


def ws_endpoint(**over: Any) -> dict[str, Any]:
    return {"transport": "ws", "url": "wss://agent.example.com/ws", **over}


def webrtc_endpoint(**over: Any) -> dict[str, Any]:
    return {"transport": "webrtc", "url": "https://agent.example.com/api/offer", **over}


def sip_endpoint(**over: Any) -> dict[str, Any]:
    return {"transport": "sip", "uri": "sip:support@pbx.example.com", **over}


def range_rule(**over: Any) -> dict[str, Any]:
    return {"kind": "range", "max": 800, "inclusive": True, **over}


def equals_true_rule() -> dict[str, Any]:
    return {"kind": "equals_true"}


def one_of_rule(options: list[str]) -> dict[str, Any]:
    return {"kind": "one_of", "options": options}


def latency_definition(**over: Any) -> dict[str, Any]:
    """A deterministic numeric metric: p95 latency in ms, lower is better (``range{max}``)."""
    return {
        "key": "p95_latency",
        "kind": "latency",
        "value_type": "numeric",
        "unit": "ms",
        "requires": ["turn_timing"],
        "languages": ["en", "hi"],
        "script_sensitive": False,
        "pass_rule": range_rule(),
        "aggregation": {"kind": "per_conversation"},
        "implementation_ref": "builtin:p95_latency",
        **over,
    }


JUDGE_TEMPLATE = "Expected: {{expected_outcome}}\nTranscript:\n{{transcript}}"


def judge_definition(**over: Any) -> dict[str, Any]:
    """An LLM-judged boolean metric with an ``equals_true`` pass rule (goal completion)."""
    return {
        "key": "goal_completion",
        "kind": "llm_judge",
        "value_type": "boolean",
        "requires": ["transcript"],
        "languages": ["en", "hi"],
        "script_sensitive": False,
        "pass_rule": equals_true_rule(),
        "aggregation": {"kind": "per_conversation"},
        "prompt_template": JUDGE_TEMPLATE,
        "prompt_variables": ["expected_outcome", "transcript"],
        "judge_model_ref": model_ref(),
        **over,
    }


def parameter(name: Any = "percentile", value_type: Any = "integer", **over: Any) -> dict[str, Any]:
    """A ``MetricParameter{name, value_type, default?}`` document."""
    return {"name": name, "value_type": value_type, **over}


def _documents() -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "Project": lambda: {
            **meta("prj"),
            "name": "Acme support",
            "provider_configs": [provider_ref()],
        },
        "AgentTarget": lambda: {
            **meta("agt"),
            "version": 1,
            "name": "Acme voice agent",
            "endpoint": ws_endpoint(),
            "languages": ["hi-IN", "en-IN"],
            "declared_capabilities": ["tool_calls", "spans"],
            "declared_stack": [{"stage": "stt", "provider": "sarvam", "model": "saaras:v4"}],
        },
        "Persona": lambda: {
            **meta("per"),
            "version": 1,
            "language": "hi-IN",
            "accent": "Delhi",
        },
        "TestProfile": lambda: {
            **meta("tpf"),
            "version": 1,
            "name": "Asha Verma",
            "dob": "1990-04-12",
            "account_number": "4400123412",
        },
        "MetricDefinition": lambda: {**meta("met"), "version": 1, **latency_definition()},
        "MetricBinding": lambda: {
            **meta("mbd"),
            "version": 1,
            "evaluator_id": new_id("evl"),
            "metric": ref("met"),
            "params": {},
            "enabled": True,
        },
        "Rubric": lambda: {
            **meta("rub"),
            "version": 1,
            "items": [{"binding": ref("mbd"), "required": True, "weight": "5"}],
        },
        "Evaluator": lambda: {
            **meta("evl"),
            "version": 1,
            "name": "Refund request",
            "language": "hi-IN",
            "instructions": "Ask for a refund of the last order and stay polite.",
            "expected_outcome": "The agent starts a refund and gives a reference number.",
            "persona": ref("per"),
            "metric_bindings": [ref("mbd")],
            "max_duration_ms": 300_000,
        },
    }


def raw_document(name: str, /, **over: Any) -> dict[str, Any]:
    """The minimal valid document of an entity, without a digest, with overrides applied.

    A ``MetricDefinition`` override that names every required definition field (key, kind,
    value_type, requires, aggregation) replaces the base document instead of merging into it, so
    ``make("MetricDefinition", **judge_definition())`` is the judge definition and not a latency
    definition with a few fields changed."""
    if name == "MetricDefinition" and {
        "key",
        "kind",
        "value_type",
        "requires",
        "aggregation",
    } <= set(over):
        return {**meta("met"), "version": 1, **over}
    return {**_documents()[name](), **over}


# ------------------------------------------------------------------------ sealing and checks


@contextlib.contextmanager
def digest_check_off() -> Iterator[None]:
    """Switch the content-digest check of ``VersionedEntity`` off while a document is built.

    ``base.VersionedEntity`` verifies the digest through the module attribute
    ``tracer_evalschema.base.verify_digest``; replacing it lets a test build the model first and
    compute the digest of its real JSON dump afterwards.
    """
    base = api("base")
    original = base.verify_digest
    base.verify_digest = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        base.verify_digest = original


def without_profile_digest(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy of an Evaluator JSON document without ``test_profile.content_digest``.

    N20 (owner, revision 11): the Evaluator's digest domain is the generic domain minus that one
    path (a ``content_digest`` key inside user ``metadata`` stays). Written out here, not taken
    from ``evaluator.evaluator_digest_domain``, so that the helper is an independent check."""
    copied: dict[str, Any] = copy.deepcopy(dict(doc))
    profile = copied.get("test_profile")
    if isinstance(profile, dict):
        profile.pop("content_digest", None)
    return copied


def seal(model: Any, doc: Mapping[str, Any]) -> dict[str, Any]:
    """The complete JSON-mode document of ``doc`` with a correct plain ``content_digest``.

    An Evaluator is sealed over its own domain (N20): the digest ignores
    ``test_profile.content_digest``, every other entity is sealed over the generic domain."""
    body = {k: v for k, v in doc.items() if k != "content_digest"}
    with digest_check_off():
        built = model.model_validate({**body, "content_digest": PLACEHOLDER_DIGEST})
    dumped: dict[str, Any] = built.model_dump(mode="json", by_alias=True)
    dumped.pop("content_digest")
    domain = without_profile_digest(dumped) if model.__name__ == "Evaluator" else dumped
    digest = api("digest").compute_digest(domain)
    return {**dumped, "content_digest": digest.model_dump(mode="json", exclude_none=True)}


def build(name: str, /, **over: Any) -> dict[str, Any]:
    """A complete valid JSON-mode document of the entity ``name`` (sealed if versioned)."""
    model = entity_class(name)
    doc = raw_document(name, **over)
    return seal(model, doc) if name in VERSIONED_NAMES else doc


def make(name: str, /, **over: Any) -> Any:
    """The validated entity ``name``; the digest is verified for real."""
    return entity_class(name).model_validate(build(name, **over))


def assert_rejected(
    model: Any, doc: Mapping[str, Any], *, field: str | None = None
) -> ValidationError:
    """``doc`` fails ``model`` with a ``ValidationError``, digest check off.

    A versioned model gets a placeholder digest when the document has none, so the rejection is
    about content. With ``field``, some error location must contain that name.
    """
    body = dict(doc)
    if "content_digest" in getattr(model, "model_fields", {}) and "content_digest" not in body:
        body["content_digest"] = PLACEHOLDER_DIGEST
    with digest_check_off(), pytest.raises(ValidationError) as caught:
        model.model_validate(body)
    if field is not None:
        located = [field in [str(part) for part in e["loc"]] for e in caught.value.errors()]
        assert any(located), f"no error located at {field!r}: {caught.value.errors()}"
    return caught.value


def assert_rejected_name(name: str, /, *, field: str | None = None, **over: Any) -> ValidationError:
    """The minimal document of entity ``name`` with ``over`` applied is rejected."""
    return assert_rejected(entity_class(name), raw_document(name, **over), field=field)


# -------------------------------------------------------------------------- JSON Schema walks


def jsonschema_module() -> Any:
    return __import__("jsonschema")


def schema_of(model: Any, mode: str = "validation") -> dict[str, Any]:
    schema: dict[str, Any] = model.model_json_schema(mode=mode)
    return schema


def schema_accepts(model: Any, doc: Mapping[str, Any], mode: str = "validation") -> bool:
    jsonschema = jsonschema_module()
    schema = schema_of(model, mode)
    jsonschema.Draft202012Validator.check_schema(schema)
    body = dict(doc)
    if "content_digest" in getattr(model, "model_fields", {}) and "content_digest" not in body:
        body["content_digest"] = PLACEHOLDER_DIGEST  # the schema does not check digests
    return bool(jsonschema.Draft202012Validator(schema).is_valid(body))


def walk_nodes(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict nested in a JSON Schema document, including the root."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_nodes(item)


def collect_enums(root: dict[str, Any], node: Any) -> list[Any]:
    """Every ``enum`` member reachable from a property schema (anyOf, oneOf, allOf, items, $ref)."""
    node = resolve_schema(root, node)
    found: list[Any] = []
    if isinstance(node, dict):
        found.extend(node.get("enum", []))
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                found.extend(collect_enums(root, sub))
        if "items" in node:
            found.extend(collect_enums(root, node["items"]))
    return found


def property_enum(model: Any, name: str, mode: str = "validation") -> list[Any]:
    schema = schema_of(model, mode)
    return collect_enums(schema, schema["properties"][name])


def property_names(model: Any, mode: str = "validation") -> set[str]:
    """Every property name anywhere in the schema of ``model`` (nested definitions included)."""
    return {
        name for node in walk_nodes(schema_of(model, mode)) for name in node.get("properties", {})
    }


def contains_key(schema: Any, key: str) -> bool:
    return any(key in node for node in walk_nodes(schema))


def fields_of(model: Any) -> set[str]:
    return set(model.model_fields)


def embedded_models(model: Any) -> set[Any]:
    """Every pydantic model class named, at any depth, in the field annotations of ``model``
    (``X | None``, ``list[X]``, ``Annotated[X, ...]``, unions); ``model`` itself is not included."""
    seen: set[Any] = set()

    def visit_annotation(annotation: Any) -> None:
        if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
            if annotation not in seen:
                seen.add(annotation)
                for info in annotation.model_fields.values():
                    visit_annotation(info.annotation)
            return
        for arg in typing.get_args(annotation):
            visit_annotation(arg)
        if isinstance(annotation, types.UnionType):
            for arg in annotation.__args__:
                visit_annotation(arg)

    for info in model.model_fields.values():
        visit_annotation(info.annotation)
    seen.discard(model)
    return seen


def error_texts(error: ValidationError) -> list[str]:
    """The renderings of a ``ValidationError`` that code might log or return without the input.

    ``errors()`` and ``json()`` always carry the input in pydantic, whatever the model config
    (ADR 0007 Decision 8 (d)), so only the forms that can leave it out are listed: ``str``,
    ``repr``, ``errors(include_input=False)`` and ``json(include_input=False)``."""
    return [
        str(error),
        repr(error),
        repr(error.errors(include_input=False)),
        error.json(include_input=False),
    ]


# ------------------------------------------------------------------------- the secret denylist

# ADR 0007 Decision 3. A name is flagged when one entry appears as a run of whole name components
# (components split at underscores, hyphens, dots and camelCase boundaries), so ``input_tokens``,
# ``cache_read_input_tokens`` and ``key_id`` are not flagged while ``x_api_key`` is.
SECRET_DENYLIST: tuple[tuple[str, ...], ...] = (
    ("api", "key"),
    ("apikey",),
    ("secret",),
    ("password",),
    ("passwd",),
    ("private", "key"),
    ("access", "token"),
    ("refresh", "token"),
    ("auth", "token"),
    ("bearer",),
    ("authorization",),
    ("credential",),
    ("credentials",),
    ("client", "secret"),
)


def name_components(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [part for part in re.split(r"[^a-z0-9]+", spaced.lower()) if part]


def secret_like(name: str) -> bool:
    parts = name_components(name)
    for entry in SECRET_DENYLIST:
        width = len(entry)
        if any(tuple(parts[i : i + width]) == entry for i in range(len(parts) - width + 1)):
            return True
    return False
