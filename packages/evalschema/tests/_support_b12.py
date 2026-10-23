# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 18 (P1-18), shared helpers: the adversarial case registry and the adversarial fixtures.
No tests here.

Three parts:

* the specifications of the new adversarial fixtures (``SPECS``) and the functions that turn them
  into files (``render()``): every fixture is a golden valid document with one deliberate defect
  (or, for the raw-text cases, with one edit of its JSON text), written under
  ``tests/fixtures/adversarial/<case>/<name>.json`` with a ``<name>.expected.json`` next to it, like
  the invalid golden fixtures. ``tests/fixtures/adversarial/README.md`` describes the layout;
* the runner of one fixture (``run_fixture``): it loads the document with the strict loader and
  reports what happened (``Outcome``). Credential-shaped values are never stored in a fixture: the
  file holds a placeholder ``@@NAME@@`` and ``expand()`` assembles the value at run time;
* the registry helpers (``read_registry``, ``collect_node_ids``, ``run_nodes``) used by
  ``test_adversarial_registry.py``: resolve covering tests with pytest collection and run them.

Everything is ASCII; non-ASCII characters are built from code points.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import _support_b9 as b9
from ._support import FIXTURES, ROOT, api

ADVERSARIAL = FIXTURES / "adversarial"
REGISTRY_PATH = ADVERSARIAL / "registry.json"
README_PATH = ADVERSARIAL / "README.md"
TESTS_REL = "packages/evalschema/tests"

# Set in the environment of the child pytest run of ``run_nodes`` so that the registry test cannot
# start itself again.
CHILD_ENV = "TRACER_ADVERSARIAL_REGISTRY_CHILD"

EXPECTATIONS = ("rejected", "preserved", "flagged")
SCHEMA_RESULTS = ("rejects", "code_only", "accepts", "not_applicable")

# ================================================================================ credentials

# Credential-shaped values, assembled from parts so that no file of the repository holds one.
SECRETS: Mapping[str, str] = {
    "AWS_KEY": "AKIA" + "IOSFODNN7EXAMPLE",
    "PASTED_KEY": "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz0123456789",
    "GITHUB_TOKEN": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "JWT": "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiJ4eHh4eHh4In0" + "." + "c2ln",
    "PEM_HEADER": "-----BEGIN " + "RSA PRIVATE KEY-----",
}
_PLACEHOLDER = re.compile(r"@@([A-Z_]+)@@")


def expand(text: str) -> str:
    """Replace every ``@@NAME@@`` placeholder by the credential-shaped value built for it."""
    return _PLACEHOLDER.sub(lambda m: SECRETS[m.group(1)], text)


# =================================================================================== specs

RLO = chr(0x202E)
LRI = chr(0x2066)
ZWSP = chr(0x200B)
ZWJ = chr(0x200D)
ZWNJ = chr(0x200C)
BOM = chr(0xFEFF)
C1_NEL = chr(0x85)
TAG_A = chr(0xE0041)
NBSP = chr(0xA0)
CYRILLIC_E = chr(0x435)
FULLWIDTH_P = chr(0xFF50)
ARABIC_INDIC_TWO = chr(0x662)

Op = tuple[Any, ...]  # ("set", path, value) or ("del", path)


@dataclass(frozen=True)
class Spec:
    """One adversarial fixture: a golden valid document, edited, and what must happen to it."""

    case: str
    name: str
    entity: str
    expect: str
    why: str
    threat: str
    base: str = "maximal"
    ops: tuple[Op, ...] = ()
    text_edits: tuple[tuple[str, str], ...] = ()
    kind: str | None = None
    path: tuple[Any, ...] = ()
    canonical: Mapping[str, Any] = field(default_factory=dict)
    no_echo: tuple[str, ...] = ()
    schema_applies: bool = True
    minify: bool = False


def _doc(spec: Spec) -> dict[str, Any]:
    document: dict[str, Any] = b9.read_json(b9.GOLDEN / spec.entity / "valid" / f"{spec.base}.json")
    for op in spec.ops:
        target: Any = document
        for step in op[1][:-1]:
            target = target[step]
        if op[0] == "set":
            target[op[1][-1]] = op[2]
        else:
            del target[op[1][-1]]
    return document


def _text(spec: Spec) -> str:
    if spec.minify:
        compact = json.dumps(_doc(spec), ensure_ascii=False, separators=(",", ":"))
        text = b9._escape_invisible(compact) + "\n"
    else:
        text = b9.pretty(_doc(spec)).decode("utf-8")
    for old, new in spec.text_edits:
        assert text.count(old) >= 1, (spec.case, spec.name, old)
        text = text.replace(old, new, 1)
    return text


def _set(*path: Any, value: Any) -> Op:
    return ("set", tuple(path), value)


def _nested(depth: int) -> Any:
    value: Any = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _spec(case: str, name: str, entity: str, expect: str, **rest: Any) -> Spec:
    return Spec(case=case, name=name, entity=entity, expect=expect, **rest)


_PROJECT_ID = "prj_01a168a1-da00-79f4-9a2e-6310ea4bd52d"
_UUID = _PROJECT_ID[4:]
_ID_THREAT = (
    "A look-alike identifier (wrong kind, other case, other script, other separators) is "
    "mistaken for another record or slips past an allow-list that compares text."
)
_LABEL_THREAT = (
    "An invisible or direction-changing character in a name makes a different string look "
    "identical on screen, so a person or a log reader trusts the wrong record."
)
_TS_THREAT = (
    "Two spellings of one instant, or an instant that only some parsers accept, order events "
    "differently in two readers and break the timeline and any digest over it."
)
_DIGEST_THREAT = (
    "A document edited after sealing, or relabelled with a weaker or unverifiable digest scheme, "
    "is accepted as the original."
)
_UNIT_THREAT = (
    "A quantity in another unit, another spelling or another number type is read at the wrong "
    "scale and prices a call at a wrong cost without any error."
)


