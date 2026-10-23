<!--
Source and retrieval date (required for every doc in this repo):
- docs/phase1/SCOPE.md (authoritative language decisions), read 2026-10-22
- IANA Language Subtag Registry, File-Date 2026-09-17, retrieved 2026-10-22
  https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry
- Unicode Blocks-18.0.0.txt (2026-07-08) and DerivedNumericType-18.0.0.txt (2026-06-29), retrieved 2026-10-22
  https://www.unicode.org/Public/UCD/latest/ucd/Blocks.txt
  https://www.unicode.org/Public/UCD/latest/ucd/extracted/DerivedNumericType.txt
- ISO 4217 List One, SIX Group, published 2026-09-17, retrieved 2026-10-22
  https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
- docs/adr/0005-language.md (language tag decisions), read 2026-10-22
- scripts/verify_language_data.py and the `_meta.verification` notes in the three data files, read 2026-10-22
Status: task P1-03. Source-data verification by script on 2026-10-22 (see below). No native-speaker verification has been performed on anything in this file.
-->

# Languages and vendored reference data (P1-03)

Verification tags used in `languages.json`: `rule` (a project decision or derivation, no outside authority), `source` (taken from a cited registry or standard), `needs_native_review` (a native speaker must check it). Nothing here is tagged as native-verified.

## Verification status

On 2026-10-22 the vendored data files were compared by script with directly downloaded upstream files. This replaces the earlier state, in which the values had been transcribed from web-fetch (model-processed) output and were unverified. Each data file records this in its `_meta.verification` note.

| File | Compared with | Result |
|---|---|---|
| `iso4217_active.json` | SIX ISO 4217 List One XML, `Pblshd` 2026-09-17 | 178 codes match in numeric code, minor units and fund flag. Names match except KMF: the source has a trailing space, which the vendored file strips (the script accepts only that one code and only that difference). TOP was corrected to the source's typographic apostrophe (Pa’anga). |
| `iana_subtag_registry_subset.json` | IANA Language Subtag Registry, File-Date 2026-09-17 | All 20 records match on type, subtag, description, added and suppress-script. |
| `languages.json` | Unicode 18.0.0 `Blocks.txt` and `extracted/DerivedNumericType.txt` | Block ranges and Decimal digit runs match for all eight native-digit languages. English has no native digit block claimed. |

The repeatable tool is `scripts/verify_language_data.py`. It downloads the four upstream files into a fresh temporary directory (HTTPS only, 20 MB cap, no XML DTDs or entities), compares them with the three JSON files, and exits 0 on a full match, 1 on a mismatch, 2 on a download or parse failure. It needs network access and is not part of the default test run or CI; run it by hand before a release or when a data file changes. I did not re-run it for this edit; the results above are those reported for the 2026-10-22 run and read from the `_meta` notes and the script.

What the script does not cover:

- It compares `languages.json` only on the Unicode block and digit fields. The IANA-derived fields in `languages.json` (language code, default script, English name) are not compared by the script directly; they are consistent with the verified IANA subset by inspection of the vendored values, not by an automated cross-check.
- `allowed_scripts` is a project rule (SCOPE.md), not an upstream fact.
- Native names (`native_name`) are not checked against any source and remain `needs_native_review`.
- The data files still carry an older `retrieval_method` string describing the web-fetch transcription. The `verification` note in each `_meta` says it supersedes that string; the string itself has not been removed.

## Files

All under `packages/evalschema/src/tracer_evalschema/data/`.

| File | Content |
|---|---|
| `languages.json` | Exactly nine configured languages |
| `iana_subtag_registry_subset.json` | 9 language, 8 script and 3 region records plus the registry `file_date` |
| `iso4217_active.json` | 178 distinct codes from ISO 4217 List One |

Script-verified against upstream on 2026-10-22; see Verification status.

## Decisions

