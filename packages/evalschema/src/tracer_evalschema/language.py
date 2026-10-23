# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Language tags: configured (``SupportedLanguage``) and observed (``ObservedLanguageTag``).

ADR 0005. Both types validate a BCP-47 tag (RFC 5646) and return it in canonical letter case
(language lower case, script title case, region upper case, everything else lower case); no other
rewriting is done and white space is never trimmed.

- ``SupportedLanguage``: one of the nine configured languages of ``data/languages.json``, with
  an optional native script and an optional region from the vendored IANA subset (IN, US, GB).
- ``ObservedLanguageTag``: syntax only (the ``langtag`` production or a private-use-only tag,
  primary subtag of 2-3 letters, grandfathered tags rejected). It never reads a data file.
- ``normalize_language_label`` / ``observe_language_label``: the pure label helpers importers
  use for provider labels (Decision 3). They keep the raw label and never trim it; trimming
  surrounding white space is an importer step (P1-31), done before the helper is called.

- ``SupportedLanguageNoScript``: a ``SupportedLanguage`` without a script subtag (a user's
  declaration of the language of an untagged import names a language, never a script; N14).
- ``script_subtag`` and ``allowed_scripts``: the script of a tag and the native scripts of a
  configured language, for the ``unsupported_script`` step (ADR 0004 Decision 7).

Inside a model that hides its input (``_hiding.values_hidden()``) the messages name the rule and
the allowed form, never the rejected tag or one of its subtags (ADR 0007 Decision 8 (c), N42).

This module defines ``CODEMIX_UNSUPPORTED_MESSAGE`` and therefore spells the rejected alias; it
is on the T-DRIFT allowlist (ADR 0005 Decision 10).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import values_hidden
from tracer_evalschema.data_loader import package_data
from tracer_evalschema.errors import DataFileError
from tracer_evalschema.text import VerbatimText

__all__ = [
    "CODEMIX_UNSUPPORTED_MESSAGE",
    "LanguageLabelWarning",
    "ObservedLanguage",
    "ObservedLanguageTag",
    "SupportedLanguage",
    "SupportedLanguageNoScript",
    "allowed_scripts",
    "normalize_language_label",
    "observe_language_label",
    "primary_language",
    "script_subtag",
]

CODEMIX_UNSUPPORTED_MESSAGE = (
    f"code-mix is not supported in {SCHEMA_VERSION}: a configured language tag names one "
    "language, and a second language code in the region position (hi-en) is not an alias for "
    "mixed-language speech"
)

# ------------------------------------------------------------------------ RFC 5646 grammar

# The 26 tags of the RFC 5646 section 2.1 `grandfathered` production, compared with the whole
# tag in any letter case (P-12, revision 7).
GRANDFATHERED_TAGS: tuple[str, ...] = (
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
)
_GRANDFATHERED_LOWER = frozenset(tag.lower() for tag in GRANDFATHERED_TAGS)

# Fragments of the RFC 5646 ABNF, written so that one spelling serves the Python validator and
# the ECMAScript JSON Schema pattern (letters of either case, ASCII only).
_ALPHA = "[A-Za-z]"
_ALNUM = "[A-Za-z0-9]"
_LANGUAGE = f"{_ALPHA}{{2,3}}"  # P-12: primary subtag of 2-3 letters only
_EXTLANG = f"{_ALPHA}{{3}}(?:-{_ALPHA}{{3}}){{0,2}}"  # extlang = 3ALPHA *2("-" 3ALPHA)
_SCRIPT = f"{_ALPHA}{{4}}"
_REGION = f"(?:{_ALPHA}{{2}}|[0-9]{{3}})"
_VARIANT = f"(?:{_ALNUM}{{5,8}}|[0-9]{_ALNUM}{{3}})"
_EXTENSION = f"[0-9A-WYZa-wyz](?:-{_ALNUM}{{2,8}})+"
_PRIVATE_USE = f"[Xx](?:-{_ALNUM}{{1,8}})+"

