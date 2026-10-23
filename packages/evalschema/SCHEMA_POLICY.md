# Schema policy: `tracer-evalschema` 0.0.1

Status: policy for schema version `0.0.1`, which is **unreleased** (no tag, no package
published). Written for task P1-20a (2026-10-24).

This file states the rules a producer or consumer of `tracer-evalschema` documents must know,
the limitations of the schema, and what is not guaranteed. The normative decisions are the
architecture decision records in `docs/adr/` (index: `docs/adr/README.md`) and the contract in
`docs/phase1/api-surface.md`. Where this file and an ADR disagree, the ADR is right and this file
has a bug. Statements that were not checked against code or tests are marked **(unverified)**;
the full list is in section 16.

Related documents: the generated field reference `docs/schema/` (one page per entity, rendered
from the JSON Schema files), the concept pages `docs/concepts/`, and `CHANGELOG.md` next to this
file.

## 1. Scope and roles

- The package describes evaluations of **an AI voice agent**. Only the agent is evaluated.
- The other party is the **caller**: a human (recordings of real calls, scored after the call)
  or a simulator (test calls the framework drives). `Run.caller_origin` is `simulated` or
  `human`, required, with no default.
- Speaker roles are exactly `caller`, `agent` and `unknown`. `unknown` is speech whose speaker the
  source does not identify (for example an undiarized transcript). Channel roles add `mixed` (one
  mono channel carrying both parties). There is no IVR role and no human hand-off or transfer
  concept; a call that reaches a human agent is outside the model (ADR 0016 Decision 1, ADR 0003
  Decision 1).
- The framework is bring-your-own-keys: it stores references to provider configuration, never
  keys (section 11).
- Money is USD only in 0.0.1 (section 10).

## 2. Models, JSON Schema files and where each rule lives

### 2.1 The two artefacts

- The **pydantic models** in `tracer_evalschema` are the source of truth. Every rule of the ADRs
  is enforced there.
- The **JSON Schema files** (draft 2020-12) are generated from the models and shipped as package
  data under `tracer_evalschema/schemas/`: one file `<ClassName>.schema.json` per entity (26 in
  0.0.1, the two bundles included), value objects under `$defs`, `$id` =
  `urn:tracer:evalschema:<version>:<ClassName>`, and `schema.lock` with the SHA-256 of every file
  and the schema version. The bytes are identical on Python 3.11 to 3.14 (verified on 3.11.16,
  3.12.14, 3.13.15 and 3.14.7). They are regenerated with
  `uv run python -m tracer_evalschema.schema_export --write` and never edited by hand; CI fails
  on any difference (`--check`). See ADR 0008 Decision 7.
- The **field reference** `docs/schema/*.md` is generated from those files by
  `scripts/generate_field_docs.py` and checked in CI the same way (`--check`).

A document that passes the JSON Schema is **not** necessarily valid. The schema states field
types, closed sets, patterns, presence rules on a closed discriminator (`if`/`then`) and
uniqueness of string arrays. It cannot state the rules below. **Validate with the models when you
need every rule** (for example `Model.model_validate_json`, or `loader.load_bundle` for bundles).
The JSON Schema is never stricter than the models: a document the models accept is accepted by
the schema (`test_schema_is_never_stricter_than_the_model`, `test_the_schema_is_never_stricter_anywhere`).

### 2.2 Code-only rules and the register