def _specs_json_text() -> list[Spec]:
    t = (
        "A non-JSON number literal or a number beyond the double range decodes differently in "
        "each language and turns into null, a string or an infinity in a stored record."
    )
    d = (
        "A parser differential: if a duplicate member is not an error, a reader that keeps the "
        "first value and one that keeps the last see two different documents (for example two "
        "schema versions)."
    )
    s = "Hostile JSON text; each reader must reach the same verdict or refuse."
    seg = '"confidence": 0.98'
    return [
        _spec(
            "X-01",
            "nan-literal-in-a-segment-confidence",
            "Turn",
            "rejected",
            text_edits=((seg, '"confidence": NaN'),),
            kind="ValueError",
            why="NaN is not a JSON value; the strict loader refuses the constant",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "infinity-literal-in-a-segment-confidence",
            "Turn",
            "rejected",
            text_edits=((seg, '"confidence": Infinity'),),
            kind="ValueError",
            why="Infinity is not a JSON value; the strict loader refuses the constant",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "negative-infinity-literal-in-a-segment-confidence",
            "Turn",
            "rejected",
            text_edits=((seg, '"confidence": -Infinity'),),
            kind="ValueError",
            why="-Infinity is not a JSON value; the strict loader refuses the constant",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "exponent-overflow-in-a-segment-confidence",
            "Turn",
            "rejected",
            text_edits=((seg, '"confidence": 1e999'),),
            kind="finite_number",
            path=("segments", 0, "confidence"),
            why="1e999 is valid JSON text that decodes to infinity; the field refuses it",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "negative-exponent-overflow-in-a-segment-confidence",
            "Turn",
            "rejected",
            text_edits=((seg, '"confidence": -1e999'),),
            kind="finite_number",
            path=("segments", 0, "confidence"),
            why="-1e999 decodes to minus infinity; the field refuses it",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "exponent-overflow-in-tool-arguments",
            "ToolCallEvent",
            "rejected",
            text_edits=(('"amount": 499,', '"amount": 1e999,'),),
            kind="ijson",
            path=("arguments",),
            why="tool arguments are I-JSON; infinity is not a JSON number",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-01",
            "integer-beyond-2-53-in-tool-arguments",
            "ToolCallEvent",
            "rejected",
            text_edits=(('"amount": 499,', '"amount": 12345678901234567890,'),),
            kind="ijson",
            path=("arguments",),
            why="tool arguments are I-JSON; an integer beyond 2**53 - 1 is read as a float by "
            "some readers",
            threat=t,
            schema_applies=False,
        ),
        _spec(
            "X-02",
            "escaped-spelling-of-an-existing-key",
            "Project",
            "rejected",
            text_edits=(
                (
                    '"name": "Support voice agents",',
                    '"name": "Support voice agents",\n  "\\u006eame": "Other",',
                ),
            ),
            kind="DuplicateKeyError",
            why="the keys name and \\u006eame are the same key after decoding",
            threat=d,
            schema_applies=False,
        ),
        _spec(
            "X-02",
            "schema-version-twice",
            "Project",
            "rejected",
            text_edits=(
                (
                    '"schema_version": "0.0.1",',
                    '"schema_version": "0.0.1",\n  "schema_version": "0.0.99",',
                ),
            ),
            kind="DuplicateKeyError",
            why="two schema_version members; the check of the version would otherwise depend on "
            "which one a reader keeps",
            threat=d,
            schema_applies=False,
        ),
        _spec(
            "X-02",
            "inside-metadata",
            "Project",
            "rejected",
            text_edits=(('"rank": 3,', '"rank": 3,\n      "rank": 4,'),),
            kind="DuplicateKeyError",
            why="a duplicate key deep inside user metadata is as much an error as one at the top",
            threat=d,
            schema_applies=False,
        ),
        _spec(
            "X-02",
            "inside-an-array-element",
            "Project",
            "rejected",
            text_edits=(('"model": "saaras:v4",', '"model": "saaras:v4",\n      "model": "x",'),),
            kind="DuplicateKeyError",
            why="a duplicate key inside an object that is an array element is found",
            threat=d,
            schema_applies=False,
        ),
        _spec(
            "X-02",
            "same-key-in-sibling-objects",
            "Project",
            "preserved",
            ops=(_set("metadata", value={"items": [{"k": 1}, {"k": 2}, {"k": 3}]}),),
            why="the same key in sibling objects is not a duplicate; nothing is dropped",
            threat="A duplicate check that is too eager would drop or reject legitimate data.",
        ),
        _spec(
            "X-02",
            "nfc-and-nfd-keys-are-two-keys",
            "Project",
            "preserved",
            ops=(_set("metadata", value={chr(0xE9): 1, "e" + chr(0x301): 2}),),
            why="JSON keys are compared code point by code point: the composed and the "
            "decomposed e are two keys and both are kept unchanged",
            threat="Normalizing keys would merge two members and silently change user data.",
        ),
        _spec(
            "X-02",
            "key-in-another-case-is-an-extra-member",
            "Project",
            "rejected",
            text_edits=(('"name": "Support voice agents",', '"Name": "Support voice agents",'),),
            kind="missing",
            path=("name",),
            why="member names are case sensitive: Name is not name (and the member name is "
            "then missing)",
            threat="A case-folding reader would accept a document that another reader rejects.",
            schema_applies=True,
        ),
        _spec(
            "X-03",
            "metadata-nested-to-the-limit",
            "Project",
            "preserved",
            ops=(_set("metadata", value={"deep": _nested(254)}),),
            minify=True,
            why="the document, metadata and 254 arrays make nesting depth 256, the default limit",
            threat=s,
            schema_applies=True,
        ),
        _spec(
            "X-03",
            "metadata-nested-one-past-the-limit",
            "Project",
            "rejected",
            ops=(_set("metadata", value={"deep": _nested(255)}),),
            minify=True,
            kind="DepthLimitExceeded",
            why="nesting depth 257 is one past the default limit and is a typed error",
            threat="A deeply nested document exhausts the stack of a recursive reader.",
            schema_applies=False,
        ),
        _spec(
            "X-03",
            "brackets-inside-a-string-do-not-count",
            "Project",
            "preserved",
            ops=(_set("metadata", value={"note": "[" * 4000 + "{" * 4000}),),
            why="brackets inside a JSON string are text, not nesting",
            threat="A depth check that counts brackets in strings refuses honest long text.",
        ),
        _spec(
            "X-03",
            "escaped-quote-then-brackets-inside-a-string",
            "Project",
            "preserved",
            ops=(_set("metadata", value={"note": 'say "hi" \\' + "[" * 600 + '" end'}),),
            why="an escaped quote and an escaped backslash do not end the string, so the brackets "
            "after them are still text",
            threat="A depth scanner that mis-reads string boundaries can be made to count text "
            "as nesting or to skip real nesting.",
        ),
        _spec(
            "X-03",
            "escaped-backslash-before-the-closing-quote-then-deep-nesting",
            "Project",
            "rejected",
            text_edits=(
                (
                    '"labels": [',
                    '"trap": "\\\\", "deep": ' + "[" * 300 + "]" * 300 + ',\n      "labels": [',
                ),
            ),
            kind="DepthLimitExceeded",
            why="a string ending in an escaped backslash does end there: the nesting after it is "
            "counted and is too deep",
            threat="A depth scanner that treats the quote after a backslash pair as escaped "
            "would read the nesting as text and let a stack-exhausting document through.",
            schema_applies=False,
        ),
    ]


def _label_fixture(
    name: str, entity: str, ops: tuple[Op, ...], kind: str, path: tuple[Any, ...], why: str
) -> Spec:
    return _spec(
        "X-05",
        name,
        entity,
        "rejected",
        ops=ops,
        kind=kind,
        path=path,
        why=why,
        threat=_LABEL_THREAT,
    )


