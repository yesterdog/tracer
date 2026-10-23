# Run bundles and export bundles

A run produces many records (artifacts, transcripts, turns, tool calls, spans, results, usage
and cost records, a verdict). Two bundle entities group them. Decisions:
[ADR 0009](../adr/0009-entity-versioning.md) Decisions 5 and 7,
[ADR 0016](../adr/0016-roles-caller-origin-privacy.md) Decision 7,
[ADR 0015](../adr/0015-sealed-payload.md) Decision 7,
[ADR 0008](../adr/0008-schema-versioning-and-compatibility.md) Decisions 3 and 6. Field
reference: [RunBundle](../schema/RunBundle.md), [ExportBundle](../schema/ExportBundle.md).

## `RunBundle` (prefix `rbnd`): the unit of one run

- Holds the `Run`, its `ConfigSnapshot` and every record of the run. Records of fact do not
  name their run (except `UsageRecord.run_id`); membership in the bundle is what makes them the
  run's records.
- **Purpose-neutral.** It is the producer's own record. It may hold human-subject data in the
  clear, a plain profile digest, test profiles and the caller identifier.
- Checked as a whole on load: every record of every kind is validated; references by ID must
  resolve (or be declared withheld); results are checked against their definitions; the verdict
  against the rubric and the results; cost records against their catalog and usage; one result
  per metric; one schema version for all records; a record listed twice or two contents under one
  ID are rejected.
- **Withheld records.** `RunBundle.withheld` declares records deliberately left out, by kind and
  ID, never by value. A declaration excuses only references to those records and a stored tier
  higher than the records present can prove; it never excuses anything else.

## `ExportBundle` (prefix `exp`): a transformation for someone else

- `purpose` is `internal`, `shared_export` or `benchmark`. It holds run bundles, datasets,
  dataset items, sealed payloads and redaction entries.
- The `purpose` label is **self-declared** by the producer: an export labelled `internal` is not
  held to the shared-export rules. A consumer that needs a purpose demands it with
  `expected_purpose` (see Loading); without it any purpose is accepted.
- An export is a **transformation** of internal data, not a copy: profile digests are dropped or
  keyed, caller identifiers are stripped, human-subject payloads are sealed or redacted, local
  `file:` and loopback URIs are refused outside `internal`, and unresolved `source_run_id` values
  are dropped with a recorded `ProvenanceRedaction`. The rules are in
  [privacy and consent](privacy-and-consent.md).
- `SealedPayload` appears only here.
- 0.0.1 validates exports; it does not build them. The producer of an export performs the
  transformation and validates the result.

## Loading

`loader.load_bundle(model, data)` loads either bundle with the strict JSON rules (no duplicate
keys, a depth limit) and refuses input larger than `DEFAULT_MAX_BUNDLE_BYTES` (256 MiB) before
parsing. A consumer that validates bundles with the JSON Schema files instead must apply its own
byte limit and check repeated records itself (`SCHEMA_POLICY.md` section 2.4).

`loader.load_bundle(ExportBundle, data, expected_purpose="shared_export")` (and
`bundle.check_export_bundle(bundle, expected_purpose=...)`) refuses an export whose own label
differs, with a `ValueError` tagged `[purpose]`; `None`, the default, accepts any purpose. A run
bundle has no purpose, so passing one with `RunBundle` is a `TypeError`. Tests:
`packages/evalschema/tests/test_export_purpose.py`.

## Tests

`test_run_and_export_bundle_are_registered_entities`,
`test_run_bundle_rejects_same_record_id_different_content`, `test_run_bundle_rejects_a_record_listed_twice`,
`test_run_bundle_has_one_result_per_metric`, `test_withheld_declaration_must_be_true_and_unique`,
`test_withheld_records_excuse_references_to_them`, `test_export_bundle_holds_no_caller_identifier`,
`test_sealed_payload_accepted_in_export_bundle_and_refused_elsewhere`, `test_bundle_loader_size_limit`,
`test_an_oversized_bundle_is_refused_before_it_is_parsed`, `test_huge_call_bundle_validates_in_calibrated_time`.
