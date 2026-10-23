# Language fixtures (task P1-17)

Synthetic test strings for the nine configured languages (en, hi, mr, te, ta, kn, ml, pa in Gurmukhi, gu), the
tests in `packages/evalschema/tests/test_language_fixtures.py` and the helper `_support_b10.py`.

## Review status

**Native-speaker review has NOT been done.** Every case carries `"needs_native_review": true`, and the test
`test_every_case_needs_native_review` fails if any case says otherwise. The strings were written by a language model
(the indic-linguistics agent). A string being accepted by a model, a regular expression or `unicodedata` says nothing
about whether it is correct, natural or polite in the language. Treat every string as unreviewed until a named native
reviewer has signed it off in this file.

## Sources of every string

There is no external text source. Nothing was copied from a real call, a transcript, a dataset, a web page, a provider
response or the private VoicEra fixture (which was never read). Specifically:

- Sentences (greetings, library opening hours, counter numbers, dates, an e-mail remark): written from scratch by the
  indic-linguistics agent. They hold no personal data: no real name, phone number, address or account number. The only
  numbers are made up (`12`, `1234`, `9876`, times and dates).
- Short words used for minimal pairs and normalization checks (February, certainly, give, time, today, evening, the
  verb stems for laugh and swan): chosen by the agent. The code points were checked against the Unicode character
  names by rule, not by a reviewer.
- Structural joiner strings (consonant + virama + ZWJ or ZWNJ + consonant): built to exercise the characters. They are
  **not** claims about orthography.
- Language names used as provider labels (`languages.json` native names, English names in lower case): taken from
  `packages/evalschema/src/tracer_evalschema/data/languages.json`, whose native names are themselves
  `needs_native_review`.