_LANGTAG_PARTS = re.compile(
    f"(?P<language>{_LANGUAGE})(?:-(?P<extlang>{_EXTLANG}))?(?:-(?P<script>{_SCRIPT}))?"
    f"(?:-(?P<region>{_REGION}))?(?P<variants>(?:-{_VARIANT})*)"
    f"(?P<extensions>(?:-{_EXTENSION})*)(?:-(?P<private_use>{_PRIVATE_USE}))?"
    f"|(?P<private_only>{_PRIVATE_USE})",
    re.ASCII,
)


def _any_case(text: str) -> str:
    """ECMAScript pattern for ``text`` in any ASCII letter case."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in text)


_GRANDFATHERED_ALTERNATION = "|".join(_any_case(tag) for tag in GRANDFATHERED_TAGS)
OBSERVED_LANGUAGE_PATTERN = (
    f"^(?!(?:{_GRANDFATHERED_ALTERNATION})$)"
    f"(?:{_LANGUAGE}(?:-{_EXTLANG})?(?:-{_SCRIPT})?(?:-{_REGION})?(?:-{_VARIANT})*"
    f"(?:-{_EXTENSION})*(?:-{_PRIVATE_USE})?|{_PRIVATE_USE})$"
)
"""JSON Schema pattern of ``ObservedLanguageTag`` (the same grammar as the validator)."""


@dataclass(frozen=True)
class _ParsedTag:
    language: str
    extlang: str | None
    script: str | None
    region: str | None
    variants: str
    extensions: str
    private_use: str | None
    private_only: str | None

    def canonical(self) -> str:
        """The tag in RFC 5646 canonical letter case (ADR 0005 Decision 1)."""
        if self.private_only is not None:
            return self.private_only.lower()
        parts = [self.language.lower()]
        if self.extlang is not None:
            parts.append(self.extlang.lower())
        if self.script is not None:
            parts.append(self.script.title())
        if self.region is not None:
            parts.append(self.region.upper())
        tag = "-".join(parts) + self.variants.lower() + self.extensions.lower()
        if self.private_use is not None:
            tag += "-" + self.private_use.lower()
        return tag


def _parse(tag: str) -> _ParsedTag | None:
    """Parse a well-formed tag, or return ``None`` (grandfathered tags included)."""
    if tag.lower() in _GRANDFATHERED_LOWER:
        return None
    match = _LANGTAG_PARTS.fullmatch(tag)
    if match is None:
        return None
    return _ParsedTag(
        language=match["language"] or "",
        extlang=match["extlang"],
        script=match["script"],
        region=match["region"],
        variants=match["variants"] or "",
        extensions=match["extensions"] or "",
        private_use=match["private_use"],
        private_only=match["private_only"],
    )


def _not_a_tag(value: str) -> PydanticCustomError:
    if values_hidden():
        return PydanticCustomError(
            "language_tag",
            "not a well-formed BCP-47 language tag (RFC 5646; primary subtag of 2-3 letters; "
            "grandfathered tags are not accepted)",
        )
    return PydanticCustomError(
        "language_tag",
        "{value} is not a well-formed BCP-47 language tag (RFC 5646; primary subtag of 2-3 "
        "letters; grandfathered tags are not accepted)",
        {"value": repr(value)},
    )


# ------------------------------------------------------------------------ ObservedLanguageTag


def _validate_observed(value: str) -> str:
    parsed = _parse(value)
    if parsed is None:
        raise _not_a_tag(value)
    return parsed.canonical()


class _ObservedRule:
    """Syntax-only validation of an observed tag; never reads a data file (ADR 0005 I11)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            _validate_observed, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": OBSERVED_LANGUAGE_PATTERN}


ObservedLanguageTag = Annotated[str, _ObservedRule()]
"""A language tag reported by a provider or source: well-formed, never checked for support."""


# -------------------------------------------------------------------------- SupportedLanguage


