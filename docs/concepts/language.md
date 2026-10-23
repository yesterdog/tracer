# Language handling

The framework is built for Indian languages and English. It separates what a user **configures**
(where quality is promised) from what a provider **observes** (which is evidence and is never
rejected). Decisions: [ADR 0005](../adr/0005-language.md),
[ADR 0004](../adr/0004-requires-and-not-applicable.md) Decisions 6 and 7. Language data notes:
[languages.md](../languages.md).

## Configured languages

`SupportedLanguage` is used by Persona, ScriptedTurn, Evaluator, DatasetItem and AgentTarget.
Exactly nine languages, as BCP-47 tags:

| Language | Tag | Native script |
| --- | --- | --- |
| English | `en` | Latin |
| Hindi | `hi` | Devanagari |
| Marathi | `mr` | Devanagari |
| Telugu | `te` | Telugu |
| Tamil | `ta` | Tamil |
| Kannada | `kn` | Kannada |
| Malayalam | `ml` | Malayalam |
| Punjabi | `pa` | Gurmukhi |
| Gujarati | `gu` | Gujarati |

- A region is kept (`hi-IN` stays `hi-IN`). Configured regions are exactly `IN`, `US` and `GB`;
  others are rejected with a message. Case is canonicalized; nothing else is rewritten.
- **Native script only.** A script subtag is accepted only if it is a native script of the
  language (`pa-Guru-IN`). Romanized or transliterated configuration (`hi-Latn`) is rejected.
- **One language per value.** 0.0.1 has no way to configure two languages in one value; such a
  tag is rejected with a message that says it is unsupported.
- Other languages, and Punjabi in Arabic script, are not configurable in 0.0.1. ADR 0005
  Decision 8 names them; adding one needs data files, fixtures and native review. (This page
  does not spell their names or tags, so it passes the T-DRIFT check without an allowlist entry.)

## Observed languages

`ObservedLanguageTag` (`Turn.language`, `Transcript.detected_language`) is checked for BCP-47
syntax only and never against a registry: any well-formed tag is accepted, including languages
outside the nine and romanized tags. The provider's label is also kept verbatim
(`Turn.language_raw`, `Transcript.detected_language_raw`). Importers turn `_` into `-` with a
warning; a label that is not a tag (`hindi`) stays raw with the tag empty. Nothing is rejected at
ingest.

## How metrics use languages

- A configured language is **never** used as an observation. Untagged input makes a
  language-dependent metric `not_applicable` with `language_not_observed`.
- A metric is either `"language_independent"` (latency, turn counts) or lists the languages it
  supports; an observed language outside the list gives `unsupported_language`.
- A user may declare the language of an untagged import (`Transcript.declared_language`). It is a
  declaration, never an observation; results that used it record `language_source=declared`, and
  script-sensitive metrics never use it.
- Script-sensitive metrics (for example WER) are `not_applicable` with `unsupported_script` on
  observed text tagged with a non-native script. In 0.0.1 the script is known only from the tag.
- Text that mixes scripts inside an observed turn is valid and stored exactly as received.

## Review status

The native names in `languages.json` and the Indic test fixtures are marked
`needs_native_review`; the fixtures were written by a language model. No native-speaker review
is recorded for 0.0.1.

## Tests

`test_supported_region_outside_vendored_set_rejected`, `test_observed_accepts_regions_outside_vendored_set`,
`test_observed_rejects_grandfathered`, `test_importer_language_name_raw_kept_tag_empty`,
`test_turn_language_raw_verbatim`, `test_languages_native_names_need_native_review`.
