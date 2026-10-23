# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-07: Persona, TestProfile, TestProfileRef and the placeholder set (ADR 0005, 0006, 0009).

Assumed public API (docs/phase1/api-surface.md section 19): ``tracer_evalschema.persona.Persona``
(versioned; ``language: SupportedLanguage``, ``accent: HumanLabel | None``),
``tracer_evalschema.profile.TestProfile`` (versioned; data fields ``name``, ``dob``,
``account_number``, each marked ``x-pii`` in the JSON Schema), ``TestProfileRef{id, version,
content_digest?}``, ``profile_placeholder_fields() -> frozenset[str]`` and
``find_literal_profile_values(model, profile) -> list[LiteralProfileValue{path, field}]``.

Strings that T-DRIFT bans (excluded languages, the mixed-language alias) are assembled in
``_support_b2`` and never spelled out here.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import PACKAGE_DIR, ROOT, api
from ._support_b2 import (
    ENTITY_FIELDS,
    EXCLUDED_LANGUAGE_TAGS,
    MIXED_ALIAS_TAG,
    MIXED_FIELD_NAME,
    MIXED_FIELD_PATTERN,
    VERSIONED_FIELDS,
    assert_rejected,
    assert_rejected_name,
    cls,
    contains_key,
    entity_class,
    error_texts,
    fields_of,
    jsonschema_module,
    make,
    mixed_alias_message,
    new_id,
    property_names,
    raw_document,
    schema_accepts,
    schema_of,
    seal,
    secret_like,
    walk_nodes,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    marked_id,
    marked_tag,
)

PLAIN = {"scheme": "sha256-jcs/v1", "value": "a" * 64}
KEYED = {"scheme": "hmac-sha256-jcs/v1", "value": "b" * 64, "key_ref": {"key_id": "tracer-key-1"}}


def _persona() -> Any:
    return entity_class("Persona")


def _profile() -> Any:
    return entity_class("TestProfile")


def _ref_model() -> Any:
    return cls("profile", "TestProfileRef")


def _data_fields(model: Any) -> set[str]:
    return fields_of(model) - ENTITY_FIELDS - VERSIONED_FIELDS


# ---------------------------------------------------------------------------------- Persona


def test_persona_has_no_codemix_field() -> None:
    """ADR 0005 I19: no mixed-language field or type anywhere (Persona, and by walk every model
    of the package); a field of that kind is rejected as unknown."""
    persona = _persona()
    names = fields_of(persona) | property_names(persona, "validation")
    names |= property_names(persona, "serialization")
    assert not [n for n in names if MIXED_FIELD_PATTERN.search(n)]
    assert make("Persona").language == "hi-IN"
    for value in ("hi-IN", True, ["hi", "en"]):
        extra: dict[str, Any] = {MIXED_FIELD_NAME: value}
        caught = assert_rejected_name("Persona", **extra)
        assert any(e["type"] == "extra_forbidden" for e in caught.errors())
    for model in api("registry").model_classes():
        assert not MIXED_FIELD_PATTERN.search(model.__name__), model.__name__
        assert not [f for f in model.model_fields if MIXED_FIELD_PATTERN.search(f)], model.__name__


def test_persona_language_supported_only() -> None:
    """ADR 0005 Decision 6: ``Persona.language`` is a ``SupportedLanguage``: one of the nine
    languages in native script, regions IN, US, GB, canonical case. Pydantic and JSON Schema
    agree on the rejected tags."""
    persona = _persona()
    for tag, canonical in (
        ("hi-IN", "hi-IN"),
        ("HI-in", "hi-IN"),
        ("en-US", "en-US"),
        ("en-GB", "en-GB"),
        ("pa-Guru-IN", "pa-Guru-IN"),
        ("ml", "ml"),
        ("gu-IN", "gu-IN"),
        ("mr", "mr"),
        ("te", "te"),
        ("ta", "ta"),
        ("kn", "kn"),
        ("en", "en"),
    ):
        assert make("Persona", language=tag).language == canonical
    rejected = [
        "hi-Latn",
        "ta-Latn",
        MIXED_ALIAS_TAG,
        *EXCLUDED_LANGUAGE_TAGS,
        "pa-" + "Arab",
        "ta-LK",
        "en-AU",
        "hi-x-foo",
        "hi-u-nu-deva",
        "und",
        "mul",
        "zxx",
        "hindi",
        "hi_IN",
        " hi-IN",
        "hi-IN ",
        "hi-IN\n",
        "",
        None,
        5,
        ["hi-IN"],
    ]
    jsonschema = jsonschema_module()
    validator = jsonschema.Draft202012Validator(schema_of(persona))
    for bad_tag in rejected:
        assert_rejected_name("Persona", language=bad_tag, field="language")
        if isinstance(bad_tag, str):
            assert not validator.is_valid(raw_document("Persona", language=bad_tag)), bad_tag
    assert mixed_alias_message() in str(assert_rejected_name("Persona", language=MIXED_ALIAS_TAG))
    with pytest.raises(ValidationError):
        persona.model_validate(
            {k: v for k, v in raw_document("Persona").items() if k != "language"}
        )


