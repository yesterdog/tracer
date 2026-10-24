# Phase 3 batch A contract: scaffold, licences, CI, WAV reader, VoicEra reader, chunker (P3-01 to P3-05)

Author: qa, 2026-10-25. Source: `docs/phase3/SCOPE.md` (tasks A.0 and A.1, Decisions D1 to D7, Q1 to Q22), ADRs 0023, 0024, 0025, 0016 and 0004 Decision 10, `docs/security/P3-02-licenses.md`, the Phase 2 batch A contract (`docs/phase2/api-surface-batch-a.md`) as the model, the lead's instructions of 2026-10-25. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test. Every statement below is pinned by a test named in section 9, unless it says otherwise.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 lesson, twice): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` and `uv lock --check` for the whole workspace (`members = ["packages/*"]`). So nothing was created under `packages/`. The tests are staged at

```
docs/phase3/evidence/red/batch-a/staging/evalcore/tests/        -> packages/evalcore/tests/
docs/phase3/evidence/red/batch-a/staging/evalproviders/tests/   -> packages/evalproviders/tests/
docs/phase3/evidence/red/batch-a/staging/cli/tests/             -> packages/cli/tests/
```

The last path component is `tests`, so the `**/tests/**` ruff rule `S101` applies; `ruff check`, `ruff format --check` and `mypy --strict` pass on the staged files as they are (no `tracer_evalcore`, `tracer_evalproviders` or `tracer_cli` import is made at module level: each test loads the package lazily with `importlib`, so before the packages exist every test fails on its own with `ModuleNotFoundError` or `FileNotFoundError` and the directory still collects). The dev's first act in P3-01 is, together with the scaffold, the root `pyproject.toml` changes of section 8 and the relock:

```
git mv docs/phase3/evidence/red/batch-a/staging/evalcore/tests       packages/evalcore/tests
git mv docs/phase3/evidence/red/batch-a/staging/evalproviders/tests  packages/evalproviders/tests
git mv docs/phase3/evidence/red/batch-a/staging/cli/tests            packages/cli/tests
mkdir -p packages/evalcore/tests/fixtures
git mv docs/phase3/evidence/staging/normaliser_mr.json packages/evalcore/tests/fixtures/normaliser_mr.json
```

Nothing in the tests depends on the location: the repository root is found by walking up to `uv.lock`. There is no `__init__.py` in any of the three directories on purpose (`packages/evalschema/tests` is the package `tests`; a second package of that name would clash). Test module names are unique across all five test directories; there is no `conftest.py`, no sibling import and no data file except the moved normaliser fixture. All audio is generated into `tmp_path`; the only repository data the tests read is the public VoicEra twin of the evalschema tests. Nothing opens a socket. Nothing reads `packages/evalschema/tests/fixtures/private`.

Commands (after the move and the section 8 changes):

* everything: `uv run --locked --all-groups pytest` (the five testpaths)
* Phase 3 only: `uv run --locked --all-groups pytest packages/evalcore/tests packages/evalproviders/tests packages/cli/tests`

## 1. P3-01: the three packages

| | tracer-evalcore | tracer-evalproviders | tracer-cli |
|---|---|---|---|
| directory | `packages/evalcore` | `packages/evalproviders` | `packages/cli` |
| import name | `tracer_evalcore` | `tracer_evalproviders` | `tracer_cli` |
| `[project] dependencies` | `tracer-evalschema==0.0.1` | `tracer-evalschema==0.0.1`, `tracer-evalcore==0.0.1`, `httpx>=0.28.1,<1` | `tracer-evalschema==0.0.1`, `tracer-evalcore==0.0.1`, `tracer-evalproviders==0.0.1`, `tracer-evaldb==0.0.1` |
| `[project.scripts]` | none | none | `tracer = "tracer_cli.cli:main"` |
| `optional-dependencies` | none | none | none (no `[postgres]` forwarding) |

1.1 Layout of each package: `pyproject.toml`, `README.md` (non-empty), `LICENSE` and `NOTICE` (both byte-identical to `packages/evalschema/LICENSE` and `NOTICE`), `src/<import name>/__init__.py`, `src/<import name>/py.typed`, `tests/`. Every `.py` under `src/` starts with the two SPDX header lines. `tracer_evalcore.__version__ == "0.0.1"` and equals `importlib.metadata.version("tracer-evalcore")` (the scorer version of ADR 0023 rule 3 is the distribution version).

1.2 `pyproject.toml` of each package: `[build-system] requires = ["hatchling==1.32.4"]`, `build-backend = "hatchling.build"`; `[project]` `name`, `version = "0.0.1"`, `readme = "README.md"`, `requires-python = ">=3.11"`, `license = "Apache-2.0"`, `license-files = ["LICENSE", "NOTICE"]`, classifiers `Typing :: Typed` and `Programming Language :: Python :: 3.11` to `3.14`; `[tool.hatch.build.targets.wheel] packages = ["src/<import name>"]`.

1.3 `requires_dist` as parsed requirement sets (`test_phase3_requires_dist_bounded_ranges`, one case per package). Wheel metadata reorders `>=0.28.1,<1` to `<1,>=0.28.1`, so the tests compare `(canonical name, marker, SpecifierSet)`, never text. Rules, all tested:

* the declared `dependencies`, the `[runtime.<key>].requires_dist` of the licence policy and the wheel's `Requires-Dist` are equal as parsed sets;
* a workspace package (`tracer-*`) is `==0.0.1` exactly (SCOPE D3: the schema-version pin of the data, not a library range);
* every other requirement has exactly the operators `>=` and `<` and the upper bound is the next major of the lower bound's major (`httpx>=0.28.1,<1`: `0 + 1`); the only such requirement in Phase 3 is httpx, and only `tracer-evalproviders` carries it;
* `Provides-Extra` is absent from all three wheels, `Requires-Python` is `>=3.11`, the wheel `Name` and `Version` are the package's.

A bound may only be lowered when the whole suite passes at that version on the lower-bound leg (SCOPE D3).

1.4 Wheels (`python -m hatchling build -t wheel`; `test_phase3_wheels_contain_no_tests`, one case per package): the top-level entries are only `<import name>/` and the one `*.dist-info/`; the wheel contains `<import name>/__init__.py` and `py.typed`; no member has a path part `tests`, `test`, `fixtures` or `__pycache__`, no basename `test_*`, `conftest.py`, `*.pyc` or containing `baseline`; the dist-info carries LICENSE and NOTICE. `tracer-evalcore` additionally ships `errors.py`, `audio/{__init__,seconds,chunker}.py`, `importers/{__init__,wav,voicera}.py`; `tracer-cli` ships `cli.py`. `entry_points.txt` of the dist-info declares exactly `[console_scripts]` `tracer = tracer_cli.cli:main` for `tracer-cli` and does not exist (or is empty) for the other two.

1.5 The lock (`test_phase3_locked_closures_are_the_reviewed_ones`): `uv.lock` holds the three packages with `source = { editable = "packages/<key>" }` and the closures

| package | closure in `uv.lock` (workspace packages included) |
|---|---|
| tracer-evalcore | annotated-types, pydantic, pydantic-core, rfc8785, typing-extensions, typing-inspection, tracer-evalschema |
| tracer-evalproviders | the above, tracer-evalcore, anyio, certifi, h11, httpcore, httpx, idna |
| tracer-cli | all of the above, tracer-evalproviders, tracer-evaldb, alembic, mako, markupsafe, sqlalchemy |

No `sniffio`, `greenlet`, `requests` or `sarvamai` in the lock (P3-02 sections 2 and 6). The oracle's `uv lock` (2026-10-25) resolved httpx 0.28.1, httpcore 1.0.9, h11 0.16.0, anyio 4.15.1, idna 3.20, certifi 2026.10.23: the versions the licence review read.

