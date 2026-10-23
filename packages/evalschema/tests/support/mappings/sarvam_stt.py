# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F3: a Sarvam speech-to-text REST call (request document, response body bytes, response
metadata) to one Transcript with at most one Turn (api-surface-mappings.md section 3).

The response carries no confidence, duration, usage, price or model, so none is stated. A forced
``language_code`` is echoed back by the service, so a label is a detection only for a request that
did not force the language and returned speech. Source pointers address
``{"request": ..., "response": <parsed body>, "meta": ...}``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from tracer_evalschema.ids import ArtifactId
from tracer_evalschema.language import normalize_language_label
from tracer_evalschema.transcript import Transcript, Turn

from .common import Citation, Draft, Leg, MappingResult, Recorder, TableRow, floor_ms, raw_text

__all__ = ["PROVENANCE_TABLE", "map_f3"]

_HOST = re.compile(r"^\s*(?:[A-Za-z]+\s+)?https?://(?P<host>[^/\s:?#]+)")
_SARVAM_HOST = "api.sarvam.ai"
_REQUEST = "/request"
_RESPONSE = "/response"


def _is_sarvam(request: Mapping[str, Any]) -> bool:
    endpoint = request.get("endpoint")
    if not isinstance(endpoint, str):
        return False
    found = _HOST.match(endpoint)
    return found is not None and found["host"].lower() == _SARVAM_HOST


def _parse_body(body: Any) -> Any | None:
    """The parsed JSON of the response body, or the marker ``_BAD`` if it is not JSON text."""
    if not isinstance(body, (bytes, bytearray)):
        return _BAD
    try:
        return json.loads(bytes(body).decode("utf-8"))
    except (ValueError, RecursionError):
        return _BAD


_BAD = object()


def map_f3(
    request: Mapping[str, Any],
    body: bytes,
    meta: Mapping[str, Any],
    *,
    created_at: str,
    privacy: Mapping[str, Any],
    audio_artifact_id: Any = None,
) -> MappingResult:
    rec = Recorder("F3", created_at, privacy)
    parsed = _parse_body(body)
    if parsed is _BAD or not isinstance(parsed, dict):
        rec.warn("body_not_json", "the response body is not a JSON object")
        return rec.result()
    status = meta.get("status") if isinstance(meta, Mapping) else None
    if status is not None:
        rec.consume("/meta/status")
    error = parsed.get("error")
    if "error" in parsed or (status is not None and status != 200):
        _error(rec, parsed, error, status)
        return rec.result()
    transcript_text = parsed.get("transcript")
    if not isinstance(transcript_text, str):
        rec.warn("body_not_json", "the response has no text transcript", path=_RESPONSE)
        return rec.result()
    forms = request.get("form_fields") if isinstance(request, Mapping) else None
    form: Mapping[str, Any] = forms if isinstance(forms, dict) else {}

    transcript_id = rec.new_id(Transcript)
    turn = Draft(Turn, {"transcript_id": transcript_id})
    segments_origin = False
    if transcript_text:
        text, derivation = rec.text(transcript_text, _RESPONSE + "/transcript")
        turn.set("seq", 0, Leg("/seq", (_RESPONSE + "/transcript",), "order_index"))
        turn.set("role", "unknown", Leg("/role", (_RESPONSE + "/transcript",), "constant:unknown"))
        turn.set(
            "source", "our_stt", Leg("/source", (_RESPONSE + "/transcript",), "constant:our_stt")
        )
        turn.set(
            "timing_source",
            "none",
            Leg("/timing_source", (_RESPONSE + "/transcript",), "constant:none"),
        )
        turn.set("text", text, Leg("/text", (_RESPONSE + "/transcript",), derivation))
        segments = _segments(rec, parsed, audio_artifact_id is not None)
        if segments is not None:
            value, legs, has_offsets = segments
            turn.set("segments", value, *legs)
            segments_origin = has_offsets
    else:
        rec.warn(
            "empty_transcript",
            "the transcript is empty; no turn is made and the label is no detection evidence",
            path=_RESPONSE + "/transcript",
            raw="",
        )

    head = Draft(Transcript, {"privacy": privacy})
    head.set("source", "our_stt", Leg("/source", (_RESPONSE + "/transcript",), "constant:our_stt"))
    if _is_sarvam(request):
        head.set(
            "provider",
            "sarvam",
            Leg("/provider", (_REQUEST + "/endpoint",), "provider_from_endpoint_host"),
        )
    model = form.get("model")
    if isinstance(model, str):
        head.set("model", model, Leg("/model", (_REQUEST + "/form_fields/model",)))
    request_id = parsed.get("request_id")
    if isinstance(request_id, str) and _is_sarvam(request):
        head.set(
            "provider_call_ref",
            {"system": "sarvam", "kind": "request_id", "value": request_id},
            Leg(
                "/provider_call_ref/system",
                (_REQUEST + "/endpoint",),
                "provider_from_endpoint_host",
            ),
            Leg("/provider_call_ref/kind", (_RESPONSE + "/request_id",), "constant:request_id"),
            Leg("/provider_call_ref/value", (_RESPONSE + "/request_id",)),
        )
    elif "request_id" in parsed and not isinstance(request_id, str):
        rec.warn(
            "malformed_entry",
            "request_id is not text and is not stored",
            path=_RESPONSE + "/request_id",
            raw=raw_text(request_id),
        )
    if segments_origin and audio_artifact_id is not None:
        head.set(
            "timeline_origin",
            {"kind": "artifact", "ref": _artifact_id(audio_artifact_id)},
            Leg("/timeline_origin/kind", (_RESPONSE + "/timestamps",), "constant:artifact"),
        )
    _language(rec, head, parsed, form, bool(transcript_text))

    for key in ("language_code", "mode"):
        if key in form:
            rec.consume(f"{_REQUEST}/form_fields/{key}")
    turn_record = rec.commit(turn, where=_RESPONSE) if transcript_text else None
    if turn_record is None and head.has("timeline_origin"):
        # no turn carries an offset, so the origin would state one that nothing uses
        del head.values["timeline_origin"]
    rec.commit(head, entity_id=transcript_id)
    return rec.result()


