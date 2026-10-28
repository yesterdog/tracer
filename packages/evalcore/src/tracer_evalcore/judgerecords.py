# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The records of the judge (ADR 0030 rule 8; ADR 0027 rule 12a): three closed documents, one
``llm_judge`` definition per golden item, and the results of a judged call.

Schema 0.0.1 holds the judge without a change. Each item gives one ``llm_judge`` enum definition
(options ``agrees`` and ``disagrees``, no pass rule); a verdict ``cannot_tell`` is an ``error`` with
the code ``judge_undetermined`` and never a value; the provenance is a ``JudgeInfo`` without a
confidence; ``MetricResult.metadata["tracer.judge"]`` holds the closed document ``tracer.judge/1``
and ``ConfigSnapshot.metadata["tracer.judge_profile"]`` the document ``tracer.judge_profile/1``.
The expected-outcome check stays inert; no ``RunVerdict`` is produced. Pure: no clock, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from tracer_evalcore._schemalite import first_violation
from tracer_evalcore.definitions import _base, _sealed
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.golden import GoldenItem, GoldenSet
from tracer_evalcore.judge import JudgeItemResult
from tracer_evalcore.judgeprompt import template_shas, template_text
from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.bundle import RunBundle, check_run_bundle
from tracer_evalschema.events import EvidenceRef
from tracer_evalschema.ids import MetricDefinitionId, MetricResultId
from tracer_evalschema.metrics import MetricDefinition, MetricDefinitionRef
from tracer_evalschema.results import JudgeInfo, MetricResult, ResultError, applicability
from tracer_evalschema.run import Run
from tracer_evalschema.snapshot import ConfigSnapshot
from tracer_evalschema.transcript import Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "CALIBRATION",
    "CONFIDENCE",
    "JUDGE_ERROR_MESSAGES",
    "JUDGE_EXCHANGE_FORMAT",
    "JUDGE_EXCHANGE_JSON_SCHEMA",
    "JUDGE_FORMAT",
    "JUDGE_JSON_SCHEMA",
    "JUDGE_KEY_PREFIX",
    "JUDGE_METADATA_KEY",
    "JUDGE_PROFILE_FORMAT",
    "JUDGE_PROFILE_JSON_SCHEMA",
    "JUDGE_PROFILE_METADATA_KEY",
    "JudgeDocumentError",
    "JudgeSetup",
    "build_exchange_document",
    "build_judge_document",
    "build_judge_profile_document",
    "judge_applicable_items",
    "judge_binding_params",
    "judge_definitions",
    "judge_metric_key",
    "judge_results",
    "judgement_document",
    "validate_judge_document",
    "validate_judge_exchange_document",
    "validate_judge_profile_document",
    "with_judge_results",
]