1.6 The normaliser fixture (`test_normaliser_fixture_moved_unchanged`): `packages/evalcore/tests/fixtures/normaliser_mr.json` exists with sha256 `cbdd56b19687761ed4d31c663f97706f5ca7c46b3ad5244e79d38969ee34900c` (the staged file, byte for byte), parses as JSON, and `docs/phase3/evidence/staging/normaliser_mr.json` no longer exists.

1.7 evalschema is untouched (`test_evalschema_unchanged_since_phase3_baseline`, `test_evalschema_does_not_import_phase3_packages`).

* `docs/phase3/evidence/evalschema-baseline.json` (the dev creates it) has exactly the keys `commit`, `schema_lock_sha256`, `source_tree_sha256`, `pyproject_sha256`; `commit` is `e942b3bd825c92a668a3a1c8061211b76a4d661d` (the Phase 2 completion commit, SCOPE D4); the three hashes equal what `packages/evaldb/tests/evalschema_baseline.py` computes now and equal the three of `docs/phase2/evidence/evalschema-baseline.json`. qa computed on 2026-10-25, tree on top of `69aa68d`: `schema_lock_sha256 ee5b2ac39ecca544f89f0757760620a52cfe4fd38913f64b4297523ccab66b73`, `source_tree_sha256 59d5b637afb46ce43e7e8ee5423fdbb757039da32695d2a1ce46a2441c5f1906`, `pyproject_sha256 1a918eef9fd6647f160722f68241783afddbe09a73f7cbc43621f7bc5920a52e`. The file content is Phase 2's with `commit` replaced (the oracle's copy is in `oracle-src.patch`).
* No module of `tracer_evalschema` imports `tracer_evalcore`, `tracer_evalproviders`, `tracer_cli`, `tracer_evaldb`, `httpx`, `httpcore`, `sqlalchemy` or `alembic` (AST), importing all of them in a fresh interpreter loads none, and its `pyproject.toml` dependencies are still `pydantic>=2.11,<3` and `rfc8785>=0.1.4,<0.2` with no extras.

## 2. Import whitelists, the console script

"Imports" means every `import`, `from ... import` and literal `importlib.import_module` / `__import__` of any module under `src/`, at any depth (inside functions, `try` and `TYPE_CHECKING`), by AST; a dynamic import with a computed name is an offence. Relative imports are intra-package and ignored. The standard library is always allowed (`sys.stdlib_module_names`).

2.1 `tracer_evalcore` (`test_evalcore_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module`): may import `tracer_evalschema` and `tracer_evalcore`. Not `pydantic` (an undeclared direct dependency), not `httpx`, not anything else. In a fresh `python -I` interpreter, importing every module loads, beyond the standard library, only `tracer_evalschema`, `tracer_evalcore`, `pydantic`, `pydantic_core`, `annotated_types`, `typing_extensions`, `typing_inspection`, `rfc8785` and the `cython_runtime` marker; and none of `httpx`, `httpcore`, `h11`, `anyio`, `requests`, `urllib3`, `aiohttp`, `websockets`, `sarvamai`, `sqlalchemy`, `alembic`, `pg8000`, `psycopg*`, `asyncpg`, `pytest`, `jsonschema`, `hypothesis`, `yaml`. No network (SCOPE P3-01, ADR 0024 rule 1): no source line imports `socket`, `ssl`, `http`, `urllib` (any submodule), `httpx`, `httpcore`, `h11`, `requests`, `urllib3`, `aiohttp`, `websockets`, `ftplib`, `smtplib`, `imaplib`, `poplib`, `nntplib`, `telnetlib`, `xmlrpc`, `socketserver` or `asyncio`; and the modules that importing `tracer_evalcore` adds on top of `tracer_evalschema` (measured as a difference of two fresh interpreters) include none of `socket`, `ssl`, `http*`, `httpx`, `httpcore`, `urllib.request`, `urllib.error`, `ftplib`, `smtplib`, `imaplib`, `poplib`, `socketserver`, `xmlrpc*`, `asyncio`. (`pathlib.Path.as_uri` imports `urllib.parse` at call time: allowed, it is string handling.) The WAV reader, the VoicEra reader and the chunker also run with `socket.socket` replaced by a function that raises (`test_wav_reader_works_without_a_network`).

2.2 `tracer_evalproviders` (`test_evalproviders_runtime_imports_whitelist`, in `packages/evalproviders/tests`): may import `httpx`, `tracer_evalschema`, `tracer_evalcore`, `tracer_evalproviders`. Importing every module loads, beyond the standard library, only those plus `httpcore`, `h11`, `anyio`, `idna`, `certifi`, `sniffio`, the evalschema closure and `cython_runtime`; and none of `requests`, `urllib3`, `aiohttp`, `websockets`, `sarvamai`, `trio`, `sqlalchemy`, `alembic`, `pg8000`, `psycopg*`, `asyncpg`, `tracer_evaldb`, `tracer_cli`, `pytest`, `jsonschema`, `hypothesis`, `yaml`. The adapter package never reaches the store or the command line.

2.3 `tracer_cli` (`test_cli_runtime_imports_whitelist`, in `packages/cli/tests`): may import `tracer_evalschema`, `tracer_evalcore`, `tracer_evalproviders`, `tracer_evaldb`, `tracer_cli` and nothing else third-party: not `httpx`, not `sqlalchemy` (providers and the store are reached through their packages; the store takes a URL string). Importing every module (a module named `__main__` is skipped) loads, beyond the standard library, only those plus the httpx closure, `sqlalchemy`, `alembic`, `mako`, `markupsafe`, the evalschema closure and `cython_runtime`; and none of `pg8000`, `scramp`, `dateutil`, `psycopg*`, `asyncpg`, `requests`, `urllib3`, `aiohttp`, `websockets`, `sarvamai`, `pytest`, `jsonschema`, `hypothesis`, `yaml`.

2.4 The console script (`test_tracer_console_script_runs_help`): after `uv sync` the script `tracer` is next to the interpreter of the environment (`shutil.which("tracer", path=dirname(sys.executable))`); `tracer --help` exits 0, prints to stdout a text starting `usage: tracer`, prints nothing to stderr and no `Traceback`, and contains none of the words `accuracy`, `WER`, `CER`, `error rate`, `correct`, `recall`, `precision` (whole words, case-insensitive; ADR 0023 rule 1). `tracer_cli.cli.main(argv: Sequence[str] | None = None) -> int` is the entry point (parameter `argv` default `None`); the parser is built with `prog="tracer"`, so `main(["--help"])` raises `SystemExit(0)` and prints `usage: tracer ...` on stdout in-process too. Subcommands (`score`) are P3-22.

## 3. P3-03: the WAV reader

Module `tracer_evalcore.importers.wav`. Names: `read_wav`, `WavAudio`, `DEFAULT_MAX_WAV_BYTES`, `WavError`, `WavMalformedError`, `WavTruncatedError`, `WavUnsupportedFormatError`, `WavTooLargeError`. Seconds: module `tracer_evalcore.audio.seconds`, function `exact_seconds`. Error bases: module `tracer_evalcore.errors`, `EvalcoreError(Exception)` and `InputRefusedError(EvalcoreError, ValueError)`; `WavError(InputRefusedError)`; the four WAV errors are direct subclasses of `WavError` (tests use `type(exc) is ...`).

```python
def read_wav(
    path: str | os.PathLike[str],
    *,
    created_at: str | datetime,
    consent: ConsentRecord,
    retention: RetentionPolicy,
    max_bytes: int = DEFAULT_MAX_WAV_BYTES,   # 1_073_741_824
) -> WavAudio

@dataclass(frozen=True)
class WavAudio:
    artifact: Artifact
    frames: int
    sample_rate_hz: int
    channels: int
    duration_seconds: Decimal | None        # exact_seconds(frames, sample_rate_hz)
    pcm: bytes                              # field(repr=False): the samples never reach a log

def exact_seconds(frames: int, rate: int) -> Decimal | None
```

