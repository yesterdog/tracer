# Phase 4 batch B contract: providers for the simulated caller (P4-08, P4-09, P4-10, P4-11, P4-12)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (rows A.2, sections B, C, D, E, findings F4 and F8, the amendments D-a to D-d and E-a/E-b), ADR 0024 (rules 1 to 17 and the "Phase 4 amendment": rules A1 to A9, invariants IA1 to IA4), ADR 0026 (rules 4, 11, 12, 13, 14, 15, 21), ADR 0028 (rules 3, 5, 6, 10), ADR 0014 (usage and cost), the decision log ("PHASE 4 P4-00f DELIVERED" for the Sarvam TTS facts; "PHASE 3 BATCH B", "batch G" and "batch I" for the adapter hardening mirrored here), the Phase 3 adapter code and tests as models (`secrets`, `calllog`, `cost`, `sarvam/*`), `docs/phase4/api-surface-batch-0b.md` (the engine boundary the adapters implement) and the lead's instructions of 2026-10-27. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test except the one named spec change of section 12. Every statement below is pinned by a test named in section 11, unless it says "not pinned". Every item that was not verified against Sarvam is marked UNVERIFIED and listed in section 14.

## 0. Where the tests are, and how to move them

IMPORTANT (Phase 2 lesson, twice): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` for the whole workspace. Nothing was created under `packages/`. The tests are staged at

```text
docs/phase4/evidence/red/batch-b/staging/tests/evalproviders/   -> packages/evalproviders/tests/   (13 files)
docs/phase4/evidence/red/batch-b/staging/tests/evalcore/        -> packages/evalcore/tests/        (2 files)
```

Two of the 15 files are not tests: `evalproviders_phase4_support.py` and `evalproviders_phase4_matrix.py` (helpers; the second holds the retry and error scenarios that run against both adapters). One REPLACES an existing file: `evalcore/evalcore_phase4_adr_support.py` (the named spec change of section 12: 36 names leave the pending sets). No `__init__.py`, no `conftest.py`, no data file. Test module names are unique across all test directories. Nothing of the batch B modules is imported at module level (the accessors import lazily), so before the code exists each test fails on its own with `ModuleNotFoundError` and the directories still collect. All files are ASCII, pass `ruff check`, `ruff format --check` and `mypy --strict` at their final path (formatted there in the oracle), open no socket and read neither a key nor a private fixture. The Marathi texts are invented, spelled with escapes.

The dev's moves, in this order:

```text
1. git mv docs/phase4/evidence/red/batch-b/staging/tests/evalproviders/*.py  packages/evalproviders/tests/
   git mv docs/phase4/evidence/red/batch-b/staging/tests/evalcore/*.py       packages/evalcore/tests/   (replaces one file)
2. the source (sections 1 to 10); no pyproject.toml change is needed (no new dependency, no new package)
3. uv run --locked --all-groups pytest packages/evalproviders/tests packages/evalcore/tests
```

`docs/phase4/evidence/red/batch-b/oracle-src.patch` is a working implementation of everything below, a starting point and not a substitute for the dev's own design: it passes every test on Python 3.11 to 3.14.

## 1. Modules and names (what exists after batch B)

```text
tracer-evalcore (pure; stdlib + evalschema; no network module)
  tracer_evalcore.audio.activity     NEW  activity_chunk_plan, select_stt_channel, SttChannel,
                                          ChannelMapRefusedError, ACTIVITY_FRAME_MS, ACTIVITY_PLAN_FORMAT
  tracer_evalcore.audio.chunker      CHANGED  ChunkPlan gains two optional trailing fields; as_metadata() may say /2
  tracer_evalcore.stt                CHANGED  SttContext gains the optional trailing field `role`

tracer-evalproviders (httpx; the only importer of it)
  tracer_evalproviders.sarvam.tts    NEW  SarvamTts, create_sarvam_tts
  tracer_evalproviders.sarvam.chat   NEW  SarvamLlm, create_sarvam_llm, SEED_STATEMENT
  tracer_evalproviders.sarvam.models CHANGED  TTS_PATH, TTS_URL, CHAT_PATH, CHAT_URL, TTS_LANGUAGE, TTS_MAX_TEXT_CHARS,
                                          TTS_MAX_AUDIO_SECONDS, LLM_MAX_REPLY_BYTES, tts_max_audio_bytes, tts_max_response_bytes
  tracer_evalproviders.sarvam.client CHANGED  build_http_client(transport) (the one httpx.Client of the package)
  tracer_evalproviders.sarvam.engine CHANGED  SarvamStt refuses an 8 kHz simulated plan (section 8.4)
  tracer_evalproviders.sarvam.mapping CHANGED  Turn.role comes from the context
  tracer_evalproviders.secrets       CHANGED  ENV_VAR_NAMES, load_role_key
  tracer_evalproviders.simcalllog    NEW  SimCallLog, SIM_CALL_LOG_NAME (and the STT view)
  tracer_evalproviders.ttscache      NEW  TtsCache, tts_cache_key, TTS_CACHE_DIR_NAME
  tracer_evalproviders.cost          CHANGED  compute_usage_cost
```

How the new modules meet the Phase 3 tests that scan the source (all pass unchanged; the dev must keep them green, they bind the design):

* `test_unit_tests_open_no_socket_source_has_one_client_with_an_injected_transport` and `test_audit_f3_source_passes_trust_env_false_to_the_http_client`: exactly ONE `httpx.Client(...)` call in the whole package, in `sarvam/client.py`, with `transport`, `follow_redirects=False` and `trust_env=False`. So the TTS and chat adapters cannot build a client of their own: `build_http_client(transport)` is that one call, `SarvamClient` uses it and so do the new adapters (through whatever shared helper the dev writes). `test_sarvam_tts_and_chat_build_no_http_client_of_their_own` pins that `tts.py` and `chat.py` name no `httpx.Client`, `AsyncClient`, `HTTPTransport` or shortcut; `test_sarvam_build_http_client_is_the_hardened_client` pins the builder (signature `(transport)`, no redirect, no environment, TLS verified, 10 s and 60 s).
* `test_sarvam_base_url_not_overridable_source_reads_no_environment_for_it`: URL literals only in `sarvam/models.py` and only `https://api.sarvam.ai` and `https://api.sarvam.ai/speech-to-text`; `os.environ` read only in `secrets.py`. So `TTS_URL` and `CHAT_URL` are built as `API_BASE_URL + PATH` (pinned by `test_sarvam_tts_base_url_not_overridable_the_urls_are_built_not_written`) and no new module reads the environment.
* the INR scan (`test_cost_inr_price_never_converted`-style: no string with the word `INR`, `0.49` or `rupee` in the source) applies to the new modules.
* `test_evalproviders_runtime_imports_whitelist`: stdlib, `httpx`, `tracer_evalschema`, `tracer_evalcore` and the package itself; `tracer_evalcore.audio.activity` imports nothing else either (the evalcore import tests apply).

## 2. P4-08: the Sarvam TTS adapter (`tracer_evalproviders.sarvam.tts`)

### 2.1 Construction

```text
class SarvamTts:                                     # satisfies TtsEngine and UsageStreamingTtsEngine
    provider = "sarvam"
    streams_usage: ClassVar[bool] = True
    def __init__(self, api_key: ApiKey, *, call_log: SimCallLog, cap_characters: int | None,
                 cache: TtsCache | None = None, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, monotonic=time.monotonic) -> None
    def synthesise(self, request: TtsRequest, context: TtsContext, *, on_usage: UsageSink | None = None) -> TtsOutcome
    def close(self) -> None            # closes the HTTP client; idempotent; NOT the call log (the run owns it)
    __enter__ / __exit__
def create_sarvam_tts(*, call_log, cap_characters, environ=None, env_file=None, cache=None,
                      transport=None, sleep=time.sleep, monotonic=time.monotonic) -> SarvamTts
```

* No parameter of the engine, of `synthesise` or of the factory is a model, speaker, language, rate, codec, pace, voice, URL, host, path, proxy, verification or redirect setting (ADR 0026 rules 3 and 12, A2: "model and speaker have no default in code"). `cap_characters` is required (no default; `None` means no cap, for the fake engines; an integer from 0 to 2**53 - 1 otherwise; anything else is a `ValueError` or `TypeError`).
* `create_sarvam_tts` reads the key with `load_role_key("tts", ...)` before anything else (section 10); a missing key raises `TtsKeyMissingError` before any request and writes nothing to the log.
* The engine takes `TtsRequest` and `TtsContext` (batch 0b section 5.3); anything else is a `TypeError` before any request. The engine reads no clock for `created_at`.

### 2.2 The request

`POST https://api.sarvam.ai/text-to-speech`; headers: `api-subscription-key` (the key, once; UNVERIFIED that TTS wants this header, assumed as for STT), `accept-encoding: identity` (once, every attempt), `content-type: application/json`; no `Authorization`; no query. The body is a JSON object with EXACTLY these seven members:

```text
text                 the request's text, verbatim (no strip, no normalisation)
language_code        "mr-IN"
speaker              request.voice.speaker
model                request.voice.model
pace                 a JSON NUMBER with the value of the decimal string request.voice.pace ("1", "0.75", "1.25" ...)
speech_sample_rate   an integer, request.sample_rate_hz
output_audio_codec   request.codec ("linear16" or "mulaw")
```

`pace` is held as a decimal string everywhere in Tracer (batch 0b section 12, ambiguity 8) and converted at the wire; the test compares `Decimal` values and pins that the token is a number. Retries repeat the same request (same bytes). Timeouts, attempts, waits and the `Retry-After` bound are the values of the STT client (section 2.6).

Refused BEFORE the send, raised as the `Tts...ConfigurationError` they are (they are `ValueError`s, with no `__cause__` and no `__context__`, and facts `index` and `reason` only; nothing is sent, nothing is logged, nothing is handed to `on_usage`, nothing is cached):

| Condition | Error | `reason` |
|---|---|---|
| `voice.language_code != "mr-IN"` (hi-IN, en-IN, ta-IN, te-IN, kn-IN, ml-IN, pa-IN, gu-IN, and `mr-US`) | `TtsLanguageNotSupportedError` | `language_not_supported` |
| more than 2,500 code points in `text` (code points, not bytes, not UTF-16 units; 2,500 is sent) | `TtsTextTooLongError` | `text_too_long` |

`TTS_MAX_TEXT_CHARS = 2500` and `TTS_LANGUAGE = "mr-IN"` are constants of `sarvam/models.py`. Which of the two checks wins when both fail is not pinned. There is no pre-send check of the rate or the pace (the provider answers; `TtsRequest` already bounds the rate to 8,000 to 48,000 Hz and the pace to its decimal shape).

### 2.3 The outcome and the order inside `synthesise`

```text
1. type check; language; length                      (raise, nothing happens)
2. cache lookup (if a cache is given)                (hit: section 7; no request, no log line, no usage, no charge)
3. cap check against cap_characters                   (over: outcome failure TtsCapReachedError; section 9)
4. send with retries; `before` and `after` log lines  (section 6)
5. validate the reply (section 2.4); on failure: outcome with the failure, no usage
6. cache put (if a cache is given); build the usage record (section 4); hand it to on_usage; return the outcome
```

`TtsOutcome(audio=<bytes>, usage=(record,), failure=None)` on success: `audio` is the decoded samples (for `linear16` the 16-bit little-endian PCM; no container); `usage` holds exactly the record that went to `on_usage`, with the same id. On failure: `TtsOutcome(audio=None, usage=(), failure=<Tts error>)`. A `BaseException` of the transport (for example `KeyboardInterrupt`) is raised unchanged, after the earlier usage was handed out and before any later request (section 4.2).

### 2.4 The reply (strict validation; the container is UNVERIFIED)

A 200 reply is judged in this order; each failure is `TtsResponseInvalid` with `index`, `attempts` and `status=200` (except where noted) and the short `reason` key, is NOT retried, makes no usage record, and quotes no body:

| Order | Check | `reason` |
|---|---|---|
| 1 | `content-type` is `application/json` (parameters and case ignored); else | `content_type` |
| 2 | the body is at most `tts_max_response_bytes(rate, codec)` bytes (reading stops at the cap; a gzip bomb is capped after decoding); else | `body_too_large` |
| 3 | the body decodes (a corrupt compressed body: no `status` fact) | `body_undecodable` |
| 4 | strict JSON as ADR 0024 rule 8 (no duplicate member, no `NaN`, depth limit, valid UTF-8, no lone surrogate) | `not_json` |
| 5 | the document is an object | `not_object` |
| 6 | `request_id` is a string of 1 to 256 characters | `request_id_invalid` |
| 7 | `audios` is an array of exactly one string | `audios_invalid` |
| 8 | the string is not empty | `audio_empty` |
| 9 | the string is strict base64: exactly the grammar `(4 characters of the standard alphabet)* ( 2 characters `==` \| 3 characters `=` )?`, no whitespace, no data URI, no excess padding, THE SAME ON EVERY PYTHON (3.11's `b64decode(validate=True)` accepts excess padding such as `AAEC=`; the oracle checks the grammar first) | `audio_not_base64` |
| 10 | the decoded audio is not empty (unreachable after rows 8 and 9; kept for the order) | `audio_empty` |
| 11 | the decoded audio is at most `tts_max_audio_bytes(rate, codec)` bytes | `audio_too_large` |
| 12 | the container (below) | `audio_not_recognised`, `audio_format_mismatch`, `audio_empty` |

Unknown members are ignored and kept out of every record (rule 8). A `request_id` that is not a plain identifier is accepted (it is kept in the usage record) and never written to the call log.

Caps (technical bounds, not measured values; ADR 0024 A2 leaves the value to P4-08, this contract fixes it; to be stated in the CHANGELOG):

```text
TTS_MAX_AUDIO_SECONDS = 600
tts_max_audio_bytes(rate, "linear16") = rate * 2 * 600        tts_max_audio_bytes(rate, "mulaw") = rate * 600
tts_max_response_bytes(rate, codec)   = 4 * ceil(tts_max_audio_bytes(rate, codec) / 3) + 4096
any other codec: ValueError whose message names "codec"
```

(8,000 Hz mu-law: 4,800,000 decoded bytes; 16,000 Hz linear16: 19,200,000; 48,000 Hz linear16: 57,600,000.) The reply of a 2,500-character text at the slowest pace is far inside 600 s; the numbers bound memory against a hostile server.

The container, UNVERIFIED (ADR 0024 A2: "which container the documented reply uses for each codec is not verified; the reviewer read 'base64 WAV'"). Everything that depends on it is in `test_sarvam_tts_container.py` and in the one helper `wav_bytes`/`tts_reply` of the support file, so that the first owner-gated reply changes those two places and nothing else. What the tests ASSUME, fail closed: for `linear16`, the decoded bytes are a canonical RIFF/WAVE file (44-byte header: PCM format 1, one channel, the requested rate, 16 bits) and `TtsOutcome.audio` is its data chunk; another rate, channel count, bit width or format tag is `audio_format_mismatch`; raw samples without a header, a wrong tag, a truncated header or an odd data length is `audio_not_recognised`; an empty data chunk is `audio_empty`. For `mulaw` NOTHING about the reply container is pinned (only the request: `output_audio_codec` `mulaw` at 8,000 Hz).

Revision after the first real call (2026-10-28; BL-378): `output_audio_codec: "linear16"` was observed to return HEADERLESS 16-bit PCM, not a WAV. The engine now sends `output_audio_codec: "wav"` (the documented default) for the domain codec `linear16` and reads the reply as a RIFF/WAVE container with a chunk walker (chunks in any order, unknown chunks skipped, `fmt ` of 16 or 18 bytes, PCM, 16 bit, one channel, the requested rate, a data size consistent with the bytes present, or a streamed size of 0 or 0xFFFFFFFF accepted only when the rest of the buffer is an even-length data chunk, no trailing bytes beyond one pad byte). Headerless bytes are refused as `audio_not_recognised`. The cache key and the usage records keep the domain codec; the audio handed to callers and cached is still the bare PCM. `mulaw` is sent as it is and stays UNVERIFIED. Tests: `test_sarvam_tts_wav_walker.py`.

### 2.5 Error facts and taxonomy

Exactly the Phase 3 table of ADR 0024 rule 10 with the `Tts*` classes of batch 0b: `TtsAuthError` (401, 403, or the code `invalid_api_key_error` on any 4xx), `TtsRequestRejected` (any other 4xx but 429), `TtsRateLimited` (429 after the last attempt, or `Retry-After` above 30 s: `reason` `retry_after_above_bound`), `TtsServerError` (5xx after the last attempt), `TtsTransportError` (connect, read, write, pool errors and timeouts after the last attempt; `kind` one of `connect_timeout`, `read_timeout`, `write_timeout`, `pool_timeout`, `connect_error`, `read_error`, `write_error`, `transport_error`), `TtsResponseInvalid` (section 2.4, a status outside 200 and 400 to 599 with `reason` `unexpected_status` and that `status`, never followed if it is a redirect). The facts are `index` (the request's index), `status`, `kind`, `code` (the `error.code` of the body only when it is a short machine key `[a-z][a-z0-9_]{0,63}`, else `unrecognised`), `request_id` (the body's `error.request_id`, else the `x-request-id` header, only when a plain identifier `[A-Za-z0-9_.:-]{1,128}`), `attempts`, `reason`. The provider's message text is in no form of the error and no log. A failure is an outcome, built outside any handler: `__cause__` and `__context__` are `None`, and its chain is itself only. The error body shape `{"error": {"message", "code", "request_id"}}` is UNVERIFIED for TTS (assumed as for STT).

### 2.6 Retries (the STT rules, ADR 0024 rule 9)

At most 4 attempts; retried: 429, any 5xx, and transport errors; waits 1, 2 and 4 s after the first, second and third failed attempt (none after the last); a `Retry-After` on a 429 or 503, as whole seconds or as an HTTP date measured against the response's own `Date`, makes the wait the larger of the backoff and the header; a value above 30 s ends the attempts (`TtsRateLimited`, `retry_after_above_bound`) and the attempt count is the attempt that got it; an unparseable value (`soon`, `1.5`, `-5`, empty, `+5`, `5s`, `0x10`, `1e1`, `NaN`, non-ASCII digits) is ignored; `Retry-After` on other statuses is ignored; no other 4xx is retried; timeouts 10 s to connect and to get a pooled connection, 60 s to read and write; the constants are the ones of `sarvam/client.py` (`MAX_ATTEMPTS`, `BACKOFF_SECONDS`, `RETRY_AFTER_MAX_SECONDS`, `*_TIMEOUT_SECONDS`) and `sleep`, `monotonic` and `transport` are injectable with the STT defaults. 111 scenarios (`evalproviders_phase4_matrix.SCENARIOS`) run against this adapter under `test_sarvam_tts_retry_taxonomy_matches_stt_rules` and against the chat adapter under `test_sarvam_llm_retry_taxonomy_matches_stt_rules`.

## 3. P4-09: the Sarvam chat v2 adapter (`tracer_evalproviders.sarvam.chat`)

### 3.1 Construction

```text
class SarvamLlm:                                     # satisfies LlmEngine and UsageStreamingLlmEngine
    provider = "sarvam"
    streams_usage: ClassVar[bool] = True
    def __init__(self, api_key: ApiKey, *, call_log: SimCallLog, cap_tokens: int | None,
                 transport=None, sleep=time.sleep, monotonic=time.monotonic) -> None
    def complete(self, request: LlmRequest, context: LlmContext, *, on_usage: UsageSink | None = None) -> LlmOutcome
    def close(self) -> None; __enter__ / __exit__
def create_sarvam_llm(*, call_log, cap_tokens, environ=None, env_file=None, transport=None, sleep=..., monotonic=...) -> SarvamLlm
SEED_STATEMENT = "replies may differ between requests, even with the same seed"
```

No parameter names a model, `max_tokens`, seed, effort, messages, temperature, tools, stream, URL, host or proxy. The engine refuses a request or a context that is not the typed record with a `TypeError` before any request.

### 3.2 The request

`POST https://api.sarvam.ai/v2/chat/completions`; the headers and the retry rules of section 2 (UNVERIFIED: the key header for chat v2). The JSON body has exactly `model` (the request's model, verbatim), `messages` (a list of `{"role", "content"}` in order, nothing else in an item) and `max_tokens` (an integer, exactly the request's, up to 2**53 - 1), plus `seed` (an integer) only when the request's seed is not `None` (a seed of 0 is sent) and `reasoning_effort` only when the request's effort is not `None`. NO `tools`, `tool_choice`, `stream`, `temperature`, `top_p`, `n`, `stop` or any other member (ADR 0028 rule 7: no tool; ADR 0024 A3: no streaming). Whether the provider defaults streaming to off is UNVERIFIED (see section 14, item 6).

### 3.3 The reply

A 200 reply is judged in the order of section 2.4 (rows 1 to 5), then:

| Check | `reason` |
|---|---|
| `id` is a string of 1 to 256 characters | `id_invalid` |
| `choices` is an array of exactly one item | `choices_invalid` |
| the item is an object with an object `message` | `message_invalid` |
| `message.content` is a string (not null, not a list of parts) | `content_invalid` |
| the content is at most `LLM_MAX_REPLY_BYTES` = 65,536 bytes in UTF-8 (a technical bound; exactly the bound is accepted) | `reply_too_large` |
| `usage` is an object whose `prompt_tokens` and `completion_tokens` are JSON integers from 0 to 2**53 - 1 (not a float such as `5.0` or `1E2`, not a string, not a boolean, not null, not negative); and, when `usage.prompt_tokens_details.cached_tokens` is present (UNVERIFIED place), an integer in the same range not above `prompt_tokens` | `usage_invalid` |

The JSON body is capped at `MAX_RESPONSE_BYTES` = 1,048,576 bytes (the STT cap; exactly the cap is read, one more is `body_too_large`). Unknown members (`object`, `model`, `created`, `system_fingerprint`, `finish_reason`, `reasoning_content`, `tool_calls`, `total_tokens` ...) are ignored. `LlmOutcome.text` is the content verbatim (no strip, no normalisation, no truncation; an empty string is a text). The validator of ADR 0028 rule 6 is batch C's, not this adapter's.

## 4. P4-12 (part): usage records

### 4.1 The records (ADR 0024 A7)

One `UsageRecord` per ANSWERED request. No record for a failed attempt, for a failed request, for a refusal (language, length, cap) or for a cache hit. Common: `domain` `evaluation`; `provider` `sarvam`; `run_id` and `created_at` from the context; `provider_call_ref` = `ExternalRef{system: "sarvam", kind: "request_id", value: <the reply's request id (TTS) or id (chat)>}`; `occurred_at` = the instant of the reply's HTTP `Date` header, and absent when the header is missing or is not an HTTP date (never the local clock); `raw` null; no text and no key anywhere in the record.

| | TTS | chat |
|---|---|---|
| `stage` | `sim_caller_tts` | `sim_caller_llm` |
| `model` | `request.voice.model`, as requested | `request.model`, as requested (the reply may echo another name) |
| `source` | `measured` | `provider_reported` |
| quantities (integers) | `input_characters` = the code points of the text sent; `requests` = 1 | `input_tokens` = reported prompt tokens minus reported cached tokens; `cache_read_input_tokens` = the cached count, ONLY when the reply reports one; `output_tokens` = reported completion tokens; `requests` = 1 |
| `metadata` | `tracer.request_index` (the request's index), `tracer.model_basis` = `requested_not_echoed` | `tracer.request_index`; and, only when sent, `tracer.seed` (the integer, 0 included), `tracer.seed_guarantee` = `none`, and `tracer.reasoning_effort` |

No record or constant of the adapter claims repeatability: the words `determin`, `reproduc`, `repeatab` and `identical` appear in no record (the seed is recorded, never claimed, ADR 0028 rule 5). A zero count is a quantity with count 0. The STT usage of Phase 3 is unchanged (`stage` `stt`).

### 4.2 Streaming usage (the R1 lesson, ADR 0024 rule 16 (d))

Both engines are `UsageStreamingEngine`s: the record of an answered request is handed to `on_usage` BEFORE the method returns, so before the next request is built; `outcome.usage` holds the same records (same ids); with `on_usage=None` the outcome is the same. Through `synthesise_all` and `complete_once` of the boundary (which do not deliver again): request k is sent only after the records of requests before it are with the sink; the run stops after the first failed outcome; a `BaseException` of the transport propagates unchanged with the earlier usage delivered, and no answered request is sent again; a sink that raises stops the run (that exception is raised, not hidden, and no later request is sent). One request is in flight at a time (`ScriptedTransport.max_active == 1`).

## 5. P4-12 (part): cost

`tracer_evalproviders.cost.compute_usage_cost(usages, *, catalog, created_at, run_started_at=None)` is the function of the Phase 3 `compute_stt_cost` under a neutral name for usage of any stage (`compute_stt_cost` stays, with the same signature; the dev may make one an alias of the other; `SttCost` may stay the return type). Behaviour pinned, for STT, TTS and chat usage alike:

* Without a user USD catalog every record is `not_available` with `na_reason` `no_price_for_model`, `usage_record_ids` the one usage id, no amount, basis, snapshot or rounding; the summary has no total, `completeness` `none`, nothing included, every record excluded with that reason; `check_cost_summary` accepts it. No usage at all is one `not_available` record with `no_usage`. Never an amount of 0.
* With a user catalog (prices the test makes up: 100 characters at 0.03 USD, 1000 tokens at 0.01 USD ...) cost is computed by the evalschema functions: 100 characters give `0.03`; 800 uncached input tokens, 500 output tokens and 200 cached tokens give `0.008 + 0.02 + 0.001 = 0.029`; the summary total is the sum and complete.
* A unit with no price (for example `requests`, or `cache_read_input_tokens`) gives `not_available` `unit_unpriced` and no amount; a model with no entry gives `not_available`. Tracer never converts a currency: no parameter takes a rate or a currency, no INR word appears in the source.

## 6. P4-12 (part): the call log of a simulated run (`tracer_evalproviders.simcalllog`)

```text
SIM_CALL_LOG_NAME = "sim_call_log.jsonl"
class SimCallLog:
    def __init__(self, path: Path, *, clock=lambda: datetime.now(UTC)) -> None     # opens with O_NOFOLLOW, mode 0600
    @classmethod open(cls, out_dir, *, clock=...) -> SimCallLog     # mkdir(mode=0o700, parents=True, exist_ok=True)
    path -> Path
    def before(self, *, stage, index, attempt, bytes_sent, characters=None, audio_seconds=None) -> None
    def after(self, *, stage, index, attempt, bytes_sent, status, error_kind, elapsed_ms, request_id,
              characters=None, audio_seconds=None) -> None
    def connect(self, *, dialect: str) -> None
    def end(self, *, end_reason: str, counts: Mapping[str, int]) -> None
    def for_stt(self) -> view          # the CallLog shape: before(*, chunk_index, attempt, bytes_sent, audio_seconds),
                                       # after(*, chunk_index, attempt, bytes_sent, audio_seconds, status, error_kind,
                                       #       elapsed_ms, request_id), close() (a no-op: the log stays open)
    def close(self) -> None            # idempotent
```

One file for every engine of the call (A8). `SarvamClient(key, call_log=log.for_stt(), ...)` writes its STT lines into it (the dev widens the annotation of `call_log` to a protocol). Lines (JSON, ASCII, one per line, `separators=(",", ":")`, flushed and `fsync`ed before the call returns):

```text
before/after:  at event stage index attempt bytes_sent characters audio_seconds status error_kind elapsed_ms request_id   (all twelve members, always)
connect:       at event stage dialect                          stage "call", dialect one of DIALECTS
end:           at event stage end_reason counts                stage "call", end_reason one of END_REASONS, counts {machine_key: integer >= 0}
```

* `at`: `%Y-%m-%dT%H:%M:%S.%fZ` of the injected clock in UTC. `event`: `before`, `after`, `connect`, `end`. `stage`: `tts`, `llm`, `stt` (or `call`). `index`: the request's index (TTS, LLM) or the chunk index (STT). `bytes_sent`: the length of the request body. `characters` (TTS only): the code points of the text, a count; `audio_seconds` (STT only): the decimal string of Phase 3; the chat stage has neither. A `before` line has `status`, `error_kind`, `elapsed_ms` and `request_id` null. `elapsed_ms` = floor((after - before) * 1000) of the injected monotonic clock, which is read twice per attempt. `request_id`: only a plain identifier (`[A-Za-z0-9_.:-]{1,128}`), else null; `error_kind`: only `[a-z0-9_]{1,64}`, else null. For a success the id is the body's `request_id` (TTS) or `id` (chat), falling back to the `x-request-id` header; for an error the body's `error.request_id`, falling back to that header.
* Refused before writing, with a `ValueError` or `TypeError` (nothing is written): a stage outside `tts`, `llm`, `stt`; a `tts` line without `characters` or with `audio_seconds`; an `stt` line without `audio_seconds` or with `characters`; an `llm` line with either; a count that is not an `int` (`bool` and `float` are not); `audio_seconds` that is not a decimal string; a dialect or end reason outside the closed lists; a `counts` key that is not `[a-z][a-z0-9_]{0,63}` (a phone number cannot be a key) or a value that is not a non-negative integer.
* The line holds no text, no phone number, no URL, no header, no key, no model or voice name and no path (A8's list is exhaustive). Every attempt, retries included, has both lines; a refusal before the send and a cache hit have none; the `before` line is on disk while the request is in flight; a crash leaves it; a log that cannot be written (`OSError`) before a request stops it unsent, and a reply whose `after` line cannot be written is dropped unread (no usage handed out, nothing cached).
* Files: the file is 0600 (an existing looser file is tightened), the created leaf directory 0700 whatever the umask (parents default; an existing directory keeps its mode), appended to and never truncated, opened with `O_NOFOLLOW`: a link in place of the file, also a dangling one, is an `OSError` from `open` and from the constructor, and nothing is written or created through it. The module imports the standard library and the package's own `calllog.safe_request_id` only.

## 7. P4-11: the TTS cache (`tracer_evalproviders.ttscache`)

```text
TTS_CACHE_DIR_NAME = "tts-cache"
def tts_cache_key(*, provider, model, speaker, language_code, sample_rate_hz, codec, pace, text) -> str
class TtsCache:
    @classmethod open(cls, out_dir) -> TtsCache      # mkdir(out_dir, 0o700, parents) then out_dir/"tts-cache" (0o700); refuses a link
    path -> Path                                      # out_dir / "tts-cache"
    def get(self, key: str) -> bytes | None           # None for a missing entry; OSError for a link, a directory, a FIFO
    def put(self, key: str, audio: bytes) -> None     # O_NOFOLLOW; mode 0600; empty audio is a ValueError
```

* The key: lower-case hexadecimal SHA-256 of the UTF-8 bytes of the canonical JSON (keys sorted, separators `,` and `:`, `ensure_ascii=False`; equal to RFC 8785 for these members) of EXACTLY `{provider, model, speaker, language_code, sample_rate_hz (int), codec, pace (the decimal string of the voice), text}`; the golden value for the test's base member set is in the test (`de6911b8...b185`). Changing any one member changes the key; the request index is not in it; Unicode forms are compared as written. All arguments are keyword-only with no default; a non-string member or a non-`int` rate (a `bool` included) is a `TypeError`. A key given to `get` or `put` that is not 64 lower-case hex digits is a `ValueError` and nothing is touched (file names are built from nothing else); a non-string key is a `TypeError`, non-bytes audio a `TypeError`.
* The entry is the engine's audio (the decoded samples, not the reply), named by the key, mode 0600; `tts-cache` is created 0700 whatever the umask; an existing directory keeps its mode; a symbolic link in place of the directory (also dangling) is an `OSError` from `open` and nothing is created or written through it; a link in place of an entry (also dangling) is an `OSError` from `get` and `put` and its target is not read, written or created; a directory or a FIFO in place of an entry is an `OSError` from `get` at once (a FIFO must not hold the run); the directory holds no other name than the keys (no temporary file with text in its name); no text and no key in any name or file.
* Through the engine (`SarvamTts(..., cache=cache)`): a miss sends the request and stores the audio; a hit returns `TtsOutcome(audio=<same bytes>, usage=(), failure=None)`, sends nothing, writes no call-log line, hands nothing to `on_usage`, reads no clock, charges nothing against `cap_characters` and is served when the cap is used up; a second engine over the same `--out` hits too (a re-run spends zero requests). A failed request stores nothing. Each of the eight members in a request is a miss when it changes (voice model, speaker, pace, rate, text).
* A hit is validated like a reply (A9): the entry must be non-empty, at most `tts_max_audio_bytes(rate, codec)` bytes (exactly that is a hit) and, for `linear16`, of even length; otherwise the outcome is `TtsResponseInvalid` with `reason` `cache_entry_invalid` and the request's `index`, no request is sent, and the file is neither repaired nor deleted. A `mulaw` entry of any length (one that is within the cap) is a hit.
* Not pinned: concurrent writers, atomic replacement, a `put` over an existing entry with other content.

## 8. P4-10: per-channel speech-to-text with the declared role

### 8.1 `tracer_evalcore.audio.activity`

```text
ACTIVITY_FRAME_MS = 20
ACTIVITY_PLAN_FORMAT = "tracer.chunk_plan/2"
def activity_chunk_plan(pcm: bytes, *, sample_rate_hz: int, channels: int, channel: int,
                        wait_for_agent_silence_ms: int, max_chunk_ms: int = 25_000, search_ms: int = 5_000) -> ChunkPlan
@dataclass(frozen=True) class SttChannel: index: int; role: str
def select_stt_channel(channel_map: ChannelMap, *, role: str = "agent") -> SttChannel
class ChannelMapRefusedError(InputRefusedError)
```

`sample_rate_hz`, `channels`, `channel` and `wait_for_agent_silence_ms` are keyword-only with no default (ADR 0026 rule 10: no default in code). Parameter errors are the chunker's: `ChunkerParameterError` for `wait_for_agent_silence_ms` below 1 or not an `int` (`bool`, `float`, `str`), `channel` below 0, at or above `channels`, or not an `int`, `sample_rate_hz` below 1 or not an `int`, `search_ms` above `max_chunk_ms`; `ChannelsRefusedError` for `channels` above 2; `ChunkerInputError` for a byte length that is not a whole number of 16-bit frames; `TypeError` for a `pcm` that is not `bytes`. Input is little-endian interleaved 16-bit PCM of 1 or 2 channels; zero frames give a plan with no chunk.

The segment rule (ADR 0024 A5, restated by the tests in plain Python):

* Frames of 20 ms (`rate * 20 // 1000` samples) counted from sample 0 of the recording; the last frame may be shorter. A frame is ACTIVE when ANY of its samples of the requested channel is not 0: no threshold (+1, -1 and -32768 count). The other channel is ignored entirely (never a downmix).
* A segment is a maximal run of active frames; two runs are joined when the silence between them is SHORTER than `wait_for_agent_silence_ms` (`gap_frames * 20 < wait`); a gap equal to the value splits. A segment runs from the first sample of its first active frame to the last sample of its last active frame, bounded by the end of the recording.
* Each segment is cut by rule 7 of ADR 0024 when longer than `max_chunk_ms` (the chunker's `chunk_pcm` over the segment's mono samples; cuts at the quietest whole 20 ms frame of the last `search_ms` of each window counted from the segment start; offsets on the recording timeline). Chunks are numbered consecutively over the whole plan.

The plan: `plan.sample_rate_hz`, `plan.channels` (the recording's), `plan.channel`, `plan.wait_for_agent_silence_ms`, `plan.total_frames` (frames of the recording), `plan.max_chunk_ms`, `plan.search_ms`; each `Chunk` has `start_frame` and `end_frame` on the recording timeline, `sample_rate_hz` the recording's, `channels` = 1 and `wav` a mono 16-bit PCM WAV of exactly the channel's samples `[start, end)`. The same input gives byte-identical chunks. `plan.as_metadata()`, the document `tracer.chunk_plan/2` (integers only besides `format`):

```text
{"format": "tracer.chunk_plan/2", "max_chunk_ms": ..., "search_ms": ..., "sample_rate_hz": ..., "channels": <recording channels>,
 "channel": <index sent>, "wait_for_agent_silence_ms": ..., "frame_ms": 20, "total_frames": ...,
 "chunks": [{"index": ..., "start_frame": ..., "end_frame": ...}, ...]}
```

`select_stt_channel(map)`: the map must be `declared` (an inferred map is `ChannelMapRefusedError`: a role is never inferred) and must name exactly one channel with the wanted role (none, two, or a `mixed` channel only: `ChannelMapRefusedError`); the default role is `agent`, so the caller's channel is never chosen by default; `role="caller"` is the explicit opt-in; any other role is a `ValueError` whose message names `role`; a non-`ChannelMap` is a `TypeError`. The index is read from the map (the agent may be channel 0 or 1).

### 8.2 `ChunkPlan` and `SttContext` (additive; Phase 3 construction unchanged)

`ChunkPlan` keeps its six fields in their order and gains `channel: int | None = None` and `wait_for_agent_silence_ms: int | None = None`; `as_metadata()` returns the unchanged `tracer.chunk_plan/1` document when `channel` is `None`, and `/2` as above otherwise. `SttContext` keeps its five fields and gains `role: str = "unknown"` as the sixth; a role outside `caller`, `agent`, `unknown` is a `ValueError` whose message names `role` (also through `dataclasses.replace`). `map_responses` and `map_usage_records` (sarvam/mapping) give every Turn `role = context.role` and change nothing else: `source` `our_stt`, times offset by the chunk's start (`start_frame` is on the recording timeline), `tracer.chunk_plan` in the transcript metadata is `plan.as_metadata()`, the usage records are those of rule 13.

### 8.3 Phase 3 mono behaviour (P4-10: "unchanged")

A recorded mono plan: `tracer.chunk_plan/1`, `plan.channel is None`, `plan.wait_for_agent_silence_ms is None`, role `unknown`, the document keys of Phase 3, the Turn times of Phase 3; and the engine sends a mono plan at 8,000 Hz exactly as before (existing engine tests do). The context's role changes nothing but the role.

### 8.4 8 kHz (ADR 0024 A6, BL-293)

Whether Sarvam STT accepts 8,000 Hz is UNVERIFIED. `SarvamStt.transcribe` raises `SttConfigurationError` (that exact class or a subclass) with `reason` `sample_rate_not_accepted` before the first request when the plan is a simulated one (`plan.channel is not None`) and a chunk is at 8,000 Hz: nothing sent, nothing logged, nothing handed to `on_usage`, no cause, no context. Other rates (16,000 to 48,000 Hz) are sent at their own rate, never resampled. The chunk is the decoded 16-bit PCM in a WAV file, never the raw mu-law bytes (the decode is the recorder's, P4-06). The refusal is limited to simulated plans because the existing Phase 3 engine test `test_sarvam_a_long_call_is_sent_chunk_by_chunk_with_one_request_in_flight` sends an 8,000 Hz mono plan, and P4-10 says "Phase 3 mono behaviour unchanged" (ambiguity 3).

## 9. Spend caps at the adapters (extra; ADR 0026 rule 15)

Rule 15 says the adapter checks the cap before each request; the error classes exist since P4-03. TTS: a request is sent only while `used + len(text) <= cap_characters`; otherwise the OUTCOME is `TtsCapReachedError(index, reason="tts_chars")`, nothing is sent or logged, and the refused request uses nothing (a smaller one that fits is sent). Characters are code points, counted once at the first send of a request (a retry is not charged again, a request that was sent and failed IS counted: billing is unverified). Cache hits are not charged. `cap_characters=0` refuses every request; `None` means no cap. LLM: a request is sent only while `reported + request.max_tokens <= cap_tokens` (inclusive); `reported` is the sum of `prompt_tokens + completion_tokens` of the answered requests (cached tokens are part of the prompt, never added twice; a failed request reports nothing); otherwise the outcome is `LlmCapReachedError(index, reason="llm_tokens")`. Both caps are required constructor arguments, an integer from 0 to 2**53 - 1 or `None`; `bool`, `float`, `str`, negative and larger values are refused with `ValueError` or `TypeError`. The prompt tokens of one request may cross the cap (ADR 0026 rule 15, BL-295).

## 10. Keys: several roles, one variable (ADR 0024 A4)

```text
ENV_VAR_NAME = "SARVAM_API_KEY"                                        (unchanged)
ENV_VAR_NAMES = {"stt": "SARVAM_API_KEY", "tts": "SARVAM_API_KEY", "llm": "SARVAM_API_KEY"}
def load_role_key(role: str, *, environ=None, env_file=None) -> ApiKey
```

`load_role_key` is `load_api_key` with the error class of the role: `SttKeyMissingError`, `TtsKeyMissingError` or `LlmKeyMissingError`, with the same `reason` keys (`key_missing`, `key_invalid`, `key_ambiguous`, `env_file_not_regular`, `env_file_unreadable`, `env_file_too_large`), no cause and no context, no key, path or file name in any form. Every call returns its own `ApiKey` (its own redacting wrapper). All the env-file rules of ADR 0024 rule 4 hold (the file is the only source when given; nothing is exported; a mode with a group or other bit earns exactly one `EnvFileModeWarning` that holds no secret and no path). `load_api_key` is unchanged. An unknown role is a `ValueError` naming `role`, a non-string a `TypeError`. Other variables ending in `_API_KEY` are never read. `provider_config_ref(model)` serves every Sarvam model (`env:SARVAM_API_KEY`).

The canary (IA2) at the adapter level: nine hostile failure paths that echo the key into an exception message, a header, a redirect location and a body leave it in no form of the failure (str, repr, args, vars, chain), no form of the outcome, the engine (`repr`, `str`, `vars`), the call log, any usage record, any DEBUG log record (`httpx`, `httpcore`, the package loggers), any cache file; the key is in the header of the requests and nowhere else of them. `test_key_canaries_never_in_any_output_channel` (P4-30) adds the store, the report, the recovery file and the CLI.

## 11. Test inventory

The 20 SCOPE names (EXACTLY these; each defined once):

| Task | Name | File |
|---|---|---|
| P4-08 | `test_sarvam_tts_sends_explicit_model_speaker_language_rate` | `evalproviders/test_sarvam_tts_request.py` |
| P4-08 | `test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send` (parametrised over 9 language codes) | `evalproviders/test_sarvam_tts_request.py` |
| P4-08 | `test_sarvam_tts_response_rejects_wrong_shape_and_oversize` (parametrised over 31 bodies) | `evalproviders/test_sarvam_tts_response.py` |
| P4-08 | `test_sarvam_tts_retry_taxonomy_matches_stt_rules` (parametrised over the scenarios) | `evalproviders/test_sarvam_tts_response.py` |
| P4-08 | `test_sarvam_tts_base_url_not_overridable` | `evalproviders/test_sarvam_tts_request.py` |
| P4-09 | `test_sarvam_llm_model_and_max_tokens_required` (4 models) | `evalproviders/test_sarvam_llm.py` |
| P4-09 | `test_sarvam_llm_usage_maps_tokens_exactly` | `evalproviders/test_sarvam_llm.py` |
| P4-09 | `test_sarvam_llm_seed_recorded_never_claimed_deterministic` | `evalproviders/test_sarvam_llm.py` |
| P4-09 | `test_sarvam_llm_response_rejects_wrong_shape_and_oversize` (parametrised over 44 bodies) | `evalproviders/test_sarvam_llm.py` |
| P4-10 | `test_stt_agent_channel_turns_have_role_agent_from_declared_map` (2 map orders) | `evalproviders/test_sarvam_stt_perchannel.py` |
| P4-10 | `test_activity_chunk_plan_is_deterministic_and_covers_agent_audio` | `evalcore/test_activity_chunk_plan.py` |
| P4-10 | `test_phase3_mono_mapping_unchanged` | `evalproviders/test_sarvam_stt_perchannel.py` |
| P4-10 | `test_stt_never_runs_on_caller_channel_by_default` | `evalproviders/test_sarvam_stt_perchannel.py` |
| P4-11 | `test_tts_cache_hit_sends_nothing_and_records_no_usage` | `evalproviders/test_sarvam_ttscache.py` |
| P4-11 | `test_tts_cache_key_covers_every_parameter` | `evalproviders/test_sarvam_ttscache.py` |
| P4-11 | `test_tts_cache_refuses_links_and_is_private` | `evalproviders/test_sarvam_ttscache.py` |
| P4-12 | `test_sim_usage_records_per_request_with_stages` | `evalproviders/test_sarvam_sim_usage.py` |
| P4-12 | `test_sim_cost_not_available_without_usd_catalog` | `evalproviders/test_sarvam_sim_usage.py` |
| P4-12 | `test_sim_call_log_has_no_text_no_number_no_key` | `evalproviders/test_sarvam_sim_calllog.py` |
| P4-12 | `test_sim_call_log_written_before_and_after_each_request` | `evalproviders/test_sarvam_sim_calllog.py` |

Added by qa (not in the SCOPE table; every one has its reason in its docstring): the rest of the 13 + 2 files: the activity-plan rule tests and the channel selection (`test_activity_chunk_plan.py`); the container tests (`test_sarvam_tts_container.py`); the chat request, reply, usage and seed tests and their taxonomy mirror (`test_sarvam_llm.py`); the streaming-usage tests, the usage-record details and the cost with a catalog (`test_sarvam_sim_usage.py`); the call-log schema, files and links (`test_sarvam_sim_calllog.py`); the cache engine integration, validation and file rules (`test_sarvam_ttscache.py`); the role keys, the factories and the canary tests (`test_sarvam_sim_keys.py`); the caps (`test_sarvam_sim_caps.py`); the hostile replies and the failing log (`test_sarvam_sim_hostile.py`); the 8 kHz refusal and the rest of the per-channel behaviour (`test_sarvam_stt_perchannel.py`). The counts per file and per Python version are in section 13.

## 12. Named spec change

```text
S-B  packages/evalcore/tests/evalcore_phase4_adr_support.py : the pending sets shrink, deletions only (36 entries of 20 names)
     before: the 20 names are in the sets as "cited by an ADR, defined by no test yet"; once the tests exist, `check_adr` refuses a
             pending name that a test defines, and 11 ADR reader tests fail (adr0024 x3, adr0026 x2, adr0027 x2, adr0028 x2, adr0029 x2)
     after:  the sets no longer hold them (the staged copy of the file has them deleted; `spec-change.diff` is the before and after)
```

New sizes: 0026 57 to 52, 0027 41 to 34, 0028 18 to 15, 0029 14 to 13, 0024A 25 to 5; 0023A, 0004 and the outside-rules set are unchanged. The deleted names, per set:

```text
PENDING["0026"]  (5): test_sarvam_tts_sends_explicit_model_speaker_language_rate, test_sarvam_llm_model_and_max_tokens_required,
                      test_sarvam_llm_seed_recorded_never_claimed_deterministic, test_tts_cache_hit_sends_nothing_and_records_no_usage,
                      test_tts_cache_key_covers_every_parameter
PENDING["0027"]  (7): test_stt_agent_channel_turns_have_role_agent_from_declared_map, test_activity_chunk_plan_is_deterministic_and_covers_agent_audio,
                      test_phase3_mono_mapping_unchanged, test_stt_never_runs_on_caller_channel_by_default,
                      test_tts_cache_hit_sends_nothing_and_records_no_usage, test_sim_usage_records_per_request_with_stages,
                      test_sim_cost_not_available_without_usd_catalog
PENDING["0028"]  (3): test_sarvam_llm_model_and_max_tokens_required, test_sarvam_llm_seed_recorded_never_claimed_deterministic,
                      test_stt_agent_channel_turns_have_role_agent_from_declared_map
PENDING["0029"]  (1): test_sim_call_log_has_no_text_no_number_no_key
PENDING["0024A"] (20): every one of the 20 SCOPE names of section 11
```

No other test was edited, skipped or deleted. The new tests do not weaken any: the Phase 3 suites of `tracer-evalproviders` (710 tests) and `tracer-evalcore` pass unchanged on the oracle. The dev deletes these names in the same commit that moves the tests (the dev's own `check_adr` run says so if one is left).

## 13. Evidence

`docs/phase4/evidence/red/batch-b/` (scratch mirrors outside the repository; mirror = the tree at `d940404` plus the staged files; logs scrubbed of home, user, scratch and temp paths):

```text
red.py3.11.log .. red.py3.14.log     the 12 staged test files + the support files at their final path on the CURRENT tree:
                                     596 tests, 592 failed, 4 passed on every version (the 4 are guards of existing facts)
red-reasons.py3.13.txt               the reason of each failure by count: ModuleNotFoundError / AttributeError of the batch B code only
oracle.py3.11.log .. oracle.py3.14.log
                                     packages/evalproviders/tests + packages/evalcore/tests on the oracle: 2,320 passed, 1 skipped
                                     (the 1 skip is the opt-in gate check) on every version; 710 of them are the Phase 3 provider tests
oracle-full-suite.py3.11.log .. py3.14.log
                                     the whole workspace on the oracle (see the report for the count and the one failure that is not batch B's)
mutants.txt                          the mutants of the oracle against the staged tests
oracle-src.patch                     the oracle's non-test changes (a starting point, not a substitute)
spec-change.diff                     the named spec change S-B, before and after
```

Counts of the 596 new test items per file: `test_sarvam_llm` 189, `test_sarvam_tts_response` 146, `test_sarvam_sim_calllog` 53, `test_sarvam_tts_request` 43, `test_sarvam_sim_hostile` 27, `test_sarvam_tts_container` 26, `test_activity_chunk_plan` 21, `test_sarvam_sim_usage` 20, `test_sarvam_ttscache` 20, `test_sarvam_sim_caps` 19, `test_sarvam_sim_keys` 17, `test_sarvam_stt_perchannel` 15; 222 of them are the 111 shared retry and error scenarios run against both adapters.


## 14. Ambiguities and UNVERIFIED items (with recommendations; the lead auto-approves)

1. **The TTS reply container per codec is not verified** (ADR 0024 A2). Recommendation (applied): fail closed on a canonical WAV for `linear16`, pin nothing for `mulaw`, isolate everything that depends on it (section 2.4); the first owner-gated reply fixes it and changes `wav_bytes`, `tts_reply` and `test_sarvam_tts_container.py`. Rejected: accepting raw samples or any container "that looks like audio" (it would hide what was heard).
2. **Header, error body and chat usage shape for TTS and chat v2 are assumed as for STT** (`api-subscription-key`, `{"error": {"message","code","request_id"}}`, `usage.prompt_tokens` / `completion_tokens`, `usage.prompt_tokens_details.cached_tokens`). The ADR names `prompt_tokens`/`completion_tokens` (verified) and says cached-token reporting is not verified. Recommendation: keep; each is one line of the adapter and one assertion; verify on the first owner-gated reply of each engine.
3. **The 8 kHz refusal is limited to simulated (`/2`) plans.** ADR 0024 A6 says "a real `--stt sarvam` request on an 8 kHz chunk is refused"; read literally it also refuses a recorded mono 8 kHz plan, which breaks an existing Phase 3 test (`test_sarvam_a_long_call_is_sent_chunk_by_chunk_with_one_request_in_flight`, an 8 kHz mono plan through a mock transport) and contradicts P4-10 "Phase 3 mono behaviour unchanged". Recommendation (applied): refuse only `plan.channel is not None`; the lead may widen it with a named spec change of that test. BL-293 stays the owner-gated check.
4. **Caps at the adapters** (section 9) are behaviour ADR 0026 rule 15 gives to "the adapter" and SCOPE gives no P4-08..12 test name; pinned as extras. A request that was sent and failed counts against the characters cap; a retry does not (billing of failed attempts is unverified). Recommendation: keep; the CLI (P4-23) passes the four caps.
5. **`SimCallLog` is one object shared by the three engines; the STT client gets a CallLog-shaped view.** A8 says one file; Phase 3's `CallLog` has the STT line schema (`chunk`, `audio_seconds`). Recommendation (applied): a new class with the A8 line schema and `for_stt()`; `sarvam_call_log.jsonl` keeps its Phase 3 role for `tracer score`. Rejected: a second file per engine (the order across engines is lost, A8).
6. **`stream` is absent from the chat body** (the exact member set). ADR 0028 rule 5 says "no streaming, and nothing the provider would default"; if Sarvam's default is not non-streaming, `"stream": false` must be sent. Recommendation: send nothing until the first owner-gated reply shows the default; if streaming turns out to be the default, add `"stream": false` and one assertion.
7. **`TtsCapReachedError`/`LlmCapReachedError` are returned as outcome failures, the configuration refusals (language, length, key) are raised.** The first is a spend decision during the call (ends it with `spend_cap`), the second a configuration error (exit 4 before connect). Recommendation: keep.
8. **A `request_id`/`id` over 256 characters is `request_id_invalid`/`id_invalid`.** Not in the ADR; a hostile reply could otherwise put a megabyte string into a stored usage record (the TTS body cap is tens of megabytes). A technical bound like the others. Recommendation: keep.
9. **The numeric bounds fixed here** (600 s of audio, +4,096 bytes of JSON slack, 65,536 bytes of reply text, 256 characters of id) are technical bounds, not measured; ADR 0024 A2 leaves the first to P4-08 and says to state them in the CHANGELOG.
10. **`TtsVoice.provider` other than `sarvam` through `SarvamTts`** is not pinned (the engine could refuse it); the cache key covers it.
11. **A truncated chat reply** (`finish_reason` `length`) is accepted: `LlmOutcome` has no field for it. ADR 0028 rule 10 says a cap ends a call "never with a truncated line"; the validator of batch C must therefore reject by script and length, and a model cut at `max_tokens` can still pass both. Recommendation: a BL row for the finish reason (a field on `LlmOutcome` is a boundary change, not batch B).
12. **A cache entry written by a failed run** (a crash between the write of the entry and the usage hand-out) is a hit afterwards with no usage record. The order is: write the entry, then hand out the usage; a `KeyboardInterrupt` in between loses one record for a request that was paid. Not pinned (BL-228 territory). Recommendation: keep the order; the call log still shows the request.
