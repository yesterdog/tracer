# Text storage and display safety

Transcripts and labels come from providers, agents and people, and may contain invisible or
direction-changing characters, deliberately or not. The schema stores text exactly and makes it
safe only when it is shown. Decisions: [ADR 0017](../adr/0017-bidi-and-display-safe.md).

## Storage: exactly as received

- Free text (`VerbatimText`: turn and segment text, raw payloads, raw language labels) is stored
  code point for code point. No Unicode normalization, no trimming: NFC and NFD spellings are
  different values. ZWJ (U+200D) and ZWNJ (U+200C), which Indic scripts need, are kept in every
  text type.
- Lone surrogates cannot be written as UTF-8 and are rejected by the type. Importers never fail
  on them: they replace each with U+FFFD and record the warning `text_not_valid_unicode`. A
  warning path passed by the caller is a locator, not data: its lone surrogates are replaced
  the same way with no warning. A cited API contract's currency code is mapping configuration:
  a lone surrogate there is an error.
- `contains_bidi_controls` is computed from the text (U+202A to U+202E, U+2066 to U+2069) and
  verified when a document is loaded; a wrong stored flag is rejected.

## Identifiers and labels: strict

Names, labels and display names (`HumanLabel`) reject bidi controls, LRM, RLM, ALM, zero-width
space, word joiner, BOM, control characters and every other default-ignorable character except
ZWJ and ZWNJ, so two labels cannot look identical while differing. Keys and provider or model
identifiers (`MachineKey`) are ASCII.

## Display: `display_safe()`

`text.display_safe(text)` is the one rendering function for untrusted text. It wraps the text in
FSI ... PDI so it cannot reorder the surrounding content, and replaces each embedding, override
and isolate control inside it with a visible marker (`[RLO]`, `[PDI]`, ...), so a PDI inside the
text cannot close the outer isolate. LRM, RLM and ALM are left alone. The marker table ships as
`data/bidi_markers.json`.

The bidi behaviour is checked against an in-repository, test-only, clean-room implementation of
the Unicode Bidirectional Algorithm (UAX #9, Unicode 16.0.0), which passes every line of the
Unicode conformance files `BidiTest.txt` and `BidiCharacterTest.txt`.

## Tests

`test_marker_table_json_matches_function`, `test_metadata_flags_verified_on_load`.