JUDGE_KEY_PREFIX: Final = "judge_golden_"
CALIBRATION: Final = "uncalibrated"
CONFIDENCE: Final = "none_claimed"
JUDGE_PROFILE_FORMAT: Final = "tracer.judge_profile/1"
JUDGE_PROFILE_METADATA_KEY: Final = "tracer.judge_profile"
JUDGE_FORMAT: Final = "tracer.judge/1"
JUDGE_METADATA_KEY: Final = "tracer.judge"
JUDGE_EXCHANGE_FORMAT: Final = "tracer.judge_exchange/1"
_PROFILE_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_profile/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "profile",
    "host",
    "path",
    "model",
    "params",
    "caps",
    "key_env",
    "golden_set",
    "template_sha256",
    "calibration"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_profile/1"
      ]
    },
    "profile": {
      "type": "string",
      "enum": [
        "anthropic_compat/1",
        "gemini_compat/1",
        "xai/1",
        "openrouter/1",
        "sarvam_v2/1",
        "openai/1",
        "local_loopback/1",
        "fake/1"
      ]
    },
    "host": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$|^\\[[0-9a-f:]{2,45}\\](:[0-9]{1,5})?$"
    },
    "path": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^/[A-Za-z0-9._~/-]{0,255}$"
    },
    "model": {
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$"
    },
    "params": {
      "type": "object",
      "additionalProperties": false,
      "required": [],
      "properties": {
        "max_tokens": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_completion_tokens": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "temperature": {
          "type": "number",
          "minimum": 0,
          "maximum": 2
        },
        "seed": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "seed_guarantee": {
          "type": "string",
          "enum": [
            "none"
          ]
        },
        "reasoning_effort": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]{0,31}$"
        },
        "response_format": {
          "type": "string",
          "enum": [
            "json_schema"
          ]
        },
        "provider": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "order",
            "allow_fallbacks",
            "require_parameters",
            "data_collection"
          ],
          "properties": {
            "order": {
              "type": "array",
              "minItems": 1,
              "maxItems": 1,
              "items": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._/-]{0,63}$"
              }
            },
            "allow_fallbacks": {
              "type": "boolean",
              "enum": [
                false
              ]
            },
            "require_parameters": {
              "type": "boolean",
              "enum": [
                true
              ]
            },
            "data_collection": {
              "type": "string",
              "enum": [
                "deny"
              ]
            }
          }
        }
      }
    },
    "caps": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "max_judge_requests",
        "max_judge_tokens"
      ],
      "properties": {
        "max_judge_requests": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_judge_tokens": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        }
      }
    },
    "key_env": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[A-Z][A-Z0-9_]{0,63}$"
    },
    "golden_set": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "key",
        "sha256"
      ],
      "properties": {
        "key": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9]*(_[a-z0-9]+)*$",
          "minLength": 1,
          "maxLength": 64
        },
        "sha256": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      }
    },
    "template_sha256": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "all_facts",
        "equivalent_answer"
      ],
      "properties": {
        "all_facts": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        },
        "equivalent_answer": {
          "type": "string",
          "pattern": "^[0-9a-f]{64}$"
        }
      }
    },
    "calibration": {
      "type": "string",
      "enum": [
        "uncalibrated"
      ]
    }
  }
}"""
_JUDGE_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "verdict",
    "reason_code",
    "facts",
    "window_turn_seqs",
    "excluded_turns",
    "attempts",
    "regenerated",
    "exchange_sha256",
    "calibration",
    "confidence"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge/1"
      ]
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
    "reason_code": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "all_facts_stated",
        "fact_contradicted",
        "fact_absent",
        "meaning_differs",
        "meaning_same",
        "answer_off_topic",
        "answer_unintelligible",
        "insufficient_information",
        null
      ]
    },
    "facts": {
      "type": "array",
      "maxItems": 10,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "index",
          "status"
        ],
        "properties": {
          "index": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9
          },
          "status": {
            "type": "string",
            "enum": [
              "stated",
              "contradicted",
              "absent",
              "cannot_tell"
            ]
          }
        }
      }
    },
    "window_turn_seqs": {
      "type": "array",
      "items": {
        "type": "integer",
        "minimum": 0,
        "maximum": 9007199254740991
      }
    },
    "excluded_turns": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "attempts": {
      "type": "integer",
      "minimum": 0,
      "maximum": 2
    },
    "regenerated": {
      "type": "boolean"
    },
    "exchange_sha256": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[0-9a-f]{64}$"
    },
    "calibration": {
      "type": "string",
      "enum": [
        "uncalibrated"
      ]
    },
    "confidence": {
      "type": "string",
      "enum": [
        "none_claimed"
      ]
    }
  }
}"""
_EXCHANGE_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.judge_exchange/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "messages",
    "response_body",
    "request_id",
    "status"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.judge_exchange/1"
      ]
    },
    "messages": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "role",
          "content"
        ],
        "properties": {
          "role": {
            "type": "string",
            "enum": [
              "system",
              "user"
            ]
          },
          "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100000
          }
        }
      }
    },
    "response_body": {
      "type": "string",
      "maxLength": 65536
    },
    "request_id": {
      "type": [
        "string",
        "null"
      ],
      "pattern": "^[A-Za-z0-9_.:-]{1,128}$"
    },
    "status": {
      "type": "integer",
      "minimum": 100,
      "maximum": 599
    }
  }
}"""
JUDGE_PROFILE_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_PROFILE_SCHEMA_TEXT)
JUDGE_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_JUDGE_SCHEMA_TEXT)
JUDGE_EXCHANGE_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_EXCHANGE_SCHEMA_TEXT)
JUDGE_ERROR_MESSAGES: Final[Mapping[str, str]] = {
    "judge_undetermined": "The judge could not decide from the answer it was shown.",
    "judge_output_invalid": (
        "The reply of the judge was not a valid verdict twice, so none was recorded."
    ),
    "judge_request_failed": (
        "The request to the judge ended in an error before a verdict was returned."
    ),
    "judge_auth_failed": "The judge provider refused the key, so no further request was sent.",
    "judge_requests_cap_reached": (
        "The cap on judge requests was reached before this item was asked."
    ),
    "judge_tokens_cap_reached": "The cap on judge tokens was reached before this item was asked.",
    "question_not_sent": "The call ended before this question was sent, so nothing was asked.",
    "answer_window_empty": "The agent said nothing in the answer window, so nothing was asked.",
}


class JudgeDocumentError(InputRefusedError):
    """A judge document was refused. ``code`` is the stable reason (the message) and ``pointer``
    names the place of the first fault, never a value."""

    def __init__(self, code: str, pointer: str = "/") -> None:
        self.code = code
        self.pointer = pointer
        super().__init__(code)


def _check(schema: Mapping[str, Any], document: Any, code: str) -> None:
    found = first_violation(schema, document)
    if found is not None:
        raise JudgeDocumentError(code, found)


def judge_metric_key(item_id: str) -> str:
    return JUDGE_KEY_PREFIX + item_id


# ---------------------------------------------------------------------------- the documents


def validate_judge_profile_document(document: Mapping[str, Any]) -> None:
    _check(JUDGE_PROFILE_JSON_SCHEMA, document, "judge_profile_invalid")
    params = document["params"]
    if sum(name in params for name in ("max_tokens", "max_completion_tokens")) != 1:
        raise JudgeDocumentError("judge_profile_invalid", "/params")
    if (document["host"] is None) != (document["path"] is None):
        raise JudgeDocumentError("judge_profile_invalid", "/host")


def build_judge_profile_document(
    *,
    profile: str,
    host: str | None,
    path: str | None,
    model: str,
    params: Mapping[str, Any],
    max_judge_requests: int | None,
    max_judge_tokens: int | None,
    key_env: str | None,
    golden: GoldenSet,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": JUDGE_PROFILE_FORMAT,
        "profile": profile,
        "host": host,
        "path": path,
        "model": model,
        "params": dict(params),
        "caps": {"max_judge_requests": max_judge_requests, "max_judge_tokens": max_judge_tokens},
        "key_env": key_env,
        "golden_set": {"key": golden.key, "sha256": golden.sha256},
        "template_sha256": template_shas(),
        "calibration": CALIBRATION,
    }
    validate_judge_profile_document(document)
    return document


def validate_judge_document(document: Mapping[str, Any]) -> None:
    _check(JUDGE_JSON_SCHEMA, document, "judge_invalid")
    verdict, reason = document["verdict"], document["reason_code"]
    if (verdict is None) != (reason is None) or (verdict is None and document["facts"]):
        raise JudgeDocumentError("judge_invalid", "/verdict")
    attempts = document["attempts"]
    if document["regenerated"] != (attempts == 2):
        raise JudgeDocumentError("judge_invalid", "/regenerated")
    if attempts == 0 and (verdict is not None or document["exchange_sha256"] is not None):
        raise JudgeDocumentError("judge_invalid", "/attempts")


def build_judge_document(
    *,
    verdict: str | None,
    reason_code: str | None,
    facts: Sequence[tuple[int, str]],
    window_turn_seqs: Sequence[int],
    excluded_turns: int,
    attempts: int,
    regenerated: bool,
    exchange_sha256: str | None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": JUDGE_FORMAT,
        "verdict": verdict,
        "reason_code": reason_code,
        "facts": [{"index": index, "status": status} for index, status in facts],
        "window_turn_seqs": list(window_turn_seqs),
        "excluded_turns": excluded_turns,
        "attempts": attempts,
        "regenerated": regenerated,
        "exchange_sha256": exchange_sha256,
        "calibration": CALIBRATION,
        "confidence": CONFIDENCE,
    }
    validate_judge_document(document)
    return document


def judgement_document(judgement: JudgeItemResult) -> dict[str, Any]:
    """The ``tracer.judge/1`` document of one item's judgement."""
    return build_judge_document(
        verdict=judgement.verdict,
        reason_code=judgement.reason_code,
        facts=judgement.facts,
        window_turn_seqs=judgement.window.turn_seqs,
        excluded_turns=judgement.window.excluded_turns,
        attempts=judgement.attempts,
        regenerated=judgement.regenerated,
        exchange_sha256=judgement.exchange_sha256,
    )


