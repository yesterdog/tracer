# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-18 (batch 18): the adversarial cases that no earlier test covered.

The numbered cases A-01 to A-44 are covered by the tests the registry names
(``fixtures/adversarial/registry.json``, checked by ``test_adversarial_registry.py``). This file
adds what was missing, as fixtures (``fixtures/adversarial/X-nn/``, described in its README.md,
generated from ``_support_b12.SPECS``) and a few tests that need code rather than a document:

- X-01 and X-14: non-JSON constants, number overflow and lone surrogates in JSON text;
- X-02: duplicate keys in every spelling and at every depth, and what is not a duplicate;
- X-03: the depth limit to the exact level, counting structure and not text;
- X-04: empty, hostile and oversized top-level documents of every entity;
- X-05: bidi, invisible and look-alike characters in identifiers and labels;
- X-06: identifier confusion; X-07: timestamp edge cases; X-08: digest tampering and downgrade;
- X-09: credentials in URLs, references and observed text; X-10: mixed units and counts;
- X-11: spelling bypasses of the export purpose; X-12: cost double counting;
- A-26: a plain digest of a low-entropy profile can be recomputed by guessing.

Credential-shaped values are assembled at run time (``_support_b12.expand``); this file stays
ASCII and builds non-ASCII characters from code points.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from . import _support_b9 as b9
from . import _support_b12 as b12
from ._support import api

ALL_FIXTURES: list[Any] = [
    pytest.param(path, id=f"{path.parent.name}/{path.stem}")
    for directory in b12.case_directories()
    for path in b12.fixture_documents(directory.name)
]


def _flag_state(model: Any) -> bool:
    """Whether a kept document carries a raised flag (bidi controls, metadata flags)."""
    return bool(getattr(model, "contains_bidi_controls", False)) or bool(model.metadata_flags)


# ===================================================================== every fixture, one rule


@pytest.mark.parametrize("path", ALL_FIXTURES)
def test_fixture_outcome_matches_expected(path: Path) -> None:
    """Each fixture is loaded with the strict loader as its entity and does what its
    ``expected.json`` says: a rejected document raises the named error at the named place and
    never echoes what it must not echo; a kept document loads, dumps to the same document (apart
    from the members the model writes in one canonical spelling) and, when it is `flagged`, is
    marked. Nothing crashes: a reader refuses a hostile document, it does not fall over."""
    outcome, expected, text = b12.run_fixture(path)
    assert expected["case"] == path.parent.name
    assert outcome.kind not in b12.CRASH_TYPES, (outcome.kind, outcome.message[:200])
    if expected["expectation"] == "rejected":
        assert not outcome.accepted, "the model accepted a document that must be rejected"
        assert outcome.kind == expected["error_kind"], (outcome.kind, outcome.message[:200])
        assert outcome.path == expected["error_path"], (outcome.path, outcome.message[:200])
        for secret in expected["no_echo"]:
            for rendering in outcome.texts:
                assert b12.expand(secret) not in rendering, "the error repeats the secret"
        return
    assert outcome.accepted, (outcome.kind, outcome.path, outcome.message[:200])
    dumped = outcome.model.model_dump(mode="json")
    assert dumped == {**json.loads(text), **expected["canonical"]}
    if expected["expectation"] == "flagged":
        assert _flag_state(outcome.model)