def test_persona_accent_is_a_human_label() -> None:
    """Proposed name (ADR 0005 Consequences: accent is a ``HumanLabel``, not a language tag):
    optional; a label is never trimmed and has no invisible or control characters, but ZWJ and
    ZWNJ are kept."""
    persona = _persona()
    for good in ("Delhi", "Pune Marathi", "ണ്\u200d കേരള", "क्\u200cष", "Hyderabadi (Deccan)"):
        assert make("Persona", accent=good).accent == good
    assert make("Persona", accent=None).accent is None
    without = {k: v for k, v in raw_document("Persona").items() if k != "accent"}
    assert (
        persona.model_validate({**without, "content_digest": _digest_for(without)}).accent is None
    )
    for bad in (
        "",
        " Delhi",
        "Delhi ",
        "\tDelhi",
        "Del\u200bhi",
        "Del\ufeffhi",
        "Del\u202ehi",
        "Del\u2066hi",
        "Del\u200ehi",
        "Del\nhi",
        "Del\x7fhi",
        "Del\x85hi",
        "Del\u00adhi",
        "Del\u2028hi",
        7,
        ["Delhi"],
    ):
        assert_rejected_name("Persona", accent=bad, field="accent")


def _digest_for(body: dict[str, Any]) -> dict[str, Any]:
    """The plain digest of a persona document that omits the optional ``accent``."""
    return dict(seal(_persona(), body)["content_digest"])


# ------------------------------------------------------------------------------- TestProfile


def test_test_profile_pii_marked() -> None:
    """ADR 0006 Decision 2: every TestProfile data field holds PII and carries the JSON Schema
    annotation ``x-pii: true`` (validation and serialization mode); no other property does."""
    profile = _profile()
    data = _data_fields(profile)
    assert {"name", "dob"} <= data, "the ADR examples {{profile.name}} and {{profile.dob}}"
    for mode in ("validation", "serialization"):
        schema = schema_of(profile, mode)
        props = schema["properties"]
        assert set(props) == data | ENTITY_FIELDS | VERSIONED_FIELDS
        for field in data:
            assert props[field].get("x-pii") is True, f"{field} ({mode})"
        for field in ENTITY_FIELDS | VERSIONED_FIELDS:
            assert "x-pii" not in props[field], f"{field} ({mode})"
        marked = {
            name
            for node in walk_nodes(schema)
            for name, prop in node.get("properties", {}).items()
            if isinstance(prop, dict) and prop.get("x-pii")
        }
        assert marked == data
    # a profile loads and keeps its values byte for byte
    loaded = make("TestProfile", name="आशा वर्मा")
    assert loaded.name == "आशा वर्मा"


def test_test_profile_has_no_secret_like_fields() -> None:
    """Proposed name. A profile holds identity data, not credentials: no data field name matches
    the credential denylist (ADR 0007 Decision 3)."""
    for name in _data_fields(_profile()):
        assert not secret_like(name), name
    for prop in property_names(_profile(), "validation") | property_names(
        _profile(), "serialization"
    ):
        assert not secret_like(prop), prop


def test_test_profile_ref_has_no_pii() -> None:
    """ADR 0006 I2 (C walk): the reference is exactly ``{id, version, content_digest?}``; nothing
    in its schema is PII-marked or named like a profile field; a dump holds no profile value."""
    ref_model = _ref_model()
    profile_fields = _data_fields(_profile())
    assert fields_of(ref_model) == {"id", "version", "content_digest"}
    for mode in ("validation", "serialization"):
        schema = schema_of(ref_model, mode)
        assert not contains_key(schema, "x-pii")
        assert not profile_fields & property_names(ref_model, mode)
        assert set(schema["properties"]) == {"id", "version", "content_digest"}
    profile = make("TestProfile")
    loaded = ref_model.model_validate({"id": str(profile.id), "version": profile.version})
    text = loaded.model_dump_json()
    for field in profile_fields:
        assert str(getattr(profile, field)) not in text
    for field in profile_fields:
        with pytest.raises(ValidationError):
            ref_model.model_validate({**json.loads(text), field: "anything"})