def validate_judge_exchange_document(document: Mapping[str, Any]) -> None:
    _check(JUDGE_EXCHANGE_JSON_SCHEMA, document, "judge_exchange_invalid")


def build_exchange_document(
    *,
    messages: Sequence[Mapping[str, str]],
    response_body: str,
    request_id: str | None,
    status: int,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": JUDGE_EXCHANGE_FORMAT,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "response_body": response_body,
        "request_id": request_id,
        "status": status,
    }
    validate_judge_exchange_document(document)
    return document


# ------------------------------------------------------------------------------ definitions


@dataclass(frozen=True)
class JudgeSetup:
    """The judge of a run as the snapshot needs it: the golden set, the model reference
    ``{provider: <profile name>, model}`` and the validated ``tracer.judge_profile/1`` document."""

    golden: GoldenSet
    model_ref: Mapping[str, str]
    profile_document: Mapping[str, Any]


def judge_binding_params(item: GoldenItem) -> dict[str, Any]:
    """The texts of the item, as the parameters of its binding."""
    params: dict[str, Any] = {"question": item.question}
    if item.match_mode == "all_facts":
        params["expected_facts"] = list(item.expected_facts)
    else:
        params["expected_answer"] = item.expected_answer
    return params


def judge_definitions(setup: JudgeSetup, *, created_at: datetime) -> list[MetricDefinition]:
    """One ``llm_judge`` definition per item, in golden order, with no pass rule."""
    definitions: list[MetricDefinition] = []
    for item in setup.golden.items:
        facts = item.match_mode == "all_facts"
        document = _base(MetricDefinitionId.new(), created_at)
        document.update(
            {
                "key": judge_metric_key(item.id),
                "kind": "llm_judge",
                "value_type": "enum",
                "scale": {"options": ["agrees", "disagrees"]},
                "requires": ["transcript", "turn_timing"],
                "languages": ["mr"],
                "script_sensitive": False,
                "aggregation": {"kind": "per_conversation"},
                "pass_rule": None,
                "parameters": [
                    {"name": "question", "value_type": "string"},
                    {
                        "name": "expected_facts" if facts else "expected_answer",
                        "value_type": "json" if facts else "string",
                    },
                ],
                "prompt_template": template_text(item.match_mode),
                "prompt_variables": [],
                "judge_model_ref": dict(setup.model_ref),
            }
        )
        definitions.append(_sealed(MetricDefinition, document))
    return definitions