def _specs_spoofing() -> list[Spec]:
    name = ("name",)
    tool = ("name",)
    cfg = ("provider_configs", 0, "config_ref")
    out = [
        _label_fixture(
            "label-with-a-right-to-left-override",
            "Project",
            (_set(*name, value="Support" + RLO + "agents"),),
            "human_label_character",
            name,
            "U+202E is not allowed in a label",
        ),
        _label_fixture(
            "label-with-a-left-to-right-isolate",
            "Project",
            (_set(*name, value="Support" + LRI + "agents"),),
            "human_label_character",
            name,
            "U+2066 is not allowed in a label",
        ),
        _label_fixture(
            "label-with-a-zero-width-space",
            "Project",
            (_set(*name, value="Support" + ZWSP + "agents"),),
            "human_label_character",
            name,
            "U+200B is not allowed in a label",
        ),
        _label_fixture(
            "label-with-a-byte-order-mark",
            "Project",
            (_set(*name, value="Support" + BOM + "agents"),),
            "human_label_character",
            name,
            "U+FEFF is not allowed in a label",
        ),
        _label_fixture(
            "label-with-a-c1-control",
            "Project",
            (_set(*name, value="Support" + C1_NEL + "agents"),),
            "human_label_character",
            name,
            "U+0085 is not allowed in a label",
        ),
        _label_fixture(
            "label-with-a-tag-character",
            "Project",
            (_set(*name, value="Support" + TAG_A + "agents"),),
            "human_label_character",
            name,
            "U+E0041 (an invisible tag character) is not allowed",
        ),
        _spec(
            "X-05",
            "label-keeps-zwj-and-zwnj-in-indic-text",
            "Project",
            "preserved",
            ops=(
                _set(
                    *name,
                    value=chr(0x915)
                    + chr(0x94D)
                    + ZWJ
                    + chr(0x937)
                    + " "
                    + chr(0x915)
                    + chr(0x94D)
                    + ZWNJ
                    + chr(0x937),
                ),
            ),
            why="ZWJ and ZWNJ are part of how Indic scripts are written and are kept unchanged "
            "(the same two joiners are the only invisible characters a label may hold)",
            threat="Refusing the joiners would make correct Indic names impossible; accepting the "
            "other invisible characters would allow spoofing.",
        ),
        _spec(
            "X-05",
            "tool-name-with-a-right-to-left-override",
            "ToolCallEvent",
            "rejected",
            ops=(_set(*tool, value="issue" + RLO + "refund"),),
            kind="machine_key",
            path=tool,
            why="a tool name is an ASCII machine key",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "tool-name-with-a-cyrillic-look-alike",
            "ToolCallEvent",
            "rejected",
            ops=(_set(*tool, value="issu" + CYRILLIC_E + "_refund"),),
            kind="machine_key",
            path=tool,
            why="a Cyrillic e is not an ASCII e",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "tool-name-with-a-trailing-newline",
            "ToolCallEvent",
            "rejected",
            ops=(_set(*tool, value="issue_refund\n"),),
            kind="machine_key",
            path=tool,
            why="the key pattern is anchored at the true end of the text, not before a newline",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "tool-name-with-a-no-break-space",
            "ToolCallEvent",
            "rejected",
            ops=(_set(*tool, value="issue_refund" + NBSP),),
            kind="machine_key",
            path=tool,
            why="U+00A0 is not an ASCII space or key character",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "config-ref-with-a-right-to-left-override",
            "Project",
            "rejected",
            ops=(_set(*cfg, value="env:SARVAM" + RLO + "_API_KEY"),),
            kind="config_ref",
            path=cfg,
            why="a config_ref is <scheme>:<name> in ASCII",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "config-ref-scheme-with-a-cyrillic-look-alike",
            "Project",
            "rejected",
            ops=(_set(*cfg, value=CYRILLIC_E + "nv:SARVAM_API_KEY"),),
            kind="config_ref",
            path=cfg,
            why="the scheme is lowercase ASCII",
            threat=_LABEL_THREAT,
        ),
        _spec(
            "X-05",
            "turn-text-with-an-override-and-the-flag-false",
            "Turn",
            "rejected",
            ops=(
                _set("text", value="pay " + RLO + "100"),
                _set("contains_bidi_controls", value=False),
            ),
            kind="bidi_flag",
            path=("contains_bidi_controls",),
            why="the stored flag must equal the computed one: a document cannot hide a bidi "
            "control by claiming there is none",
            threat="A forged flag lets a reader that trusts it skip the display-safe wrapping and "
            "show reordered text such as an amount.",
        ),
        _spec(
            "X-05",
            "turn-text-with-an-override-and-the-flag-true",
            "Turn",
            "flagged",
            ops=(_set("text", value="pay " + RLO + "100"),),
            why="the override is kept byte for byte and the flag says so, so a renderer can "
            "isolate it",
            threat="Altering or dropping the control would change what the caller said.",
        ),
    ]
    return out


def _specs_ids() -> list[Spec]:
    pid = ("id",)

    def bad(name: str, value: str, why: str) -> Spec:
        return _spec(
            "X-06",
            name,
            "Project",
            "rejected",
            ops=(_set(*pid, value=value),),
            kind="is_instance_of",
            path=("id", "is-instance[ProjectId]"),
            why=why,
            threat=_ID_THREAT,
        )

    def ref(name: str, entity: str, ops: tuple[Op, ...], path: tuple[Any, ...], why: str) -> Spec:
        return _spec(
            "X-06",
            name,
            entity,
            "rejected",
            ops=ops,
            kind="is_instance_of",
            path=path,
            why=why,
            threat=_ID_THREAT,
        )

    return [
        bad("another-kinds-prefix", "run_" + _UUID, "a run_ identifier is not a project"),
        bad("uppercase-prefix", "PRJ_" + _UUID, "prefixes are lowercase"),
        bad("uppercase-hex", _PROJECT_ID.upper(), "the UUID part is lowercase hex"),
        bad("bare-uuid", _UUID, "the wire form always carries its prefix"),
        bad(
            "uuid-version-4", "prj_" + _UUID[:14] + "4" + _UUID[15:], "the version nibble must be 7"
        ),
        bad(
            "uuid-variant-bits", "prj_" + _UUID[:19] + "c" + _UUID[20:], "the variant must be 10xx"
        ),
        bad("braces-around-the-uuid", "prj_{" + _UUID + "}", "braces are not part of the form"),
        bad("urn-form", "urn:uuid:" + _UUID, "the urn form is not the wire form"),
        bad("no-hyphens", "prj_" + _UUID.replace("-", ""), "the hyphens are part of the form"),
        bad("en-dash-for-hyphen", "prj_" + _UUID.replace("-", chr(0x2013)), "an en dash is not -"),
        bad("fullwidth-prefix", FULLWIDTH_P + "rj_" + _UUID, "a fullwidth p is not ASCII p"),
        bad("arabic-indic-digit", "prj_" + ARABIC_INDIC_TWO + _UUID[1:], "digits are ASCII only"),
        bad("zero-width-space-in-the-prefix", "p" + ZWSP + "rj_" + _UUID, "no invisible character"),
        bad("leading-space", " " + _PROJECT_ID, "no surrounding white space"),
        bad("trailing-newline", _PROJECT_ID + "\n", "the pattern is anchored at the true end"),
        bad("trailing-no-break-space", _PROJECT_ID + NBSP, "no surrounding white space"),
        ref(
            "transcript-reference-holds-a-run-id",
            "Turn",
            (_set("transcript_id", value="run_" + _UUID),),
            ("transcript_id", "is-instance[TranscriptId]"),
            "a reference field takes only the kind it names",
        ),
        ref(
            "usage-run-reference-holds-a-project-id",
            "UsageRecord",
            (_set("run_id", value=_PROJECT_ID),),
            ("run_id", "is-instance[RunId]"),
            "a reference field takes only the kind it names",
        ),
        ref(
            "timeline-reference-holds-a-turn-id",
            "ToolCallEvent",
            (_set("timeline_origin", "ref", value="turn_" + _UUID),),
            ("timeline_origin", "ref", "artifact_id", "is-instance[ArtifactId]"),
            "a timeline origin of kind artifact names an artifact",
        ),
    ]