def test_fixture_expected_files_are_well_formed() -> None:
    """Every fixture has an ``expected.json`` with exactly the documented members, in a known
    case directory, for a registered entity; every threat is a sentence; a rejected case names
    its error and a kept case does not; a kept document is accepted by the JSON Schema; nothing
    is left over without its pair."""
    members = {
        "case",
        "entity",
        "base",
        "expectation",
        "error_path",
        "error_kind",
        "schema",
        "canonical",
        "no_echo",
        "why",
        "threat",
    }
    entities = b9.entity_models()
    documents = [p for d in b12.case_directories() for p in b12.fixture_documents(d.name)]
    assert len(documents) == len(b12.SPECS) >= 100
    for path in documents:
        expected = b9.read_json(path.with_name(path.stem + ".expected.json"))
        where = f"{path.parent.name}/{path.stem}"
        assert set(expected) == members, where
        assert expected["case"] == path.parent.name, where
        assert expected["entity"] in entities, where
        assert expected["expectation"] in b12.EXPECTATIONS, where
        assert expected["schema"] in b12.SCHEMA_RESULTS, where
        assert expected["why"].strip(), where
        assert expected["threat"].strip().endswith("."), where
        assert (b9.GOLDEN / expected["base"].split("/")[0]).is_dir(), where
        if expected["expectation"] == "rejected":
            assert isinstance(expected["error_kind"], str), where
            assert isinstance(expected["error_path"], list), where
            assert expected["canonical"] == {}, where
        else:
            assert expected["error_kind"] is None, where
            assert expected["error_path"] is None, where
            assert expected["schema"] in ("accepts", "not_applicable"), where
            assert expected["no_echo"] == [], where
    stems = {
        p.name.removesuffix(".expected.json")
        for d in b12.case_directories()
        for p in d.glob("*.expected.json")
    }
    assert stems == {p.stem for p in documents}


def test_fixtures_equal_the_generator_output() -> None:
    """The committed files are exactly what ``_support_b12.render()`` produces, and the tree holds
    nothing else but the registry and the README (no scratch file)."""
    rendered = b12.render()
    on_disk = {
        str(p.relative_to(b12.ADVERSARIAL)): p.read_bytes()
        for p in b12.ADVERSARIAL.rglob("*")
        if p.is_file()
    }
    assert on_disk.pop("registry.json") is not None
    assert on_disk.pop("README.md") is not None
    assert sorted(on_disk) == sorted(rendered)
    for relative, data in rendered.items():
        assert on_disk[relative] == data, relative


def test_adversarial_fixtures_hold_no_credential_shaped_value() -> None:
    """No string of any fixture (or of the registry) matches a credential pattern of ADR 0007
    Decision 5a or is a high-entropy token: the credential-shaped values of the X-09 cases are
    placeholders until run time. The scan has teeth: each placeholder expands to a value the
    detector recognizes."""
    credentials = api("credentials")
    checked = 0
    for path in sorted(b12.ADVERSARIAL.rglob("*.json")):
        stack: list[Any] = [json.loads(path.read_text(encoding="utf-8"))]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, str):
                checked += 1
                assert not credentials.contains_credential_pattern(value), path.name
                if value.startswith(("sha256-", "hmac-")) or path.name == "registry.json":
                    continue  # node ids and paths of the registry are long, not secret
                digest_like = len(value) == 64 and value == value.lower()
                id_like = re.search("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", value) is not None
                if not (digest_like or id_like):
                    assert credentials.credential_reason(value) is None, path.name
    assert checked > 3000
    for name, value in b12.SECRETS.items():
        assert credentials.contains_credential_pattern(value), name
        assert b12.expand(f"@@{name}@@") == value
    assert "@@" not in "".join(b12.SECRETS.values())


# ============================================================================== X-01 and X-14


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_x01_non_json_constants_are_refused_by_the_loader_at_any_depth(constant: str) -> None:
    """X-01: ``NaN``, ``Infinity`` and ``-Infinity`` are not JSON; Python's decoder reads them, so
    the strict loader refuses them by name, at the top, in an array and in a nested object, and the
    message names the constant and not the document."""
    loader = api("loader")
    for text in (
        constant,
        f"[1, {constant}]",
        '{"a": {"b": [{"c": ' + constant + "}]}}",
    ):
        with pytest.raises(ValueError, match="not a JSON value") as caught:
            loader.loads_strict(text)
        assert constant in str(caught.value)
        assert '"a"' not in str(caught.value)


