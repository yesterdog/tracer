# tracer-evalschema

Pydantic v2 models and JSON Schema (draft 2020-12) for Tracer evaluation entities.
Status: 0.0.1, unreleased (Phase 1 in progress). The rules, limitations and non-guarantees are in
`SCHEMA_POLICY.md` and the release notes in `CHANGELOG.md`, both next to this file in the
repository and in the sdist (not in the wheel).

## JSON Schema files

The package ships one JSON Schema (draft 2020-12) per entity under `tracer_evalschema/schemas/`,
named `<Entity>.schema.json`, with `$id` `urn:tracer:evalschema:<version>:<Entity>`, and
`schema.lock`, which records the SHA-256 of every file for the schema version. The files are
generated from the models and never edited by hand:

    python -m tracer_evalschema.schema_export --write   # regenerate after a model change
    python -m tracer_evalschema.schema_export --check   # exit 1 on any drift (run in CI)

Rules that JSON Schema cannot state (cross-record checks, digests, arithmetic) are enforced by
the models only; validate with the models when you need every rule.

Uniqueness of the elements of an array of objects (the record lists of `RunBundle` and
`ExportBundle`, `PricingCatalog.entries`, `metadata_flags` and every other array of objects) is
also a model-only rule, on purpose: `uniqueItems` there would make a JSON Schema validator compare
every pair of objects, which is quadratic in a length the document chooses. Arrays of strings keep
`uniqueItems`. If you validate untrusted bundles with the schema alone, apply a byte limit first
and check repeated record IDs yourself. The same applies to `ConfigSnapshot.metric_definitions`
and `ConfigSnapshot.metric_bindings`: the model rejects a definition or binding embedded twice
(same `id` and `version`); the schema does not.

Neither the wheel nor the sdist carries tests. The sdist holds the package source (with the
schema files and `schema.lock`), this README, `SCHEMA_POLICY.md`, `CHANGELOG.md`, `LICENSE`,
`NOTICE` and `pyproject.toml` (hatchling also adds `PKG-INFO` and the repository's `.gitignore`);
building the wheel from it gives the same wheel. The tests need files that live only in the
repository (captured provider fixtures, test support code, scripts and docs), so run them from a
git checkout.
