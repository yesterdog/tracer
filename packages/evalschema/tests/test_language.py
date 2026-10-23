# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04c: SupportedLanguage, ObservedLanguageTag, raw label helpers (ADR 0005, SCOPE rev 5).

Assumed public API (docs/phase1/api-surface.md) in ``tracer_evalschema.language``:
``SupportedLanguage`` and ``ObservedLanguageTag`` (validate to the canonical-case ``str``),
``primary_language()``, ``normalize_language_label()``, ``observe_language_label()``,
``LanguageLabelWarning`` and ``CODEMIX_UNSUPPORTED_MESSAGE``.

This module carries the negative parameters of the configured-language rules, so it is one of the
test modules on the T-DRIFT allowlist (ADR 0005 Decision 10), next to test_data_files.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ._support import DATA_DIR, adapter, api

NINE = ["en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu"]
SPECIAL_AND_EXTENSION = ["und", "mul", "zxx", "hi-x-foo", "hi-u-nu-deva", "hi-1901"]

# RFC 5646 section 2.1, the `grandfathered` production, verbatim: 17 irregular + 9 regular = 26.
GRANDFATHERED = [
    "en-GB-oed",
    "i-ami",
    "i-bnn",
    "i-default",
    "i-enochian",
    "i-hak",
    "i-klingon",
    "i-lux",
    "i-mingo",
    "i-navajo",
    "i-pwn",
    "i-tao",
    "i-tay",
    "i-tsu",
    "sgn-BE-FR",
    "sgn-BE-NL",
    "sgn-CH-DE",
    "art-lojban",
    "cel-gaulish",
    "no-bok",
    "no-nyn",
    "zh-guoyu",
    "zh-hakka",
    "zh-min",
    "zh-min-nan",
    "zh-xiang",
]


def _supported() -> TypeAdapter[Any]:
    return adapter("language", "SupportedLanguage")


def _observed() -> TypeAdapter[Any]:
    return adapter("language", "ObservedLanguageTag")


def _error_text(adapter: TypeAdapter[Any], value: str) -> str:
    with pytest.raises(ValidationError) as caught:
        adapter.validate_python(value)
    return str(caught.value)


# ---------------------------------------------------------------------- SupportedLanguage


@pytest.mark.parametrize("tag", ["hi-IN", "en-US", "en-GB", "pa-Guru-IN", "ml", "gu-IN"])
def test_supported_accepts(tag: str) -> None:
    adapter = _supported()
    assert adapter.validate_python(tag) == tag, "hi-IN round-trips unchanged"
    assert adapter.validate_json(json.dumps(tag)) == tag
    assert adapter.dump_python(tag, mode="json") == tag
    assert adapter.dump_json(tag) == json.dumps(tag).encode()


def test_supported_accepts_every_configured_language_code() -> None:
    """Proposed name. The nine bare codes and each with the IN region (en also US, GB)."""
    adapter = _supported()
    for code in NINE:
        assert adapter.validate_python(code) == code
        assert adapter.validate_python(f"{code}-IN") == f"{code}-IN"
    assert adapter.validate_python("en-US") == "en-US"
    assert adapter.validate_python("en-GB") == "en-GB"


def test_canonical_case() -> None:
    adapter = _supported()
    assert adapter.validate_python("HI-in") == "hi-IN"
    cases = {
        "hi-in": "hi-IN",
        "HI-IN": "hi-IN",
        "Hi-In": "hi-IN",
        "EN-us": "en-US",
        "PA-guru-in": "pa-Guru-IN",
        "pa-GURU": "pa-Guru",
        "ML": "ml",
    }
    for raw, canonical in cases.items():
        assert adapter.validate_python(raw) == canonical, raw
    observed = _observed()
    assert observed.validate_python("HI-in") == "hi-IN", "same case canonicalization (ADR 0005)"


