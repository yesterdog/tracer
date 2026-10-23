# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-06: Project, ProviderConfigRef, AgentTarget and the secret rules (ADR 0007, 0009, 0012).

Assumed public API (docs/phase1/api-surface.md section 19): ``tracer_evalschema.project.Project``,
``tracer_evalschema.provider.ProviderConfigRef{provider, model, config_ref}``,
``tracer_evalschema.agent.AgentTarget`` (a versioned entity with ``endpoint`` discriminated by
``transport`` ws | webrtc | sip, ``declared_capabilities``, ``declared_stack``, ``languages``),
``DeclaredStackEntry{stage, provider, model}`` and the three endpoint variants.

Tests named in SCOPE section 5 keep their names; a name marked "Proposed name" in the docstring is
new. Documents are JSON-mode dicts built in ``_support_b2``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    BATCH2_MODELS,
    EXCLUDED_LANGUAGE_TAGS,
    MIXED_ALIAS_TAG,
    PLACEHOLDER_DIGEST,
    assert_rejected,
    assert_rejected_name,
    build,
    cls,
    embedded_models,
    entity_class,
    error_texts,
    fields_of,
    jsonschema_module,
    make,
    mixed_alias_message,
    property_enum,
    property_names,
    provider_ref,
    raw_document,
    schema_accepts,
    schema_of,
    seal,
    secret_like,
    sip_endpoint,
    walk_nodes,
    webrtc_endpoint,
    ws_endpoint,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    marked_id,
    marked_key,
    marked_set,
    marked_tag,
)

# A credential-shaped value, assembled so that this file contains no secret-looking literal.
PASTED_KEY = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz0123456789"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _provider() -> Any:
    return cls("provider", "ProviderConfigRef")


def _walked_models() -> list[Any]:
    """Every model of the package, after checking that each batch 2 model exists."""
    for module, name in BATCH2_MODELS:
        cls(module, name)
    return list(api("registry").model_classes())


def _target(**over: Any) -> dict[str, Any]:
    return raw_document("AgentTarget", **over)


# ------------------------------------------------------------------------------ secret walk


def test_secret_field_walk() -> None:
    """No model field, alias or JSON Schema property name matches the credential denylist.

    The walk includes every batch 2 model (a missing one fails the test); ``metadata`` keys are
    user-owned data and are not property names."""
    models = _walked_models()
    names = {model.__name__ for model in models}
    assert {name for _module, name in BATCH2_MODELS} <= names
    offenders: list[tuple[str, str]] = []
    for model in models:
        for field, info in model.model_fields.items():
            for candidate in (field, info.alias or field):
                if secret_like(candidate):
                    offenders.append((model.__name__, candidate))
        for mode in ("validation", "serialization"):
            offenders.extend(
                (model.__name__, prop) for prop in property_names(model, mode) if secret_like(prop)
            )
    assert not offenders, f"credential-like field names: {sorted(set(offenders))}"


def test_secret_walk_does_not_flag_usage_units() -> None:
    """The denylist matches whole name components, so usage units and key references pass.

    Also runs the walk over the batch 2 models, so the check covers P1-06 and not only the matcher.
    """
    for unit in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "audio_input_seconds",
        "audio_output_seconds",
        "input_characters",
        "token_count",
        "max_tokens",
        "key_id",
        "key_version",
        "config_ref",
        "keyring",
        "monkey",
    ):
        assert not secret_like(unit), unit
    for name in (
        "api_key",
        "apiKey",
        "API_KEY",
        "apikey",
        "x_api_key",
        "client_secret",
        "secret",
        "password",
        "passwd",
        "private_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer",
        "bearer_token",
        "Authorization",
        "credential",
        "credentials",
    ):
        assert secret_like(name), name
    for model in _walked_models():
        flagged = [f for f in model.model_fields if secret_like(f)]
        assert not flagged, (model.__name__, flagged)


# ------------------------------------------------------------------------ ProviderConfigRef


def test_provider_ref_shape_and_open_values() -> None:
    """Proposed name. ``ProviderConfigRef`` is exactly ``{provider, model, config_ref}``, all
    required, no other field, and provider and model are open ``MachineKey`` values."""
    model = _provider()
    good = model.model_validate(provider_ref())
    assert (good.provider, good.model, good.config_ref) == (
        "sarvam",
        "saaras:v4",
        "env:SARVAM_API_KEY",
    )
    assert fields_of(model) == {"provider", "model", "config_ref"}
    schema = schema_of(model)
    assert set(schema["required"]) == {"provider", "model", "config_ref"}
    assert schema.get("additionalProperties") is False
    for field in ("provider", "model", "config_ref"):
        with pytest.raises(ValidationError):
            model.model_validate({k: v for k, v in provider_ref().items() if k != field})
    unseen = model.model_validate(
        provider_ref(provider="some-new-vendor", model="org/Model-Name:v2")
    )
    assert (unseen.provider, unseen.model) == ("some-new-vendor", "org/Model-Name:v2")
    for field in ("provider", "model"):
        for bad in ("", "two words", "प्रदाता", "x" * 257, None, 7, "name\n"):
            with pytest.raises(ValidationError):
                model.model_validate(provider_ref(**{field: bad}))
    # a value object: no entity fields
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(model)


def test_provider_ref_extra_api_key_rejected() -> None:
    """A-04: an unexpected credential field is a validation error, never ignored."""
    model = _provider()
    project = entity_class("Project")
    for extra in (
        {"api_key": PASTED_KEY},
        {"apiKey": PASTED_KEY},
        {"secret": PASTED_KEY},
        {"password": "hunter2-hunter2"},
        {"token": PASTED_KEY},
        {"authorization": "Bearer " + PASTED_KEY},
        {"client_secret": PASTED_KEY},
    ):
        (field,) = extra
        with pytest.raises(ValidationError) as caught:
            model.model_validate({**provider_ref(), **extra})
        assert any(
            e["type"] == "extra_forbidden" and field in e["loc"] for e in caught.value.errors()
        )
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps({**provider_ref(), **extra}))
        # also where a provider reference is embedded
        doc = raw_document("Project", provider_configs=[{**provider_ref(), **extra}])
        assert_rejected(project, doc)
    assert model.model_config.get("extra") == "forbid"
    assert schema_of(model).get("additionalProperties") is False


def test_rejected_credentials_are_not_echoed_in_error_text() -> None:
    """Proposed name (ADR 0007 is silent; QA responsibility "secret leakage in logs").

    Pydantic shows the rejected input in ``str(ValidationError)`` unless the model hides it, so a
    pasted key or a credential URL would be written to logs by the very check that refuses it.
    The text of the error must not contain the credential."""
    model = _provider()
    target = entity_class("AgentTarget")
    project = entity_class("Project")
    secret_url = "wss://user:" + "s3cretvalue1234@agent.example.com/ws"
    attempts: list[tuple[Any, dict[str, Any], str]] = [
        (model, {**provider_ref(), "api_key": PASTED_KEY}, PASTED_KEY),
        (model, provider_ref(config_ref="env:" + PASTED_KEY), PASTED_KEY),
        (model, provider_ref(config_ref=AWS_KEY), AWS_KEY),
        (
            project,
            raw_document("Project", provider_configs=[{**provider_ref(), "api_key": PASTED_KEY}]),
            PASTED_KEY,
        ),
        (target, _target(endpoint=ws_endpoint(url=secret_url)), "s3cretvalue1234"),
        (
            target,
            _target(endpoint=ws_endpoint(url="wss://agent.example.com/ws?token=" + PASTED_KEY)),
            PASTED_KEY,
        ),
    ]
    for validator, doc, secret in attempts:
        error = assert_rejected(validator, doc)
        assert secret not in str(error), "the secret is echoed in the error text"
        assert secret not in repr(error)


def test_config_ref_shape() -> None:
    """P-19: ``config_ref`` is ``<scheme>:<name>``; a value with ``=`` or a known key prefix is
    rejected, so a pasted key cannot hide in an innocently named field."""
    model = _provider()
    for good in (
        "env:SARVAM_API_KEY",
        "keyring:tracer/sarvam",
        "vault:kv/prod/openai",
        "env:A",
        "env:sarvam.stt@prod",
    ):
        assert model.model_validate(provider_ref(config_ref=good)).config_ref == good
    pasted = [
        "env:" + PASTED_KEY,
        "env:" + AWS_KEY,
        "custom:" + "gh" + "p_" + "A" * 36,
        "vault:" + "AI" + "za" + "A" * 35,
        "env:" + "github_" + "pat_" + "A" * 22,
        PASTED_KEY,
        AWS_KEY,
    ]
    shape = [
        "SARVAM_API_KEY",
        "",
        "env:",
        ":SARVAM_API_KEY",
        "env: SARVAM_API_KEY",
        "env:SARVAM_API_KEY ",
        " env:SARVAM_API_KEY",
        "env:SARVAM_API_KEY\n",
        "env:A=B",
        "env:NAME=value",
        "env:नाम",
        "env:two words",
        "env:" + "x" * 300,
    ]
    for bad in [*pasted, *shape, None, 7, ["env:A"]]:
        with pytest.raises(ValidationError):
            model.model_validate(provider_ref(config_ref=bad))
    # the JSON Schema states the shape too (the known-prefix rule needs code, not a pattern)
    jsonschema = jsonschema_module()
    validator = jsonschema.Draft202012Validator(schema_of(model))
    assert validator.is_valid(provider_ref(config_ref="env:SARVAM_API_KEY"))
    for bad_shape in ("SARVAM_API_KEY", "env:", ":SARVAM_API_KEY", "env:A=B", ""):
        assert not validator.is_valid(provider_ref(config_ref=bad_shape)), bad_shape