def test_test_profile_ref_shape_and_digest() -> None:
    """Proposed name (ADR 0006 Decision 1 and 6, ADR 0009 Decision 4): ``{id, version,
    content_digest?}``; ``id`` is a ``TestProfileId``; ``version`` is required (>= 1); the digest
    is optional and is a plain ``sha256-jcs/v1`` or a keyed ``hmac-sha256-jcs/v1`` ContentDigest."""
    ref_model = _ref_model()
    ids = api("ids")
    errors = api("errors")
    tpf = str(ids.TestProfileId.new())
    plain = ref_model.model_validate({"id": tpf, "version": 3, "content_digest": PLAIN})
    assert (str(plain.id), plain.version, plain.content_digest.scheme) == (tpf, 3, "sha256-jcs/v1")
    keyed = ref_model.model_validate({"id": tpf, "version": 1, "content_digest": KEYED})
    assert keyed.content_digest.key_ref.key_id == "tracer-key-1"
    bare = ref_model.model_validate({"id": tpf, "version": 1})
    assert bare.content_digest is None
    assert isinstance(bare.id, ids.TestProfileId)
    assert json.loads(
        ref_model.model_validate_json(plain.model_dump_json()).model_dump_json()
    ) == json.loads(plain.model_dump_json())
    wrong_kind = (
        new_id("per"),
        new_id("run"),
        new_id("evl"),
        str(ids.TestProfileId.new().uuid),
        "",
    )
    for bad_id in wrong_kind:
        with pytest.raises(ValidationError):
            ref_model.model_validate({"id": bad_id, "version": 1})
    for bad_version in (None, 0, -1, 1.5, "1", True):
        with pytest.raises(ValidationError):
            ref_model.model_validate({"id": tpf, "version": bad_version})
    for missing in ("id", "version"):
        with pytest.raises(ValidationError):
            ref_model.model_validate(
                {k: v for k, v in {"id": tpf, "version": 1}.items() if k != missing}
            )
    with pytest.raises(errors.UnknownDigestScheme):
        ref_model.model_validate(
            {"id": tpf, "version": 1, "content_digest": {**PLAIN, "scheme": "md5"}}
        )
    for bad_digest in (
        {**PLAIN, "key_ref": {"key_id": "k"}},  # plain scheme forbids key_ref
        {"scheme": "hmac-sha256-jcs/v1", "value": "b" * 64},  # keyed scheme needs key_ref
        {**PLAIN, "value": "A" * 64},
        {**PLAIN, "value": "a" * 63},
        {"scheme": "sha256-jcs/v1"},
    ):
        with pytest.raises(ValidationError):
            ref_model.model_validate({"id": tpf, "version": 1, "content_digest": bad_digest})
    schema = schema_of(ref_model)
    assert set(schema["required"]) == {"id", "version"}
    assert schema["properties"]["id"]["pattern"].startswith("^tpf_")
    assert schema_accepts(ref_model, {"id": tpf, "version": 1})
    assert not schema_accepts(ref_model, {"id": new_id("per"), "version": 1})
    assert not schema_accepts(ref_model, {"id": tpf})


def test_snapshot_embedded_entity_schemas_carry_no_pii_marker() -> None:
    """Proposed name (ADR 0006 I1 prepared; the ConfigSnapshot walk itself is P1-12). The
    entities and references a ConfigSnapshot embeds hold no ``x-pii`` field: Evaluator, Persona,
    MetricDefinition, MetricBinding, Rubric and TestProfileRef."""
    for name in ("Evaluator", "Persona", "MetricDefinition", "MetricBinding", "Rubric"):
        model = entity_class(name)
        for mode in ("validation", "serialization"):
            assert not contains_key(schema_of(model, mode), "x-pii"), (name, mode)
    assert not contains_key(schema_of(_ref_model()), "x-pii")
    assert contains_key(schema_of(_profile()), "x-pii"), "positive control"


# --------------------------------------------------------------------------- the policy text


def _policy_text() -> str:
    candidates = [
        PACKAGE_DIR / "SCHEMA_POLICY.md",
        ROOT / "SCHEMA_POLICY.md",
        ROOT / "docs" / "SCHEMA_POLICY.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.fail(
        "SCHEMA_POLICY.md does not exist yet (P1-20a); expected at one of "
        + ", ".join(str(p.relative_to(ROOT)) for p in candidates)
    )


