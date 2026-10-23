<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Sarvam STT REST: observations (F3 ground truth for P1-31)

Captured 2026-10-23 with `tracer_capture.sarvam_capture`. Fixtures:
`packages/evalschema/tests/fixtures/real/sarvam-2026-10-23/` (one folder per case with
`request.json`, `response_body.json` (raw bytes as received), `response_meta.json`; shared clips in
`clips/`; `provenance.json`). The 9 Sarvam TTS clips (`tts_*.wav`) and `short_300ms.wav` (a 0.3 s cut of `tts_hi`) are not distributed (Sarvam Terms
of Service v2.0 section 10.2; `docs/security/P1-02-licenses.md` section 8): they are kept in the
git-ignored `packages/evalschema/tests/fixtures/private/sarvam-clips/`, and `clips-manifest.json`
records their sha256, size, duration and TTS parameters. The Kokoro clips and `silence_2s.wav` (generated silence) remain in `clips/`. Call log: `tools/capture/sarvam_call_log.jsonl` (47 calls of the 60 cap).

Docs read (retrieved 2026-10-23, markdown form):
- https://docs.sarvam.ai/api-reference/speech-to-text/transcribe
- https://docs.sarvam.ai/api-reference/text-to-speech/convert

Request: `POST https://api.sarvam.ai/speech-to-text`, multipart/form-data, auth header
`api-subscription-key`. No API version in the URL or in any response header or body. The model is
chosen by the optional `model` form field (docs default `saaras:v4`; the response does not echo the
model, so the model that produced a transcript is not recoverable from the response alone). All
captures use the default model except one `saaras:v3` + `mode=translate` case.

## Success body (HTTP 200, `content-type: application/json`)

Key order as received: `request_id`, `transcript`, [`timestamps`], `language_code`,
[`language_probability`].

| field | JSON type observed | when present |
|---|---|---|
| `request_id` | string, `YYYYMMDD_<uuid4>` (also in header `x-request-id`, same value) | always (47/47 bodies, success and error) |
| `transcript` | string; `""` for silence | always |
| `language_code` | string, never null in 33 successful captures | always |
| `language_probability` | number (1.0, 0.995, 0.915 seen) | only when the request had `language_code=unknown` or the field omitted, and speech was detected |
| `timestamps` | object | only with `with_timestamps=true` |

`timestamps` = `{"words": [str], "start_time_seconds": [float], "end_time_seconds": [float]}`:
three parallel arrays. Despite the name `words`, each entry is a whole phrase or sentence; every
clip (3 to 4.5 s, one sentence) returned a single entry, start 0.0. No word-level timestamps, no
per-segment confidence, no per-word confidence, no speaker or channel field, no audio duration, no
usage or cost field. So F3 confidence stays null (the only score is the language-level
`language_probability`) and there is no cost signal.

## Language reporting

- Label format is BCP-47 with a hyphen and an upper-case region: `en-IN`, `hi-IN`, `mr-IN`,
  `te-IN`, `ta-IN`, `kn-IN`, `ml-IN`, `pa-IN`, `gu-IN`, `bn-IN`. No underscore, no ISO-639-only tag,
  no language names, no surrounding white space in any capture. Every captured value matched the
  form `<iso639>-IN`. `bn_IN` (the SCOPE test name) was not observed; it is a synthetic robustness
  case only.
- Request side rejects underscore: `hi_IN` returns HTTP 400 (see errors).
- Forced `language_code=X`: the response echoes X as `language_code`, always, even when the audio
  is in another language (Hindi clip + `ta-IN` returned `ta-IN`; English clip + `hi-IN` returned
  `hi-IN`). So with a forced code, `language_code` is the request echo, not a detection. There is
  no `language_probability` in that case.
- `unknown` or omitted: `language_code` is a detection; `language_probability` accompanies it.
  Omitted and `unknown` behaved the same. All nine clips detected correctly (probability 1.0 except
  Hindi 0.995). The 300 ms clip detected `en-IN` at 0.915.
- Silence (2 s of zeros): HTTP 200, `transcript: ""`, `language_code` non-null (`hi-IN` when forced,
  `en-IN` when `unknown`), and NO `language_probability` even with `unknown`. A non-null language on
  an empty transcript is not evidence of detection. The docs say `language_code` is null when no
  language is detected; null was never observed (so "language_code null" in the F3 test remains a
  doc-derived synthetic case, not a captured one).
- Forced wrong language changes the script of the transcript (the engine transliterates):
  English audio with `hi-IN` gave Devanagari "हेलो, आई वुड लाइक ...", Hindi audio with `ta-IN`
  gave Tamil-script transliteration. Forced `bn-IN` on a Hindi clip returned Bengali script
  with HTTP 200.
- `mode=translate` (saaras:v3) on Hindi audio returned an English transcript with
  `language_code: "hi-IN"`: the label is the spoken language, not the transcript language.

## Errors

Shape: `{"error": {"message": str, "code": str, "request_id": str}}`. Note the key order in the
error object is `message`, `code`, `request_id`. Same `x-request-id` header. Observed:

| case | HTTP | `error.code` | message |
|---|---|---|---|
| placeholder key | 403 | `invalid_api_key_error` | Invalid or missing authentication credentials |
| `language_code=xx-YY` | 400 | `invalid_request_error` | `body.language_code : Input should be 'unknown', 'hi-IN', ... or 'doi-IN'` |
| `language_code=hi_IN` | 400 | `invalid_request_error` | same enumeration message |
| no `file` field | 400 | `invalid_request_error` | `body.file : Field required` |
| non-audio bytes | 400 | `invalid_request_error` | Failed to read the file, please check the audio format. |
| empty file | 400 | `invalid_request_error` | same as above |

Differences from the docs: validation failures return 400, not 422 (the docs list 422
`unprocessable_entity_error`). 429, 500, 503 not observed. The enumeration in the error message
lists 23 language codes plus `unknown`, matching the docs.

## Rate limits, headers, timing

No 429 in 47 calls (about 1 call per 1 to 3 s, sequential). No rate-limit headers
(`x-ratelimit-*`, `retry-after`) on any response. Headers present on every response: `date`,
`content-type`, `content-length`, `connection`, `server: uvicorn`, `x-request-id`, plus generic
security headers (`referrer-policy`, `permissions-policy`, `cache-control`, `pragma`, `expires`,
`strict-transport-security`, `content-security-policy`, `x-content-type-options`,
`x-frame-options`). Latency 0.24 to 1.6 s per STT call for 0.3 to 12 s clips.

## Other

- The 12 s 16 kHz mono Kokoro clips (left channel of the Pipecat F6 recordings) were accepted
  (WAV, no codec field needed). A 2 s silence WAV and a 0.3 s WAV were accepted (HTTP 200).
- Sarvam TTS (`bulbul:v3`, `speech_sample_rate=16000`, `output_audio_codec=wav`) response is
  `{"request_id": str, "audios": [base64 WAV]}`; all nine languages worked.
- Transcript text differs slightly between runs of the same clip under different options (for
  example Malayalam forced "നമസ്കാരം." vs unknown "നമസ്കാരം,"; Punjabi/Hindi spelling variants), so
  transcripts are model output, not stable references. All text is `needs_native_review` and must
  never be a WER/CER reference (ADR 0018 Decision 9).
- Not tested: batch API, saaras:v4-only `keyterms`, `input_audio_codec` PCM, clips over 30 s,
  concurrency, a real 429.