# --------------------------------------------------------------------------------- Project


def test_project_is_a_record_of_fact_entity() -> None:
    """Proposed name. ``Project`` (prefix ``prj``) is a plain entity: not versioned, a ``name``
    label, ``provider_configs`` (default empty) holding ``ProviderConfigRef`` objects."""
    project = entity_class("Project")
    base = api("base")
    assert issubclass(project, base.EntityBase)
    assert not issubclass(project, base.VersionedEntity)
    assert project.id_prefix == "prj"
    assert not {"version", "content_digest"} & fields_of(project)
    loaded = make("Project")
    assert loaded.name == "Acme support"
    assert [ref.provider for ref in loaded.provider_configs] == ["sarvam"]
    assert str(loaded.id).startswith("prj_")
    minimal = {k: v for k, v in raw_document("Project").items() if k != "provider_configs"}
    assert project.model_validate(minimal).provider_configs == []
    for bad in ("", " padded", "padded ", "a\u200bb", "a\u202eb", "a\nb", None, 5):
        assert_rejected_name("Project", name=bad, field="name")
    with pytest.raises(ValidationError):
        project.model_validate({k: v for k, v in raw_document("Project").items() if k != "name"})
    assert project.model_validate(raw_document("Project", name="ണ്\u200d കേരള")).name.endswith(
        "കേരള"
    )
    assert_rejected_name("Project", provider_configs=[{"provider": "sarvam"}])
    assert_rejected_name("Project", provider_configs="env:SARVAM_API_KEY")


def test_metadata_secret_value_preserved_but_flagged() -> None:
    """A-03: a secret-looking metadata value is kept byte for byte and flagged in the serialized
    ``metadata_flags`` (ADR 0007 Decision 5 and 5a). Asserted on a real entity, ``Project``."""
    project = entity_class("Project")
    metadata = {
        "note": PASTED_KEY,
        "nested": {"token": AWS_KEY, "ok": "hello"},
        "list": ["plain", PASTED_KEY],
        "count": 3,
    }
    doc = raw_document("Project")
    doc.pop("metadata_flags")
    loaded = project.model_validate({**doc, "metadata": metadata})
    assert loaded.metadata == metadata, "the value is never altered or dropped"
    dumped = json.loads(loaded.model_dump_json())
    assert dumped["metadata"] == metadata
    flags = dumped["metadata_flags"]
    assert flags == [
        {"path": "/metadata/list/1", "reason": "known_credential_prefix"},
        {"path": "/metadata/nested/token", "reason": "known_credential_prefix"},
        {"path": "/metadata/note", "reason": "known_credential_prefix"},
    ]
    # the serialized flags load again (they are verified, not trusted)
    again = project.model_validate(dumped)
    assert again.metadata_flags == loaded.metadata_flags
    # a clean entity has an empty list that is still serialized
    clean = json.loads(make("Project").model_dump_json())
    assert clean["metadata_flags"] == []
    # a flag list that omits the secret is a forgery
    forged = {**dumped, "metadata_flags": []}
    assert_rejected(project, forged)


# --------------------------------------------------------------------------------- endpoint


def _bad_url_targets() -> list[tuple[str, dict[str, Any]]]:
    base = "agent.example.com"
    return [
        ("ws userinfo with password", ws_endpoint(url=f"wss://user:pass@{base}/ws")),
        ("ws userinfo only", ws_endpoint(url=f"wss://sometoken@{base}/ws")),
        ("ws token", ws_endpoint(url=f"wss://{base}/ws?token=abc123")),
        ("ws key", ws_endpoint(url=f"wss://{base}/ws?key=abc123")),
        ("ws api_key", ws_endpoint(url=f"wss://{base}/ws?api_key=abc123")),
        ("ws signature", ws_endpoint(url=f"wss://{base}/ws?X-Amz-Signature=abc123")),
        ("ws credential", ws_endpoint(url=f"wss://{base}/ws?X-Amz-Credential=abc123")),
        ("ws sig", ws_endpoint(url=f"wss://{base}/ws?sig=abc123")),
        ("ws signature name", ws_endpoint(url=f"wss://{base}/ws?signature=abc123")),
        ("ws second parameter", ws_endpoint(url=f"wss://{base}/ws?room=1&lang=hi&token=abc123")),
        ("webrtc userinfo", webrtc_endpoint(url=f"https://user:pass@{base}/api/offer")),
        ("webrtc token", webrtc_endpoint(url=f"https://{base}/api/offer?token=abc123")),
        ("webrtc presigned", webrtc_endpoint(url=f"https://{base}/o?X-Amz-Signature=abc123")),
        ("sip password", sip_endpoint(uri="sip:alice:s3cret@pbx.example.com")),
        ("sip token parameter", sip_endpoint(uri="sip:support@pbx.example.com?token=abc123")),
    ]


def test_endpoint_rejects_credentials_in_url() -> None:
    """P-20: endpoints reject userinfo and the known credential query parameters; presigned URLs
    are runtime-only. Parameter names match whole names, not substrings, and an ``@`` in the path
    is not userinfo."""
    target = entity_class("AgentTarget")
    for label, endpoint in _bad_url_targets():
        assert_rejected(target, _target(endpoint=endpoint), field="endpoint")
        assert label
    for label, endpoint in (
        ("plain", ws_endpoint()),
        ("port", ws_endpoint(url="wss://agent.example.com:8443/ws")),
        ("benign query", ws_endpoint(url="wss://agent.example.com/ws?room=support&lang=hi")),
        (
            "lookalike names",
            ws_endpoint(url="wss://agent.example.com/ws?monkey=1&tokenizer=x&keyboard=2"),
        ),
        ("at sign in path", ws_endpoint(url="wss://agent.example.com/users/@me/ws")),
        ("webrtc", webrtc_endpoint()),
        ("sip user part", sip_endpoint()),
    ):
        loaded = make("AgentTarget", endpoint=endpoint)
        assert loaded.endpoint is not None, label


def test_endpoint_url_credential_params_case_and_encoding() -> None:
    """Proposed name (ADR 0007 lists the parameter names only in the spellings below). A
    credential parameter must not slip through by case or percent-encoding of its name."""
    target = entity_class("AgentTarget")
    base = "wss://agent.example.com/ws"
    for url in (
        f"{base}?Token=abc123",
        f"{base}?API_KEY=abc123",
        f"{base}?Api_Key=abc123",
        f"{base}?x-amz-signature=abc123",
        f"{base}?X-AMZ-CREDENTIAL=abc123",
        f"{base}?%74oken=abc123",
        f"{base}?room=1&%61pi_key=abc123",
    ):
        assert_rejected(target, _target(endpoint=ws_endpoint(url=url)), field="endpoint")


def test_sip_endpoint_userinfo_rule() -> None:
    """Proposed name. In a SIP URI the user part is the address (``sip:1001@pbx``), so only a
    password in the userinfo is a credential (ambiguity: ADR 0007 Decision 6 says "userinfo
    (user:pass@)" for every URL)."""
    target = entity_class("AgentTarget")
    for uri in (
        "sip:support@pbx.example.com",
        "sips:+911234567890@gw.example.com",
        "sip:1001@pbx.example.com;transport=tcp",
        "sip:pbx.example.com",
    ):
        assert make("AgentTarget", endpoint=sip_endpoint(uri=uri)).endpoint.uri == uri
    for uri in ("sip:alice:s3cret@pbx.example.com", "sips:alice:s3cret@pbx.example.com"):
        assert_rejected(target, _target(endpoint=sip_endpoint(uri=uri)), field="endpoint")


def test_endpoint_transport_scheme_rules() -> None:
    """Proposed name. The URL scheme must fit the transport: ws takes ws or wss, sip takes sip or
    sips, webrtc takes http or https (its signalling endpoint)."""
    target = entity_class("AgentTarget")
    assert make("AgentTarget", endpoint=ws_endpoint(url="ws://localhost:8765")).endpoint.url
    assert make("AgentTarget", endpoint=webrtc_endpoint(url="http://localhost:7860/api/offer"))
    for endpoint in (
        ws_endpoint(url="https://agent.example.com/ws"),
        ws_endpoint(url="ftp://agent.example.com/ws"),
        ws_endpoint(url="agent.example.com/ws"),
        ws_endpoint(url=""),
        ws_endpoint(url="wss://"),
        webrtc_endpoint(url="ftp://agent.example.com/offer"),
        webrtc_endpoint(url="agent.example.com/offer"),
        sip_endpoint(uri="https://pbx.example.com"),
        sip_endpoint(uri="pbx.example.com"),
        sip_endpoint(uri=""),
    ):
        assert_rejected(target, _target(endpoint=endpoint), field="endpoint")