def test_policy_documents_low_entropy_digest_limitation() -> None:
    """ADR 0006 Decision 6 / I12, A-26: SCHEMA_POLICY.md states that a plain digest of a
    low-entropy profile in internal data can be brute-forced, and that shared exports never
    carry such a plain digest (they drop or key it). Gate-bound: green when P1-20a writes the
    file."""
    text = _policy_text().lower()
    flat = re.sub(r"\s+", " ", text)
    assert re.search(r"low[- ]entropy", flat), "the limitation must be named"
    assert re.search(r"brute[- ]?forc", flat)
    assert "internal" in flat
    assert re.search(r"plain (sha-?256|digest)", flat)
    assert re.search(r"shared export", flat)
    assert re.search(r"(drop|omit|absent)", flat)
    assert re.search(r"(keyed|hmac)", flat)


# ------------------------------------------------------------------------------ placeholders


def test_profile_placeholder_fields_are_the_profile_data_fields() -> None:
    """P-16 / SCOPE P1-07: the set of TestProfile field names usable as ``{{profile.<field>}}``
    is exactly the TestProfile data fields (not the entity fields), exposed as a frozenset."""
    fields = api("profile").profile_placeholder_fields()
    assert isinstance(fields, frozenset)
    assert fields == _data_fields(_profile())
    assert {"name", "dob"} <= fields
    assert not fields & (ENTITY_FIELDS | VERSIONED_FIELDS)
    assert all(isinstance(f, str) and f == f.lower() and f.isidentifier() for f in fields)


def _evaluator_with_text(**over: Any) -> Any:
    return make("Evaluator", **over)


def _profile_ref_of(profile: Any) -> dict[str, Any]:
    """A reference to ``profile`` (revision 8, N8: an evaluator whose text holds a profile
    placeholder must reference a ``test_profile``)."""
    return {"id": str(profile.id), "version": profile.version}


def test_literal_profile_value_in_free_text_is_flagged_with_path_not_value() -> None:
    """Proposed name (ADR 0006 Decision 9; the snapshot-level ``lint_profile_values_in_free_text``
    is P1-12 and wraps this seam). A literal value equal to a profile field is reported by JSON
    Pointer path and field name, never by value; placeholders and clean text give no hit. Revision
    8 (N8): both evaluators carry ``{{profile.name}}``, so they reference a ``test_profile``."""
    profile_module = api("profile")
    profile = make("TestProfile")
    turns = [
        {"index": 0, "text": "मेरा नाम {{profile.name}} है", "language": "hi-IN"},
        {"index": 1, "text": "My account number is 4400123412 please", "language": "en-IN"},
        {"index": 2, "text": "I was born on 1990-04-12.", "language": "en-IN"},
        {"index": 3, "text": "Nothing personal here.", "language": "en-IN"},
    ]
    evaluator = _evaluator_with_text(
        instructions="Introduce yourself as Asha Verma and ask for a refund.",
        scripted_turns=turns,
        test_profile=_profile_ref_of(profile),
    )
    hits = profile_module.find_literal_profile_values(evaluator, profile)
    pairs = [(h.path, h.field) for h in hits]
    assert pairs == [
        ("/instructions", "name"),
        ("/scripted_turns/1/text", "account_number"),
        ("/scripted_turns/2/text", "dob"),
    ]
    assert all(set(h.model_dump()) == {"path", "field"} for h in hits)
    rendered = " ".join([repr(hits), str(hits), *(h.model_dump_json() for h in hits)])
    for secret in ("Asha Verma", "4400123412", "1990-04-12"):
        assert secret not in rendered
    clean = _evaluator_with_text(
        instructions="Introduce yourself as {{profile.name}} and ask for a refund.",
        scripted_turns=[turns[0], {**turns[3], "index": 1}],
        test_profile=_profile_ref_of(profile),
    )
    assert profile_module.find_literal_profile_values(clean, profile) == []
    devanagari = make("TestProfile", name="आशा वर्मा")
    hindi = _evaluator_with_text(
        scripted_turns=[{"index": 0, "text": "मैं आशा वर्मा बोल रही हूँ", "language": "hi-IN"}]
    )
    found = profile_module.find_literal_profile_values(hindi, devanagari)
    assert [(h.path, h.field) for h in found] == [("/scripted_turns/0/text", "name")]


