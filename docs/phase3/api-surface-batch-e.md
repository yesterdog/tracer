# Phase 3 batch E contract: the score command, the HTML report, the adversarial suite (P3-22 to P3-24)

Author: qa, 2026-10-26. Source: `docs/phase3/SCOPE.md` (A.5, Decisions D1 to D11, Q12, F9), ADR 0023 (rules 1, 11, 14 to 17, invariant I1), ADR 0024 (rules 3 to 5, 15, 16), ADR 0016 and ADR 0017 with `docs/concepts/display-safety.md` (`display_safe`), the contracts of batches A to D and the implemented `tracer-evalcore`, `tracer-evalproviders` and `tracer-cli` (`store.py` and the scaffold `cli.py`), `docs/examples/rubric-mr.example.json`, and the decision log (the PHASE 3 and OWNER ANSWERS entries). qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test. Every statement below is pinned by a test named in section 9, unless it says "not pinned".

No schema change (SCOPE D5). Batch E adds no record kind: the command builds the batch D bundle and the report reads it.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): nothing was created under `packages/` and nothing under it was edited. The tests are staged at

```text
docs/phase3/evidence/red/batch-e/staging/tests/cli/  -> packages/cli/tests/   (4 files)
```

No `__init__.py`, no `conftest.py` (each test module holds its own autouse no-network guard). The dev's first act in batch E:

```text
git mv docs/phase3/evidence/red/batch-e/staging/tests/cli/*.py packages/cli/tests/
```

and, in the same commit, the two PENDING tightenings of section 10. There is no `staging/data/` directory: batch E needs no data file (the example rubric of batch D is the product file, and every input of a test is written by the test).

| File | Holds | Functions | Collected |
|---|---|---|---|
| `cli_batche_support.py` | helpers (not collected): lazy module loaders, input writers, `run_cli` (the in-process run), the replay directory writer, `SarvamScript` (an `httpx.MockTransport`), the user price file, a small HTML tree builder, the structural and external-request scanners | - | - |
| `test_cli_score.py` | P3-22 and the rules of the command (parser, exit codes, refusals, dry run, files, Sarvam path, replay, help, source rules, the SCOPE-name guard) | 52 | 66 |
| `test_cli_report.py` | P3-23 (structure, CSP, escaping, bidi, highlights, STT text beside each flag, audio, deny-list, language, paths, key canary, usage, budget, not scored) | 33 | 33 |
| `test_cli_adversarial.py` | P3-24 (one parametrized test over 24 inputs, and the specific hostile Sarvam, path, rubric and WAV tests) | 9 | 32 |
| total | | 94 | 131 |

