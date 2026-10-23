# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-17: language fixtures for the nine configured languages (SCOPE P1-17, ADR 0005, 0011, 0017).

The fixtures are in ``tests/fixtures/languages`` (see the README there). Every case must
round-trip code point for code point through the model fields that hold it: ``Turn.text``,
``Segment.text``, ``ScriptedTurn.text`` (also inside an ``Evaluator``), ``Persona.accent``,
``Persona.language``, ``ScriptedTurn.language``, ``Evaluator.language``, ``DatasetItem.language``,
``Transcript.detected_language`` / ``detected_language_raw``, ``Turn.language`` / ``language_raw``
and ``Transcript.declared_language``. Normalization is never applied by storage (ADR 0017
Decision 1); NFC, NFD and the joiners are asserted with ``unicodedata`` only.

Negative cases (excluded tags, the mixed-language alias, romanized tags) are read from
``fixtures/languages/negatives/negatives.json``; no test module spells them, so this module needs
no T-DRIFT allowlist entry. ``test_fixtures_outside_negatives_hold_no_excluded_tokens`` checks that
no other fixture file, this module or its helper spells them either. The native-speaker review of
the strings is NOT done: ``test_every_case_needs_native_review``.

This file is ASCII only. Special characters are read from the JSON fixtures or built with chr().
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import adapter, api
from ._support_b2 import assert_rejected, assert_rejected_name, make, mixed_alias_message
from ._support_b3 import (
    assert_rejected3,
    doc3,
    entity_class3,
    language_declaration,
    make3,
    transcript_doc,
    turn_doc,
)
from ._support_b10 import (
    COMPOSITION_EXCLUDED,
    CROSS_CUTTING_FILE,
    FSI,
    LANGUAGE_FILES,
    LANGUAGES_DIR,
    NEGATIVES_DIR,
    NEGATIVES_FILE,
    NINE,
    PDI,
    ZWJ,
    ZWNJ,
    Case,
    code_points,
    declared_scripts,
    digit_forms,
    expected_form,
    facts_for,
    fixture_files,
    invisible_counts,
    iter_json_strings,
    json_plain,
    load_cases,
    metric_flag,
    native_digit_range,
    negative_cases,
    nf,
    positive_cases,
    script_sensitive_definition,
    scripts_in,
    tdrift_hits,
)

HERE = Path(__file__).resolve().parent

POSITIVE = positive_cases()
NEGATIVE = negative_cases()
EVERY = [*POSITIVE, *NEGATIVE]
BY_ID = {str(case["id"]): case for case in EVERY}


def _expected(case: Case) -> dict[str, Any]:
    value = case.get("expected")
    return value if isinstance(value, dict) else {}


def _of(cases: list[Case], kind: str) -> list[Case]:
    return [case for case in cases if case.get("kind") == kind]


def _with(cases: list[Case], key: str) -> list[Case]:
    return [case for case in cases if key in case]


def _params(cases: list[Case]) -> list[Any]:
    return [pytest.param(case, id=str(case["id"])) for case in cases]


TEXT_CASES = _of(EVERY, "text")
PRESERVED_TEXT_CASES = [c for c in TEXT_CASES if _expected(c).get("outcome") == "preserved"]
FLAGGED_TEXT_CASES = [c for c in TEXT_CASES if _expected(c).get("outcome") == "flagged"]
TAG_CASES = _of(POSITIVE, "tag")
NEGATIVE_TAG_CASES = _of(NEGATIVE, "tag")
LABEL_CASES = _of(EVERY, "label")
UNPARSEABLE_LABEL_CASES = [c for c in LABEL_CASES if _expected(c).get("tag", "x") is None]
MUST_CONTAIN_CASES = _with(EVERY, "must_contain")
INVISIBLE_CASES = _with(EVERY, "invisibles")
JOINER_CASES = [c for c in INVISIBLE_CASES if {"U+200D", "U+200C"} & set(c["invisibles"])]
DIGIT_CASES = _with(POSITIVE, "digits_forms")
NUMBER_SIGN_CASES = _with(POSITIVE, "number_signs")
CLAIM_CASES = _with(TEXT_CASES, "normalization")
UNCLAIMED_CASES = [c for c in TEXT_CASES if "normalization" not in c]
PAIR_CASES = _with(EVERY, "pair_of")
LABEL_CANDIDATES = [c for c in POSITIVE if c.get("label_candidate") is True]
LABEL_RULE_CASES = _with(POSITIVE, "label_rejected")