def _artifact_id(value: Any) -> Any:
    if isinstance(value, ArtifactId):
        return value
    return ArtifactId.parse(str(value))


def _error(rec: Recorder, parsed: Mapping[str, Any], error: Any, status: Any) -> None:
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str):
        rec.warn(
            "provider_error_response",
            "the provider answered with an error; there is no transcript to map",
            path=_RESPONSE + "/error/code",
            raw=code,
        )
    else:
        rec.warn(
            "provider_error_response",
            "the provider answered with an error status and no error code",
            path="/meta/status" if status is not None else None,
            raw=raw_text(status) if status is not None else None,
        )
    request_id = error.get("request_id") if isinstance(error, dict) else None
    if isinstance(request_id, str):
        rec.external_ref("sarvam", "request_id", request_id, _RESPONSE + "/error/request_id")


def _language(
    rec: Recorder, head: Draft, parsed: Mapping[str, Any], form: Mapping[str, Any], speech: bool
) -> None:
    """The language label, recorded as a detection only when it is one (M13)."""
    if "language_code" not in parsed or parsed["language_code"] is None:
        return
    label = parsed["language_code"]
    path = _RESPONSE + "/language_code"
    if not isinstance(label, str):
        rec.warn(
            "malformed_entry",
            "the language label is not text and is not stored",
            path=path,
            raw=raw_text(label),
        )
        return
    requested = form.get("language_code", "unknown")
    if requested != "unknown":
        rec.warn(
            "language_label_is_request_echo",
            "the language was forced, so the label repeats the request and is no detection",
            path=path,
            raw=raw_text(label),
        )
        return
    if not speech:
        return
    stored, derivation = rec.text(label, path)
    head.set("detected_language_raw", stored, Leg("/detected_language_raw", (path,), derivation))
    trimmed = stored.strip()
    tag, notes = normalize_language_label(trimmed)
    if tag is not None:
        head.set(
            "detected_language", tag, Leg("/detected_language", (path,), "language_label_to_tag")
        )
    if notes:
        code = notes[0].code
    elif trimmed != stored:
        code = "language_label_normalized"
    else:
        code = None
    if code is not None:
        rec.warn(
            code,
            "the provider label was read as a language tag"
            if code == "language_label_normalized"
            else "the provider label is not a language tag; it is kept as received",
            path=path,
            raw=stored,
        )
    probability = parsed.get("language_probability")
    if "language_probability" in parsed:
        if type(probability) is float:
            head.set(
                "language_confidence",
                probability,
                Leg("/language_confidence", (_RESPONSE + "/language_probability",)),
            )
        else:
            rec.warn(
                "malformed_entry",
                "language_probability is not a number and is not stored",
                path=_RESPONSE + "/language_probability",
                raw=raw_text(probability),
            )
    mode = form.get("mode")
    if isinstance(mode, str) and mode != "transcribe":
        rec.warn(
            "label_not_transcript_language",
            "the request is not a transcription, so the text is not in the labelled language",
            path=_REQUEST + "/form_fields/mode",
            raw=mode,
        )