def test_x01_number_overflow_is_not_a_constant_but_never_reaches_a_number_field() -> None:
    """X-01: ``1e999`` is JSON text that decodes to infinity. The loader lets it through (it is not
    a constant), so every typed field must refuse it: a segment confidence, tool arguments and a
    decimal string (which takes no JSON number at all)."""
    loader = api("loader")
    assert loader.loads_strict("[1e999]") == [float("inf")]
    path = b12.ADVERSARIAL / "X-01"
    names = {p.stem for p in b12.fixture_documents("X-01")}
    assert {
        "exponent-overflow-in-a-segment-confidence",
        "exponent-overflow-in-tool-arguments",
    } <= names
    usage = json.dumps(b9.read_json(b9.GOLDEN / "UsageRecord" / "valid" / "maximal.json"))
    for member in ('"count": 1000', '"count": "12.5"'):
        assert member in usage
        outcome = b12.run_text("UsageRecord", usage.replace(member, '"count": 1e999', 1))
        assert outcome.kind == "usage_count" or outcome.kind in {"int_type", "less_than_equal"}
    assert path.is_dir()


def test_x14_a_valid_surrogate_pair_and_a_lone_one_are_told_apart_by_the_loader() -> None:
    """X-14: the decoder yields a lone surrogate for ``"\\ud800"`` and one character for a valid
    escaped pair; the strict loader leaves both to the models, which refuse the lone one where
    text or I-JSON is stored (the fixtures) and keep the pair."""
    loader = api("loader")
    assert loader.loads_strict('"\\ud83d\\ude00"') == chr(0x1F600)
    assert loader.loads_strict('"\\ud800"') == chr(0xD800)


# ====================================================================================== X-02


def test_x02_a_duplicate_is_found_after_decoding_and_at_every_depth() -> None:
    """X-02: keys are compared after unescaping (``"a"`` and ``"\\u0061"`` are one key), in
    objects inside arrays and inside objects, at the top and deep down; the error names the key
    and never repeats a value; sibling objects with the same key are not duplicates; keys that
    differ only in Unicode normalization are two keys."""
    loader = api("loader")
    errors = api("errors")
    secret = b12.SECRETS["PASTED_KEY"]
    hostile = [
        '{"a": 1, "a": 2}',
        '{"a": 1, "\\u0061": 2}',
        '{"x": {"a": 1, "a": 2}}',
        '{"x": [{"ok": 1}, {"a": 1, "a": 2}]}',
        '{"x": [[{"a": 1, "\\u0061": 2}]]}',
        '{"A": 1, "a": 2, "a": 3}',
    ]
    for text in hostile:
        with pytest.raises(errors.DuplicateKeyError) as caught:
            loader.loads_strict(text.replace("1", f'"{secret}"', 1))
        assert "'a'" in str(caught.value)
        assert secret not in str(caught.value)
    assert loader.loads_strict('[{"a": 1}, {"a": 2}, {"a": {"a": 3}}]') == [
        {"a": 1},
        {"a": 2},
        {"a": {"a": 3}},
    ]
    nfc, nfd = chr(0xE9), "e" + chr(0x301)
    parsed = loader.loads_strict(json.dumps({nfc: 1, nfd: 2}))
    assert list(parsed) == [nfc, nfd]
    assert loader.loads_strict(json.dumps({"k": 1, "K": 2})) == {"k": 1, "K": 2}


# ====================================================================================== X-03