SUPPORTED_MESSAGES = {
    "not_configured": "is not a configured language",
    "script": "is not a native script",
    "region": "is not supported for a configured language",
    "variant_extension": "variants, extensions and private-use subtags",
    "not_a_tag": "is not a well-formed BCP-47 language tag",
}
ALL_FORMS = ("NFC", "NFD", "NFKC", "NFKD")
CLAIM_FORMS = {"nfc": "NFC", "nfd": "NFD", "nfkc": "NFKC"}


def _locs(error: ValidationError) -> list[list[str]]:
    return [[str(part) for part in item["loc"]] for item in error.errors()]


def _scripted_turn() -> Any:
    return api("evaluator").ScriptedTurn


def _declared_doc(tag: str) -> dict[str, Any]:
    """A Transcript document that declares ``tag`` as the language of an untagged import."""
    declaration = language_declaration(language=tag)
    return transcript_doc(source="agent_export", declared_language=declaration)


# ------------------------------------------------------------------------------ the files


def test_fixture_files_present_and_wellformed() -> None:
    """The fixture tree is exactly what the README lists, strict UTF-8 without BOM or CR."""
    expected = {*LANGUAGE_FILES, CROSS_CUTTING_FILE, "README.md"}
    present = {path.name for path in LANGUAGES_DIR.iterdir() if path.is_file()}
    assert present == expected, (sorted(present - expected), sorted(expected - present))
    assert {path.name for path in LANGUAGES_DIR.iterdir() if path.is_dir()} == {"negatives"}
    assert {path.name for path in NEGATIVES_DIR.iterdir()} == {NEGATIVES_FILE, "README.md"}
    for path in fixture_files():
        raw = path.read_bytes()
        assert not raw.startswith(bytes((0xEF, 0xBB, 0xBF))), f"{path.name}: byte order mark"
        text = raw.decode("utf-8")
        assert text.endswith("\n"), path.name
        assert "\r" not in text, path.name
        if path.suffix == ".json":
            document = json.loads(text)
            assert set(document) == {"_meta", "cases"}, path.name
            assert document["_meta"]["needs_native_review"] is True, path.name
            assert document["cases"], path.name


def test_case_ids_are_unique_and_match_their_file() -> None:
    seen: set[str] = set()
    for code in NINE:
        cases = load_cases(LANGUAGES_DIR / f"{code}.json")
        assert len(cases) >= 15, code
        for case in cases:
            assert case["language"] == code, case["id"]
            assert str(case["id"]).startswith(f"{code}-"), case["id"]
            assert case["id"] not in seen, case["id"]
            seen.add(str(case["id"]))
    cross = load_cases(LANGUAGES_DIR / CROSS_CUTTING_FILE)
    assert len(cross) >= 10
    for case in cross:
        assert case["language"] in NINE, case["id"]
        assert str(case["id"]).startswith("cc-"), case["id"]
        assert case["id"] not in seen, case["id"]
        seen.add(str(case["id"]))
    assert len(NEGATIVE) >= 40
    for case in NEGATIVE:
        assert str(case["id"]).startswith("neg-"), case["id"]
        assert case["id"] not in seen, case["id"]
        seen.add(str(case["id"]))


@pytest.mark.parametrize("case", _params(EVERY))
def test_case_record_is_complete(case: Case) -> None:
    for name in ("id", "language", "script", "kind", "category", "text", "purpose", "expected"):
        assert name in case, (case.get("id"), name)
    assert case["kind"] in ("text", "tag", "label")
    assert isinstance(case["text"], str)
    assert isinstance(case["purpose"], str)
    assert case["purpose"]
    expected = case["expected"]
    assert expected["outcome"] in ("preserved", "rejected", "flagged")
    kind, outcome = case["kind"], expected["outcome"]
    if kind == "tag" and outcome == "preserved":
        assert {"canonical", "declarable"} <= set(expected)
    elif kind == "tag":
        assert outcome == "rejected"
        assert {"configured", "path", "observed", "metric_flag"} <= set(expected)
        assert {"type", "message"} <= set(expected["configured"])
        assert expected["configured"]["message"] in (*SUPPORTED_MESSAGES, "mixed_alias")
    elif kind == "label":
        assert {"tag", "warnings"} <= set(expected)
    elif outcome == "flagged":
        assert {"flag", "stored"} <= set(expected)
    else:
        assert outcome == "preserved"