A rule JSON Schema 2020-12 cannot state (cross-record, arithmetic, cryptographic, a property of a
string's content, or a property of the JSON Schema language itself) is a **code-only rule**. Each
one is listed, with an owner, the documentation that states it and the mutations it explains, in
the register `CODE_ONLY_RULES` of the test suite (`packages/evalschema/tests/_support_b8.py`).
The tests keep the register exact in both directions: every difference between a model and its
exported schema must be claimed by a row, and every claim must still match a difference
(`test_every_difference_between_a_model_and_its_schema_is_claimed_by_a_rule`,
`test_every_claim_still_matches_a_difference`). The register is separate from
`KNOWN_SCHEMA_GAPS`, which lists only non-blocking mapping gaps (ADR 0008 Decision 8, gate
item 2). The register in the tests is authoritative; the list below is a reader's summary of
the rows present on 2026-10-24.

The P1-21 security audit added these code-only rules (revision 30); none changes a schema file,
the schema lock, a golden fixture or a field document, because a content check and
`hide_input_in_errors` are invisible to JSON Schema:

- `KeyRef.key_id` and `key_version` reject a credential-shaped value (section 9), wherever a
  `KeyRef` sits (`ContentDigest.key_ref`, `DekWrap.kek_ref`, both key references of a
  `SealedPayload`). This rule has its own register row, `CO-KEY-REF-CREDENTIALS`.
- `ProviderConfigRef.config_ref` runs the high-entropy rule on the name part after the scheme,
  for every scheme (row `CO-CONFIG-REF-CREDENTIALS`, section 11).
- Key separation in a `SealedPayload` compares `key_id` only (section 9).
- A keyed digest key has at least 32 bytes (section 6): a rule on a function argument, not on a
  document.
- Messages outside hiding models quote at most 64 code points of a rejected value (section 11).

### 2.3 Where the exported schema is looser than the models

Each family below is a set of documents the JSON Schema accepts and the models reject:

| Register row | What the models reject that the schema accepts |
| --- | --- |
| `CO-INTEGRAL-FLOAT` | a float with a zero fraction (`1.0`) where an integer is required (section 2.6) |
| `CO-IJSON-RANGE` | integers above 2**53 - 1 where no `maximum` is emitted (section 2.7), inside digested entities and inside `metadata` (section 11) |
| `CO-LABEL-TEXT` | human labels with control, bidi or default-ignorable characters other than ZWJ and ZWNJ, or with surrounding white space |
| `CO-KEYED-UNIQUENESS` | two list entries with the same key (record ID, `entry_key`, unit, primary language, binding, channel index, language tag compared by canonical case) |
| `CO-LONG-LIST-UNIQUENESS` | an exact repeat in an array of objects (section 2.4) |
| `CO-PROMPT-TEMPLATE-VARIABLES` | judge prompt templates whose variables differ from `prompt_variables` |
| `CO-ONE-OF-SUBSET-OF-SCALE` | `one_of` options outside the enum scale |
| `CO-RANGE-BOUNDS-ORDER` | `min > max`, or range bounds outside the unit's or scale's domain |
| `CO-RUBRIC-ITEM-NEEDS-PASS-RULE` | a required (or weighted) rubric item whose definition has no pass rule |
| `CO-BINDING-PARAMS-DECLARED` | binding parameters the definition does not declare |
| `CO-TOOL-ARGUMENTS-PARSE` | `arguments_complete=true` with `arguments_raw` that is not one whole JSON object |
| `CO-STORED-CURRENCY-LABEL` | a stored currency label that contradicts its `na_reason` |
| `CO-URL-RULES` | credential parameter names and credential patterns in URLs, loopback hosts where refused, fragments, percent-encoding errors |
| `CO-CONFIG-REF-CREDENTIALS` | a `config_ref` that holds a credential instead of naming one |
| `CO-KEY-REF-CREDENTIALS` | a `KeyRef` `key_id` or `key_version` that holds a credential instead of naming a key |
| `CO-PROFILE-PLACEHOLDERS` | unknown or malformed profile placeholders, and placeholders without a test profile |
| `CO-DIGEST-VERIFICATION` | a `content_digest` that does not match the document |
| `CO-TURN-TIMING-ORDER` | `end_ms < start_ms` and the other turn timing rules |
| `CO-VERDICT-COUNTS` | verdict counts that do not add up, or a status that does not follow from them |
| `CO-PRICE-ARITHMETIC` | price ranges out of order or overlapping, negative prices, minimums, amounts and totals |
| `CO-PRICE-NOTE-NOT-BLANK` | a price provenance `note` made only of white space |
| `CO-BUNDLE-CROSS-RECORD` | bundle rules that read several records (references, tiers, results against definitions, the applicability re-run of every result against the run, export protection rules) |
| `CO-SUMMARY-CONSISTENCY` | cost summaries inconsistent with their records |
| `CO-CROSS-RECORD-CHECKS` | the `check_*` functions, contiguous indexes and ranks, reference resolution |
| `CO-CHANNEL-AGENT-COUNT` | more than one agent channel, or a channel index outside the audio's channels |
| `CO-PER-TURN-SUMMARY` | per-turn shares and counts that do not follow from each other |
| `CO-PYTHON-DOLLAR-NEWLINE` | a trailing newline that Python validators accept (section 2.5) |

### 2.4 `uniqueItems` (A22)

`uniqueItems` is emitted **only on arrays of strings** (closed-set members, IDs, language tags,
option names). It is not emitted on any array of objects: the record lists of `RunBundle` and
`ExportBundle`, `PricingCatalog.entries`, `metadata_flags`, `ConfigSnapshot.metric_definitions`
and `ConfigSnapshot.metric_bindings`, and every other array of objects. No exported schema bounds
the length of those arrays, and a validator compares objects pairwise, so the keyword would cost
time quadratic in a length the document chooses (measured with `jsonschema` 4.26 on a bundle of
4,000 turns: about 4.2 s with the keyword, 0.5 s without). The models still reject a repeated
record, in linear time. This is the register row `CO-LONG-LIST-UNIQUENESS` (ADR 0008 Decision 8).

A consumer that validates untrusted bundles with the JSON Schema alone must therefore apply a
byte limit before parsing (the models' loader refuses more than 256 MiB by default,
`loader.DEFAULT_MAX_BUNDLE_BYTES`, before parsing; ADR 0008 Decision 6) and check repeated
records itself.

### 2.5 Patterns and Python's `$` (S-N90, A14)

Every `pattern` starts with `^` and ends with `$` and uses syntax that means the same in ECMA-262,
Python and the common Rust and Go engines. In ECMA-262 `$` does not match before a trailing
newline; in Python's `re` it does. So a Python validator (the `jsonschema` package) accepts
`"abc\n"` for `^[a-z]+$`, while ECMA-262 validators reject it (checked with `jsonschema` 4.26 on
2026-10-24). The models match patterns as full matches and reject the trailing newline
everywhere. The schema is left as it is (emitting `\Z` would not be ECMA-262). If you validate
with a Python JSON Schema validator, also reject strings that end in a newline where a pattern
applies, or validate with the models. Register row `CO-PYTHON-DOLLAR-NEWLINE`.

### 2.6 Integers written as `1.0` (A15)

JSON Schema 2020-12 counts a number with a zero fraction as an integer, so no keyword can reject
`1.0` in an integer field (checked with `jsonschema` 4.26). The models reject a float there,
including `1.0`. **Producers must write integers without a fraction or exponent** (`1`, not
`1.0` or `1e0`). Register row `CO-INTEGRAL-FLOAT`.

### 2.7 No `maximum` on `*_ms` fields (A16)

The `*_ms` fields (`start_ms`, `end_ms`, `duration_ms`, `ttfb_ms`, `offset_ms`) carry the base
schema `{"type": "integer", "minimum": 0}` with no `maximum` (ADR 0002 Decision 2; a field may
add bounds, as `Evaluator.max_duration_ms` does). The models reject values above 2**53 - 1, the
largest integer every JSON implementation reads exactly. Register row `CO-IJSON-RANGE`.

### 2.8 Descriptions in the schema files (A5)

The `description` text of each schema object is the model's docstring, copied unchanged. It
cites internal decision identifiers (for example `P-16`, `N42`, `A22`, `O3`); they refer to the
decision records in this repository (`docs/adr/`, `docs/phase1/SCOPE.md`,
`docs/phase1/api-surface.md`) and are kept, because they let an auditor trace a rule to its
decision (finding A29, option (a)). Each description is self-contained: no description points at
a module docstring or other text that a reader of the schema file alone cannot see; the rules of
a model are stated in its own docstring (A29). Descriptions are informative, never normative: a
rule that a description states and no keyword expresses is enforced only by the models.

### 2.9 Size, depth and length limits

- **Size.** Bundles are bounded only by the loader: `loader.load_bundle` refuses input of more
  than 256 MiB of UTF-8 (`DEFAULT_MAX_BUNDLE_BYTES`) before it is parsed.
- **Depth of JSON text.** The strict loader (`loader.loads_strict`, used by `load_model` and
  `load_bundle`) refuses JSON text nested deeper than 256 levels (`DEFAULT_MAX_DEPTH`,
  `DepthLimitExceeded`).
- **Depth of Python objects.** Only the JSON-text loader bounds depth. A caller that passes
  Python objects to `model_validate` directly gets no depth limit from the models: a `metadata`
  dict nested 5,000 levels deep raises Python's `RecursionError`, not a validation error. Code
  that validates untrusted Python objects must bound their depth itself, or serialize them and
  use the loader.
- **No length limits in 0.0.1.** Labels, lists and `metadata` have no maximum length, item count
  or member count in the models or the JSON Schema; the size limit of the loader is the only
  bound. This is intended for 0.0.1 and is revisited together with the Phase 2 storage limits; no
  number is fixed before then.

## 3. Versioning and compatibility (ADR 0008, ADR 0009)

- **One version.** `SCHEMA_VERSION` is a semver string, equal to the package version; the first
  release is `0.0.1`. Every entity carries `schema_version`, which is required on input and must
  be `MAJOR.MINOR.PATCH` with non-negative integers without leading zeros, compared numerically.
- **Reader rules.** A newer version raises `SchemaVersionTooNew`; fields are never dropped
  silently. While the version is `0.0.x` the reader requires an **exact match**, so an older file
  raises `SchemaVersionMismatch`. A malformed version raises `SchemaVersionInvalid`. These errors
  derive from `TracerSchemaError`, are not wrapped by pydantic, and carry the found and expected
  versions. Unknown fields are rejected everywhere (`extra="forbid"`), duplicate JSON object keys
  are rejected and nesting depth is limited by the strict loader.
- **Bundles are single-version.** Every record in a `RunBundle` or `ExportBundle` has the same
  `schema_version`.
- **Change classes during 0.0.x.** Every release bumps the patch and is treated as incompatible.
  The CHANGELOG still classifies each change: additive (new optional field, new closed-set
  member, new digest scheme, new ID prefix) or breaking (removing, renaming, retyping,
  tightening), and a breaking change carries a migration note. Exceptions stated in the ADRs:
  adding a currency is breaking (it reshapes `CostSummary`, ADR 0014 Decision 5); changing the
  metadata flagging rule is a schema change (ADR 0007 Decision 5a).
- **From 0.1.0 (P-22).** Between 0.1.0 and 1.0.0 a minor bump is breaking and a patch is
  additive. From 1.0.0 a reader accepts the same major with a minor up to its own (missing
  optional fields become `None`), rejects a newer minor with `SchemaVersionTooNew`, and rejects
  another major unless the document is upgraded through `upgrade()`. From 0.1.0 every release
  ships an upgrade step and upgraded twins of its frozen fixtures. No upgrade step exists in
  0.0.1.
- **Frozen fixtures and locks.** Each released version `V` keeps its fixtures under
  `tests/fixtures/frozen/<V>/`, immutable once released. When `V` is released its `schema.lock`
  is recorded there too; any later schema change must then bump the version (and the package
  version, and add a CHANGELOG entry) before regenerating. Before the first release the lock is
  simply regenerated.
- **Entity versions.** Versioned entities (Persona, TestProfile, Evaluator, MetricDefinition,
  MetricBinding, Rubric, PricingCatalog, AgentTarget, Dataset) keep one `id` across edits; each
  edit is a new record with `version + 1`, and `(id, version)` is immutable. Its `content_digest`
  is verified on load. References to them are objects `{id, version, content_digest?}`, never
  strings. Every other entity is a record of fact, written once and superseded only by a new
  record with a new ID; a bundle that holds two contents under one ID is rejected.
- **Database.** No database tables exist in 0.0.1, so there are no migrations yet. Phase 2
  keys versioned tables on `(id, version)` and records of fact on `id` (ADR 0009 Decision 6).

## 4. Missing data: `not_applicable` and `not_available`

The core rule: consume telemetry when present, never break when absent, and never assume or
extrapolate.

- A metric whose inputs are missing is `not_applicable`, with `value` and `passed` both `None`
  and a closed `na_reason` (`missing_input` with `missing_kinds`, `agent_channel_unknown`,
  `language_not_observed`, `unsupported_language`, `unsupported_script`, reported in that
  precedence). It is never 0, false or failed (ADR 0004).
- A cost that is neither reported nor computable is `not_available` with a closed `na_reason`.
  It has no amount and no `basis`, is excluded from totals, and the total says what it excludes.
  It is never 0 (ADR 0014).
- Applicability is decided per input kind from `Run.available_inputs`, derived from the evidence
  the run holds. `Run.observed_tier` (`T0`, `T1`, `T2`) is a display summary; no logic reads it
  (ADR 0004 Decisions 2, 2a).
- Importers never fill a field by guessing: an absent source value stays absent (or uses the
  explicit `unknown` member of a closed set) with an ingest warning (ADR 0018 Decision 2).

### 4.1 Bundle checks: every result is re-checked against its run (ADR 0004 Decision 9)

A `RunBundle` (alone or inside an `ExportBundle`) does not take a stored result's applicability
on trust. Its last rule, tagged `[applicability]`, re-runs `results.applicability` for every
result with the metric definition the bundle's snapshot embeds, over the run and the
transcripts and turns the result read, and rejects the bundle when the stored result differs in
the status class (`not_applicable` or not), `na_reason`, `missing_kinds` (in the order of
`requires`), `language_source` or `declared_language_transcript_ids`. So a result that claims a
score for an input the run does not have, or `not_applicable` for an input it has, or an
observed language where none was observed, does not load.

- **Which transcripts a result read.** `MetricResult.transcript_ids` (optional) names them. A
  bundle with one transcript derives it and a bundle with none reads none; a bundle with several
  requires the field on every result of a metric that reads transcripts or is language
  dependent (`transcript_ids required when the bundle holds several transcripts`). The turns
  read are those of the named transcripts, only the agent turns for `agent_transcript`.
- **Error results.** An `error` result is not compared on the status class, except that an
  `error` where the run lacks an input (`missing_input`, `agent_channel_unknown`) is rejected:
  an error is for a metric whose inputs existed. Its language provenance is compared.
- **Agent turns.** A run that lists `agent_transcript` and whose bundle holds turns has at least
  one agent turn, unless a turn is declared withheld.
- **Withheld records.** With a transcript or turn declared withheld the language steps cannot be
  re-run, so only the run-level reasons are judged and the language outcome is accepted as
  stored; the same holds for a per-turn metric that reads no turn. Other withheld kinds take
  nothing away.
- **Not re-checked.** The value itself, the per-turn counts against the turns, and whether an
  `agent_transcript` metric named a transcript that has agent turns. The message names the
  result's ID, its position `results[<index>]` and the first field that differs; it never
  quotes a text, a tag or a reason value.

The JSON Schema cannot express any of this (it reads several records); it is part of the
register row `CO-BUNDLE-CROSS-RECORD`.

## 5. Verdict, weighted score and per-turn share (ADR 0013, ADR 0010)

- **Headline.** `render_headline()` returns exactly `"{passed}/{required_total} required checks
  passed"`. The denominator counts every required check, including `not_applicable` and
  `errored` ones. Weights never change the headline.
- **Status.** `pass` iff every required check passed; `fail` iff any failed; otherwise
  `inconclusive` (none failed, some did not run). A stored status that contradicts the counts is
  rejected.
- **Weighted score (P-33).** Over the weighted checks that were scored (`passed` true or false):
  `weighted_score = Σ(w · passed) / Σ(w)`, with `passed` as 1 or 0. `not_applicable` and errored
  weighted checks are excluded and listed in `weighted_excluded`.
- **Coverage.** `weight_coverage = Σ(w over scored weighted checks) / Σ(w over all weighted
  checks)`. It is required whenever `weighted_score` is present, and both are absent when nothing
  weighted was scored: there is never a score of 0 standing for "nothing measured". Both are exact
  decimals, rounded half-even at scale 18 only when the quotient does not terminate.
- **Rendering rule.** No report or helper shows the weighted score without its coverage. The one
  helper is `render_weighted_score(verdict)`, which returns `"weighted score {score} (weight
  coverage {coverage})"` with two decimal places (display only), or `None` when there is no
  score.
- **Per-turn share (P-26, P-56, P-58).** A metric aggregated as `per_turn_share` stores its share
  in `MetricResult.per_turn`, never in `value` (which is `None`). The share is `passed turns /
  (passed + failed turns)`: turns that are `not_applicable`, and turns whose judge verdict is
  `undetermined`, are excluded from the denominator and counted separately. With no counted turn
  the result is `not_applicable` (every turn not applicable) or `error` with `judge_undetermined`
  (some turn undetermined). `passed` is the definition's range pass rule applied to the share. In
  the verdict such a metric is one check and contributes `w · passed`, never `w · share`.
- **Turn coverage.** The share is never shown without its turn coverage (counted turns over all
  turns). The one helper is `render_per_turn_share(result)`, which returns `"turn share {share}
  (turn coverage {counted}/{total} turns)"`, or `None` when the result is not scored.

## 6. Digests (ADR 0006)

- **Scheme registry.** `ContentDigest{scheme, value, key_ref?}`. 0.0.1 registers exactly two
  schemes; `value` is 64 lowercase hex characters for both:

  | Scheme | Computation | `key_ref` |
  | --- | --- | --- |
  | `sha256-jcs/v1` | plain SHA-256 over the RFC 8785 (JCS) canonical UTF-8 JSON | must be absent |
  | `hmac-sha256-jcs/v1` | HMAC-SHA-256 over the same bytes | required: a `KeyRef` naming the key |

  An unknown scheme raises `UnknownDigestScheme`; it is never accepted as "unverifiable". A new
  scheme is an additive change. The HMAC key is resolved outside the schema and is never stored
  in any document, bundle or export.
- **Domain.** The digest covers the document's canonical JSON with `content_digest` and
  `schema_version` removed at the top level and every `null` object member removed at every depth
  (so inside `metadata` a `null` member and an absent member have the same digest). Entity-specific
  exclusions are listed in ADR 0006 Decision 5 (the profile digest inside a snapshot and inside an
  Evaluator, the `schema_version` of records embedded in a snapshot). Digested documents must be
  I-JSON: no integer beyond 2**53 and no non-finite number.
- **One verification entry point.** `digest.verify_digest(obj, digest, *, key_resolver=None)`
  returns `None` on a match and raises otherwise (`DigestMismatch`, `UnknownDigestScheme`,
  `DigestKeyUnavailable`, `NonIJsonValueError`). A keyed digest without an available key raises
  `DigestKeyUnavailable`; it never reports success. Its message names the failure kind only (no
  resolver, the resolver failed, the resolver returned no key bytes), never the `key_id`,
  `key_version` or `provider_hint`; a resolver's exception is chained as `__cause__` and nothing
  of it (text, arguments, class name) is quoted.
- **Key length.** A key for `hmac-sha256-jcs/v1` has at least 32 bytes, in `compute_digest` and
  for the key a resolver returns to `verify_digest`. A shorter key raises `WeakDigestKeyError`,
  which is both a `ValueError` and a `DigestKeyUnavailable`; on the verify side it is raised
  before any content is hashed, so a short key is never reported as `DigestMismatch`. The message
  says `at least 32 bytes` and names the scheme, never key bytes or the key id. A key that is not
  `bytes` (a `bytearray` included) is still a `TypeError`.
- **Where each scheme may be used.** Internal data (snapshots, runs, run bundles, `internal`
  exports) carries the profile digest `TestProfileRef.content_digest` as a **plain** SHA-256
  digest (an `internal` export requires it). In a **shared export** or a benchmark export
  (`ExportBundle.purpose` `shared_export` or `benchmark`) every profile digest is either
  **dropped** (absent) or **keyed** with `hmac-sha256-jcs/v1`; a plain one is rejected. The
  `plaintext_digest` of every sealed payload follows the same rule: dropped or keyed (section 9). The
  snapshot and Evaluator digests exclude the profile digest, so a recipient can still verify them
  after the profile digest was dropped or keyed.
- **Limitation: low-entropy profiles in internal data.** A plain SHA-256 digest of a
  low-entropy profile (for example a 10-digit phone number plus a date of birth) can be
  brute-forced offline by anyone who holds the internal snapshot or bundle: they hash guesses
  until one matches, and learn the profile. The schema accepts this for internal data (owner
  decision OQ5) and protects only what leaves it: shared exports never carry such a plain digest.
  Treat internal snapshots and bundles as personal data.

## 7. Profile placeholders and the free-text lint (ADR 0006 Decisions 9, 9a)

- Test-profile values (names, dates of birth, account numbers) live only in `TestProfile`, whose
  personal fields are marked `x-pii` in the JSON Schema. A `ConfigSnapshot` holds a reference to
  the profile, never its fields.
- Free text that must mention a profile value uses a placeholder: exactly `{{profile.<field>}}`
  (for example `{{profile.name}}`), in `Evaluator.instructions`, `ScriptedTurn.text`,
  `Evaluator.expected_outcome` and string values of `ExpectedToolCall.arguments`. Placeholders are
  stored unresolved and filled at run time; an unknown field or a malformed placeholder is
  rejected, and an Evaluator with a placeholder must reference a test profile.
- The lint `lint_profile_values_in_free_text(snapshot, profile)` warns (code
  `profile_value_in_free_text`, with the path, never the value) when free text in a snapshot
  contains a literal value equal to a field of the profile it is given.
- **Limitations of the lint.** The lint can only find values of the profile it is given.
  Personal data typed into free text that is in no profile (for example a real person's name used
  as an example) cannot be detected and remains the author's responsibility. The lint also does
  not find another format of the same value (a date written differently) or a part of a value (a
  first name alone). It folds case, Unicode normalization, white space and native digits before
  comparing, using the running Python's Unicode database, so results may differ between Python
  versions for very new characters (section 15). Non-string values inside tool-call arguments
  are never checked.
- `ConfigSnapshot`, `Evaluator` and the models they embed hide their input in validation errors,
  because their free text may hold personal data the lint cannot see (section 11).

## 8. Privacy markers, consent and export rules (ADR 0016)

- **Markers.** Every `Artifact`, every `Transcript` and every recording- or transcript-based
  `DatasetItem` carries a `PrivacyMarker{human_subject, human_subject_reason?, consent?,
  retention?}`. Consent and retention are present if and only if `human_subject` is true.
  Consent status is `obtained`, `not_obtained` or `unknown`. Tool-call arguments and results take
  their run's derived value.
- **Derivation.** `privacy.derive_human_subject(run, turns)` is true when `caller_origin=human`,
  when any role evidence (turn roles, channel roles) is `unknown`, or when there is no role
  evidence at all. It is false only for a simulated run whose role evidence shows only the caller
  and the agent (a `mixed` channel counts as both). `turns` is a required argument; passing
  nothing or `None` is a `TypeError`.
- **Stricter, never looser.** A stored marker may say true where the derivation says false, with
  a reason (`marked_before_role_evidence`, `real_person_likeness`, `manual`); a person may always
  mark true. A stored false where the derivation says true is rejected. A too-loose marker is
  corrected by a new record with a new ID, never by editing the old one.
- **Export purposes.** `ExportBundle.purpose` is `internal`, `shared_export` or `benchmark`.
  The label is **self-declared** by whoever wrote the bundle: a bundle can say `internal` (which
  turns the shared-export rules off) while a consumer takes it for a shared one. A consumer that
  needs a purpose demands it with the keyword-only `expected_purpose` of
  `bundle.check_export_bundle` or `loader.load_bundle`: `None` (the default) accepts any purpose;
  a different label is refused with a `ValueError` tagged `[purpose]` naming both purposes. A
  value outside the three purposes is a call error (`TypeError` if not a `str`, `ValueError`
  otherwise), and `load_bundle(RunBundle, ..., expected_purpose=...)` is a `TypeError` because a
  run bundle has no purpose. The model itself (`ExportBundle.model_validate`) accepts all three.
  - `internal` may carry human-subject data in the clear and plain profile digests.
  - `shared_export` and `benchmark`: every audio, text, tool-argument and tool-result payload that
    is human-subject at export time must be sealed (section 9) or carry a `RedactionRecord`
    covering that payload kind (`audio`, `text`, `tool_arguments`, `tool_result`). A
    `ProvenanceRedaction` never counts as protection.
  - `benchmark` additionally requires `consent.status=obtained` for every human-subject payload.
    `not_obtained` blocks it even when sealed or redacted; `unknown` is treated as not obtained.
    Tool calls, which have no marker, are covered by the consent of the run's human-subject
    artifacts and transcripts.
- **Export-time value (P-57).** Both export rules use `privacy.export_human_subject(marker, run,
  turns)`: a marker with reason `marked_before_role_evidence` is re-derived from the role
  evidence present in the bundle being validated; `manual` and `real_person_likeness` stay true;
  a stored true without a reason stays true. The stored record is never edited. A simulated run
  whose bundle shows only caller and agent roles therefore needs no seal, redaction or consent.
- **Other export rules.** Every export purpose rejects a run carrying `caller_external_id`; the
  exporter strips it. `shared_export` and `benchmark` reject artifact URIs with the `file:` scheme
  or a loopback host, embedded test profiles in the clear (a profile travels only as a sealed
  payload) and a `DatasetItem.source_run_id` that names no run of the export (the exporter drops
  it and records a `ProvenanceRedaction`).
- **Retention.** The marker carries the policy (`retain_until`, `policy_ref`,
  `delete_on_expiry`). Nothing in 0.0.1 deletes data; enforcement is Phase 2 or later work.
- **The schema does not redact.** A `RedactionRecord` is an assertion by a tool (`tool_ref`); the
  schema cannot verify that redaction was correct. Redaction of audio is not reliable, which is
  why benchmarks also need consent.
- **No export builder in 0.0.1.** The package validates exports; it does not build them (finding
  B19, deferred). Whoever writes an `ExportBundle` performs the transformations above (dropping
  or keying digests, stripping caller identifiers, sealing, dropping unresolved `source_run_id`
  values) and must validate the result with the models.

## 9. Sealing (ADR 0015)

- `SealedPayload` exists only inside `ExportBundle.sealed_payloads` (any purpose). Snapshots,
  runs and run bundles refuse it.
- Format `tracer.sealed.v1`: a fresh 256-bit data key per payload, AES-256-GCM with a 12-byte
  nonce, the data key wrapped with AES Key Wrap (A256KW) under a key-encryption key named by a
  `KeyRef`. A `KeyRef` names a key and never carries key material. The AAD is the JCS bytes of
  `aad_fields` (`payload_type`, `subject_id`, `subject_version` for test profiles only,
  `schema_version` of the plaintext) with null members removed, so a ciphertext cannot be moved to
  another subject. Binary fields are canonical base64url without padding; the ciphertext ends with
  the 16-byte tag. Unknown `format` or `alg` values are rejected.
- Payload types: `test_profile`, `audio`, `transcript_text`, `tool_arguments`, `tool_result`.
  One sealed payload per `(payload_type, subject_id)`, and its subject must be in the export.
- **Digest rule.** `plaintext_digest` is absent or keyed (`hmac-sha256-jcs/v1`) for every payload
  type; a plain digest is rejected. A present digest requires a JSON-object plaintext. Its
  `key_ref` must not name the key-encryption key: the two references name the same key when
  their `key_id` strings are equal, whatever their `key_version` values (a version of a key is
  still that key). `key_id` is compared verbatim: case, trailing white space and NFC against NFD
  make different ids. The error is `key_separation` at `plaintext_digest.key_ref` and quotes no
  key id.
- **Key names are never key material.** `KeyRef.key_id` and `key_version` reject a
  credential-shaped value with the error type `key_ref_credential`, at the field, with one
  message for every rejected value that quotes no part of it. Rejected: a known credential prefix
  (ADR 0007 Decision 5a) anywhere in the value, also after `/`, `:` or `_`; any `=` (a
  `key=value` pair or base64 padding); a high-entropy run (32 or more of `[A-Za-z0-9+/=_-]` with
  an upper-case letter, a lower-case letter and a digit, 16 distinct characters) inside one
  segment of the value split at `/` and `:`. Accepted and stored verbatim: vault names, KMS
  aliases and ARNs, GCP KMS resource names, URN-like ids, UUIDs, 32 or 40 hexadecimal digits in
  one letter case (a 40-digit OpenPGP fingerprint is a key name), long names without a digit and
  non-ASCII names. `provider_hint` has no credential rule (it is a machine key). `KeyRef`,
  `ContentDigest` and every model that embeds a `KeyRef`, a `ContentDigest` or a `DekWrap` hide
  their input in validation errors (section 11).
- **What a sealed payload replaces.** The metadata record of the subject stays in the export. A
  sealed `transcript_text` replaces the transcript's turns, which are then absent and declared
  withheld. A sealed `tool_arguments` or `tool_result` payload protects only when the clear fields
  (`arguments_raw`, `arguments`; `result_raw`) are absent.
- **Exporter duty: the artifact `uri`.** When an exporter seals the audio of an artifact, it
  **replaces the artifact's `uri` with the location of the sealed copy**. The original `uri`
  names where the plaintext recording lives, and the schema cannot check this (a URI does not say
  what it points to), so a correct export depends on the exporter doing it (decision B3).
- The package contains no cryptography. The reference seal and unseal are test-only (the
  `cryptography` package is a test dependency); production key management is outside Phase 1.

## 10. Cost (ADR 0014)

- `Money{amount, currency}`: `amount` is a canonical decimal string (no float, no exponent, no
  trailing zero, at most 18 fractional digits); `currency` is exactly `USD` in 0.0.1. There is no
  FX conversion. Configured and user-supplied prices must be USD.
- `CostRecord.basis` is `reported` or `computed` and is present if and only if the cost is
  `available`. There is no `estimated` basis. A computed cost needs known usage of the billable
  event and a known price including billing rules (`increment`, `minimum`, each a value or
  `"none"`; `"unknown"` billing gives `billing_rules_missing`). A partial sum is never reported.
- Agent-under-test costs are computed only from usage that the provider or telemetry reports; a
  recording's duration is never the agent's speech-to-text usage, and a black-box agent's LLM
  tokens are `not_available` (`usage_not_reported`).
- A reported cost in another currency (`unsupported_currency`), without a currency
  (`currency_not_reported`, unless a cited API contract states it), in USD with an amount that is
  not a canonical non-negative decimal after lexical normalization (`amount_unparseable`), or
  with a USD label that conflicts with a cited API contract (`currency_conflict`) is
  `not_available` with the raw text kept in `reported_raw`; it is never converted and the import
  is never rejected. Importers map `usd` and ` USD ` to
  USD with a warning and never map `$`.
- `CostSummary` has one optional USD `total`, present if and only if at least one record is
  included; `completeness` is `complete`, `partial` or `none`. Each usage record counts in at
  most one included cost record (P-54): an available reported record is preferred over a
  computed one for the same usage.
- **Limitation: a reported cost without usage records.** An available reported cost without
  usage records (a source that reports a cost but not the usage behind it) shares no usage with
  any other record in a summary. If a computed record for the same call is also included, the
  double count is not detected: the summary includes both and the total counts the call twice.
  Importers must link the usage records whenever the source identifies them, so that the P-54
  rule can find the overlap.

## 11. Secrets and errors (ADR 0007)

- **No secret-bearing fields.** No model has a field meant to hold a credential. Providers are
  referenced as `ProviderConfigRef{provider, model, config_ref}`, where `config_ref` names a
  secret (`env:NAME`, `keyring:...`, `vault:...`) that a runtime outside the schema resolves; a
  value with `=` or a known key prefix is rejected, and so is a name part (after the scheme, for
  every scheme) with a high-entropy run. Upper-case environment variable names such as
  `env:OPENAI_API_KEY`, lower-case names and paths such as `vault:secret/data/tracer/sarvam`
  stay valid. Keys for sealing and keyed digests are named by `KeyRef`, whose `key_id` and
  `key_version` reject credential-shaped values (section 9). Unknown fields such as `api_key` are rejected on every model, and a contract test
  fails on any property name from the credential denylist.
- **URLs.** Endpoint, artifact and price-provenance URLs reject userinfo, 25 credential query
  parameter names (compared percent-decoded, case-folded, with `-` equal to `_`) and known
  credential patterns anywhere in the URL. Presigned URLs are runtime-only and never stored.
  Artifact URIs use only `http`, `https`, `s3`, `gs` and `file:///` with an absolute path, and no
  fragment.
- **Metadata.** `metadata` is a user-owned I-JSON object (RFC 7493, `ijson.IJsonObject`): no
  NaN or infinities, integers within +-(2**53 - 1) (finite floats such as `1e300` are accepted),
  no lone surrogates in strings or keys, and no Python value that is not JSON (tuples, sets,
  bytes, non-string keys). The error names the JSON Pointer of the member, never its value. The
  JSON Schema states only `type: object` (`CO-IJSON-RANGE`). Values are kept unchanged. A string that looks like a credential is kept
  byte for byte and flagged in `metadata_flags` (`known_credential_prefix` or
  `high_entropy_token`), never dropped or altered; the flags are verified on load. Redaction of
  flagged metadata is not in 0.0.1 (known gap A-03), so **remove credentials from metadata before
  sharing a document**.
- **Errors never echo a credential or personal data.** Every model that can reach a
  credential-checked field or holds personal data or user-typed text hides its input in
  validation errors (`hide_input_in_errors`), and the set is closed under embedding; inside those
  models no message or error context quotes a rejected value, only the field location and the
  rule. The set includes `KeyRef`, `ContentDigest`, `VersionedRef` and its subclasses, and
  every model that embeds one of them or a `DekWrap`, inherited fields included. Bundle and
  export messages name records by ID and the rule, never a value. `DigestKeyUnavailable` names
  the failure kind only, never a key id; a key resolver's exception is chained, never quoted.
- **Echoed values are bounded.** Outside a model that hides its input, a message may quote a
  rejected value (a pattern or closed-set mismatch, a typed ID, a language tag, a UTC instant, a
  decimal string, an unknown digest scheme from an unvalidated record, a schema version, a
  duplicate JSON key). Up to 64 code points the quote is the value's `repr`; a longer value is
  cut to its first 64 code points followed by three ASCII dots `...`. The size of a message
  therefore does not grow with the size of the invalid value.
- **Network locations are not vetted for reachability (SSRF).** The schema accepts private
  address ranges and link-local addresses in `SipEndpoint`, `WebSocketEndpoint`,
  `WebRtcEndpoint` and `Artifact.uri` values, by design: a test target or a recording store is
  often on a private network, and a schema cannot know where a name will resolve. Any consumer that fetches or dials such a value from an untrusted bundle must
  resolve the name itself and deny private, loopback and link-local ranges (after every
  redirect and DNS answer), apply rate limits and offer a TLS-only option. This is a Phase 2
  requirement on every fetcher and dialer.
- **Phase 2 requirements from the P1-21 audit.** Every ingest, exporter and CLI entry point that
  loads an `ExportBundle` must pass `expected_purpose` (required there; the library default
  `None` stays for library callers, section 8). The judge runner must isolate untrusted text in
  prompts: fixed delimiters around every untrusted variable, an untrusted-variable tag the
  prompt names, output validated against a schema before use, and a size cap on every untrusted
  input and on the output.
- **Logging.** `ValidationError.errors()` always contains the input, whatever the configuration.
  Code that logs or returns validation errors must call `errors(include_input=False)` (or
  `json(include_input=False)`).
- **Typed errors.** Version, digest, loader and bundle errors derive from `TracerSchemaError`,
  are not wrapped by pydantic and propagate as themselves out of `model_validate`; catch them by
  class.

## 12. Language rules (ADR 0005, ADR 0004 Decisions 6 and 7)

- **Configured languages** (`SupportedLanguage`, used by Persona, ScriptedTurn, Evaluator,
  DatasetItem and AgentTarget): exactly nine, English `en`, Hindi `hi`, Marathi `mr`, Telugu
  `te`, Tamil `ta`, Kannada `kn`, Malayalam `ml`, Punjabi `pa` (Gurmukhi script) and Gujarati
  `gu`, as BCP-47 tags. A region is kept (`hi-IN` stays `hi-IN`); configured regions are exactly
  `IN`, `US` and `GB`, and other regions are rejected with a message (adding one is a data
  change). Case is canonicalized (`HI-in` becomes `hi-IN`); nothing else is rewritten.
- **Native script only.** A configured tag may carry a script subtag only if it is a native
  script of its language (`pa-Guru-IN` is valid; romanized tags such as `hi-Latn` are rejected).
  Transliterated or romanized configuration is not supported in 0.0.1.
- **One language per configured value.** There is no type, field or alias that combines two
  languages in one configured value in 0.0.1, and such a tag is rejected with a message that says
  it is unsupported. Text that mixes scripts inside an observed turn is valid and stored as is.
  Other languages are not configurable in 0.0.1 (adding one needs data files, fixtures and native
  review); the concept page `docs/concepts/language.md` and ADR 0005 Decision 8 list the
  exclusions.
- **Observed languages are never rejected.** A tag a provider or source reports
  (`ObservedLanguageTag` on `Turn.language` and `Transcript.detected_language`) is checked for
  BCP-47 syntax only: any well-formed tag is accepted, including languages outside the nine and
  romanized tags; the raw label is kept verbatim (`detected_language_raw`, `Turn.language_raw`).
  Importers turn `_` into `-` with a warning, and a label that is not a tag stays raw with the
  normalized field empty.
- **Configured is never observed.** A configured language is never used in place of an observed
  tag. Untagged input makes a language-dependent metric `not_applicable`
  (`language_not_observed`); a user may declare the language of an untagged import
  (`Transcript.declared_language`), which is recorded as a declaration, never as an observation,
  and is never used by script-sensitive metrics.
- **Script-sensitive metrics** (`MetricDefinition.script_sensitive=true`, for example WER) are
  `not_applicable` with `unsupported_script` on observed text whose tag names a non-native script.
  In 0.0.1 the script is known only from the script subtag of an observed tag.

## 13. Text and display safety (ADR 0017)

- Free text is stored code point for code point: no normalization, no trimming. NFC and NFD
  forms are different values. ZWJ and ZWNJ are preserved everywhere. Lone surrogates are rejected
  by the type; importers replace them with U+FFFD and record `text_not_valid_unicode`.
- `contains_bidi_controls` is computed from the text and verified on load.
- Untrusted text must be shown through `text.display_safe()`, which isolates it (FSI ... PDI) and
  shows bidi control characters as visible markers such as `[RLO]`.
- Names, labels and keys reject bidi controls, invisible characters and control characters.

## 14. Mappings and the gap register (ADR 0018)

A mapping from a source format (Pipecat spans, Langfuse, provider responses, agent exports) fills
a field only from a source field or a documented derivation; anything else stays absent, with an
ingest warning. Every unmapped or lossy source field is a row of the gap register, classified
blocking or non-blocking; a blocking gap is resolved by a schema change, never by a guess. The
mapping adapters of Phase 1 are test support code, not part of the package.

## 15. What is not guaranteed

- **Native review.** The native names in `languages.json` are marked `needs_native_review`. The
  Indic language fixtures of the test suite are synthetic, written by a language model, and
  marked `needs_native_review`. No native-speaker review is recorded for 0.0.1. A fixture marked
  `needs_native_review` is never used as a WER or CER reference.
- **Python versions and Unicode data.** CI requires Python 3.11 to 3.14; the suite was run on
  3.11.16, 3.12.14, 3.13.15 and 3.14.7, whose Unicode databases are 14.0.0, 15.0.0, 15.1.0 and
  16.0.0. Behaviour that reads the running Python's Unicode database can differ between them for
  characters assigned in later Unicode versions: the profile-value lint (normalization, case
  folding, digit and category lookups), case-folding of URL parameter names, the white-space test
  of price notes (`str.isspace()`) and of label edges. The default-ignorable set of labels is
  pinned to Unicode 16.0.0 in the code and does not follow the runtime. **Python 3.15 is not
  verified**: its CI leg is non-blocking, and no 3.15 run is recorded **(unverified)**.
- **The CI workflow** has run on GitHub Actions for the jobs that existed before revision 30.
  The secret-scanner job (gitleaks, pinned by checksum) and the dependency-audit job (pip-audit
  on `uv export`) are new and are **unverified until they run on GitHub**. The multi-version
  results above come from local runs.
- **Interoperability with other languages.** No non-Python JSON Schema validator was run against
  the schema files; portability rests on the pattern syntax rules of section 2.5
  **(unverified)**. Digests use the `rfc8785` Python package; agreement with JCS
  implementations in other languages is not tested **(unverified)**. The sealed payload layout is
  checked against Python's `cryptography` only; compatibility with other AES-GCM
  implementations is stated in ADR 0015 and not tested **(unverified)**.
- **Credential detection** uses our own pattern list. Its match with each vendor's current key
  format, and the list of presigned-URL forms, were not re-checked against provider
  documentation **(unverified)**. Metadata flags are heuristics: a false negative is possible.
- **Correctness of redaction** is asserted by the tool that performed it, not checked (section 8).
- **Retention** is recorded, not enforced (section 8).
- **Pydantic behaviour** (hiding input in errors, schema generation) was verified with pydantic
  2.14.1, the pinned version; other versions are not supported.
- **Package name.** The availability of `tracer-evalschema` on PyPI is not verified.

## 16. Statements in this file that are not verified

- Python 3.15 behaviour (section 15).
- Results of the new secret-scanner and dependency-audit CI jobs (section 15).
- Validation by non-Python JSON Schema validators; JCS and AES-GCM interoperability with other
  languages (section 15).
- The match of the credential patterns with current vendor key formats and presigned-URL forms
  (section 15).

Everything else in this file restates an ADR decision that the test suite checks, or was checked
directly on 2026-10-24 (the `jsonschema` behaviour of sections 2.5 and 2.6, the Unicode database
versions of section 15).