@pytest.mark.parametrize("tag", ["bn", "ur", "or", "as", "pa-Arab"])
def test_supported_rejects(tag: str) -> None:
    adapter = _supported()
    message = _error_text(adapter, tag)
    assert tag.split("-")[-1] in message or tag in message, "the message names what was rejected"
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(tag))
    # the same primary subtag with a region is rejected too (for the excluded languages)
    if "-" not in tag:
        with pytest.raises(ValidationError):
            adapter.validate_python(f"{tag}-IN")


def test_supported_script_allowed_only_if_native() -> None:
    """Proposed name. ADR 0005 Decision 1: a script subtag is accepted only if native."""
    adapter = _supported()
    for ok in (
        "hi-Deva",
        "mr-Deva-IN",
        "te-Telu",
        "ta-Taml-IN",
        "kn-Knda",
        "ml-Mlym",
        "gu-Gujr",
        "en-Latn",
        "pa-Guru",
    ):
        assert adapter.validate_python(ok) == ok
    for bad in ("hi-Arab", "hi-Beng", "hi-Guru", "pa-Deva", "ta-Latn-IN", "en-Deva", "en-Cyrl"):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_supported_region_outside_vendored_set_rejected() -> None:
    adapter = _supported()
    regions = {"ta-LK": "LK", "en-AU": "AU", "en-EN": "EN"}  # en-EN: ordinary region message
    for tag, region in regions.items():
        message = _error_text(adapter, tag)
        assert region in message, "the message names the rejected region"
        for supported in ("IN", "US", "GB"):
            assert supported in message, "the message lists the supported regions"
        assert "data change" in message, "the message says adding a region is a data change"
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(tag))
    # the three vendored regions are accepted for a configured language
    for tag in ("en-IN", "en-US", "en-GB", "ta-IN", "pa-Guru-IN"):
        assert adapter.validate_python(tag) == tag
    # numeric (UN M.49) and unknown regions are not in the vendored set either
    for tag in ("en-001", "hi-ZZ", "ta-SG"):
        with pytest.raises(ValidationError):
            adapter.validate_python(tag)


@pytest.mark.parametrize("tag", ["ta-LK", "en-AU"])
def test_observed_accepts_regions_outside_vendored_set(tag: str) -> None:
    """P-14: observed tags are unaffected by the vendored region set."""
    adapter = _observed()
    assert adapter.validate_python(tag) == tag
    assert adapter.validate_json(json.dumps(tag)) == tag
    for other in ("hi-PK", "en-001", "bn-BD", "ta-SG", "en-ZA"):
        assert adapter.validate_python(other) == other


def test_hi_en_rejected_with_codemix_message() -> None:
    language = api("language")
    adapter = _supported()
    assert isinstance(language.CODEMIX_UNSUPPORTED_MESSAGE, str)
    assert language.CODEMIX_UNSUPPORTED_MESSAGE
    for tag in ("hi-en", "hi-EN", "HI-EN", "hi-En"):
        message = _error_text(adapter, tag)
        assert language.CODEMIX_UNSUPPORTED_MESSAGE in message, tag
    lowered = language.CODEMIX_UNSUPPORTED_MESSAGE.lower()
    assert re.search(r"code-?mix", lowered)
    assert "unsupported" in lowered or "not supported" in lowered


def _codemix_and_region_messages(tag: str) -> tuple[bool, bool]:
    """(has the unsupported-mixed-language message, has the unsupported-region message)."""
    language = api("language")
    message = _error_text(_supported(), tag)
    has_codemix = language.CODEMIX_UNSUPPORTED_MESSAGE in message
    has_region = "data change" in message and all(r in message for r in ("IN", "US", "GB"))
    return has_codemix, has_region


def test_codemix_message_applies_to_the_eight_non_english_languages_with_region_EN() -> None:
    """Proposed name. ADR 0005 Decision 1 (revision 7): primary subtag one of the eight configured
    non-English languages and region subtag EN in any case -> the mixed-language message, which
    takes precedence over the region message (EN is not a vendored region)."""
    eight = [code for code in NINE if code != "en"]
    assert len(eight) == 8
    for code in eight:
        for region in ("en", "EN", "En", "eN"):
            for tag in (f"{code}-{region}", f"{code.upper()}-{region}", f"{code}-Deva-{region}"):
                if tag.startswith(f"{code}-Deva-") and code not in ("hi", "mr"):
                    continue  # Deva is not a native script of the other languages
                has_codemix, has_region = _codemix_and_region_messages(tag)
                assert has_codemix, f"{tag}: expected the mixed-language message"
                assert not has_region, f"{tag}: the region message must not win over it"
        with pytest.raises(ValidationError):
            _supported().validate_json(json.dumps(f"{code}-EN"))