def test_profile_value_lint_is_total_on_other_models() -> None:
    """Proposed name. The seam walks the JSON-mode dump of any model, so it also works on a
    Persona or a Rubric, and a profile with no overlapping value gives no hit."""
    profile_module = api("profile")
    profile = make("TestProfile")
    for name in ("Persona", "Rubric", "MetricDefinition"):
        assert profile_module.find_literal_profile_values(make(name), profile) == []
    leaking = make("Persona", accent="Asha Verma")
    hits = profile_module.find_literal_profile_values(leaking, profile)
    assert [(h.path, h.field) for h in hits] == [("/accent", "name")]


def test_every_profile_field_is_usable_as_a_placeholder() -> None:
    """Proposed name. Each name in ``profile_placeholder_fields()`` is accepted inside
    ``{{profile.<field>}}`` and an unknown name is rejected (the full matrix is in
    test_evaluator.py). Revision 8 (N8): a placeholder needs a ``test_profile``, so the evaluator
    references one; the unknown-name rejection is checked with the profile present, so it cannot
    be a rejection for the missing profile."""
    fields = api("profile").profile_placeholder_fields()
    profile = make("TestProfile")
    reference = _profile_ref_of(profile)
    for field in fields:
        text = "Use {{profile." + field + "}} now."
        assert make("Evaluator", instructions=text, test_profile=reference).instructions == text
    bad = {
        **raw_document("Evaluator", test_profile=reference),
        "instructions": "Use {{profile.unknown_field}}",
    }
    caught = assert_rejected(entity_class("Evaluator"), bad, field="instructions")
    assert "unknown profile placeholder field" in str(caught)
    # owner decision (placeholder message): the typed name is not shown anywhere
    shown = [
        str(caught),
        repr(caught),
        repr(caught.errors(include_input=False)),
        caught.json(include_input=False),
        repr([e.get("ctx") for e in caught.errors()]),
    ]
    for text in shown:
        assert "unknown_field" not in text, text


# ================================================================================ revision 8


def _lint(model: Any, profile: Any) -> list[tuple[str, str]]:
    hits = api("profile").find_literal_profile_values(model, profile)
    return [(h.path, h.field) for h in hits]


def _note_persona(texts: list[str]) -> Any:
    """A persona whose ``metadata`` holds one free-text note per text, under keys n00, n01, ..."""
    return make("Persona", metadata={f"n{i:02d}": text for i, text in enumerate(texts)})


def _expect_hits(profile: Any, cases: list[tuple[str, str | None]]) -> None:
    """Each case is (text, field that must be flagged or None). One persona holds all notes, so a
    wrong flag is reported together with the text that caused it."""
    persona = _note_persona([text for text, _field in cases])
    flagged: dict[str, list[str]] = {}
    for path, hit_field in _lint(persona, profile):
        flagged.setdefault(path, []).append(hit_field)
    wrong = []
    for index, (text, field) in enumerate(cases):
        got = flagged.get(f"/metadata/n{index:02d}", [])
        if got != ([field] if field else []):
            wrong.append((text, field, got))
    assert not wrong, f"unexpected lint result for (text, expected, got): {wrong}"


def _native(zero: int, text: str) -> str:
    """``text`` with every ASCII digit replaced by the decimal digit of the script whose zero is
    the code point ``zero`` (Nd digits run 0-9 consecutively from it)."""
    return "".join(chr(zero + int(c)) if c.isdigit() else c for c in text)


# the zero digit of each script of the nine languages, plus Arabic-Indic and full-width digits
NATIVE_ZEROS = {
    "Devanagari": 0x0966,
    "Gurmukhi": 0x0A66,
    "Gujarati": 0x0AE6,
    "Tamil": 0x0BE6,
    "Telugu": 0x0C66,
    "Kannada": 0x0CE6,
    "Malayalam": 0x0D66,
    "Arabic-Indic": 0x0660,
    "full-width": 0xFF10,
}