(The counts are those of the final staged files; the evidence is in section 11.) Nothing of the batch E modules is imported at module level: each test loads `tracer_cli.cli`, `.report`, `.summary` or `.replay` lazily with `importlib`, so before the modules exist every test fails on its own with `ModuleNotFoundError` (or on the scaffold's missing names) and the directory still collects. The support module loads the evalcore batch D support file by path (the fake engine, the base scenario) through `cli_batchd_support`, which therefore must be in place first (it is: batch D moved its tests). All four files pass `ruff check`, `ruff format --check` and `mypy` (repository configuration) as staged, and are ASCII only (every non-ASCII character is a `\u` escape). Nothing opens a socket, reads the clock for a result, reads the machine's environment or touches any private fixture.

Commands (after the move): `uv run --locked --all-groups pytest packages/cli/tests` or the whole workspace.

The test values of the batch D support file (`TEST_ONLY_FLAG_MIN_CONFIDENCE` 0.3 and `TEST_ONLY_REVIEW_BUDGET` 0.25) are used by the batch E tests too; they are test values, not defaults of the product. The product's own values are in the rubric file the user passes (`docs/examples/rubric-mr.example.json`: threshold 0.5, placeholder budget 1.0), which one test runs end to end.

## 1. Modules, signatures, source rules

```text
tracer_cli/cli.py       parser, run, main, Environment, EngineRequest, exit codes, caps     (P3-22)
tracer_cli/summary.py   the facts of a run as text lines, shared by the command and the report (P3-22, P3-23)
tracer_cli/report.py    the single self-contained HTML report                               (P3-23)
tracer_cli/replay.py    the replay engine of --stt fake:<dir>                                (P3-22)
tracer_cli/store.py     (batch D, unchanged)
```

Dependency order, no cycles: `store` and `replay` <- `summary` <- `report` <- `cli`. The batch A import tests walk every module under `src/` and cover the new modules with no change: `tracer_cli` imports the standard library and the four workspace packages only (`html`, `base64`, `hashlib`, `urllib.parse`, `argparse`, `re`, `unicodedata` are standard library); it imports no network module (`socket`, `ssl`, `http`, `urllib.request`, `httpx`, `requests`), so the Sarvam engine and its transport stay behind `tracer_evalproviders` (an `httpx` transport is passed through as an opaque value, section 2).

Source rules (AST, `test_cli_has_no_key_option_and_reads_no_environment_but_in_environment_system`):

- the process environment (`os.environ`, `os.getenv`) and the clock (`datetime.now`, `time.time` and the like) are read in exactly one place, the classmethod `Environment.system` of `cli.py`; no other function of `tracer_cli` reads either;
- `report.py`, `summary.py` and `replay.py` read no clock, no environment and no file other than the replay directory (`replay.py`); `report.py` and `summary.py` read no file at all;
- no network module is imported by any file of `tracer_cli`;
- `store.py` stays the only importer of `tracer_evaldb` (batch D) and does not import the report.

### 1.1 `cli.py`

```text
EXIT_OK = 0 (= pipeline.EXIT_SCORED);  EXIT_UNEXPECTED = 1;  EXIT_NOT_SCORED = 2 (= pipeline.EXIT_NOT_SCORED)
EXIT_STT_FAILED = 3 (= pipeline.EXIT_STT_FAILED);  EXIT_REFUSED = 4;  EXIT_USAGE = 64
EXIT_CODES = {0: "scored", 1: "unexpected failure", 2: "not scored", 3: "speech-to-text failed",
              4: "input refused", 64: "usage"}
MAX_CONFIG_FILE_BYTES = 1_048_576        # rubric file and price file
MAX_TRANSCRIPT_FILE_BYTES = 67_108_864   # the export file

@dataclass(frozen=True, kw_only=True) class Environment:
    stdout: TextIO; stderr: TextIO; environ: Mapping[str, str]
    clock: Callable[[], datetime]; sleep: Callable[[float], None]
    transport: Any = None                        # an httpx transport, test seam only (never a CLI option)
    engine_factory: Callable[[EngineRequest], SttEngine] | None = None
    @classmethod system() -> Environment         # real streams, os.environ, datetime.now(UTC), time.sleep

@dataclass(frozen=True, kw_only=True) class EngineRequest:
    stt: str; out_dir: Path; model: str; environ: Mapping[str, str]; env_file: Path | None
    clock: Callable[[], datetime]; sleep: Callable[[float], None]; transport: Any

default_engine_factory(request: EngineRequest) -> SttEngine
    # "sarvam": tracer_evalproviders.sarvam.engine.create_sarvam_stt(out_dir=, model=, environ=,
    #           env_file=, transport=, sleep=, clock=); "fake:<dir>": ReplayStt(Path(<dir>))
build_parser(stdout: TextIO | None = None, stderr: TextIO | None = None) -> argparse.ArgumentParser
run(argv: Sequence[str], environment: Environment) -> int
main(argv: Sequence[str] | None = None) -> int          # run(argv or sys.argv[1:], Environment.system())
```

`main` keeps exactly the one optional parameter `argv` (the scaffold test `test_tracer_console_script_runs_help` pins `list(params) == ["argv"]` and `main(["--help"])` raising `SystemExit(0)` with the help on `sys.stdout`; the existing test is not weakened). `run` is the same command with its world injected, which is how the tests drive it. `--help` prints to `environment.stdout` and raises `SystemExit(0)` (it is not caught by `run`); every other outcome is a return value, never a `SystemExit`.

The injected `engine_factory` is called exactly once per real run, after every input has been read and checked and before the pipeline runs; a dry run and every refusal before that point never call it (`factory_calls == 0` in the tests). If the object it returns has a `close()` method, the command calls it exactly once, also when the pipeline raises (`test_cli_closes_the_engine_once_whatever_happens`). Without a factory the command builds its own engine with `default_engine_factory`: for `sarvam` the key is read first (a missing key stops there, before `--out` exists and before any request, batch B), for `fake:<dir>` no key is read and no HTTP client is built.

How a test runs the command (no process, no socket, no real clock):

```text
env = cli.Environment(stdout=io.StringIO(), stderr=io.StringIO(), environ={"SARVAM_API_KEY": <canary>},
                      clock=lambda: <aware datetime>, sleep=sleeps.append,
                      transport=<httpx.MockTransport or None>,
                      engine_factory=<lambda request: FakeEngine(...)> or None)
code = cli.run(["score", <wav>, "--transcript", ..., "--out", <dir>], env)
```

With a factory the fake engine of the batch D support file answers (the base scenario: two chunks of a 3 s recording at 8 kHz, `--max-chunk-ms 2000 --search-ms 1000`); without one, `--stt sarvam` talks to the `MockTransport` and `--stt fake:<dir>` replays a directory.

The clock is read exactly once (`created_at`, converted to UTC; a naive datetime is a `ValueError`, exit 1, nothing created: UTC is never assumed). That one instant stamps every record, the language declaration and the report name; the retention and price instants come from their own options and are parsed, never defaulted.

## 2. The command

```text
tracer score AUDIO --transcript FILE --rubric FILE --declared-language CODE
             --caller-origin {human,simulated} --consent {obtained,not_obtained,unknown}
             --retention POLICY --out DIR
             --stt ENGINE [--dry-run] [--embed-audio] [--env-file FILE] [--price-file FILE]
             [--priced-at INSTANT] [--max-chunk-ms MS] [--search-ms MS]
             [--accept-provider-downmix] [--accept-export-warnings]
```

### 2.1 Required inputs, no defaults (SCOPE P3-22, ADR 0016 Decision 2)

`--transcript`, `--rubric`, `--declared-language`, `--caller-origin`, `--consent`, `--retention`, `--stt` and `--out` are required (`required=True`, `default=None`; shown without brackets in the usage line; their help text says "required"). No environment variable, configuration file or earlier run fills a missing one. A missing one, an unknown option (there is no `--api-key`), an abbreviated option (`allow_abbrev=False`: `--cons` is not `--consent`), a value outside the choices or an unparsable value is a usage error: exit 64, one `usage:` block and one `tracer: error: ...` line on stderr, nothing on stdout, nothing created, no factory call. The error message names the option.

| Option | Value | Notes |
|---|---|---|
| `AUDIO` | path of a 16-bit PCM WAV | read by `read_wav` (batch A); relative paths are relative to the working directory |
| `--declared-language` | a language tag shape `[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*` | any other shape is usage (64); a well-formed tag other than exactly `mr` (`hi`, `mr-IN`, `en`) is refused (4) with the code `language_not_supported`, before anything is read (BL-162: Phase 3 supports mr only) |
| `--caller-origin` | `human` or `simulated` | recorded on the Run |
| `--consent` | `obtained`, `not_obtained` or `unknown` | `ConsentRecord(status=...)` on the artifact and both transcripts; nothing else (no reference) |
| `--retention` | `keep`, `keep-until:INSTANT` or `delete-after:INSTANT` | `keep` is `delete_on_expiry=False` with no `retain_until`; `keep-until` is `False` with the instant; `delete-after` is `True` with the instant. The word `keep` is an explicit statement, not a default (the option is required). `policy_ref` is not settable in Phase 3 |
| `--stt` | `sarvam` or `fake:<dir>` | required, no default; see below |
| `--out` | directory | created (mode 0700, parents too) by the store; an existing path that is not a directory is refused (4) |
| `INSTANT` | RFC 3339: `YYYY-MM-DDTHH:MM:SS`, optional `.f` to six digits, then `Z` or `+HH:MM`/`-HH:MM` | no date alone, no space, no missing zone; anything else is usage (64) |

`--stt` is required with no default (lead decision, batch E: sending audio to a provider is always an explicit word on the command line): its value is exactly `sarvam` (the audio is sent to Sarvam under the user's key) or `fake:<dir>` (section 4.2, nothing is sent); any other value, `fake:` with no directory, a different case or a trailing space is usage (64). A run without it, also a `--dry-run`, is a usage error that names `--stt`; the factory is never called.

Optional options and their defaults (technical, none is a privacy or language input): `--max-chunk-ms` 25000 and `--search-ms` 5000 (the documented technical defaults of the chunker, ADR 0024 rule 7; a non-integer is usage, a value the chunker refuses is exit 4); `--dry-run`, `--embed-audio`, `--accept-provider-downmix` (the opt-in of ADR 0024 Ambiguity 3: stereo chunks are sent unchanged and the provider merges the channels; Tracer never downmixes), `--accept-export-warnings` (the reader's `accept_warnings=True`: a warning that drops a line no longer refuses); `--env-file`; `--price-file`; `--priced-at` (an instant; usage if given without `--price-file`).

The model is not an option: it is `saaras:v4` (the adapter default, ADR 0024 rule 6), sent explicitly and recorded as requested.

### 2.2 Order of operations (pinned: a refusal before the engine writes nothing)

1. Parse; usage errors (64). Read the clock once; build the consent and retention records.
2. The language check (4). Check that `--out`, if it exists, is a directory (4).
3. Read and parse the rubric file (cap `MAX_CONFIG_FILE_BYTES`; `parse_rubric_file`; its codes are the refusal text). Read the price file if given (same cap; `load_user_catalog`).
4. Read the export file (cap `MAX_TRANSCRIPT_FILE_BYTES`; strict UTF-8, no replacement; a byte order mark stays for the reader, which strips one). Read the audio (`read_wav`). Build the configuration (`build_configuration`, `configured_language="mr"`).
5. Read the export once more as the pipeline will (`read_voicera_export` with the declaration `{mr, user_declared, declared_at=created_at}` and `accept_warnings=--accept-export-warnings`), and chunk the audio (`chunk_wav` with the options): every refusal of the reader and the chunker happens here, before any engine exists.
6. The report name is `report-YYYYMMDDTHHMMSSZ.html` in `--out`, from the one instant in UTC. If anything exists under that name (a symlink, also a dangling one, counts: `os.path.lexists`) the run is refused (4, text `report_exists`). With `--embed-audio` the file is read again and its sha256 must equal the artifact's, else refused.
7. `--dry-run`: print the plan (section 3.3) and return 0. Nothing is written, `--out` is not created, no key is read, `--env-file` is not read, no factory is called.
8. Call the engine factory (`sarvam`: the key is read here; errors are exit 4). With `--embed-audio` print the warning of section 3.2.
9. The pipeline (`score_recorded_call`), with a `CostBuilder` composed by the command (section 4.3), `max_chunk_ms`, `search_ms`, `accept_provider_downmix`, `accept_export_warnings`, `export_declared_language="mr"`, `configured_language="mr"`.
10. The store (`open_sqlite_store`, `store_run`; the project is named `recorded-call scoring` with `ProviderConfigRef(sarvam, saaras:v4, env:SARVAM_API_KEY)` only for `--stt sarvam`, none for a replay; the agent target `recorded call agent`), then the report file, created exclusively (`O_CREAT | O_EXCL`, mode 0600, never following a link), then the stdout lines of section 3.1. The exit code is the pipeline's `exit_code` (0, 2 or 3).

The run is stored and the report written for all three pipeline outcomes: a call that was not scored is still a fact (and the usage that happened was paid for). A run joins the store of an earlier run of the same call (finding F14); the new report name keeps the earlier report.

### 2.3 Files, caps, text, modes

- A file is read with a cap plus one byte; one byte over the cap is refused (4, text `the <role> file is too large (the cap is N bytes)`), exactly the cap is accepted; the cap is read from the module constant when the command runs (the tests lower it). A path that cannot be opened, a directory and an empty path are refused (4, `cannot read the <role> file (<exception class>)`, no path, no errno text). Bytes that are not UTF-8 are refused (4, `the <role> file is not valid UTF-8`).
- Roles in these messages: `audio` (WAV refusals use the reader's own typed message), `rubric`, `price`, `transcript`.
- Refusals are one line on stderr, `error: <text>`, ASCII, at most 500 characters, every character outside printable ASCII replaced by `?`; the text is a code, a size, a pointer or an exception class name, never a value from a file (rubric codes `rubric_*`, `binding_param_*`, `catalog_*`, `unknown_scorer`, the reader's `VoicEra export refused: <codes> at <pointers>`, the chunker's and WAV reader's typed messages, `key_missing`, `key_invalid`, `key_ambiguous`, `env_file_unreadable`, `replay_directory_unreadable`, `replay_extra_file`, `language_not_supported`, `report_exists`).
- Files created: `<out>/tracer.sqlite` (0600, batch D), `<out>/report-<UTC>.html` (0600), and, for `--stt sarvam` only, `<out>/sarvam_call_log.jsonl` (0600, batch B). The directory is 0700 when the command created it. Nothing else is written, nowhere else.

## 3. What the command prints

stdout holds the result lines and nothing else (ASCII); stderr holds usage errors, refusals, the embed warning and the unexpected-failure line. The output never holds any text of either transcript, the call id, a key, the path of an input file or a rubric `note`; the last three lines of a result block name the files the command wrote.

### 3.1 The result block (`summary.render_summary`, then three lines of the command)

```text
summary.render_summary(bundle, *, outcome: str, stt_failure: object | None) -> str     # lines end in LF
summary.not_scored_reasons(bundle) -> tuple[str, ...]                                   # section 3.4
summary.stt_failure_detail(bundle) -> str | None     # the message of the Run's `stt_failed` warning
```

Scored (all labels, in this order, each once; `<count>` values are integers, `<float>` is `repr` of the stored float):

```text
result: scored
language declared by user: mr
stt provider: <provider of the STT transcript>
stt model: <model> (as requested)
normaliser: mr-Deva/1
agreement distance (MER): <float> (<S+D+I> of <H+S+D+I>; H <h>, S <s>, D <d>, I <i>)
tokens: <export tokens> in the export, <stt tokens> in the stt transcript
flags: <count> (confidence at or above <threshold text of the flags document>)
flagged share of export tokens: <flagged export tokens> of <export tokens> (<float>)
export turns with a flag: <distinct turns with a flagged span> of <export turns>
review budget: <see 3.5>
stt usage: <n> requests, <seconds> audio seconds        |  none
cost: <see 4.3>
ingest warnings: <count>
bundle: <RunBundleId wire text>
store: <out>/tracer.sqlite
report: <out>/report-<UTC>.html
```

`<out>/...` is `Path(--out) / name` as typed (relative stays relative). The base scenario prints `agreement distance (MER): 0.25 (3 of 12; H 9, S 1, D 1, I 1)`, `flags: 2 (confidence at or above 0.3)`, `flagged share of export tokens: 1 of 11 (0.09090909090909091)`, `export turns with a flag: 1 of 3`. `stt usage` sums the `requests` quantities and the `audio_input_seconds` quantities (canonical decimal text) of the bundle's usage records; `none` when there is no usage record.

Not scored (outcome `not_scored` or `stt_failed`): the labels `result` (value `not scored`), `language declared by user`, one `not scored` line per reason (section 3.4), `detail` (only for a failed transcription: the text of the Run's `stt_failed` warning, which is the error's own text, e.g. `SttServerError: chunk 1, HTTP 503, 4 attempt(s)`; every character outside printable ASCII is replaced by `?` on this line, so a terminal never receives a control or bidi character), `stt usage`, `cost`, `ingest warnings`, `bundle`, `store`, `report`. No number of the comparison, no zero, no `flags` line.

### 3.2 stderr

- Embed: `warning: --embed-audio puts the call audio into the report file; do not share the file` (exactly, one line, printed before the engine runs, only for a real run).
- Refusal: `error: <text>` (section 2.3), exit 4.
- Usage: the usage block and `tracer: error: <message>`, exit 64; `tracer` with no command prints the help to stderr and exits 64.
- Unexpected: `error: unexpected failure (<exception class name>)`, exit 1; never the message, never a traceback, never a chained cause (the message of an engine failure could hold a key or call text).

### 3.3 The dry run

```text
chunks: <n>
audio seconds: <canonical decimal>[ (rounded half-even to 18 digits)]
calls: <n>
cost: <not available (no USD price supplied) | <amount> USD (computed from the supplied USD price) | not available (<reason>)>
max attempts per chunk: 4
dry run: nothing was sent
```

The first four lines are exactly `tracer_evalproviders.dryrun.build_dry_run_plan(plan, model="saaras:v4", catalog=<user catalog or None>, priced_at=<--priced-at or None>).render()` (batch B); the last two are the command's (the `calls` count is one per chunk, a chunk can take up to `MAX_ATTEMPTS` = 4 requests: finding F-B9). A price file without `--priced-at` gives `not available (ambiguous_price)`, a price file that does not price `requests` gives `not available (unit_unpriced)`. `--embed-audio` with `--dry-run` prints no warning (nothing is embedded).

### 3.4 Reasons a call is not scored (`not scored: <reason>`)

The reasons are listed once each, in the order of the three results; a failed transcription gives only the first row.

| Result | Reason text |
|---|---|
| a failed transcription (the Run holds the warning `stt_failed`) | `speech-to-text failed` |
| `error` `stt_unsupported_language` | `unsupported language detected` (the ADR 0023 rule 14 wording) |
| `error` `stt_language_not_observed` | `language not observed in the stt transcript` |
| `error` `no_tokens`, message of the export side | `no tokens in the export` |
| `error` `no_tokens`, the speech-to-text side | `no tokens in the stt transcript` |
| `error` `alignment_too_large` or `levenshtein_too_large` | `input too large to compare` |
| `error` `stt_provenance_missing` | `speech-to-text provenance missing` |
| `error` `flags_document_refused` | `flags document refused` |
| `not_applicable` `missing_input` | `missing input <kinds joined by ", ">` (e.g. `missing input agent_transcript`) |
| `not_applicable` `unsupported_language` / `language_not_observed` | `unsupported language detected` / `language not observed` |
| any other code | the code (or the reason with `_` replaced by a space) |

The side of a `no_tokens` error is read from the scorer's message (`the export has no token` / `the stt transcript has no token`, batch D).

### 3.5 The review budget line

The budget is the `range` pass rule of the share definition in the bundle's snapshot (`max`, `inclusive`). Wording:

- `review budget: <max> (no limit)` iff `max == 1` and `inclusive` is true (a share is never above 1, so the rule cannot fail); `<max>` is printed as stored: `1.0` for the float of the example file, `1` for an integer. Nothing about passing or failing is printed in that case.
- otherwise `review budget: <max> (flagged share is within it)` or `(flagged share is over it)`, by the share result's `passed`.

An over-budget call is still `scored`: exit 0. The rule is the owner's review workload line (ADR 0023 rule 17), never a judgement of the agent, and never changes which spans are flagged.

## 4. Engines, cost, the replay directory

### 4.1 The Sarvam engine

Unchanged from batch B: one request per chunk with the key header, the call log before and after each attempt, retries that wait with the **injected** `sleep` (the first wait of a 503 is 1 s: `Environment.sleep` is called with `1`, `time.sleep` never; the tests never wait), `ApiKey` redaction. The tests drive it through `Environment.transport` (an `httpx.MockTransport` built from scripted 200 bodies and error statuses) and the injected `environ`; the key is read from `environ` or, with `--env-file`, from that file only (the file is then the only source: no fallback to `environ`; two definitions of the variable are refused; the variable name and the file path are never written to any output). The Project records `env:SARVAM_API_KEY`, never a value, never the file path.

### 4.2 The replay engine (`--stt fake:<dir>`, `tracer_cli.replay.ReplayStt`)

```text
REPLAY_FILE_FORMAT = "chunk-{index:04d}.json"      REPLAY_PROVIDER = "replay"      REPLAY_MODEL = "recorded-responses"
class ReplayStt:  __init__(directory: Path);  transcribe(plan: ChunkPlan, context: SttContext) -> SttOutcome
```

`<dir>/chunk-0000.json`, `chunk-0001.json`, ... hold one Sarvam 200 body per chunk of the plan (the 2026-10-23 capture format: `request_id`, `transcript`, optional `language_code`, `language_probability`, `timestamps`), read with `parse_response` and mapped with `map_responses` (batch B) so that the real mapping, the language handling and the segment offsets are the ones a real run gets. Rules:

- No request is made, no HTTP client is built, no key and no env file is read, no call log is written: the command runs offline (`test_cli_fake_stt_runs_offline`).
- A replay never claims a request that was not made: it returns **no usage record** (so `stt usage: none` and `cost: not available (no_usage)`, also with a price file), and the STT Transcript carries `provider="replay"` and `model="recorded-responses"` (so the summary, the report and the store say replay).
- A missing chunk file is a failed transcription (`SttResponseInvalid`, reason `replay_file_missing`, all or nothing, exit 3). A body larger than 1,048,576 bytes (the adapter's `MAX_RESPONSE_BYTES`; exactly the cap is read) is `body_too_large`; a body the parser refuses carries the parser's short reason (`not_json`, `not_object`, `transcript_invalid`, ...). A file `chunk-N.json` with N at or above the number of chunks is a stale directory: refused (`SttConfigurationError`, reason `replay_extra_file`, exit 4, nothing written). Files that do not match the pattern are ignored. A directory that does not exist is refused (`replay_directory_unreadable`, exit 4) when the engine is built.
- The language tag of a body is read as the provider's tag: a non-mr body makes the call `not scored: unsupported language detected` (exit 2).

### 4.3 The cost line

The command composes the batch D `CostBuilder` from `tracer_evalproviders.cost.compute_stt_cost(usages, catalog=<user catalog or None>, created_at=<the instant>, run_started_at=<--priced-at or None>)`: the bundle holds the catalog (if any), one cost record per usage record and one summary. `summary.cost_text`:

- complete: `<amount> USD (computed from the supplied USD price)` (the base price file below gives `0.0003 USD` for 3 audio seconds);
- none: `not available (<na_reasons, sorted, joined by ", ">)`: `no_price_for_model` (no price file, or a price for another model), `unit_unpriced` (a price file that prices `audio_input_seconds` but not `requests`: BL-210), `ambiguous_price` (no `occurred_at` and no `--priced-at`), `no_usage` (a replay);
- partial: `partial: <amount> USD (<k> of <n> usage records priced)` (never worded as complete or as "computed"; pinned by `test_cli_a_price_that_starts_mid_run_is_a_partial_cost_never_a_complete_one`, where a price that becomes effective between two response dates prices one of two chunks; the report's `#usage-cost` shows the same text);
- no cost summary in the bundle (a bundle built without the command): `not available (no cost record)`.

Never `0 USD` for want of a price (`test_cli_sarvam_path_prices_from_a_user_file_with_the_response_date`, `test_report_usage_table_and_cost`). The price file is the batch B format (`{published_at, entries}`, every entry `user_supplied`, Tracer seals it); `--price-file` help says that it "must also price requests" as well as `audio_input_seconds` (BL-210). INR is never converted.

## 5. The report (`tracer_cli.report`)

```text
@dataclass(frozen=True) class RelativeAudio:  src: str       # ValueError("... relative ...") unless src matches
                                                             # [A-Za-z0-9._~%/-]+, does not start with "/", has no "//"
@dataclass(frozen=True) class EmbeddedAudio:  data: bytes    # TypeError if not bytes, ValueError("... empty ...") if empty
render_report(bundle: RunBundle, *, audio: RelativeAudio | EmbeddedAudio) -> str     # both parameters, no defaults
relative_audio_src(audio_path, report_dir) -> str     # os.path.relpath of the resolved paths, "/" separators,
                                                      # urllib.parse.quote(..., safe="/")
safe_html(text: str) -> str                           # section 5.4; TypeError for a non-str
outcome_of(bundle) -> "scored" | "not_scored" | "stt_failed"
csp_hash_source(text: str) -> str                     # "'sha256-" + base64(sha256(utf8(text))) + "'"
content_security_policy(style_text: str, script_text: str, *, embedded: bool) -> str
SEEK_SCRIPT: str      STYLE: str                      # the exact text of the one <script> and the one <style>
```

`render_report` reads the bundle alone (the record kinds of batch D, scored or not) and the audio argument: it reads no clock, no file, no environment, and it prints nothing. `render_report(stored_bundle, audio=...)` for the bundle read back from the store is byte-identical to the report the command wrote (`test_report_the_command_writes_is_the_report_of_the_stored_run`). The report holds no typed id and no call id, so two runs of the same call with the same inputs and clock give the same bytes (`test_report_is_reproducible_and_holds_no_record_ids`).

`outcome_of`: `stt_failed` if the Run holds the warning `stt_failed`; else `scored` if every result is `scored`; else `not_scored` (the same label as `PipelineResult.outcome`).

### 5.1 Document shape

```text
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="...">      first policy in the head, before style, script, audio
<meta name="tracer-report" content="1">                         the marker for the repository guard (P3-34)
<title>...</title><style>STYLE</style></head>
<body><main id="report">...</main><script>SEEK_SCRIPT</script></body></html>
```

### 5.2 Structural rules (`structure_violations`, `external_request_violations`; every report, every outcome)

- Exactly one `<main>`, one `<style>` and one `<script>`; the script and the style have no attribute; at most one `<audio>`.
- Forbidden tags: `a img iframe frame frameset object embed applet link base form input textarea select option video source track picture canvas template noscript portal slot font center marquee`.
- Allowed attributes: `class id lang charset http-equiv content name controls preload src type width height x y viewbox role aria-label aria-hidden aria-labelledby aria-describedby colspan rowspan scope headers dir title open tabindex` and any `data-*`; no attribute repeats on an element; `src` only on `<audio>`; a `<meta>` is a charset, the one `http-equiv` Content-Security-Policy, or a `name`. No `href`, `srcset`, `action`, `formaction`, `poster`, `background`, `ping`, no `style` attribute, no event-handler attribute (`on...`).
- No external request: no `url(`, `@import`, `http:`, `https:`, `ftp:`, `javascript:` or `//` in an attribute value, in the style text, in the script text or in any Tracer-written text; the audio `src` is a relative reference (`[A-Za-z0-9._~%/-]+`, no leading `/`, no `:`, no `//`) or, with `--embed-audio`, `data:audio/wav;base64,<payload>` (whose payload may hold `//`). In relative mode the document holds no `data:audio` reference.
- Every text node outside an `ut` element is Tracer-written (section 5.4) and is scanned by the deny-list and the url rules; every attribute value except `src` and the CSP `content` is scanned by the deny-list.

### 5.3 The CSP and the script hash

```text
default-src 'none'; style-src 'sha256-<S>'; script-src 'sha256-<H>'; media-src <'self' | data:>; base-uri 'none'; form-action 'none'
```

exactly this string, in this order, as the `content` of the one policy `<meta>`: `<S>` and `<H>` are `base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest())` (standard alphabet, with padding) of the exact text between `<style>` and `</style>` and between `<script>` and `</script>` (the text the browser hashes); `media-src` is `'self'` for a relative audio reference and `data:` when the audio is embedded. No `unsafe-inline`, `unsafe-eval`, `*`, `blob:` or any host. The style is therefore hashed too, which is why no element carries a `style` attribute (the histogram bars are `<svg>` with presentation attributes).

`SEEK_SCRIPT` is ASCII, at most 2,000 characters, one function that, on a click on an element (or a descendant of one) that carries `data-seek-start-ms`, sets `currentTime` of the element with id `player` to that many milliseconds divided by 1000 and plays. It contains none of `eval Function fetch XMLHttpRequest import document.write innerHTML outerHTML location open( WebSocket // </ <!-- http cookie localStorage postMessage setTimeout setInterval src`. Not tested: that it runs, and whether a relative `<audio src>` plays under `file://` in Chrome, Firefox and Safari (SCOPE Q12; manual check below).

### 5.4 Text: `display_safe`, markers, escaping

Every piece of untrusted text (an export turn segment, the export and STT text of a flag row, a provider or model name, a warning message, a failure detail) is written as `safe_html(piece)`:

1. `display_safe(piece)` (ADR 0017): the piece wrapped in FSI ... PDI, each embedding, override and isolate control replaced by its marker (`[LRE]`, `[RLE]`, `[PDF]`, `[LRO]`, `[RLO]`, `[LRI]`, `[RLI]`, `[FSI]`, `[PDI]`); LRM, RLM, ALM, ZWJ and ZWNJ unchanged;
2. every character of general category `Cc` (NUL, the other C0 controls, DEL, C1) and U+2028 and U+2029 replaced by `[U+XXXX]` (upper-case hexadecimal, at least four digits), so the file holds no raw control character;
3. `html.escape(..., quote=True)`.

It is applied **per segment**: a turn text is cut at the flagged spans and each piece (before, the span, after) is wrapped on its own, so each wrapper isolates one piece and the highlight markup never sits inside an isolate. The element holding such a piece has the class `ut` and holds nothing but that text: its text starts with FSI and ends with PDI and holds neither inside; empty pieces are not written; no raw U+202A to U+202E, U+2066 or U+2067 ever appears in the file; the number of FSI equals the number of PDI equals the number of `ut` elements.

Rejected: `display_safe` alone (it leaves NUL and the other controls raw in a file that tools treat as text; browsers drop a NUL silently, so what the reader sees would differ from what was stored); escaping only (bidi reordering). Recorded as ambiguity 8.

### 5.5 Elements a test can rely on (ids; classes `ut` as above)

```text
header           p#status[data-outcome]               "result: scored" | "result: not scored"
                 section#what-this-is                 contains "neither", "reference", "recorded call", "no call was simulated"
                 p#declared-language[data-language]   "language declared by user: mr"        (the word "detected" appears nowhere)
audio            p#audio-facts[data-sha256][data-bytes]   audio: wav, <rate> Hz, <n> channel(s), <bytes> bytes, sha256 <hex>
                 audio#player[controls][preload="none"][src]   exactly one when there is an artifact
                 p#embed-warning.warning              only with --embed-audio: "... personal data ... do not share it."
                 p#privacy                            "personal data: yes. consent: <status>. delete on expiry: yes|no. retain until: <UTC Z instant>|not set."
provenance       dl#provenance with dd#scorer-version (the distribution version), and, when scored,
                 dd#signal, dd#stt-provider (ut), dd#stt-model (ut), dd#stt-model-source ("requested"),
                 dd#normaliser, dd#threshold (the threshold text of the document)
numbers (scored) section#summary with p#agreement[data-value][data-hits][data-substitutions][data-deletions][data-insertions],
                 p#tokens, p#flag-count[data-value], p#flagged-share[data-numerator][data-denominator], p#flag-turns,
                 p#review-budget[data-max][data-no-limit="true|false"][data-met="true|false" (absent when no limit)];
                 the text of each is the line of the command's output (section 3.1), label included
not scored       section#not-scored > ul > li         "not scored: <reason>" per reason, then "detail: <ut>" for a failed transcription;
                 and none of #summary, #agreement, #tokens, #flag-count, #flagged-share, #flag-turns, #review-budget, #histogram, #flags
histogram        table#histogram, 10 tr.bin[data-bin=0..9][data-low][data-high][data-spans][data-flagged]  (low "0.<bin>", high "0.<bin+1>", last "1.0")
                 p#histogram-total "spans: <n>"
                 bin = min(9, edit_distance * 10 // key_length) of every recorded span, flagged or not (integer arithmetic)
                 text: "Histogram of the confidence of every span ... uncalibrated disagreement strength and not a probability"; no recommendation
flags            section#flags: table#flags-table with one tr.flag-row[data-span][data-turn-seq] per FLAGGED span, in span order,
                 cells with classes turn ("turn 2"), role ("agent" | "caller"), offsets ("12-15"), confidence (the document string),
                 edit ("edit distance 3 of 3"), boundary ("yes"|"no"), exportside, heard, audio (the last cell: button.seek or
                 "no audio range"); the table head has the labels "agent export" and "STT heard", in this order, over the two cells;
                 tr.flag-row also carries [data-stt-token-start][data-stt-token-end] (the span's STT token range, half open)
                 td.exportside: span.export-piece.ut = safe_html(turn_text[start:end])      or, for a zero-width span (an insertion), span.none "not in export"
                 td.heard: one span.heard-piece.ut[data-stt-turn][data-stt-start][data-stt-end] per STT turn (chunk) that the span's STT
                           tokens touch, in order, each = safe_html(stt_turn_text[first token start : last token end] of that turn's
                           tokens in the range), or, for a span with no STT token (a deletion), span.none "nothing heard";
                           a span.none holds no ut element
                 with no flagged span a p "no span reached the threshold" instead of the table
                 button.seek[type=button][data-seek-start-ms][data-seek-end-ms] only for a span with an audio range
transcript       ol#transcript > li.turn#turn-<seq>[data-seq][data-role][data-flags], one per export turn in seq order, holding
                 span.role and div.text; div.text holds, in order, span.seg.ut (plain pieces), mark.flag.ut[data-span][data-turn]
                 [data-start][data-end][data-confidence] (a flagged span with width) and mark.flag.gap[same data attributes] with
                 the text "[+]" (a zero-width flagged span: an insertion, shown at its offset; not a ut element)
usage            section#usage: td#usage-requests ("2" | "none"), td#usage-audio-seconds ("3" | "none"), td#usage-cost (the cost text of 4.3);
                 p#usage-note ("A user price file must also price requests as well as audio_input_seconds ...") iff the cost is not available
warnings         section#warnings[data-count]: p#warning-count "ingest warnings: <n>", ul > li > code (the code) + span.ut
snapshot         section#snapshot-note: names recorded_call_no_outcome_check, "never run", "schema 0.0.1", "not a judgement" (ADR 0023 rule 17)
```

The STT text of a flag (lead decision, batch E). The STT tokens of a span are those of indices `stt_token_start` to `stt_token_end` (half open) of the tokens of all STT turns, in `seq` order, tokenised over each turn's text with the normaliser named in the flags document (`tracer_evalcore.tokens.tokenise_turns`, no segments needed); a span whose tokens lie in two chunks shows two pieces. The text shown is the original STT text from the first to the last token of the span in that turn (offsets index the turn text as stored), never the normalised key. It is read from the bundle's STT turns by the report alone: the text goes nowhere else (see section 8).

Only the flagged spans are highlighted; the spans under the threshold appear only in the histogram. The text of a `mark.flag.ut` is `safe_html(turn_text[start:end])`; the concatenation of the `ut` pieces of a turn, wrappers removed, is the turn text with its markers (`markerize` in the support module).

### 5.6 Audio

By default the report refers to the audio file by a relative, URL-encoded `src` from the report's directory to the audio file, both paths resolved (the command passes `relative_audio_src(Path(AUDIO).resolve(), out.resolve())`); the report holds no absolute path (no `file:`, no drive, no home, no temporary directory, not the artifact's `file:///` URI; the stored artifact keeps it). `--embed-audio` embeds the file's exact bytes as `data:audio/wav;base64,...` with the visible warning of 5.5, the stderr warning of 3.2 and `media-src data:`. A relative reference that climbs (`../in/call.wav`) shows the names of the directories between the two files: stated in ambiguity 10.

### 5.7 Deny-list scanning rules

The words `accuracy`, `WER`, `CER`, `error rate`, `correct`, `recall`, `precision` (case-insensitive, whole words; regex `(?i)\b(?:accuracy|wer|cer|error rate|correct|recall|precision)\b`) never appear in: the whole stdout and stderr of every scenario (scored, over budget, placeholder budget, each not-scored outcome, a failed transcription, dry run, embed, refusals) and the `--help` output of the program and of `score`; the Tracer-written text of the report (text outside `ut` elements) and its attribute values (except `src` and the CSP). Also absent: the phrases `accuracy`, `error rate`, `word error`, `character error`, and in the report `recommend` and `suggest`. The value is labelled `agreement distance (MER)` everywhere. User-supplied text (the transcripts) is exempt and shown escaped. The one allowed sentence naming the words is the concept page's (ADR 0023 rule 1), not these outputs.

## 6. The adversarial suite (P3-24)

`test_adversarial_inputs_never_break_report_or_store` runs the whole command on 24 hostile exports. For every input the command either **refuses cleanly** (exit 4: one `error: ` line, nothing on stdout, `--out` not created, no factory call) or **completes** (exit 0, 2 or 3) with all of:

- exactly two files in `--out` (the store and one report);
- the report passes every structural and external-request rule of 5.2 and 5.3 (the script is `SEEK_SCRIPT`, the hashes match, no raw bidi or control character, every `ut` wrapper intact, nothing from the transcript outside an `ut` element);
- the report equals `render_report` of the bundle read back from the store; what the report shows of every export turn is that turn, with markers, and nothing else;
- stdout and stderr hold no traceback, no `<script>`, no hostile text, and no character of category `Cc` or `Cf` other than LF.

The inputs: script and attribute breakouts, the closing tags of the report itself, url forms in text, an override storm (10,000 controls), a PDI breakout, every bidi control, NUL, the C0 and C1 controls, line and paragraph separators, zero-width characters, astral and combining characters, template and format strings and SQL text, a 1,000,000-character token, 12,000 tokens (over the aligner's cap), 1,200 turns, a 400 KB line with a hostile tail, an empty file, a header only, caller lines only, a call id with a bidi control, an unknown speaker, bytes that are not UTF-8, a byte order mark with CRLF line ends, and a replacement character. Which exit codes are allowed per input is the table in the test; the oversize inputs are `not scored: input too large to compare` (exit 2), never a crash and never a number.

The other P3-24 tests: a Sarvam body with markup, bidi, a NUL and a canary (compared and stored; its text is shown beside the flag it causes, escaped and marked, and appears in no stream, call log or flags metadata); a body with a markup language label (not shown); replay bodies that are over the cap, not JSON, not an object, with the wrong type, `NaN` or 5,000 levels deep (each a failed transcription, exit 3, with the reason); the exact cap; a lone surrogate (a file cannot hold one; the library path replaces it with U+FFFD and warns, and the report renders); `--out` that is a file; a directory for the audio, the rubric or the export; an empty WAV; an existing report name that is a symlink, also a dangling one (never followed, nothing written); an option abbreviation; a rubric `note` with hostile text (never stored, never shown); a zero-frame recording (`no_tokens`) and a WAV whose header claims 2 GiB (refused).

## 7. Findings and ambiguities (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

1. **Exit codes.** The pipeline's `EXIT_NOT_SCORED` is 2, which is also argparse's usage code. Pinned: usage is 64 (`EX_USAGE`), refused input 4, unexpected failure 1, plus the pipeline's 0, 2, 3; the parser raises instead of exiting, so the two are never confused. Alternative: usage 2 and not-scored 5 (changes batch D's pinned constants; rejected).
2. **`main` and the injected world.** The scaffold test fixes `main(argv)` to one parameter. Pinned: `run(argv, environment)` is the command, `main` wraps it with `Environment.system()`; `Environment` is the seam for streams, environment, clock, sleep, transport and engine factory. The transport is a test seam, not a CLI option (ADR 0024 rule 3: no way to change the URL).
3. **`--retention`.** SCOPE says `--retention ...`. Pinned: `keep`, `keep-until:INSTANT`, `delete-after:INSTANT` (no default, `keep` is explicit). Rejected: two options (`--retention-delete-on-expiry` plus `--retain-until`; a combination the schema allows but means nothing: delete with no date); a bare date. `policy_ref` and a consent reference are not settable in Phase 3 (backlog candidates).
4. **`--stt` is required.** Decided by the lead (batch E): no default, so that sending audio to a provider is always explicit. The SCOPE bracket made it optional; the rejected default was `sarvam` (a real send also needs the user's key, but a forgotten option should never be a send).
5. **The replay directory.** SCOPE says `fake:<dir>` replays recorded responses. Pinned: section 4.2 (provider `replay`, no usage, extra files refused). Rejected: replaying through an `httpx.MockTransport` inside the command (the command may not import `httpx`, and a replay would then write a call log and measured usage records for requests that were never made, a false provenance).
6. **Report file name.** Pinned: `report-<UTC instant>.html`, refused if the name exists (never overwritten, never followed). The decision of F14 lets R1 to R3 share one store, so a fixed name would be overwritten or refused on the second run. Alternative: a name with the run id (unpredictable); a fixed `report.html` with an overwrite flag.
7. **The language gate on `--declared-language`.** Exactly `mr` in this release (BL-162); `mr-IN` is refused. Alternative: accept any tag whose primary subtag is `mr`.
8. **Controls beyond `display_safe`.** SCOPE P3-24 lists NUL; ADR 0017 says `display_safe` is the one function and it leaves NUL and the other controls alone. Pinned: a report-layer step after `display_safe` (`[U+XXXX]` for `Cc`, U+2028, U+2029), so the file is clean text. Alternative: extend `display_safe` itself (a change in `tracer-evalschema`, forbidden by D5).
9. **Style under the CSP.** SCOPE says "inline style". Pinned: one `<style>` element hashed in `style-src`, no `style` attribute anywhere (stricter than `'unsafe-inline'`). Alternative: `style-src 'unsafe-inline'` (one less constraint on the report's markup, a weaker policy).
10. **A relative reference shows directory names.** `../in/my-calls/call.wav` reveals folder names, not an absolute path. Alternative: place the audio next to the report (a copy of personal data, rejected) or show only the file name (the link would break). Whether a relative `<audio src>` plays under `file://` is unverified (Q12): manual check, ambiguity 18.
11. **Wording for `stt_language_not_observed`.** ADR 0023 rule 14 gives one wording ("not scored: unsupported language detected") for the language gate. Nothing was detected when the tag is missing, so the second case says `language not observed in the stt transcript`. Alternative: the ADR wording for both.
12. **STT text in the report.** Decided by the lead (batch E): each flagged span shows the STT text of its aligned STT tokens beside the export text, labelled "agent export" and "STT heard", with the same escaping and markers; a deletion shows "nothing heard", an insertion "not in export". Implementation: the report re-tokenises the STT turns with `tokenise_turns` and the normaliser of the flags document (the same function the scorer used, so the token indices agree); no second tokenizer. Consequence: the report now holds provider text (personal data, as it already held the export); the existing privacy line, the embed warning and the guard marker cover it. A span whose STT tokens lie across a chunk edge shows one piece per chunk; the piece text is exactly the stored turn text between its first and last token, so any punctuation between the tokens is shown.
13. **Technical options.** `--max-chunk-ms`, `--search-ms`, `--accept-provider-downmix` and `--accept-export-warnings` exist because the pipeline has the parameters (R3 of P3-31 shifts the chunking; stereo needs the opt-in named by ADR 0024; warnings that drop a line need the reader's opt-in). They have documented technical defaults (or are off), are not privacy or language inputs and are recorded in the chunk plan.
14. **Priced date.** A user price file needs the instant the price applies to when the usage has no `occurred_at` (a replay has no usage at all, so only a real run or the dry run). Pinned: `--priced-at INSTANT`, usage if given alone. Without it the cost is `ambiguous_price`, never the local clock (batch B).
15. **A re-render command.** There is no command that renders a report from a stored run (the function exists; a command would be an export surface, BL-140). `test_cli_has_no_export_command` pins the single subcommand `score` and refuses `export`, `share`, `redact`, `upload`, `publish`, `report` and `bundle`.
16. **The scaffold behaviour changes.** `tracer` with no command printed the help and returned 2; it returns 64 now (the scaffold tests do not pin it).
17. **POSIX.** File modes (0600, 0700), `O_EXCL`/symlink handling and relative paths are POSIX; Windows is not supported (the batch A caveats).
18. **No JavaScript execution in CI.** The seek script is tested statically; nothing runs it. The manual check (P3-23): open a report from a real run under `file://` in Chrome, Firefox and Safari, click a seek button, and confirm that the audio loads (relative reference) and seeks. Until then the claim "relative audio plays" is unverified.
19. **Fixed record names.** The Project is named `recorded-call scoring` and the agent target `recorded call agent`; there is no option to name them (a name is a `HumanLabel`, and the owner has not asked for one). Alternative: `--name` later.
20. **Numbers as `repr` of the stored float.** The command prints `0.09090909090909091`, not a rounded figure, so that the printed value is the stored value; the counts beside it are the readable form. Alternative: a fixed number of digits (a second representation of the same fact).
21. **`tracer` when the store write fails.** After a paid transcription, a failed store or report write is an unexpected failure (exit 1) and the usage is only in the call log; not tested. Recommendation: a later task writes the report first or retries; the call log is the record of what was sent.

## 8. Coverage against the gate

- Gate 4 (no network in unit tests, no key value anywhere): every module of the batch E tests patches `socket.connect` and relatives; `test_key_never_in_store_and_report_outputs` (report channel) runs the Sarvam path with the canary key through `MockTransport`, from the environment and from an env file, and searches every file under `--out`, stdout, stderr and the log records for the key, the variable name (allowed only in the SQLite file as `config_ref`) and the env-file path; the check is not vacuous (a text canary is found in the store and the report).
- Gate 5: `test_cli_output_never_claims_accuracy`, `test_report_never_claims_accuracy`.
- The report is validated by structure and by parsing it with `html.parser`, not by a browser; the seek script and `file://` playback are the two manual checks of ambiguities 10 and 18.
- The STT text is in the report and in the store (as the STT turns, the transcript itself) and nowhere else: `test_stt_text_never_reaches_stdout_the_call_log_the_flags_metadata_or_the_logs` runs the Sarvam path with a canary inside a flagged span and searches stdout, stderr, the call log, the log records, every result (metric result metadata and evidence), the usage records and the Run; the check is not vacuous (the canary is in the report). `tracer.flags/1` holds no text (batch D).
- Not tested here: Windows; the `[postgres]` store (batch D); Python 3.15; a real send (P3-30); a terminal that interprets escape sequences (the command prints no text of the call); that the audio bytes embedded equal the file read earlier under a race (the sha256 comparison is pinned only by reading).

## 9. Test inventory (every SCOPE name, exact)

| Task | Names | File |
|---|---|---|
| P3-22 | `test_cli_requires_every_privacy_and_language_input`, `test_cli_prints_counts_with_denominators`, `test_cli_output_never_claims_accuracy`, `test_cli_fake_stt_runs_offline`, `test_cli_exit_codes` (13 cases), `test_cli_has_no_export_command` | `test_cli_score.py` |
| P3-22 extra | not scored outcomes (language, not observed, no tokens, no agent line, failed transcription, missing provenance, refused document), budget wording, stored run and file modes, privacy inputs recorded, clock once, aware clock and report names, refusals before anything is created, UTF-8 and caps, unreadable inputs, `mr` only, stereo opt-in, embed warning, dry run (4) and `main` on the real streams, the Sarvam path (requests and call log, user price and response date, `--priced-at`, a partial cost, injected sleep, missing key, env file, rejection, project record), replay rules (2) and no cost, help and exit-code table, source rules (AST), summary text, engine request, closing, relative paths and symlinks, chunking parameters, price options, ASCII stdout, no abbreviation of program options | same |
| P3-23 | `test_report_has_no_external_requests`, `test_report_csp_present_and_script_hash_matches`, `test_report_escapes_html_and_script_in_transcript`, `test_report_shows_bidi_controls_as_markers`, `test_report_highlights_exact_flag_spans`, `test_report_relative_audio_by_default_embed_opt_in`, `test_report_never_claims_accuracy`, `test_report_shows_language_declared_by_user`, `test_report_has_no_absolute_paths`, `test_key_never_in_store_and_report_outputs` (report channel) | `test_cli_report.py` |
| P3-23 extra | STT text beside each flag (`test_report_shows_stt_text_beside_each_flag`, `test_report_flag_without_stt_tokens_is_labelled`, `test_report_stt_text_is_escaped_and_marked` with markup, bidi, NUL and a 150,000-character body, `test_stt_text_never_reaches_stdout_the_call_log_the_flags_metadata_or_the_logs`), relative `src` encoding, structure and numbers, histogram bins (and integer arithmetic on varied confidences), budget wording, usage and cost, calls that are not scored, no flags, seek controls and script, `safe_html`, reproducibility and no ids, `outcome_of` and argument checks, the report of the stored run, the inert-check note, highlights at the head of a turn, a flag in a caller line, retention shown, usage of a failed transcription, a hostile failure text | same |
| P3-24 | `test_adversarial_inputs_never_break_report_or_store` (24 inputs) | `test_cli_adversarial.py` |
| P3-24 extra | Sarvam body with markup and bidi, markup language label, replay bodies over the cap or malformed, lone surrogate, paths and options, rubric note, zero-frame and lying WAV, the exact cap | same |

Every name that SCOPE section A gives to P3-22, P3-23 and P3-24 is defined above, with the exact spelling (the first column), and `test_every_test_named_by_the_scope_rows_of_batch_e_exists` (one case per task, in `test_cli_score.py`; the batch D pattern) reads the three rows of SCOPE and checks that each backticked test name is defined in some `packages/*/tests` file. That guard passes on the red tree (it reads SCOPE and test sources only; its teeth are the mutants).

## 10. Required changes to existing tests (named spec change; qa did not edit them; tightening only, nothing removed that a test still asserts)

Two existing tests assert that a pending name leaves `PENDING` in the commit that adds its test (`assert not PENDING & defined`), so they go red the moment the batch E files are moved. The edit, to be made in the same commit as the move (`spec-change.diff` is the reference):

- `packages/evalcore/tests/test_adr0023_recorded_scoring.py`: remove 5 names from `PENDING`: `test_cli_output_never_claims_accuracy`, `test_cli_prints_counts_with_denominators`, `test_cli_requires_every_privacy_and_language_input`, `test_report_never_claims_accuracy`, `test_report_shows_language_declared_by_user`.
- `packages/evalproviders/tests/test_evalproviders_adr0024.py`: remove `test_cli_fake_stt_runs_offline` from `PENDING`.

Before: the sets contain the names; after: they do not. The guards that `PENDING` is a subset of the SCOPE names and that the ADRs still cite them keep holding. ADR text edits that this batch makes necessary and qa did not make: none that a test requires; the following are recommended: ADR 0023 rule 14 (the second wording, ambiguity 11), ADR 0023 rule 1 (the exit codes and the labels), ADR 0024 rule 4 (`--env-file` and the replay engine), ADR 0017 Consequences (the report-layer control markers, ambiguity 8).

## 11. Evidence (`docs/phase3/evidence/red/batch-e/`)

Counts (2026-10-26). Everything ran in a scratch copy of the repository under the session scratchpad, one environment per Python through `UV_PROJECT_ENVIRONMENT` (the shared `.venv` was not used and stays on its default Python); the real tree changed only under `docs/phase3/`: nothing was created or edited under `packages/`. The copy holds no private fixture, no `.env.local` and no `marathi_samples`; nothing opened a network socket (an autouse guard in every module) and the only key is the canary.

- red (`red.py3.11.log` to `red.py3.14.log`), the four staged files in `packages/cli/tests` of a copy whose `tracer_cli` is the tree as it stands (the scaffold `cli.py` and `store.py`; no `report.py`, `summary.py` or `replay.py`), `--tb=line`: **127 failed, 21 passed, 1 deselected** on 3.11, 3.12, 3.13 and 3.14 (148 selected: 131 new and the 17 existing tests of the directory). `red-reasons.py3.13.txt` groups the 127 by the exception line: 90 `tracer_cli.cli` has no attribute `Environment` (every test that runs the command), 28 no module `tracer_cli.report`, 3 no module `tracer_cli.summary`, 1 no module `tracer_cli.replay`, 1 no attribute `run`, 1 no attribute `MAX_CONFIG_FILE_BYTES`, 2 `StopIteration` (the scaffold parser has no sub-command to find) and 1 `SystemExit: 2` (the scaffold parser refuses the `score` command). The 4 new tests that pass are guards that read source files only and so hold on the scaffold: `test_cli_has_no_key_option_and_reads_no_environment_but_in_environment_system` (the AST rules; its teeth are the mutants C71 to C73, C75 and C76) and the three cases of `test_every_test_named_by_the_scope_rows_of_batch_e_exists` (their teeth are the removal of a name from SCOPE or from the tests). The 17 others are the existing scaffold and store tests (unchanged).
- oracle (`oracle.py3.11.log` to `oracle.py3.14.log`), a hand-written minimal implementation (`oracle-src.patch`: `cli.py` rewritten, `summary.py`, `report.py` and `replay.py` new; about 1,200 lines of patch; not lint-clean or strictly typed, the tests are; the dev owns the real code and may differ wherever the contract does not pin it): `packages/cli/tests` on 3.11, 3.12, 3.13 and 3.14: **148 passed, 1 deselected** each.
- whole workspace with the oracle and the PENDING tightening of section 10 (`spec-change.diff`): `oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`: **11,921 passed, 1,288 deselected** on each (11,790 existing + 131 new). Without the spec change the two PENDING tests fail as section 10 says; no other earlier test changed.
- mutants (`mutants.txt`): **171 mutants of the oracle, 161 killed, 10 equivalent (each with its reason), 0 survived unexplained**: across the parser and the exit codes, the caps, the order of operations, the files and modes, the clock, the engine request, the cost builder, the summary text, the budget wording, the CSP, the escaping and the control markers, the histogram, the audio reference, the flags table, the replay engine and the AST source rules, the required `--stt`, and the STT text beside each flag (labels, the empty and insertion cases, the tokens of the range, the per-chunk pieces, the escaping, the data attributes). The first run left 22 survivors (and 2 mutants that did not apply); each led to a new test or to the mutant being corrected.
- the staged tests alone: `ruff check`, `ruff format --check` and `mypy` (repository configuration) pass on the four files and `test_cli_scaffold.py`; no non-ASCII byte in a staged file, in this contract, in a patch or in a log.

Not run: Python 3.15 (non-blocking); GitHub-hosted execution; Windows; any real send (owner-gated); a browser (the report is checked by structure only, ambiguities 10 and 18). Timing: the whole `packages/cli/tests` directory takes about 9 seconds on one core (the slowest case is the 1,200-turn export, under one second; the SCOPE-name guards and the 1,000,000-character token take about 0.4 seconds each); no test asserts a time. Logs are scrubbed of home, scratch and temporary paths and are under 5,000 lines each.