def test_endpoint_discriminated_by_transport() -> None:
    """ADR 0007 I7: the endpoint is a union discriminated by ``transport`` (ws, webrtc, sip);
    each variant has its own address field and nothing else; the tag is an ``enum`` in the
    JSON Schema (ADR 0003 Decision 6), and pydantic and JSON Schema agree."""
    target = entity_class("AgentTarget")
    for transport, endpoint in (
        ("ws", ws_endpoint()),
        ("webrtc", webrtc_endpoint()),
        ("sip", sip_endpoint()),
    ):
        doc = build("AgentTarget", endpoint=endpoint)
        loaded = target.model_validate(doc)
        assert loaded.endpoint.transport == transport
        assert loaded.model_dump(mode="json")["endpoint"] == endpoint
        assert schema_accepts(target, doc)
        assert target.model_validate_json(loaded.model_dump_json()) == loaded
    wrong: list[dict[str, Any]] = [
        {"url": "wss://agent.example.com/ws"},  # no transport
        {"transport": "grpc", "url": "https://agent.example.com"},
        {"transport": "WS", "url": "wss://agent.example.com/ws"},
        {"transport": "", "url": "wss://agent.example.com/ws"},
        {"transport": None, "url": "wss://agent.example.com/ws"},
        {"transport": "ws", "uri": "sip:support@pbx.example.com"},  # sip's field on ws
        {"transport": "sip", "url": "wss://agent.example.com/ws"},  # ws's field on sip
        {"transport": "webrtc", "uri": "https://agent.example.com/api/offer"},
        {"transport": "ws", "url": "wss://agent.example.com/ws", "extra": 1},
        {"transport": "ws"},
        {"transport": "sip"},
    ]
    for endpoint in wrong:
        assert_rejected(target, _target(endpoint=endpoint), field="endpoint")
        assert not schema_accepts(target, raw_document("AgentTarget", endpoint=endpoint)), endpoint
    # the schema carries one variant per transport, tagged with an enum, never a const
    schema = schema_of(target)
    tags = sorted(
        tuple(node["properties"]["transport"].get("enum", ()))
        for node in walk_nodes(schema)
        if "transport" in node.get("properties", {})
    )
    assert tags == [("sip",), ("webrtc",), ("ws",)]
    assert not any("const" in node for node in walk_nodes(schema))
    assert _validate_all_variants_agree(schema)


def _validate_all_variants_agree(schema: dict[str, Any]) -> bool:
    """The three valid endpoints validate against the schema, one variant each."""
    jsonschema = jsonschema_module()
    validator = jsonschema.Draft202012Validator(schema)
    return all(
        validator.is_valid(build("AgentTarget", endpoint=endpoint))
        for endpoint in (ws_endpoint(), webrtc_endpoint(), sip_endpoint())
    )


# -------------------------------------------------------------------------------- AgentTarget


def test_agent_target_is_a_versioned_entity() -> None:
    """Proposed name. ``AgentTarget`` (prefix ``agt``) is versioned for its declared fields."""
    target = entity_class("AgentTarget")
    base = api("base")
    assert issubclass(target, base.VersionedEntity)
    assert target.id_prefix == "agt"
    loaded = make("AgentTarget")
    assert loaded.version == 1
    assert loaded.content_digest.scheme == "sha256-jcs/v1"
    assert loaded.name == "Acme voice agent"
    assert loaded.languages == ["hi-IN", "en-IN"]
    for bad in ("", " padded", "a\u200bb", None):
        assert_rejected_name("AgentTarget", name=bad, field="name")
    minimal = {
        k: v
        for k, v in raw_document("AgentTarget").items()
        if k not in {"languages", "declared_capabilities", "declared_stack"}
    }
    bare = target.model_validate(seal(target, minimal))
    assert (bare.languages, bare.declared_capabilities, bare.declared_stack) == ([], [], [])
    assert_rejected_name("AgentTarget", endpoint=None, field="endpoint")


def test_agent_target_languages_supported_only() -> None:
    """Proposed name. ``languages`` holds configured languages: native script, supported regions,
    canonical case; excluded languages, romanized tags and the mixed alias are rejected."""
    assert make("AgentTarget", languages=["HI-in", "pa-Guru-IN", "ml"]).languages == [
        "hi-IN",
        "pa-Guru-IN",
        "ml",
    ]
    assert make("AgentTarget", languages=[]).languages == []
    bad_tags = [
        "hi-Latn",
        MIXED_ALIAS_TAG,
        *EXCLUDED_LANGUAGE_TAGS,
        "pa-" + "Arab",
        "ta-LK",
        "en-AU",
        "hindi",
        "hi_IN",
        "und",
        "",
    ]
    for tag in bad_tags:
        assert_rejected_name("AgentTarget", languages=["hi-IN", tag], field="languages")
    mixed_message = mixed_alias_message()
    assert mixed_message in str(assert_rejected_name("AgentTarget", languages=[MIXED_ALIAS_TAG]))


def test_declared_vs_detected_capabilities_separate() -> None:
    """P-23: the versioned AgentTarget holds declared capabilities only; what a run detects lives
    on ``Run`` (``available_inputs``, ``detected_capabilities``) and is never written back.

    Asserted here: the declared list is a closed ``InputKind`` list, no AgentTarget field or
    property can hold a detection, and a field named for one is rejected. (The Run side belongs
    to P1-12.)"""
    target = entity_class("AgentTarget")
    assert "declared_capabilities" in fields_of(target)
    names = (
        fields_of(target)
        | property_names(target, "validation")
        | property_names(target, "serialization")
    )
    assert not [n for n in names if "detected" in n.lower() or "observed" in n.lower()]
    for field in ("detected_capabilities", "available_inputs", "capabilities", "observed_tier"):
        extra: dict[str, Any] = {field: ["tool_calls"]}
        assert_rejected_name("AgentTarget", **extra)
    declared = make(
        "AgentTarget", declared_capabilities=["tool_calls", "spans", "agent_transcript"]
    )
    assert declared.declared_capabilities == ["tool_calls", "spans", "agent_transcript"]
    assert set(property_enum(target, "declared_capabilities")) == {
        "audio",
        "agent_audio",
        "transcript",
        "agent_transcript",
        "turn_timing",
        "tool_calls",
        "tool_results",
        "spans",
        "usage",
        "reference_text",
        "expected_tool_calls",
    }
    for bad in (["telepathy"], ["Tool_Calls"], "tool_calls", [None]):
        assert_rejected_name(
            "AgentTarget", declared_capabilities=bad, field="declared_capabilities"
        )
    # declared fields are part of the version: changing one without a new digest is rejected
    doc = build("AgentTarget")
    errors = api("errors")
    with pytest.raises(errors.DigestMismatch):
        target.model_validate({**doc, "declared_capabilities": ["usage"]})
    empty = make("AgentTarget", declared_capabilities=[])
    assert empty.declared_capabilities == []


def test_declared_stack_entry_shape() -> None:
    """ADR 0012 I15: a declared-stack entry is exactly ``{stage, provider, model}``; ``stage`` is a
    closed set, ``provider`` and ``model`` are open ``MachineKey`` values."""
    entry = cls("agent", "DeclaredStackEntry")
    good = {"stage": "stt", "provider": "sarvam", "model": "saaras:v4"}
    loaded = entry.model_validate(good)
    assert loaded.model_dump(mode="json") == good
    assert fields_of(entry) == {"stage", "provider", "model"}
    schema = schema_of(entry)
    assert set(schema["required"]) == {"stage", "provider", "model"}
    assert schema.get("additionalProperties") is False
    assert {"stt", "llm", "tts"} <= set(property_enum(entry, "stage"))
    assert not any("const" in node for node in walk_nodes(schema))
    for stage in ("stt", "llm", "tts"):
        assert entry.model_validate({**good, "stage": stage}).stage == stage
    for bad_stage in ("STT", "speech_to_text", "", None, 3, "stt "):
        with pytest.raises(ValidationError):
            entry.model_validate({**good, "stage": bad_stage})
    for field in good:
        with pytest.raises(ValidationError):
            entry.model_validate({k: v for k, v in good.items() if k != field})
    with pytest.raises(ValidationError):
        entry.model_validate({**good, "api_key": PASTED_KEY})
    with pytest.raises(ValidationError):
        entry.model_validate({**good, "stage_raw": "x"})
    unseen = entry.model_validate(
        {**good, "provider": "some-new-vendor", "model": "org/Model-Name:v2"}
    )
    assert (unseen.provider, unseen.model) == ("some-new-vendor", "org/Model-Name:v2")
    for field in ("provider", "model"):
        for bad in ("", "two words", "प्रदाता", None):
            with pytest.raises(ValidationError):
                entry.model_validate({**good, field: bad})
    # embedded in the target, in order
    stack = [
        good,
        {"stage": "llm", "provider": "openai", "model": "gpt-4.1"},
        {"stage": "tts", "provider": "elevenlabs", "model": "eleven_multilingual_v2"},
    ]
    assert [e.stage for e in make("AgentTarget", declared_stack=stack).declared_stack] == [
        "stt",
        "llm",
        "tts",
    ]
    assert_rejected_name("AgentTarget", declared_stack=[{"stage": "stt", "provider": "sarvam"}])


# ================================================================================ revision 8
# Pins api-surface 19.4 / 19.4.1 (revision 8), ADR 0007 Decisions 6a and 8, and the owner's answer
# to P-60 (wider credential detection in endpoint URLs).

HOST = "agent.example.com"
WS = f"wss://{HOST}/ws"


def _credential_samples() -> dict[str, str]:
    """Strings that match a known-credential pattern (ADR 0007 Decision 5a), built from parts."""
    return {
        "openai": PASTED_KEY,
        "aws": AWS_KEY,
        "github": "gh" + "p_" + "A1" * 18,
        "google": "AI" + "za" + "B2" * 17 + "c",
        "slack": "xox" + "b-" + "1234567890-abcdefghij",
        "gitlab": "gl" + "pat-" + "abcdefghij" * 2,
        "hugging": "hf_" + "aBcDeFgHiJ" * 3,
        "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".sig",
    }


def _rejected_endpoint(endpoint: dict[str, Any]) -> None:
    assert_rejected(entity_class("AgentTarget"), _target(endpoint=endpoint), field="endpoint")


def _accepted_endpoint(endpoint: dict[str, Any]) -> Any:
    return make("AgentTarget", endpoint=endpoint).endpoint


# -------------------------------------------------------------------------- uniqueness rules