# ---------------------------------------------------------------------------------- results


def judge_applicable_items(
    *,
    snapshot: ConfigSnapshot,
    run: Run,
    transcripts: Sequence[Transcript],
    turns: Sequence[Turn],
) -> tuple[str, ...]:
    """The item ids whose judge definition applies to the run (the others are ``not_applicable``
    and need no judgement), in the order of the snapshot."""
    stt = next((t for t in transcripts if t.source == "our_stt"), None)
    stt_turns = [] if stt is None else [t for t in turns if t.transcript_id == stt.id]
    read = [stt] if stt is not None else [t for t in transcripts if t.source != "script"]
    return tuple(
        d.key.removeprefix(JUDGE_KEY_PREFIX)
        for d in snapshot.metric_definitions
        if d.key.startswith(JUDGE_KEY_PREFIX)
        and applicability(d, run, turns=stt_turns, transcripts=read).na_reason is None
    )


def judge_results(
    *,
    snapshot: ConfigSnapshot,
    run: Run,
    transcripts: Sequence[Transcript],
    turns: Sequence[Turn],
    judgements: Mapping[str, JudgeItemResult],
    created_at: datetime,
) -> tuple[MetricResult, ...]:
    """One result for every judge definition of the snapshot, in its order."""
    stt = next((t for t in transcripts if t.source == "our_stt"), None)
    stt_turns = [] if stt is None else [t for t in turns if t.transcript_id == stt.id]
    read = [stt] if stt is not None else [t for t in transcripts if t.source != "script"]
    ids = [t.id for t in read] or None
    by_id = {t.id: t for t in turns}
    results: list[MetricResult] = []
    for definition in snapshot.metric_definitions:
        if not definition.key.startswith(JUDGE_KEY_PREFIX):
            continue
        answer = applicability(definition, run, turns=stt_turns, transcripts=read)
        common: dict[str, Any] = {
            "id": MetricResultId.new(),
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "metric": MetricDefinitionRef(
                id=definition.id,  # type: ignore[arg-type]
                version=definition.version,
                content_digest=definition.content_digest,
            ),
            "transcript_ids": ids,
            "language_source": answer.language_source,
            "declared_language_transcript_ids": list(answer.declared_language_transcript_ids),
        }
        if answer.na_reason is not None:
            results.append(
                MetricResult(
                    **common,
                    status="not_applicable",
                    value=None,
                    na_reason=answer.na_reason,
                    missing_kinds=list(answer.missing_kinds),
                )
            )
            continue
        item_id = definition.key.removeprefix(JUDGE_KEY_PREFIX)
        judgement = judgements.get(item_id)
        if judgement is None:
            raise ValueError("a judge definition needs a judgement and none was given")
        info = None
        if judgement.prompt_sha256 is not None:
            info = JudgeInfo(
                model_ref=definition.judge_model_ref,  # type: ignore[arg-type]
                prompt_sha256=judgement.prompt_sha256,
                params=dict(judgement.params),
                per_turn=[],
                confidence=None,
                reported_model=judgement.reported_model,
            )
        evidence = [
            EvidenceRef(
                kind="turn",
                ref_id=turn_id,
                start_ms=by_id[turn_id].start_ms,
                end_ms=by_id[turn_id].end_ms,
            )
            for turn_id in judgement.window.turn_ids
        ]
        metadata = {JUDGE_METADATA_KEY: judgement_document(judgement)}
        if judgement.state == "verdict":
            results.append(
                MetricResult(
                    **common,
                    status="scored",
                    value=judgement.verdict,
                    passed=None,
                    judge=info,
                    evidence_refs=evidence,
                    metadata=metadata,
                )
            )
            continue
        code = judgement.error_code or "judge_request_failed"
        results.append(
            MetricResult(
                **common,
                status="error",
                value=None,
                error=ResultError(code=code, message=JUDGE_ERROR_MESSAGES[code]),
                judge=info,
                evidence_refs=evidence,
                metadata=metadata,
            )
        )
    return tuple(results)


def with_judge_results(
    bundle: RunBundle, results: Sequence[MetricResult], usage: Sequence[UsageRecord]
) -> RunBundle:
    """The bundle with the judge results and the judge usage appended; it passes
    ``check_run_bundle`` or this raises."""
    updated = bundle.model_copy(
        update={
            "results": [*bundle.results, *results],
            "usage_records": [*bundle.usage_records, *usage],
        }
    )
    check_run_bundle(updated)
    return updated
