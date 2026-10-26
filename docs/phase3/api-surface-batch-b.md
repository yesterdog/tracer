# Phase 3 batch B contract: Sarvam adapter, key handling, mapping, cost, call log, dry run (P3-06 to P3-11)

Author: qa, 2026-10-25. Source: `docs/phase3/SCOPE.md` (tasks A.2, Decisions D1 to D8, findings F1, F2, F8, F10), ADR 0024 (17 rules, invariants I1 to I9, the amended rules 7 and 13), ADR 0004 Decision 10, the batch A contract (`docs/phase3/api-surface-batch-a.md`) and its implementation in `packages/evalcore` and `packages/evalproviders` (scaffold), the 47 Sarvam captures of 2026-10-23 (`packages/evalschema/tests/fixtures/real/sarvam-2026-10-23/`, `tools/capture/SARVAM_OBSERVATIONS.md`), the F3 mapper (`packages/evalschema/tests/support/mappings/sarvam_stt.py`), `tracer_evalschema` `usage.py`, `pricing.py`, `cost.py`, `transcript.py`, `docs/security/P3-02-licenses.md`, the decision log "PHASE 3" entries. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test. Every statement below is pinned by a test named in section 12, unless it says "not pinned".

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/`. The tests are staged at

```
docs/phase3/evidence/red/batch-b/staging/tests/   -> packages/evalproviders/tests/
```

The folder has no `__init__.py` (packages/evalschema/tests is the package `tests`). It holds 11 files: `conftest.py` (the no-network guard and the key-free environment, autouse), `evalproviders_support.py` (shared helpers, the evaldb precedent `evaldb_support.py`), and 9 `test_*.py` files. The dev's first act in batch B is

```
git mv docs/phase3/evidence/red/batch-b/staging/tests/*.py packages/evalproviders/tests/
```

(the existing `packages/evalproviders/tests/test_evalproviders_scaffold.py` stays). Nothing in the tests depends on the location: the repository root is found by walking up to `uv.lock`; the sibling import is `evalproviders_support` (module names are unique across the five test directories). No root `pyproject.toml` change is needed: `packages/evalproviders/tests` is already in `testpaths`, in `[tool.ruff] src` and in `[tool.mypy] files`. The staged files pass `ruff check`, `ruff format --check` and `mypy --strict` in the repository as they are; they are ASCII only (non-ASCII test data is written as `\u` escapes); nothing at module level imports a batch B module (each test loads `tracer_evalcore.stt` and `tracer_evalproviders.*` lazily with `importlib`), so before the code exists every test fails on its own with `ModuleNotFoundError` and the directory still collects. The Phase 1 T-DRIFT scan (54 tests) passes on the tree with the staged files and this contract.

Commands (after the move and the new sources):

* Phase 3 only: `uv run --locked --all-groups pytest packages/evalcore/tests packages/evalproviders/tests packages/cli/tests`
* everything: `uv run --locked --all-groups pytest`

No test opens a socket (section 1), reads the environment of the machine for a key, reads `.env.local`, the private fixture or the keys notes file, or sleeps for real.

## 1. Hard rules the tests enforce (ADR 0024 rule 17)

* `conftest.py` (autouse) replaces `socket.socket.connect`, `connect_ex`, `socket.create_connection`, `socket.getaddrinfo`, `asyncio.BaseEventLoop.create_connection` and `sock_connect` with functions that raise `AssertionError("a unit test tried to use the network ...")`. It is an `AssertionError` on purpose (not an `OSError`): an adapter that retries or translates transport errors must not swallow it. `test_unit_tests_open_no_socket` proves each guard and, by AST, that the adapter source builds exactly one HTTP client, `httpx.Client(transport=..., follow_redirects=...)` in `sarvam/client.py`, with no `verify`, `base_url` or `cert` keyword and no httpx shortcut (`httpx.get` and the like), and that no test file builds a client without `transport=`.
* The HTTP is an `httpx.MockTransport` subclass (`ScriptedTransport`): scripted responses, scripted exceptions, or a function of the request; more requests than scripted is an `AssertionError`.
* Sleeping and timing are injected: `sleep(seconds)`, `monotonic()` and the call log's `clock()`. The tests never wait.
* The key is the canary `CANARY_KEY`, built from parts in `evalproviders_support.py` (the literal never appears in a file; it belongs to no account). Two more canaries stand for response text (`TEXT_CANARY`) and provider error text (`MESSAGE_CANARY`).
* The response bodies are the captures (read as bytes, with their recorded status and `date`, `content-type` and `x-request-id` headers) or bodies written in the tests.

## 2. Modules, names, locations

The SttEngine protocol, the typed result, the context and the error taxonomy are provider-neutral and live in the pure core, because the pipeline (batch D) and the CLI must type the engine and catch the failures without importing the network package (ADR 0024 rule 1); the module imports no network module. **New module `tracer_evalcore.stt`** (the dev adds it to evalcore; evalcore's import whitelist is unchanged: it imports `tracer_evalschema` and the standard library only):

| Name | Kind |
|---|---|
| `SttEngine` | `typing.Protocol`, `@runtime_checkable`: `transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome` |
| `SttContext` | frozen dataclass: `run_id: RunId`, `audio_artifact_id: ArtifactId`, `created_at: datetime`, `privacy: PrivacyMarker`, `configured_language: str` |
| `SttOutcome` | frozen dataclass: `transcript: Transcript \| None`, `turns: tuple[Turn, ...]`, `usage: tuple[UsageRecord, ...]`, `warnings: tuple[IngestWarning, ...]`, `failure: SttError \| None` |
| `SttError(EvalcoreError)` and the six taxonomy classes, `SttConfigurationError`, `SttKeyMissingError`, `SttLanguageNotSupportedError` | exceptions, section 5.3 |

**New modules of `tracer_evalproviders`** (the package stays `httpx`, `tracer_evalschema`, `tracer_evalcore` and the standard library; no `pydantic` import, no `sarvamai`, no `requests`):

| Module | Names |
|---|---|
| `tracer_evalproviders.secrets` | `ENV_VAR_NAME`, `CONFIG_REF`, `PLACEHOLDER`, `ApiKey`, `load_api_key`, `provider_config_ref` |
| `tracer_evalproviders.sarvam.models` | `API_BASE_URL`, `STT_PATH`, `STT_URL`, `DEFAULT_MODEL`, `FORM_FIELD_NAMES`, `MAX_RESPONSE_BYTES`, `language_code_for`, `SarvamRequest`, `SarvamPhrase`, `SarvamSttResponse`, `ChunkResponse`, `SarvamResponseShapeError`, `parse_response` |
| `tracer_evalproviders.sarvam.client` | `MAX_ATTEMPTS`, `BACKOFF_SECONDS`, `RETRY_AFTER_MAX_SECONDS`, `CONNECT_TIMEOUT_SECONDS`, `POOL_TIMEOUT_SECONDS`, `READ_TIMEOUT_SECONDS`, `WRITE_TIMEOUT_SECONDS`, `SarvamClient` |
| `tracer_evalproviders.sarvam.engine` | `SarvamStt`, `create_sarvam_stt` |
| `tracer_evalproviders.sarvam.mapping` | `MODEL_BASIS`, `ROUNDING_KEY`, `usage_audio_seconds`, `map_usage_records`, `map_responses` |
| `tracer_evalproviders.calllog` | `CALL_LOG_NAME`, `CallLog`, `safe_request_id` |
| `tracer_evalproviders.cost` | `CatalogRefusedError`, `SttCost`, `load_user_catalog`, `compute_stt_cost` |
| `tracer_evalproviders.dryrun` | `DryRunCost`, `DryRunPlan`, `build_dry_run_plan` |

`tracer_evalproviders/sarvam/__init__.py` exists (a package). Only `secrets.py` may read the environment (`os.environ`, `os.getenv`; AST test); `calllog.py` imports the standard library only; `dryrun.py` imports neither `httpx`, `os`, `socket`, `ssl`, nor `secrets`, `calllog`, `sarvam.client` or `sarvam.engine`, and importing it in a fresh interpreter loads no `httpx`, `httpcore` or `h11`.

## 3. The request (P3-06; ADR 0024 rules 3 and 6)

Constants: `API_BASE_URL = "https://api.sarvam.ai"`, `STT_PATH = "/speech-to-text"`, `STT_URL = API_BASE_URL + STT_PATH`, `DEFAULT_MODEL = "saaras:v4"`, `FORM_FIELD_NAMES = ("file", "language_code", "model", "with_timestamps")`, `MAX_RESPONSE_BYTES = 1_048_576`. They are the only URL literals in the package, in `sarvam/models.py`.

3.1 One request per chunk: `POST https://api.sarvam.ai/speech-to-text`, `multipart/form-data` with **exactly** these four fields, each once and no other (`test_sarvam_request_sends_unknown_language_and_explicit_model`):

| Field | Value |
|---|---|
| `file` | the chunk's WAV file, `chunk.wav` byte for byte (native rate, never resampled; stereo chunks unchanged); part filename matching `[A-Za-z0-9._-]+\.wav` (the oracle uses `chunk-<index>.wav`; a local path is never a filename); part content type `audio/wav` |
| `language_code` | `unknown`, always (`test_sarvam_request_never_sends_forced_code_in_base_case`: no `mr-IN`, `mr_IN`, `hi-IN`, `en-IN` byte appears in any request) |
| `model` | the model the client was built with, always sent (default `saaras:v4`); the other three fields are plain text parts with no filename and no content type |
| `with_timestamps` | `true` |

3.2 The key travels in one header only, `api-subscription-key`; no `Authorization` header, nothing in the URL, the query or the body. The URL is exactly `https://api.sarvam.ai/speech-to-text` (scheme https, host `api.sarvam.ai`, no port, no query, no fragment) whatever the environment holds (`test_sarvam_base_url_not_overridable` plants seven candidate variables). No public entry point (`SarvamClient.__init__`, `send_chunk`, `SarvamStt.__init__`, `transcribe`, `create_sarvam_stt`, `SarvamRequest`) has a parameter named `url`, `base_url`, `endpoint`, `host`, `port`, `verify`, `cert`, `proxy`, `proxies`, `follow_redirects`, `http2`, `trust_env`, `headers`, `path`, `scheme` or `event_hooks`.

*Amended by batch G (docs/phase3/api-surface-batch-g.md 6.1): every attempt, also every retry, carries exactly one `Accept-Encoding: identity` header (`test_audit_f10_every_attempt_asks_for_an_uncompressed_reply`); the key header, the method and the multipart body are unchanged. A reply that is compressed anyway is still decoded and still bounded by the size cap of 4.3.*

3.3 `language_code_for(configured: str) -> str` returns `"unknown"` for exactly `"mr"` and raises `SttLanguageNotSupportedError` for anything else, including the other eight in-scope languages (`en hi te ta kn ml pa gu`), `"mr-IN"`, `"MR"`, `"mr "`, `""`, `"unknown"`, `"mar"` (`test_sarvam_configured_language_other_than_mr_refused`, 17 cases). `SarvamStt.transcribe` calls it first: refused before any request and before any call-log line. `SarvamRequest(model)` is a frozen dataclass with `.text_fields(configured_language) -> dict[str, str]` (`language_code`, `model`, `with_timestamps`, in that order).

3.4 The model name is checked when the client is built: it must match the evalschema machine key `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$` (`saaras:v4` does); otherwise `SttConfigurationError`.

## 4. The response (P3-06; ADR 0024 rule 8)

`parse_response(body: bytes) -> SarvamSttResponse`; on failure `SarvamResponseShapeError(reason)` (a `ValueError`; `str(exc) == reason`; the body is never in it). **Any** failure of the parser is one typed refusal: the oracle needed `except Exception` because `loads_strict` raises `DuplicateKeyError` and `DepthLimitExceeded` (finding F-B3).

```text@dataclass(frozen=True)
class SarvamPhrase:   text: str (repr=False); start_seconds: Decimal; end_seconds: Decimal
@dataclass(frozen=True)
class SarvamSttResponse:   # exactly these fields, in this order; unknown members are dropped
    request_id: str; transcript: str (repr=False); language_code: str | None
    language_probability: Decimal | None; phrases: tuple[SarvamPhrase, ...] | None   # None = no timestamps
@dataclass(frozen=True)
class ChunkResponse:   chunk_index: int; response: SarvamSttResponse; responded_at: datetime | None; attempts: int
```

4.1 The rules and their `reason` keys (`test_sarvam_response_rejects_wrong_types_and_oversize_body`, 51 bad bodies; `test_sarvam_response_accepts_the_documented_variants`, 8 good ones):

| reason | when |
|---|---|
| `not_json` | not UTF-8, empty, truncated, trailing text, a byte order mark, `NaN` or `Infinity`, a duplicate member, a lone surrogate (`"\ud800"`), nesting 100,000 deep |
| `not_object` | valid JSON that is not an object (array, string, number, null) |
| `request_id_invalid` | `request_id` absent, null, number or list |
| `transcript_invalid` | `transcript` absent, null, number or list (`""` and white space are valid) |
| `language_code_invalid` | present and a number, bool, list or object (null and absent are valid and read as `None`) |
| `language_probability_invalid` | present and a string, null, bool, list, below 0 or above 1 (0 and 1 are valid) |
| `timestamps_invalid` | present and null, a string, a list, an empty object; `words`, `start_time_seconds` or `end_time_seconds` missing or not lists; unequal lengths; a word that is not text; a time that is not a number (string, null, bool); a negative time; an end before its start. Empty arrays and `start == end` are valid; extra members inside `timestamps` are ignored |

4.2 Numbers are read from their JSON text as `Decimal`, never through a float: `0.9950000000000000001` and `3.3300000000000000001` keep every digit; `1E0` and integer times (`0`, `2`) are decimals. `repr` and `str` of a `SarvamSttResponse` and of a `SarvamPhrase` hold no transcript and no phrase text. All 32 captures with status 200 (31 `stt_*` and `err_bn_in_unrelated_clip`) parse to the values written in their bodies; the 6 error captures are not a success body (`test_sarvam_response_models_parse_all_captures`).

4.3 The size cap (client): at most `MAX_RESPONSE_BYTES` = 1,048,576 decoded bytes of any response body are read. A 200 body of exactly the cap is accepted; one byte more is `SttResponseInvalid(reason="body_too_large")`; reading stops at the cap (an endless stream is read at most the cap plus two 64 KiB pieces); a gzip bomb is capped after decoding. An error response (4xx, 5xx) is classified by its status; if its body is over the cap it is not read to the end and its `code` is `unrecognised` (not pinned by the ADR text; section 11 item 9).

## 5. The HTTP client (P3-07; ADR 0024 rules 2, 3, 5, 8, 9, 10)

### 5.1 Signature

```textclass SarvamClient:
    def __init__(self, api_key: ApiKey, *, call_log: CallLog, model: str = DEFAULT_MODEL,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None
    model: str                                   # property
    def send_chunk(self, chunk: Chunk) -> ChunkResponse     # raises an SttError subclass
    def close(self) -> None;  __enter__ / __exit__
```

`api_key` must be an `ApiKey` (`TypeError` otherwise); `call_log` is required (every attempt is logged); the defaults of `sleep`, `monotonic` and `transport` are `time.sleep`, `time.monotonic` and `None` (production: httpx's default transport). The one `httpx.Client` is built with `transport=transport`, `follow_redirects=False`, `trust_env=False` (amended by batch G: the client reads no proxy, certificate or `.netrc` environment; `test_audit_f3_*`) and `timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)`; the request's `extensions["timeout"]` equals `{"connect": 10.0, "read": 60.0, "write": 60.0, "pool": 10.0}` (`test_sarvam_timeouts_are_10s_to_connect_and_pool_60s_to_read_and_write`). Constants: `MAX_ATTEMPTS = 4`, `BACKOFF_SECONDS = (1, 2, 4)`, `RETRY_AFTER_MAX_SECONDS = 30`, `CONNECT_TIMEOUT_SECONDS = POOL_TIMEOUT_SECONDS = 10`, `READ_TIMEOUT_SECONDS = WRITE_TIMEOUT_SECONDS = 60`.

### 5.2 Retry policy (rule 9)

* A chunk gets at most 4 attempts. Retried: HTTP 429, any 5xx, and every `httpx.RequestError` that is not a decoding error (connect, read and write errors, the four timeouts, protocol errors, a bare `httpx.RequestError`). No other status is retried.
* The waits before attempts 2, 3 and 4 are 1, 2 and 4 s, no jitter; `sleep` is called with exactly those values (floats), once per wait, and never after the last attempt (`test_sarvam_retries_429_and_503_then_succeeds`, 8 cases; `test_sarvam_gives_up_after_bounded_retries`, 6 cases: 429, 500, 503, 599, connect error, read timeout, each with exactly 4 requests, sleeps `[1, 2, 4]`, `attempts == 4`). The class of the final error follows the last failure (429, 503, timeout, 500 gives `SttServerError`).
* `Retry-After`, honoured on **429 and 503 only** (on 500, 502, 504 it is ignored): the wait is `max(backoff, header)`, never less than the header asks. Delay-seconds are ASCII digits only (`^[0-9]+$`): `"7"` waits 7, `"0"` waits the backoff, `"30"` waits 30. An HTTP date is measured against the **same response's** `Date` header, never the local clock: 10 s after `Date` waits 10; in the past or equal gives the backoff; no `Date`, or an unparseable `Date`, ignores the header. Unparseable values are ignored (backoff only): `soon`, `1.5`, `-5`, empty, `+5`, `5s`, `0x10`, `1e1`, `5 6`, `NaN`, and Arabic-Indic digits. A value **above 30 s** (31, 3600, a 23-digit number, a date 32 s or a day after `Date`) ends the attempts for that chunk with `SttRateLimited` (status 429 or 503, `attempts` the number of attempts made, no sleep, no further request); exactly 30 is honoured (`test_sarvam_honours_bounded_retry_after`, 15 + 8 + 13 + 6 + 1 cases).
* A 4xx other than 429 is never retried and ends the attempts, also after a retry (429 then 400 stops at attempt 2). 401 and 403 give `SttAuthError`; any status with `error.code == "invalid_api_key_error"` gives `SttAuthError`; every other 4xx (400, 404, 408, 409, 413, 422, 451, ...) gives `SttRequestRejected` (`test_sarvam_does_not_retry_400_403`, 9 statuses).
* A failed attempt makes no UsageRecord (the client returns none; the engine maps successes only).

### 5.3 The error taxonomy (rule 10) and its facts

Module `tracer_evalcore.stt`. `SttError(EvalcoreError)` takes keyword-only `chunk_index`, `status`, `kind`, `code`, `request_id`, `attempts`, `reason` (all default `None`; positional arguments are a `TypeError`) and keeps them as attributes; `str(error)` is built from them only and contains `chunk <i>`, `HTTP <status>`, `code <code>`, `request <id>` and the attempts when set; the error is picklable. The six taxonomy classes are **direct** subclasses of `SttError` (the tests use `type(exc) is ...`); none is a `ValueError`:

| Class | When | Retried | Facts set |
|---|---|---|---|
| `SttAuthError` | 401, 403, or `invalid_api_key_error` | no | `status`, `code`, `request_id`, `chunk_index`, `attempts` |
| `SttRequestRejected` | any other 4xx except 429 | no | same |
| `SttRateLimited` | 429 after the last attempt; `Retry-After` above 30 s (also on 503) | yes, within rule 9 | same; `reason="retry_after_above_bound"` for the bound |
| `SttServerError` | 5xx after the last attempt | yes | same |
| `SttTransportError` | a request error after the last attempt | yes | `kind`, `chunk_index`, `attempts` (no `status`) |
| `SttResponseInvalid` | 200 body that fails section 4 (`reason` from section 4.1), `content_type` (a 200 whose media type is not `application/json`, parameters and case ignored), `body_too_large`, `unexpected_status` (1xx, 3xx, a 2xx other than 200; never followed), `body_undecodable` (a corrupt compressed body) | no | `reason`, `status`, `chunk_index`, `attempts` |

`SttConfigurationError(SttError, ValueError)` with direct subclasses `SttKeyMissingError` and `SttLanguageNotSupportedError` (both `type(exc) is` exact) are raised before any request (sections 3.3 and 6).

* `kind` is one of `connect_timeout`, `read_timeout`, `write_timeout`, `pool_timeout`, `connect_error`, `read_error`, `write_error`, `transport_error` (any other request error, such as `RemoteProtocolError`, `ProxyError` or a bare `RequestError`).
* `code` is `error.code` of the body only when it matches `^[a-z][a-z0-9_]{0,63}$`; otherwise `"unrecognised"` (upper case, hyphen, space, a newline, markup, a leading digit, empty, 65 characters, an HTML or empty or non-object body). `request_id` is `error.request_id` of the body, else the `x-request-id` header, kept only when it matches `^[A-Za-z0-9_.:-]{1,128}$`, else `None`. The `message` of the provider, the body and the headers are in no attribute, no `str`, no `repr`, no `args`, no `__dict__` of any error (`test_sarvam_error_taxonomy_matches_captures`: the 6 `err_*` captures give `err_bad_key` -> `SttAuthError` 403 `invalid_api_key_error`; the five 400s -> `SttRequestRejected` `invalid_request_error`; request ids equal to the bodies').
* The error is raised with **no `__cause__` and no `__context__`** (`chain(exc) == [exc]`), also for transport errors whose own text and request hold the key: the oracle records the facts inside the `except` block and raises after it (finding F-B2). `raise ... from None` inside the handler is not enough: `__context__` stays set.

### 5.4 The date and the result

`ChunkResponse.responded_at` is the response `Date` header parsed as an instant (UTC), or `None` when it is missing, empty, unparseable, out of range, ISO 8601 text, or carries no zone (`-0000`); `+0530` is converted to UTC. It is never the local clock. `attempts` is the attempt that succeeded.

### 5.5 The engine

```textclass SarvamStt:    # satisfies SttEngine (isinstance on the runtime-checkable protocol)
    provider = "sarvam"
    def __init__(self, client: SarvamClient) -> None
    model: str       # property, the client's
    def transcribe(self, plan: ChunkPlan, context: SttContext) -> SttOutcome

def create_sarvam_stt(*, out_dir, model=DEFAULT_MODEL, environ=None, env_file=None, transport=None,
                      sleep=time.sleep, monotonic=time.monotonic, clock=<utc now>) -> SarvamStt
```

* `transcribe` checks the configured language first (raises `SttLanguageNotSupportedError`), then sends the chunks **sequentially, in chunk order, one request in flight** (the test counts concurrency), each carrying its own `chunk.wav`. A plan with no chunk sends nothing and returns a transcript with no turn and no usage. A 400-chunk call (40 s at 8 kHz in 100 ms windows) works.
* All or nothing (rule 11, `test_sarvam_partial_chunk_failure_yields_no_transcript`, 3 positions x 8 failure kinds): on the first chunk that fails after its attempts, **any** failure class, the engine **returns** (does not raise) `SttOutcome(transcript=None, turns=(), usage=<one record per chunk that succeeded>, warnings=(), failure=<the SttError>)` and sends nothing for later chunks; `failure.chunk_index` is the failed chunk; a chunk that succeeded on a retry counts once, with the request id of the successful attempt.
* `SttOutcome.__post_init__`: exactly one of `transcript` and `failure` is set, and a failed outcome holds no turn and no warning (`ValueError` otherwise).
* `create_sarvam_stt` reads the key **first** (section 6), then opens the call log in `out_dir` (`CallLog.open`), then builds the client: a missing key raises `SttKeyMissingError` before any request and **before `out_dir` exists**.

## 6. Key handling (P3-08; ADR 0024 rules 4 and 5)

```textENV_VAR_NAME = "SARVAM_API_KEY";  CONFIG_REF = "env:SARVAM_API_KEY";  PLACEHOLDER = "<redacted api key>"
class ApiKey:          # __slots__ = ("_value",)
    def __init__(self, value: str)   # SttKeyMissingError for non-str, blank, or any char outside 0x21..0x7E
    def reveal(self) -> str          # the only reader; used to build the one header
def load_api_key(*, environ: Mapping[str, str] | None = None, env_file: str | PathLike[str] | None = None) -> ApiKey
def provider_config_ref(model: str) -> ProviderConfigRef    # {provider: sarvam, model, config_ref: env:SARVAM_API_KEY}
```

6.1 `ApiKey` prints the fixed `PLACEHOLDER` everywhere: `str`, `repr` (`ApiKey(<redacted api key>)`), `format` with any spec, f-strings, `%`, `.format`, inside a list, tuple, dict or set; the same text for every key (no prefix, no suffix, no length). It is immutable, has no `__dict__`, and `pickle`, `copy.copy` and `copy.deepcopy` raise `TypeError`; `json.dumps` of it raises `TypeError`. A value that is blank, not text, or holds a space, a control character, a newline, a non-ASCII character or a bidi control is `SttKeyMissingError` and the value is in none of the error's forms (`test_key_wrapper_refuses_an_unusable_value_without_quoting_it`; header injection `key\r\nX-Evil: 1` is refused at load).

6.2 Source: `environ` (default `os.environ`) variable `SARVAM_API_KEY`, surrounding white space (space, tab, CR, LF) removed; or, with `env_file`, that file **only**: the environment is then not consulted and there is **no fallback** (a file without the variable is a missing key even when the environment holds one). The file is read as bytes (amended by batch G, 6.3 of its contract: it is opened read-only and non-blocking and judged by `fstat`; a regular file of at most `ENV_FILE_MAX_BYTES` = 65,536 bytes is read, anything else is refused, and a mode with a group or other bit earns an `EnvFileModeWarning`) and only a line `[ws]` `[export ws]` `SARVAM_API_KEY` `[ws]` `=` value matches; the value is the rest of the line, trailing white space and one pair of matching `"` or `'` removed, `=` and `#` inside it kept (no inline comments); comment lines, blank lines, other variables (`SARVAM_API_KEY_BACKUP`, `XSARVAM_API_KEY`), garbage and bytes that are not UTF-8 on other lines are never decoded or parsed; CRLF files work. Nothing is exported into the process environment (`dict(os.environ)` is equal before and after). If the variable is defined twice, which wins is not pinned (the oracle takes the last; section 11 item 3).

6.3 A missing source, an empty, blank or empty-quoted value, a file without the variable, an empty file, an unreadable file, a directory (amended by batch G: reason `env_file_not_regular`, as for a FIFO or a device such as `/dev/null`; a file over 65,536 bytes is reason `env_file_too_large`, also when the key is its first line; a missing or unreadable file keeps `env_file_unreadable`; a symbolic link to a regular file is read): `SttKeyMissingError` (`type(exc) is`, a `SttConfigurationError` and `ValueError`), with `chain == [exc]` (the `OSError` is not kept), and no line of the file, no other variable's value, no file name and no directory in `str`, `repr`, `args` or `__dict__` (`test_env_file_reads_only_named_variable` x 9 forms, `test_missing_key_refuses_before_any_request` x 9, `test_env_file_errors_quote_no_line_and_no_path`).

*Amended by batch G (its 6.3): a regular file whose mode has any group or other permission bit (`mode & 0o077`) emits exactly one `tracer_evalproviders.secrets.EnvFileModeWarning` (a `UserWarning`) before the content is parsed, with the message `the env file can be read by other users (mode 0o644); run chmod 600 on it` (the mode is the file's; no path, no name, no key). A warning is never a refusal: the key is still returned, or `key_missing` still raised. Reading from `environ` never warns. Tests: `test_audit_f9_*`.*

6.4 The key reaches nothing else (`test_key_never_in_exception_str_repr_or_chain`, `test_key_never_in_logs_at_debug`, `test_key_never_in_call_log_store_or_report`): across 9 failure paths in which the library error text, the request, a response header and the response body all hold the key (connect error, read timeout, bare request error, 403 and 400 whose message and `x-echo` header echo it, 429 and 503 four times, an invalid 200 body, a redirect), no form of the raised error or of its chain holds it, and neither does the call log, `repr(client)` or `client.__dict__`; the root logger, `httpx`, `httpcore`, `httpcore.http11`, `tracer_evalproviders` and `tracer_evalcore` at DEBUG hold it in no record (message, args, `exc_text`, the record dict, the formatted traceback), through a run with a retry, a connect error, a 503, a 403 and with the key loaded from a file and from the environment; every record of a run (`Transcript`, `Turn`, `UsageRecord`, `IngestWarning` and the cost records and summary and the dry-run plan) and every file left in the output directory is free of it; `metadata_flags` of every record is empty. **Not covered here:** the store and the report (batches D and E; section 11 item 14).

6.5 `provider_config_ref("saaras:v4")` dumps to `{"provider": "sarvam", "model": "saaras:v4", "config_ref": "env:SARVAM_API_KEY"}`; it takes the model only (never a file path).

## 7. Mapping to records (P3-09; ADR 0024 rules 12 and 13)

```textmap_responses(plan: ChunkPlan, responses: Sequence[ChunkResponse], *, model: str, context: SttContext) -> SttOutcome
map_usage_records(plan, responses, *, model, context) -> tuple[UsageRecord, ...]
usage_audio_seconds(frames: int, rate: int) -> tuple[str, str]       # (canonical decimal, "exact" | "half_even_18")
ROUNDING_KEY = "tracer.audio_seconds_rounding";  MODEL_BASIS = "requested_not_echoed"
```

`map_responses` is pure (no clock, no network, no I/O): `responses` may come in any order, must hold exactly one `ChunkResponse` per chunk of the plan (`ValueError` for a missing, a duplicated or an out-of-range index) and the outcome lists everything in chunk order. `map_usage_records` takes any subset (the usage of the chunks that succeeded). Records are built from `context`: every `created_at` is `context.created_at`; ids are new typed ids.

7.1 Transcript (`test_stt_transcript_maps_one_turn_per_nonempty_chunk`): `source=our_stt`, `provider=sarvam`, `model` as requested, `provider_call_ref` absent (it took several calls), `declared_language` absent, `language_confidence` absent (the per-chunk probability is not averaged), `privacy=context.privacy`. `metadata`: `tracer.chunk_plan` = `plan.as_metadata()` (format `tracer.chunk_plan/1`), `tracer.empty_chunks` = `{"count": n, "indexes": [...]}` (always present, `{"count": 0, "indexes": []}` when none), `tracer.model_basis` = `"requested_not_echoed"` (the response does not echo the model, rule 6); `metadata_flags` empty. `timeline_origin` = `{kind: artifact, ref: context.audio_artifact_id}` **if and only if** some Segment was made (the schema refuses an origin that no offset uses and refuses offsets without one, `check_transcript`); otherwise absent (finding F-B4). `detected_language` and `detected_language_raw`: section 7.3.

7.2 Turns: one Turn per chunk whose `transcript` is not empty; **empty = `transcript.strip() == ""`** (Unicode white space: `""`, `" "`, `"\n\t \r\n"`, U+00A0, U+3000, U+2003 U+2009 are empty; U+200B, U+FEFF, `"."` and `"0"` are not). `seq` runs 0, 1, 2, ... without gap over the Turns made (not the chunk index); `metadata["tracer.chunk_index"]` is the chunk index; `role=unknown`, `source=our_stt`; `text` the response transcript **verbatim** (no strip, no normalisation, no case change; leading and trailing blanks, NBSP, joiners, bidi controls, NFC-unstable U+0958, astral characters, 20,000 characters, instructions and markup in the text: all kept; `contains_bidi_controls` computed by the schema). An empty chunk makes no Turn, no Segment and no warning, but is counted in `tracer.empty_chunks` and still has its UsageRecord (`test_empty_chunk_makes_no_turn_and_is_counted`); a chunk with an empty transcript and non-empty timestamps makes nothing from the timestamps; when every chunk is empty the Transcript exists with no Turn, and the tag the engine returned for silence is not recorded as a detection (captures `stt_silence_unknown`, `stt_silence_forced_hi`). `check_transcript(transcript, turns)` holds.

7.3 Language (`test_stt_turn_language_is_observed_from_unknown_request`, `test_detected_language_set_only_when_all_chunks_agree`):

* `Turn.language_raw` = the response label verbatim (also when it does not normalise; `None` when the label is null/absent); `Turn.language` = `normalize_language_label(label.strip())` tag, or `None`. Never from `context.configured_language`: a response without a label gives `language=None` although the configured language is `mr`. Warnings (`IngestWarning`, `path="/chunks/<i>/language_code"`, `raw=<label>`, non-empty `message`): the code of the label warning when `normalize_language_label` gives one (`language_label_normalized` for `hi_IN`, `language_label_unparseable` for `xx_??`, `Marathi`, the empty string); `language_label_normalized` when only surrounding white space was trimmed (` mr-IN `); none for a clean tag (`mr-in` is a case-only difference) or a null label.
* `Turn.metadata["tracer.language_probability"]` = the probability as a canonical decimal **string** (`1.0` gives `"1"`, `0.995` `"0.995"`, `0` `"0"`, `0.9950000000000000001` keeps every digit), absent when the response has none (forced-language captures).
* `Transcript.detected_language` is set only when **every non-empty chunk** returned the same tag after normalisation (empty chunks are ignored; a null or unparseable label among them, or one different tag, means not set); `detected_language_raw` is set only when, in addition, the raw labels of those chunks are all identical strings (`mr-IN` and `mr_IN` give the tag but no raw).

7.4 Segments and timing (`test_stt_segments_offset_by_chunk_start`, `test_stt_segments_use_the_floor_rule_of_the_f3_mapper`, `test_stt_segments_offset_is_exact_for_a_chunk_start_that_has_no_decimal_expansion`):

* One Segment per phrase: `granularity=phrase`, `text` verbatim, no confidence, `start_ms` and `end_ms` = `floor((Fraction(chunk.start_frame, rate) + Fraction(phrase_seconds)) * 1000)`, exact rational arithmetic on the chunk's first sample and the decimal seconds of the response, relative to sample 0 of the audio (`1.001` s gives 1001, not 1000; `0.0005` s gives 0; at 22,050 Hz a chunk starting at frame 22,040 plus 0.0005 s gives 1000, not 999). Zero-length phrases keep `start == end`.
* A Turn with at least one Segment: `timing_source=audio_alignment`, `start_ms` = the smallest segment start, `end_ms` = the largest segment end. A Turn without timestamps in its response, or with empty timestamp arrays: `timing_source=none`, `start_ms`, `end_ms` and `segments` absent. Timing is per Turn: a transcript can mix both.
* A phrase that ends **after** its chunk's exact duration (`Fraction(end_seconds) > Fraction(frames, rate)`; equal is kept; decimal text such as `1.0000000000000000001` s on a 1 s chunk and `0.0000417` s on a one-frame chunk at 24 kHz are after it) gives that chunk **no Segment at all** and its Turn `timing_source=none` (all or none per chunk), plus **one** `IngestWarning` per affected chunk: `code="phrase_time_outside_chunk"`, `path="/chunks/<i>/timestamps"`, non-empty `message`, no `raw`. The text, the other chunks and every other time are untouched; no time is clamped or moved (`test_stt_phrase_time_outside_chunk_gives_no_segment_and_a_warning`; finding F-B5 on the rounding of Sarvam's times).

7.5 UsageRecords (`test_usage_record_per_chunk_measured_seconds_exact`, `test_usage_audio_seconds_rounded_half_even_18_digits_and_stated`, `test_usage_raw_never_holds_transcript_text`): one per chunk **including empty chunks**, in chunk order: `run_id=context.run_id`, `domain=evaluation`, `stage=stt`, `provider=sarvam`, `model` as requested, `source=measured`, `raw=None`, `provider_call_ref={system: sarvam, kind: request_id, value: <response request_id>}`, `quantities` exactly `[audio_input_seconds (a canonical decimal string), requests (the integer 1)]` in this order, `occurred_at` = `ChunkResponse.responded_at` (absent when `None`; never the context time or the clock). `metadata`: `tracer.audio_seconds_rounding`, `tracer.model_basis` = `"requested_not_echoed"`, `tracer.chunk_index`. No text of the call anywhere in a UsageRecord (transcript, phrase text, labels).

`usage_audio_seconds(frames, rate)` returns `(text, key)`: `text` is `frames / rate` **rounded half to even to 18 fractional digits** in canonical form (no exponent, no trailing zeros, `"25"`, `"1.5"`, `"0"`); `key` is `"exact"` when the quotient fits in 18 fractional digits unrounded and `"half_even_18"` otherwise; `key == "exact"` exactly when evalcore's `exact_seconds(frames, rate)` is not `None`. Pinned values (checked in the test against independent decimal arithmetic): `(1, 24000)` -> `0.000041666666666667`; `(599999, 24000)` -> `24.999958333333333333`; `(1, 3)` -> `0.333333333333333333`; `(2, 3)` -> `0.666666666666666667`; the half-even ties `(1, 524288)` -> `0.000001907348632812` (19 digits, previous digit 2, stays), `(3, 524288)` -> `0.000005722045898438` (previous digit 7, rounds up), `(5, 524288)` -> `0.000009536743164062`, `(7, 524288)` -> `0.000013351440429688`; exact: `(1, 262144)` -> `0.000003814697265625` (exactly 18 digits), `(36000, 24000)` -> `1.5`, `(345600000, 24000)` -> `14400`, `(0, 24000)` -> `0`. Not through a float. The exact frames and rate stay in `tracer.chunk_plan` of the Transcript.

## 8. Cost (P3-10; ADR 0024 rule 14; ADR 0014)

```textcompute_stt_cost(usages: Sequence[UsageRecord], *, catalog: PricingCatalog | None, created_at: datetime,
                 run_started_at: datetime | None = None) -> SttCost         # SttCost(records, summary)
load_user_catalog(text: str, *, created_at: datetime) -> PricingCatalog     # CatalogRefusedError(reason)
```

8.1 `compute_stt_cost` returns **one `CostRecord` per `UsageRecord`** (domain `evaluation`) and `summarize_costs` of them (a `CostSummary`, `check_cost_summary` holds). Without a catalog (`catalog=None`, `test_cost_not_available_without_usd_catalog`): every record is `not_available`, `na_reason=no_price_for_model`, `usage_record_ids=[that usage's id]`, no amount, basis, snapshot or rounding; the summary has `total=None`, `included=[]`, `completeness=none`, every record excluded with reason `no_price_for_model`. No usage at all: exactly one record with `na_reason=no_usage` and no usage id (with or without a catalog). With a catalog every record is `compute_cost(...)` of the evalschema (a `decimal.Context` of precision 200, `domain="evaluation"`, `run_started_at` passed through): `available`/`computed` with the amount in USD, `pricing_snapshot` naming the catalog id, version, digest and the entries used; `rounding` present only when the amount was rounded; the summary total is the exact sum. With the made-up test price of 0.006 USD per 60 s (and 0 per request), 25 s and 12.5 s chunks cost exactly `0.0025` and `0.00125`, total `0.00375` (`test_cost_computed_with_user_usd_catalog`); a one-frame chunk gives the independently computed half-even amount and a `rounding` of `{scale: 18, mode: half_even, exact: false}` iff it changed it.

8.2 Never a partial or invented amount (`test_cost_never_zero_when_unpriced`, 7 scenarios; each leaves every record `not_available`, no amount, summary total `None`, completeness `none`, no `"amount": "0"` anywhere): a catalog that prices only `audio_input_seconds` gives `unit_unpriced` with `unpriced_units == ["requests"]` (finding F-B6); an entry whose `billing` is `"unknown"` gives `billing_rules_missing`; another model (`saaras:v3`, `saaras:v4-extra`, `other`) gives `no_price_for_model`; a usage with no `occurred_at` and no `run_started_at` gives `ambiguous_price` (not the local clock), and with `run_started_at` it prices; a price not yet effective gives `no_price_for_model`; entries are chosen by the usage's date (a price that changes on 2026-06-01 prices an October usage with the new entry). A price of `"0"` the user wrote is the user's statement and gives an available `0` amount (`test_cost_a_price_of_zero_is_only_ever_the_users_statement`).

8.3 INR is never converted (`test_inr_price_never_converted`): an entry whose price currency is not USD is refused (`catalog_entry_invalid`, the message is the code only: no amount, no currency); no entry point (`compute_stt_cost`, `load_user_catalog`, `build_dry_run_plan`) has a parameter named `fx`, `rate`, `inr`, `currency`, `usd_per_inr`, `exchange_rate` or `fx_rate`; no string constant (docstrings excluded) of the package holds `INR`, `0.49` or `rupee`, and no name `fx`, `inr` or `usd_per_inr`. A user who converts by hand writes the USD number and says so in `provenance.note` (the schema's rule); Tracer takes the number as written.

8.4 The user's price file (`test_user_catalog_file_is_refused_with_a_short_code`, 21 cases; `..._reads_published_at_...`, 3; `..._is_sealed_into_a_valid_catalog`): `{"published_at": <RFC 3339 instant>, "entries": [<PriceEntry documents>]}` and nothing else (no `id`, no `version`, no `content_digest`: a person cannot compute a digest). `load_user_catalog` validates strictly (`loads_strict`) and returns a sealed `PricingCatalog` (new `pcat_` id, `version=1`, `created_at` as given, plain content digest, `PricingCatalog.model_validate(cat.model_dump(mode="json")) == cat`, so the digest verifies); `published_at` is read as the schema reads an instant (`...Z`, six digits or none, or an offset). Refusals raise `CatalogRefusedError(reason)` (a `ValueError`, `str(exc) == reason`, no cause, no context) with: `catalog_not_json` (not JSON, empty, `NaN`), `catalog_shape` (not an object, a member other than `published_at` and `entries`, `entries` absent, empty or not a list), `catalog_entry_invalid` (an entry that is not a valid `PriceEntry`: INR or EUR price, negative price, no or blank `provenance.note`, an unknown unit, a float amount, not an object), `catalog_entry_not_user_supplied` (`price_source` other than `user_supplied`), `catalog_invalid` (duplicate `entry_key`, overlapping ranges, a bad `published_at`).

## 9. The call log and the dry run (P3-11; ADR 0024 rules 15 and 16)

```textCALL_LOG_NAME = "sarvam_call_log.jsonl"
class CallLog:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] = <utc now>) -> None
    @classmethod
    def open(cls, out_dir, *, clock=...) -> CallLog        # creates out_dir (parents; the leaf 0700), the file <out_dir>/sarvam_call_log.jsonl
    path: Path                                              # property
    def before(self, *, chunk_index: int, attempt: int, bytes_sent: int, audio_seconds: str) -> None
    def after(self, *, chunk_index, attempt, bytes_sent, audio_seconds, status: int | None,
              error_kind: str | None, elapsed_ms: int, request_id: str | None) -> None
    def close(self) -> None
def safe_request_id(value: object) -> str | None    # value if it is a str matching ^[A-Za-z0-9_.:-]{1,128}$, else None
```

9.1 Format: one JSON object per line, ASCII, `,`/`:` separators, newline-terminated, **exactly these 10 members** in both kinds of line (`test_call_log_has_no_text_no_key`): `at` (UTC instant `YYYY-MM-DDTHH:MM:SS.ffffffZ` from the injected `clock`), `phase` (`before` or `after`), `chunk` (int), `attempt` (int, from 1), `bytes_sent` (int: the length of the request body that the transport saw, the multipart bytes), `audio_seconds` (the decimal string of `usage_audio_seconds`), `status` (HTTP status or `null`), `error_kind` (the transport kind or `null`), `elapsed_ms` (`int`, or `null` in a `before` line), `request_id` (a `safe_request_id` or `null`). A `before` line has `null` for `status`, `error_kind`, `elapsed_ms` and `request_id`. No transcript, no error message, no header, no key, no file name, no path (a request id with a space or a newline, a hostile `error.message`, a timeout text holding the key: none of it appears). The request id of a success is the body's `request_id`, of an error `error.request_id` or `x-request-id`, when safe.

9.2 When: for every attempt a `before` line is written and **flushed to disk before the request is sent** (the test reads the file from inside the transport and sees it), and an `after` line when the response is read or the error caught; `elapsed_ms = floor((after - before) * 1000)` from the injected `monotonic`, which is read **exactly twice per attempt**, immediately before the send and immediately after the body is read or the error caught (a clock that adds 0.25 s per call gives 250); `at` is read once per line from `clock`. A crash in the middle of a request (a `BaseException` from the transport) leaves the `before` line and no `after` line (`test_call_log_written_before_and_after_each_send`, `test_call_log_a_crash_mid_request_leaves_the_before_line`).

9.3 File: `close()` is idempotent; created with mode `0o600` whatever the umask (the test sets umask 0), and an existing file with a looser mode is tightened to `0o600` when opened; opened for append (an existing log is appended to, never truncated; `test_call_log_is_private_and_append_only`); `CallLog.open` creates missing parent directories. POSIX only (the permission test uses `st_mode`).

*Amended by batch G (its 6.2): the missing leaf directory is created with mode `0o700` whatever the umask (parents with the default mode); an existing directory keeps its mode. The log is opened with `O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW`: a symbolic link in place of `sarvam_call_log.jsonl`, also a dangling one, is refused with an `OSError` by `CallLog.open` and by the constructor `CallLog(path)`, and the target is neither written, created nor chmodded. Tests: `test_audit_f7_*`.*

9.4 The dry run (`test_dry_run_sends_nothing_and_prints_plan`):

```textbuild_dry_run_plan(plan: ChunkPlan, *, model: str, catalog: PricingCatalog | None = None,
                   priced_at: datetime | None = None) -> DryRunPlan
@dataclass(frozen=True)
class DryRunCost:  status: str ("estimated" | "not_available"); amount: str | None; na_reason: str | None
@dataclass(frozen=True)
class DryRunPlan:  chunks: int; calls: int; total_frames: int; sample_rate_hz: int; audio_seconds: Decimal
                   audio_seconds_rounding: str ("exact" | "half_even_18"); max_chunk_ms: int; search_ms: int
                   cost: DryRunCost | None;  def render(self) -> str
```

`calls == chunks` (one request per chunk; retries are not counted); `audio_seconds` is `usage_audio_seconds(total_frames, rate)` as a `Decimal`. The signature holds exactly these four parameters: no key, no transport, no path, no URL. It reads no key (`load_api_key` is patched to fail), opens no log (`CallLog.open` and `__init__` patched to fail), sends nothing (`httpx.Client.send`, `request`, `HTTPTransport.handle_request` and `MockTransport.handle_request` patched to fail), writes no file (the working directory is empty afterwards) and, with `SARVAM_API_KEY` unset, works. `render()` is exactly four lines:

```
chunks: 3
audio seconds: 2.5
calls: 3
cost: not available (no USD price supplied)
```

When the total has no finite expansion the second line is `audio seconds: 254.600041666666666667 (rounded half-even to 18 digits)`. With a user catalog and `priced_at` the cost line is `cost: 0.00375 USD (computed from the supplied USD price)` (`cost.status="estimated"`, `amount="0.00375"`); the estimate is `compute_stt_cost` over hypothetical per-chunk usage (no `occurred_at`, price date `priced_at`); without `priced_at` it is `cost: not available (ambiguous_price)`; another model `no_price_for_model`; a catalog that prices only audio `unit_unpriced`; an estimate is printed only when every chunk is priced (completeness `complete`). Zero chunks print `chunks: 0`, `audio seconds: 0`, `calls: 0`.

## 10. ADR 0024 machine check (P3-00b)

`test_adr0024_rules_are_tested` (file `test_evalproviders_adr0024.py`) reads `docs/adr/0024-provider-adapters-byok-sarvam-rest.md` in the format of `test_adr0021_*` and `test_adr0022_*`: the 17 numbered rules of "## Decision" (numbered 1 to 17, no gap) and the invariants I1 to I9 each cite a test in backticks, and at least one cited test exists in a `packages/*/tests` directory; a cited name that exists nowhere must be on `PENDING`. **`PENDING` is exactly the three names that SCOPE section A gives to later batches and that no test of batches A and B defines:** `test_stt_failure_is_missing_input_with_warning_and_nonzero_exit` (P3-20, batch D), `test_cli_fake_stt_runs_offline` (P3-22, batch E), `test_guard_rejects_staged_report_store_and_call_log` (P3-34, batch F). The test also checks that `PENDING` is a subset of the names that section A of `docs/phase3/SCOPE.md` names, that the ADR still cites each, and that no name in `PENDING` exists in a test directory (a landed test must leave the set: a tightening edit by the batch that adds it, so the set is empty by the gate P3-G). `test_every_test_named_by_the_scope_rows_of_batch_b_exists` (6 cases) and `test_the_scope_rows_of_batch_b_name_the_expected_counts` (5, 7, 5, 9, 4, 3 names) pin the SCOPE rows. All 33 names of the batch B rows exist under their exact names; `test_unit_tests_open_no_socket` (SCOPE P3-27) exists for the evalproviders directory.

## 11. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

1. **Where the engine boundary lives and what the engine returns.** ADR 0024 rule 1 says only that evalcore defines the protocol. Recommendation (pinned): `tracer_evalcore.stt` holds the protocol, `SttContext`, `SttOutcome` and the whole error taxonomy; the engine returns the records (`SttOutcome`), mapped by `sarvam.mapping`, so the pipeline needs no provider knowledge and `--stt fake:<dir>` can replay recorded bodies through the same mapping (`ChunkResponse` objects parsed from files). Rejected: a protocol that returns raw per-chunk responses (the pipeline would map provider shapes); the taxonomy in evalproviders (evalcore could not catch the failures without importing the network package).
2. **`SttContext.created_at`.** Every record needs a `created_at`; the batch must not read a clock. Recommendation (pinned): the pipeline passes one instant that stamps every record the engine builds. Rejected: the engine reading the clock (non-deterministic records, ADR 0014 Decision 8's spirit).
3. **Key source precedence and duplicates.** Recommendation (pinned): with `--env-file` the file is the only source and there is no fallback to the environment (explicit beats ambient; a typo in the file does not silently use another account's key). Not pinned: two definitions of the variable in one file (the oracle takes the last, as shell sourcing does); a recommendation is to refuse a file with two definitions (`key_ambiguous`), which would be a new test.
4. **`language_code` absent, `language_probability` null.** ADR rule 8 says `language_code` is "a string or null" and `language_probability` "absent or a number". Recommendation (pinned): an absent `language_code` is read as null (the docs say it may be null; refusing would drop a paid response), a null `language_probability` is invalid (the rule lists absent or a number only; loosen it only with an ADR edit if the real API sends null).
5. **Retry-After on a 503 above the bound.** The ADR table gives `SttRateLimited` for "a Retry-After above the bound" without saying which status. Recommendation (pinned): any 429 or 503 with a value above 30 s is `SttRateLimited` (status 503 kept in `status`), `attempts` the attempts made.
6. **Which failure stops the run.** ADR 0024 rule 10 says auth errors stop before the next chunk, rule 11 says any failed chunk makes the transcript fail. Recommendation (pinned): the first chunk that fails after its attempts, whatever the class, ends the run (all or nothing makes later spend useless).
7. **Several phrases, one outside the chunk.** ADR rule 12 says "a phrase that ends after its chunk's exact duration is not mapped to a Segment for that chunk (its Turn gets timing none)". Recommendation (pinned): all or none per chunk (a Turn span built from the phrases that remain would be wrong), one warning per chunk. Rejected: dropping only the late phrase.
8. **`timeline_origin` and `tracer.empty_chunks`.** The schema refuses an origin without an offset and offsets without an origin. Recommendation (pinned): the origin is set if and only if a Segment exists; `tracer.empty_chunks` is always present. Metadata key names are this contract's choice (`tracer.chunk_index`, `tracer.language_probability`, `tracer.empty_chunks`, `tracer.model_basis` = `requested_not_echoed`).
9. **An error response with a body over the cap.** Rule 8 speaks of 200 bodies. Recommendation (pinned): the status classifies; the body is not read past the cap; `code` is `unrecognised`.
10. **`detected_language_raw`.** Recommendation (pinned): set only when all raw labels of the non-empty chunks are identical.
11. **Turn timing.** `Turn.start_ms` and `end_ms` are the smallest start and largest end of its Segments (the schema wants them whenever the Turn is aligned; the ADR says only "timing audio_alignment from those Segments").
12. **Cost: the `requests` unit.** See finding F-B6. Recommendation (pinned): keep the ADR 13 quantities; a user catalog must price `requests` too (a `"0"` price the user states, with a note, is legitimate); an audio-only catalog is `unit_unpriced` and says which unit. Rejected: dropping the `requests` quantity (an ADR change for a convenience); stripping the unit inside `compute_stt_cost` (it would invent that requests are free).
13. **The user price file.** Recommendation (pinned): a document without `id`, `version`, `content_digest` that Tracer seals (section 8.4); every entry `user_supplied`. The CLI flag name for it and for the dry-run price date (`priced_at`) belong to the P3-22 contract.
14. **Store and report channels of `test_key_never_in_call_log_store_or_report`.** The store and the report do not exist yet. Recommendation: batches D and E add `test_key_never_in_store_and_report_outputs` (a new name for the SCOPE rows of P3-21 and P3-23); the staged test covers the call log, every record, every file in the output directory, the errors and the logs.
15. **Call-log file name and members** (`sarvam_call_log.jsonl`, the 10 members): this contract's choice; ADR 0024 rule 15 lists the facts, not the names.
16. **TLS verification** cannot be exercised through a `MockTransport`. Pinned by AST only (no `verify`, `cert` or `base_url` keyword, no `ssl` import; the import whitelist of batch A). The first real send (P3-30) is the real check.
17. **Python 3.11 to 3.14 and POSIX.** The call-log mode test and the guard patches assume POSIX; `asyncio.BaseEventLoop` is patched by name.

## 12. Test inventory

All files are in `docs/phase3/evidence/red/batch-b/staging/tests/`; counts are collected cases (a parametrized test keeps its name; pytest appends the case). 463 new collected tests plus the existing scaffold test (464 in the directory).

| File | Collected | SCOPE and ADR names it holds |
|---|---|---|
| `test_sarvam_models.py` | 85 | P3-06: `test_sarvam_request_sends_unknown_language_and_explicit_model` (3), `test_sarvam_request_never_sends_forced_code_in_base_case`, `test_sarvam_response_models_parse_all_captures`, `test_sarvam_configured_language_other_than_mr_refused` (17), `test_sarvam_response_rejects_wrong_types_and_oversize_body` (adversarial, 51) |
| `test_sarvam_client.py` | 139 | P3-07: `test_sarvam_retries_429_and_503_then_succeeds` (8), `test_sarvam_gives_up_after_bounded_retries` (6), `test_sarvam_does_not_retry_400_403` (9), `test_sarvam_error_taxonomy_matches_captures`, `test_sarvam_honours_bounded_retry_after` (15), `test_sarvam_base_url_not_overridable` |
| `test_sarvam_engine.py` | 38 | P3-07: `test_sarvam_partial_chunk_failure_yields_no_transcript` (24) |
| `test_sarvam_secrets.py` | 43 | P3-08: `test_key_never_in_exception_str_repr_or_chain`, `test_key_never_in_logs_at_debug`, `test_key_never_in_call_log_store_or_report`, `test_env_file_reads_only_named_variable` (9), `test_missing_key_refuses_before_any_request` (9) |
| `test_sarvam_mapping.py` | 91 | P3-09: `test_stt_transcript_maps_one_turn_per_nonempty_chunk`, `test_empty_chunk_makes_no_turn_and_is_counted` (6), `test_stt_turn_language_is_observed_from_unknown_request` (10), `test_stt_segments_offset_by_chunk_start`, `test_usage_record_per_chunk_measured_seconds_exact` (6), `test_usage_raw_never_holds_transcript_text`, `test_detected_language_set_only_when_all_chunks_agree` (9), `test_stt_phrase_time_outside_chunk_gives_no_segment_and_a_warning`, `test_usage_audio_seconds_rounded_half_even_18_digits_and_stated` (16) |
| `test_sarvam_cost.py` | 39 | P3-10: `test_cost_not_available_without_usd_catalog`, `test_cost_computed_with_user_usd_catalog`, `test_cost_never_zero_when_unpriced`, `test_inr_price_never_converted` |
| `test_sarvam_calllog.py` | 16 | P3-11: `test_call_log_has_no_text_no_key`, `test_dry_run_sends_nothing_and_prints_plan`, `test_call_log_written_before_and_after_each_send` |
| `test_socket_guard.py` | 3 | P3-27: `test_unit_tests_open_no_socket` |
| `test_evalproviders_adr0024.py` | 9 | P3-00b: `test_adr0024_rules_are_tested` |

Added by qa (each makes a contract item testable), beyond the SCOPE names: the retry policy constants, the timeouts, the transport kinds, the content-type, status, body cap, gzip and corrupt-body cases, the `Retry-After` HTTP-date and unparseable forms, the error `code` and `request_id` sanitising, the error class bases, the `Date` header forms, the engine boundary (`SttEngine`, `SttOutcome` invariant, `SttContext`, pickling), native rate and stereo pass-through, sequential sending, a 400-chunk call, the model as requested, the key wrapper, the env-file forms, mapping verbatim text (10 cases), empty-chunk and zero-chunk rules, probability strings, the floor rule, the chunk-start exactness, the dry-run rounding, zero-chunk and cost forms, the user price file (21 refusals, 3 instants, sealing), the call-log mode, append, safe request ids and clocks.

## 13. Findings (read first)

F-B1. **The engine returns records, and the error classes belong to the core.** The pipeline (batch D, in evalcore) must type the engine and catch its failures without importing the network package, so `SttEngine`, `SttContext`, `SttOutcome` and the whole `Stt...` taxonomy are in `tracer_evalcore.stt` (section 2, ambiguity 1). The class names are the ADR's; their spelling is pinned here.

F-B2. **`raise ... from None` does not leave an exception without a context.** ADR 0024 rule 5 says the adapter error is "raised `from None` (no `__cause__` or `__context__` that keeps the httpx request)". Inside an `except` block, `from None` clears `__cause__` and sets `__suppress_context__`, but `__context__` still holds the httpx error, whose text and request can hold the key. The tests pin the stronger fact (`chain(exc) == [exc]`) and the oracle records the facts inside the handler and raises after it. The same holds for the env-file reader (an `OSError`) and the user-catalog loader (a validation error). The first oracle run showed it: the env-file test failed until the raise moved out of the handler.

F-B3. **`tracer_evalschema.loader.loads_strict` raises exceptions that are not `ValueError`.** `DuplicateKeyError` and `DepthLimitExceeded` are `TracerSchemaError`s. A parser guard that catches `ValueError` lets them escape as raw errors (mutant M28 is caught by the duplicate-member and depth cases). The guard must treat any failure of the parser as `not_json`.

F-B4. **`Transcript.timeline_origin` is conditional.** ADR 0024 rule 12 says "`timeline_origin` the audio artifact". `check_transcript` refuses an origin that no turn or segment uses and refuses offsets without one, so the origin is set if and only if at least one Segment is made (section 7.1). A run with no timestamps in any response has no origin; it still has `tracer.chunk_plan` and a `Turn.timing_source` of `none`.

F-B5. **Sarvam rounds its phrase times to two decimals, and the rule is exact.** The captures show `3.33` for a 3.33... s phrase. ADR rule 12 drops the Segments of a chunk when a phrase ends after the chunk's exact duration (a rational number: 25 s windows are exact, the last chunk and any non-ending quotient are not). A last phrase that ends within 5 ms of the end of the audio, rounded up by the provider, is therefore "after" the chunk and the chunk loses its timing, with one warning. The cut falls on the quietest 20 ms frame, so this is rare except at the end of the audio. Recommendation: keep the exact rule (a tolerance is a number nobody measured, `search_ms` is the same kind of number); count the `phrase_time_outside_chunk` warnings in the first real run (P3-30) and decide on that evidence; a BACKLOG row if the count is not zero. No test can show this offline.

F-B6. **The `requests` quantity makes the natural user catalog unusable.** ADR 0024 rule 13 gives each UsageRecord the quantities `audio_input_seconds` and `requests`. `compute_cost` prices every quantity and refuses a partial sum, so a user catalog with an audio price only gives `unit_unpriced` with `unpriced_units == ["requests"]` for every chunk, and no total. A catalog must also price `requests` (a `"0"` with a `provenance.note` that says the provider states no per-request charge is the user's statement, not Tracer's). Sarvam states no per-request charge or minimum (F8), so Tracer cannot write that entry for the user. The CLI help and the report must say so (P3-22, P3-24); ambiguity 12.

F-B7. **A test can hide a leak from the next test.** In the first mutation run, the mutant that exported the key into `os.environ` survived: the earlier tests of the run had already put the same value there, so the "environment unchanged" test saw no difference. `conftest.py` now removes `SARVAM_API_KEY` from the environment before every test (which also keeps a developer's own key out of every test), and two tests assert that it is absent afterwards.

F-B8. **Overlapping guards.** Of the six patches of the network guard, `asyncio` connection and `socket.create_connection` are reached through `socket.getaddrinfo` and `socket.socket.connect`, which are also patched, so removing either patch changes no behaviour (mutants G04 and G06 are equivalent; they stay as defence in depth). Likewise: a JSON `true` can never be a `Decimal` (M14), a negative `Retry-After` can never win against a positive backoff (C49), an oversize body is already `None` (C62), and every value of a call-log line is ASCII by construction (L14). `mutants.txt` lists each with its reason.

F-B9. **The dry run counts calls, not attempts.** `calls == chunks`; a chunk can cost up to 4 requests (ADR rule 9), and the plan does not say so. Recommendation: the CLI prints "at most 4 attempts per chunk" next to the count (P3-22); the plan object stays as pinned.

F-B10. **Cannot be tested offline** (owner-gated, P3-30): Sarvam's behaviour for audio over 30 s and at 24 kHz, whether it bills a failed attempt, its rate limits and 5xx behaviour, TLS verification (only the absence of a `verify` setting and of the `ssl` import is pinned), the real latency against the 10 s and 60 s timeouts, and the accuracy of the response `Date` as a price date.

## 14. Evidence (`docs/phase3/evidence/red/batch-b/`)

Counts (final, 2026-10-25). Everything ran in scratch copies of the repository under the session scratchpad (own environments per Python via `UV_PROJECT_ENVIRONMENT`; the shared `.venv` was not used and stays on the default Python); the real tree changed only under `docs/phase3/`: no directory under `packages/` was created and no file under `packages/` was edited.

* red, on a copy of the repository without the batch B code (batch A tree plus the staged tests moved to `packages/evalproviders/tests`), `--tb=line` (`red.py3.11.log` to `red.py3.14.log`): **451 failed, 13 passed** on 3.11, 3.12, 3.13 and 3.14 (464 tests: 463 new and the existing scaffold test). `red-reasons.py3.13.txt` groups the 451 failures: 447 `ModuleNotFoundError` (`tracer_evalproviders.calllog` 202, `.sarvam` 161, `.cost` 35, `tracer_evalcore.stt` 28, `.secrets` 18, `.dryrun` 3 and nothing else), 2 `FileNotFoundError` (the tests that read `calllog.py` and `dryrun.py`), 2 assertions that name the missing code (no httpx client in the source; the dry-run module does not import). The 13 that pass are guards (listed in the file): the 9 tests of `test_evalproviders_adr0024.py` (the names exist once the tests are staged; their teeth are mutants A01 to A07), the existing scaffold test, `test_unit_tests_open_no_socket` and the test that no test builds a client without a transport (their teeth are G01 to G06), and `test_sarvam_base_url_not_overridable_source_reads_no_environment_for_it` (vacuous while there is no source; its teeth are mutants C66 to C68).
* oracle: the same tests with a hand-written minimal implementation of the batch (`oracle-src.patch`, about 1,450 lines: `tracer_evalcore/stt.py` and nine modules of `tracer_evalproviders`; the oracle is not lint-clean and not typed to the repository's mypy strictness, only the tests are; the dev owns the real code and may differ): Phase 3 directories (`packages/evalcore/tests packages/evalproviders/tests packages/cli/tests`, `oracle.py3.11.log` to `oracle.py3.14.log`): **628 passed** on 3.11, 3.12, 3.13 and 3.14 (164 earlier Phase 3 tests, 464 here); the whole workspace suite (`oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`): **11,186 passed, 1,287 deselected** on 3.11, 3.13 and 3.14 (the 10,723 of batch A plus the 463 new tests; the scratch copy needs a local `git init` for the Phase 1 tests that call git: without it 6 of them fail, none of them a Phase 3 test).
* mutants (`mutants.txt`): **231 mutants: 225 caught, 6 equivalent (with reasons), 0 survived** on the final tests: 28 in the models, 67 in the client, 7 in the engine, 20 in the key handling, 56 in the mapping, 12 in the cost, 14 in the call log, 8 in the dry run, 6 in the evalcore boundary, 6 in the network guard and 7 in the ADR check. The first run left 15 survivors; each is explained in the header of the file (six equivalent, nine led to a test or to the mutant being changed).
* the staged tests alone (`staged-gates.py3.13.log`): `ruff check`, `ruff format --check` and `mypy --strict` (repository configuration) pass on `packages/evalproviders/tests` as they are; `scripts/check_headers.py` passes; no non-ASCII byte in a staged file or in this contract; the Phase 1 T-DRIFT scan (54 tests) passes on the tree with the staged files and this contract.

Not run: Python 3.15 (non-blocking leg); GitHub-hosted execution; Windows; the suites against Postgres (nothing in this batch touches a database); any real send (owner-gated, P3-30). Gaps and risks for the gate checklist: the store and report channels of the key-leak test (item 14 of section 11); no test of Sarvam's real behaviour (finding F-B10); the exact-duration rule against Sarvam's two-decimal times (F-B5); the call-log mode test is POSIX only; the dependency range `httpx>=0.28.1,<1` is still tested at one version only (batch A, BL-196); `check_licenses` and the guard script were not affected by this batch.