1. **Nine languages, set by SCOPE.md.** en, hi, mr, te, ta, kn, ml, pa (Gurmukhi), gu. Anything else is out of scope for configured languages (the exclusions are listed in SCOPE.md and are not repeated in the data files, so they cannot leak into them). Provider-observed tags are handled elsewhere (P1-04c).
2. **Default script** comes from the registry `Suppress-Script` field of each language record (source). en=Latn, hi=Deva, mr=Deva, te=Telu, ta=Taml, kn=Knda, ml=Mlym, pa=Guru, gu=Gujr. These agree with the values the task specified.
3. **Allowed scripts are the single native script** (rule, from SCOPE.md "native script only"). Romanized variants are not allowed for configured languages.
4. **Native digits.** Ranges are Numeric_Type=Decimal runs from `DerivedNumericType` (source). English has none (ASCII is canonical). Hindi and Marathi share Devanagari U+0966..U+096F. Tamil digits are U+0BE6..U+0BEF; U+0BF0..U+0BF2 are number signs (ten, hundred, thousand) and are Numeric, not Decimal, so they are excluded from the digit range. Whether providers emit native digits or ASCII digits is not verified for any language.
5. **Names.** English names come from the registry Description (Punjabi is listed as "Panjabi" then "Punjabi"; `english_name` uses "Punjabi" and keeps "Panjabi" as an alternate). Native names were entered from general knowledge and not checked against CLDR or a reviewer, so they are `needs_native_review` (the 2026-10-22 verification did not cover them).
6. **Registry subset is minimal.** It validates the nine languages, their scripts, and regions IN, US, GB. I judged no further region necessary for configured-language tags. A subset cannot validate provider-observed tags such as one for a language outside the nine; P1-04c says observed tags must be "any well-formed registered BCP-47 tag", which needs either the full registry (about 731,000 characters) or a policy of well-formedness only. ADR 0005 has since decided syntax-only validation for observed tags, so the full registry is not needed (see Language-tag decisions). The test name `test_scripts_in_vendored_iso15924` is satisfied by the registry script records (script subtags are ISO 15924 codes); no separate ISO 15924 file was vendored.
7. **ISO 4217.** The official SIX file is the source; no alternative source was used. Rows are de-duplicated by code. The file is kept complete (all List One rows, including funds such as BOV and CLF and X-codes such as XXX and XAU), with the `is_fund` and `is_x_code` flags as in the source. Per the user decision recorded in `docs/adr/0014-money-usage-cost-pricing.md` (Decision on `SupportedCurrency`), `Money` is USD only for 0.0.1. This file is used only to recognise and name a non-USD code in error messages, in the unsupported-currency path and in ingest warnings, and to tell a known ISO code from an unknown string. It never widens what `Money` accepts, and the two flags are informational only: no "no X-codes" rule or fund-acceptance decision depends on them, because funds and X-codes are rejected simply because they are not USD. The data that decides what `Money` accepts is `supported_currencies.json`, which contains exactly `["USD"]`. The P1-03 test that checks this file keeps every row is named `test_iso4217_data_keeps_all_list_one_rows`. Minor units are `null` where the source says N.A.

## Language-tag decisions (from docs/adr/0005-language.md)

These are recorded user decisions, summarized here; the ADR is authoritative.

- Configured languages are native script only. `SupportedLanguage` covers exactly the nine languages above, and a script subtag is accepted only if it is in the language's allowed native scripts (`pa-Guru-IN` is valid; `hi-Latn` and `pa-Arab` are rejected). Romanized configured text is not allowed.
- Configured regions stay IN, US and GB for 0.0.1 (user decision; this settles the region question that ADR 0005 still lists as proposed item P-14). Tags such as `ta-LK` or `en-AU` are therefore not configurable yet. Observed tags are not affected: they are syntax-checked only, so an observed `ta-LK` or `en-AU` is accepted as reported.
- Observed tags are syntax-only and never rejected. `ObservedLanguageTag` must be a well-formed BCP-47 tag and is never checked against a registry or rejected as unknown, unregistered or unsupported (`bn-IN`, `hi-Latn`, `und` are valid observations). This is why the full IANA registry is not vendored. The exact syntax profile (P-12) is still a proposed item awaiting user review.
- The raw provider label is kept in `detected_language_raw`. It holds the label exactly as the provider sent it, whether or not it parsed. Importers, not the type, convert `_` to `-` for the normalized `detected_language`, record a warning, and leave `detected_language` empty if the label is still not well-formed.
- Mixed-language turns are out of scope for 0.0.1. There is no field, type or tag alias for them, and a Hindi-plus-English style tag is rejected as a configured language. As an observed tag such a value is just a well-formed tag with a region subtag and must not be read as mixed-language. The section on Latin-script words below is a scoring proposal for stray Latin tokens only. (This paragraph avoids the ADR's exact drift-check tokens because this file is not on the ADR's drift-check allowlist.)