def test_every_case_needs_native_review() -> None:
    """Native-speaker review has NOT been done; no case may claim it has."""
    assert EVERY
    assert [c["id"] for c in EVERY if c.get("needs_native_review") is not True] == []


COMMON_INDIC = {
    "greeting",
    "opening_hours",
    "number_words",
    "digits_native",
    "digits_mixed",
    "latin_tokens",
    "transliteration",
    "configured_tag",
    "configured_tag_case",
    "provider_label",
}
REQUIRED_CATEGORIES: dict[str, set[str]] = {
    "en": {
        "greeting",
        "opening_hours",
        "number_words",
        "digits_ascii",
        "digits_mixed",
        "nfc_nfd_pair",
        "configured_tag",
        "configured_tag_case",
        "provider_label",
    },
    "hi": COMMON_INDIC | {"joiners", "nukta_pair", "minimal_pair"},
    "mr": COMMON_INDIC | {"joiners", "minimal_pair"},
    "te": COMMON_INDIC | {"joiners", "vowel_sign_pair"},
    "ta": COMMON_INDIC | {"joiners", "vowel_sign_pair", "number_sign"},
    "kn": COMMON_INDIC | {"joiners", "vowel_sign_pair"},
    "ml": COMMON_INDIC | {"joiners", "chillu_pair", "vowel_sign_pair"},
    "pa": COMMON_INDIC | {"tippi_addak", "tippi_bindi_pair", "addak_pair", "nukta_pair"},
    "gu": COMMON_INDIC | {"joiners"},
}


@pytest.mark.parametrize("code", NINE)
def test_every_language_covers_what_p1_17_lists(code: str) -> None:
    found = {str(case["category"]) for case in load_cases(LANGUAGES_DIR / f"{code}.json")}
    assert REQUIRED_CATEGORIES[code] <= found, sorted(REQUIRED_CATEGORIES[code] - found)


def test_hindi_and_english_india_tags_present() -> None:
    texts = {str(case["text"]) for case in TAG_CASES}
    assert {"hi-IN", "en-IN", "pa-Guru-IN"} <= texts


def test_negatives_cover_the_p1_17_list() -> None:
    """The excluded tags, the Arabic-script Punjabi tag, the mixed alias and a romanized tag are
    all present (assembled from parts so that this module never spells the excluded ones)."""
    wanted = {
        "b" + "n",
        "u" + "r",
        "o" + "r",
        "a" + "s",
        "pa" + "-" + "Arab",
        "hi" + "-" + "en",
        "hi-Latn",
    }
    present = {str(case["text"]) for case in NEGATIVE_TAG_CASES}
    assert wanted <= present, sorted(wanted - present)


# ------------------------------------------------------------------------- the text itself


@pytest.mark.parametrize("case", _params(EVERY))
def test_text_is_valid_unicode_and_survives_json(case: Case) -> None:
    text = str(case["text"])
    assert text.encode("utf-8").decode("utf-8") == text
    assert not any(0xD800 <= ord(char) <= 0xDFFF for char in text)
    assert json.loads(json.dumps(text, ensure_ascii=False)) == text
    assert json.loads(json.dumps(text, ensure_ascii=True)) == text


@pytest.mark.parametrize("case", _params(TEXT_CASES))
def test_declared_script_matches_the_letters(case: Case) -> None:
    """The script field describes the letters; a typo in another script would fail here."""
    found = scripts_in(str(case["text"]))
    if found:
        assert found == declared_scripts(case), (sorted(found), case["script"])


@pytest.mark.parametrize("case", _params(MUST_CONTAIN_CASES))
def test_required_code_points_are_present(case: Case) -> None:
    present = set(code_points(str(case["text"])))
    assert set(case["must_contain"]) <= present, sorted(set(case["must_contain"]) - present)


@pytest.mark.parametrize("case", _params(INVISIBLE_CASES))
def test_invisible_characters_are_counted_as_declared(case: Case) -> None:
    wanted = dict(case["invisibles"])
    assert invisible_counts(str(case["text"]), list(wanted)) == wanted


@pytest.mark.parametrize("case", _params(DIGIT_CASES))
def test_digit_forms_are_as_declared(case: Case) -> None:
    forms = digit_forms(str(case["text"]), str(case["language"]))
    assert forms == set(case["digits_forms"]), sorted(forms)