def test_project_provider_configs_unique_per_provider_and_model() -> None:
    """Section 19.4.1 and ADR 0007 Decision 1: ``Project.provider_configs`` is unique per
    ``(provider, model)``, because the run resolves a ``ModelRef`` to the one entry with the same
    pair; a different ``config_ref`` does not make a second entry. (Name from SCOPE section 5,
    revision 8, proposed.)"""
    first = provider_ref()
    ok = [
        first,
        provider_ref(model="saarika:v2", config_ref="env:SARVAM_STT_KEY"),
        provider_ref(provider="openai", model="saaras:v4", config_ref="env:OPENAI_API_KEY"),
    ]
    assert len(make("Project", provider_configs=ok).provider_configs) == 3
    for clash in (
        provider_ref(),
        provider_ref(config_ref="env:OTHER_KEY"),
        provider_ref(config_ref="keyring:tracer/sarvam"),
    ):
        assert_rejected_name("Project", provider_configs=[first, clash], field="provider_configs")
    assert_rejected_name(
        "Project",
        provider_configs=[*ok, provider_ref(config_ref="env:LAST")],
        field="provider_configs",
    )


def test_declared_capabilities_unique() -> None:
    """Section 19.4 row 4: ``AgentTarget.declared_capabilities`` is a unique list of ``InputKind``
    (declaration only: no not-applicable logic reads it, ADR 0004 Decision 2); order is kept."""
    declared = ["spans", "tool_calls", "usage"]
    assert make("AgentTarget", declared_capabilities=declared).declared_capabilities == declared
    for bad in (["tool_calls", "tool_calls"], ["spans", "tool_calls", "spans"]):
        assert_rejected_name(
            "AgentTarget", declared_capabilities=bad, field="declared_capabilities"
        )


def test_agent_target_languages_empty_means_undeclared_and_unique() -> None:
    """Resolution 12 (ADR 0005 Decision 6): ``AgentTarget.languages`` may be empty, which means "no
    language declared", never "all languages". Exact duplicates after case canonicalization are
    rejected; two tags of one primary language are allowed (no logic reads this list), and the
    metric-only value ``language_independent`` (P-59) is not accepted here."""
    assert make("AgentTarget", languages=[]).languages == []
    bare = {k: v for k, v in raw_document("AgentTarget").items() if k != "languages"}
    target = entity_class("AgentTarget")
    assert target.model_validate(seal(target, bare)).languages == []
    assert make("AgentTarget", languages=["hi", "hi-IN"]).languages == ["hi", "hi-IN"]
    for bad in (
        ["hi-IN", "hi-IN"],
        ["hi-IN", "HI-in"],
        ["en", "hi", "en"],
        "language_independent",
        ["language_independent"],
        "all",
        None,
    ):
        assert_rejected_name("AgentTarget", languages=bad, field="languages")


def test_declared_stack_stage_is_exact() -> None:
    """Resolution 13: ``DeclaredStackEntry.stage`` is exactly ``stt``, ``llm``, ``tts``,
    ``realtime`` (a subset of the usage-stage set). Evaluation-side stages are never the agent's,
    and ``other`` would need a raw label that the entry does not have. Proposed name."""
    entry = cls("agent", "DeclaredStackEntry")
    assert sorted(property_enum(entry, "stage")) == ["llm", "realtime", "stt", "tts"]
    good = {"stage": "stt", "provider": "sarvam", "model": "saaras:v4"}
    for stage in ("stt", "llm", "tts", "realtime"):
        assert entry.model_validate({**good, "stage": stage}).stage == stage
    for bad in ("judge", "sim_caller_llm", "sim_caller_tts", "other", "Realtime", "asr"):
        with pytest.raises(ValidationError):
            entry.model_validate({**good, "stage": bad})


# ----------------------------------------------------------------- no-echo rule (ADR 0007 D8)


def test_hide_input_set_closed_under_embedding() -> None:
    """ADR 0007 Decision 8 (a), (b), (e): every model whose validation can reach a
    credential-checked field or personal data sets ``hide_input_in_errors=True``, and the set is
    closed under embedding, because the setting of the outermost model governs (a model that
    embeds a hiding model but does not hide itself shows the rejected value). Seeds:
    ``ProviderConfigRef``, ``Project``, ``AgentTarget``, the three endpoint classes,
    ``TestProfile``. Revision 11 (N31) adds ``Transcript``, ``Turn``, ``Segment``,
    ``ToolCallEvent`` and ``IngestWarning``; revision 12 (O4) adds ``ConfigSnapshot`` and
    ``Evaluator``; revision 13 (N41) adds ``ScriptedTurn``, ``ExpectedToolCall``, ``Persona``,
    ``MetricDefinition``, ``MetricBinding`` and ``Rubric``; revision 15 (N60) adds ``UsageRecord``,
    ``ReportedCostRaw`` and ``CostRecord``, and revision 16 (N62) ``PriceProvenance``,
    ``PriceEntry`` and ``PricingCatalog``; revision 18 (S-N88) ``SealedPayload`` and the two models
    nested in it. A model that embeds any of them, at
    any depth, hides too. Models added later (``Artifact``, bundles) fall under the same walk."""
    registry = api("registry")
    by_name = {m.__name__: m for m in registry.model_classes()}
    # revision 18 (api-surface-sealing R2, S-N88): the sealed payload and the two models nested in
    # it (the key-wrap model and the AAD model; their class names are not pinned, so they are read
    # from the field annotations of ``SealedPayload``)
    sealed_nested: list[str] = []
    if "SealedPayload" in by_name:
        for field in ("dek_wrap", "aad_fields"):
            annotation = by_name["SealedPayload"].model_fields[field].annotation
            assert isinstance(annotation, type), (field, annotation)
            sealed_nested.append(annotation.__name__)
        assert len(set(sealed_nested)) == 2, sealed_nested
    seeds = (
        "ProviderConfigRef",
        "Project",
        "AgentTarget",
        "WebSocketEndpoint",
        "WebRtcEndpoint",
        "SipEndpoint",
        "TestProfile",
        # revision 11, N31 (api-surface 22.9 item 8)
        "Transcript",
        "Turn",
        "Segment",
        "ToolCallEvent",
        "IngestWarning",
        # revision 12, O4 (item 15)
        "ConfigSnapshot",
        "Evaluator",
        # revision 13, N41 (item 17)
        "ScriptedTurn",
        "ExpectedToolCall",
        "Persona",
        "MetricDefinition",
        "MetricBinding",
        "Rubric",
        # revision 15, N60 (api-surface 23.6 group I item 1): the usage and cost records that keep
        # provider payloads; revision 16, N62: the pricing models that reach the credential-checked
        # provenance URL
        "UsageRecord",
        "ReportedCostRaw",
        "CostRecord",
        "PriceProvenance",
        "PriceEntry",
        "PricingCatalog",
        # revision 18, S-N88: a rejected value can be a damaged ciphertext or wrapped DEK, the
        # nonce, or the id of the person's record whose data is sealed
        "SealedPayload",
        "KeyRef",
        "ContentDigest",
        *sealed_nested,
    )
    for name in seeds:
        assert name in by_name, f"{name} is not implemented yet"
        assert by_name[name].model_config.get("hide_input_in_errors") is True, name
    seed_models = {by_name[name] for name in seeds}
    unprotected = [
        model.__name__
        for model in by_name.values()
        if embedded_models(model) & seed_models
        and model.model_config.get("hide_input_in_errors") is not True
    ]
    assert not unprotected, f"models that embed a hiding model but show their input: {unprotected}"
    # the walk reaches through unions and lists: AgentTarget embeds the endpoint classes
    assert {by_name["WebSocketEndpoint"], by_name["SipEndpoint"]} <= embedded_models(
        by_name["AgentTarget"]
    )
    assert by_name["ProviderConfigRef"] in embedded_models(by_name["Project"])


def test_credential_errors_have_no_input_in_any_logged_form() -> None:
    """ADR 0007 Decision 8 (c), (d): custom messages name the rule, never the value, so a rejected
    credential does not appear in ``str``, ``repr``, ``errors(include_input=False)`` (the form that
    code which logs errors calls; plain ``errors()`` always carries the input in pydantic) or
    ``json(include_input=False)``, for the Python and the JSON entry point, nested or not.
    Proposed name."""
    samples = _credential_samples()
    target = entity_class("AgentTarget")
    project = entity_class("Project")
    model = _provider()
    attempts: list[tuple[Any, dict[str, Any], str]] = []
    for label, secret in samples.items():
        attempts += [
            (model, provider_ref(config_ref="env:" + secret), secret),
            (model, {**provider_ref(), "api_key": secret}, secret),
            (
                project,
                raw_document("Project", provider_configs=[{**provider_ref(), "key": secret}]),
                secret,
            ),
            (
                project,
                raw_document("Project", provider_configs=[provider_ref(config_ref=secret)]),
                secret,
            ),
            (target, _target(endpoint=ws_endpoint(url=f"{WS}/{secret}")), secret),
            (target, _target(endpoint=ws_endpoint(url=f"{WS}?room={secret}")), secret),
            (target, _target(endpoint=ws_endpoint(url=f"{WS}?access_token={secret}")), secret),
            (target, _target(endpoint=sip_endpoint(uri=f"sip:{secret}@pbx.example.com")), secret),
        ]
        assert label
    attempts += [
        (
            target,
            _target(endpoint=ws_endpoint(url=f"wss://user:hunter2hunter2@{HOST}/ws")),
            "hunter2hunter2",
        ),
        (
            target,
            _target(endpoint=webrtc_endpoint(url=f"https://{HOST}/o?sig=abcdef123456")),
            "abcdef123456",
        ),
        (
            target,
            _target(endpoint=sip_endpoint(uri="sip:alice:hunter2hunter2@pbx.example.com")),
            "hunter2hunter2",
        ),
        (
            target,
            _target(endpoint=sip_endpoint(uri="sip:alice%3Ahunter2hunter2@pbx.example.com")),
            "hunter2hunter2",
        ),
    ]
    for validator, doc, secret in attempts:
        error = assert_rejected(validator, doc)
        for text in error_texts(error):
            assert secret not in text, f"{secret!r} is echoed in a validation error"
        body = dict(doc)
        if "content_digest" in validator.model_fields:
            body.setdefault("content_digest", PLACEHOLDER_DIGEST)
        with pytest.raises(ValidationError) as via_json:
            validator.model_validate_json(json.dumps(body))
        for text in error_texts(via_json.value):
            assert secret not in text, f"{secret!r} is echoed in a validation error (JSON)"