def _segments(
    rec: Recorder, parsed: Mapping[str, Any], have_artifact: bool
) -> tuple[list[dict[str, Any]], list[Leg], bool] | None:
    stamps = parsed.get("timestamps")
    if not isinstance(stamps, dict):
        return None
    base = _RESPONSE + "/timestamps"
    words = stamps.get("words")
    starts = stamps.get("start_time_seconds")
    ends = stamps.get("end_time_seconds")
    if not (
        isinstance(words, list) and isinstance(starts, list) and isinstance(ends, list)
    ) or not (len(words) == len(starts) == len(ends)):
        rec.warn(
            "timestamps_arrays_mismatch",
            "the timestamp arrays are missing or differ in length; they are not aligned by guess",
            path=base,
        )
        return None
    if not words:
        return None
    if not have_artifact:
        rec.warn(
            "timeline_origin_unknown",
            "offsets count from the start of the clip; without its artifact they are not stored",
            path=base,
        )
    segments: list[dict[str, Any]] = []
    legs: list[Leg] = []
    has_offsets = False
    for i, word in enumerate(words):
        if not isinstance(word, str):
            rec.warn("malformed_entry", "a phrase is not text", path=f"{base}/words/{i}")
            return None
        text, derivation = rec.text(word, f"{base}/words/{i}")
        segment: dict[str, Any] = {"granularity": "phrase", "text": text}
        legs.append(Leg(f"/segments/{i}/granularity", (f"{base}/words/{i}",), "constant:phrase"))
        legs.append(Leg(f"/segments/{i}/text", (f"{base}/words/{i}",), derivation))
        if have_artifact:
            begin = _offset(rec, starts[i], f"{base}/start_time_seconds/{i}")
            end = _offset(rec, ends[i], f"{base}/end_time_seconds/{i}")
            if begin is not None and end is not None and end >= begin:
                segment["start_ms"], segment["end_ms"] = begin, end
                has_offsets = True
                for field, pointer in (
                    ("start_ms", "start_time_seconds"),
                    ("end_ms", "end_time_seconds"),
                ):
                    legs.append(
                        Leg(
                            f"/segments/{i}/{field}",
                            (f"{base}/{pointer}/{i}",),
                            "seconds_to_ms_floor",
                        )
                    )
            elif begin is not None and end is not None:
                rec.warn(
                    "malformed_entry",
                    "a phrase ends before it starts; its offsets are not stored",
                    path=f"{base}/end_time_seconds/{i}",
                )
        segments.append(segment)
    return segments, legs, has_offsets


def _offset(rec: Recorder, seconds: Any, path: str) -> int | None:
    measured = floor_ms(seconds)
    if measured is None:
        rec.warn(
            "malformed_entry",
            "a time offset is not a non-negative number of seconds",
            path=path,
            raw=raw_text(seconds),
        )
        return None
    if measured[1]:
        rec.warn(
            "time_precision_reduced",
            "digits finer than a millisecond were cut",
            path=path,
            raw=raw_text(seconds),
        )
    return measured[0]


