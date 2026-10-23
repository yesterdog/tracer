# Adversarial fixtures (P1-18)

This tree holds the adversarial documents that no earlier test covered, and the registry that lists
every adversarial case of the project.

```
registry.json                             one entry per case: A-01..A-44 (SCOPE section 5) and the
                                          extra cases X-01..X-14 named in this task
<case>/<name>.json                        a document, or the text of one (see below)
<case>/<name>.expected.json               what must happen to it, and the threat in one sentence
```

## The registry

`registry.json` has the member `cases`, a list ordered by id. Each entry has exactly these members:

| member | meaning |
| --- | --- |
| `id` | `A-01` to `A-44` (the numbered cases of SCOPE section 5, A-33 to A-44 being the sealing cases) or `X-01` to `X-14` (cases added by P-18; the architect numbers them A-45 and up when SCOPE is next revised) |
| `title` | one line |
| `source` | where the case is defined: `SCOPE`, `ADR <number>` or `test` |
| `expectation` | `rejected`, `preserved` (kept byte for byte or in its canonical spelling) or `flagged` (kept and marked) |
| `where` | the model, field or bundle that must behave this way |
| `covering_tests` | node ids (`path::function`, a function id covers its parametrizations) of tests that run the case; `test_adversarial_registry.py` collects them, runs them and fails on a missing, skipped or failing one |
| `fixture` | the directory of this tree that holds the case's documents, or `null` |
| `security_relevant` | whether a threat is recorded |
| `threat` | the threat in one sentence (`null` when `security_relevant` is false) |

## The fixtures

Every fixture is a golden valid document (`tests/fixtures/golden/<Entity>/valid/<base>.json`) with
one deliberate defect, or with one edit of its JSON text (a literal that is not JSON, a duplicate
key). They are generated, never edited by hand, from `SPECS` in `tests/_support_b12.py`;
`test_fixtures_equal_the_generator_output` fails when a committed file differs from the generator's
output (a change of a golden document or of a model shows up as a diff here too). To write them:

```
uv run python -c "from packages.evalschema.tests import _support_b12 as b; b.write_fixtures()"
```

`<name>.expected.json` holds:

| member | meaning |
| --- | --- |
| `case`, `entity`, `base` | the case id, the model the document is loaded as, the golden document it was made from |
| `expectation` | `rejected`, `preserved` or `flagged` |
| `error_path`, `error_kind` | for `rejected`: the pydantic `loc` and error type of the first error, or the class name of a typed error (`DuplicateKeyError`, `DigestMismatch`, `ValueError` for a constant such as `NaN`); `null` otherwise |
| `schema` | `rejects` or `code_only` for a rejected document (the exported JSON Schema also rejects it, or only the model's code can), `accepts` for a kept one, `not_applicable` when the document is not JSON text a schema validator can read |
| `canonical` | for a kept document: members whose dump differs from the file because the model writes them in one canonical spelling (for example an instant in UTC with six digits) |
| `no_echo` | strings that must not appear in any rendering of the error |
| `why`, `threat` | the rule and the threat, one sentence each |

## Credentials are never stored

No fixture holds a credential-shaped value. Where a case needs one, the file holds a placeholder
such as `@@AWS_KEY@@` and `tests/_support_b12.py` assembles the value at run time (`expand()`).
`test_adversarial_fixtures_hold_no_credential_shaped_value` scans every string of every fixture.

## Synthetic data only

Nothing here comes from a real call, person, account or provider. Invisible characters (bidi
controls, zero-width characters, lone surrogates) are written as JSON escapes so a reviewer can see
them in a diff. The two Indic strings (Devanagari with a zero-width joiner and non-joiner) are
synthetic and have not been checked by a native speaker (status `needs_native_review`); they exercise
storage only.