def test_profile_lint_folding_and_token_bounds() -> None:
    """Resolution 10 (ADR 0006 Decision 9a): every string leaf is compared with every non-empty
    profile value after folding both sides (NFC, ``casefold``, NFC, every decimal digit to ASCII,
    white-space runs to one space). A hit needs token bounds: where the value's first (last) code
    point is a letter, mark or digit, the code point before (after) it must be absent or outside
    those categories. There is no length threshold. Stored text is never changed. Proposed name."""
    profile = make("TestProfile")  # Asha Verma, 1990-04-12, 4400123412
    name, account, dob = "name", "account_number", "dob"
    cases: list[tuple[str, str | None]] = [
        # case folding
        ("ASHA VERMA", name),
        ("asha verma", name),
        ("aSHa vERMa", name),
        # white space: runs collapse, any white space counts, leading and trailing is not part
        ("Asha  Verma", name),
        ("Asha\nVerma", name),
        ("Asha\t " + chr(0xA0) + "Verma", name),
        ("Asha" + chr(0x2003) + "Verma", name),
        ("  Asha Verma  ", name),
        # native decimal digits fold to ASCII, in every script
        *((_native(zero, "4400123412"), account) for zero in NATIVE_ZEROS.values()),
        (_native(0x0966, "1990-04-12"), dob),
        (_native(0x0966, "4400123412") + _native(0x0966, "1"), None),
        # token bounds: a neighbour that is a letter, mark or digit blocks the hit
        ("xAsha Verma", None),
        ("Asha Vermas", None),
        (chr(0xC1) + "Asha Verma", None),
        ("Asha Verma" + chr(0x301), None),
        ("Asha Verma2", None),
        ("144001234125", None),
        ("a4400123412", None),
        ("4400123412b", None),
        ("44001234120", None),
        # any other neighbour (punctuation, white space, symbols) is a boundary
        ("(Asha Verma)", name),
        ("Asha Verma.", name),
        ("Name:Asha Verma, hello", name),
        ("-Asha Verma-", name),
        ("_Asha Verma_", name),
        ("ID-4400123412-X", account),
        ("[4400123412]", account),
        # near misses are not hits (other formats of the same date are a documented limitation)
        ("Asha-Verma", None),
        ("Asha Varma", None),
        ("Asha", None),
        ("12/04/1990", None),
        ("1990-4-12", None),
        ("{{profile.name}} and {{profile.dob}}", None),
        # one hit per (path, field), whatever the number of occurrences; a later occurrence with
        # good bounds counts after an earlier one that has none
        ("Asha Verma met Asha Verma", name),
        ("xAsha Verma and Asha Verma", name),
        ("Asha Vermas and Asha Verma", name),
    ]
    _expect_hits(profile, cases)
    # two fields in one text: two hits at one path, in field order
    both = _note_persona(["Asha Verma, account 4400123412"])
    assert _lint(both, profile) == [
        ("/metadata/n00", "account_number"),
        ("/metadata/n00", "name"),
    ]
    # leaves other than strings are not compared, and neither are keys
    odd = make(
        "Persona",
        metadata={"number": 4400123412, "Asha Verma": "ok", "list": ["x", "Asha Verma"]},
    )
    assert _lint(odd, profile) == [("/metadata/list/1", "name")]
    # hex digests and ids are not hit by a short value (token bounds), and nothing is skipped
    digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    short = make("TestProfile", name="d6", dob="c15", account_number="7")
    assert _lint(make("Persona", metadata={"digest": digest}), short) == []
    # no length threshold: a one- or two-character value is found where it stands alone
    tiny = make("TestProfile", name="Al", dob="7", account_number="99")
    _expect_hits(
        tiny,
        [
            ("call Al now", name),
            ("Alan", None),
            ("Mal", None),
            ("press 7 for sales", dob),
            ("press 77 now", None),
            ("a7", None),
            ("7a", None),
            ("(7)", dob),
            (_native(0x0966, "7"), dob),
            ("99 balloons", account),
            ("x99", None),
        ],
    )
    # token bounds apply only where the value starts or ends with a letter, mark or digit
    phone = make("TestProfile", account_number="+91 98765 43210")
    _expect_hits(
        phone,
        [
            ("call x+91 98765 43210 now", account),
            ("call +91 98765 43210 now", account),
            ("call +91 98765 432101", None),
            ("call +91 98765 43210", account),
        ],
    )


def _chars(*code_points: int) -> str:
    return "".join(chr(cp) for cp in code_points)