@pytest.mark.parametrize("case", _params(DIGIT_CASES))
def test_native_digits_are_decimal_and_nfkc_folds_only_other_forms(case: Case) -> None:
    """A native digit is Nd in the language's block with a decimal value, and NFKC leaves it alone
    (so NFKC never turns a native digit into an ASCII one); other forms (full-width) fold."""
    own = native_digit_range(str(case["language"]))
    for char in str(case["text"]):
        if unicodedata.category(char) != "Nd" or 0x30 <= ord(char) <= 0x39:
            continue
        assert 0 <= unicodedata.decimal(char) <= 9
        if own is not None and own[0] <= ord(char) <= own[1]:
            assert nf("NFKC", char) == char, code_points(char)
            assert nf("NFC", char) == char, code_points(char)
        else:
            assert nf("NFKC", char) == str(unicodedata.decimal(char)), code_points(char)


@pytest.mark.parametrize("case", _params(NUMBER_SIGN_CASES))
def test_number_signs_are_numbers_not_decimal_digits(case: Case) -> None:
    """The Tamil number signs U+0BF0..U+0BF2 are Numeric_Type=Numeric (category No)."""
    own = native_digit_range(str(case["language"]))
    assert own is not None
    for label, value in case["number_signs"].items():
        char = chr(int(label.removeprefix("U+"), 16))
        assert char in str(case["text"]), label
        assert unicodedata.category(char) == "No", label
        assert unicodedata.decimal(char, None) is None, label
        assert unicodedata.numeric(char) == value, label
        assert nf("NFKC", char) == char, label
        assert not own[0] <= ord(char) <= own[1], label


@pytest.mark.parametrize("case", _params(CLAIM_CASES))
def test_normalization_claims_hold(case: Case) -> None:
    text = str(case["text"])
    claims = dict(case["normalization"])
    assert set(claims) <= set(CLAIM_FORMS), sorted(claims)
    for key, claim in claims.items():
        wanted = expected_form(str(claim), text)
        got = nf(CLAIM_FORMS[key], text)
        assert got == wanted, (key, code_points(got), code_points(wanted))


@pytest.mark.parametrize("case", _params(UNCLAIMED_CASES))
def test_text_without_a_normalization_claim_is_already_nfc(case: Case) -> None:
    """A case that does not say it is normalization-sensitive must be NFC-stable, and must not
    hold a composition-excluded letter, so a tool that re-normalizes a file cannot change what a
    test means."""
    text = str(case["text"])
    excluded = sorted(code_points(char)[0] for char in text if ord(char) in COMPOSITION_EXCLUDED)
    assert not excluded, excluded
    assert nf("NFC", text) == text, code_points(text)


@pytest.mark.parametrize("case", _params(JOINER_CASES))
def test_joiners_survive_every_normalization_form(case: Case) -> None:
    text = str(case["text"])
    for form in ALL_FORMS:
        result = nf(form, text)
        assert result.count(ZWJ) == text.count(ZWJ), form
        assert result.count(ZWNJ) == text.count(ZWNJ), form


@pytest.mark.parametrize("case", _params(PAIR_CASES))
def test_pair_relation_holds_and_both_spellings_are_stored(case: Case) -> None:
    partner = BY_ID[str(case["pair_of"])]
    assert partner["pair_of"] == case["id"]
    assert partner["pair_relation"] == case["pair_relation"]
    first, second = str(case["text"]), str(partner["text"])
    assert first != second
    relation = case["pair_relation"]
    if relation == "nfc_equal":
        assert nf("NFC", first) == nf("NFC", second)
        assert nf("NFD", first) == nf("NFD", second)
    elif relation == "nfd_equal":
        assert nf("NFD", first) == nf("NFD", second)
    elif relation == "distinct":
        for form in ALL_FORMS:
            assert nf(form, first) != nf(form, second), form
    else:
        pytest.fail(f"unknown pair_relation {relation!r}")
    # storage keeps each spelling as given, so the two stay distinct values (ADR 0017 I1)
    one, other = make3("Turn", text=first), make3("Turn", text=second)
    assert (one.text, other.text) == (first, second)
    assert one.text != other.text


# ------------------------------------------------------------- free text through the models