@dataclass(frozen=True)
class _LanguageData:
    scripts: Mapping[str, tuple[str, ...]]  # configured language -> allowed native scripts
    regions: tuple[str, ...]  # regions of the vendored IANA subset


def _load_language_data() -> _LanguageData:
    try:
        languages = package_data("languages.json")["languages"]
        scripts = {row["language"]: tuple(row["allowed_scripts"]) for row in languages}
        records = package_data("iana_subtag_registry_subset.json")["records"]
        regions = tuple(row["subtag"] for row in records if row["type"] == "region")
    except (KeyError, TypeError) as exc:
        raise DataFileError(f"malformed language data file: {exc!r}") from exc
    if not scripts or not regions:
        raise DataFileError("the language data files list no language or no region")
    return _LanguageData(MappingProxyType(scripts), regions)


_DATA = _load_language_data()


def _rejected(reason: str) -> PydanticCustomError:
    return PydanticCustomError("supported_language", "{reason}", {"reason": reason})


def _validate_supported(value: str) -> str:
    parsed = _parse(value)
    if parsed is None or parsed.private_only is not None:
        raise _not_a_tag(value)
    hidden = values_hidden()
    # Inside a model that hides its input no part of the tag is quoted (ADR 0007 Decision 8 (c)).
    tag = "" if hidden else f"{value!r}: "
    language = parsed.language.lower()
    configured = ", ".join(_DATA.scripts)
    if parsed.extlang is not None or language not in _DATA.scripts:
        named = "the language" if hidden else f"language {parsed.language!r}"
        raise _rejected(f"{tag}{named} is not a configured language (configured: {configured})")
    region = parsed.region.upper() if parsed.region is not None else None
    if language != "en" and region == "EN":
        raise _rejected(f"{tag}{CODEMIX_UNSUPPORTED_MESSAGE}")
    if parsed.variants or parsed.extensions or parsed.private_use is not None:
        raise _rejected(
            f"{tag}variants, extensions and private-use subtags are not allowed in a configured "
            "language"
        )
    if parsed.script is not None and parsed.script.title() not in _DATA.scripts[language]:
        allowed = ", ".join(_DATA.scripts[language])
        if hidden:
            raise _rejected(
                "the script subtag is not a native script of the language (a configured language "
                "is written in its native script only)"
            )
        raise _rejected(
            f"{tag}script {parsed.script.title()!r} is not a native script of "
            f"{language!r} (allowed: {allowed})"
        )
    if region is not None and region not in _DATA.regions:
        regions = ", ".join(_DATA.regions)
        named = "the region" if hidden else f"region {region!r}"
        raise _rejected(
            f"{tag}{named} is not supported for a configured language "
            f"(supported regions: {regions}); adding a region is a data change to the vendored "
            "IANA subtag subset"
        )
    return parsed.canonical()


def _supported_pattern() -> str:
    languages = "|".join(
        _any_case(language) + "(?:-(?:" + "|".join(_any_case(s) for s in scripts) + "))?"
        for language, scripts in _DATA.scripts.items()
    )
    regions = "|".join(_any_case(region) for region in _DATA.regions)
    return f"^(?:{languages})(?:-(?:{regions}))?$"


SUPPORTED_LANGUAGE_PATTERN = _supported_pattern()
"""JSON Schema pattern of ``SupportedLanguage``, built from the same data files."""