# -------------------------------------------------------------- URL rules (ADR 0007 D6a, P-60)

# The P-20 names and the P-60 additions: ADR 0007 Decision 3 credential names and the presign names.
P20_NAMES = ("token", "key", "api_key", "x-amz-signature", "x-amz-credential", "sig", "signature")
P60_NAMES = (
    "apikey",
    "access_token",
    "auth_token",
    "refresh_token",
    "client_secret",
    "password",
    "passwd",
    "secret",
    "bearer",
    "authorization",
    "credential",
    "credentials",
    "private_key",
    "x-amz-security-token",
    "x-goog-signature",
    "x-goog-credential",
)


def _urls_with_param(name: str) -> list[dict[str, Any]]:
    """The endpoint documents of every transport that carry the parameter ``name``."""
    return [
        ws_endpoint(url=f"{WS}?{name}=abc123"),
        ws_endpoint(url=f"{WS}?room=1&{name}=abc123&lang=hi"),
        ws_endpoint(url=f"{WS}?room=1;{name}=abc123"),
        webrtc_endpoint(url=f"https://{HOST}/api/offer?{name}=abc123"),
        sip_endpoint(uri=f"sip:support@pbx.example.com?{name}=abc123"),
        sip_endpoint(uri=f"sip:support@pbx.example.com;{name}=abc123"),
    ]


def test_endpoint_rejects_wider_credential_names_and_prefixes() -> None:
    """P-60 (accepted by the owner), the conditional test of SCOPE section 5: the credential
    parameter names are the P-20 list plus the ADR 0007 Decision 3 names (``apikey``,
    ``access_token``, ``auth_token``, ``refresh_token``, ``client_secret``, ``password``,
    ``passwd``, ``secret``, ``bearer``, ``authorization``, ``credential``, ``credentials``,
    ``private_key``) and the presign names ``x-amz-security-token``, ``x-goog-signature``,
    ``x-goog-credential``, in the query of ws and webrtc URLs and in the ``;uri-parameters`` and
    ``?headers`` of SIP. A URL whose percent-decoded text contains a match of a known-credential
    pattern (ADR 0007 Decision 5a, with its boundary rule) anywhere, including the path and a SIP
    user part, is rejected too."""
    for name in (*P20_NAMES, *P60_NAMES):
        for spelling in (name, name.upper(), name.title()):
            for endpoint in _urls_with_param(spelling):
                _rejected_endpoint(endpoint)
    # a credential pattern anywhere in the decoded URL, with an innocent parameter name or none
    for label, secret in _credential_samples().items():
        for endpoint in (
            ws_endpoint(url=f"{WS}/{secret}"),
            ws_endpoint(url=f"{WS}/v1/task_{secret}/stream"),
            ws_endpoint(url=f"{WS}?room={secret}"),
            ws_endpoint(url=f"{WS}?room=1&label={secret}"),
            webrtc_endpoint(url=f"https://{HOST}/api/{secret}/offer"),
            webrtc_endpoint(url=f"https://{HOST}/api/offer?session={secret}"),
            sip_endpoint(uri=f"sip:{secret}@pbx.example.com"),
            sip_endpoint(uri=f"sip:support@pbx.example.com?Subject={secret}"),
        ):
            _rejected_endpoint(endpoint)
        assert label
    # percent-encoded text is decoded before the patterns are applied
    encoded_sk = "%73k-" + PASTED_KEY[3:]
    encoded_akia = "%41KIA" + AWS_KEY[4:]
    for endpoint in (
        ws_endpoint(url=f"{WS}/{encoded_sk}"),
        ws_endpoint(url=f"{WS}?room={encoded_akia}"),
        sip_endpoint(uri=f"sip:{encoded_akia}@pbx.example.com"),
    ):
        _rejected_endpoint(endpoint)
    # the boundary rule of Decision 5a: the pattern must start the text or follow a character
    # outside [A-Za-z0-9], so a longer word that merely ends like a key prefix is not a key
    tail = PASTED_KEY[3:]
    for endpoint in (
        ws_endpoint(url=f"{WS}/desk-{tail}"),
        ws_endpoint(url=f"{WS}?room=x{AWS_KEY}"),
        webrtc_endpoint(url=f"https://{HOST}/api/task-{tail}"),
    ):
        assert _accepted_endpoint(endpoint) is not None
    # whole names only: look-alikes, plurals and longer names are not credential names
    benign = (
        "tokenizer",
        "monkey",
        "keyboard",
        "keys",
        "signal",
        "signature_version",
        "secretary",
        "passwords",
        "author",
        "token_id",
        "access_token_id",
        "credential_type",
        "bearers",
    )
    for name in benign:
        for endpoint in _urls_with_param(name):
            assert _accepted_endpoint(endpoint) is not None, name
    # ordinary endpoints stay valid
    for endpoint in (
        ws_endpoint(),
        ws_endpoint(url=f"{WS}/v1/agents/support?room=support&lang=hi-IN&retries=3"),
        webrtc_endpoint(),
        sip_endpoint(),
        sip_endpoint(uri="sip:support@pbx.example.com;transport=tcp?Subject=help"),
    ):
        assert _accepted_endpoint(endpoint) is not None


def test_endpoint_parameter_names_percent_decoded_and_casefolded() -> None:
    """ADR 0007 Decision 6a (parameter names): the query is split at ``&`` and ``;``; a name is the
    text before the first ``=``, percent-decoded as UTF-8, case-folded with ``str.casefold()`` and
    compared as a whole name. A malformed escape or invalid UTF-8 rejects the URL. Decoding comes
    before folding, the order a server applies. The long s (U+017F) folds to ``s``, so it is
    caught by ``casefold`` and not by ``lower``."""
    long_s = "%C5%BF"
    for query in (
        "%74oken=1",
        "%54OKEN=1",
        "to%6Ben=1",
        "%61pi_key=1",
        "Api%5FKey=1",
        "%78-amz-signature=1",
        "X-Amz-Sig%6Eature=1",
        "access%5Ftoken=1",
        f"{long_s}ig=1",
        f"{long_s}ecret=1",
        f"{long_s}ignature=1",
        "token",
        "TOKEN=",
        "room=1&%74oken=1",
        "room=1;Token=1",
    ):
        _rejected_endpoint(ws_endpoint(url=f"{WS}?{query}"))
        _rejected_endpoint(webrtc_endpoint(url=f"https://{HOST}/o?{query}"))
    for malformed in ("%zz=1", "%ff=1", "%C3=1", "a%=1", "%4=1", "to%=1", "%E0%A4=1"):
        _rejected_endpoint(ws_endpoint(url=f"{WS}?{malformed}"))
        _rejected_endpoint(webrtc_endpoint(url=f"https://{HOST}/o?{malformed}"))
    for ordinary in ("%72oom=1", "room=%74oken", "a=b%26token%3D1", "room=1&&lang=hi"):
        assert _accepted_endpoint(ws_endpoint(url=f"{WS}?{ordinary}")) is not None, ordinary


def test_endpoint_url_syntax_rules() -> None:
    """ADR 0007 Decision 6a (ws and webrtc): an absolute RFC 3986 URI, ASCII only, with no white
    space or control characters; the scheme is lower case (``ws``/``wss``, ``http``/``https``); a
    host is required; any userinfo is rejected, where the authority ends at the first ``/``, ``?``
    or ``#``, so an ``@`` in the path or the query is not userinfo; a fragment is rejected. Proposed
    name."""
    for url in (
        f"{WS}#room",
        f"{WS}?room=1#frag",
        f"{WS}#token=abc",
        f"wss://{HOST}#top",
    ):
        _rejected_endpoint(ws_endpoint(url=url))
    _rejected_endpoint(webrtc_endpoint(url=f"https://{HOST}/api/offer#x"))
    for url in (
        f"{WS} path",
        f"{WS}\n",
        f"{WS}\t",
        f" {WS}",
        f"{WS} ",
        f"wss://{HOST}/w\x00s",
        f"wss://{HOST}/w\x7fs",
        "wss://" + chr(0x905) + chr(0x91C) + ".example.com/ws",
        f"{WS}/" + chr(0xE9),
        f"{WS}?room=" + chr(0xE9),
    ):
        _rejected_endpoint(ws_endpoint(url=url))
    for url in (
        f"WSS://{HOST}/ws",
        f"Wss://{HOST}/ws",
        f"WS://{HOST}/ws",
        f"wss:{HOST}/ws",
        "wss:///ws",
        "wss://:8443/ws",
        "wss://",
    ):
        _rejected_endpoint(ws_endpoint(url=url))
    for url in (
        f"HTTPS://{HOST}/o",
        f"Https://{HOST}/o",
        f"https:{HOST}/o",
        "https:///o",
        f"https://{HOST}/o p",
    ):
        _rejected_endpoint(webrtc_endpoint(url=url))
    for url in (
        f"wss://user@{HOST}/ws",
        f"wss://user:pass@{HOST}/ws",
        f"wss://{HOST}@evil.example.com/ws",
        f"wss://@{HOST}/ws",
    ):
        _rejected_endpoint(ws_endpoint(url=url))
    _rejected_endpoint(webrtc_endpoint(url=f"https://user@{HOST}/o"))
    for url in (
        f"wss://{HOST}?contact=a@example.com",
        f"{WS}?contact=a@example.com",
        f"wss://{HOST}/users/@me/ws",
        "wss://[::1]:8765/ws",
        f"wss://{HOST}:8443/ws",
    ):
        assert _accepted_endpoint(ws_endpoint(url=url)) is not None, url
    assert _accepted_endpoint(webrtc_endpoint(url=f"https://{HOST}?contact=a@example.com"))


