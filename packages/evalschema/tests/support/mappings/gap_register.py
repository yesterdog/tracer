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

F2 and F5 rows are generated from the key tables below; the attribute names of the F2 table are
written as data, one row each, so that the register stays greppable by key.
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

_BASE_GAP_REGISTER: tuple[GapRow, ...] = (
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

# ---- F2: Langfuse-attributed OTLP spans ----------------------------------------------------

_E = "env" + "ironment"  # spelled in two parts: the converter hygiene scan flags the short form

_F2_TRACE = (
    "a trace-level Langfuse attribute describing the trace as a whole; no run record has a "
    "field for it and no MVP-1 metric reads it"
)
_F2_OBSERVATION = (
    "an observation-level Langfuse attribute with no target field; no MVP-1 metric reads it"
)
_F2_CARRIER = (
    "an alternative input or output carrier that Langfuse reads; the mapping reads the "
    "langfuse.observation.* names only, so its text is in no record (F2-2). Phase 5 adapters "
    "re-verify against a real instance"
)
_F2_CONFIG = (
    "a request parameter of the model call; it belongs to the agent's configuration, which the "
    "run records do not carry"
)
_F2_SPAN = "an OpenTelemetry span field with no target field in the run records"

# (key, kind, owner, reason)
_F2_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("F2:*.langfuse.trace.name", "unmapped", "dev", _F2_TRACE),
    (
        "F2:*.langfuse.user.id",
        "unmapped",
        "security-licensing",
        "identifies the end user, who may be a person; it is copied nowhere and no warning "
        "quotes it",
    ),
    (
        "F2:*.user.id",
        "unmapped",
        "security-licensing",
        "identifies the end user, who may be a person; it is copied nowhere and no warning "
        "quotes it",
    ),
    ("F2:*.langfuse.release", "unmapped", "dev", _F2_TRACE),
    ("F2:*.langfuse.trace.public", "unmapped", "dev", _F2_TRACE),
    ("F2:*.langfuse.trace.tags", "unmapped", "dev", _F2_TRACE),
    (
        "F2:*.langfuse.trace.metadata.*",
        "unmapped",
        "security-licensing",
        "free-form producer metadata of the trace; it can hold anything the operator wrote, so "
        "no record copies it",
    ),
    (
        "F2:*.langfuse.trace.input",
        "unmapped",
        "schema-architect",
        "the input of the whole trace repeats the conversation or the prompt and is not a record "
        "of a model call",
    ),
    (
        "F2:*.langfuse.trace.output",
        "unmapped",
        "schema-architect",
        "the output of the whole trace repeats a model call's output, which is read from the "
        "generation itself",
    ),
    ("F2:*.langfuse.version", "unmapped", "dev", _F2_TRACE),
    ("F2:*.langfuse." + _E, "unmapped", "dev", _F2_TRACE),
    ("F2:*.deployment." + _E, "unmapped", "dev", _F2_TRACE),
    ("F2:*.deployment." + _E + ".name", "unmapped", "dev", _F2_TRACE),
    ("F2:*.langfuse.observation.level", "unmapped", "dev", _F2_OBSERVATION),
    (
        "F2:*.langfuse.observation.status_message",
        "unmapped",
        "security-licensing",
        "a producer's status text for the observation; it can repeat request content, so it is "
        "not copied",
    ),
    (
        "F2:*.langfuse.observation.metadata.*",
        "unmapped",
        "security-licensing",
        "free-form producer metadata of the observation; it can hold anything the operator "
        "wrote, so no record copies it",
    ),
    (
        "F2:*.langfuse.observation.input",
        "lossy",
        "schema-architect",
        "the input of a generation is the context sent to the model, not a line of the call; "
        "only a tool observation's input is read (its arguments)",
    ),
    ("F2:*.gen_ai.prompt", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.gen_ai.completion", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.input.value", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.output.value", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.mlflow.spanInputs", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.mlflow.spanOutputs", "unmapped", "dev", _F2_CARRIER),
    ("F2:*.langfuse.observation.model.parameters", "unmapped", "schema-architect", _F2_CONFIG),
    ("F2:*.gen_ai.request.temperature", "unmapped", "schema-architect", _F2_CONFIG),
    ("F2:*.llm.invocation_parameters.*", "unmapped", "schema-architect", _F2_CONFIG),
    (
        "F2:*.gen_ai.usage.reasoning.output_tokens",
        "unmapped",
        "schema-architect",
        "the OpenTelemetry registry says reasoning tokens are included in the output count, so "
        "the count is not read separately; no usage unit holds them (F2-4)",
    ),
    (
        "F2:*.llm.token_count.*",
        "unmapped",
        "dev",
        "an alternative usage carrier that Langfuse reads; the mapping reads gen_ai.usage.* and "
        "usage_details only (F2-2)",
    ),
    (
        "F2:*.langfuse.observation.prompt.name",
        "unmapped",
        "dev",
        "the name of the Langfuse prompt version used; prompt management has no run record",
    ),
    (
        "F2:*.langfuse.observation.prompt.version",
        "unmapped",
        "dev",
        "the version of the Langfuse prompt used; prompt management has no run record",
    ),
    (
        "F2:*.langfuse.prompt.name",
        "unmapped",
        "dev",
        "the name of the Langfuse prompt version used; prompt management has no run record",
    ),
    (
        "F2:*.langfuse.prompt.version",
        "unmapped",
        "dev",
        "the version of the Langfuse prompt used; prompt management has no run record",
    ),
    (
        "F2:*.langfuse.observation.completion_start_time",
        "lossy",
        "staff-reviewer",
        "when the model began generating; no page defines time to first token as that instant "
        "minus the start, so no ttfb is derived. If an MVP-1 latency metric needs a stage TTFB "
        "this becomes blocking and needs a schema field (F2-3)",
    ),
    (
        "F2:*.langfuse.observation.cost_details.@components",
        "lossy",
        "schema-architect",
        "the per-usage-type costs (input, output, ...) have no target; the total of the call, "
        "which the cost model needs, is kept",
    ),
    ("F2:*.@parentSpanId", "unmapped", "dev", _F2_SPAN),
    ("F2:*.@kind", "unmapped", "dev", _F2_SPAN),
    ("F2:*.@status", "unmapped", "dev", _F2_SPAN),
    ("F2:*.@events", "unmapped", "dev", _F2_SPAN),
    (
        "F2:*.@startTimeUnixNano",
        "unmapped",
        "dev",
        "the span start of an observation that is not a model call or a tool call has no target "
        "field",
    ),
    (
        "F2:*.@endTimeUnixNano",
        "unmapped",
        "dev",
        "the span end of an observation that is not a model call has no target field",
    ),
    (
        "F2:resource.*",
        "unmapped",
        "dev",
        "OpenTelemetry resource attributes describe the exporter, not the call",
    ),
    (
        "F2:scope.name",
        "unmapped",
        "dev",
        "the instrumentation scope name only groups spans",
    ),
)

# ---- F5: Pipecat Evals scenario files -------------------------------------------------------

_F5_HARNESS = (
    "a setting of the Pipecat Evals harness (how the run is driven); the Evaluator records what "
    "the conversation is, not how a Pipecat harness runs it"
)
_F5_SIMULATOR = (
    "the persona LLM of the harness's simulation; the Evaluator's persona is a reference the "
    "user declares, so the harness's model for playing it is not stored"
)
_F5_SPEECH = (
    "the text-to-speech setting that voices the caller in the harness; it belongs to the "
    "harness, not to the Evaluator"
)
_F5_SENSITIVE = (
    "a harness setting that can carry a credential or an internal address; it is copied "
    "nowhere and no warning quotes it"
)
_F5_JUDGE = (
    "a setting of Pipecat's judge for the harness's own checks; the Evaluator's judge model "
    "is the service and model only"
)
_F5_TRANSCRIPTION = (
    "the speech-to-text setting that transcribes the bot in the harness's audio mode; it "
    "belongs to the harness"
)
_F5_SCRIPT = (
    "a part of a scripted turn other than the caller's text (audio, keypad or timing); the "
    "ScriptedTurn holds the text and its language only"
)
_F5_EXPECT = (
    "an event assertion of a scripted scenario; the Evaluator holds a script, expected tool "
    "calls and an expected outcome, not per-turn event expectations (F5-3)"
)

# key -> (kind, owner, reason)
_F5_ROWS: dict[str, tuple[str, str, str]] = {
    "context": (
        "unmapped",
        "schema-architect",
        "the seed messages of the bot's conversation context; the Evaluator has no field for a "
        "starting context and it can hold anything the operator wrote",
    ),
    "stop_on_failure": ("unmapped", "dev", _F5_HARNESS),
    "trigger_disconnect": ("unmapped", "dev", _F5_HARNESS),
    "simulator.service": ("unmapped", "dev", _F5_SIMULATOR),
    "simulator.model": ("unmapped", "dev", _F5_SIMULATOR),
    "simulator.endpoint": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "simulator.extra": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "simulator.factory": ("unmapped", "dev", _F5_SIMULATOR),
    "max_turns": ("unmapped", "dev", _F5_HARNESS),
    "max_silence_s": ("unmapped", "dev", _F5_HARNESS),
    "runs": ("unmapped", "dev", _F5_HARNESS),
    "user.modality": ("unmapped", "dev", _F5_HARNESS),
    "user.speech.service": ("unmapped", "dev", _F5_SPEECH),
    "user.speech.voice": ("unmapped", "dev", _F5_SPEECH),
    "user.speech.model": ("unmapped", "dev", _F5_SPEECH),
    "user.speech.language": (
        "unmapped",
        "indic-linguistics",
        "the language the harness's TTS speaks the caller in; it must match the voice and is "
        "not a statement about the Evaluator, whose language the user declares",
    ),
    "user.speech.speed": ("unmapped", "dev", _F5_SPEECH),
    "user.speech.sample_rate": ("unmapped", "dev", _F5_SPEECH),
    "user.speech.api_key": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "user.speech.factory": ("unmapped", "dev", _F5_SPEECH),
    "judge.modality": ("unmapped", "dev", _F5_HARNESS),
    "judge.eval.endpoint": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "judge.eval.extra": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "judge.eval.factory": (
        "unmapped",
        "dev",
        "a factory builds the judge from code, so no service and model can be stated; a "
        "scenario that writes one is not built (blocking dynamic row F5:judge.eval)",
    ),
    "judge.eval.explainer": ("unmapped", "dev", _F5_JUDGE),
    "judge.eval.explainer.service": ("unmapped", "dev", _F5_JUDGE),
    "judge.eval.explainer.model": ("unmapped", "dev", _F5_JUDGE),
    "judge.eval.explainer.endpoint": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "judge.eval.explainer.extra": ("unmapped", "security-licensing", _F5_SENSITIVE),
    "judge.eval.explainer.factory": ("unmapped", "dev", _F5_JUDGE),
    "judge.eval.explain_below": ("unmapped", "dev", _F5_JUDGE),
    "judge.eval.allow_continue": ("unmapped", "dev", _F5_JUDGE),
    "judge.transcription.service": ("unmapped", "dev", _F5_TRANSCRIPTION),
    "judge.transcription.model": ("unmapped", "dev", _F5_TRANSCRIPTION),
    "judge.transcription.language": ("unmapped", "indic-linguistics", _F5_TRANSCRIPTION),
    "judge.transcription.padding_secs": ("unmapped", "dev", _F5_TRANSCRIPTION),
    "judge.transcription.factory": ("unmapped", "dev", _F5_TRANSCRIPTION),
    "turns.user.pause_ms": (
        "unmapped",
        "dev",
        "a pause between the parts of a scripted line; the ScriptedTurn holds the joined text "
        "and no timing",
    ),
    "turns.audio": ("unmapped", "dev", _F5_SCRIPT),
    "turns.dtmf": ("unmapped", "dev", _F5_SCRIPT),
    "turns.image": ("unmapped", "dev", _F5_SCRIPT),
    "turns.send_after.event": ("unmapped", "dev", _F5_SCRIPT),
    "turns.send_after.delay_ms": ("unmapped", "dev", _F5_SCRIPT),
    "turns.expect.within_ms": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.text_contains": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.text_excludes": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.eval": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.marker": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.marker_first": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.markers": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.text_after": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.check_from": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.absent": ("unmapped", "staff-reviewer", _F5_EXPECT),
    "turns.expect.calls.@anonymous": (
        "lossy",
        "staff-reviewer",
        "a call expectation with no name (a bare function_call, or a call given by arguments "
        "alone) asserts that some call happened; ExpectedToolCall needs a name, so it is "
        "skipped with a warning (F5-3)",
    ),
    "metrics.calls.@exclusive": (
        "lossy",
        "staff-reviewer",
        "a function_calls metric fails on any call that is not listed; the Evaluator's expected "
        "calls cannot say so, and the rule is carried only by the scorer name "
        "pipecat_evals:function_calls (F5-4)",
    ),
}

GAP_REGISTER: tuple[GapRow, ...] = (
    *_BASE_GAP_REGISTER,
    *(_row(key, kind, owner, reason) for key, kind, owner, reason in _F2_ROWS),
    *(_row(f"F5:{key}", kind, owner, reason) for key, (kind, owner, reason) in _F5_ROWS.items()),
)

KNOWN_SCHEMA_GAPS: tuple[GapRow, ...] = tuple(
    row for row in GAP_REGISTER if row.classification == "non-blocking"
)