3.1 What is read (`test_wav_reader_reads_mono_24k_16bit_exact_duration`): a RIFF/WAVE file with a `fmt ` chunk of format tag 1 (plain PCM, 16 bits, at least one channel, a rate above zero) and a `data` chunk; other chunks before or after (odd sizes are padded to even) are skipped; trailing chunks after the data are ignored. `frames = data bytes // (2 * channels)`; `pcm` is the data chunk's bytes (little-endian, interleaved), nothing else. The RIFF size field is not used (unspecified, section 10 item 7). Zero frames is a valid read (`frames == 0`, `pcm == b""`, duration `Decimal(0)`).

3.2 Exact duration, with a finding (section 11, F-A1). `frames / rate` has no finite decimal expansion for most counts at 24 kHz (`24000 = 2^6 * 3 * 5^3`; one frame is `1/24000`), and the schema's decimal carries at most 18 fractional digits (`DecimalString`). So `exact_seconds` returns the `Decimal` that equals `frames / rate` exactly when that quotient terminates within 18 fractional digits, and `None` otherwise. It is never rounded and never a float: `(24000, 36000) -> 1.5`, `(24000, 24000) -> 1`, `(24000, 0) -> 0`, `(8000, 1) -> 0.000125`, `(1000, 254600) -> 254.6`, `(262144, 1) -> 0.000003814697265625` (exactly 18 digits), but `(24000, 1)`, `(44100, 1)`, `(3, 1)` and `(524288, 1)` (19 digits) give `None`. The result is a `Decimal` whose product with `rate` is `frames`. `frames` and `rate` are `int` and not `bool` (`TypeError` otherwise); `frames >= 0` and `rate > 0` (`ValueError` naming `frames` or `rate` otherwise). `WavAudio.duration_seconds` is this value; `frames` and `sample_rate_hz` are always the exact facts.

3.3 Refusals (each a typed error, none echoes any byte of the file; the messages hold sizes and format numbers only):

| Type | When |
|---|---|
| `WavUnsupportedFormatError` | format tag other than 1 (float 3, A-law 6, MP3 0x55, the extensible tag 0xFFFE on every Python), samples not 16 bit (8, 12, 24, 32), zero channels, sample rate zero (`test_wav_reader_rejects_non_pcm_and_8_or_24_bit`) |
| `WavTruncatedError` | the `data` chunk declares more bytes than the file holds (a placeholder `0xFFFFFFFF` included); the data size is not a whole number of frames; the file ends inside a chunk; refused without allocating for the claim (tracemalloc peak under 8 MiB with a header that claims about 4 GiB) (`test_wav_reader_rejects_truncated_and_lying_header`) |
| `WavMalformedError` | not RIFF/WAVE (empty, 4 bytes, text, `RIFF....AVI `), no `fmt ` chunk, no `data` chunk, `data` before `fmt `, a `fmt ` chunk shorter than 16 bytes, a block alignment that contradicts the channels, not a regular file |
| `WavTooLargeError` | the file is larger than `max_bytes` |

For the truncated and malformed rows the exact class is pinned where it is unambiguous (a data chunk cut short, a lying data size, a partial frame: `WavTruncatedError`); for a file that ends inside the header, an empty file and a file without a data chunk any `WavError` is accepted. A missing file raises `FileNotFoundError` (operating-system errors are not rewrapped).

3.4 The size cap (`test_wav_reader_rejects_oversize_before_reading`, `test_wav_reader_refuses_non_regular_files_without_reading`). `max_bytes` is an `int` (not `bool`; `TypeError` otherwise) greater than zero (`ValueError` naming `max_bytes`). The size comes from `os.fstat` on the opened descriptor; a file of exactly `max_bytes` is read, one byte more raises `WavTooLargeError` after reading zero bytes (the test counts the bytes that `open`, `io.open` and `os.read` hand out). A descriptor that is not a regular file (device, pipe, socket: the test fakes `st_mode`) raises `WavMalformedError` after reading zero bytes. The default cap is `DEFAULT_MAX_WAV_BYTES = 1_073_741_824` (1 GiB, a technical value, not measured: four hours of 24 kHz mono 16-bit is 691 MB), applied to a sparse file of cap plus one byte with a tracemalloc peak under 8 MiB. Finding F-A3: a reader that allocates its buffer from the cap (`handle.read(max_bytes + 1)`) allocates 1 GiB for a 50-byte file; the oracle sizes the buffer by `st_size + 1` and the tests catch the other form (mutant W15).