def test_sip_uri_credential_rules() -> None:
    """ADR 0007 Decision 6a (sip): ``sip:``/``sips:`` (lower case) with a host. The user part is the
    address and is allowed (``sip:1001@pbx``); a password, meaning a ``:`` in the userinfo before
    ``@`` also when percent-encoded as ``%3A`` (either case), is rejected, while a ``:`` after the
    host is a port. Parameter names are checked in ``;uri-parameters`` and ``?headers``. Proposed
    name."""
    for uri in (
        "sip:support@pbx.example.com",
        "sips:+911234567890@gw.example.com",
        "sip:1001@pbx.example.com;transport=tcp",
        "sip:support@pbx.example.com:5060",
        "sip:pbx.example.com:5060",
        "sip:pbx.example.com",
        "sips:support@pbx.example.com:5061;transport=tls?Subject=help",
    ):
        assert _accepted_endpoint(sip_endpoint(uri=uri)).uri == uri
    for uri in (
        "sip:alice:s3cret@pbx.example.com",
        "sips:alice:s3cret@pbx.example.com",
        "sip:alice:@pbx.example.com",
        "sip:alice%3As3cret@pbx.example.com",
        "sip:alice%3as3cret@pbx.example.com",
        "sip:alice%3A@pbx.example.com",
        "SIP:support@pbx.example.com",
        "Sips:support@pbx.example.com",
        "sip:",
        "sip:support@",
        "sip:support@pbx.example.com;token=abc",
        "sip:support@pbx.example.com;Password=abc",
        "sip:support@pbx.example.com?Authorization=abc",
        "sip:support@pbx.example.com?Subject=help&X-Goog-Signature=abc",
        "sip:support@pbx.example.com?%74oken=abc",
    ):
        _rejected_endpoint(sip_endpoint(uri=uri))


