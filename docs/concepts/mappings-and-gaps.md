# Source mappings and the gap register

The schema must hold data from real sources: OpenTelemetry spans of Pipecat agents, Langfuse
traces, speech-to-text responses, agent conversation exports, test scenario files, recordings.
Phase 1 proves that each source can be expressed without loss and without guessing. Decisions:
[ADR 0018](../adr/0018-real-sample-mapping-policy.md),
[ADR 0003](../adr/0003-enum-extensibility.md) Decision 3.

## The no-fabrication rule

A mapping fills a target field only from a source field or from a documented derivation that
uses source fields only. Anything else stays absent (or uses the explicit `unknown` member of a
closed set) and the mapping records an ingest warning. Forbidden: 0 or false for an unknown
value, made-up timestamps or confidences, an assumed timezone, an assumed transcript stage, an
assumed meaning of a timestamp. Each mapping declares a field-provenance table (target path to
source path, derivation or `absent`), and a test checks every non-null value against it.

## Fixtures

Eight sample families (F1 to F8), each with a provenance file stating source, retrieval date,
software version, method (`captured`, `synthetic_from_docs` or `private_real`), licence and
personal-data status. One real call export is private: it is never committed, never quoted, and
its tests are deselected in CI; a synthetic twin with the same structure is public.

## The gap register

Every source field that a mapping cannot represent, or represents with loss, is a row of the gap
register (fixture, classification, reason, owner). A gap is **blocking** when the information is
needed by a metric, the verdict or the cost model and could only be stored with loss or a guessed
value. The gate requires zero blocking gaps; a blocking gap is closed by a schema change, never by
a guess. `KNOWN_SCHEMA_GAPS` holds only non-blocking mapping gaps.

This register is not the register of code-only rules (`CODE_ONLY_RULES`), which lists rules the
JSON Schema files cannot state (`SCHEMA_POLICY.md` section 2).

The mapping adapters of Phase 1 are test support code under `packages/evalschema/tests/support/`;
they are not part of the package.

## Tests

`test_no_fabricated_values_all_mappings`,
`test_f1_repeated_unit_quantities_merged_only_for_one_billable_event`,
`test_register_is_separate_from_the_mapping_known_schema_gaps`.