def test_profile_lint_folds_unicode_forms() -> None:
    """Resolution 10: the fold is NFC, then ``str.casefold()`` (not ``lower()``: sharp s becomes
    ``ss``), then NFC again. A value stored in one Unicode form is found in another form, in Latin
    and in Indic text (the Devanagari letter QA, U+0958, is a composition exclusion: NFC turns it
    into KA + NUKTA). Stored text is never changed. The source is ASCII: the forms are built from
    code points, so no tool can normalize them. Proposed name."""
    sharp_s, u_diaeresis, diaeresis = chr(0xDF), chr(0xFC), chr(0x308)
    german = make("TestProfile", name=f"Stra{sharp_s}e M{u_diaeresis}ller")
    _expect_hits(
        german,
        [
            (f"STRASSE M{chr(0xDC)}LLER", "name"),
            (f"Strasse Mu{diaeresis}ller", "name"),
            (f"stra{sharp_s}e m{u_diaeresis}ller", "name"),
            (f"STRA{sharp_s}E MU{diaeresis}LLER", "name"),
            (f"xStrasse M{u_diaeresis}ller", None),
            ("Strasse Mueller", None),
        ],
    )
    qa, ka_nukta = _chars(0x958), _chars(0x915, 0x93C)
    rest = " " + _chars(0x905, 0x932, 0x940)
    indic = make("TestProfile", name=qa + _chars(0x92E, 0x930) + rest)
    assert qa != ka_nukta
    _expect_hits(
        indic,
        [
            (ka_nukta + _chars(0x92E, 0x930) + rest, "name"),
            (qa + _chars(0x92E, 0x930) + rest, "name"),
            ("x" + ka_nukta + _chars(0x92E, 0x930) + rest, None),
            (ka_nukta + _chars(0x92E, 0x930) + rest + _chars(0x903), None),
        ],
    )
    composed = make("TestProfile", name=f"Jos{chr(0xE9)} {chr(0xC1)}lvarez")
    decomposed = make("TestProfile", name=f"Jose{chr(0x301)} A{chr(0x301)}lvarez")
    for stored in (composed, decomposed):
        _expect_hits(
            stored,
            [
                (f"JOS{chr(0xC9)} {chr(0xC1)}LVAREZ", "name"),
                (f"Jose{chr(0x301)} A{chr(0x301)}lvarez.", "name"),
                ("Jose Alvarez", None),
            ],
        )
    # case folding can leave marks in an order that is not canonical: I-with-dot (U+0130) folds to
    # i + U+0307, and after a mark with a lower combining class (U+0316) the second NFC must
    # reorder the marks to meet the value i + U+0316 + U+0307
    ordered = make("TestProfile", name=_chars(0x69, 0x316, 0x307))
    _expect_hits(
        ordered,
        [(_chars(0x130, 0x316), "name"), (_chars(0x69, 0x307, 0x316), "name"), ("i", None)],
    )
    persona = _note_persona([f"STRASSE M{chr(0xDC)}LLER"])
    before = persona.model_dump_json()
    assert _lint(persona, german) == [("/metadata/n00", "name")]
    assert persona.model_dump_json() == before


def test_profile_lint_paths_order_and_privacy() -> None:
    """Resolution 10: a hit is ``LiteralProfileValue{path, field}`` with the path an RFC 6901 JSON
    Pointer from the document root (``~`` as ``~0``, ``/`` as ``~1``), sorted by path and then
    field in UTF-16 code-unit order (the order JCS uses, which differs from code point order for
    characters above U+FFFF), and the value is never in the hit, its repr or its JSON. Proposed
    name."""
    profile = make("TestProfile")
    value = "Asha Verma"
    keys = ["b", "a~b", "a/b", chr(0xE000), chr(0x10000), "A"]
    persona = make("Persona", metadata=dict.fromkeys(keys, value))
    hits = api("profile").find_literal_profile_values(persona, profile)
    paths = [h.path for h in hits]
    assert paths == [
        "/metadata/A",
        "/metadata/a~0b",
        "/metadata/a~1b",
        "/metadata/b",
        "/metadata/" + chr(0x10000),
        "/metadata/" + chr(0xE000),
    ]
    assert paths != sorted(paths), "UTF-16 order is not code point order"
    assert {h.field for h in hits} == {"name"}
    lint_class = type(hits[0])
    assert set(lint_class.model_fields) == {"path", "field"}
    assert lint_class.__name__ == "LiteralProfileValue"
    assert not {"id", "schema_version", "created_at", "metadata"} & set(lint_class.model_fields)
    rendered = " ".join([repr(hits), str(hits), *(h.model_dump_json() for h in hits)])
    assert value not in rendered
    with pytest.raises(ValidationError):
        lint_class.model_validate({"path": "/x", "field": "name", "value": value})