@pytest.mark.parametrize("case", _params(TEXT_CASES))
def test_turn_text_round_trips_code_point_for_code_point(case: Case) -> None:
    text = str(case["text"])
    flag = bool(case.get("bidi_flag", False))
    turn = make3("Turn", text=text)
    assert turn.text == text
    assert turn.text.encode("utf-8") == text.encode("utf-8")
    assert turn.contains_bidi_controls is flag
    assert api("text").contains_bidi_controls(text) is flag
    dumped = turn.model_dump(mode="json")
    assert dumped["text"] == text
    wire = turn.model_dump_json()
    assert json.loads(wire)["text"] == text
    needle = json_plain(text)
    if needle is not None:
        assert needle in wire.encode("utf-8"), "the JSON bytes hold the UTF-8 bytes of the text"
    again = entity_class3("Turn").model_validate_json(wire)
    assert again.text == text
    assert again.model_dump(mode="json") == dumped
    wanted = dict(case.get("invisibles", {}))
    assert invisible_counts(turn.text, list(wanted)) == wanted


@pytest.mark.parametrize("case", _params(TEXT_CASES))
def test_segment_text_round_trips_code_point_for_code_point(case: Case) -> None:
    text = str(case["text"])
    turn = make3("Turn", text=text, segments=[{"granularity": "utterance", "text": text}])
    assert turn.segments is not None
    assert turn.segments[0].text == text
    assert turn.segments[0].contains_bidi_controls is bool(case.get("bidi_flag", False))
    again = entity_class3("Turn").model_validate_json(turn.model_dump_json())
    assert again.segments is not None
    assert again.segments[0].text == text


@pytest.mark.parametrize("case", _params(TEXT_CASES))
def test_display_safe_wraps_and_marks_only_bidi_controls(case: Case) -> None:
    text = str(case["text"])
    shown = api("text").display_safe(text)
    assert shown.startswith(FSI)
    assert shown.endswith(PDI)
    inner = shown[1:-1]
    assert not any(char in api("text").BIDI_CONTROLS for char in inner)
    markers = list(case.get("markers", []))
    for marker in markers:
        assert marker in inner, marker
    if not markers:
        assert inner == text, "LRM, RLM, ZWJ, ZWNJ and ordinary text pass through unchanged"


@pytest.mark.parametrize("case", _params(PRESERVED_TEXT_CASES))
def test_scripted_turn_and_evaluator_keep_the_text(case: Case) -> None:
    text = str(case["text"])
    tag = f"{case['language']}-IN"
    turn = _scripted_turn().model_validate({"index": 0, "text": text, "language": tag})
    assert turn.text == text
    assert turn.language == tag
    scripted = [{"index": 0, "text": text, "language": tag}]
    evaluator = make("Evaluator", language=tag, scripted_turns=scripted)
    dumped = evaluator.model_dump(mode="json", by_alias=True)
    assert dumped["scripted_turns"][0]["text"] == text
    again = type(evaluator).model_validate(dumped)
    assert again.scripted_turns[0].text == text
    assert again.language == tag


@pytest.mark.parametrize("case", _params(LABEL_CANDIDATES))
def test_label_candidate_is_accepted_as_accent_and_kept(case: Case) -> None:
    text = str(case["text"])
    persona = make("Persona", language=f"{case['language']}-IN", accent=text)
    assert persona.accent == text
    dumped = persona.model_dump(mode="json", by_alias=True)
    assert dumped["accent"] == text
    assert type(persona).model_validate(dumped).accent == text


@pytest.mark.parametrize("case", _params(LABEL_RULE_CASES))
def test_label_rule_for_invisible_characters(case: Case) -> None:
    """ADR 0017 Decision 4: a label keeps ZWJ and ZWNJ and rejects the other invisible characters
    that free text keeps."""
    text = str(case["text"])
    tag = f"{case['language']}-IN"
    if case["label_rejected"]:
        assert_rejected_name("Persona", field="accent", language=tag, accent=text)
    else:
        assert make("Persona", language=tag, accent=text).accent == text


# ---------------------------------------------------------------------------- tags (positive)