def _row(
    target: str,
    kind: str,
    source: str | None,
    derivation: str | None,
    note: str,
    citation: Citation | None = None,
) -> TableRow:
    return TableRow(target, kind, source, derivation, citation, note)


PROVENANCE_TABLE: tuple[TableRow, ...] = (
    _row(
        "Transcript.source", "constant", "response.transcript", "constant:our_stt", "our STT call"
    ),
    _row(
        "Transcript.provider",
        "derivation",
        "request.endpoint",
        "provider_from_endpoint_host",
        "the host api.sarvam.ai names the provider",
    ),
    _row(
        "Transcript.model",
        "source",
        "request.form_fields.model",
        None,
        "only when the request names it",
    ),
    _row(
        "Transcript.detected_language",
        "derivation",
        "response.language_code",
        "language_label_to_tag",
        "trim, then normalize_language_label (ADR 0005); detection requests only",
    ),
    _row(
        "Transcript.detected_language_raw",
        "source",
        "response.language_code",
        None,
        "the label as received, detection requests only",
    ),
    _row(
        "Transcript.detected_language_raw",
        "derivation",
        "response.language_code",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row(
        "Transcript.language_confidence",
        "source",
        "response.language_probability",
        None,
        "the one reported score, detection requests only (M15)",
    ),
    _row(
        "Transcript.declared_language",
        "absent",
        None,
        None,
        "no declaration was given to the importer; never filled from the source",
    ),
    _row(
        "Transcript.timeline_origin.kind",
        "constant",
        "response.timestamps",
        "constant:artifact",
        "offsets count from sample 0 of the clip the caller names",
    ),
    _row(
        "Transcript.provider_call_ref.system",
        "derivation",
        "request.endpoint",
        "provider_from_endpoint_host",
        "the host names the provider",
    ),
    _row(
        "Transcript.provider_call_ref.kind",
        "constant",
        "response.request_id",
        "constant:request_id",
        "Sarvam's request identifier",
    ),
    _row("Transcript.provider_call_ref.value", "source", "response.request_id", None, "verbatim"),
    _row("Turn.seq", "derivation", "response.transcript", "order_index", "one turn per clip"),
    _row("Turn.role", "constant", "response.transcript", "constant:unknown", "one undiarized clip"),
    _row("Turn.source", "constant", "response.transcript", "constant:our_stt", "our STT output"),
    _row(
        "Turn.timing_source",
        "constant",
        "response.transcript",
        "constant:none",
        "no turn-level timing",
    ),
    _row("Turn.text", "source", "response.transcript", None, "verbatim"),
    _row(
        "Turn.text",
        "derivation",
        "response.transcript",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row(
        "Turn.segments.*.granularity",
        "constant",
        "response.timestamps.words",
        "constant:phrase",
        "every capture returned whole phrases under the name words",
    ),
    _row("Turn.segments.*.text", "source", "response.timestamps.words", None, "verbatim"),
    _row(
        "Turn.segments.*.text",
        "derivation",
        "response.timestamps.words",
        "lone_surrogate_replaced",
        "a lone surrogate is replaced by U+FFFD (N82)",
    ),
    _row(
        "Turn.segments.*.start_ms",
        "derivation",
        "response.timestamps.start_time_seconds",
        "seconds_to_ms_floor",
        "seconds truncated to whole milliseconds; needs the clip's artifact",
    ),
    _row(
        "Turn.segments.*.end_ms",
        "derivation",
        "response.timestamps.end_time_seconds",
        "seconds_to_ms_floor",
        "seconds truncated to whole milliseconds; needs the clip's artifact",
    ),
    _row("Turn.segments.*.confidence", "absent", None, None, "the response has no confidence"),
    _row("Turn.start_ms", "absent", None, None, "the response states no turn-level timing"),
)