def test_en_EN_and_other_pairs_get_the_ordinary_messages() -> None:
    """Proposed name. ``en-EN`` is English with an unsupported region, not a code-mix alias; no
    other pair is read as code-mix either (api-surface 15a item 11)."""
    for tag in ("en-EN", "en-en", "EN-EN", "EN-en"):
        has_codemix, has_region = _codemix_and_region_messages(tag)
        assert not has_codemix, tag
        assert has_region, tag
        assert "EN" in _error_text(_supported(), tag), "the message names the rejected region"
    for tag in ("bn-EN", "xx-EN", "hi-ZZ", "ta-LK", "hi-hi"):
        has_codemix, _ = _codemix_and_region_messages(tag)
        assert not has_codemix, tag
    # as observations all of these are well-formed (en-EN is region EN of English)
    assert _observed().validate_python("en-en") == "en-EN"
    assert _observed().validate_python("EN-EN") == "en-EN"


def test_observed_hi_en_is_region_EN() -> None:
    adapter = _observed()
    assert adapter.validate_python("hi-en") == "hi-EN"
    assert adapter.validate_python("hi-EN") == "hi-EN"
    assert adapter.validate_json('"hi-en"') == "hi-EN"


def test_configured_rejects_hi_Latn() -> None:
    adapter = _supported()
    for tag in ("hi-Latn", "hi-latn", "hi-Latn-IN", "mr-Latn", "ta-Latn"):
        message = _error_text(adapter, tag)
        assert "Latn" in message or "latn" in message.lower()


def test_observed_accepts_hi_Latn() -> None:
    adapter = _observed()
    assert adapter.validate_python("hi-Latn") == "hi-Latn"
    assert adapter.validate_python("hi-Latn-IN") == "hi-Latn-IN"
    assert adapter.validate_python("ta-latn") == "ta-Latn"


@pytest.mark.parametrize("tag", SPECIAL_AND_EXTENSION)
def test_rejects(tag: str) -> None:
    """SCOPE name; target type SupportedLanguage: special, extension, variant, private use."""
    with pytest.raises(ValidationError):
        _supported().validate_python(tag)
    with pytest.raises(ValidationError):
        _supported().validate_json(json.dumps(tag))


def test_observed_accepts_special_and_extension_tags() -> None:
    adapter = _observed()
    for tag in SPECIAL_AND_EXTENSION:
        assert adapter.validate_python(tag) == tag, tag
        assert adapter.validate_json(json.dumps(tag)) == tag


def test_observed_accepts_bn_IN() -> None:
    adapter = _observed()
    assert adapter.validate_python("bn-IN") == "bn-IN"
    assert adapter.validate_python("BN-in") == "bn-IN"
    assert adapter.validate_json('"bn-IN"') == "bn-IN"
    with pytest.raises(ValidationError):
        _supported().validate_python("bn-IN")


@pytest.mark.parametrize("tag", ["xq-IN", "hi-AA", "qaa"])
def test_observed_accepts_well_formed_unregistered(tag: str) -> None:
    assert _observed().validate_python(tag) == tag


def test_observed_accepts_private_use_only_and_extlang_forms() -> None:
    """Proposed name. ADR 0005 Decision 2: langtag and private-use-only tags are well-formed;
    an optional extlang may follow a 2-3 letter primary subtag (P-12)."""
    adapter = _observed()
    for tag in ("x-foo", "x-whatever-1", "zh-yue", "zh-cmn-Hans-CN", "en-GB", "fil", "ast-ES"):
        assert adapter.validate_python(tag) == tag, tag


