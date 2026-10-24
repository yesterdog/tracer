# Test fixtures of tracer-evalschema

This directory holds fixture data. Three trees are generated or captured elsewhere and are described
in their own files (`real/` by `provenance.json` files, `unicode/` by `provenance.json`, `private/`
is git-ignored and never committed). This file describes the two trees of task P1-16:

```
golden/<ClassName>/valid/<name>.json                 a valid document
golden/<ClassName>/invalid/<name>.json               a document the model must reject
golden/<ClassName>/invalid/<name>.expected.json      where and how it is rejected
golden/needs_native_review.json                      generated: files with Indic text (see below)
frozen/0.0.1/<ClassName>.minimal.json                byte copy of golden/<ClassName>/valid/minimal.json
frozen/0.0.1/<ClassName>.maximal.json                byte copy of golden/<ClassName>/valid/maximal.json
frozen/0.0.1/digests.json                            SHA-256 of every frozen file and entity digest
frozen/0.0.1/schema.lock                             added by the release of 0.0.1 (P1-15); absent before
```

There is one `<ClassName>` for each of the 26 registered entities (`tracer_evalschema.registry`),
the two bundles included.

## Generated, never edited by hand

Everything below `golden/` and `frozen/` except this file and `schema.lock` is written by

```
uv run python scripts/build_golden_fixtures.py --write
```

from the models and the test helpers (`tests/_support_b9*.py`). Every random source is replaced by a
seeded one, so two runs give the same bytes. `test_golden_fixtures.py` fails when a committed file
differs from the generator's output, so a change of a model shows up as a diff of these files. To
see the drift without writing: `uv run python scripts/build_golden_fixtures.py --check`.

## Valid documents

* `minimal.json` holds the required members only. The file is the model's own dump, so every
  optional member is an explicit `null` and every defaulted list is present. Loading the file and
  dumping it gives the same bytes (`test_golden_valid_fixtures_round_trip_byte_for_byte`), and
  loading the file without its `null` members gives the same dump
  (`test_golden_minimal_fixture_loads_without_its_null_members`).
* `maximal.json` fills every optional member that one document can hold.
* `maximal-<what>.json` carries what `maximal` cannot: another endpoint kind, another unit, another
  status, another consent state. Together the valid files of an entity cover every property, every
  union alternative and every enum member (`scripts/property_coverage.py`, gate item 4).

Property coverage stops at an embedded entity (a snapshot's evaluator, a bundle's turns): that
entity is covered by its own fixtures. `--deep` also descends into embedded entities and is
informational.

A few points no valid document can reach are excluded by name, each with its reason, in
`scripts/property_coverage.py` (`EXCLUDED`): the `metadata_flags` of a credential-shaped metadata
value (no fixture may hold one), a keyed digest as an entity's own `content_digest` (an entity
verifies its digest on load, and a keyed digest cannot be verified without a key), and a plain
`plaintext_digest` of a sealed payload (rejected by P-42).
`test_property_coverage_exclusions_are_the_unreachable_points_and_nothing_else` proves that each
exclusion is real.

## Invalid documents

Each invalid file is a valid document with one deliberate defect. `<name>.expected.json` holds:

| member | meaning |
| --- | --- |
| `error_path` | the pydantic `loc` of the first error (empty for a rule of the whole model or a typed error) |
| `error_kind` | the pydantic error type, or the class name of a typed error that pydantic does not wrap (`DigestMismatch`, `SchemaVersionTooNew`, ...) |
| `error_tag` | the bracketed tag a bundle rule puts at the start of its message (`tier`, `duplicate`, ...), or `null` |
| `schema` | `rejects` when the exported JSON Schema (`tracer_evalschema.schema_export`; the schema pydantic generates while that module is absent) also rejects the document, `code_only` when only the model's code can (cross-record, arithmetic, cryptographic and similar rules) |
| `why` | the rule, in one sentence |

## Synthetic data only

