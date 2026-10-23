# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The gap register of the real-sample mappings (ADR 0018 Decision 4, gate item 13).

Every source key of a captured fixture that no mapping copies, derives from or reads as decision
input has a row here, with a classification, a reason and an owner. A blocking gap is a source field
whose information is needed by an MVP-1 metric, the verdict or the cost model and that cannot be
represented without loss or a guess (ADR 0018 Decision 3); the register has none, and the
converters add a blocking row to their result only for the inputs they refuse to guess (N76,
unknown cache semantics). ``KNOWN_SCHEMA_GAPS`` is the non-blocking part (M16).

Keys are ``<fixture>:<source key>`` and may use ``*`` (``fnmatch``). F2, F5 and F8 add their rows
when their mappings exist.
"""

from __future__ import annotations

from .common import GapRow

__all__ = ["GAP_REGISTER", "KNOWN_SCHEMA_GAPS"]


def _row(key: str, kind: str, owner: str, reason: str) -> GapRow:
    return GapRow(key.split(":", 1)[0], key, "non-blocking", kind, reason, owner)


_TELEMETRY_CONFIG = (
    "a service setting or request parameter of the agent under test; it belongs to the agent's "
    "configuration, which the run records do not carry"
)

GAP_REGISTER: tuple[GapRow, ...] = (
    # ---- F1: Pipecat OTel spans -----------------------------------------------------------
    _row(
        "F1:turn.turn.user_bot_latency_seconds",
        "unmapped",
        "staff-reviewer",
        "Pipecat's pre-computed user-bot latency has no target field; latency metrics compute "
        "from turn timing (ADR 0004 6f). If an MVP-1 metric consumes Pipecat's value this becomes "
        "blocking and needs an Exchange field (M7)",
    ),
    _row(
        "F1:stt.language",
        "unmapped",
        "indic-linguistics",
        "Pipecat documents the attribute as 'Detected or configured language', so it cannot be "
        "told from the configured value and is not stored as an observed tag (M2)",
    ),
    _row(
        "F1:llm.gen_ai.request.top_k",
        "unmapped",
        "dev",
        "the value is Pipecat's NOT_GIVEN sentinel (a setting that was not given); there is "
        "nothing to map, and the sentinel is never stored",
    ),
    _row("F1:llm.gen_ai.request.temperature", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:llm.gen_ai.request.max_tokens", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:llm.param.*", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:stt.settings.*", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:tts.settings.*", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:tts.voice_id", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row("F1:stt.vad_enabled", "unmapped", "schema-architect", _TELEMETRY_CONFIG),
    _row(
        "F1:llm.gen_ai.system_instructions",
        "unmapped",
        "schema-architect",
        "the agent's system prompt is agent configuration, not a record of the run; it can hold "
        "anything the operator wrote, so no record copies it",
    ),
    _row(
        "F1:llm.tools",
        "unmapped",
        "schema-architect",
        "the tool definitions offered to the model are agent configuration; the calls that were "
        "made are read from the llm input and stored as ToolCallEvents",
    ),
    _row(
        "F1:llm.tool_count",
        "unmapped",
        "schema-architect",
        "the number of tool definitions is agent configuration, not a call that was made",
    ),
    _row(
        "F1:llm.input",
        "lossy",
        "schema-architect",
        "the LLM context is read only to recover tool calls and their results; the rest of the "
        "context repeats the conversation turns or the system prompt and is not stored",
    ),
    _row(
        "F1:llm.stream",
        "unmapped",
        "schema-architect",
        "whether the service streamed has no target field and no MVP-1 metric uses it",
    ),
    _row(
        "F1:*.gen_ai.operation.name",
        "unmapped",
        "dev",
        "restates the span name (stt, chat, tts), which already gives the stage",
    ),
    _row(
        "F1:*.gen_ai.output.type",
        "unmapped",
        "dev",
        "text or speech output type of the service; the stage already says it",
    ),
    _row(
        "F1:turn.turn.type",
        "unmapped",
        "dev",
        "Pipecat's turn type label (conversation) has no target field",
    ),
    _row(
        "F1:conversation.conversation.type",
        "unmapped",
        "dev",
        "the conversation type label (voice) has no target field",
    ),
    _row(
        "F1:conversation.capture.scenario",
        "unmapped",
        "qa",
        "a label the capture harness adds to name its scenario; it is not Pipecat data",
    ),
    _row(
        "F1:conversation.@startTimeUnixNano",
        "unmapped",
        "schema-architect",
        "the whole-call span start has no target; run timing comes from the run record",
    ),
    _row(
        "F1:conversation.@endTimeUnixNano",
        "unmapped",
        "schema-architect",
        "the whole-call span end has no target; run timing comes from the run record",
    ),
    _row(
        "F1:turn.@endTimeUnixNano",
        "lossy",
        "schema-architect",
        "the turn span interval is not stored; Exchange.duration_ms comes from "
        "turn.duration_seconds, which Pipecat reports for the turn (M6)",
    ),
    _row(
        "F1:*.@kind",
        "unmapped",
        "dev",
        "the OpenTelemetry span kind is always internal here and has no target field",
    ),
    _row(
        "F1:*.@status",
        "unmapped",
        "dev",
        "the OpenTelemetry span status has no target field; the captures hold no error status",
    ),
    _row(
        "F1:*.@events",
        "unmapped",
        "dev",
        "span events have no target field; the captures hold none",
    ),
    _row(
        "F1:resource.*",
        "unmapped",
        "dev",
        "OpenTelemetry resource attributes (SDK version, service name) describe the exporter, not "
        "the call",
    ),
    _row(
        "F1:scope.name",
        "unmapped",
        "dev",
        "the instrumentation scope name (pipecat, pipecat.turn) only groups spans",
    ),
    # ---- F3: Sarvam STT REST --------------------------------------------------------------
    _row(
        "F3:response.language_code",
        "lossy",
        "indic-linguistics",
        "for a forced request the label is the request echoed back and is not stored (a forced "
        "code has no home in Transcript, M13); for an empty transcript it is no detection "
        "evidence. For a detection request it is stored as detected_language(_raw)",
    ),
    _row(
        "F3:response.error.message",
        "lossy",
        "security-licensing",
        "the provider's error message may repeat the request, so it is not copied; the error "
        "code and the request id are kept",
    ),
    _row(
        "F3:meta.elapsed_s",
        "unmapped",
        "schema-architect",
        "the client-measured duration of the call has no target field; Transcript carries no "
        "latency and no MVP-1 metric needs it",
    ),
    _row(
        "F3:meta.headers.*",
        "unmapped",
        "dev",
        "HTTP response headers (date, security headers, the request id repeated) have no target "
        "field; the request id is read from the body",
    ),
    _row(
        "F3:request.auth",
        "unmapped",
        "security-licensing",
        "a note about the authentication header (never its value); it must not be copied",
    ),
    _row(
        "F3:request.content_type",
        "unmapped",
        "dev",
        "the transport content type of the request has no target field",
    ),
    _row(
        "F3:request.file.*",
        "unmapped",
        "dev",
        "the file name, clip path, hash and size describe the audio the caller sent; the caller "
        "supplies the Artifact ID, so the clip is not described twice",
    ),
    _row(
        "F3:request.form_fields.with_timestamps",
        "unmapped",
        "dev",
        "the request option is redundant with the presence of response.timestamps, which is "
        "what the mapping reads",
    ),
    _row(
        "F3:request.requested_at_utc",
        "unmapped",
        "dev",
        "the harness time of the request has no target; Transcript carries no creation time "
        "from the source",
    ),
    # ---- F4: Pipecat conversation.json ----------------------------------------------------
    _row(
        "F4:assistant.content",
        "absent_in_source",
        "schema-architect",
        "a bot turn in which the bot said nothing is not in the file (Pipecat defines it as no "
        "turn), so an interrupted empty bot turn is lost; the turn events (F7) keep it",
    ),
    _row(
        "F4:tool_call.@position",
        "absent_in_source",
        "schema-architect",
        "where a tool call sits between turns has no target field; the file orders messages but "
        "the ToolCallEvent has no link to a turn",
    ),
    # ---- F6: AudioBufferProcessor WAV -----------------------------------------------------
    _row(
        "F6:wav.fmt/byte_rate",
        "unmapped",
        "dev",
        "derivable from the sample rate, channels and bits per sample, which are mapped",
    ),
    _row(
        "F6:wav.fmt/block_align",
        "unmapped",
        "dev",
        "derivable from the channels and bits per sample, which are mapped",
    ),
    # ---- F7: Pipecat turn events ----------------------------------------------------------
    _row(
        "F7:*.@harness_observed_at_utc",
        "unmapped",
        "qa",
        "the capture harness clock, not Pipecat's: it is never a turn time or an ordering key",
    ),
    _row(
        "F7:*.@seq",
        "unmapped",
        "qa",
        "the capture harness sequence number; the order of the file is used instead",
    ),
    _row(
        "F7:*.@source",
        "unmapped",
        "qa",
        "the name the capture harness gives the observer that emitted the event",
    ),
    _row(
        "F7:on_user_turn_started.payload.strategy",
        "unmapped",
        "schema-architect",
        "the name of the turn-start strategy has no target field",
    ),
    _row(
        "F7:on_user_turn_stopped.payload.strategy",
        "unmapped",
        "schema-architect",
        "the name of the turn-stop strategy has no target field",
    ),
    _row(
        "F7:on_user_turn_stopped.payload.message.user_id",
        "unmapped",
        "schema-architect",
        "the user id of the message has no target field; the captures hold null",
    ),
    _row(
        "F7:on_user_turn_started.@event",
        "unmapped",
        "schema-architect",
        "the event has no payload besides the strategy and no Pipecat time, so it states no "
        "instant that a Turn or Exchange could keep",
    ),
    _row(
        "F7:on_assistant_turn_started.@event",
        "unmapped",
        "schema-architect",
        "the event has no payload and no Pipecat time, so it states no instant that a Turn or "
        "Exchange could keep",
    ),
    _row(
        "F7:bot_started_speaking.@event",
        "unmapped",
        "schema-architect",
        "the event carries no Pipecat time (only the harness clock), so it states no instant "
        "that a latency computation could use",
    ),
    _row(
        "F7:bot_stopped_speaking.@event",
        "unmapped",
        "schema-architect",
        "the event carries no Pipecat time (only the harness clock), so it states no instant "
        "that a latency computation could use",
    ),
)

KNOWN_SCHEMA_GAPS: tuple[GapRow, ...] = tuple(
    row for row in GAP_REGISTER if row.classification == "non-blocking"
)
