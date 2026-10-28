# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The human labels file ``tracer.judge_labels/1``, the blind packet and the agreement counts
(ADR 0030 rule 22).

A label has a verdict (``null`` for an item not labelled: shown separately and never counted as a
disagreement) and the basis the human judged on. The packet shows a human what to judge and never
the judge's verdict. The agreement report is counts with denominators: no rate, no kappa, no
interval, no claim word (ADR 0023 amendment A4). Pure: no clock, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore._schemalite import first_violation
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.golden import GoldenSet
from tracer_evalcore.judgerecords import JUDGE_KEY_PREFIX, JUDGE_METADATA_KEY
from tracer_evalcore.judgewindow import answer_window
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "HUMAN_VERDICTS",
    "JUDGE_CATEGORIES",
    "JUDGE_LABELS_FORMAT",
    "JUDGE_LABELS_JSON_SCHEMA",
    "JUDGE_PACKET_FORMAT",
    "LABEL_BASES",
    "MAX_LABELS_BYTES",
    "AgreementReport",
    "JudgeLabelsError",
    "JudgedItem",
    "agreement_counts",
    "agreement_lines",
    "build_packet",
    "judge_category",
    "judged_items",
    "parse_judge_labels",
    "validate_judge_labels",
]

JUDGE_LABELS_FORMAT: Final = "tracer.judge_labels/1"
LABEL_BASES: Final = ("audio", "stt_text", "both")
JUDGE_PACKET_FORMAT: Final = "tracer.judge_packet/1"
MAX_LABELS_BYTES: Final = 65_536
HUMAN_VERDICTS: Final = ("agrees", "disagrees", "cannot_tell")
JUDGE_CATEGORIES: Final = ("agrees", "disagrees", "cannot_tell", "no verdict", "not applicable")
_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_labels/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "bundle_id",
    "labeller",
    "labels"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_labels/1"
      ]
    },
    "bundle_id": {
      "type": "string",
      "pattern": "^rbnd_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "labeller": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9_-]{0,63}$"
    },
    "labels": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "item_id",
          "verdict",
          "basis"
        ],
        "properties": {
          "item_id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
            "minLength": 1,
            "maxLength": 40
          },
          "verdict": {
            "type": [
              "string",
              "null"
            ],
            "enum": [
              "agrees",
              "disagrees",
              "cannot_tell",
              null
            ]
          },
          "basis": {
            "type": "string",
            "enum": [
              "audio",
              "stt_text",
              "both"
            ]
          }
        }
      }
    }
  }
}"""
JUDGE_LABELS_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)


class JudgeLabelsError(InputRefusedError):
    """A labels document was refused. ``code`` is the stable reason (the message); ``pointer`` names
    the place of the first fault, never a value."""

    def __init__(self, code: str, pointer: str = "/") -> None:
        self.code = code
        self.pointer = pointer
        super().__init__(code)


def validate_judge_labels(document: Mapping[str, Any]) -> None:
    """The schema, then: no item id twice in a file."""
    found = first_violation(JUDGE_LABELS_JSON_SCHEMA, document)
    if found is not None:
        raise JudgeLabelsError("judge_labels_invalid", found)
    ids = [label["item_id"] for label in document["labels"]]
    if len(set(ids)) != len(ids):
        raise JudgeLabelsError("judge_labels_invalid", "/labels")


def parse_judge_labels(text: str) -> dict[str, Any]:
    """The labels document of ``text``: strict JSON (a repeated key is refused), then the schema and
    the cross-field rule of ``validate_judge_labels``. ``JudgeLabelsError`` on the first fault."""
    if len(text.encode("utf-8")) > MAX_LABELS_BYTES:
        raise JudgeLabelsError("judge_labels_too_large")
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError, RecursionError):
        raise JudgeLabelsError("judge_labels_not_json") from None
    if not isinstance(document, dict):
        raise JudgeLabelsError("judge_labels_invalid", "/")
    validate_judge_labels(document)
    return document


# ------------------------------------------------------------------------------- judged items


@dataclass(frozen=True)
class JudgedItem:
    """One golden item as the stored run records it."""

    item_id: str
    question: str
    match_mode: str
    expected_facts: tuple[str, ...]
    expected_answer: str | None
    status: str  # scored, error or not_applicable
    verdict: str | None
    reason_code: str | None
    facts: tuple[tuple[int, str], ...]
    error_code: str | None
    na_reason: str | None
    missing_kinds: tuple[str, ...]
    attempts: int
    window_turn_seqs: tuple[int, ...]


def judge_category(item: JudgedItem) -> str:
    """The row of the contingency table: the verdict, or why there is none."""
    if item.status == "not_applicable":
        return "not applicable"
    if item.verdict in ("agrees", "disagrees", "cannot_tell"):
        return item.verdict
    return "no verdict"


def judged_items(bundle: RunBundle) -> list[JudgedItem]:
    """The judge items of a bundle in the order of its snapshot (empty when it holds none)."""
    snapshot = bundle.snapshot
    by_metric: dict[Any, Any] = {r.metric.id: r for r in bundle.results}
    bound: dict[Any, Any] = {b.metric.id: b for b in snapshot.metric_bindings}
    items: list[JudgedItem] = []
    for definition in snapshot.metric_definitions:
        if not definition.key.startswith(JUDGE_KEY_PREFIX):
            continue
        params = bound[definition.id].params
        result = by_metric.get(definition.id)
        document = {} if result is None or result.metadata is None else result.metadata
        doc = document.get(JUDGE_METADATA_KEY) or {}
        facts = tuple((f["index"], f["status"]) for f in doc.get("facts", ()))
        listed = params.get("expected_facts")
        items.append(
            JudgedItem(
                item_id=definition.key.removeprefix(JUDGE_KEY_PREFIX),
                question=str(params["question"]),
                match_mode="all_facts" if listed is not None else "equivalent_answer",
                expected_facts=tuple(str(x) for x in listed) if isinstance(listed, list) else (),
                expected_answer=(
                    None
                    if params.get("expected_answer") is None
                    else str(params["expected_answer"])
                ),
                status="not_applicable" if result is None else result.status,
                verdict=doc.get("verdict"),
                reason_code=doc.get("reason_code"),
                facts=facts,
                error_code=None if result is None or result.error is None else result.error.code,
                na_reason=None if result is None else result.na_reason,
                missing_kinds=tuple(() if result is None else (result.missing_kinds or ())),
                attempts=int(doc.get("attempts", 0)),
                window_turn_seqs=tuple(doc.get("window_turn_seqs", ())),
            )
        )
    return items


# ----------------------------------------------------------------------------------- packet


def build_packet(bundle: RunBundle, golden: GoldenSet, *, call_end_ms: int) -> dict[str, Any]:
    """The blind packet of a judged run: per item the question, the expectation, the range of the
    agent channel to listen to, our speech-to-text of it and an empty label. Nothing the judge said
    (no verdict, reason, fact status, error code, model or exchange) is in it."""
    items = judged_items(bundle)
    stt = [t for t in bundle.transcripts if t.source == "our_stt"]
    scripted = {item.id: item.scripted_turn_index for item in golden.items}
    packet_items: list[dict[str, Any]] = []
    for item in items:
        audio: dict[str, int] | None = None
        text: str | None = None
        if len(stt) == 1:
            window = answer_window(
                scripted_turn_index=scripted[item.item_id],
                transcripts=bundle.transcripts,
                turns=bundle.turns,
                call_end_ms=call_end_ms,
            )
            if window.open_ms is not None and window.close_ms is not None:
                audio = {"start_ms": window.open_ms, "end_ms": window.close_ms}
                text = window.text
        packet_items.append(
            {
                "item_id": item.item_id,
                "question": item.question,
                "match_mode": item.match_mode,
                "expected_facts": list(item.expected_facts)
                if item.match_mode == "all_facts"
                else None,
                "expected_answer": item.expected_answer,
                "audio_range_ms": audio,
                "stt_text": text,
            }
        )
    artifact = bundle.artifacts[0].sha256 if bundle.artifacts else None
    bundle_id = str(bundle.id)
    return {
        "format": JUDGE_PACKET_FORMAT,
        "bundle_id": bundle_id,
        "language": "mr",
        "golden_set": {"key": golden.key, "sha256": golden.sha256},
        "recording": {"sha256": artifact, "channel": "agent"},
        "items": packet_items,
        "labels_template": {
            "format": JUDGE_LABELS_FORMAT,
            "bundle_id": bundle_id,
            "labeller": "labeller-1",
            "labels": [{"item_id": i.item_id, "verdict": None, "basis": "audio"} for i in items],
        },
    }


# --------------------------------------------------------------------------------- agreement


@dataclass(frozen=True)
class AgreementReport:
    bundle_id: str
    item_count: int
    labellers: tuple[str, ...]
    judge_by_human: Mapping[str, Mapping[tuple[str, str], int]]  # labeller -> (judge, human) -> n
    unlabelled: Mapping[str, int]
    bases: Mapping[str, Mapping[str, int]]
    human_by_human: Mapping[tuple[str, str], int] | None
    labelled_by_one_only: int
    labelled_by_neither: int


def agreement_counts(
    bundle_id: str,
    items: Sequence[JudgedItem],
    labels: Sequence[Mapping[str, Any]],
) -> AgreementReport:
    """Counts only. An item a labeller left ``null`` or did not list is unlabelled, shown apart and
    never counted as a disagreement. One or two labellers."""
    if not 1 <= len(labels) <= 2:
        raise JudgeLabelsError("judge_labels_labeller_count")
    names = tuple(str(doc["labeller"]) for doc in labels)
    if len(set(names)) != len(names):
        raise JudgeLabelsError("judge_labels_labeller_repeated")
    known = {i.item_id for i in items}
    table: dict[str, dict[tuple[str, str], int]] = {}
    unlabelled: dict[str, int] = {}
    bases: dict[str, dict[str, int]] = {}
    verdicts: dict[str, dict[str, str | None]] = {}
    for doc in labels:
        name = str(doc["labeller"])
        if doc["bundle_id"] != bundle_id:
            raise JudgeLabelsError("judge_labels_bundle_differs")
        given = {lab["item_id"]: lab for lab in doc["labels"]}
        if set(given) - known:
            raise JudgeLabelsError("judge_labels_item_unknown")
        cells = {(j, h): 0 for j in JUDGE_CATEGORIES for h in HUMAN_VERDICTS}
        base = dict.fromkeys(LABEL_BASES, 0)
        mine: dict[str, str | None] = {}
        count = 0
        for item in items:
            label = given.get(item.item_id)
            if label is None or label["verdict"] is None:
                mine[item.item_id] = None
                count += 1
                continue
            mine[item.item_id] = label["verdict"]
            cells[(judge_category(item), label["verdict"])] += 1
            base[label["basis"]] += 1
        table[name], unlabelled[name], bases[name], verdicts[name] = cells, count, base, mine
    human: dict[tuple[str, str], int] | None = None
    one_only = neither = 0
    if len(names) == 2:
        first, second = (verdicts[n] for n in names)
        human = {(a, b): 0 for a in HUMAN_VERDICTS for b in HUMAN_VERDICTS}
        for item in items:
            a, b = first[item.item_id], second[item.item_id]
            if a is not None and b is not None:
                human[(a, b)] += 1
            elif a is None and b is None:
                neither += 1
            else:
                one_only += 1
    return AgreementReport(
        bundle_id, len(items), names, table, unlabelled, bases, human, one_only, neither
    )


def agreement_lines(report: AgreementReport) -> list[str]:
    """The text of the report: every count with its denominator, no other number."""
    total = report.item_count
    out = [
        "agreement report: counts of an uncalibrated judge signal against human labels",
        f"bundle: {report.bundle_id}",
        f"items: {total}",
    ]
    for name in report.labellers:
        base = report.bases[name]
        out.append(
            f"labeller: {name} (basis audio {base['audio']}, stt_text {base['stt_text']}, "
            f"both {base['both']})"
        )
        for j in JUDGE_CATEGORIES:
            for h in HUMAN_VERDICTS:
                out.append(
                    f"judge {j}, human {h}: {report.judge_by_human[name][(j, h)]} of {total} items"
                )
        out.append(
            f"unlabelled by {name}: {report.unlabelled[name]} of {total} items "
            "(not counted as agreement or disagreement)"
        )
    if report.human_by_human is not None:
        first, second = report.labellers
        for a in HUMAN_VERDICTS:
            for b in HUMAN_VERDICTS:
                out.append(
                    f"{first} {a}, {second} {b}: {report.human_by_human[(a, b)]} of {total} items"
                )
        out.append(f"labelled by one only: {report.labelled_by_one_only} of {total} items")
        out.append(f"labelled by neither: {report.labelled_by_neither} of {total} items")
    return out