_AUDIT_CHILD = textwrap.dedent(
    """
    import json
    import os
    import sys

    from pydantic import TypeAdapter

    from tracer_evalschema import language

    adapter = TypeAdapter(language.ObservedLanguageTag)
    data_dir = sys.argv[1]
    opened = []

    def hook(event, args):
        if event == "open":
            target = args[0]
            if isinstance(target, int):
                return
            if os.path.realpath(os.fsdecode(target)).startswith(data_dir):
                opened.append(os.fsdecode(target))

    sys.addaudithook(hook)
    tags = json.loads(sys.argv[2])
    results = [adapter.validate_python(tag) for tag in tags]
    print(json.dumps({"opened": opened, "results": results}))
    """
)


def test_observed_validation_does_not_read_registry() -> None:
    """Runs observed validation in a fresh interpreter and watches every file open (audit hook):
    nothing under the package data directory may be read while validating (ADR 0005 I11)."""
    tags = ["bn-IN", "hi-Latn", "xq-IN", "und", "hi-x-foo", "ta-LK", "en-AU", "hi-en", "qaa"]
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            _AUDIT_CHILD,
            str(DATA_DIR.resolve()),
            json.dumps(tags),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["opened"] == [], f"observed validation read data files: {report['opened']}"
    assert report["results"] == [
        "bn-IN",
        "hi-Latn",
        "xq-IN",
        "und",
        "hi-x-foo",
        "ta-LK",
        "en-AU",
        "hi-EN",
        "qaa",
    ]


@pytest.mark.parametrize("tag", ["hi_IN", "hi--IN", "-hi", "hi-IN-", ""])
def test_observed_rejects_malformed(tag: str) -> None:
    adapter = _observed()
    with pytest.raises(ValidationError):
        adapter.validate_python(tag)
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(tag))


