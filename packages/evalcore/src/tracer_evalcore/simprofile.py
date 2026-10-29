# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The simulator configuration: the input file ``tracer.sim_profile_file/1`` and the stored document
``tracer.sim_profile/1`` (ADR 0027 rules 1 and 13; docs/phase4/api-surface-batch-e.md section 5).

The input file holds every value the call needs and Tracer supplies none (ADR 0026 rule 10: no
default in code). The stored document is the resolved profile of one run: the voice of the run's
persona only, the four caps as given on the command line, and the connection labels of the target's
allowlist entry. It holds machine keys, integers and booleans only. ``validate_sim_profile`` checks
the JSON Schema of the ADR and the cross-field rules that the schema cannot state, by hand, and
refuses with a stable code (never a value).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracer_evalcore._schemalite import first_violation
from tracer_evalcore.errors import InputRefusedError
from tracer_evalcore.normalise import SUPPORTED_NORMALISERS
from tracer_evalcore.prompt import template_sha256
from tracer_evalcore.simtypes import CAP_NAMES, DIALECTS
from tracer_evalschema.errors import DepthLimitExceeded, DuplicateKeyError
from tracer_evalschema.loader import loads_strict

__all__ = [
    "SIM_PROFILE_FILE_FORMAT",
    "SIM_PROFILE_FORMAT",
    "SIM_PROFILE_JSON_SCHEMA",
    "SIM_PROFILE_METADATA_KEY",
    "SimProfileError",
    "SimProfileFile",
    "build_sim_profile",
    "parse_sim_profile_file",
    "validate_sim_profile",
]

