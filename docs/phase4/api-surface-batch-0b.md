# Phase 4 batch 0b contract: scaffold of tracer-evalsim, licence policy, engine boundaries (P4-01, P4-02, P4-03)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (rows P4-01, P4-02, P4-03, sections B, C, D, the amendments D-a to D-d and E-a/E-b, the O questions), ADRs 0026 (rules 1 to 4, 9, 20, 23, invariants I1 to I2, I7), 0027 (the three document schemas), 0028 (rules 1, 5 and 7), `docs/security/P4-02-licenses.md`, `docs/phase4/evidence/red/batch-0/staging/license-policy.proposed.toml` (applied minus nothing: protobuf is already absent from it), the Phase 3 scaffold tests as models, and the lead's instructions of 2026-10-27. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test except the four named spec changes of section 9. Every statement below is pinned by a test named in section 10, unless it says "not pinned".

## 0. Where the tests are, and how to move them

IMPORTANT (Phase 2 lesson, twice): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` and `uv lock --check` for the whole workspace. So nothing was created under `packages/`. The tests are staged at

```text
docs/phase4/evidence/red/batch-0b/staging/tests/evalcore/   -> packages/evalcore/tests/    (6 files)
docs/phase4/evidence/red/batch-0b/staging/tests/evalsim/    -> packages/evalsim/tests/     (1 file; the dev creates packages/evalsim first)
docs/phase4/evidence/red/batch-0b/staging/tests/cli/        -> packages/cli/tests/         (2 files)
docs/phase4/evidence/red/batch-0b/staging/tests/evaldb/     -> packages/evaldb/tests/      (1 file, a named spec change)
(nothing is staged for evalproviders or evalschema: no test there changes, and tracer-evalschema stays byte-unchanged)
```

Four of the ten staged files REPLACE an existing file (section 9: `evalcore_phase4_adr_support.py`, `test_phase3_scaffold.py`, `test_cli_score.py`, `test_evaldb_ci.py`); the other six are new. No `__init__.py`, no `conftest.py`, no data file. Test module names are unique across all test directories. The tests find the repository root by walking up to `uv.lock`; none depends on its location. All are ASCII, pass `ruff check`, `ruff format --check` and `mypy --strict` at their final path (they were formatted there in the oracle), open no socket and read neither a key nor the private fixtures. The new modules of `tracer-evalcore` are imported lazily (`importlib`), so before they exist each test fails on its own with `ModuleNotFoundError` and the directory still collects.

The dev's first acts, in this order (the oracle patches in section 11 are a working starting point, not a substitute for the dev's own `uv lock` with network):

```text
1. create packages/evalsim (section 1), then:
   mkdir -p packages/evalsim/tests
   git mv docs/phase4/evidence/red/batch-0b/staging/tests/evalsim/*.py   packages/evalsim/tests/
   git mv docs/phase4/evidence/red/batch-0b/staging/tests/evalcore/*.py  packages/evalcore/tests/    (replaces three existing files)
   git mv docs/phase4/evidence/red/batch-0b/staging/tests/cli/*.py       packages/cli/tests/         (replaces test_cli_score.py)
   git mv docs/phase4/evidence/red/batch-0b/staging/tests/evaldb/*.py    packages/evaldb/tests/      (replaces test_evaldb_ci.py)
2. root pyproject.toml, uv lock (with network), docs/security/license-policy.toml, scripts/check_licenses.py,
   .github/workflows/ci.yml, tools/samplebot, docs/phase4/evidence/evalschema-baseline.json (sections 2 to 8)
3. the evalcore modules and the cli subcommand (sections 5 and 7)
```

Commands (after the move and the changes):

```text
everything:        uv run --locked --all-groups pytest
Phase 4 batch 0b:  uv run --locked --all-groups pytest packages/evalsim/tests \
                     packages/evalcore/tests/test_sim_engine_boundary.py packages/evalcore/tests/test_phase4_scaffold.py \
                     packages/evalcore/tests/test_phase4_licenses.py packages/evalcore/tests/test_phase4_ci.py \
                     packages/cli/tests/test_cli_simulate_help.py
ADR readers:       uv run --locked --all-groups pytest packages/evalcore/tests -k "adr0004 or adr0023_phase4 or adr0024_phase4 or adr0026 or adr0027 or adr0028 or adr0029 or phase4"
```

## 1. P4-01: the package `tracer-evalsim`

| | |
|---|---|
| directory | `packages/evalsim` |
| distribution / import name | `tracer-evalsim` / `tracer_evalsim` |
| `[project] dependencies` | `tracer-evalschema==0.0.1`, `tracer-evalcore==0.0.1`, `websockets>=15.0,<18` (nothing else: no protobuf, no httpx, no Pipecat) |
| `[project.optional-dependencies]` | exactly `webrtc = ["aiortc>=1.15.0,<2"]` |
| `[project.scripts]` | none (the command line is `tracer-cli`'s) |
| `requires-python` | `>=3.11` (as the other packages) |

1.1 Layout: `pyproject.toml`, `README.md`, `LICENSE` and `NOTICE` (both byte-identical to `packages/evalschema/LICENSE` and `NOTICE`), `src/tracer_evalsim/{__init__.py, py.typed, errors.py}`, `tests/`. Every `.py` under `src/` starts with the two SPDX header lines. `README.md` is non-empty and contains the words `webrtc` and `FFmpeg` and the path `docs/security/P4-02-licenses.md` (P4-02 review section 7, condition 3: the extra installs a third-party wheel with GPL and LGPL parts). `tracer_evalsim.__version__ == "0.0.1"` and equals `importlib.metadata.version("tracer-evalsim")`. `tracer_evalsim.errors.EvalsimError` is a subclass of `tracer_evalcore.errors.EvalcoreError`. Later tasks add `dialects/`, `runner`, `sender`, `webrtc` and the kill-switch constants; their names are not pinned here.

1.2 `pyproject.toml`: as the other packages: `[build-system] requires = ["hatchling==1.32.4"]`, `build-backend = "hatchling.build"`; `[project]` `name`, `version = "0.0.1"`, `readme = "README.md"`, `license = "Apache-2.0"`, `license-files = ["LICENSE", "NOTICE"]`, the classifiers `Typing :: Typed` and `Programming Language :: Python :: 3.11` to `3.14`; `[tool.hatch.build.targets.wheel] packages = ["src/tracer_evalsim"]`. The oracle's file is in `oracle-src.patch`.

1.3 Ranges (`test_phase4_requires_dist_bounded_ranges`). The declared dependencies, the `[runtime.evalsim].requires_dist` of the policy and the built wheel's `Requires-Dist` are equal as parsed requirements (the wheel metadata reorders `>=15.0,<18` to `<18,>=15.0`, so the tests compare `(canonical name, marker, SpecifierSet)`), the extra with the marker `extra == "webrtc"`; the wheel has `Provides-Extra: webrtc` and nothing else, `Requires-Python: >=3.11`. A workspace package is `==0.0.1`; a third-party requirement has exactly the operators `>=` and `<`, with the upper bound a larger MAJOR than the lower. This differs from Phase 3 (`httpx>=0.28.1,<1`, next major) on purpose: websockets 17 is already released, so `<18` is the first unreleased major (SCOPE D3, lead brief). The word `protobuf`, `pipecat`, `httpx`, `requests`, `aiohttp` or `sarvamai` appears in no declared requirement. The installed `websockets` and `aiortc` satisfy their ranges (the extra is in the dev group, section 2, so it is installed in every test environment and on the lowest-direct leg at its lowest version).

1.4 Wheel (`test_evalsim_wheel_contains_no_tests`; `python -m hatchling build -t wheel`): the top-level entries are only `tracer_evalsim/` and the one `*.dist-info/`; the wheel contains `tracer_evalsim/__init__.py`, `py.typed` and `errors.py`; no member has a path part `tests`, `test`, `fixtures` or `__pycache__`, no basename `test_*`, `conftest.py`, `*.pyc` or containing `baseline`, and no member name contains `samplebot` or `pipecat`; the dist-info carries LICENSE and NOTICE and declares no console script; every `.py` member starts with the SPDX header.

1.5 Imports (`test_evalsim_runtime_imports_whitelist`). By AST (every `import`, `from ... import` and literal `importlib.import_module` / `__import__`, at any depth, a computed dynamic import is an offence): the package may import the standard library, `websockets`, `aiortc`, `tracer_evalschema`, `tracer_evalcore` and itself. In a fresh `python -I` interpreter, importing every module loads, beyond the standard library, only `tracer_evalschema`, `tracer_evalcore`, `tracer_evalsim`, `websockets`, the evalschema closure (`pydantic`, `pydantic_core`, `annotated_types`, `typing_extensions`, `typing_inspection`, `rfc8785`) and the `cython_runtime` marker; none of `httpx`, `httpcore`, `h11`, `anyio`, `requests`, `urllib3`, `aiohttp`, `sarvamai`, `sqlalchemy`, `alembic`, `pg8000`, `psycopg*`, `asyncpg`, `tracer_evalproviders`, `tracer_evaldb`, `tracer_cli`, `pytest`, `jsonschema`, `hypothesis`, `yaml`, `google`, `pipecat`; and NONE of the `[webrtc]` closure (`aiortc`, `av`, `aioice`, `pylibsrtp`, `pyee`, `OpenSSL`, `cryptography`, `cffi`, `google_crc32c`, `ifaddr`, `dns`): an import of `aiortc` is lazy, inside a function (ADR 0026 rule 7: without the extra the command is refused with a message that names the extra, which needs the package to import without it). No other package (`evalschema`, `evaldb`, `evalcore`, `evalproviders`, `cli`) imports `websockets`, `aiortc`, `av`, `aioice`, `pylibsrtp` or `pyee`; no tracer package imports `pipecat` or `google` (protobuf). `websockets.asyncio.client.connect` and `websockets.asyncio.server.serve` exist in the installed version (the API the dialects and the fake agents use; on the lowest-direct leg this is the lowest version).

1.6 Pure core (`test_evalcore_imports_no_asyncio_network`, in `test_phase4_scaffold.py`; `test_evalcore_imports_no_network_module` of Phase 3 stays and stays unchanged, it already refuses `import asyncio` altogether). The new test adds the API level of ADR 0026 rule 2: by AST, no use of the asyncio network API through any alias (`open_connection`, `start_server`, `create_connection`, `create_server`, `create_datagram_endpoint`, `sock_*`, `start_tls`, `getaddrinfo`, `create_subprocess_*`, `StreamReader`, `StreamWriter`, the selector and proactor loops, as an attribute, a name, an import from `asyncio` or from an `asyncio.` submodule, or a string such as a `getattr` argument or `import_module("asyncio.streams")`), and no import of `selectors`, `select`, `_socket`, `_ssl`, `_asyncio`, `uvloop`, `trio`, `anyio`, `curio`, `gevent`, `eventlet`, `twisted`, `tornado`, `websockets`, `aiortc`, `av`, `wsproto`, `h2`; the scan must have read `simtypes.py`, `tts.py`, `llm.py` and `brain.py` (it is red until P4-03 lands); importing `tracer_evalcore` on top of `tracer_evalschema` adds no `asyncio*`, `selectors`, `select`, `socket`, `_socket`, `ssl` or `_ssl` module; and the scan function itself flags 20 violating snippets and passes 6 ordinary ones (teeth).

## 2. Root `pyproject.toml`, `uv.lock`

2.1 `[dependency-groups] dev` gains `"tracer-evalsim[webrtc]"` (with a comment: the extra is installed so that its closure is scanned and tested; the precedent is `tracer-evaldb[postgres]`). `[tool.uv.sources]` gains `tracer-evalsim = { workspace = true }`. `[tool.ruff] src`, `[tool.mypy] files` gain `packages/evalsim/src` and `packages/evalsim/tests`; `mypy_path` gains `packages/evalsim/src`; `[tool.pytest.ini_options] testpaths` becomes the six directories in the order `evalschema, evaldb, evalcore, evalproviders, cli, evalsim` (named spec change, section 9). `[tool.uv] no-build-package` gains `av`, `google-crc32c`, `pylibsrtp` (the compiled packages of the extra; the review notes that wheels exist for 3.11 to 3.15 on the platforms read, so a missing wheel must fail loudly).

2.2 `uv.lock` is regenerated by the dev with network (`uv lock`). The oracle's offline resolution (cache only) gave 75 packages and the versions the review read: websockets 17.3, aiortc 1.15.0, av 17.1.0, aioice 0.10.2, dnspython 2.9.0, google-crc32c 1.9.0, ifaddr 0.2.0, pyee 13.0.1 (Python below 3.12) and 16.0.0 (3.12 and above), pylibsrtp 1.0.0, pyopenssl 26.4.0 (`oracle-uv.lock.patch`). The tests pin the closure by NAME (section 4), not by version, except `pyopenssl` (override pinned to the locked version) and the two `[[bundled_notices]]` entries (`av`, `pylibsrtp`); a re-resolution that moves one of these re-triggers the review, as ADR 0024 and the policy header say.

2.3 The lock holds `tracer-evalsim` with `source = { editable = "packages/evalsim" }`, `provides-extras = ["webrtc"]` and `optional-dependencies.webrtc = [aiortc]`. Closure of the base: the six evalschema packages, `tracer-evalschema`, `tracer-evalcore`, `websockets`. Closure added by the extra: `aioice, aiortc, av, cffi, cryptography, dnspython, google-crc32c, ifaddr, pycparser, pyee, pylibsrtp, pyopenssl`. None of `pipecat-ai, protobuf, num2words, soxr, soundfile, onnxruntime, numba, llvmlite, sentencex, loudness, sarvamai, opencv-python-headless, tracer-samplebot` is in `uv.lock`. `tracer-evalcore`, `tracer-evalproviders`, `tracer-cli` and `tracer-evaldb` do not reach `websockets` or `tracer-evalsim` (`test_phase4_locked_closures_are_the_reviewed_ones`).

## 3. `tools/samplebot` (test_samplebot_not_a_workspace_member_and_not_in_uv_lock)

3.1 `tools/samplebot/pyproject.toml`: `[project] name = "tracer-samplebot"`, `version`, `readme = "README.md"`, `requires-python` starting `>=3.11`, `license = "Apache-2.0"`, `dependencies` holding exactly one Pipecat requirement, pinned `==1.14.0` and nothing else in its specifier (extras allowed: the oracle uses `pipecat-ai[websocket,runner,webrtc]==1.14.0`, the extras the review listed), and no `tracer-*` dependency; `[tool.uv] package = false`; no `[tool.uv.workspace]`, no `[tool.uv.sources]`. `README.md` non-empty; for the licence test it must contain `LGPL` and the path `docs/security/P4-02-licenses.md`.

3.2 Isolation, how it is asserted: no member pattern of the root `[tool.uv.workspace].members` matches it (the patterns are globbed and compared by resolved path); the root `dependency-groups`, `tool.uv.sources` and `members` do not contain the word `samplebot`; the root `uv.lock` holds neither `pipecat-ai` nor `tracer-samplebot` and the lock text contains neither `samplebot` nor `pipecat`; `pipecat-ai` and `tracer-samplebot` are not installed in the test environment; and `[tools.samplebot].pin` of the policy equals the project's pin. Its own lock: not required at P4-01 (the lock arrives with the bots, P4-26, and needs network for Pipecat's closure; see section 12, ambiguity 4). The tests tolerate its absence and, when `tools/samplebot/uv.lock` exists, pin it: Pipecat 1.14.0, no tracer package but the project itself (`source = { virtual = "." }`), and the LGPL tool-only packages at the reviewed versions (`num2words 0.5.14`, `soxr 1.0.0`, `soundfile 0.13.1`).

## 4. P4-02: licence policy and checker

4.1 `docs/security/license-policy.toml` gets the proposed text of `docs/phase4/evidence/red/batch-0/staging/license-policy.proposed.toml`, which already has no protobuf entry (E-b), with the stale wording edited (the oracle's result is in `oracle-src.patch`): `[runtime.evalsim]` (`closure` of 7 sorted names: the six evalschema packages and `websockets`; `requires_dist`: `tracer-evalschema==0.0.1`, `tracer-evalcore==0.0.1`, `websockets>=15.0,<18`, `aiortc>=1.15.0,<2; extra == 'webrtc'`); `[runtime.evalsim.extras_webrtc]` (key `closure` only, the 12 sorted names above); `[[rejected]] pipecat-ai` (spdx `BSD-2-Clause`; the reason names `num2words`, `soxr`, `LGPL`, `tools/samplebot`, `uv.lock` and `1.14.0`); `[[overrides]] pyopenssl` (version equal to the locked one, spdx `Apache-2.0`, scope `runtime`, `licenses/LICENSE`, sha256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`, which the test recomputes from the installed file); `[[bundled_notices]]` for `av` and `pylibsrtp` (versions equal to the locked ones, `recheck` non-empty); `[tools.samplebot]` (`pin = "pipecat-ai==1.14.0"`, `reviewed` a date, `copyleft_tool_only` with exactly the five entries `num2words`, `soxr`, `soundfile`, `opencv-python-headless`, `certifi` in the form `<name> <version> (<note>)` -- `soxr` at `1.0.0` with `LGPL-2.1-or-later` in the note (D-d), `num2words` with `LGPL`, `soundfile` with `libsndfile` --, and a non-empty `not_fully_reviewed`). Stale wording that must go: `PROPOSED`, `NOTHING IS APPLIED`, `ignored by the current loader` anywhere in the file; the `av` status must start with `named exception`, must not contain `proposed` or `lead to log`, and must cite `docs/security/P4-02-licenses.md` (the oracle writes: `named exception for the optional extra [webrtc] (lead decision 2026-10-27 under the owner rule 'do not dwell on copyleft', owner question O6); conditions in docs/security/P4-02-licenses.md section 7`). The `bundled` text of `av` keeps the words `GPL`, `FFmpeg` and `libx264`. The word `protobuf` appears nowhere in the parsed policy data.

4.2 The exceptions do not change: `{hypothesis, pathspec, certifi}` (`test_license_exceptions_exact`, unchanged). `av` cannot be an `[[exceptions]]` entry: the checker consults exceptions only for packages whose metadata is not allowed, and `av` declares BSD-3-Clause; the named exception for the bundled FFmpeg (owner question O6, decided by the lead) is therefore recorded in `[[bundled_notices]]` and in the review, and the lead logs it in the decision log. `test_no_lgpl_or_gpl_in_lock` (existing, unchanged) stays green: no package of the closures declares a GPL-family licence and `uv.lock` holds no `gpl` text.

4.3 `scripts/check_licenses.py`, three changes (the oracle's diff is in `oracle-src.patch`; all are behaviour-tested by `test_check_licenses_covers_evalsim_and_its_extra` and `test_check_licenses_accepts_one_name_locked_at_two_versions`):

* `PHASE3_PROJECTS` gains `"tracer-evalsim": "evalsim"` (base closure and bounded ranges are then frozen as for the other three).
* An extras table: a constant `PHASE4_EXTRAS = {"tracer-evalsim": "webrtc"}` (its name and value are pinned); the loader reads `runtime.evalsim.extras_webrtc.closure`; the project check compares `lock.closure([project], [extra]) - workspace - base` with it (message `webrtc extra closure changed: extra [...], missing [...]`); a requirement of the project that carries that extra (a lock row with an `extra` key, which `test_check_licenses_allows_only_the_declared_extra_of_evalsim` feeds the checker; uv 0.12 writes the extra into `marker` only, so the branch is dead on today's lock) is not an "extra dependency" failure but must be a bounded range, and a row of any other extra still fails; `check_artifact` expects `Provides-Extra == ["webrtc"]` for the `tracer-evalsim` wheel (and still `[]` for the Phase 3 wheels, `["postgres"]` for evaldb).
* A name locked at TWO versions (found by the oracle on Python 3.11: `pyee` 13.0.1 below Python 3.12, 16.0.0 from 3.12). `Lock.load` keeps every entry of a name (`Lock.versions`), `evaluate_dists` accepts the installed version when it is any locked version of the name (and fails with all locked versions in the note when it is none), the stale override and exception check looks at every locked version, and the closure follows the union of the dependencies of the entries. Without this the script fails on Python 3.11 (`installed version differs from lock (16.0.0)`), so `test_check_licenses_covers_phase3_packages` and the new test would fail on the 3.11 leg of CI, which runs them with `sys.executable`.

4.4 Every licence is read from a file: in the installed distributions of the closures (the extra included) a licence-like file exists in the dist-info for every package except `dnspython` (finding L-3 of the review: none in the wheel); the metadata of none declares a GPL-family licence. The review file `docs/security/P4-02-licenses.md` is read, not changed: it must keep the heading `## 8. tools/samplebot closure (TOOL-ONLY)` with the names `num2words, soxr, soundfile, onnxruntime, numba, sentencex, loudness, opencv-python-headless`, and the phrase `outside every tracer wheel` (`test_samplebot_closure_reviewed_and_tool_only`).

## 5. P4-03: engine boundaries (`tracer-evalcore`, pure: stdlib and evalschema only)

New modules, each with the two SPDX lines and a sorted `__all__`: `simtypes.py`, `tts.py`, `llm.py`, `brain.py`, `_engine_errors.py`. All names below are pinned.

5.1 `tracer_evalcore.simtypes` (pure data; the lists equal the ADR 0027 schema enums and the ADR 0026 rule 9 text, `test_sim_constants_are_the_ones_the_adrs_state`):

```text
DIALECTS      = ("pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1")
CALL_STATES   = ("preflight", "connecting", "connected", "caller_speaking", "awaiting_agent",
                 "agent_speaking", "ending", "ended")                                   # 8, in ADR order
END_REASONS   = ("script_complete", "caller_end_call", "agent_hangup", "max_duration", "max_turns",
                 "max_silence", "kill_switch", "setup_failed", "transport_error",
                 "brain_invalid_output", "spend_cap")                                    # 11, in ADR order
CALLER_MODES  = ("scripted", "llm", "replay")
CAP_NAMES     = ("call_seconds", "tts_chars", "llm_tokens", "stt_seconds")
END_SENTINEL  = "END_CALL"
FRAME_MS      = 20
MAX_LINE_CHARS = 2500
```

5.2 `tracer_evalcore._engine_errors.FactError(EvalcoreError)` (private base of both families). Constructor: keyword-only, all optional, no positional argument, no other keyword: `index: int|None`, `status: int|None`, `kind: str|None`, `code: str|None`, `request_id: str|None`, `attempts: int|None`, `reason: str|None`. A value that is not a machine fact raises `ValueError` whose message names the fact and never quotes the value: `index` an `int` (not `bool`) at least 0; `status` an `int` 100 to 599; `attempts` an `int` at least 1; `kind`, `code`, `reason` a `str` matching `[a-z][a-z0-9_]{0,63}`; `request_id` a `str` matching `[A-Za-z0-9_.:-]{1,128}`. Attributes are exactly those seven (`vars(error)` has no other key). The message is `"<ClassName>: " + ", ".join(parts)` with the parts `request <index>`, `HTTP <status>`, `transport <kind>`, `code <code>`, `reason <reason>`, `request id <id>`, `<n> attempt(s)` (the oracle's order; the tests require the class name first, ASCII, under 300 characters, every fact value present, `args == (message,)`, `repr == "<Name>('<message>')"`). `__reduce__` rebuilds the class from its facts, so `copy`, `deepcopy` and `pickle` keep type and facts. An error built inside an `except` block has `__cause__` and `__context__` None; raised afterwards outside any handler its chain is itself only (finding F-B2 of Phase 3; the adapters of P4-08 and P4-09 raise outside the handler).

5.3 `tracer_evalcore.tts`:

```text
TtsError(FactError)  <- TtsAuthError, TtsRequestRejected, TtsRateLimited, TtsServerError,
                        TtsTransportError, TtsResponseInvalid, TtsCapReachedError
TtsConfigurationError(TtsError, ValueError) <- TtsKeyMissingError, TtsLanguageNotSupportedError, TtsTextTooLongError
TTS_CODECS = ("linear16", "mulaw")
@dataclass(frozen=True) TtsVoice(provider, model, speaker, language_code, pace)   # all str, no default
    provider/model/speaker match [A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}; language_code [a-z]{2,3}-[A-Z]{2}
    (any language: the ENGINE refuses non-mr with TtsLanguageNotSupportedError, BL-162);
    pace a canonical decimal STRING matching (?:0|[1-9][0-9]{0,2})(?:\.[0-9]{0,5}[1-9])?  (never a float)
@dataclass(frozen=True) TtsRequest(index, text, voice, sample_rate_hz, codec)
    index int >= 0; text non-empty str (no length cap in the core: the 2,500 limit is the engine's);
    voice a TtsVoice (else TypeError); sample_rate_hz int 8000..48000; codec in TTS_CODECS
@dataclass(frozen=True) TtsContext(run_id: RunId, created_at: datetime)
@dataclass(frozen=True) TtsOutcome(audio: bytes|None, usage: tuple[UsageRecord, ...], failure: TtsError|None)
    exactly one of audio / failure; audio non-empty bytes; every usage item a UsageRecord with
    stage "sim_caller_tts" and domain "evaluation" (a cache hit: audio set, usage ())
UsageSink = tracer_evalcore.stt.UsageSink   (the same object, re-exported in __all__)
class TtsEngine(Protocol), runtime_checkable:  def synthesise(self, request: TtsRequest, context: TtsContext) -> TtsOutcome
class UsageStreamingTtsEngine(Protocol), runtime_checkable:  streams_usage: bool
        def synthesise(self, request, context, *, on_usage: UsageSink | None = None) -> TtsOutcome
def synthesise_all(engine, requests: Sequence[TtsRequest], context, *, on_usage: UsageSink) -> tuple[TtsOutcome, ...]
```

`synthesise_all` is the P4-21 step 7 driver and the R1 lesson in code: for each request in order it calls the engine; an engine that is a `UsageStreamingTtsEngine` with `streams_usage is True` gets `on_usage=` and delivers each record itself (the driver does not deliver again); any other engine is called with two arguments and the driver passes each record of the outcome to `on_usage` before building the next request (an engine with the keyword but `streams_usage` False is called WITHOUT the keyword); the run stops after the first outcome with a failure (that outcome is the last returned; its usage is delivered first); a sink that raises, or a `BaseException` from the engine, propagates at once, after the earlier usage was delivered and before the next request; no request returns `()`.

5.4 `tracer_evalcore.llm`:

```text
LlmError(FactError)  <- LlmAuthError, LlmRequestRejected, LlmRateLimited, LlmServerError,
                        LlmTransportError, LlmResponseInvalid, LlmCapReachedError
LlmConfigurationError(LlmError, ValueError) <- LlmKeyMissingError
LLM_ROLES = ("system", "user", "assistant")
@dataclass(frozen=True) LlmMessage(role, content)           # role in LLM_ROLES; content non-empty str
@dataclass(frozen=True) LlmRequest(index, messages, model, max_tokens, seed, reasoning_effort)   # NO defaults
    index int >= 0; messages a non-empty TUPLE of LlmMessage; model matches the TtsVoice name pattern;
    max_tokens int 1..2**53-1 (not bool); seed int 0..2**53-1 or None; reasoning_effort [a-z][a-z0-9_]{0,31} or None
@dataclass(frozen=True) LlmContext(run_id: RunId, created_at: datetime)
@dataclass(frozen=True) LlmOutcome(text: str|None, usage: tuple[UsageRecord, ...], failure: LlmError|None)
    exactly one of text / failure (an EMPTY text is a text); usage items stage "sim_caller_llm", domain "evaluation"
class LlmEngine(Protocol):  def complete(self, request: LlmRequest, context: LlmContext) -> LlmOutcome
class UsageStreamingLlmEngine(Protocol):  streams_usage: bool;  def complete(self, request, context, *, on_usage: UsageSink | None = None) -> LlmOutcome
def complete_once(engine, request, context, *, on_usage: UsageSink) -> LlmOutcome     # same delivery rule as synthesise_all
```

5.5 `tracer_evalcore.brain`:

```text
BRAIN_END_REASONS = ("script_complete", "caller_end_call", "max_turns", "brain_invalid_output", "spend_cap")  # subset of END_REASONS
@dataclass(frozen=True) BrainDecision(line, end_reason, invalid_outputs, regenerations)   # no defaults
    exactly one of line / end_reason; line a str of 1..MAX_LINE_CHARS characters; end_reason in BRAIN_END_REASONS;
    the two counts ints >= 0 (not bool) with regenerations <= invalid_outputs
class CallerBrain(Protocol), runtime_checkable:  mode: str;  def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision
def require_caller_brain(candidate: object) -> CallerBrain
    returns the candidate itself when it is a CallerBrain whose mode is in CALLER_MODES;
    TypeError when it is not a CallerBrain (no mode, no next_line, a string, None); ValueError for an unknown mode
```

The three brains themselves (scripted, LLM, replay) are P4-13 and P4-15; `agent_text` is the cleaned text of the agent's last finished segment (ADR 0028 rule 9) or `None`.

## 6. CI (`.github/workflows/ci.yml`; `test_phase4_ci.py`)

6.1 Job `licenses`: after `uv build --package tracer-cli --out-dir dist` and before the artifact check, `- run: uv build --package tracer-evalsim --out-dir dist`. Order of the six builds: evalschema, evaldb, evalcore, evalproviders, cli, evalsim. The environment scan is unchanged.

6.2 New job `test-oldest-phase4-deps`, name `test (lowest-direct phase 4 deps, py3.11)`, `runs-on: ubuntu-latest`, no container, no service, default permissions, steps: the pinned `actions/checkout` (`persist-credentials: false`), the pinned `astral-sh/setup-uv` (`version: ${{ env.UV_VERSION }}`, `python-version: "3.11"`), then exactly two run steps:

```text
uv lock --resolution lowest-direct
uv run --locked --resolution lowest-direct --all-groups pytest packages/evalsim/tests --deselect packages/evalsim/tests/test_evalsim_scaffold.py::test_phase4_locked_closures_are_the_reviewed_ones
```

(the deselected test compares the closure with the committed lock and cannot hold after the deliberate downgrade; it runs in every other leg). `phase4_ci_problems` in the test file is the reviewed shape; 22 weakenings (a full `lowest` resolution, a missing `--locked`, another test path, another or a second deselect, another Python, an unpinned action, `continue-on-error`, a service, wider permissions, `|| true`, a database or integration marker) are each refused. It is required, unlike an httpx leg, because the lowest websockets is not the newest. The leg also lowers `aiortc` (it is in the dev group through the extra).

6.3 Job `test-oldest-phase3-deps`: its command gains `--deselect packages/evalcore/tests/test_phase4_licenses.py` (the Phase 3 leg runs the whole evalcore directory, which now holds the Phase 4 licence tests; they compare installed versions with the committed lock, like `test_phase3_licenses.py`). The existing `test_ci_lower_bound_leg_for_phase3_ranges` allows this (it tests substrings). Nothing else of the existing jobs changes: `test` and `test-py315` still run the bare `uv run --locked --all-groups pytest` (the testpaths decide), lint covers the new paths through the root configuration, the two Postgres jobs do not name the new package, no job mentions `samplebot` or `pipecat`, no secret or key reaches the workflow, every action stays pinned by commit.

## 7. `tracer simulate` (P4-01 scaffold)

7.1 `tracer_cli.cli.build_parser` registers a subcommand `simulate` next to `score` (`allow_abbrev=False`, the same streams). `tracer simulate --help` exits 0, prints `usage: tracer simulate ...` on stdout, nothing on stderr, ASCII, no traceback, no claim word (`accuracy`, `wer`, `cer`, `error rate`, `correct`, `recall`, `precision`); the top-level help lists `score` and `simulate`; `tracer score --help` is unchanged; `cli.main(["simulate", "--help"])` raises `SystemExit(0)` like `--help` does. The options arrive with P4-23 (not pinned). In `test_cli_simulate_help.py` the test also asserts that importing `tracer_cli.cli` loads none of `tracer_evalsim`, `websockets`, `aiortc`, `av`: the command line imports the simulator inside the command, at P4-23, not at P4-01.

7.2 `tracer-cli` does NOT yet declare `tracer-evalsim` (section 12, ambiguity 1): its `requires_dist`, its policy block and its lock closure stay as in Phase 3, so `test_phase3_requires_dist_bounded_ranges[cli]`, `test_phase3_locked_closures_are_the_reviewed_ones[cli]`, `test_cli_runtime_imports_whitelist` and `test_license_policy_lists_phase3_closures` are untouched. P4-23 adds the dependency, the lazy import and its spec changes together.

## 8. The evalschema baseline (`test_evalschema_unchanged_since_phase4_baseline`)

`docs/phase4/evidence/evalschema-baseline.json` has exactly the keys `commit`, `schema_lock_sha256`, `source_tree_sha256`, `pyproject_sha256`; `commit` is the Phase 3 gate commit `a1f01617116cb44f749f4e642d4fcb5f165f8e4a` (SCOPE D4); the three hashes equal what `packages/evaldb/tests/evalschema_baseline.py` computes on the current tree and equal those of `docs/phase3/evidence/evalschema-baseline.json` (nothing moved between `e942b3b` and the gate commit). qa computed them from `git archive a1f0161` (the evalschema source tree and its `pyproject.toml`) and from the current tree: identical.

```text
schema_lock_sha256  ee5b2ac39ecca544f89f0757760620a52cfe4fd38913f64b4297523ccab66b73
source_tree_sha256  59d5b637afb46ce43e7e8ee5423fdbb757039da32695d2a1ce46a2441c5f1906
pyproject_sha256    1a918eef9fd6647f160722f68241783afddbe09a73f7cbc43621f7bc5920a52e
```

Script (the existing tool plus the commit; the dev writes the file and commits it; qa's oracle output is byte-identical):

```text
uv run python -c "import importlib.util as u, json; s=u.spec_from_file_location('b','packages/evaldb/tests/evalschema_baseline.py'); m=u.module_from_spec(s); s.loader.exec_module(m); v=m.current_values(); v['commit']='a1f01617116cb44f749f4e642d4fcb5f165f8e4a'; print(json.dumps(v, indent=2, sort_keys=True))" > docs/phase4/evidence/evalschema-baseline.json
```

The test also proves the hash follows content (a copy with one byte more differs), and, where the baseline commit is present in the clone (a shallow CI checkout lacks it), recomputes the three hashes from `git archive` of that commit.

## 9. Named spec changes (tightening or extending only; before and after in `spec-change.diff`)

```text
S1  packages/evalcore/tests/test_phase3_scaffold.py::test_phase3_scaffold_files_and_repo_config
    before: testpaths == [5 directories]    after: the same 5 plus "packages/evalsim/tests" at the end
S2  packages/evaldb/tests/test_evaldb_ci.py::test_ci_sqlite_runs_on_every_python_leg
    before: testpaths == [5 directories]    after: the same 5 plus "packages/evalsim/tests" at the end
S3  packages/cli/tests/test_cli_score.py::test_cli_has_no_export_command
    before: set(action.choices) == {"score"}    after: == {"score", "simulate"}   (still a closed list)
S4  packages/evalcore/tests/evalcore_phase4_adr_support.py : the pending sets shrink (section 10.2), deletions only
```

Found by the oracle's full suite (S1 to S3 failed, nothing else of 12,908 earlier tests did). No test was skipped, deleted or weakened.

## 10. Test inventory

10.1 SCOPE names (EXACTLY these: 15 names in the three rows, 12 new tests and 3 existing tests that stay unchanged; 52 new test items in all, parametrised cases counted):

| Task | Name | File |
|---|---|---|
| P4-01 | `test_evalsim_runtime_imports_whitelist` | `packages/evalsim/tests/test_evalsim_scaffold.py` |
| P4-01 | `test_evalcore_imports_no_network_module` (existing, unchanged) | `packages/evalcore/tests/test_phase3_scaffold.py` |
| P4-01 | `test_evalcore_imports_no_asyncio_network` | `packages/evalcore/tests/test_phase4_scaffold.py` |
| P4-01 | `test_phase4_requires_dist_bounded_ranges` | `packages/evalsim/tests/test_evalsim_scaffold.py` |
| P4-01 | `test_evalsim_wheel_contains_no_tests` | `packages/evalsim/tests/test_evalsim_scaffold.py` |
| P4-01 | `test_evalschema_unchanged_since_phase4_baseline` | `packages/evalcore/tests/test_phase4_scaffold.py` |
| P4-01 | `test_tracer_simulate_help_runs` | `packages/cli/tests/test_cli_simulate_help.py` |
| P4-01 | `test_samplebot_not_a_workspace_member_and_not_in_uv_lock` | `packages/evalcore/tests/test_phase4_scaffold.py` |
| P4-02 | `test_license_policy_lists_phase4_closures` | `packages/evalcore/tests/test_phase4_licenses.py` |
| P4-02 | `test_no_lgpl_or_gpl_in_lock` (existing, unchanged) | `packages/evaldb/tests/test_evaldb_licenses.py` |
| P4-02 | `test_license_exceptions_exact` (existing, unchanged) | `packages/evalschema/tests/test_repo_contract.py` |
| P4-02 | `test_samplebot_closure_reviewed_and_tool_only` | `packages/evalcore/tests/test_phase4_licenses.py` |
| P4-03 | `test_tts_llm_errors_hold_facts_only_and_no_chain` (parametrised `tts`, `llm`) | `packages/evalcore/tests/test_sim_engine_boundary.py` |
| P4-03 | `test_engines_stream_usage_before_next_request` | `packages/evalcore/tests/test_sim_engine_boundary.py` |
| P4-03 | `test_brain_protocol_accepts_scripted_llm_replay` | `packages/evalcore/tests/test_sim_engine_boundary.py` |

Added by qa (not in the SCOPE table; each has a reason in its docstring): `test_tts_llm_request_records_have_no_defaults_and_validate` and `test_sim_constants_are_the_ones_the_adrs_state` (P4-03); `test_phase4_scaffold_files_and_repo_config`, `test_phase4_locked_closures_are_the_reviewed_ones` and `test_phase4_dev_environment_has_the_extra` (P4-01, in the evalsim directory); `test_samplebot_and_pipecat_are_not_installed_in_the_tracer_environment` (P4-01); `test_check_licenses_covers_evalsim_and_its_extra`, `test_check_licenses_accepts_one_name_locked_at_two_versions`, `test_check_licenses_allows_only_the_declared_extra_of_evalsim` and `test_evalsim_lock_entry_declares_its_extra` (P4-02); the CI tests of `test_phase4_ci.py` (P4-01). The two `test_*` of Phase 3 CI (`test_ci_lower_bound_leg_for_phase3_ranges`, `test_ci_phase3_wheels_are_built_and_checked`) pass unchanged.

10.2 Pending sets (the dev deletes these names from the sets of `evalcore_phase4_adr_support.py` in the same commit that moves the tests; the staged copy of the file has them deleted already, and a set entry that is also defined by a test is a failure of `check_adr`). 16 deletions of 12 names:

```text
PENDING["0026"] (12): test_brain_protocol_accepts_scripted_llm_replay, test_engines_stream_usage_before_next_request,
  test_evalcore_imports_no_asyncio_network, test_evalschema_unchanged_since_phase4_baseline,
  test_evalsim_runtime_imports_whitelist, test_evalsim_wheel_contains_no_tests,
  test_license_policy_lists_phase4_closures, test_phase4_requires_dist_bounded_ranges,
  test_samplebot_closure_reviewed_and_tool_only, test_samplebot_not_a_workspace_member_and_not_in_uv_lock,
  test_tracer_simulate_help_runs, test_tts_llm_errors_hold_facts_only_and_no_chain
PENDING["0027"] (1):  test_evalschema_unchanged_since_phase4_baseline
PENDING["0028"] (1):  test_brain_protocol_accepts_scripted_llm_replay
PENDING["0024A"] (2): test_engines_stream_usage_before_next_request, test_tts_llm_errors_hold_facts_only_and_no_chain
```

New sizes: 0026 69 to 57, 0027 42 to 41, 0028 19 to 18, 0024A 27 to 25; 0029, 0023A, 0004 and the outside-rules set are unchanged.

## 11. Evidence

`docs/phase4/evidence/red/batch-0b/`:

```text
red.py3.11.log, red.py3.12.log, red.py3.13.run1.log, red.py3.13.run2.log, red.py3.14.log
    the staged files at their final path in a mirror of the CURRENT tree (environments synced from the current lock),
    9 files, 139 tests: 28 failed, 111 passed on every version (3.13 twice, identical). The 28 are 25 of the 52 new
    tests plus the three spec-changed Phase 3 tests; the 111 passes are earlier tests plus the new guards and
    teeth tests that hold on any tree (the CI check refusals, `pipecat-ai` not installed, ...).
red-reasons.py3.13.txt
    the one-line reason of each failure: ModuleNotFoundError for tts, llm, brain, simtypes; "the policy has no
    [runtime.evalsim] block"; "uv.lock does not hold tracer-evalsim"; FileNotFoundError for the baseline file and
    tools/samplebot/pyproject.toml; "job test-oldest-phase4-deps is missing"; testpaths lacks evalsim; usage shows
    {score} only; ... (no failure is an import error of the test itself)
oracle.py3.11.log .. oracle.py3.14.log
    the same 9 files on the oracle (all staged files and the minimal changes): 139 passed on every version
oracle-full-suite.py3.11.log .. oracle-full-suite.py3.14.log
    the whole workspace on the oracle: 12,960 passed, 1 skipped (the opt-in gate test) on every version;
    the earlier suite had 12,908 passed (3.13, scratch copy of the tree before any change) plus 52 new tests
mutants.txt
    105 mutants of the oracle, 105 caught, 0 survived (3 survived in a first run and led to test additions, see its header)
oracle-src.patch, oracle-uv.lock.patch
    the oracle's non-test changes and its offline lock: a starting point for the dev, not a substitute for
    the dev's own `uv lock` with network
spec-change.diff
    the four named spec changes of section 9, before and after
```

Method notes. The oracle was built offline in a scratch copy of the tree. `uv lock --offline` resolved the extra from the cache; the wheels of the extra were not in the cache, so they were unpacked from the hash-verified downloads of the P4-02 review into each oracle environment (the reviewed versions; for `google-crc32c` the only wheel downloaded, cp311 macOS, serves all four interpreters because nothing imports it; `websockets` is the pure-Python 17.3 wheel). The hosted CI installs the wheels `uv sync --locked` selects for its platform. Not run, for lack of network: the lowest-direct leg (it installs websockets 15.0.x, pydantic 2.11.0 and others), the Postgres legs, Python 3.15.

## 12. Ambiguities, with recommendations (the lead auto-approves)

1. **Does `tracer-cli` depend on `tracer-evalsim` at P4-01?** SCOPE says "tracer-cli (extended) adds evalsim" without a task. A declared dependency without an import adds `websockets` to every CLI install, and changes the pinned CLI closure, `requires_dist` and policy block of three Phase 3 tests. Recommendation (applied): the dependency and the first import arrive together with `tracer simulate`'s body (P4-23), with their own named spec changes then; at P4-01 only the subcommand and its help exist. Rejected: declaring it now (a spec change with no code behind it).
2. **The named exception for the FFmpeg stack inside `av` cannot be an `[[exceptions]]` entry** (the checker consults exceptions only for non-allowed packages; `av` declares BSD-3-Clause), so `test_license_exceptions_exact` cannot and does not change. Recommendation (applied): `[[bundled_notices]]` is the machine-checked record (status, `recheck`, evidence) and the lead logs the exception in the decision log (owner question O6 is decided). Not pinned: that the log entry exists.
3. **The checker did not support a name locked at two versions** (`pyee`); it fails on Python 3.11 and would fail the 3.11 CI leg. The review's note 4 asked only that "the closure test tolerate one name with two versions". Recommendation (applied in the oracle, pinned by a synthetic-lock test): section 4.3, third bullet.
4. **`tools/samplebot/uv.lock` at P4-01?** Resolving Pipecat 1.14.0's closure needs the network (qa had none) and the lock is part of P4-26's acceptance ("Own lock; README"). Recommendation: create it at P4-26 at the latest; the tests tolerate its absence and pin its content when it exists (section 3.2). If the dev has network at P4-01 and writes it then, the same tests pass.
5. **websockets 15.0 may be unusable as the lower bound.** In the oracle `uv lock --resolution lowest-direct` (offline, cache) resolved `websockets` to 15.0.1, not 15.0, although the cached index lists 15.0; that is what uv does for a yanked release (not verified: no network). The lower-bound leg then tests 15.0.1 and the declared floor `>=15.0` is untested. Recommendation: the dev checks PyPI; if 15.0 is yanked, the floor becomes `>=15.0.1,<18` (one change in `DEPENDENCIES` of `test_evalsim_scaffold.py`, in `REQUIRES` of `test_phase4_licenses.py`, in the policy, `pyproject.toml` and this file). The tests follow the lead's brief (`>=15.0,<18`) until then.
6. **The lowest-direct leg could not be run** (it installs websockets 15.0.x and the oldest pydantic and aiortc, none cached). The test `test_evalsim_runtime_imports_whitelist` is written to fail on it if the `websockets.asyncio` API is missing; the leg's first real run is on hosted CI.
7. **Engine boundary design beyond ADR 0026 rule 4** (the lead may veto): a `FactError` that validates its facts (stricter than `SttError`, which stores any string: ADR 0026 rule 4 says "facts only"), `TtsCapReachedError`/`LlmCapReachedError` and `TtsTextTooLongError` (so the end reason `spend_cap` and the pre-send refusals of ADR 0026 rule 15 and ADR 0024 A2 have a typed carrier), request records in place of loose arguments, and the two drivers `synthesise_all` and `complete_once` (the testable form of "usage before the next request"). Recommendation: keep; each is a few lines and removing one removes a test, not a rule.
8. **`TtsVoice.pace` is a decimal string, not a float** (sim profile schema: `pace` is a string with a canonical-decimal pattern; no float in any record, ADR 0014 Decision 15a). The Sarvam adapter converts at the wire.
9. **Not pinned at P4-01**: the module list of `tracer_evalsim` beyond `errors`, the kill-switch file name, the message and frame caps (P4-16, P4-17), and the placeholder text of `tracer simulate --help`.