def test_observed_rejects_other_malformed_forms() -> None:
    """Proposed name. Neighbours of the SCOPE list: whitespace, newline, non-ASCII, wrong shapes."""
    adapter = _observed()
    for tag in (
        " hi-IN",
        "hi-IN ",
        "hi-IN\n",
        "hi IN",
        "h",
        "hi-I",
        "hi-\u0939\u093f\u0928\u094d\u0926\u0940",
        "\u0939\u093f",
        "hi-IN-IN",
        "hi-u",
        "hi-x",
        "hi-1",
        "12",
        "hi-\x00",
        "hi-IN\x00",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(tag)


@pytest.mark.parametrize("tag", ["hindi"])
def test_observed_primary_subtag_2_3_letters(tag: str) -> None:
    adapter = _observed()
    with pytest.raises(ValidationError):
        adapter.validate_python(tag)
    for name_like in (
        "hindi",
        "Hindi",
        "HINDI",
        "hind",
        "hindus",
        "hindust",
        "hindusta",
        "english",
        "marathi",
        "tamil",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(name_like)
        with pytest.raises(ValidationError):
            adapter.validate_python(name_like + "-IN")
    for short in ("hi", "fil", "ast", "ja", "kok"):
        assert adapter.validate_python(short) == short
    # a language NAME is not stored as a tag: the importer keeps it raw instead (see below)


@pytest.mark.parametrize("tag", ["i-klingon", "en-GB-oed", "zh-min-nan", "art-lojban"])
def test_observed_rejects_grandfathered(tag: str) -> None:
    adapter = _observed()
    with pytest.raises(ValidationError):
        adapter.validate_python(tag)
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(tag))


def test_observed_rejects_all_26_grandfathered_tags() -> None:
    """Proposed name. P-12 reads 'grandfathered' literally: the whole RFC 5646 production, in any
    letter case (the ABNF is case-insensitive), while near neighbours stay valid."""
    assert len(GRANDFATHERED) == 26
    assert len(set(GRANDFATHERED)) == 26
    adapter = _observed()
    for tag in GRANDFATHERED:
        for spelling in (tag, tag.lower(), tag.upper()):
            with pytest.raises(ValidationError):
                adapter.validate_python(spelling)
    # not on the list: remain valid observations
    for tag in ("zh-min-nan-TW", "art-lojban-x-a", "zh-yue", "no-NO", "sgn-BE"):
        assert adapter.validate_python(tag) == tag, f"{tag} is not on the grandfathered list"


def test_observed_grandfathered_is_a_whole_tag_case_insensitive_match() -> None:
    """Proposed name. ADR 0005 Decision 2 (revision 7): the whole tag is compared, in any letter
    case, with each of the 26 literal tags (RFC 5646 section 2.1.1); a longer tag that only begins
    with one is an ordinary langtag judged by the grammar."""
    adapter = _observed()
    mixed = [
        "I-Klingon",
        "En-Gb-OeD",
        "EN-gb-OED",
        "Zh-Min-Nan",
        "ZH-min-NAN",
        "Art-Lojban",
        "SGN-be-fr",
    ]
    for spelling in mixed:
        with pytest.raises(ValidationError):
            adapter.validate_python(spelling)
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(spelling))
    # every one of the 26, in title case per subtag as well
    for tag in GRANDFATHERED:
        titled = "-".join(part.title() for part in tag.split("-"))
        with pytest.raises(ValidationError):
            adapter.validate_python(titled)
    # longer tags that begin with a grandfathered tag are ordinary langtags (or fail the grammar
    # for their own reasons), never rejected as grandfathered
    for tag, canonical in (
        ("zh-min-nan-TW", "zh-min-nan-TW"),
        ("ZH-MIN-NAN-tw", "zh-min-nan-TW"),
        ("art-lojban-x-a", "art-lojban-x-a"),
        ("zh-min-x-a", "zh-min-x-a"),
        ("no-bok-x-a", "no-bok-x-a"),
        ("no-nyn-x-a", "no-nyn-x-a"),
        ("cel-gaulish-x-a", "cel-gaulish-x-a"),
        ("zh-hakka-x-a", "zh-hakka-x-a"),
    ):
        assert adapter.validate_python(tag) == canonical, tag
    # shorter or different tags that look similar are valid too
    for tag in ("zh", "art", "no", "sgn", "cel", "en-GB", "sgn-BE", "zh-yue", "ia"):
        assert adapter.validate_python(tag) == tag, tag
    # not a substring match: the grandfathered text inside a longer subtag is fine
    assert adapter.validate_python("zh-min-nan-hant") == "zh-min-nan-Hant"


def test_observed_accepts_up_to_three_extlang_subtags() -> None:
    """Proposed name. ADR 0005 Decision 2 (revision 7): a 2-3 letter primary subtag may be
    followed by at most three 3-letter extlang subtags (RFC 5646 ``extlang = 3ALPHA *2("-"
    3ALPHA)``)."""
    adapter = _observed()
    for tag in (
        "zh-yue",
        "zh-cmn-yue",
        "zh-yue-cmn-hak",  # three extlangs
        "yue-cmn-hak-nan",  # 3-letter primary, three extlangs
        "zh-yue-cmn-hak-Hans-CN",  # then script and region
        "zh-yue-Hans",
        "zh-cmn-Hans-CN",
    ):
        assert adapter.validate_python(tag) == tag, tag
        assert adapter.validate_json(json.dumps(tag)) == tag
    for tag in (
        "zh-yue-cmn-hak-nan",  # four extlangs
        "yue-cmn-hak-nan-wuu",
        "zh-yue-cmn-hak-nan-Hans",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(tag)
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(tag))
    # a 4-letter or longer primary subtag takes no extlang and is rejected (P-12)
    for tag in ("hind-yue", "hindi-yue"):
        with pytest.raises(ValidationError):
            adapter.validate_python(tag)