3.5 The Artifact (`test_artifact_uri_is_absolute_file_uri`, `test_artifact_privacy_marker_requires_explicit_consent_input`, `test_wav_reader_ids_and_clock`): `id` a new `ArtifactId`; `schema_version` the current one; `created_at` exactly the argument (a string or a datetime; never the clock; an invalid one is the model's `ValueError`); `uri = Path(path).resolve(strict=True).as_uri()`: absolute `file:///...`, symbolic links and `..` resolved (the URI names the file whose bytes were read), a relative path resolved against the working directory at the time of the call, every character that a URI cannot carry percent-encoded (non-ASCII, space, `#`, `?`, `%`), so that `Artifact.uri` is ASCII with no query and no fragment and `unquote(path) == str(resolved)`; `sha256` the hex digest of the whole file; `size` the file's byte length; `audio_format = AudioFormat(container="wav", codec="pcm_s16le", sample_rate_hz=<rate>, channels=<n>, bits_per_sample=16)`; `turn_ref` absent; `metadata` default.

3.6 Privacy marker from explicit inputs (ADR 0023 rule 16): `consent: ConsentRecord` and `retention: RetentionPolicy` are keyword-only with no default; leaving one out is a `TypeError`, and so is passing anything that is not exactly that model (a string `"obtained"`, a dict): nothing is coerced. The marker is `PrivacyMarker(human_subject=True, consent=<as given>, retention=<as given>)` with no `human_subject_reason`: a recording is human-subject data whatever the caller origin (rule 16). `ConsentRecord(status="unknown")` stays `unknown` (never turned into `obtained`), `reference` is kept.

3.7 Frozen and quiet: `WavAudio` is a frozen dataclass with exactly the fields above, in that order; its `repr` is under 2,000 characters for a 96 kB `pcm`.

## 4. P3-04: the VoicEra export reader

Module `tracer_evalcore.importers.voicera`. Names: `read_voicera_export`, `VoicEraRead`, `VoicEraRefusedError`, `NON_REFUSING_WARNING_CODES`.

```python
def read_voicera_export(
    text: str,
    *,
    created_at: str | datetime,
    privacy: PrivacyMarker | Mapping[str, Any],
    declared_language: LanguageDeclaration | Mapping[str, Any] | None = None,
    accept_warnings: bool = False,
) -> VoicEraRead

@dataclass(frozen=True)
class VoicEraRead:
    transcript: Transcript | None
    turns: tuple[Turn, ...]                        # in seq order
    warnings: tuple[IngestWarning, ...]
    external_refs: tuple[ExternalRef, ...]

class VoicEraRefusedError(InputRefusedError):      # InputRefusedError is a ValueError
    warnings: tuple[IngestWarning, ...]            # every warning of the read

NON_REFUSING_WARNING_CODES: frozenset[str] = frozenset({
    "call_id_not_uuid", "timestamp_not_parseable", "naive_timestamp", "time_precision_reduced"})
```

4.1 The mapping is the F8 mapper's, unchanged (`test_voicera_reader_equals_f8_mapper_on_public_cases`, 38 inputs): the F8 module `packages/evalschema/tests/support/mappings/voicera_transcript.py` is loaded by file path (the test builds a package named `_tracer_f8_support` over the `mappings` directory; the file is not edited) and the two are compared on the public twin and 37 hand-written inputs (every kind of line, every warning code, CRLF, a byte order mark, blank lines, unknown speakers, a missing header, a missing delimiter, naive, unparseable, offset, `Z` and over-precise stamps, a non-UUID and an empty call id, a header with a leading or a trailing space, empty text, leading and trailing spaces and tabs, a lone surrogate, the empty export, NFC-unstable and joiner text, bidi controls in text, a 200,000-character line, upper-case speakers, 600 turns, a declared language). Equal means: the transcript and each turn compare equal as `model_dump(mode="json")` without `id` (and without `transcript_id` on turns); `turn.transcript_id == transcript.id`; the turns' `seq` is 0, 1, 2... in file order with no gap; the warnings are equal as `(code, path, raw, message)` in the same order; `external_refs` are equal; when F8 builds no record (empty export), `transcript is None` and `turns == ()`. Not shipped: F8's trace, gap rows and consumed pointers.

4.2 Records (`test_voicera_reader_maps_assistant_agent_user_caller`, `test_voicera_reader_never_sets_language_or_timing`): `Transcript.source = "agent_export"`; `provider_call_ref = {system: voicera, kind: call_id, value: <header value verbatim>}`; `assistant` maps to role `agent`, `user` to `caller`, every other label (`Assistant`, `agent`, `caller`, `system`) is not mapped and warns `unknown_enum_value`; every turn has `source = "agent_log"`, `timing_source = "none"`, `wall_time = {at, meaning: "unknown"}` when the stamp parses and none otherwise. Never set: `Transcript.provider`, `model`, `detected_language`, `detected_language_raw`, `language_confidence`, `timeline_origin`; `Turn.start_ms`, `end_ms`, `segments`, `language`, `language_raw`, `interrupted`, `exchange_index`, whatever the text says (language tags and clock times in the text are text). The caller (user) turns are agent outputs under the amended ADR 0016 Decision 1 and keep role `caller`.

4.3 Warnings that drop or alter content are a refusal (`test_voicera_reader_refuses_dropped_lines_by_default`). After the read, if any warning has a code outside `NON_REFUSING_WARNING_CODES` and `accept_warnings` is false, `VoicEraRefusedError` is raised. The refusing codes F8 emits are `empty_input`, `header_not_recognized`, `delimiter_not_found`, `unparsed_line`, `unknown_enum_value`, `text_not_valid_unicode` and `malformed_entry`; any code not in the frozenset refuses, including a code a later version adds (default deny). The four non-refusing codes lose nothing and are returned in `warnings`. With `accept_warnings=True` the same input is read and all warnings are returned. `accept_warnings` is keyword-only, `bool` exactly (`"yes"`, `1`, `None` are a `TypeError`), default `False`. The refusal's `warnings` holds every warning of the read, harmless ones included, in order (it quotes dropped lines in `raw`); its message holds only the codes and source pointers (`f8:3/speaker`), at most 20 of them and then "and N more", and never a raw line: the test plants a sentinel in each dropped line and in a 500-warning input, and checks `str`, `repr`, and the length of the message (under 5,000 characters).

4.4 Verbatim text and order (`test_voicera_reader_keeps_text_verbatim_and_seq_order`): `Turn.text` is the text after `<speaker>:` and one optional space, as written: no strip, no case change, no Unicode normalisation (the NFC-unstable `U+0958` keeps its code point), joiners and bidi controls kept, an empty text kept, a 10,000-character text kept. `seq` follows the order of the lines, never of the stamps (a later line with an earlier stamp keeps its place); lines that make no turn leave no gap. 5,000 lines are read whole.

4.5 The declared-language argument (`test_voicera_reader_declared_language_only_from_argument`): `declared_language` is a `LanguageDeclaration` or a mapping the model accepts; it is the only source of `Transcript.declared_language` (absent argument or `None` gives `None`; language tags inside the export change nothing). The caller's own arguments are validated first, whatever the export holds, with the schema's `ValueError` (a pydantic `ValidationError`) and not a refusal: an unsupported language, a `source` other than `user_declared`, an unparseable `declared_at`, a missing member, a `privacy` that is `None`, malformed or a human-subject marker without consent and retention, and a `created_at` that is not an instant; each with the export non-empty, empty, and junk. A `str` is required for `text` (`bytes`, `Path`, `None`, `int` are a `TypeError`): the caller decodes (section 10 item 8).

4.6 Deviation from F8: a call id with a control or bidi character is refused, always (`test_voicera_reader_rejects_bidi_in_call_id_safely`, 17 characters). F8 keeps such a value in `provider_call_ref` and warns `call_id_not_uuid`; the shipped reader raises `VoicEraRefusedError` for a header value containing any of U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F, U+061C or any character of category `Cc` (C0 controls, DEL, C1 controls, NUL), whether or not `accept_warnings` is true, because a call id is shown in the CLI and the report and bidi characters let one identifier pose as another. The message names the code `call_id_unsafe_characters` and the pointer `f8:1/call_id` and nothing of the identifier: not the character, not any part of the value (`str`, `repr`, `args`, and the log records at DEBUG are checked); `refusal.warnings` carries no `raw` that holds the identifier. The same identifier without the character is an ordinary non-UUID call id (`call_id_not_uuid`, kept verbatim). This is the one place where the shipped mapping differs from F8, and the differential corpus has no such input.

4.7 `VoicEraRead` is a frozen dataclass with exactly the four fields above, in that order; two reads of the same text differ only in their ids.

## 5. P3-05: the deterministic chunker

Module `tracer_evalcore.audio.chunker`. Names: `chunk_pcm`, `chunk_wav`, `Chunk`, `ChunkPlan`, `DEFAULT_MAX_CHUNK_MS = 25_000`, `DEFAULT_SEARCH_MS = 5_000`, `ChunkerError`, `ChunkerParameterError`, `ChunkerInputError`, `ChannelsRefusedError`. Error bases: `ChunkerError(InputRefusedError)`; the other three are direct subclasses of `ChunkerError`.

```python
def chunk_pcm(
    pcm: bytes, *,
    sample_rate_hz: int, channels: int,
    max_chunk_ms: int = 25_000, search_ms: int = 5_000,
    accept_provider_downmix: bool = False,
) -> ChunkPlan

def chunk_wav(audio: WavAudio, *, max_chunk_ms: int = 25_000, search_ms: int = 5_000,
              accept_provider_downmix: bool = False) -> ChunkPlan   # chunk_pcm over audio.pcm

@dataclass(frozen=True)
class Chunk:        # index, start_frame, end_frame, sample_rate_hz, channels, wav (repr=False)
    frames: int                       # property: end_frame - start_frame
    duration_seconds: Decimal | None  # property: exact_seconds(frames, sample_rate_hz)
    start_seconds: Decimal | None     # property: exact_seconds(start_frame, sample_rate_hz)

@dataclass(frozen=True)
class ChunkPlan:    # sample_rate_hz, channels, max_chunk_ms, search_ms, total_frames, chunks: tuple[Chunk, ...]
    def as_metadata(self) -> dict[str, Any]
```

5.1 The cut rule (ADR 0024 rule 7, restated so that it can be implemented and tested; ambiguities resolved in section 10 items 2 and 3). With `F = rate * 20 // 1000` sample frames per 20 ms frame, `W = rate * max_chunk_ms // 1000` the window (a floor, so a chunk never exceeds `max_chunk_ms`) and `S = rate * search_ms // 1000` the search region, all in sample frames (one sample per channel):

* a window starts at the current start (initially 0); if `total - start <= W` the rest is the last chunk; otherwise the window `[start, start + W)` is cut;
* the frames of the window are counted from the window start: frame `k` is `[start + k*F, start + (k+1)*F)` for `k = 0 .. W // F - 1`; frame `k` is a candidate iff it lies whole inside the last `S` frames of the window: `k*F >= W - S` and `(k+1)*F <= W`;
* the energy of a frame is the exact integer sum of the squares of all its samples over all channels; the lowest energy wins and a tie goes to the latest frame;
* the cut is the winning frame's middle sample frame, `start + k*F + F // 2`; the chunk is `[start, cut)` and the next start is `cut`;
* chunks cover every sample frame exactly once, in order, none empty; audio of at most `W` frames is one chunk; zero frames give zero chunks (`chunks == ()`, `total_frames == 0`, nothing to send).

Pinned by hand-made signals (a quiet frame inside, just before and just after the region, a frame beyond the window, magnitude against energy, ties, a second window whose grid is half a frame off the first, two channels, the smallest window) and by a plain-Python oracle in the test file on 24 seeded random signals over rates 8000 to 44100, one and two channels, windows from 100 ms to 1.5 s (`test_chunker_cuts_at_lowest_energy_frame`, `test_chunker_matches_the_reference_on_seeded_random_signals`).

5.2 Determinism and coverage (`test_chunker_is_deterministic`, `test_chunker_covers_every_sample_once`, `test_chunker_never_exceeds_max_chunk_ms`, `test_chunker_short_audio_is_one_chunk`): the same input gives equal plans and byte-identical chunk files, also in a fresh interpreter with another hash seed (sha256 of the joined chunk files compared); the chunk files joined give back the input's samples (12 parametrizations: window exactly full, one frame over, a floor window of 44100 Hz x 333 ms, the defaults on 700,001 frames at 24 kHz, stereo); `frames * 1000 <= rate * max_chunk_ms` for every chunk; `DEFAULT_MAX_CHUNK_MS` and `DEFAULT_SEARCH_MS` are the signature defaults.

5.3 The chunk file: a canonical PCM WAV of 44-byte header and the chunk's samples, byte for byte `RIFF`, `36 + n`, `WAVE`, `fmt `, 16, 1, channels, rate, `rate*channels*2`, `channels*2`, 16, `data`, `n`, samples (`test_chunk_wav_bytes_are_the_canonical_pcm_file`); parseable by the standard library `wave`.

5.4 Stereo (`test_chunker_stereo_is_not_downmixed_silently`; ADR 0024 Ambiguity 3): two channels are refused with `ChannelsRefusedError` unless `accept_provider_downmix=True`, checked before the audio is looked at (an empty stereo input is refused too); with the opt-in the chunks are stereo and the concatenated sample bytes of the chunks equal the input exactly (Tracer computes no downmix; the provider merges the channels); the energy is over both channels; three or more channels are refused even with the opt-in; the flag has no effect on mono. The flag name `accept_provider_downmix` is this contract's choice (SCOPE: "unless an explicit downmix is requested"; ADR 0024: "the flag name is the CLI contract's"): the CLI flag of P3-22 sets it.

5.5 Parameters (`test_chunker_parameters_are_validated`): `sample_rate_hz`, `channels`, `max_chunk_ms`, `search_ms` are `int`, not `bool`: otherwise `ChunkerParameterError`; `sample_rate_hz >= 1`, `channels >= 1`, `max_chunk_ms >= 1`, `search_ms >= 0`, `search_ms <= max_chunk_ms`. When a cut is needed and no whole 20 ms frame lies in the search region (`search_ms` 0 or 19 at 8 kHz; a window shorter than one frame), `ChunkerParameterError`; audio that needs no cut never looks at the region. `pcm` must be `bytes` (`TypeError` for `str`, `list`, `None`, `int`) and a whole number of 16-bit sample frames (`ChunkerInputError`). Keyword-only: everything except `pcm`; `sample_rate_hz` and `channels` have no default.

5.6 The plan record (`test_chunk_plan_metadata_is_integers_only_and_fits_a_transcript`, ADR 0024 rule 7): `ChunkPlan.as_metadata()` returns a fresh `dict` with exactly the keys `format` (`"tracer.chunk_plan/1"`), `max_chunk_ms`, `search_ms`, `sample_rate_hz`, `channels`, `total_frames` and `chunks`, a list of `{index, start_frame, end_frame}`; every number an `int`; `json.loads(json.dumps(m)) == m`; accepted as `Transcript.metadata["tracer.chunk_plan"]` with no `metadata_flags`. Which metadata key batch B uses is its decision (section 10 item 6); the document is this one. `Chunk.duration_seconds` and `start_seconds` follow section 3.2 (`test_chunk_durations_are_exact_or_absent`). `chunk_wav` is `chunk_pcm` over the reader's `pcm`, rate and channels (`test_chunk_wav_reads_the_audio_of_the_reader`).

## 6. P3-02: licence policy blocks and the checker

The blocks of P3-02-licenses.md section 9 with two corrections (section 11, F-A5): the closure of a package is its whole third-party closure (the evaldb precedent), so `evalproviders` lists the evalschema closure as well, and `requires_dist` lists the workspace pins. Format as `[runtime.evaldb]`: exactly the keys `closure` (sorted, workspace packages left out) and `requires_dist`.

```toml
[runtime.evalcore]
closure = ["annotated-types", "pydantic", "pydantic-core", "rfc8785", "typing-extensions", "typing-inspection"]
requires_dist = ["tracer-evalschema==0.0.1"]

[runtime.evalproviders]
closure = ["annotated-types", "anyio", "certifi", "h11", "httpcore", "httpx", "idna", "pydantic",
           "pydantic-core", "rfc8785", "typing-extensions", "typing-inspection"]
requires_dist = ["tracer-evalschema==0.0.1", "tracer-evalcore==0.0.1", "httpx>=0.28.1,<1"]

[runtime.cli]
closure = ["alembic", "annotated-types", "anyio", "certifi", "h11", "httpcore", "httpx", "idna", "mako",
           "markupsafe", "pydantic", "pydantic-core", "rfc8785", "sqlalchemy", "typing-extensions",
           "typing-inspection"]
requires_dist = ["tracer-evalschema==0.0.1", "tracer-evalcore==0.0.1",
                 "tracer-evalproviders==0.0.1", "tracer-evaldb==0.0.1"]

[[rejected]]   # name, spdx, reason (non-empty), as the two psycopg entries
name = "sarvamai"    # spdx = "none declared"
name = "requests"    # spdx = "Apache-2.0", the reason says it is not a licence rejection

[[exceptions]]
name = "certifi"
version = "2026.10.23"          # the lock's version; every bump re-triggers review
spdx = "MPL-2.0"
scope = "runtime"
groups = ["dev"]
license_file = "licenses/LICENSE"
license_file_sha256 = "e93716da6b9c0d5a4a1df60fe695b370f0695603d21f6f83f053e42cfc10caf7"
decided_by = "project owner (standing rule: do not dwell on copyleft; accepted by name 2026-10-25)"
reason = "File-level weak copyleft. Used unmodified as a transitive dependency of httpx and httpcore, never vendored ..."
```

Pinned by `test_license_policy_lists_phase3_closures`: each block's `closure` equals the sorted lock closure minus the workspace packages (so a `uv lock` that adds a package fails until the policy is edited); `requires_dist` equals the section 1 requirements as parsed sets; the `[runtime]` and `[runtime.evaldb]` blocks are unchanged; the exception set is exactly `{hypothesis, pathspec, certifi}` (spec change, section 8.3); certifi's version equals the lock's and the installed one, `groups == ["dev"]`, `scope == "runtime"`, `license_file == "licenses/LICENSE"`, and its `license_file_sha256` equals the sha256 of the licence file of the installed distribution (read, not assumed); `decided_by` names the project owner; no override exists for httpx, httpcore, h11, anyio, idna or certifi (their metadata clears them); `sarvamai` and `requests` are in `[[rejected]]` with a non-empty `spdx` and `reason` and are not in the lock; the allowlist has no GPL-family term and does not contain `MPL-2.0`. `test_phase3_closures_are_not_copyleft_and_licence_files_are_read`: no installed package of the three closures declares a GPL-family licence or classifier, and the licence file of httpx, httpcore, idna (BSD "Redistribution and use"), h11, anyio (MIT "Permission is hereby granted") and certifi (Mozilla Public License) is present and reads as that text.

`scripts/check_licenses.py` (`test_check_licenses_covers_phase3_packages`; reference change in `oracle-src.patch`, about 50 lines: `PHASE3_PROJECTS`, `Policy.phase3_closure` / `phase3_requires_dist`, `phase3_findings`, per-package `requires_dist` in `check_artifact`): exit 0 and `0 failed` on the real policy; exit 1 naming `tracer-evalcore` and the entry when `[runtime.evalcore].closure` gains an entry the lock lacks; exit 1 naming `tracer-evalproviders` and `httpx` when `httpx` is dropped from its closure; exit 1 naming `tracer-cli` and `sqlalchemy` when that is dropped from the cli closure; exit 1 naming `certifi` when the exception entry is removed (class `mpl` outside the exception list); `--wheel <dir> --artifacts-only` on the three built wheels prints `3 ok, 0 failed` (`Requires-Dist` compared with `[runtime.<key>].requires_dist` as normalised requirements; no extras); exit 1 with `Requires-Dist` and the wheel name `tracer_evalproviders` in the message when the policy lists the looser range `httpx>=0.28.1`. `test_phase3_wheels_do_not_name_certifi`: no tracer wheel lists `certifi` in `Requires-Dist` or ships a top-level `certifi`, `httpx` or `httpcore` directory.

## 7. P3-01: CI

All statements are about `.github/workflows/ci.yml`; no test starts a process. Every `uses:` stays pinned by a 40-hex commit (Phase 1 rule), workflow `permissions` stay `contents: read`, no job has `services:`.

7.1 `licenses` job (`test_ci_phase3_wheels_are_built_and_checked`): its run steps contain `uv build --package tracer-evalschema`, `tracer-evaldb`, `tracer-evalcore`, `tracer-evalproviders` and `tracer-cli` (each `--out-dir dist`), all before `check_licenses.py --wheel dist --artifacts-only`, and the environment scan `uv run --locked --all-groups python scripts/check_licenses.py` still runs.

7.2 The existing legs (`test_ci_phase3_test_legs_run_the_new_directories`): `test` (3.11 to 3.14, required) and `test-py315` (non-blocking) keep running exactly `uv run --locked --all-groups pytest`, so the five `testpaths` of section 8 decide what runs; `lint` still runs bare `ruff check`, `ruff format --check`, `mypy` and `scripts/check_headers.py`.

7.3 New job `test-oldest-phase3-deps` (`test_ci_lower_bound_leg_for_phase3_ranges`), the lower-bound leg for SCOPE D3: `name` exactly `test (lowest-direct phase 3 deps, py3.11)`, `runs-on: ubuntu-latest`, required (no `continue-on-error`), no `services`; setup-uv with `python-version: "3.11"` and `version: ${{ env.UV_VERSION }}`; exactly two run steps, `uv lock --resolution lowest-direct` and then `uv run --locked --resolution lowest-direct --all-groups pytest packages/evalcore/tests packages/evalproviders/tests packages/cli/tests --deselect packages/evalcore/tests/test_phase3_licenses.py --deselect packages/evalcore/tests/test_phase3_scaffold.py::test_phase3_locked_closures_are_the_reviewed_ones` (the two places that compare the installed versions or the closure with the committed lock; the Phase 2 pattern); never `--resolution lowest` (full). No `docker`, no `POSTGRES`, no `TRACER_TEST_POSTGRES_URL`, no `SARVAM`, no `secrets.` in the job. The Phase 2 job `test-oldest-evaldb-deps` and its name are untouched.

7.4 The lower-bound statement per the licence review (`test_ci_has_no_httpx_specific_job`; P3-02 section 8): the only third-party range of Phase 3 is `httpx>=0.28.1,<1`; its lowest-direct resolution is its highest one (0.28.1 is the newest stable release, uploaded 2024-12-06), so a job for httpx would be a tautology and none exists. The statement is testable as: no job key, job name or step name contains `httpx`, no `run` or `with` text of any step mentions it, and no line outside comments pins it by hand (`httpx==`, `httpx>=`). The range is covered by the leg of 7.3, which lowers every direct dependency of the workspace together. The leg becomes informative the day httpx 0.28.2 or 1.0 ships; until then the CHANGELOG says so (P3-32).

7.5 Pinned actions and images (`test_ci_phase3_actions_pinned_and_no_new_image`): every `uses:` in the file matches `<owner>/<repo>@<40 hex>`; the actions of the new job are a subset of those of the `test` job (no new action, so no new pin to review); the only container images in the file are the Postgres images with a `@sha256:` digest; no key reaches the workflow (`secrets.`, `SARVAM`, `api-subscription-key` appear nowhere outside comments).

## 8. Required changes to the repository (qa did not make them)

8.1 Root `pyproject.toml`:

```toml
[dependency-groups]
dev = [ ..., "tracer-evaldb[postgres]", "tracer-evalcore", "tracer-evalproviders", "tracer-cli", ... ]
        # the three must be in dev: certifi's exception is "reachable from group dev" only through them

[tool.uv.sources]
tracer-evalcore = { workspace = true }
tracer-evalproviders = { workspace = true }
tracer-cli = { workspace = true }

[tool.ruff]
src = [ ...existing..., "packages/evalcore/src", "packages/evalcore/tests", "packages/evalproviders/src",
        "packages/evalproviders/tests", "packages/cli/src", "packages/cli/tests", "scripts" ]

[tool.mypy]
files = [ ...same six paths... ]
mypy_path = "packages/evalschema/src:packages/evaldb/src:packages/evalcore/src:packages/evalproviders/src:packages/cli/src"

[tool.pytest.ini_options]
testpaths = ["packages/evalschema/tests", "packages/evaldb/tests", "packages/evalcore/tests",
             "packages/evalproviders/tests", "packages/cli/tests"]
```

`addopts` and `markers` stay as they are. Also: `uv lock` (adds anyio, certifi, h11, httpcore, httpx, idna and the three workspace packages; `no-build-package` needs no entry, all new packages are pure Python); `docs/security/license-policy.toml` (section 6); `scripts/check_licenses.py` (section 6); `.github/workflows/ci.yml` (section 7); `docs/phase3/evidence/evalschema-baseline.json` (section 1.7); the package sources and `pyproject.toml` files (section 1). Reference implementation: `docs/phase3/evidence/red/batch-a/oracle-src.patch` (everything except the tests and `uv.lock`; the lock diff is `oracle-uv.lock.patch`): qa's scratch oracle that makes the tests pass; the dev owns the real code and may differ.

8.2 No change to `packages/evalschema/src`, `packages/evaldb/src` or any `docs/adr` file.

8.3 SPEC CHANGES TO TWO EXISTING TESTS (named; qa did not edit them; the lead decides; SCOPE gate item 3 requires before and after). Both fail the moment the section 8.1 and 6 changes are made, and cannot be satisfied otherwise: the first because the certifi exception is the only way the licence checker accepts an MPL-2.0 runtime dependency (P3-02), the second because the CI legs run the bare command and the new test directories can only be reached through `testpaths`.

| Test | File | Before | After |
|---|---|---|---|
| `test_license_exceptions_exact` | `packages/evalschema/tests/test_repo_contract.py:295` | `assert set(exceptions) == {"hypothesis", "pathspec"}` | `assert set(exceptions) == {"hypothesis", "pathspec", "certifi"}`, plus `exceptions["certifi"]["spdx"] == "MPL-2.0"` and `["groups"] == ["dev"]`; every other line (version pinned to the lock, disjoint from the runtime closure) unchanged |
| `test_ci_sqlite_runs_on_every_python_leg` | `packages/evaldb/tests/test_evaldb_ci.py:230` | `ini["testpaths"] == ["packages/evalschema/tests", "packages/evaldb/tests"]` | `ini["testpaths"] == [` those two, then `"packages/evalcore/tests", "packages/evalproviders/tests", "packages/cli/tests"]`; every other line unchanged |

The oracle applies exactly these two edits (`oracle-spec-change.patch`) and then the whole suite passes (section 9). Phase 3's own tests assert the new values (`test_license_policy_lists_phase3_closures`, `test_phase3_scaffold_files_and_repo_config`), so the change cannot be forgotten once made. SCOPE section A names `test_license_exceptions_exact` as "existing, unchanged" for P3-02: that is not satisfiable with the certifi exception SCOPE also requires.

## 9. Test inventory

Files (staged): `evalcore/tests/test_phase3_scaffold.py` (16 collected), `test_phase3_licenses.py` (6), `test_phase3_ci.py` (5), `test_wav_reader.py` (24), `test_voicera_reader.py` (61), `test_chunker.py` (50); `evalproviders/tests/test_evalproviders_scaffold.py` (1); `cli/tests/test_cli_scaffold.py` (2). 165 collected, all run by default (no marker, no database, no network).

All 28 names of SCOPE A.0 (P3-01) and A.1 (P3-03 to P3-05) exist under their exact names (a parametrized test keeps its name, pytest appends the case): P3-01: `test_evalcore_runtime_imports_whitelist`, `test_evalcore_imports_no_network_module`, `test_evalproviders_runtime_imports_whitelist`, `test_cli_runtime_imports_whitelist`, `test_evalschema_does_not_import_phase3_packages`, `test_phase3_requires_dist_bounded_ranges` (3 cases), `test_phase3_wheels_contain_no_tests` (3), `test_evalschema_unchanged_since_phase3_baseline`, `test_tracer_console_script_runs_help`; P3-03: `test_wav_reader_reads_mono_24k_16bit_exact_duration`, `test_wav_reader_rejects_non_pcm_and_8_or_24_bit`, `test_wav_reader_rejects_truncated_and_lying_header`, `test_wav_reader_rejects_oversize_before_reading`, `test_artifact_uri_is_absolute_file_uri`, `test_artifact_privacy_marker_requires_explicit_consent_input`; P3-04: `test_voicera_reader_equals_f8_mapper_on_public_cases` (38), `test_voicera_reader_refuses_dropped_lines_by_default`, `test_voicera_reader_keeps_text_verbatim_and_seq_order`, `test_voicera_reader_maps_assistant_agent_user_caller`, `test_voicera_reader_never_sets_language_or_timing`, `test_voicera_reader_declared_language_only_from_argument`, `test_voicera_reader_rejects_bidi_in_call_id_safely` (17); P3-05: `test_chunker_is_deterministic`, `test_chunker_covers_every_sample_once` (12), `test_chunker_never_exceeds_max_chunk_ms` (5), `test_chunker_cuts_at_lowest_energy_frame`, `test_chunker_short_audio_is_one_chunk`, `test_chunker_stereo_is_not_downmixed_silently`. From P3-02, `test_license_policy_lists_phase3_closures`. (`test_no_lgpl_or_gpl_in_lock` and `test_license_exceptions_exact` are the existing tests; the second needs the spec change of 8.3.)

Added by qa (each makes a contract item testable): `test_phase3_scaffold_files_and_repo_config`, `test_phase3_locked_closures_are_the_reviewed_ones` (3), `test_normaliser_fixture_moved_unchanged`, `test_evalcore_version_is_the_distribution_version`, `test_phase3_closures_are_not_copyleft_and_licence_files_are_read`, `test_check_licenses_covers_phase3_packages`, `test_phase3_wheels_do_not_name_certifi` (3), `test_ci_phase3_wheels_are_built_and_checked`, `test_ci_phase3_test_legs_run_the_new_directories`, `test_ci_lower_bound_leg_for_phase3_ranges`, `test_ci_has_no_httpx_specific_job`, `test_ci_phase3_actions_pinned_and_no_new_image`, `test_wav_reader_signature_and_result_type`, `test_wav_audio_is_frozen_and_does_not_print_samples`, `test_wav_reader_ids_and_clock`, `test_wav_reader_works_without_a_network`, `test_wav_reader_refuses_non_regular_files_without_reading`, `test_exact_seconds_is_exact_or_none` (12), `test_exact_seconds_validates_its_arguments`, `test_voicera_reader_result_is_a_frozen_value`, `test_chunker_matches_the_reference_on_seeded_random_signals` (24), `test_chunk_wav_bytes_are_the_canonical_pcm_file`, `test_chunker_parameters_are_validated`, `test_chunk_plan_metadata_is_integers_only_and_fits_a_transcript`, `test_chunk_durations_are_exact_or_absent`, `test_chunk_wav_reads_the_audio_of_the_reader`.

Three tests pass on the current tree by nature (guards; their teeth are mutants): `test_ci_phase3_test_legs_run_the_new_directories` (the bare-command legs already exist; mutant I10), `test_ci_has_no_httpx_specific_job` (mutants I6, I15) and `test_evalschema_does_not_import_phase3_packages` (evalschema imports none of them today; mutants P10, P11).

## 10. Ambiguities, with recommendations (the lead auto-approves; qa implemented the recommendation in the tests)

1. Where the tests live until P3-01: staged outside `packages/`, one folder per package (section 0). Recommendation: the dev moves them in the P3-01 commit.
2. The candidate frames of ADR 0024 rule 7 ("the frames there are the 20 ms frames counted from the window start") do not say whether a frame that straddles the start of the last `search_ms` counts, nor what happens when no frame fits. Recommendation (pinned): only whole frames inside the region are candidates (`k*F >= W - S` and `(k+1)*F <= W`); no whole frame in the region when a cut is needed is a `ChunkerParameterError` (a refusal, not a silent fixed cut). Rejected: partial frames (an unmeasured number); a fixed cut at the window end (a word split with no search).
3. `W`, `F`, `S` use integer floors (`rate * ms // 1000`), so `max_chunk_ms` is never exceeded for rates such as 22050 Hz x 750 ms (16,537.5 frames). Recommendation (pinned): floor. Rejected: rounding (a chunk of 16,538 frames is longer than 750 ms).
4. Stereo: SCOPE says "refused unless an explicit downmix is requested", ADR 0024 Ambiguity 3 says the opt-in only accepts the provider's merge and Tracer never computes a downmix. Recommendation (pinned): `accept_provider_downmix: bool = False`, chunks keep both channels unchanged, three or more channels refused always. Rejected: an in-Python mean of the channels (the provider already merges; a second merge would be unmeasured).
5. `exact_seconds` returns `None` for non-terminating or more than 18-digit quotients (finding F-A1). Recommendation (pinned): `Decimal | None`, never rounded. Batch B must decide how a UsageRecord's `audio_input_seconds` is written for a chunk whose duration is `None` (section 11, F-A1).
6. Which `Transcript.metadata` key holds the chunk plan. Recommendation: `tracer.chunk_plan` with the value of `ChunkPlan.as_metadata()` (the test uses it; the `format` member inside the value already versions it). Batch B pins the key when it writes the P3-09 contract.
7. The RIFF size field is ignored; only the `data` chunk size is compared with the file. Recommendation: ignore (many writers leave it stale or `0xFFFFFFFF`); the data size is the claim that matters and it is checked. No test pins either reading. The extensible format tag (0xFFFE) is refused even with a PCM sub-format, so that the result does not depend on the Python version (`wave` reads it from 3.12 only): pinned.
8. Decoding the export file (UTF-8, byte order mark, a size cap) is not the reader's job: `read_voicera_export` takes decoded `str`. Recommendation: P3-22 reads the file with a size cap and strict UTF-8 and refuses a decoding error; qa writes that test in batch E. A `read_voicera_file` helper is deliberately not in the contract.
9. `human_subject` of the Artifact is always `True` (ADR 0023 rule 16: the unknown-role STT turns make the run human-subject whatever the caller origin). Recommendation (pinned). A synthetic recording would still carry consent and retention; `consent.status="not_obtained"` or `"unknown"` are the honest values then.
10. Refusing codes by default deny: any warning code not in `NON_REFUSING_WARNING_CODES` refuses, including `text_not_valid_unicode` (the reader alters the text with U+FFFD) and `malformed_entry`. Recommendation (pinned). Rejected: listing the refusing codes (a code F8 adds later would be accepted silently).
11. `tracer_cli` imports no third-party package directly (section 2.3). If P3-21 needs a SQLAlchemy name for typing, the dev adds `sqlalchemy` to the whitelist consciously (and to the cli's declared dependencies), with a test edit named in the commit. Recommendation: pass the store as a URL string and use `tracer_evaldb`'s own API.
12. evalproviders depends on `tracer-evalschema` and `tracer-evalcore` (P3-02 open item 1) and the cli forwards no `[postgres]` extra (open item 2): both resolved as pinned in section 1. The cli ships no template, script or font (open item 3): the HTML report is generated text (P3-23); a bundled third-party file would be a new licence review.
13. The licence file hash in the certifi exception is read from the installed distribution by the test, so a certifi bump needs the version and the hash edited together (stale-exception check of the script, intended).
14. The Phase 1 T-DRIFT scan reads every committable text file under `docs/` and `packages/evalschema/`: the staged tests and this contract avoid the tokens it matches (the scan was run: 54 passed on the tree with the staged files). The lead re-runs the Phase 1 suite after each Phase 3 document is committed.

## 11. Findings (read first)

F-A1. **"Duration = frames/rate as an exact Decimal" (SCOPE P3-03) cannot hold in general.** `frames / rate` terminates in decimal only when `rate / gcd(frames, rate)` has no prime factor other than 2 and 5; at 24 kHz (`2^6 * 3 * 5^3`) that is one chunk length in three, and at 44.1 kHz almost never. A cut chunk of the base case has an arbitrary frame count, so most chunk durations have no finite decimal, and `DecimalString` carries at most 18 fractional digits. ADR 0024 rule 13 says the UsageRecord's `audio_input_seconds` is "the chunk's exact duration ... as a canonical decimal string": not representable for those chunks. This contract returns `Decimal | None` (never rounded). Recommendation for batch B and the ADR: record `audio_input_seconds` as the quotient rounded half to even to 18 fractional digits (an error under 5e-19 s, below any billing resolution) and state the rounding in the UsageRecord's metadata and in ADR 0024 rule 13; the chunk plan keeps the exact integers (`start_frame`, `end_frame`, `sample_rate_hz`), so the exact value is always recoverable. Rejected: a float (a different value on different machines); refusing the chunk (almost every chunk); cutting only at frame counts whose duration terminates (changes the cut rule of ADR 0024 rule 7 for a representation problem). The lead decides before P3-09.

F-A2. **Two existing tests need a named spec change** (section 8.3). The oracle ran the whole workspace suite on Python 3.13 with the Phase 3 files added: 2 failed, 10,717 passed; the two failures are exactly these.

F-A3. **A bounded reader must not size its buffer by the cap.** `read(max_bytes + 1)` allocates the cap for any file (1 GiB for a 50-byte file; tracemalloc shows it). The tests measure the peak with a tiny file under the default cap and with a lying header (mutant W15 is caught).

F-A4. **The bidi call id deviation from F8** (section 4.6). "Identical mapping" holds on every F8 case in the corpus; the one deliberate difference is the refusal of control and bidi characters in the call id, which F8 stores and warns about.

F-A5. **P3-02 policy blocks** differ from the licence review in two places, both implied by its open items: the evalproviders closure lists the whole third-party closure (12 packages, because the package depends on evalschema and evalcore) rather than the seven of httpx alone, and `requires_dist` lists the workspace pins. The certifi hash in the review (`e93716da...`, 2026.10.23) was verified: the oracle's checker run (`62 ok, 0 failed`) re-reads it from the real wheel in the lock.

F-A6. **Reading the Python 3.14 and 3.15 legs**: the oracle and the red runs cover 3.11 to 3.14; 3.15 is non-blocking and not run here (section 12).

## 12. Evidence (`docs/phase3/evidence/red/batch-a/`)

Counts (final, 2026-10-25):

* red, on the repository without the three packages (Phase 2 tree plus the Phase 3 ADRs), staged tests, own environment per Python (`red.py3.11.log` to `red.py3.14.log`, `--tb=line`): **162 failed, 3 passed** on 3.11, 3.12, 3.13 and 3.14. `red-reasons.py3.13.txt` groups the 162 failures: 136 `ModuleNotFoundError` (the package does not exist), 14 `FileNotFoundError`/`KeyError` on the missing `packages/` directories, 8 assertions that name the missing job, package, console script, fixture or module, and 4 other absences; nothing else. The 3 that pass are the guards named in section 9.
* oracle, the same tests moved to `packages/*/tests` of a scratch copy that holds a hand-written minimal implementation of the three packages (`oracle-src.patch`, about 1,450 lines with the checker, policy, workflow and root `pyproject.toml` changes; `oracle-uv.lock.patch`, 200 lines, from a real `uv lock` on 2026-10-25): **165 passed** on 3.11, 3.12, 3.13 and 3.14 (`oracle.py3.*.log`); the whole workspace suite with the two spec changes of section 8.3 (`oracle-spec-change.patch`): **10,723 passed, 1,287 deselected** (10,558 earlier tests plus the 165) on 3.11, 3.13 and 3.14 (`oracle-full-suite.py3.*.log`, 3.12 ran the Phase 3 directories only); without the two spec changes: 2 failed, 10,717 passed on 3.13 (`oracle-full-suite-before-spec-change.py3.13.log`, taken when the Phase 3 directories held 163 tests): exactly the two tests of section 8.3. `oracle-gates.py3.13.log`: `ruff check`, `ruff format --check`, `mypy` (strict, 251 files including the moved tests), `check_headers.py`, `guard_private_fixtures.py`, `check_licenses.py` (62 ok, 0 failed, certifi accepted by its pinned exception), `--wheel ... --artifacts-only` on the five wheels (5 ok, 0 failed), the schema export and field-doc checks, and the installed `tracer --help`.
* mutants (`mutants.txt`): **139 mutants, 139 caught, 0 survived** on the final tests: 27 in the WAV reader and `exact_seconds`, 27 in the chunker, 25 in the VoicEra reader, 35 in the scaffold and import rules, 10 in the licence policy and checker, 15 in the workflow. The first run left 7 survivors; each one changed a test (the history is in the header of the file).
* the staged tests alone: `ruff check`, `ruff format --check`, `mypy --strict` pass in the repository as they are; the Phase 1 T-DRIFT test (54 passed) and `scripts/guard_private_fixtures.py`, `scripts/check_headers.py`, `scripts/check_licenses.py` (53 ok) pass on the tree with the staged files and this contract; the files are ASCII.

Not run: Python 3.15 (non-blocking leg); GitHub-hosted execution of the new CI job; a Windows path; the Phase 1 to 3 suites against Postgres (nothing in this batch touches a database).

Reading the reference: `oracle-src.patch` contains the three packages' `pyproject.toml`, `README.md` and sources (without the copied `LICENSE` and `NOTICE`), the root `pyproject.toml`, `scripts/check_licenses.py`, `docs/security/license-policy.toml`, `.github/workflows/ci.yml` and `docs/phase3/evidence/evalschema-baseline.json`. Gaps and risks for the gate checklist: no test pins the RIFF size field or the shape of the CLI beyond `--help` (P3-22); the sparse-file test needs a file system with sparse files (every GitHub-hosted Linux runner and macOS APFS have them); the chunker tests use `SIGALRM` where it exists (POSIX); the lower-bound leg is a tautology for httpx until a release other than 0.28.1 exists (section 7.4); no test of the real Sarvam behaviour on 24 kHz or over 30 s exists or can exist offline (owner-gated, P3-30).