def _ts(name: str, value: Any, expect: str, why: str, **rest: Any) -> Spec:
    return _spec(
        "X-07",
        name,
        "Project",
        expect,
        ops=(_set("created_at", value=value),),
        why=why,
        threat=_TS_THREAT,
        **rest,
    )


def _specs_timestamps() -> list[Spec]:
    reject: dict[str, Any] = {"kind": "utc_datetime", "path": ("created_at",)}
    return [
        _ts("leap-second", "2016-12-31T23:59:60Z", "rejected", "second 60 is refused", **reject),
        _ts(
            "leap-second-with-offset",
            "2016-12-31T18:59:60-05:00",
            "rejected",
            "second 60",
            **reject,
        ),
        _ts(
            "seven-fraction-digits",
            "2026-10-23T12:00:00.1234567Z",
            "rejected",
            "more than six digits are refused, never truncated or rounded",
            **reject,
        ),
        _ts(
            "nanoseconds",
            "2026-10-23T12:00:00.123456789Z",
            "rejected",
            "nanoseconds are refused, never truncated",
            **reject,
        ),
        _ts(
            "naive-datetime", "2026-10-23T12:00:00", "rejected", "no offset is no instant", **reject
        ),
        _ts("date-only", "2026-10-23", "rejected", "a date is not an instant", **reject),
        _ts(
            "lowercase-z",
            "2026-10-23T12:00:00z",
            "rejected",
            "RFC 3339 form: Z in capitals",
            **reject,
        ),
        _ts("lowercase-t", "2026-10-23t12:00:00Z", "rejected", "T in capitals", **reject),
        _ts("hour-24", "2026-10-23T24:00:00Z", "rejected", "hour 24 is refused", **reject),
        _ts(
            "offset-24-hours",
            "2026-10-23T12:00:00+24:00",
            "rejected",
            "offset beyond 23:59",
            **reject,
        ),
        _ts("comma-fraction", "2026-10-23T12:00:00,5Z", "rejected", "a dot, not a comma", **reject),
        _ts("trailing-newline", "2026-10-23T12:00:00Z\n", "rejected", "no trailing text", **reject),
        _ts(
            "arabic-indic-year-digit",
            ARABIC_INDIC_TWO + "026-10-23T12:00:00Z",
            "rejected",
            "ASCII digits only",
            **reject,
        ),
        _ts(
            "february-30", "2026-02-30T00:00:00Z", "rejected", "a day that does not exist", **reject
        ),
        _ts(
            "february-29-in-a-common-year",
            "2026-02-29T00:00:00Z",
            "rejected",
            "2026 is not a leap year",
            **reject,
        ),
        _ts(
            "year-9999-pushed-past-the-range-by-the-offset",
            "9999-12-31T23:59:59-01:00",
            "rejected",
            "the UTC instant is beyond year 9999; a validation error, not an OverflowError",
            **reject,
        ),
        _ts("epoch-number", 1792756800, "rejected", "a number is not an RFC 3339 string", **reject),
        _ts(
            "offset-crossing-the-year-boundary",
            "2026-12-31T23:30:00-05:00",
            "preserved",
            "the instant is kept; it is written in UTC with six digits",
            canonical={"created_at": "2027-01-01T04:30:00.000000Z"},
        ),
        _ts(
            "leap-day-in-a-leap-year",
            "2028-02-29T12:00:00Z",
            "preserved",
            "2028 is a leap year; the instant is written with six digits",
            canonical={"created_at": "2028-02-29T12:00:00.000000Z"},
        ),
        _ts(
            "six-fraction-digits-are-kept",
            "2026-10-23T12:00:00.123456Z",
            "preserved",
            "microseconds are the finest resolution and are kept exactly",
        ),
    ]


def _digest(name: str, value: Any, expect: str, kind: str | None, why: str, **rest: Any) -> Spec:
    return _spec(
        "X-08",
        name,
        "AgentTarget",
        expect,
        ops=(_set("content_digest", value=value),),
        kind=kind,
        why=why,
        threat=_DIGEST_THREAT,
        **rest,
    )


def _specs_digests() -> list[Spec]:
    golden = b9.read_json(b9.GOLDEN / "AgentTarget" / "valid" / "maximal.json")["content_digest"]
    value = golden["value"]
    scheme = golden["scheme"]

    def with_(**changes: Any) -> dict[str, Any]:
        return {**golden, **changes}

    flipped = value[:-1] + ("0" if value[-1] != "0" else "1")
    return [
        _digest(
            "uppercase-hex",
            with_(value=value.upper()),
            "rejected",
            "digest_value",
            "the digest text is lowercase hex",
            path=("content_digest", "value"),
        ),
        _digest(
            "truncated",
            with_(value=value[:-2]),
            "rejected",
            "digest_value",
            "the digest is exactly 64 hex digits",
            path=("content_digest", "value"),
        ),
        _digest(
            "trailing-space",
            with_(value=value + " "),
            "rejected",
            "digest_value",
            "the digest is exactly 64 hex digits",
            path=("content_digest", "value"),
        ),
        _digest(
            "one-nibble-flipped",
            with_(value=flipped),
            "rejected",
            "DigestMismatch",
            "the digest is checked against the document on load",
        ),
        _digest(
            "keyed-scheme-without-a-key-reference",
            with_(scheme="hmac-sha256-jcs/v1"),
            "rejected",
            "value_error",
            "a keyed scheme names its key",
            path=("content_digest",),
        ),
        _digest(
            "plain-scheme-with-a-key-reference",
            with_(key_ref={"key_id": "k1", "key_version": None, "provider_hint": None}),
            "rejected",
            "value_error",
            "a plain digest has no key",
            path=("content_digest",),
        ),
        _digest(
            "keyed-digest-as-the-entitys-own-digest",
            with_(
                scheme="hmac-sha256-jcs/v1",
                key_ref={"key_id": "k1", "key_version": None, "provider_hint": None},
            ),
            "rejected",
            "DigestKeyUnavailable",
            "an entity verifies its digest on load and a keyed digest cannot be verified "
            "without a key",
        ),
        _digest(
            "unknown-scheme-version",
            with_(scheme="sha256-jcs/v2"),
            "rejected",
            "UnknownDigestScheme",
            "only registered schemes are accepted",
        ),
        _digest(
            "scheme-in-capitals",
            with_(scheme=scheme.upper()),
            "rejected",
            "UnknownDigestScheme",
            "scheme names are matched exactly",
        ),
        _digest(
            "weak-scheme-md5",
            with_(scheme="md5"),
            "rejected",
            "UnknownDigestScheme",
            "no downgrade to a weaker hash",
        ),
        _digest(
            "extra-member",
            with_(note="x"),
            "rejected",
            "extra_forbidden",
            "a digest object has exactly its three members",
            path=("content_digest", "note"),
        ),
        _digest(
            "null",
            None,
            "rejected",
            "model_type",
            "the digest is required",
            path=("content_digest",),
        ),
        _spec(
            "X-08",
            "metadata-edited-after-sealing",
            "AgentTarget",
            "rejected",
            ops=(_set("metadata", "source", value="edited"),),
            kind="DigestMismatch",
            why="the stored digest no longer matches the document",
            threat=_DIGEST_THREAT,
        ),
        _spec(
            "X-08",
            "version-raised-under-the-old-digest",
            "AgentTarget",
            "rejected",
            ops=(_set("version", value=4),),
            kind="DigestMismatch",
            why="(id, version) is immutable: a new version needs its own digest",
            threat=_DIGEST_THREAT,
        ),
        _spec(
            "X-08",
            "digest-removed",
            "AgentTarget",
            "rejected",
            ops=(("del", ("content_digest",)),),
            kind="missing",
            path=("content_digest",),
            why="a versioned entity cannot drop its digest",
            threat=_DIGEST_THREAT,
        ),
    ]