def test_types_do_not_trim_labels() -> None:
    """Proposed name. Revision 7 (api-surface 15a item 10): the types never trim or rewrite
    white space; a padded label is not a tag. (Trimming of provider labels is an importer step,
    E4, accepted by the user: its tests belong to the importer tasks, P1-31.)"""
    for checker in (_observed(), _supported()):
        for padded in (
            " hi-IN",
            "hi-IN ",
            " hi-IN ",
            "\thi-IN",
            "hi-IN\n",
            "hi-IN\r",
            "\u00a0hi-IN",
        ):
            with pytest.raises(ValidationError):
                checker.validate_python(padded)
            with pytest.raises(ValidationError):
                checker.validate_json(json.dumps(padded))
        assert checker.validate_python("hi-IN") == "hi-IN"
    # the raw label is kept code point for code point, padding included
    language = api("language")
    for raw in (" hi-IN ", "\thi-IN\n", "hi-IN  "):
        assert language.observe_language_label(raw).raw == raw


# --------------------------------------------------------------------------- raw labels


@pytest.mark.parametrize(
    "raw",
    ["hindi", "Hindi", "\u0939\u093f\u0928\u094d\u0926\u0940", "English (India)", "hi in"],
)
def test_importer_language_name_raw_kept_tag_empty(raw: str) -> None:
    language = api("language")
    tag, warnings = language.normalize_language_label(raw)
    assert tag is None, raw
    assert [w.code for w in warnings] == ["language_label_unparseable"], raw
    assert warnings[0].raw == raw
    seen = language.observe_language_label(raw)
    assert seen.tag is None, "the normalized field is empty"
    assert seen.raw == raw, "the raw label is kept"
    assert [w.code for w in seen.warnings] == ["language_label_unparseable"]


def test_detected_language_raw_verbatim() -> None:
    """The raw label is kept code point for code point, whether or not it parses (ADR 0005 I13)."""
    language = api("language")
    labels = [
        "hi-IN",
        "hi_IN",
        " hi-IN ",
        "HI-in",
        "hi_in\n",
        "bn_IN",
        "hindi",
        "\u0939\u093f\u0928\u094d\u0926\u0940",
        "hi-IN\u200d",
        "i-klingon",
        "",
        "  ",
        "x" * 300,
    ]
    for raw in labels:
        seen = language.observe_language_label(raw)
        assert seen.raw == raw, f"raw label changed for {raw!r}"
        assert type(seen.raw) is str
    assert language.observe_language_label(None).raw is None
    assert language.observe_language_label(None).tag is None
    assert language.observe_language_label(None).warnings == []


def test_detected_language_raw_kept_when_tag_unparseable() -> None:
    language = api("language")
    for raw in ("hindi", "hi--IN", "i-klingon", "??", "und_??", "12345", "\u0939\u093f"):
        seen = language.observe_language_label(raw)
        assert seen.tag is None, raw
        assert seen.raw == raw
        assert any(w.code == "language_label_unparseable" for w in seen.warnings), raw
        assert all(w.raw == raw for w in seen.warnings)


def test_importer_underscore_to_hyphen_with_warning_raw_kept() -> None:
    language = api("language")
    for raw, tag in (
        ("hi_IN", "hi-IN"),
        ("bn_IN", "bn-IN"),
        ("hi_in", "hi-IN"),
        ("pa_Guru_IN", "pa-Guru-IN"),
    ):
        normalized, warnings = language.normalize_language_label(raw)
        assert normalized == tag, raw
        assert [w.code for w in warnings] == ["language_label_normalized"], raw
        assert warnings[0].raw == raw, "the warning carries the raw label"
        seen = language.observe_language_label(raw)
        assert seen.tag == tag
        assert seen.raw == raw
        assert [w.code for w in seen.warnings] == ["language_label_normalized"]
    # the type itself stays strict: it rejects hi_IN and never rewrites it
    with pytest.raises(ValidationError):
        _observed().validate_python("hi_IN")
    with pytest.raises(ValidationError):
        _supported().validate_python("hi_IN")
    # a clean tag needs no warning
    assert language.normalize_language_label("hi-IN") == ("hi-IN", [])
    assert language.normalize_language_label("bn-IN") == ("bn-IN", [])
    assert language.observe_language_label("hi-IN").warnings == []