def test_x03_the_depth_limit_is_exact_and_counts_structure_not_text() -> None:
    """X-03: depth 256 loads and depth 257 is ``DepthLimitExceeded`` (a scalar is depth 0, each
    array or object one more); ``max_depth`` moves the limit both ways; brackets, quotes and
    backslashes inside strings are text, whatever they look like; the error names an offset and
    nothing of the content; and the limit applies to the models' entry point too."""
    loader = api("loader")
    errors = api("errors")
    assert loader.DEFAULT_MAX_DEPTH == 256
    for depth, ok in ((255, True), (256, True), (257, False), (1000, False)):
        text = "[" * depth + "]" * depth
        if ok:
            assert loader.loads_strict(text) is not None
        else:
            with pytest.raises(errors.DepthLimitExceeded):
                loader.loads_strict(text)
    deep = "[" * 12 + "]" * 12
    assert loader.loads_strict(deep, max_depth=12) is not None
    with pytest.raises(errors.DepthLimitExceeded):
        loader.loads_strict(deep, max_depth=11)
    assert loader.loads_strict(deep, max_depth=500) is not None
    texts = [
        json.dumps("[" * 100_000),
        json.dumps({"k": "{" * 100_000}),
        json.dumps(['\\"' + "[" * 1000, "\\" * 999 + '"' + "[" * 1000]),
    ]
    for text in texts:
        assert loader.loads_strict(text, max_depth=70) is not None
    secret = "carrier-" + "x" * 20
    hostile = '{"note": "' + secret + '", "deep": ' + "[" * 300 + "]" * 300 + "}"
    with pytest.raises(errors.DepthLimitExceeded) as caught:
        loader.loads_strict(hostile)
    assert secret not in str(caught.value)
    with pytest.raises(errors.DepthLimitExceeded):
        loader.load_model(b9.entity_models()["Project"], "[" * 300 + "]" * 300)
    with pytest.raises(errors.DepthLimitExceeded):
        loader.load_bundle(b9.entity_models()["RunBundle"], "[" * 300 + "]" * 300)


# ====================================================================================== X-04

HOSTILE_TOP_LEVELS = (
    "",
    " ",
    "\n\t",
    "null",
    "true",
    "false",
    "0",
    "-0",
    "1.5",
    "1e999",
    '""',
    '"{}"',
    "[]",
    "[{}]",
    "[null]",
    "{}",
    '{"schema_version": null}',
    '{"schema_version": "0.0.1"}',
    '{"schema_version": ["0.0.1"]}',
    '{"schema_version": 1}',
    '{"schema_version": "0.0.1", "id": null}',
    '{"id": ""}',
    '{"": ""}',
    '{"metadata": []}',
)


@pytest.mark.parametrize("entity", sorted(b9.entity_models()))
def test_x04_empty_and_hostile_top_level_documents_are_refused_not_crashes(entity: str) -> None:
    """X-04: for each of the 26 entities, an empty text, white space, a scalar, an array, an
    object with no members, a string that holds JSON and objects with wrong or hostile
    ``schema_version`` and ``id`` members are refused with a validation error or a typed error:
    never accepted, never a crash (``TypeError``, ``KeyError``, ``RecursionError`` and so on)."""
    for text in HOSTILE_TOP_LEVELS:
        outcome = b12.run_text(entity, text)
        assert not outcome.accepted, (entity, text)
        assert outcome.kind not in b12.CRASH_TYPES, (entity, text, outcome.kind)


def test_x04_hostile_bytes_are_value_errors_not_crashes() -> None:
    """X-04: the strict loader takes bytes too. A byte order mark, invalid UTF-8, UTF-16 text, a
    NUL, a truncated multibyte character and trailing garbage are all ``ValueError`` (a decoding
    error is one), and none of them is accepted."""
    loader = api("loader")
    model = b9.entity_models()["Project"]
    hostile = [
        b"\xef\xbb\xbf{}",
        b'{"name": "\xff"}',
        '{"name": "x"}'.encode("utf-16"),
        b'{"name": "x"}\x00',
        b'{"name": "\xe0\xa4"}',
        b"{} {}",
        b"\x00",
        b"",
    ]
    for data in hostile:
        with pytest.raises(ValueError):  # noqa: PT011 (every decoding and JSON error is one)
            loader.load_model(model, data)