@pytest.mark.parametrize("case", _params(TAG_CASES))
def test_configured_tag_round_trips_through_every_configured_field(case: Case) -> None:
    tag = str(case["text"])
    canonical = str(case["expected"]["canonical"])
    supported = adapter("language", "SupportedLanguage")
    assert supported.validate_python(tag) == canonical
    assert supported.validate_json(json.dumps(tag)) == canonical
    persona = make("Persona", language=tag)
    assert persona.language == canonical
    reloaded = type(persona).model_validate(persona.model_dump(mode="json", by_alias=True))
    assert reloaded.language == canonical
    scripted = _scripted_turn().model_validate({"index": 0, "text": "Hello", "language": tag})
    assert scripted.language == canonical
    assert make("Evaluator", language=tag).language == canonical
    assert make3("DatasetItem", language=tag).language == canonical
    assert api("language").primary_language(tag) == case["language"]
    script = api("language").script_subtag(tag)
    assert script is None or script == case["script"]


@pytest.mark.parametrize("case", _params(TAG_CASES))
def test_observed_tag_and_raw_label_are_kept(case: Case) -> None:
    """The same strings as observations: the normalized tag is canonical, the raw label is the
    string as given (also its letter case), on the transcript and on the turn."""
    tag = str(case["text"])
    canonical = str(case["expected"]["canonical"])
    assert adapter("language", "ObservedLanguageTag").validate_python(tag) == canonical
    transcript = make3("Transcript", detected_language=tag, detected_language_raw=tag)
    assert transcript.detected_language == canonical
    assert transcript.detected_language_raw == tag
    turn = make3("Turn", language=tag, language_raw=tag)
    assert turn.language == canonical
    assert turn.language_raw == tag
    reloaded = entity_class3("Transcript").model_validate_json(transcript.model_dump_json())
    assert reloaded.detected_language_raw == tag


@pytest.mark.parametrize("case", _params(TAG_CASES))
def test_declared_language_names_no_script(case: Case) -> None:
    """N14: a declared language is a configured language without a script subtag. It is never
    copied into an observed field."""
    tag = str(case["text"])
    canonical = str(case["expected"]["canonical"])
    if case["expected"]["declarable"]:
        transcript = make3("Transcript", **_declared_doc(tag))
        assert transcript.declared_language.language == canonical
        assert transcript.detected_language is None
        assert transcript.detected_language_raw is None
    else:
        error = assert_rejected3(entity_class3("Transcript"), _declared_doc(tag), field="language")
        assert ["declared_language", "language"] in _locs(error)


# ---------------------------------------------------------------------------- labels


@pytest.mark.parametrize("case", _params(LABEL_CASES))
def test_provider_label_is_read_by_the_helper_and_kept_raw(case: Case) -> None:
    raw = str(case["text"])
    expected = case["expected"]
    seen = api("language").observe_language_label(raw)
    assert seen.raw == raw
    assert seen.tag == expected["tag"]
    assert [warning.code for warning in seen.warnings] == expected["warnings"]
    assert all(warning.raw == raw for warning in seen.warnings)
    transcript = make3("Transcript", detected_language=seen.tag, detected_language_raw=raw)
    assert transcript.detected_language == seen.tag
    assert transcript.detected_language_raw == raw
    again = entity_class3("Transcript").model_validate_json(transcript.model_dump_json())
    assert again.detected_language_raw == raw
    turn = make3("Turn", language=seen.tag, language_raw=raw)
    assert turn.language == seen.tag
    assert turn.language_raw == raw


@pytest.mark.parametrize("case", _params(UNPARSEABLE_LABEL_CASES))
def test_unparseable_label_leaves_the_language_not_observed(case: Case) -> None:
    """ADR 0005 Decision 3 and ADR 0004 Decision 6: a raw label without a tag is untagged, so a
    language-dependent metric is not applicable with language_not_observed."""
    raw = str(case["text"])
    transcript = make3("Transcript", source="our_stt", detected_language_raw=raw)
    turn = make3("Turn", transcript_id=str(transcript.id), seq=0, language_raw=raw)
    outcome = api("results").check_language(script_sensitive_definition(), [turn], [transcript])
    assert outcome.na_reason == "language_not_observed"


# ---------------------------------------------------------------------------- negatives