def test_normalize_language_label_is_pure_and_total() -> None:
    """Proposed name. SCOPE P1-04c: 'pure normalize_language_label(raw) -> (tag or None,
    warnings)'. It never raises, is deterministic and returns a fresh warnings list each call."""
    language = api("language")
    inputs = [
        "hi_IN",
        "hindi",
        "",
        "  ",
        "hi-IN",
        "\u0939",
        "a" * 1000,
        "i_klingon",
        "x_foo",
        "hi__IN",
        "_hi",
    ]
    for raw in inputs:
        first = language.normalize_language_label(raw)
        second = language.normalize_language_label(raw)
        assert isinstance(first, tuple)
        assert len(first) == 2
        tag, warnings = first
        assert tag is None or isinstance(tag, str)
        assert isinstance(warnings, list)
        assert first[0] == second[0]
        assert [(w.code, w.raw) for w in first[1]] == [(w.code, w.raw) for w in second[1]]
        assert first[1] is not second[1]
        if tag is not None:
            assert _observed().validate_python(tag) == tag, (
                "a returned tag is always a valid observation"
            )
    # underscores inside a label that is still malformed afterwards give no tag
    assert language.normalize_language_label("hi__IN")[0] is None
    assert language.normalize_language_label("_hi")[0] is None


def test_case_only_difference_gives_no_warning() -> None:
    """Proposed name. ADR 0005 Decision 3 (revision 7): ``language_label_normalized`` is added
    iff the underscore replacement changed the label. A case-only difference loses nothing (tags
    are case-insensitive and the raw label is kept), so it gives no warning."""
    language = api("language")
    for raw, tag in (
        ("HI-in", "hi-IN"),
        ("hi-in", "hi-IN"),
        ("HI-IN", "hi-IN"),
        ("PA-guru-in", "pa-Guru-IN"),
        ("BN-in", "bn-IN"),
        ("EN", "en"),
        ("HI-LATN", "hi-Latn"),
        ("Hi-En", "hi-EN"),
    ):
        assert language.normalize_language_label(raw) == (tag, []), raw
        seen = language.observe_language_label(raw)
        assert (seen.tag, seen.raw, seen.warnings) == (tag, raw, []), raw
    # an underscore AND a case change: exactly one warning, for the underscore, carrying the raw
    for raw, tag in (("HI_in", "hi-IN"), ("Pa_GURU_in", "pa-Guru-IN")):
        normalized, warnings = language.normalize_language_label(raw)
        assert normalized == tag
        assert [(w.code, w.raw) for w in warnings] == [("language_label_normalized", raw)]
    # a label that does not parse stays unparseable whatever its case
    assert language.normalize_language_label("HINDI")[0] is None


def test_primary_language_equivalence() -> None:
    language = api("language")
    primary = language.primary_language
    assert primary("hi-IN") == primary("hi") == "hi"
    assert primary("pa-Guru-IN") == primary("pa") == "pa"
    assert primary("hi-Deva-IN") == primary("hi-IN") == "hi"
    assert primary("hi-Latn") == "hi"
    assert primary("bn-IN") == "bn"
    assert primary("HI-in") == "hi"
    assert primary("en-GB") == primary("en-US") == "en"
    assert primary("hi-IN") != primary("mr-IN")
    # an observed hi-EN (region EN, not code-mix) has the same primary language as hi
    assert primary(_observed().validate_python("hi-en")) == "hi"


def test_supported_no_suppress_script_rewrite() -> None:
    """ADR 0005 Decision 1: no other rewriting; a suppressed script is not removed or added."""
    adapter = _supported()
    assert adapter.validate_python("hi-Deva-IN") == "hi-Deva-IN"
    assert adapter.validate_python("hi-IN") == "hi-IN", "no script is added"
    assert adapter.validate_python("en-Latn-US") == "en-Latn-US"
    assert adapter.validate_python("hi-Deva") == "hi-Deva"
    assert adapter.validate_python("pa") == "pa", "no region and no script is added"
    observed = _observed()
    assert observed.validate_python("hi-Deva-IN") == "hi-Deva-IN"
    primary = api("language").primary_language
    assert primary("hi-Deva-IN") == primary("hi-IN")