SIM_PROFILE_FORMAT: Final = "tracer.sim_profile/1"
SIM_PROFILE_FILE_FORMAT: Final = "tracer.sim_profile_file/1"
SIM_PROFILE_METADATA_KEY: Final = "tracer.sim_profile"
_SCHEMA_TEXT: Final = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "tracer.sim_profile/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "format",
    "dialect",
    "connection",
    "audio",
    "turn_taking",
    "barge_ins",
    "caller",
    "voice",
    "stt",
    "limits"
  ],
  "properties": {
    "format": {
      "type": "string",
      "enum": [
        "tracer.sim_profile/1"
      ]
    },
    "dialect": {
      "type": "string",
      "enum": [
        "pipecat-protobuf/1",
        "twilio-media-stream/1",
        "smallwebrtc/1"
      ]
    },
    "connection": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "auth_token_env",
        "origin",
        "bot_holds_telephony_credentials"
      ],
      "properties": {
        "auth_token_env": {
          "type": [
            "string",
            "null"
          ],
          "pattern": "^[A-Z_][A-Z0-9_]{0,63}$"
        },
        "origin": {
          "type": [
            "string",
            "null"
          ],
          "pattern": "^(?:https?|wss?)://[a-z0-9.-]+(?::[0-9]{1,5})?$"
        },
        "bot_holds_telephony_credentials": {
          "type": [
            "boolean",
            "null"
          ]
        }
      }
    },
    "audio": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "sample_rate_hz",
        "channels",
        "frame_ms",
        "wire_codec"
      ],
      "properties": {
        "sample_rate_hz": {
          "type": "integer",
          "minimum": 8000,
          "maximum": 48000
        },
        "channels": {
          "type": "integer",
          "enum": [
            1
          ]
        },
        "frame_ms": {
          "type": "integer",
          "enum": [
            20
          ]
        },
        "wire_codec": {
          "type": "string",
          "enum": [
            "pcm_s16le",
            "mulaw",
            "webrtc_negotiated"
          ]
        }
      }
    },
    "turn_taking": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms"
      ],
      "properties": {
        "caller_speaks_first": {
          "type": "boolean"
        },
        "wait_for_agent_silence_ms": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_silence_ms": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "activity_min_peak": {
          "type": "integer",
          "minimum": 1,
          "maximum": 32767
        }
      }
    },
    "barge_ins": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "turn_index",
          "barge_in_after_ms"
        ],
        "properties": {
          "turn_index": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          },
          "barge_in_after_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9007199254740991
          }
        }
      }
    },
    "caller": {
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "mode"
          ],
          "properties": {
            "mode": {
              "type": "string",
              "enum": [
                "scripted"
              ]
            }
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "mode",
            "provider",
            "model",
            "max_tokens",
            "seed",
            "reasoning_effort",
            "max_line_chars",
            "max_turns",
            "max_regenerations",
            "end_sentinel",
            "prompt"
          ],
          "properties": {
            "mode": {
              "type": "string",
              "enum": [
                "llm"
              ]
            },
            "provider": {
              "type": "string",
              "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
            },
            "model": {
              "type": "string",
              "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
            },
            "max_tokens": {
              "type": "integer",
              "minimum": 1,
              "maximum": 9007199254740991
            },
            "seed": {
              "type": [
                "integer",
                "null"
              ],
              "minimum": 0,
              "maximum": 9007199254740991
            },
            "reasoning_effort": {
              "type": [
                "string",
                "null"
              ],
              "pattern": "^[a-z][a-z0-9_]{0,31}$"
            },
            "max_line_chars": {
              "type": "integer",
              "minimum": 1,
              "maximum": 2500
            },
            "max_turns": {
              "type": "integer",
              "minimum": 1,
              "maximum": 9007199254740991
            },
            "max_regenerations": {
              "type": "integer",
              "enum": [
                1
              ]
            },
            "end_sentinel": {
              "type": "string",
              "enum": [
                "END_CALL"
              ]
            },
            "prompt": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "template_id",
                "template_sha256",
                "agent_text_max_chars"
              ],
              "properties": {
                "template_id": {
                  "type": "string",
                  "pattern": "^tracer\\.caller_prompt/[1-9][0-9]{0,8}$"
                },
                "template_sha256": {
                  "type": "string",
                  "pattern": "^[0-9a-f]{64}$"
                },
                "agent_text_max_chars": {
                  "type": "integer",
                  "minimum": 1,
                  "maximum": 9007199254740991
                }
              }
            }
          }
        },
        {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "mode",
            "max_line_chars",
            "max_turns",
            "end_sentinel"
          ],
          "properties": {
            "mode": {
              "type": "string",
              "enum": [
                "replay"
              ]
            },
            "max_line_chars": {
              "type": "integer",
              "minimum": 1,
              "maximum": 2500
            },
            "max_turns": {
              "type": "integer",
              "minimum": 1,
              "maximum": 9007199254740991
            },
            "end_sentinel": {
              "type": "string",
              "enum": [
                "END_CALL"
              ]
            }
          }
        }
      ]
    },
    "voice": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "provider",
        "model",
        "speaker",
        "language_code",
        "pace",
        "sample_rate_hz",
        "codec"
      ],
      "properties": {
        "provider": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
        },
        "model": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
        },
        "speaker": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
        },
        "language_code": {
          "type": "string",
          "enum": [
            "mr-IN"
          ]
        },
        "pace": {
          "type": "string",
          "pattern": "^(?:0|[1-9][0-9]{0,2})(?:\\.[0-9]{0,5}[1-9])?$"
        },
        "sample_rate_hz": {
          "type": "integer",
          "minimum": 8000,
          "maximum": 48000
        },
        "codec": {
          "type": "string",
          "enum": [
            "linear16",
            "mulaw"
          ]
        }
      }
    },
    "stt": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "provider",
        "model",
        "max_chunk_ms"
      ],
      "properties": {
        "provider": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
        },
        "model": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}$"
        },
        "max_chunk_ms": {
          "type": "integer",
          "minimum": 1,
          "maximum": 9007199254740991
        }
      }
    },
    "limits": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "max_call_seconds",
        "max_tts_chars",
        "max_llm_tokens",
        "max_stt_seconds",
        "min_call_interval_ms"
      ],
      "properties": {
        "max_call_seconds": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 14400
        },
        "max_tts_chars": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_llm_tokens": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "max_stt_seconds": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 9007199254740991
        },
        "min_call_interval_ms": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        }
      }
    }
  }
}"""
SIM_PROFILE_JSON_SCHEMA: Final[Mapping[str, Any]] = json.loads(_SCHEMA_TEXT)
_PROPS: Final = SIM_PROFILE_JSON_SCHEMA["properties"]
_FILE_KEYS: Final = frozenset(
    {
        "format",
        "dialect",
        "audio",
        "turn_taking",
        "barge_ins",
        "caller",
        "voices",
        "stt",
        "agreement",
        "min_call_interval_ms",
    }
)
_WIRE: Final = {
    "pipecat-protobuf/1": "pcm_s16le",
    "twilio-media-stream/1": "mulaw",
    "smallwebrtc/1": "webrtc_negotiated",
}
_MAX: Final = 9007199254740991


class SimProfileError(InputRefusedError):
    """A profile file or document was refused. ``code`` is the stable reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SimProfileFile:
    """The parsed input file; ``voices`` maps a persona key to its voice entry."""

    dialect: str
    audio: Mapping[str, Any]
    turn_taking: Mapping[str, Any]
    barge_ins: tuple[Mapping[str, Any], ...]
    caller: Mapping[str, Any]
    voices: Mapping[str, Mapping[str, Any]]
    stt: Mapping[str, Any]
    flag_min_confidence: int | float
    normaliser: str
    min_call_interval_ms: int