@pytest.mark.parametrize("case", _params(NEGATIVE_TAG_CASES))
def test_configured_rejection_names_the_rule_and_the_path(case: Case) -> None:
    tag = str(case["text"])
    expected = case["expected"]
    configured = expected["configured"]
    supported = adapter("language", "SupportedLanguage")
    with pytest.raises(ValidationError) as caught:
        supported.validate_python(tag)
    assert caught.value.errors()[0]["type"] == configured["type"]
    shown = str(caught.value)
    if configured["message"] == "mixed_alias":
        assert mixed_alias_message() in shown
    else:
        assert SUPPORTED_MESSAGES[configured["message"]] in shown
        assert mixed_alias_message() not in shown
    with pytest.raises(ValidationError):
        supported.validate_json(json.dumps(tag))
    path = [str(expected["path"])]
    assert path in _locs(assert_rejected_name("Persona", field="language", language=tag))
    assert path in _locs(assert_rejected_name("Evaluator", field="language", language=tag))
    scripted = {"index": 0, "text": "Hello", "language": tag}
    assert path in _locs(assert_rejected(_scripted_turn(), scripted, field="language"))
    item = assert_rejected3(entity_class3("DatasetItem"), doc3("DatasetItem", language=tag))
    assert path in _locs(item)
    declared = assert_rejected3(entity_class3("Transcript"), _declared_doc(tag), field="language")
    assert ["declared_language", "language"] in _locs(declared)


@pytest.mark.parametrize("case", _params(NEGATIVE_TAG_CASES))
def test_negative_tag_as_an_observation(case: Case) -> None:
    """ADR 0005 Decision 2: observed tags are syntax-only. A well-formed excluded tag is kept
    (canonical letter case, raw label verbatim) and the metric reads it as unsupported; a string
    that is not a tag is rejected by the type and can still be kept as a raw label."""
    tag = str(case["text"])
    expected = case["expected"]
    observed = expected["observed"]
    observed_type = adapter("language", "ObservedLanguageTag")
    if observed["outcome"] == "preserved":
        assert observed_type.validate_python(tag) == observed["tag"]
        transcript = make3("Transcript", detected_language=tag, detected_language_raw=tag)
        assert transcript.detected_language == observed["tag"]
        assert transcript.detected_language_raw == tag
        turn = make3("Turn", language=tag, language_raw=tag)
        assert turn.language == observed["tag"]
        assert turn.language_raw == tag
        assert metric_flag(tag) == expected["metric_flag"]
    else:
        with pytest.raises(ValidationError):
            observed_type.validate_python(tag)
        assert_rejected3(entity_class3("Turn"), turn_doc(language=tag), field="language")
        doc = transcript_doc(detected_language=tag)
        assert_rejected3(entity_class3("Transcript"), doc, field="detected_language")
        kept = make3("Transcript", detected_language_raw=tag)
        assert kept.detected_language is None
        assert kept.detected_language_raw == tag


def test_mixed_alias_is_a_region_for_observations_and_a_message_for_configuration() -> None:
    """The mixed-language alias is rejected as configured with the mixed-language message and is
    the ordinary region EN as an observation (ADR 0005 Decision 5, I6, I6b); English with the
    region EN gets the ordinary region message."""
    alias = [c for c in NEGATIVE_TAG_CASES if c["category"] == "mixed_alias"]
    assert len(alias) >= 9
    lookalike = [c for c in NEGATIVE_TAG_CASES if c["category"] == "mixed_alias_lookalike"]
    assert [c["expected"]["configured"]["message"] for c in lookalike] == ["region"]
    for case in alias:
        assert case["expected"]["configured"]["message"] == "mixed_alias"
        assert case["expected"]["observed"]["tag"].endswith("-EN")
        assert case["expected"]["metric_flag"] is None


# ------------------------------------------------------------- Latin letters and wrong scripts


@pytest.mark.parametrize("case", _params(FLAGGED_TEXT_CASES))
def test_flagged_text_is_stored_and_flagged_by_the_observed_tag(case: Case) -> None:
    """Latin-letter text for an Indic language, or Indic text with Latin tokens. Observed text is
    stored verbatim; the script comes from the observed tag (ADR 0004 Decision 7): a script
    subtag outside the language's native scripts makes a script-sensitive metric not applicable,
    a plain region tag does not (the schema does not scan the text; see the task report)."""
    text = str(case["text"])
    language = str(case["language"])
    expected = case["expected"]
    assert expected["stored"] == "preserved"
    found = scripts_in(text)
    native = set(api("language").allowed_scripts(language))
    region_tag = f"{language}-IN"
    assert metric_flag(region_tag) == expected["metric_flag_with_region_tag"]
    if expected["flag"] == "wrong_script_for_language":
        assert found
        assert found.isdisjoint(native), (sorted(found), sorted(native))
        script_tag = f"{language}-{min(found)}"
        assert metric_flag(script_tag) == expected["metric_flag_with_script_tag"]
        with pytest.raises(ValidationError):
            adapter("language", "SupportedLanguage").validate_python(script_tag)
        turn = make3("Turn", text=text, language=script_tag, language_raw=script_tag)
        assert turn.text == text
        assert turn.language == script_tag
    else:
        assert expected["flag"] == "contains_latin_tokens"
        assert found == native | {"Latn"}, sorted(found)
        turn = make3("Turn", text=text, language=region_tag)
        assert turn.text == text, "a turn with a few Latin-letter words is valid and verbatim"