def _specs_secrets() -> list[Spec]:
    cfg = ("provider_configs", 0, "config_ref")
    uri = ("uri",)
    base_uri = "https://recordings.example.com/calls/2026/10/call-0001.wav"
    out: list[Spec] = []
    uri_threat = (
        "A credential stored in a URL travels with every export and log line that shows the URL."
    )
    ref_threat = (
        "A pasted key hidden in a field that is meant for a reference is stored and exported."
    )
    for name, value, kind, secret in (
        (
            "aws-key-in-a-query-value",
            base_uri + "?x=@@AWS_KEY@@",
            "url_credential_pattern",
            "AWS_KEY",
        ),
        (
            "pasted-key-in-the-path",
            "https://recordings.example.com/@@PASTED_KEY@@.wav",
            "url_credential_pattern",
            "PASTED_KEY",
        ),
        ("jwt-in-a-query-value", base_uri + "?x=@@JWT@@", "url_credential_pattern", "JWT"),
        (
            "github-token-in-the-path",
            "https://recordings.example.com/@@GITHUB_TOKEN@@/a.wav",
            "url_credential_pattern",
            "GITHUB_TOKEN",
        ),
    ):
        out.append(
            _spec(
                "X-09",
                "artifact-uri-" + name,
                "Artifact",
                "rejected",
                ops=(_set(*uri, value=value),),
                kind=kind,
                path=uri,
                no_echo=("@@" + secret + "@@",),
                why="a known credential pattern in an artifact URI is refused and never repeated "
                "in the error",
                threat=uri_threat,
            )
        )
    for name, value, kind, secret in (
        ("pasted-key", "@@PASTED_KEY@@", "config_ref", "PASTED_KEY"),
        ("github-token", "@@GITHUB_TOKEN@@", "config_ref", "GITHUB_TOKEN"),
        ("key-behind-a-scheme", "env:@@AWS_KEY@@", "config_ref_credential", "AWS_KEY"),
        ("jwt-behind-a-scheme", "secret:@@JWT@@", "config_ref_credential", "JWT"),
        ("pem-header", "@@PEM_HEADER@@", "config_ref", "PEM_HEADER"),
    ):
        out.append(
            _spec(
                "X-09",
                "config-ref-" + name,
                "Project",
                "rejected",
                ops=(_set(*cfg, value=value),),
                kind=kind,
                path=cfg,
                no_echo=("@@" + secret + "@@",),
                why="a config_ref is a reference, never a key; the value is refused and never "
                "repeated in the error",
                threat=ref_threat,
            )
        )
    out.append(
        _spec(
            "X-09",
            "turn-text-that-holds-a-key-is-observed-data",
            "Turn",
            "preserved",
            ops=(
                _set("text", value="my key is @@AWS_KEY@@ please save it"),
                _set("contains_bidi_controls", value=False),
                _set("segments", value=[]),
            ),
            why="what a caller said is observed data: it is kept byte for byte, is not metadata "
            "and "
            "is not flagged (redaction of call content is a Phase 3 feature)",
            threat="A caller may read a credential aloud; the record keeps it because altering "
            "observed text would be silent data change, so exports must be sealed or redacted.",
        )
    )
    out.append(
        _spec(
            "X-09",
            "tool-arguments-that-hold-a-key-are-observed-data",
            "ToolCallEvent",
            "preserved",
            ops=(
                _set("arguments_raw", value='{"token": "@@PASTED_KEY@@"}'),
                _set("arguments", value={"token": "@@PASTED_KEY@@"}),
            ),
            why="observed tool arguments are kept byte for byte and are not flagged",
            threat="An agent may pass a credential to a tool; the record keeps what was observed, "
            "so "
            "exports must be sealed or redacted.",
        )
    )
    return out


def _unit(
    name: str, entity: str, quantities: Any, kind: str, path: tuple[Any, ...], why: str
) -> Spec:
    return _spec(
        "X-10",
        name,
        entity,
        "rejected",
        ops=(_set("quantities", value=quantities),),
        kind=kind,
        path=path,
        why=why,
        threat=_UNIT_THREAT,
    )