def test_agent_target_has_no_tier_adapter_or_description_field() -> None:
    """Section 19.4.1 (N11): the plan's ``description``, ``declared_tier`` and ``adapter`` are not
    fields of ``AgentTarget`` in 0.0.1: an adapter is a provider-specific concern, and a declared
    tier would be read as availability, which ADR 0004 Decision 2 forbids. They are rejected as
    unknown fields. Proposed name."""
    target = entity_class("AgentTarget")
    data = fields_of(target) - {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert data == {
        "version",
        "content_digest",
        "name",
        "endpoint",
        "languages",
        "declared_capabilities",
        "declared_stack",
    }
    for field, value in (
        ("description", "Acme support line"),
        ("declared_tier", "T1"),
        ("adapter", "pipecat"),
    ):
        caught = assert_rejected_name("AgentTarget", **{field: value})
        assert any(e["type"] == "extra_forbidden" for e in caught.errors()), field


# ================================================================================ N15
# Owner decision N15 (accepted): in the credential-parameter name check, '-' and '_' are equal, and
# x-api-key and x-goog-api-key join the set (25 names).

URL_CREDENTIAL_NAMES_25 = frozenset(
    {
        "token",
        "key",
        "api_key",
        "sig",
        "signature",
        "x-amz-signature",
        "x-amz-credential",
        "apikey",
        "access_token",
        "auth_token",
        "refresh_token",
        "client_secret",
        "password",
        "passwd",
        "secret",
        "bearer",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
        "x-api-key",
        "x-goog-api-key",
    }
)


def test_url_credential_parameter_names_exact() -> None:
    """ADR 0007 Decision 6b (I11), extended by the accepted N15: ``credentials.
    URL_CREDENTIAL_PARAMETER_NAMES`` holds exactly 25 case-folded names: the 23 of Decision 6b plus
    ``x-api-key`` and ``x-goog-api-key`` (no folding of ``-`` and ``_`` maps one to the other
    names). Each is rejected on every transport, in the query and in SIP parameters and headers.
    Proposed location of the constant is the documented one (``tracer_evalschema.credentials``);
    if the documents list a different set, they govern."""
    names = api("credentials").URL_CREDENTIAL_PARAMETER_NAMES
    assert isinstance(names, frozenset)
    assert names == URL_CREDENTIAL_NAMES_25
    assert len(names) == 25
    assert all(name == name.casefold() for name in names)
    for name in sorted(names):
        for endpoint in _urls_with_param(name):
            _rejected_endpoint(endpoint)


def test_url_credential_parameter_names_hyphen_underscore_equivalent() -> None:
    """N15 (accepted): names are percent-decoded, case-folded and compared with ``-`` and ``_``
    treated as equal, so ``access-token``, ``api-key``, ``x-api-key``, ``X_Api_Key`` and
    ``x_amz_signature`` are rejected like ``access_token``. Only ``-`` and ``_`` are equal: other
    punctuation, a removed separator and longer or shorter names are different names. Proposed
    name."""
    swaps = {}
    for name in URL_CREDENTIAL_NAMES_25:
        swaps[name] = {
            name.replace("-", "_"),
            name.replace("_", "-"),
            name.replace("-", "_").replace("_", "-"),
            name.upper().replace("-", "_"),
            name.title().replace("_", "-"),
            name.replace("-", "%2D").replace("_", "%5F"),
            name.replace("_", "%2d").replace("-", "%5f"),
        }
    for variants in swaps.values():
        for variant in sorted(variants):
            for endpoint in _urls_with_param(variant):
                _rejected_endpoint(endpoint)
    for variant in ("access-token", "api-key", "x-api-key", "X_Api_Key", "x_amz_signature"):
        for endpoint in _urls_with_param(variant):
            _rejected_endpoint(endpoint)
    # a mixed spelling inside one name
    for variant in ("x-goog_api-key", "Client-Secret", "private-KEY", "x_goog-credential"):
        for endpoint in _urls_with_param(variant):
            _rejected_endpoint(endpoint)
    for benign in (
        "access.token",
        "accesstoken",
        "access token",
        "access-token-ttl",
        "x-api-keys",
        "x-api-key-id",
        "api--key",
        "x-goog-api",
        "apiKey1",
        "tokens",
    ):
        quoted = benign.replace(" ", "%20")
        for endpoint in _urls_with_param(quoted):
            assert _accepted_endpoint(endpoint) is not None, benign


# ------------------------------------------------- URL credential patterns (ADR 0007 D6b, P-60)

_RUN_ALPHABET = "aB3dE5fG7hJ9kLmNpQ"


def _run(count: int) -> str:
    """``count`` characters of ``[A-Za-z0-9]`` that contain no separator and no pattern prefix."""
    return (_RUN_ALPHABET * (count // len(_RUN_ALPHABET) + 1))[:count]


def _pattern_cases() -> dict[str, tuple[list[str], list[str]]]:
    """For each Decision 5a pattern: strings that match at the minimum length (and a variant of
    the class), and near misses that do not. Built from parts, so this file holds no key."""
    jwt_head = "eyJ" + _run(10) + ".eyJ" + _run(10) + "."
    return {
        "sk-": (["sk-" + _run(20), "sk-" + _run(12) + "_" + "-" + _run(8)], ["sk-" + _run(19)]),
        "stripe": (
            ["sk_" + "live_" + _run(16), "rk_" + "test_" + _run(16), "sk_" + "test_" + _run(30)],
            ["sk_" + "live_" + _run(15), "sk_" + "prod_" + _run(16), "pk_" + "live_" + _run(16)],
        ),
        "aws": (
            ["AKIA" + "IOSFODNN7EXAMPLE", "ASIA" + "0123456789ABCDEF", "AKIA" + "A" * 17],
            ["AKIA" + "IOSFODNN7EXAMPL", "AKIA" + "iosfodnn7example", "AKIB" + "IOSFODNN7EXAMPLE"],
        ),
        "google": (["AI" + "za" + _run(35)], ["AI" + "za" + _run(34), "AI" + "zb" + _run(35)]),
        "github": (
            ["gh" + "p_" + _run(36)] + ["gh" + c + "_" + _run(36) for c in "ousr"],
            ["gh" + "p_" + _run(35), "gh" + "x_" + _run(36)],
        ),
        "github_pat": (["github_" + "pat_" + _run(22)], ["github_" + "pat_" + _run(21)]),
        "gitlab": (["gl" + "pat-" + _run(20)], ["gl" + "pat-" + _run(19)]),
        "slack": (
            ["xox" + "b-" + _run(10), "xox" + "p-" + "1234-" + _run(8)],
            ["xox" + "b-" + _run(9), "xox" + "z-" + _run(10)],
        ),
        "hugging": (["hf_" + _run(30)], ["hf_" + _run(29)]),
        "jwt": (
            [jwt_head, jwt_head + "sig", jwt_head + _run(5) + "-_"],
            [
                "eyJ" + _run(9) + ".eyJ" + _run(10) + ".sig",
                "eyJ" + _run(10) + ".eyJ" + _run(9) + ".sig",
                "eyJ" + _run(10) + ".eyJ" + _run(10),
                "eyJ" + _run(10) + ".abc" + _run(10) + ".sig",
            ],
        ),
    }


PEM_ENCODED = "-----BEGIN%20RSA%20PRIVATE%20KEY-----"
PEM_ENCODED_BARE = "-----BEGIN%20PRIVATE%20KEY-----"


def _percent_all(text: str) -> str:
    return "".join(f"%{ord(char):02X}" for char in text)


def _percent_first(text: str) -> str:
    return f"%{ord(text[0]):02X}" + text[1:]


def _placements(
    secret: str, *, host: bool = False, user: bool = True, sip_params: bool = True
) -> list[tuple[str, dict[str, Any], tuple[str, ...]]]:
    """Endpoints that carry ``secret`` in one URL part each, with the words (any one of them) the
    error message must use for that part. ADR 0007 Decision 6b: "anywhere" covers the authority
    (a SIP user part, host labels), the path, query names and values, SIP parameters and headers."""
    cases: list[tuple[str, dict[str, Any], tuple[str, ...]]] = [
        ("ws path", ws_endpoint(url=f"{WS}/{secret}"), ("path",)),
        ("ws path middle", ws_endpoint(url=f"{WS}/v1/task_{secret}/stream"), ("path",)),
        ("ws query value", ws_endpoint(url=f"{WS}?room={secret}"), ("query",)),
        ("ws query later value", ws_endpoint(url=f"{WS}?room=1&label={secret}"), ("query",)),
        ("ws query ; value", ws_endpoint(url=f"{WS}?room=1;label={secret}"), ("query",)),
        ("ws path then query", ws_endpoint(url=f"{WS}/{secret}?room=1&lang=hi"), ("path",)),
        ("ws query name", ws_endpoint(url=f"{WS}?{secret}=1"), ("query",)),
        ("ws query bare name", ws_endpoint(url=f"{WS}?{secret}"), ("query",)),
        ("webrtc path", webrtc_endpoint(url=f"https://{HOST}/api/{secret}/offer"), ("path",)),
        ("webrtc query", webrtc_endpoint(url=f"https://{HOST}/api/offer?s={secret}"), ("query",)),
        (
            "sip header",
            sip_endpoint(uri=f"sip:support@pbx.example.com?Subject={secret}"),
            ("header",),
        ),
    ]
    if user:
        cases.append(
            (
                "sip user part",
                sip_endpoint(uri=f"sip:{secret}@pbx.example.com"),
                ("user", "authority"),
            )
        )
        cases.append(
            (
                "sip user part with port, parameters and headers",
                sip_endpoint(uri=f"sip:{secret}@pbx.example.com:5060;transport=tcp?Subject=help"),
                ("user", "authority"),
            )
        )
    if sip_params:
        cases.append(
            (
                "sip uri parameter",
                sip_endpoint(uri=f"sip:support@pbx.example.com;x-ref={secret}"),
                ("parameter",),
            )
        )
        cases.append(
            (
                "sip uri parameter with a header after it",
                sip_endpoint(uri=f"sip:support@pbx.example.com;x-ref={secret}?Subject=help"),
                ("parameter",),
            )
        )
    if host:
        cases += [
            (
                "ws host label",
                ws_endpoint(url=f"wss://{secret}.example.com/ws"),
                ("host", "author"),
            ),
            (
                "webrtc host label",
                webrtc_endpoint(url=f"https://{secret}.example.com/api/offer"),
                ("host", "author"),
            ),
            (
                "sip host label",
                sip_endpoint(uri=f"sip:support@{secret}.example.com"),
                ("host", "author"),
            ),
            (
                "sip host label with port and parameters",
                sip_endpoint(uri=f"sip:support@{secret}.example.com:5060;transport=tcp"),
                ("host", "author"),
            ),
        ]
    return cases


def _field_messages(error: ValidationError) -> list[str]:
    return [str(entry["msg"]).lower() for entry in error.errors(include_input=False)]


def _assert_pattern_rejected(
    label: str, endpoint: dict[str, Any], words: tuple[str, ...], forbidden: tuple[str, ...]
) -> None:
    """Rejected at the endpoint; the message names the rule and the URL part; no text of the error
    holds the value (ADR 0007 Decision 8)."""
    error = assert_rejected(
        entity_class("AgentTarget"), _target(endpoint=endpoint), field="endpoint"
    )
    messages = _field_messages(error)
    assert any("pattern" in m or "credential" in m for m in messages), (label, messages)
    assert any(any(word in m for word in words) for m in messages), (label, words, messages)
    for text in error_texts(error):
        for value in forbidden:
            assert value not in text, f"{label}: the value is echoed in a validation error"


def test_url_credential_pattern_anywhere_rejected() -> None:
    """P-60 (accepted by the owner), ADR 0007 Decision 6b and invariant I12: a URL is rejected if a
    Decision 5a ``known_credential_prefix`` pattern matches anywhere in it, with the 5a left
    boundary (the start of the text or after a character outside ``[A-Za-z0-9]``), searched in the
    URL as written and in the URL percent-decoded once as UTF-8. The places are the authority (a
    SIP user part, host labels), the path, query names and values, SIP parameters and headers.
    The error names the rule and the URL part (for example "credential pattern in the path"), and
    never the value. The ws, webrtc and sip endpoints are covered here; ``Artifact.uri`` has its
    own test at P1-10a. Proposed message words, as in the ADR example: the message holds
    ``pattern`` or ``credential``, and one word for the part (``path``, ``query``, ``header``,
    ``parameter``, ``user`` or ``authority``, ``host`` or ``authority``)."""
    cases = _pattern_cases()
    host_safe = {"sk-", "aws", "google", "gitlab", "slack"}
    # 1. every pattern, at its minimum length, as written, in every place
    for name, (hits, _misses) in cases.items():
        for secret in hits:
            for label, endpoint, words in _placements(secret, host=name in host_safe):
                _assert_pattern_rejected(
                    f"{name} {label}", endpoint, words, (secret, secret[4:], _percent_all(secret))
                )
    # 2. percent-encoded: decoded once, then searched (the first character, or every character)
    for name, (hits, _misses) in cases.items():
        for secret in hits:
            for encode in (_percent_first, _percent_all):
                encoded = encode(secret)
                for label, endpoint, words in _placements(encoded):
                    _assert_pattern_rejected(
                        f"{name} encoded {label}", endpoint, words, (secret, secret[4:], encoded)
                    )
    # 3. the PEM header contains spaces, so only its percent-encoded form can be a URL
    for pem in (PEM_ENCODED, PEM_ENCODED_BARE, "-----BEGIN%20EC%20PRIVATE%20KEY-----"):
        for label, endpoint, words in _placements(pem, user=False, sip_params=False):
            _assert_pattern_rejected(f"pem {label}", endpoint, words, ("PRIVATE KEY", pem))
    for text in ("-----BEGIN%20PUBLIC%20KEY-----", "-----BEGIN%20private%20key-----"):
        for label, endpoint, _words in _placements(text, user=False, sip_params=False):
            assert _accepted_endpoint(endpoint) is not None, (text, label)
    # 4. near misses (too short, outside the class, a different prefix) are not credentials
    for name, (_hits, misses) in cases.items():
        for text in misses:
            for label, endpoint, _words in _placements(text):
                assert _accepted_endpoint(endpoint) is not None, (name, text, label)
    # 5. the patterns are case-sensitive and a separator ends a character run
    for text in (
        "SK-" + _run(20),
        "Sk-" + _run(20),
        "akia" + "IOSFODNN7EXAMPLE",
        "GHP_" + _run(36),
    ):
        for label, endpoint, _words in _placements(text):
            assert _accepted_endpoint(endpoint) is not None, (text, label)
    for text in (
        "sk-" + _run(10) + "/" + _run(10),
        "sk-" + _run(10) + "." + _run(10),
        "sk-" + _run(10) + "%2F" + _run(10),
        "sk-" + _run(10) + "=" + _run(10),
    ):
        for endpoint in (
            ws_endpoint(url=f"{WS}/{text}"),
            ws_endpoint(url=f"{WS}?room={text}"),
            sip_endpoint(uri=f"sip:support@pbx.example.com?Subject={text}"),
        ):
            assert _accepted_endpoint(endpoint) is not None, text
    # 6. the 5a left boundary, in the text as written and in the decoded text
    openai, aws = cases["sk-"][0][0], cases["aws"][0][0]
    for separator in (
        "/",
        "_",
        "-",
        ".",
        "=",
        ":",
        "~",
        "+",
        "%2F",
        "%5F",
        "%2D",
        "%2E",
        "%3D",
        "%3A",
        "%20",
    ):
        for secret in (openai, aws):
            for endpoint in (
                ws_endpoint(url=f"{WS}/seg{separator}{secret}"),
                ws_endpoint(url=f"{WS}?room=a{separator}{secret}"),
                sip_endpoint(uri=f"sip:support@pbx.example.com?Subject=a{separator}{secret}"),
            ):
                _rejected_endpoint(endpoint)
    for name, (hits, _misses) in cases.items():
        for secret in hits:
            for prefix in ("x", "Z", "9", "%78", "%39", "%5A"):
                for endpoint in (
                    ws_endpoint(url=f"{WS}/{prefix}{secret}"),
                    ws_endpoint(url=f"{WS}?room={prefix}{secret}"),
                    webrtc_endpoint(url=f"https://{HOST}/api/{prefix}{secret}"),
                    sip_endpoint(uri=f"sip:{prefix}{secret}@pbx.example.com"),
                ):
                    assert _accepted_endpoint(endpoint) is not None, (name, prefix)
            # a prefix that is itself written as an escape is part of the decoded text
            for endpoint in (
                ws_endpoint(url=f"{WS}/a{_percent_first(secret)}"),
                sip_endpoint(uri=f"sip:a{_percent_first(secret)}@pbx.example.com"),
            ):
                assert _accepted_endpoint(endpoint) is not None, name
    # 7. decoded once, not twice: a double-encoded pattern is not decoded to the pattern
    for secret in (openai, aws):
        double = "%25" + _percent_first(secret)[1:]
        for endpoint in (
            ws_endpoint(url=f"{WS}/{double}"),
            ws_endpoint(url=f"{WS}?room={double}"),
            webrtc_endpoint(url=f"https://{HOST}/api/{double}"),
            sip_endpoint(uri=f"sip:{double}@pbx.example.com"),
        ):
            assert _accepted_endpoint(endpoint) is not None, double
    # 8. decoding is over the whole URL, as UTF-8: a malformed escape or invalid UTF-8 anywhere
    for bad in ("%zz", "%4", "%", "%ff", "%C3", "%E0%A4", "%C0%AF"):
        for endpoint in (
            ws_endpoint(url=f"{WS}/{bad}"),
            ws_endpoint(url=f"{WS}/a{bad}/b"),
            ws_endpoint(url=f"{WS}?room={bad}"),
            webrtc_endpoint(url=f"https://{HOST}/api/{bad}"),
            sip_endpoint(uri=f"sip:{bad}@pbx.example.com"),
            sip_endpoint(uri=f"sip:support@pbx.example.com?Subject={bad}"),
        ):
            _rejected_endpoint(endpoint)
    for good in ("%C3%A9", "%E0%A4%B9%E0%A4%BF", "%2F", "%20", "%7E"):
        for endpoint in (
            ws_endpoint(url=f"{WS}/{good}"),
            ws_endpoint(url=f"{WS}?room={good}"),
            webrtc_endpoint(url=f"https://{HOST}/api/{good}"),
        ):
            assert _accepted_endpoint(endpoint) is not None, good
    # 9. the JSON entry point applies the same rules and keeps the value out of its errors
    target = entity_class("AgentTarget")
    for endpoint in (
        ws_endpoint(url=f"{WS}/{openai}"),
        ws_endpoint(url=f"{WS}?room={_percent_first(openai)}"),
        sip_endpoint(uri=f"sip:{aws}@pbx.example.com"),
    ):
        body = {**_target(endpoint=endpoint), "content_digest": PLACEHOLDER_DIGEST}
        with pytest.raises(ValidationError) as via_json:
            target.model_validate_json(json.dumps(body))
        for text in error_texts(via_json.value):
            assert openai not in text
            assert aws not in text
    # 10. endpoints without a pattern stay valid
    for endpoint in (
        ws_endpoint(),
        ws_endpoint(url=f"{WS}/v1/agents/support?room=support&lang=hi-IN&retries=3"),
        webrtc_endpoint(),
        sip_endpoint(),
        sip_endpoint(uri="sip:support@pbx.example.com;transport=tcp?Subject=help"),
    ):
        assert _accepted_endpoint(endpoint) is not None


HIGH_ENTROPY_SEGMENTS = (
    "Zq8RtW3nLp0XcV7bYh2KdF5sMa9GjE1u",  # 32 characters, three classes, 32 distinct
    "Qm3Xz9KdL2vNb7TpR5WcHy1JfA8sEg4UoI6",  # 35 characters
    "rec9X1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2w",  # a recording-ID shape, 40 characters
    "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo2NDhhMw==",  # base64 with padding
    "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0UvWxYz-_ABcdEf",  # url-safe base64 alphabet
)


def test_url_high_entropy_segment_accepted() -> None:
    """P-60 (accepted by the owner), ADR 0007 Decision 6b and invariant I12: the
    ``high_entropy_token`` reason of Decision 5a is not applied to URLs. Metadata keeps and flags
    such a value, but a URL rule rejects the whole record, and ordinary opaque IDs (recording IDs,
    CDN paths) look the same, so only the named patterns reject. A long mixed-case run in the path,
    in a query value, in a SIP user part or parameter and in a host label is accepted, while the
    same run in a query parameter *name* from the credential set is still rejected by name."""
    credentials = api("credentials")
    for segment in HIGH_ENTROPY_SEGMENTS:
        # the premise: the metadata rule does flag it, and no known pattern matches it
        assert credentials.credential_reason(segment) == credentials.HIGH_ENTROPY_TOKEN, segment
        for label, endpoint, _words in _placements(segment, host=False):
            assert _accepted_endpoint(endpoint) is not None, (segment, label)
        for endpoint in (
            ws_endpoint(url=f"{WS}/{segment}/{segment}"),
            ws_endpoint(url=f"{WS}?a={segment}&b={segment}"),
            webrtc_endpoint(url=f"https://{HOST}/cdn/{segment}.mp3"),
            sip_endpoint(uri=f"sip:{segment}@pbx.example.com;x-ref={segment}?Subject={segment}"),
        ):
            assert _accepted_endpoint(endpoint) is not None, segment
        # percent-encoded: still not a pattern, so still accepted
        encoded = _percent_all(segment)
        for endpoint in (
            ws_endpoint(url=f"{WS}/{encoded}"),
            ws_endpoint(url=f"{WS}?room={encoded}"),
        ):
            assert _accepted_endpoint(endpoint) is not None, segment
        # a credential parameter name is still a credential, whatever its value
        _rejected_endpoint(ws_endpoint(url=f"{WS}?token={segment}"))
        _rejected_endpoint(sip_endpoint(uri=f"sip:support@pbx.example.com?sig={segment}"))
    for hostname in (HIGH_ENTROPY_SEGMENTS[0], HIGH_ENTROPY_SEGMENTS[1]):
        assert _accepted_endpoint(ws_endpoint(url=f"wss://{hostname}.example.com/ws")) is not None


def test_url_pattern_fitting_slug_is_rejected_known_false_positive() -> None:
    """ADR 0007 Decision 6b, "Known false positive", documented as a positive/negative pair
    (proposed name, a second test for the rule of ``test_url_high_entropy_segment_accepted``). A
    path segment that happens to fit a Decision 5a pattern, such as ``sk-`` followed by 20 or more
    letters, digits, ``_`` or ``-``, is rejected: refusing is the safe failure, and the message
    names the rule and the part so that the user can see why. Its neighbours that do not fit a
    pattern (19 characters, another prefix, an alphanumeric before ``sk-``, a separator inside the
    run) and a high-entropy run of the same length are accepted."""
    slug_rejected = "sk-" + "my-product-launch-notes"  # 23 characters after the prefix
    for endpoint, words in (
        (ws_endpoint(url=f"{WS}/blog/{slug_rejected}"), ("path",)),
        (webrtc_endpoint(url=f"https://{HOST}/docs/{slug_rejected}/offer"), ("path",)),
    ):
        _assert_pattern_rejected("slug", endpoint, words, (slug_rejected,))
    for slug_accepted in (
        "sk-" + "my-product-launch",  # 17 after the prefix: too short
        "sk-" + "my-product-launc",  # 16
        "task-" + "my-product-launch-notes",  # a letter before ``sk-``
        "sk-" + "my-product/launch-notes",  # a separator ends the run
        "sk" + "_my-product-launch-notes",  # another prefix
        "SK-" + "MY-PRODUCT-LAUNCH-NOTES",  # patterns are case-sensitive
    ):
        for endpoint in (
            ws_endpoint(url=f"{WS}/blog/{slug_accepted}"),
            webrtc_endpoint(url=f"https://{HOST}/docs/{slug_accepted}/offer"),
        ):
            assert _accepted_endpoint(endpoint) is not None, slug_accepted
    assert _accepted_endpoint(ws_endpoint(url=f"{WS}/blog/{HIGH_ENTROPY_SEGMENTS[0]}")) is not None


def test_provider_project_agent_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed ID, MachineKey, closed-set
    member, BCP-47 tag and
    discriminator tag on ``ProviderConfigRef``, ``Project`` and ``AgentTarget`` does not appear in
    ``str``, ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``, through
    ``model_validate`` and ``model_validate_json``, and the error is still located at the field.
    Proposed name (SCOPE section 5, P1-06)."""
    mark = QUOTE_MARK
    project = entity_class("Project")
    target = entity_class("AgentTarget")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("typed ID", project, raw_document("Project", id=marked_id("prj")), "id"),
        (
            "MachineKey",
            project,
            raw_document("Project", provider_configs=[provider_ref(provider=marked_key())]),
            "provider",
        ),
        ("MachineKey", _provider(), provider_ref(model=marked_key()), "model"),
        ("config_ref", _provider(), provider_ref(config_ref="nocolon" + mark), "config_ref"),
        ("typed ID", target, _target(id=marked_id("agt")), "id"),
        ("BCP-47", target, _target(languages=[marked_tag()]), "languages"),
        (
            "ClosedSet",
            target,
            _target(declared_capabilities=[marked_set()]),
            "declared_capabilities",
        ),
        (
            "ClosedSet",
            target,
            _target(declared_stack=[{"stage": marked_set(), "provider": "sarvam", "model": "x"}]),
            "stage",
        ),
        (
            "MachineKey",
            target,
            _target(declared_stack=[{"stage": "stt", "provider": marked_key(), "model": "x"}]),
            "provider",
        ),
        (
            "discriminator",
            target,
            _target(endpoint={**ws_endpoint(), "transport": marked_set()}),
            "endpoint",
        ),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, mark, field=field, label=label)