# ------------------------------------------------------------------ T-DRIFT and fixture hygiene


def test_fixtures_outside_negatives_hold_no_excluded_tokens() -> None:
    """ADR 0005 Decision 10: only the negatives directory may spell excluded languages and the
    mixed-language alias. Every other fixture file, this module and its helper are clean, both as
    written and as decoded (JSON escapes cannot hide a token)."""
    scanned = 0
    for path in fixture_files():
        if NEGATIVES_DIR in path.parents:
            continue
        written = path.read_text(encoding="utf-8")
        assert tdrift_hits(written) == [], (path.name, tdrift_hits(written))
        if path.suffix == ".json":
            for string in iter_json_strings(json.loads(written)):
                assert tdrift_hits(string) == [], (path.name, string)
        scanned += 1
    assert scanned == len(LANGUAGE_FILES) + 2
    for module in (Path(__file__), HERE / "_support_b10.py"):
        assert tdrift_hits(module.read_text(encoding="utf-8")) == [], module.name


def test_tdrift_matcher_is_not_vacuous() -> None:
    """The approximation finds what it should (the negatives file, assembled tags and names) and
    leaves ordinary prose alone."""
    negatives = (NEGATIVES_DIR / NEGATIVES_FILE).read_text(encoding="utf-8")
    assert len(tdrift_hits(negatives)) >= 20
    assert tdrift_hits('"' + "b" + "n" + '"') != []
    assert tdrift_hits("b" + "n" + "-IN") != []
    assert tdrift_hits("hi" + "-" + "en") != []
    assert tdrift_hits("code" + "-" + "mix") != []
    assert tdrift_hits("U" + "rdu") != []
    assert tdrift_hits("works as it is, or else it fails") == []


def test_this_module_and_its_helper_are_ascii_only() -> None:
    for module in (Path(__file__), HERE / "_support_b10.py"):
        module.read_bytes().decode("ascii")


def test_fixtures_hold_no_personal_data_shapes() -> None:
    """Synthetic strings only: no address, link, signed number or long digit run."""
    for case in EVERY:
        text = str(case["text"])
        assert "@" not in text, case["id"]
        assert "http" not in text.lower(), case["id"]
        assert not re.search(r"\+\d", text), case["id"]
        assert not re.search(r"\d{7,}", text), case["id"]


def test_fixtures_are_not_in_the_private_directory() -> None:
    assert "private" not in LANGUAGES_DIR.parts
    assert "private" not in NEGATIVES_DIR.parts


def test_readmes_state_the_review_status_and_list_every_file() -> None:
    readme = (LANGUAGES_DIR / "README.md").read_text(encoding="utf-8")
    assert "has NOT been done" in readme
    assert "needs_native_review" in readme
    for name in (*LANGUAGE_FILES, CROSS_CUTTING_FILE):
        assert name in readme, name
    assert "negatives/" in readme
    negatives_readme = (NEGATIVES_DIR / "README.md").read_text(encoding="utf-8")
    assert "has NOT been done" in negatives_readme
    assert NEGATIVES_FILE in negatives_readme
    assert "fixtures/languages/negatives/" in negatives_readme, "names the allowlist entry needed"


def test_normalization_facts_dump() -> None:
    """Computes the Unicode normalization facts of every case with ``unicodedata`` only. When the
    environment variable TRACER_LANGUAGE_FACTS_OUT names a file, the facts are written there as
    JSON (evidence for the batch; the fixtures are never modified)."""
    facts = [facts_for(case) for case in EVERY]
    assert EVERY
    assert len(facts) == len(EVERY)
    assert {item["unidata_version"] for item in facts} == {unicodedata.unidata_version}
    target = os.environ.get("TRACER_LANGUAGE_FACTS_OUT")
    if target:
        document = {"unidata_version": unicodedata.unidata_version, "cases": facts}
        Path(target).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