## Text normalization (judgment calls, not yet implemented here)

This task only supplies data. For the later WER/CER step the following are proposals, each needing review:

- Store text byte-for-byte (SCOPE.md). Normalize only inside the scoring function, never in storage.
- Prefer NFC over NFKC for scoring (judgment). Reason: NFC is the canonical form and leaves compatibility characters alone; NFKC folds more than is needed. Note from the Unicode Standard (UAX #15, not re-fetched in this task): some Devanagari and Gurmukhi precomposed nukta letters are composition exclusions, so NFC rewrites them to base plus nukta. Fixture authors must not assume a precomposed form survives NFC.
- Keep ZWJ and ZWNJ in storage (SCOPE.md). Whether they are stripped or preserved at scoring time is an open question below.
- Digits: choose one canonical form (ASCII or native) per language for scoring. Not decided.

## Latin-script words inside a native-script turn (no mixed-language support in 0.0.1)

Proposal (judgment call):
- The canonical script for a turn in a configured language is that language's native script (SCOPE.md).
- A configured-language reference (scripted turn) is native script only. Whether brand names or acronyms in Latin may appear inside a native-script reference is unresolved and flagged below.
- In observed (provider) text, Latin tokens are recorded verbatim and not rewritten.
- For WER, a Latin token is an ordinary token: it matches only by exact (case-folded) equality. No transliteration equivalence in 0.0.1. A turn whose observed text is entirely romanized makes script-sensitive metrics not_applicable (SCOPE.md).
- A per-turn flag for "contains tokens outside allowed scripts" would let reports show how much of a score is affected. Not specified in SCOPE.md; a suggestion only.

## Provider coverage and confidence

Not assessed. I did not consult provider documentation in this task, so I make no claim about which languages have weak STT or TTS coverage. This needs a separate sourced pass per provider (supported language lists, any published per-language WER) before confidence flags are set.

## Open questions for a native reviewer

Each item is a question; none is answered here.

1. Gurmukhi: should tippi (U+0A70) and addak (U+0A71) be treated as distinct from nasal/gemination spellings in WER, or normalized? Which spellings do Punjabi speakers consider equivalent?
2. Gurmukhi: are precomposed nukta letters (U+0A59..U+0A5B, U+0A5E) and their base-plus-nukta forms interchangeable for readers, and which form do STT providers emit?
3. Malayalam: the atomic chillu letters (U+0D7A..U+0D7F) versus the older consonant + virama + ZWJ sequences. Should these count as equal in scoring? Does NFC or NFKC unify them in the Unicode version used? (Needs a test against the actual normalization library.)
4. ZWJ/ZWNJ: in Hindi/Marathi half-forms, Kannada/Telugu conjuncts and Malayalam, which occurrences change meaning or legibility, and which are font-rendering hints that scoring may ignore?
5. Devanagari: nukta presence or absence (for example क़ versus क) in loanwords. Equal or different for WER?
6. Digits: in spoken-number transcripts, when are native digits expected versus ASCII digits, per language? Are number words preferred over digits in any language's formal speech?
7. Marathi and Hindi share a script; are there spellings (for example the Marathi letter ळ, or chandrabindu versus anusvara usage) that a Hindi-trained normalizer would wrongly merge?
8. Tamil: number signs U+0BF0..U+0BF2 versus spelled-out numbers. Relevant in transcripts?
9. Latin tokens in native-script references: which are acceptable (brand names, acronyms, units) and how should they be matched?
10. Native names in `languages.json` (all eight non-English): confirm spelling and the name speakers use for their own language.
11. Punjabi English name: confirm whether product UI should say "Punjabi" or "Panjabi".

## Items not verified

- Native-speaker verification of anything: native names, digit behaviour, normalization proposals and fixtures.
- Native names are `needs_native_review` and not checked against CLDR.
- The IANA-derived fields of `languages.json` are not cross-checked automatically (see Verification status).
- The remaining roughly 730,000 characters of the registry were not inspected; only the records listed were extracted.
- CLDR native names, provider digit behaviour, and provider language coverage.