def _specs_units() -> list[Spec]:
    unit_path = ("quantities", 0, "unit")
    count_path = ("quantities", 0, "count")
    out = []
    for unit in (
        "seconds",
        "milliseconds",
        "minutes",
        "audio_seconds",
        "tokens",
        "characters",
        "INPUT_TOKENS",
        "input_tokens ",
    ):
        slug = re.sub(r"[^a-z]+", "-", unit.lower()).strip("-") or "empty"
        slug = slug + ("-in-capitals" if unit.isupper() else "")
        slug = slug + ("-with-a-trailing-space" if unit.endswith(" ") else "")
        out.append(
            _unit(
                "unit-" + slug,
                "UsageRecord",
                [{"unit": unit, "count": 1}],
                "closed_set",
                unit_path,
                "the eight usage units are a closed set; another spelling or scale is "
                "refused, never converted",
            )
        )
    out += [
        _unit(
            "seconds-as-a-json-integer",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": 10}],
            "usage_count",
            count_path,
            "an audio-seconds count is a decimal string, never a number",
        ),
        _unit(
            "seconds-as-a-json-float",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": 10.5}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "an audio-seconds count is a decimal string, never a number",
        ),
        _unit(
            "seconds-with-a-non-canonical-decimal",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": "10.50"}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "write 10.5, not 10.50",
        ),
        _unit(
            "seconds-in-exponent-form",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": "1e3"}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "no exponent form",
        ),
        _unit(
            "seconds-negative",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": "-1"}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "a quantity is never negative",
        ),
        _unit(
            "seconds-with-arabic-indic-digits",
            "UsageRecord",
            [{"unit": "audio_input_seconds", "count": ARABIC_INDIC_TWO}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "ASCII digits only",
        ),
        _unit(
            "tokens-as-a-string",
            "UsageRecord",
            [{"unit": "input_tokens", "count": "10"}],
            "usage_count",
            count_path,
            "a token count is an integer, never text",
        ),
        _unit(
            "tokens-as-a-float",
            "UsageRecord",
            [{"unit": "input_tokens", "count": 10.0}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "a token count is an integer, never a float (10.0 included)",
        ),
        _unit(
            "tokens-as-a-boolean",
            "UsageRecord",
            [{"unit": "input_tokens", "count": True}],
            "int_type",
            ("quantities", 0, "count", "constrained-int"),
            "true is not the count 1",
        ),
        _unit(
            "tokens-beyond-2-53",
            "UsageRecord",
            [{"unit": "input_tokens", "count": 2**53}],
            "less_than_equal",
            ("quantities", 0, "count", "constrained-int"),
            "a count beyond 2**53 - 1 is not exact in every reader",
        ),
        _unit(
            "one-unit-twice",
            "UsageRecord",
            [{"unit": "input_tokens", "count": 10}, {"unit": "input_tokens", "count": 20}],
            "value_error",
            ("quantities",),
            "the units are disjoint buckets: one unit twice would be summed or overwritten "
            "depending on the reader",
        ),
        _unit(
            "no-quantity-at-all",
            "UsageRecord",
            [],
            "too_short",
            ("quantities",),
            "a usage record without a quantity is no usage",
        ),
    ]
    return out


def _cost(name: str, ops: tuple[Op, ...], kind: str, path: tuple[Any, ...], why: str) -> Spec:
    return _spec(
        "X-12",
        name,
        "CostSummary",
        "rejected",
        ops=ops,
        kind=kind,
        path=path,
        why=why,
        threat="A cost record counted twice (or both counted and excluded) doubles a total that "
        "an operator will be billed or budgeted on.",
    )


def _specs_cost() -> list[Spec]:
    golden = b9.read_json(b9.GOLDEN / "CostSummary" / "valid" / "maximal.json")
    first = golden["included"][0]
    return [
        _cost(
            "included-twice",
            (_set("included", value=[first, first]),),
            "included",
            ("included",),
            "one cost record cannot be summed twice",
        ),
        _cost(
            "included-and-excluded",
            (_set("excluded", 0, "ref", value=first),),
            "excluded",
            ("excluded",),
            "a record is either included or excluded",
        ),
        _cost(
            "excluded-twice",
            (_set("excluded", value=golden["excluded"] + [golden["excluded"][0]]),),
            "excluded",
            ("excluded",),
            "one exclusion per record",
        ),
        _cost(
            "covered-by-the-same-record-twice",
            (_set("excluded", 2, "covered_by", value=[first, first]),),
            "value_error",
            ("excluded", 2, "covered_by"),
            "a list of covering records holds each record once",
        ),
        _cost(
            "covered-by-an-excluded-record",
            (_set("excluded", 2, "covered_by", value=[golden["excluded"][0]["ref"]]),),
            "covered_by",
            ("excluded",),
            "covered_by names only records the summary includes: an excluded record covers nothing",
        ),
        _cost(
            "total-in-another-currency",
            (_set("total", "currency", value="EUR"),),
            "currency_not_supported",
            ("total", "currency"),
            "a total never mixes currencies; only USD is supported in 0.0.1",
        ),
        _cost(
            "negative-total",
            (_set("total", "amount", value="-1.65"),),
            "value_error",
            ("total",),
            "a cost is never negative",
        ),
        _cost(
            "total-without-any-included-record",
            (_set("total", value=None),),
            "total",
            ("total",),
            "the total is present exactly when a record is included",
        ),
        _cost(
            "total-spelled-with-a-trailing-zero",
            (_set("total", "amount", value="1.650"),),
            "decimal_not_canonical",
            ("total", "amount"),
            "one amount, one spelling",
        ),
    ]


def _specs_export() -> list[Spec]:
    threat = (
        "A purpose spelled in a way the closed set does not know could bypass the rules that "
        "belong to a shared or benchmark export."
    )

    def purpose(name: str, value: Any, kind: str) -> Spec:
        return _spec(
            "X-11",
            "purpose-" + name,
            "ExportBundle",
            "rejected",
            base="minimal",
            ops=(_set("purpose", value=value),),
            kind=kind,
            path=("purpose",),
            why="the purpose is one of internal, shared_export, benchmark, spelled exactly",
            threat=threat,
        )

    return [
        purpose("in-capitals", "SHARED_EXPORT", "closed_set"),
        purpose("with-a-hyphen", "shared-export", "closed_set"),
        purpose("with-a-zero-width-suffix", "shared_export" + ZWSP, "closed_set"),
        purpose("as-null", None, "string_type"),
        purpose("as-a-list", ["shared_export"], "string_type"),
    ]


def _specs_surrogates() -> list[Spec]:
    threat = (
        "A lone surrogate cannot be written as UTF-8: a record that holds one fails later, at "
        "export or digest time, instead of at the door."
    )
    return [
        _spec(
            "X-14",
            "lone-high-surrogate-in-segment-text",
            "Turn",
            "rejected",
            text_edits=(('"text": "hello"', '"text": "he\\ud800llo"'),),
            kind="lone_surrogate",
            path=("segments", 0, "text"),
            why="an escaped lone high surrogate is refused where text is stored",
            threat=threat,
            schema_applies=False,
        ),
        _spec(
            "X-14",
            "lone-low-surrogate-in-tool-arguments",
            "ToolCallEvent",
            "rejected",
            text_edits=(('"account": "ACCT-4400-1234",', '"account": "ACCT\\udc00",'),),
            kind="ijson",
            path=("arguments",),
            why="tool arguments are I-JSON; a lone surrogate in a value is refused",
            threat=threat,
            schema_applies=False,
        ),
        _spec(
            "X-14",
            "lone-surrogate-in-a-tool-argument-key",
            "ToolCallEvent",
            "rejected",
            text_edits=(('"account": "ACCT-4400-1234",', '"acc\\ud800ount": "ACCT-4400-1234",'),),
            kind="ijson",
            path=("arguments",),
            why="tool arguments are I-JSON; a lone surrogate in a key is refused",
            threat=threat,
            schema_applies=False,
        ),
        _spec(
            "X-14",
            "escaped-surrogate-pair-is-one-character",
            "Turn",
            "preserved",
            text_edits=(('"text": "hello"', '"text": "he\\ud83d\\ude00llo"'),),
            why="an escaped high and low surrogate together are one valid character and are kept",
            threat="Refusing valid pairs would reject emoji in honest call text.",
            schema_applies=False,
        ),
    ]


SPECS: tuple[Spec, ...] = tuple(
    _specs_json_text()
    + _specs_spoofing()
    + _specs_ids()
    + _specs_timestamps()
    + _specs_digests()
    + _specs_secrets()
    + _specs_units()
    + _specs_export()
    + _specs_cost()
    + _specs_surrogates()
)


# ================================================================================== render


def fixture_text(spec: Spec) -> str:
    """The text of the fixture (placeholders unexpanded)."""
    return _text(spec)


def schema_result(spec: Spec) -> str:
    """Whether the JSON Schema (the exported file) agrees with the expectation."""
    if not spec.schema_applies:
        return "not_applicable"
    document = json.loads(expand(_text(spec)))
    rejects = b9.schema_rejects(spec.entity, document)
    if spec.expect == "rejected":
        return "rejects" if rejects else "code_only"
    return "code_only" if rejects else "accepts"


def expected_document(spec: Spec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "case": spec.case,
        "entity": spec.entity,
        "base": f"{spec.entity}/{spec.base}",
        "expectation": spec.expect,
        "error_path": list(spec.path) if spec.expect == "rejected" else None,
        "error_kind": spec.kind if spec.expect == "rejected" else None,
        "schema": schema_result(spec),
        "canonical": dict(spec.canonical),
        "no_echo": list(spec.no_echo),
        "why": spec.why,
        "threat": spec.threat,
    }
    return result


def render() -> dict[str, bytes]:
    """Every generated file, relative to ``fixtures/adversarial``."""
    files: dict[str, bytes] = {}
    for spec in SPECS:
        stem = f"{spec.case}/{spec.name}"
        assert f"{stem}.json" not in files, stem
        files[f"{stem}.json"] = fixture_text(spec).encode("utf-8")
        files[f"{stem}.expected.json"] = b9.pretty(expected_document(spec))
    return files


def write_fixtures() -> None:  # pragma: no cover - maintenance entry point
    """Write the generated files (run by hand: ``python -m`` is not wired; call from a shell)."""
    for relative, data in render().items():
        target = ADVERSARIAL / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def fixture_documents(case: str) -> list[Path]:
    """The fixture documents (not the expected files) of one case directory, sorted."""
    return sorted(
        p for p in (ADVERSARIAL / case).glob("*.json") if not p.name.endswith(".expected.json")
    )


def case_directories() -> list[Path]:
    return sorted(p for p in ADVERSARIAL.iterdir() if p.is_dir())


# =================================================================================== runner

# Exception types that mean a reader crashed instead of refusing a document.
CRASH_TYPES = frozenset(
    {
        "RecursionError",
        "TypeError",
        "AttributeError",
        "KeyError",
        "IndexError",
        "OverflowError",
        "UnicodeEncodeError",
        "UnicodeDecodeError",
        "MemoryError",
        "AssertionError",
        "PydanticSerializationError",
    }
)


@dataclass
class Outcome:
    accepted: bool
    kind: str | None = None
    path: list[Any] | None = None
    message: str = ""
    texts: list[str] = field(default_factory=list)
    model: Any = None


def run_text(entity: str, text: str) -> Outcome:
    """Load ``text`` with the strict loader as ``entity``; report what happened."""
    loader = api("loader")
    model = b9.entity_models()[entity]
    try:
        loaded = loader.load_model(model, text)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        from ._support_b2 import error_texts

        return Outcome(
            False, str(first["type"]), list(first["loc"]), str(first["msg"]), error_texts(error)
        )
    except Exception as error:  # a typed error or a crash: the caller tells them apart
        return Outcome(False, type(error).__name__, [], str(error), [str(error), repr(error)])
    return Outcome(True, model=loaded)


def run_fixture(path: Path) -> tuple[Outcome, dict[str, Any], str]:
    """(outcome, expected, expanded text) of one fixture document."""
    expected: dict[str, Any] = b9.read_json(path.with_name(path.stem + ".expected.json"))
    text = expand(path.read_text(encoding="utf-8"))
    return run_text(expected["entity"], text), expected, text


# ================================================================================== registry

REGISTRY_KEYS = frozenset(
    {
        "id",
        "title",
        "source",
        "expectation",
        "where",
        "covering_tests",
        "fixture",
        "security_relevant",
        "threat",
    }
)
A_NUMBERS = tuple(f"A-{n:02d}" for n in range(1, 45))
CASE_ID = re.compile(r"^(?:A|X)-[0-9]{2}$")


def read_registry() -> dict[str, Any]:
    registry: dict[str, Any] = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return registry


def node_file(node: str) -> str:
    return node.split("::", 1)[0]


def node_function(node: str) -> str:
    return node.split("::", 1)[1] if "::" in node else ""


def _pytest(
    args: Sequence[str], env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, CHILD_ENV: "1", **(env or {})}
    return subprocess.run(  # noqa: S603 (fixed argument list: the running interpreter)
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def collect_node_ids(files: Sequence[str]) -> set[str]:
    """The node ids pytest collects from ``files`` (repository-relative paths), with the default
    options of the project (so a test deselected by default is not in the result)."""
    result = _pytest(["--collect-only", "-q", *files])
    nodes = {line.strip() for line in result.stdout.splitlines() if "::" in line}
    if result.returncode not in (0, 5):  # 5 = nothing collected
        raise AssertionError("collection failed:\n" + result.stdout[-2000:] + result.stderr[-2000:])
    return nodes


@dataclass
class RunResult:
    passed: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def run_nodes(nodes: Sequence[str]) -> RunResult:
    """Run ``nodes`` (node ids; a function node id covers all its parametrizations) in one child
    pytest process and read its junit file."""
    result = RunResult()
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "junit.xml"
        _pytest(["-q", f"--junitxml={report}", *nodes])
        if not report.exists():
            raise AssertionError("the child pytest run wrote no report")
        for case in ET.parse(report).getroot().iter("testcase"):  # noqa: S314 (our own file)
            file_part = case.get("classname", "").replace(".", "/")
            name = case.get("name", "")
            # classname is the dotted module path (a test class would add a part: none are used)
            node = f"{file_part}.py::{name}"
            if case.find("skipped") is not None:
                skipped = case.find("skipped")
                result.skipped[node] = skipped.get("message", "") if skipped is not None else ""
            elif case.find("failure") is not None or case.find("error") is not None:
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                result.failed[node] = (failure.get("message", "") if failure is not None else "")[
                    :300
                ]
            else:
                result.passed.add(node)
    return result


def node_matches(entry: str, collected: str) -> bool:
    """Whether a collected node id belongs to the registry node id ``entry`` (itself or one of its
    parametrizations)."""
    return collected == entry or collected.startswith(entry + "[")


# =========================================================================== registry checks

# Marks and calls that make a test not run (or not count).
_SKIP_MARKS = ("skip", "skipif", "xfail", "private_fixtures", "local_only")
_SKIP_CALLS = frozenset({"skip", "importorskip", "xfail"})
COVERING_CASES_TEST = (
    f"{TESTS_REL}/test_adversarial_cases.py::test_fixture_outcome_matches_expected"
)


def skip_reasons(path: Path) -> dict[str, str]:
    """Function name -> why it may not run, for every top-level ``test_*`` function of a file that
    is marked skip, skipif, xfail, private_fixtures or local_only (on the function or through a
    module-level ``pytestmark``), or that calls ``pytest.skip``, ``importorskip`` or ``xfail``
    in its body. Functions without a reason are absent."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_marks = [
        ast.unparse(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
    ]
    reasons: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        marks = [ast.unparse(d) for d in node.decorator_list] + module_marks
        for mark in marks:
            if any(re.search(rf"\b{m}\b", mark) for m in _SKIP_MARKS):
                reasons[node.name] = f"marked {mark}"
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in _SKIP_CALLS:
                    reasons[node.name] = f"calls {name}()"
    return reasons


def shape_problems(registry: Mapping[str, Any]) -> list[str]:
    """Problems with the structure of the registry itself."""
    problems: list[str] = []
    if set(registry) != {"cases"} or not isinstance(registry.get("cases"), list):
        return ["the registry is an object with the one member 'cases' (a list)"]
    seen: list[str] = []
    for entry in registry["cases"]:
        if not isinstance(entry, dict) or set(entry) != REGISTRY_KEYS:
            problems.append(f"an entry does not have exactly the members {sorted(REGISTRY_KEYS)}")
            continue
        ident = entry["id"]
        if not isinstance(ident, str) or not CASE_ID.match(ident):
            problems.append(f"{ident!r}: not an A-nn or X-nn id")
            continue
        seen.append(ident)
        for name in ("title", "source", "where"):
            if not isinstance(entry[name], str) or not entry[name].strip():
                problems.append(f"{ident}: {name} is empty")
        if entry["expectation"] not in EXPECTATIONS:
            problems.append(
                f"{ident}: expectation {entry['expectation']!r} is not one of {EXPECTATIONS}"
            )
        tests = entry["covering_tests"]
        if not isinstance(tests, list) or not tests or len(set(tests)) != len(tests):
            problems.append(f"{ident}: covering_tests is empty or lists a test twice")
        elif not all(
            isinstance(t, str) and re.fullmatch(rf"{TESTS_REL}/test_\w+\.py::test_\w+", t)
            for t in tests
        ):
            problems.append(
                f"{ident}: a covering test is not '<path>::<function>' under {TESTS_REL}"
            )
        elif any(node_file(t).endswith("test_adversarial_registry.py") for t in tests):
            problems.append(f"{ident}: the registry test cannot cover a case")
        fixture = entry["fixture"]
        if fixture is not None and not (
            isinstance(fixture, str) and fixture.startswith(f"{TESTS_REL}/fixtures/adversarial/")
        ):
            problems.append(f"{ident}: fixture is null or a directory of the adversarial tree")
        security = entry["security_relevant"]
        threat = entry["threat"]
        if not isinstance(security, bool):
            problems.append(f"{ident}: security_relevant is not a boolean")
        elif security:
            if not (
                isinstance(threat, str) and threat.strip().endswith(".") and threat.count(". ") == 0
            ):
                problems.append(f"{ident}: the threat is not one sentence ending in a full stop")
        elif threat is not None:
            problems.append(f"{ident}: threat is set but security_relevant is false")
    if len(seen) != len(set(seen)):
        problems.append("an id appears twice")
    if seen != sorted(seen):
        problems.append("the entries are not ordered by id")
    return problems


def numbering_problems(registry: Mapping[str, Any], scope_text: str) -> list[str]:
    """Every A-01..A-44 is present (and defined in SCOPE), no other A-number is, and the extra
    cases X-01.. are numbered without a gap."""
    problems: list[str] = []
    ids = [e["id"] for e in registry["cases"]]
    for ident in A_NUMBERS:
        if ident not in ids:
            problems.append(f"{ident}: no registry entry")
        if ident not in scope_text:
            problems.append(f"{ident}: not defined in docs/phase1/SCOPE.md")
    others = [i for i in ids if i.startswith("A-") and i not in A_NUMBERS]
    if others:
        problems.append(f"entries outside A-01..A-44: {others}")
    extras = [i for i in ids if i.startswith("X-")]
    if extras != [f"X-{n:02d}" for n in range(1, len(extras) + 1)]:
        problems.append(f"the X cases are not X-01.. without a gap: {extras}")
    return problems


def node_problems(
    entry: Mapping[str, Any],
    *,
    collected: set[str],
    skips: Mapping[str, Mapping[str, str]],
    results: RunResult,
) -> list[str]:
    """Problems with the covering tests of one entry: missing, not collected, marked or called
    skip, failing, or skipped when run."""
    problems: list[str] = []
    ident = entry["id"]
    for node in entry["covering_tests"]:
        matches = sorted(c for c in collected if node_matches(node, c))
        if not matches:
            problems.append(f"{ident}: {node} is not collected by pytest")
            continue
        reason = skips.get(node_file(node), {}).get(node_function(node))
        if reason:
            problems.append(f"{ident}: {node} {reason}")
        for match in matches:
            if match in results.skipped:
                problems.append(f"{ident}: {match} was skipped")
            elif match in results.failed:
                problems.append(f"{ident}: {match} failed: {results.failed[match]}")
            elif match not in results.passed:
                problems.append(f"{ident}: {match} did not run")
    fixture = entry["fixture"]
    if fixture is not None:
        if COVERING_CASES_TEST not in entry["covering_tests"]:
            problems.append(f"{ident}: a case with a fixture lists {COVERING_CASES_TEST}")
        directory = ROOT / fixture
        documents = (
            sorted(p for p in directory.glob("*.json") if not p.name.endswith(".expected.json"))
            if directory.is_dir()
            else []
        )
        if not documents:
            problems.append(f"{ident}: fixture {fixture} holds no document")
        for path in documents:
            node = f"{COVERING_CASES_TEST}[{directory.name}/{path.stem}]"
            if node not in results.passed:
                problems.append(f"{ident}: fixture {directory.name}/{path.stem} did not pass")
    return problems


def fixture_problems(registry: Mapping[str, Any]) -> list[str]:
    """Every fixture directory belongs to the X case of its name, and that case's expectation is
    the expectation of at least one of its fixtures."""
    problems: list[str] = []
    entries = {e["id"]: e for e in registry["cases"]}
    on_disk = {p.name: p for p in case_directories()}
    for name in on_disk:
        entry = entries.get(name)
        if entry is None:
            problems.append(f"fixtures/adversarial/{name} has no registry entry")
        elif entry["fixture"] != f"{TESTS_REL}/fixtures/adversarial/{name}":
            problems.append(f"{name}: the entry's fixture is not its directory")
        else:
            kinds = {
                b9.read_json(p.with_name(p.stem + ".expected.json"))["expectation"]
                for p in fixture_documents(name)
            }
            if entry["expectation"] not in kinds:
                problems.append(f"{name}: no fixture has the entry's expectation")
    for ident, entry in entries.items():
        if entry["fixture"] is not None and not (ROOT / entry["fixture"]).is_dir():
            problems.append(f"{ident}: fixture {entry['fixture']} does not exist")
        if ident.startswith("X-") and entry["fixture"] is not None and ident not in on_disk:
            problems.append(f"{ident}: its fixture directory is missing")
    return problems