def test_x04_the_machine_key_length_limit_is_exact() -> None:
    """X-04, the size limit the models define: a machine key (a tool name, a provider, a model) is
    1 to 256 characters; 256 loads and 257 is refused. (A label has no length limit in the
    schema; only the loader's depth and bundle-size limits bound a document, see X-03 and
    ``test_an_oversized_bundle_is_refused_before_it_is_parsed``.)"""
    document = b9.read_json(b9.GOLDEN / "ToolCallEvent" / "valid" / "maximal.json")
    for length, ok in ((1, True), (255, True), (256, True), (257, False), (5000, False)):
        document["name"] = "a" * length
        outcome = b12.run_text("ToolCallEvent", json.dumps(document))
        assert outcome.accepted is ok, length
        if not ok:
            assert outcome.kind == "machine_key"
    document["name"] = ""
    assert not b12.run_text("ToolCallEvent", json.dumps(document)).accepted


# ====================================================================================== X-09


def test_x09_observed_text_that_holds_a_key_is_kept_and_is_not_metadata() -> None:
    """X-09: what a caller said or an agent passed to a tool is observed data. A credential-shaped
    value in it is kept byte for byte and raises no ``metadata_flags`` (the flags are for
    ``metadata``, the user-owned JSON); the secret is therefore in the record, and the only
    protection is the export rule (seal or redact). The fixtures really hold such a value: the
    detector recognizes it after the placeholder is expanded."""
    credentials = api("credentials")
    for name in (
        "turn-text-that-holds-a-key-is-observed-data",
        "tool-arguments-that-hold-a-key-are-observed-data",
    ):
        path = b12.ADVERSARIAL / "X-09" / f"{name}.json"
        outcome, expected, text = b12.run_fixture(path)
        assert outcome.accepted
        assert expected["expectation"] == "preserved"
        assert credentials.contains_credential_pattern(text)
        assert outcome.model.metadata_flags == []
        dumped = outcome.model.model_dump(mode="json")
        assert dumped["metadata_flags"] == []
        assert credentials.contains_credential_pattern(json.dumps(dumped))


# ====================================================================================== A-26


def test_a26_a_plain_digest_of_a_low_entropy_profile_can_be_recomputed_by_guessing() -> None:
    """A-26 (ADR 0006 Decision 3, the documented limitation): the plain ``sha256-jcs/v1`` digest
    that an internal snapshot keeps for a test profile is a function of the profile's values, so
    anyone who knows the other members and has a list of candidate names or account numbers can
    find the real one by recomputing the digest. A keyed digest cannot be recomputed without the
    key, which is why shared exports drop or key it. (The policy document that states this is
    checked by ``test_policy_documents_low_entropy_digest_limitation``.)"""
    digest = api("digest")
    document = b9.read_json(b9.GOLDEN / "TestProfile" / "valid" / "maximal.json")
    stored = document["content_digest"]["value"]
    assert digest.compute_digest(document).value == stored
    candidates = ["ACCT-0099-5543", "ACCT-0099-5545", "ACCT-0099-5544", "ACCT-0000-0000"]
    found = [
        guess
        for guess in candidates
        if digest.compute_digest({**document, "account_number": guess}).value == stored
    ]
    assert found == ["ACCT-0099-5544"], "a guess list finds the account number from the digest"
    key_ref = digest.KeyRef(key_id="adversarial-test-key")
    keyed = digest.compute_digest(
        document, scheme=digest.KEYED_SCHEME, key=b"k" * 32, key_ref=key_ref
    )
    assert keyed.value != stored
    assert all(
        digest.compute_digest(
            {**document, "account_number": guess},
            scheme=digest.KEYED_SCHEME,
            key=b"g" * 32,
            key_ref=key_ref,
        ).value
        != keyed.value
        for guess in candidates
    ), "without the key no guess reproduces a keyed digest"