- Tags: written from RFC 5646 and ADR 0005.
- Facts about code points (composition exclusions, canonical decompositions, digit ranges): from the Unicode Standard
  (UAX #15, UnicodeData) as exposed by Python `unicodedata`, and checked by the tests at run time. The recorded
  expectations in the files were derived by hand from the Unicode data rules before any run; see "Unverified by
  execution" below.

## Files

| File | Content |
|---|---|
| `en.json`, `hi.json`, `mr.json`, `te.json`, `ta.json`, `kn.json`, `ml.json`, `pa.json`, `gu.json` | Per language: sentences, digit forms, joiners, normalization pairs, Latin-letter cases, configured tags and provider labels |
| `cross_cutting.json` | Invisible and bidi characters inside native-script text (ADR 0017) |
| `negatives/` | Tags that must be rejected or flagged, with its own README (separate directory because ADR 0005 Decision 10 allows the negatives to spell excluded tags there) |

Case counts: en 17, hi 23, mr 19, te 19, ta 20, kn 21, ml 23, pa 25, gu 17, cross_cutting 12, negatives 51 (247 in all).

## Case record

`id`, `language`, `script` (ISO 15924; `Deva+Latn` for a native sentence with Latin letters), `kind`, `category`,
`text`, `purpose`, `expected`, `needs_native_review`, plus optional fields:

- `kind`: `text` (free text stored as turn or scripted-turn text), `tag` (a configured or observed language tag), `label`
  (a raw provider language label).
- `expected.outcome`: `preserved` (stored code point for code point), `rejected` (a configured field rejects it, with
  the error type and the field path), `flagged` (stored, and a named flag applies).
- `tag`: the configured tag used next to a text (default `<language>-IN`).
- `label_candidate`: the text is also tried as `Persona.accent` (a `HumanLabel`).
- `pair_of` and `pair_relation` (`nfc_equal`, `nfd_equal`, `distinct`): a pair of spellings and how normalization
  relates them. In every case both spellings are stored as given.
- `normalization`: expected `nfc`, `nfd` or `nfkc` output; `"="` means "equal to `text`". Only claims made with
  certainty are recorded; the test computes the rest.
- `invisibles`: expected count of each invisible code point; `must_contain`: code points that must be present;
  `digits_forms`: `ascii`, `native` (the language's own block) or `other`; `number_signs`: Numeric (not Decimal)
  digit-like characters and their values.
- `bidi_flag`, `label_rejected`, `markers` (cross-cutting cases): expected `contains_bidi_controls`, whether a
  `HumanLabel` rejects the text, and the markers `display_safe` inserts.

Every string is stored in the JSON files as raw UTF-8 code points, invisible characters and precomposed or decomposed
letters included. I (the author) intended to write the fragile ones as JSON `\u` escapes; the tool that wrote the files
decoded the escapes, so the files hold the characters themselves. Nothing was re-normalized (spot-checked by searching
the files for the intended code point sequences), but a text editor that normalizes on save would change them without
a trace in the diff. The tests catch that: `test_normalization_claims_hold`, `test_required_code_points_are_present`,
`test_invisible_characters_are_counted_as_declared`, `test_text_without_a_normalization_claim_is_already_nfc` and
`test_pair_relation_holds_and_both_spellings_are_stored`. Only the line feed in `cc-010` is a JSON escape.
"Byte-for-byte" in this suite means code point for code point after JSON decoding (ADR 0017, Consequences); the model
tests also check that the UTF-8 bytes of the text appear in the serialized JSON.

## How the schema treats the cases (ADR 0005, 0011, 0017)

- **Storage is verbatim.** Turn, segment and scripted-turn text and `detected_language_raw` keep NFC and NFD forms,
  ZWJ, ZWNJ and bidi controls as given. Nothing is normalized or stripped.
- **Script.** Configured text is native script only. Observed Latin-letter text is stored; script-sensitive metrics
  decide by the observed *tag*: `<lang>-Latn` gives `unsupported_script`. Text itself is not scanned. A Hindi tag on
  Latin-letter text is therefore not flagged by the schema (see the ambiguity list in the task report).
- **Latin tokens inside a native sentence.** The native script is canonical. The Latin token is stored verbatim. For
  scoring (not implemented in this package) a Latin token matches only by exact equality; transliteration variants
  never count as matches (docs/languages.md proposal; not decided).
- **Tags.** `hi-IN`, `en-IN`, `pa-Guru-IN` are valid configured tags; letter case is canonicalized; raw provider labels
  are kept verbatim and the helper converts `_` to `-` with a warning; names such as `hindi` are not tags.
- **Labels (`HumanLabel`)** accept ZWJ and ZWNJ and reject LRM, RLM, ZWSP, word joiner, BOM, soft hyphen, variation
  selectors, line separators and control characters.

## Normalization facts

The test module checks, with `unicodedata` only (no network):

- NFC and NFD forms of each pair as recorded; joiners (U+200D, U+200C) survive NFC and NFKC;
- native decimal digits are in the language's block (`languages.json` `digit_range`), are `Nd`, and NFKC leaves them
  unchanged; the Tamil number sign is `No` with a numeric value and no decimal value;
- the precomposed nukta letters U+0958..U+095F and U+0A33, U+0A36, U+0A59..U+0A5B, U+0A5E rewrite under NFC and so
  appear only in cases that say so.

`test_normalization_facts_dump` computes the facts for every case (NFC/NFD/NFKC/NFKD changes, code-point counts,
`unicodedata.unidata_version`) and writes them to the file named by the environment variable
`TRACER_LANGUAGE_FACTS_OUT` when it is set. Python 3.11 to 3.14 ship different Unicode versions (14.0 to 16.0); the
characters used here are old and stable, so the recorded expectations must hold on all four.

## Unverified by execution

The author of these files had no shell and could not run Python. The recorded `normalization` expectations, the
tests and the case schema were written by hand from the Unicode rules and have **not** been executed. A failing
`normalization`, `pair_relation` or `codepoints` check is information about the fixture, not necessarily about the
schema: read the assertion message first.

## What a native reviewer must check

See the list in the task report. In short: every sentence (spelling, grammar, register, number words, danda versus
full stop, loanword spellings); which spellings are really one word (chandrabindu versus anusvara, tippi versus bindi,
addak, chillu atomic versus legacy, nukta forms, the vowel signs written as two code points); whether the joiner
strings are meaningful anywhere; which digit form a speaker or provider expects; whether the Latin tokens are
acceptable in native-script references; the native language names used as labels.