def _bad(code: str) -> SimProfileError:
    return SimProfileError(code)


def parse_sim_profile_file(text: str) -> SimProfileFile:
    """Read the input file: strict JSON, exactly its keys, each section against the ADR schema."""
    try:
        document = loads_strict(text)
    except (ValueError, DepthLimitExceeded, DuplicateKeyError):
        raise _bad("sim_profile_file_not_json") from None
    if not isinstance(document, Mapping):
        raise _bad("sim_profile_file_shape")
    if document.get("format") != SIM_PROFILE_FILE_FORMAT:
        raise _bad("sim_profile_file_format")
    for key in sorted(_FILE_KEYS):
        if key not in document:
            raise _bad(f"sim_profile_file_missing_{key}")
    if set(document) - _FILE_KEYS:
        raise _bad("sim_profile_file_unknown_key")
    for key in ("dialect", "audio", "turn_taking", "barge_ins", "caller", "stt"):
        if first_violation(_PROPS[key], document[key]) is not None:
            raise _bad(f"sim_profile_file_invalid_{key}")
    voices = document["voices"]
    if not isinstance(voices, Mapping) or not voices:
        raise _bad("sim_profile_file_invalid_voices")
    for persona, voice in voices.items():
        if (
            type(persona) is not str
            or not persona
            or first_violation(_PROPS["voice"], voice) is not None
        ):
            raise _bad("sim_profile_file_invalid_voices")
    agreement = document["agreement"]
    if (
        not isinstance(agreement, Mapping)
        or set(agreement) != {"flag_min_confidence", "normaliser"}
        or type(agreement["flag_min_confidence"]) not in (int, float)
        or type(agreement["normaliser"]) is not str
        or agreement["normaliser"] not in SUPPORTED_NORMALISERS
    ):
        raise _bad("sim_profile_file_invalid_agreement")
    interval = document["min_call_interval_ms"]
    if type(interval) is not int or not 0 <= interval <= _MAX:
        raise _bad("sim_profile_file_invalid_min_call_interval_ms")
    return SimProfileFile(
        dialect=document["dialect"],
        audio=dict(document["audio"]),
        turn_taking=dict(document["turn_taking"]),
        barge_ins=tuple(dict(item) for item in document["barge_ins"]),
        caller=dict(document["caller"]),
        voices={str(k): dict(v) for k, v in voices.items()},
        stt=dict(document["stt"]),
        flag_min_confidence=agreement["flag_min_confidence"],
        normaliser=agreement["normaliser"],
        min_call_interval_ms=interval,
    )


