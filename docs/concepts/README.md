# Concepts

Short explanations of the ideas behind `tracer-evalschema` 0.0.1 (unreleased). Each page links
to the architecture decision records that decide the details and names tests that check them.
The rules, limitations and non-guarantees are collected in `packages/evalschema/SCHEMA_POLICY.md`;
field-level details are in the generated [field reference](../schema/README.md).

| Page | About |
| --- | --- |
| [verdict.md](verdict.md) | "x/y required checks passed", status, weighted score with weight coverage, per-turn share with turn coverage |
| [tiers-and-missing-data.md](tiers-and-missing-data.md) | tiers T0, T1, T2; `not_applicable` results and `not_available` costs |
| [bundles.md](bundles.md) | the run bundle and the export bundle |
| [privacy-and-consent.md](privacy-and-consent.md) | roles, human-subject markers, consent, export rules, sealing |
| [cost.md](cost.md) | USD money, reported or computed costs, summaries, prices |
| [language.md](language.md) | configured and observed languages, native script only |
| [display-safety.md](display-safety.md) | exact text storage, bidi controls, `display_safe()` |
| [versioning.md](versioning.md) | schema version, compatibility, versioned entities |
| [mappings-and-gaps.md](mappings-and-gaps.md) | source mappings, no fabrication, the gap register |
| [storage.md](storage.md) | the relational store (`tracer-evaldb`): documents and projections, two backends, what the database does not do, deletion, migrations |

Scope, in one paragraph: only the AI agent is evaluated. The caller is a human (recordings of
real calls) or a simulator (test calls). Speaker roles are `caller`, `agent` and `unknown`;
there is no IVR and no hand-off to a human agent. All provider calls use the user's own keys,
which the schema never stores.
