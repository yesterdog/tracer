# Negative language fixtures (task P1-17)

`negatives.json` lists tags and labels that a configured language must reject or that must be flagged. This directory
is the "negative-fixture directory for languages" that ADR 0005 Decision 10 allows to spell excluded languages and
the mixed-language alias, because the negative tests could not exist without them. It holds nothing else.

**Native-speaker review has NOT been done.** Every case has `"needs_native_review": true`. The tags are strings taken
from RFC 5646 and ADR 0005 and need no language review; the single word of Arabic-script Punjabi (`neg-201`) does.
No text was copied from a real call or dataset; the private VoicEra fixture was never read.

## Cases

| Group | Ids | Configured | Observed | Metric flag (script-sensitive, covers all nine) |
|---|---|---|---|---|
| Excluded languages, bare and with a region | neg-001..008 | rejected, `supported_language`, "not a configured language" | kept as given (canonical case) | `unsupported_language` |
| Arabic-script Punjabi | neg-009, 010 | rejected, "not a native script" | kept | `unsupported_script` |
| The mixed-language alias for the eight non-English languages, also upper case | neg-011..019 | rejected with `CODEMIX_UNSUPPORTED_MESSAGE` | well-formed, region `EN`, canonical `xx-EN`; not read as mixed-language | scored (null) |
| A look-alike that is not the alias (English with region `EN`) | neg-020 | rejected, ordinary region message | kept | scored |
| Romanized tags (script `Latn`), and other foreign scripts | neg-021..031 | rejected, "not a native script" | kept | `unsupported_script` |
| Regions outside IN, US, GB | neg-032..034 | rejected, region message | kept | scored |
| Special tags | neg-035..037 | rejected, "not a configured language" | kept | `language_not_observed` (undetermined), `unsupported_language` |
| Private-use, extension and variant subtags | neg-038..040 | rejected, "variants, extensions and private-use" | kept | scored |
| Private-use-only tag | neg-041 | rejected, `language_tag` | kept | `unsupported_language` |
| Not tags: a language name, underscore form, empty, padded, empty subtag | neg-042..046 | rejected, `language_tag` | rejected by the type | not reached |
| Provider labels | neg-101..104 | n/a | read by `observe_language_label`; the raw label is kept | n/a |
| Arabic-script text | neg-201 | n/a | stored verbatim | `unsupported_script` under an Arabic-script tag |

Tags are quoted here only where the test or ADR names them; the exact strings are in `negatives.json`.

## Why this directory

The T-DRIFT check (ADR 0005 Decision 10) fails any file that spells an excluded language or the alias, except the files
it lists. Its list names "the negative-fixture directory for languages" without a path. This directory is that
directory; the T-DRIFT allowlist, when the test is written, needs the entry
`packages/evalschema/tests/fixtures/languages/negatives/` (the check against fixture text is done here by
`test_fixtures_outside_negatives_hold_no_excluded_tokens`). No other fixture file, README, test module or helper of
P1-17 contains an excluded token: the test code reads these tags from the JSON file.