def build_sim_profile(
    profile: SimProfileFile,
    *,
    persona_key: str,
    connection: Mapping[str, Any],
    caps: Mapping[str, int | None],
) -> dict[str, Any]:
    """The stored document of one run; the caller validates it with ``validate_sim_profile``."""
    voice = profile.voices.get(persona_key)
    if voice is None:
        raise _bad("voice_missing_for_persona")
    if set(caps) != set(CAP_NAMES):
        raise _bad("caps_invalid")
    return {
        "format": SIM_PROFILE_FORMAT,
        "dialect": profile.dialect,
        "connection": dict(connection),
        "audio": dict(profile.audio),
        "turn_taking": dict(profile.turn_taking),
        "barge_ins": [dict(item) for item in profile.barge_ins],
        "caller": dict(profile.caller),
        "voice": dict(voice),
        "stt": dict(profile.stt),
        "limits": {
            "max_call_seconds": caps["call_seconds"],
            "max_tts_chars": caps["tts_chars"],
            "max_llm_tokens": caps["llm_tokens"],
            "max_stt_seconds": caps["stt_seconds"],
            "min_call_interval_ms": profile.min_call_interval_ms,
        },
    }


def validate_sim_profile(
    document: Mapping[str, Any],
    *,
    scripted_turn_count: int | None = None,
    check_caps: bool = True,
) -> None:
    """The schema of the ADR, then its cross-field rules; ``SimProfileError`` on the first fault.

    ``scripted_turn_count`` is the number of scripted turns of the evaluator: when given and the
    caller is scripted, each barge-in must name one of them. ``check_caps=False`` leaves out the
    rule that a real engine has its cap (the command checks the caps at its own step 4).
    """
    found = first_violation(SIM_PROFILE_JSON_SCHEMA, document)
    if found is not None:
        raise _bad(f"schema:{found}")
    dialect = document["dialect"]
    audio = document["audio"]
    if dialect not in DIALECTS or audio["wire_codec"] != _WIRE[dialect]:
        raise _bad("dialect_audio_mismatch")
    if dialect == "twilio-media-stream/1" and audio["sample_rate_hz"] != 8000:
        raise _bad("dialect_audio_mismatch")
    voice = document["voice"]
    expected_codec = "mulaw" if audio["wire_codec"] == "mulaw" else "linear16"
    if voice["sample_rate_hz"] != audio["sample_rate_hz"] or voice["codec"] != expected_codec:
        raise _bad("voice_audio_mismatch")
    turn_taking = document["turn_taking"]
    if turn_taking["max_silence_ms"] < turn_taking["wait_for_agent_silence_ms"]:
        raise _bad("max_silence_below_wait")
    indexes = [item["turn_index"] for item in document["barge_ins"]]
    if indexes != sorted(set(indexes)):
        raise _bad("barge_ins_not_sorted_unique")
    caller = document["caller"]
    if (
        scripted_turn_count is not None
        and caller["mode"] == "scripted"
        and any(index >= scripted_turn_count for index in indexes)
    ):
        raise _bad("barge_in_turn_unknown")
    if document["turn_taking"]["caller_speaks_first"] and 0 in indexes:
        raise _bad("barge_in_on_first_line")
    limits = document["limits"]
    real_caller = caller["mode"] == "llm" and caller["provider"] != "fake"
    real = {
        "max_call_seconds": voice["provider"] != "fake"
        or real_caller
        or document["stt"]["provider"] != "fake",
        "max_tts_chars": voice["provider"] != "fake",
        "max_llm_tokens": real_caller,
        "max_stt_seconds": document["stt"]["provider"] != "fake",
    }
    for name, needed in real.items():
        if check_caps and needed and limits[name] is None:
            raise _bad(f"cap_missing_{name}")
    if caller["mode"] == "llm":
        prompt = caller["prompt"]
        try:
            expected = template_sha256(prompt["template_id"])
        except ValueError:
            raise _bad("prompt_template_unknown") from None
        if prompt["template_sha256"] != expected:
            raise _bad("prompt_hash_mismatch")
    connection = document["connection"]
    twilio = dialect == "twilio-media-stream/1"
    if twilio != (connection["bot_holds_telephony_credentials"] is not None) or (
        twilio and connection["bot_holds_telephony_credentials"] is not False
    ):
        raise _bad("connection_inconsistent")