def test_profile_lint_covers_expected_outcome_and_tool_arguments() -> None:
    """E7: the lint reads every string leaf, so a literal profile value in the expected outcome or
    in a string argument of an expected tool call is reported with its path (never the value), and
    the placeholders that E7 allows there give no hit. Proposed name."""
    profile = make("TestProfile")
    reference = {"id": str(profile.id), "version": profile.version}
    literal = make(
        "Evaluator",
        expected_outcome="The agent greets Asha Verma.",
        expected_tool_calls=[
            {
                "name": "lookup_account",
                "match": "arguments_exact",
                "arguments": {
                    "account_number": "4400123412",
                    "nested": {"ids": ["x", "4400123412"]},
                },
                "order_index": 0,
            }
        ],
    )
    assert _lint(literal, profile) == [
        ("/expected_outcome", "name"),
        ("/expected_tool_calls/0/arguments/account_number", "account_number"),
        ("/expected_tool_calls/0/arguments/nested/ids/1", "account_number"),
    ]
    placeholders = make(
        "Evaluator",
        expected_outcome="The agent greets {{profile.name}}.",
        expected_tool_calls=[
            {
                "name": "lookup_account",
                "match": "arguments_exact",
                "arguments": {"account_number": "{{profile.account_number}}"},
                "order_index": 0,
            }
        ],
        test_profile=reference,
    )
    assert _lint(placeholders, profile) == []


def test_test_profile_errors_do_not_echo_values() -> None:
    """ADR 0007 Decision 8 (b): a model that holds personal data hides its input in validation
    errors, like the credential models. A rejected profile document must not leak the profile's
    values through ``str``, ``repr``, ``errors(include_input=False)`` or
    ``json(include_input=False)`` (``errors()`` always carries the input in pydantic). Proposed
    name."""
    model = entity_class("TestProfile")
    assert model.model_config.get("hide_input_in_errors") is True
    name, dob, number = "Asha Verma", "1990-04-12", "4400123412"
    phone = "98765-43210-XYZ"
    attempts: list[tuple[dict[str, Any], str]] = [
        (raw_document("TestProfile", phone=phone), phone),
        (raw_document("TestProfile", name=[name]), name),
        (raw_document("TestProfile", dob={"date": dob}), dob),
        (raw_document("TestProfile", account_number=[number]), number),
        (raw_document("TestProfile", name=name + chr(0xD800)), name),
        (raw_document("TestProfile", name=name, dob=7), name),
    ]
    for doc, secret in attempts:
        error = assert_rejected(model, doc)
        for text in error_texts(error):
            assert secret not in text, "the profile value is echoed in a validation error"
    # the JSON entry point too
    document = json.dumps({**raw_document("TestProfile", phone=phone), "content_digest": PLAIN})
    with pytest.raises(ValidationError) as caught:
        model.model_validate_json(document)
    for text in error_texts(caught.value):
        assert phone not in text
        assert name not in text
    # a tampered profile raises the typed digest error, which does not carry the values either
    errors = api("errors")
    sealed = seal(model, raw_document("TestProfile"))
    with pytest.raises(errors.DigestMismatch) as mismatch:
        model.model_validate({**sealed, "name": "Priya Nair"})
    assert "Priya Nair" not in str(mismatch.value)
    assert name not in str(mismatch.value)


def test_persona_has_only_language_and_accent() -> None:
    """Section 19.4.1 (N11): in 0.0.1 a ``Persona`` has ``language`` and ``accent`` and nothing
    else; the plan fields ``speaking_pace``, ``interruption_style``, ``emotion``,
    ``background_noise`` and ``voice_ref`` are not adopted (they need closed sets reviewed with
    indic-linguistics before the simulator), so they are rejected as unknown fields. Adding optional
    fields later is a minor change. Proposed name."""
    assert _data_fields(_persona()) == {"language", "accent"}
    for field in (
        "speaking_pace",
        "interruption_style",
        "emotion",
        "background_noise",
        "voice_ref",
    ):
        caught = assert_rejected_name("Persona", **{field: "calm"})
        assert any(e["type"] == "extra_forbidden" for e in caught.errors()), field


def test_persona_and_profile_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed ID or
    language tag on ``Persona`` and ``TestProfile`` does not appear in ``str``, ``repr``,
    ``errors(include_input=False)`` or ``json(include_input=False)``, through ``model_validate``
    and ``model_validate_json``, and the error is still located at the field. Proposed name
    (SCOPE section 5, P1-07)."""
    persona = entity_class("Persona")
    profile = entity_class("TestProfile")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("typed ID", persona, raw_document("Persona", id=marked_id("per")), "id"),
        ("BCP-47", persona, raw_document("Persona", language=marked_tag()), "language"),
        ("typed ID", profile, raw_document("TestProfile", id=marked_id("tpf")), "id"),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, QUOTE_MARK, field=field, label=label)