class _SupportedRule:
    """Validation against ``languages.json`` and the vendored IANA subset (ADR 0005 Decision 1)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            _validate_supported, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": SUPPORTED_LANGUAGE_PATTERN}


SupportedLanguage = Annotated[str, _SupportedRule()]
"""A configured language: one of the nine, native script only, region IN, US or GB."""


def primary_language(tag: str) -> str:
    """The primary language subtag in lower case (``hi-IN`` and ``hi`` share ``hi``)."""
    return tag.partition("-")[0].lower()


def script_subtag(tag: str) -> str | None:
    """The script subtag of a well-formed tag in title case (``hi-Latn-IN`` -> ``Latn``), or
    ``None`` when the tag has none or is not a well-formed tag."""
    parsed = _parse(tag)
    if parsed is None or parsed.script is None:
        return None
    return parsed.script.title()


def allowed_scripts(language: str) -> tuple[str, ...]:
    """The native scripts of a configured language (``languages.json``); ``()`` for a language
    that is not configured."""
    return _DATA.scripts.get(language.lower(), ())


def _without_script(tag: str) -> str:
    if script_subtag(tag) is not None:
        if values_hidden():
            raise _rejected("a declared language names no script subtag (language and region only)")
        raise _rejected(
            f"{tag!r}: a declared language names no script subtag (language and region only)"
        )
    return tag


def _supported_no_script_pattern() -> str:
    languages = "|".join(_any_case(language) for language in _DATA.scripts)
    regions = "|".join(_any_case(region) for region in _DATA.regions)
    return f"^(?:{languages})(?:-(?:{regions}))?$"


SUPPORTED_LANGUAGE_NO_SCRIPT_PATTERN = _supported_no_script_pattern()
"""JSON Schema pattern of ``SupportedLanguageNoScript``."""


class _SupportedNoScriptRule:
    """A configured language without a script subtag (``LanguageDeclaration.language``, N14)."""

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            lambda value: _without_script(_validate_supported(value)),
            core_schema.str_schema(strict=True),
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": SUPPORTED_LANGUAGE_NO_SCRIPT_PATTERN}


SupportedLanguageNoScript = Annotated[str, _SupportedNoScriptRule()]
"""A configured language with an optional region and no script subtag (``mr-IN``, ``mr``)."""


# ------------------------------------------------------------------------- provider labels

LANGUAGE_LABEL_NORMALIZED = "language_label_normalized"
LANGUAGE_LABEL_UNPARSEABLE = "language_label_unparseable"


class LanguageLabelWarning(BaseModel):
    """What the label helper changed or could not read; the importer turns each one into an
    ``IngestWarning`` with the same ``code`` and ``raw`` (P1-10c)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Annotated[str, ClosedSet((LANGUAGE_LABEL_NORMALIZED, LANGUAGE_LABEL_UNPARSEABLE))]
    raw: VerbatimText


class ObservedLanguage(BaseModel):
    """A provider label and the tag read from it (``Transcript.detected_language(_raw)``,
    ``Turn.language(_raw)``). ``raw`` is kept code point for code point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tag: ObservedLanguageTag | None = None
    raw: VerbatimText | None = None
    warnings: list[LanguageLabelWarning] = Field(default_factory=list)


def normalize_language_label(raw: str) -> tuple[str | None, list[LanguageLabelWarning]]:
    """Read a provider language label (ADR 0005 Decision 3). Pure and total.

    ``_`` becomes ``-`` and the result is validated as an observed tag in canonical case.
    Returns ``(tag, [])`` for a clean tag (a case-only difference is not a change),
    ``(tag, [language_label_normalized])`` when an underscore was replaced, and
    ``(None, [language_label_unparseable])`` when the label is not a tag. White space is not
    trimmed here (an importer trims surrounding white space before calling, P1-31). Every
    warning carries the label as given; the list is new on every call.
    """
    candidate = raw.replace("_", "-")
    parsed = _parse(candidate)
    if parsed is None:
        return None, [LanguageLabelWarning(code=LANGUAGE_LABEL_UNPARSEABLE, raw=raw)]
    if candidate != raw:
        return parsed.canonical(), [LanguageLabelWarning(code=LANGUAGE_LABEL_NORMALIZED, raw=raw)]
    return parsed.canonical(), []


def observe_language_label(raw: str | None) -> ObservedLanguage:
    """The tag, the raw label and the warnings for one provider label (``None``: no label)."""
    if raw is None:
        return ObservedLanguage()
    tag, warnings = normalize_language_label(raw)
    return ObservedLanguage(tag=tag, raw=raw, warnings=warnings)