Nothing in these files comes from a real call, person, account or provider. Names, dates of birth,
account numbers, URLs (`example.com`, `example.net`), digests and key identifiers are invented. No
fixture holds a credential-shaped value; `test_no_secrets_or_personal_data_in_fixtures` scans every
string.

Invisible characters (bidi controls, zero-width characters, lone surrogates) are written as JSON
escapes (`⁦`), so a reviewer can see them in a diff.

## needs_native_review

The Indic text in these fixtures (Devanagari for Hindi and Marathi, Malayalam) is synthetic and has
**not** been checked by a native speaker: status `needs_native_review`, like the native names in the
language data. It exists to exercise storage, digests and byte-for-byte round trips, not to be
correct usage. `golden/needs_native_review.json` lists the files that hold such text; the list is
generated and a test compares it with the files. The language fixtures with native-reviewed cases
for the nine languages are a separate task (P1-17, `fixtures/languages/`).

## Frozen fixtures

`frozen/0.0.1/` records what the documents of schema version 0.0.1 look like. `digests.json` holds,
for each frozen file, the SHA-256 of its bytes and the plain `sha256-jcs/v1` digest of the entity
(the digest domain of ADR 0006 Decision 5). `test_frozen_fixture_dirs_immutable` fails when a file
or a recorded hash changes, `test_frozen_fixtures_still_parse` when a document no longer loads. Once
0.0.1 is released, `schema.lock` is added here (ADR 0008 Decision 7) and the directory is not
changed again; a schema change then bumps the version and adds the next directory.

## Regeneration while 0.0.1 is unreleased

`frozen/0.0.1/` is immutable once 0.0.1 is released (a tag exists and `schema.lock` is added). Until
then the schema itself is unreleased and the directory is a generated copy like `golden/`, so it may
change, and only because of that. Every such regeneration is made with
`uv run python scripts/build_golden_fixtures.py --write` and reviewed as a diff.

Regeneration log:

* 2026-10-24 (batch 19): `Evaluator.expected_tool_calls_exhaustive` (`true`, `false` or `null`) was
  added to the schema (ADR 0010 Decision 8). Every Evaluator document gains the member, so the
  Evaluator digests change, and so does every document that embeds an Evaluator (ConfigSnapshot,
  RunBundle, ExportBundle) and the file hashes and entity digests in `frozen/0.0.1/digests.json`.
  `Evaluator/valid/minimal.json` holds `null`, `maximal.json` and `maximal-keyed-profile.json` hold
  `true`, the new `maximal-not-exhaustive.json` holds `false`, and the new invalid file
  `Evaluator/invalid/exhaustive-flag-is-a-string.json` holds the string `"true"` (rejected: the member
  is a strict boolean). After the release of 0.0.1 a change like this needs a new schema version and
  a new frozen directory instead.
* 2026-10-24 (batch 27, SCR-1..SCR-8): `MetricResult.transcript_ids` and the `[applicability]` bundle
  rule landed (ADR 0004 Decision 9). Every MetricResult document gains `transcript_ids` (`null`
  unless set), so the MetricResult, RunBundle and ExportBundle files, their entity digests and the
  file hashes in `frozen/0.0.1/digests.json` change. The `maximal` MetricResult holds one transcript
  id, the RunBundle worlds declare `hi-IN` (turn_timing available, transcript tagged `hi-IN`), and
  the new invalid files `MetricResult/invalid/transcript-ids-twice.json` and
  `transcript-ids-empty.json` are rejected (duplicate id; empty list).
* 2026-10-24 (batch 28, A-C6): a `not_applicable` result with a run-level reason (`missing_input`,
  `agent_channel_unknown`) may not carry a `per_turn` summary. `MetricResult/valid/maximal-na-missing-input.json`
  loses its summary and `maximal-na-language-not-observed.json` gains it (4 not-applicable turns, one
  turn evidence reference), so a golden still shows a not_applicable result with a summary. The
  invalid files derived from them change with them (`missing-input-without-kinds`,
  `not-applicable-with-value`, `not-applicable-without-reason`; same expectations, including error
  path `missing_kinds`). No `frozen/` file and no digest changed.
