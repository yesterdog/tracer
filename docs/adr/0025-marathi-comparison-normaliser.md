# ADR 0025: Comparison normaliser `mr-Deva/1` (Marathi, Devanagari)

- Status: Accepted (Phase 3 SCOPE question Q11, task P3-00c; auto-approved on the recommended option under the owner's standing rule of 2026-10-23, decision log "PHASE 3 SCOPE DELIVERED"). **Every rule in this ADR is `needs_native_review` (BL-169): no native speaker has reviewed any of it, and nothing here is an Indic quality claim.** The eyelash-ra sequence stays unconfirmed (BL-132). Open [Proposed] items: none.
- Date: 2026-10-25
- Task: P3-00c (fixtures by P3-12 in `packages/evalcore/tests/fixtures/normaliser_mr.json`; implemented by P3-13 and P3-14)

## Context
Phase 3 compares two Marathi transcripts of one call: a speech-to-text output and an agent export. It aligns them word by word and flags the export spans that disagree (`docs/phase3/SCOPE.md` section A). Two spellings that a reader sees as the same word, or that differ only in code points a reader cannot tell apart, must not be flagged. The two texts also come from different systems, so they differ in digit forms, joiners, punctuation and Unicode composition.

The project rules that bind this ADR:
- Stored text is never normalised (ADR 0017 Decision 1 and Rejected alternatives: "Normalization belongs in WER/CER pipelines (indic-linguistics), where it is explicit and versioned"). NFC and NFD spellings are different stored values; ZWJ and ZWNJ are kept in every text type.
- Native script only for configured text; romanised Marathi has no standard spelling (ADR 0005 Decision 7 and Rejected alternatives). A turn with a few Latin-script words inside a Marathi sentence is valid and stored verbatim (ADR 0005 Decision 7).
- Lone surrogates cannot be stored; importers replace them with U+FFFD and warn (ADR 0017 Decision 1).
- Display of untrusted text goes through `display_safe()` (ADR 0017 Decision 3; `docs/concepts/display-safety.md`).
- Results from a normaliser carry no Indic quality claim before native review (BL-132, BL-169; ADR 0005 Decision 9).

Facts about the Unicode data that the rules rest on (checked against Unicode 18.0.0 sources, see "Sources"):
- U+0958 to U+095F (QA, KHHA, GHHA, ZA, DDDHA, RHA, FA, YYA) are in `CompositionExclusions.txt`. Each has a canonical decomposition to a base letter plus U+093C (nukta). NFC and NFD both decompose them, so they never survive NFC.
- U+0929, U+0931 and U+0934 (NNNA, RRA, LLLA) are not excluded: NFC composes NA, RA and LLA followed by U+093C into them.
- The nukta has canonical combining class 7 and the virama (U+094D) class 9, so NFC puts a nukta before a virama that came first.
- NFKC does not fold Devanagari digits U+0966 to U+096F to ASCII (they have no compatibility decomposition), so the digit fold is a rule of ours, not a side effect of NFKC.
- UAX #29 keeps letters and digits together across scripts (rules WB5, WB9, WB10), ignores Format and Extend characters inside a word (rule WB4), and since Unicode 15.1 does not break inside a consonant-virama-consonant conjunct (rule GB9c). U+200B is not in the Format class of the word-break rules.

## Decision

### 1. What the normaliser is
`mr-Deva/1` is a **comparison normaliser**. It produces a comparison key per token. It never produces replacement text. The display text, the stored transcript and every offset refer to the original text exactly as stored. Tokenisation runs over the original text; each token is normalised on its own, so offsets stay valid (a key may be shorter or longer than its span).

The id has the form `<language>-<script>/<integer>` (here `mr-Deva/1`). The language and script parts are the ADR 0005 primary language and ISO 15924 script; the integer is the rule version. The id is recorded in every result that used the normaliser (`test_normaliser_id_is_versioned_and_recorded`).

### 2. Order of the steps for one token
For each token (Decision 7) the key is built by these steps in this order:
1. Drop the ignorable characters (Decision 5 and 6) from the token text.
2. Fold the digits U+0966 to U+096F to U+0030 to U+0039 (Decision 4).
3. Apply full Unicode case folding to every character (Decision 8).
4. Apply NFC (Decision 3).

The order is part of the rule: dropping a joiner before NFC lets a nukta that followed a joiner compose with its base (`nfc-joiner-between-base-and-nukta`). Case folding runs before the final NFC so that a folded result (for example `i` followed by U+0307) is in NFC.

### 3. NFC, not NFKC (rules R01, R02)
The key is in NFC (UAX #15). It is not NFKC.
- Consequence for the nukta letters: U+0958 to U+095F never appear in a key; the key holds base + U+093C. A token spelled with U+095B and a token spelled with U+091C U+093C get the same key. NA, RA and LLA followed by U+093C get the keys U+0929, U+0931 and U+0934.
- NFKC would also fold fullwidth forms, circled digits, superscripts and ligatures into ASCII. Those are different characters to a Marathi reader and are not produced by a Marathi speech-to-text or agent export, so folding them would only hide odd input.
- Rejected: **NFKC.** Trade-off: it would merge a few compatibility characters we have no evidence for, and it still would not fold Devanagari digits, so it buys nothing for the main case. Rejected: **NFD.** Trade-off: no benefit for comparison; longer keys; the confidence of the later task is a character edit distance (Q7), and NFD would count one letter as two characters. Rejected: **no normalisation.** Trade-off: the same word in two canonically equivalent spellings (U+095B against U+091C U+093C) would be flagged as a difference no reader can see.
- Offsets are on the original text, which is not changed to NFC.

### 4. Digits (rules R03, R14)
Devanagari digits U+0966 to U+096F are folded to ASCII `0` to `9` **in the key only**. The display and the span keep the original code points.
- Other decimal digits (category Nd outside U+0966 to U+096F, for example fullwidth digits or Gujarati digits) are token characters and are **not** folded.
- Number words are not folded against digits: a number written as a word in one text and as digits in the other gets two different keys. This is a known source of flags (documented here, backlog row BL-192, and the numeric check of BL-175).
- Digit-grouping and decimal separators (`,` `.`) and the colon in a time are punctuation and separate tokens: `1,00,000` is three tokens and does not align with `100000`. This is also a known source of flags.
- Rejected: **fold all Nd characters.** Trade-off: more matches, but we have no evidence that another digit block occurs in a Marathi turn, and a fold we cannot review is a rule we cannot defend. Rejected: **fold number words (a word table).** Trade-off: fewer false flags, but the table needs native review (BL-169) and would be a Marathi data file with no reviewer; deferred behind a new normaliser id. Rejected: **keep number-internal separators in the token (UAX #29 rules WB11 and WB12).** Trade-off: `3.5` stays one token, but `1,00,000` and `100000` would still differ in key and token count, and folding the separators is a number normalisation that needs the table above.

### 5. Joiners (rule R04)
ZWJ (U+200D) and ZWNJ (U+200C) are **dropped from the key** and **kept in the span and in the display** (decision Q11). They do not split a token. A joiner that follows a token character is part of the token (its grapheme cluster, UAX #29 rule GB9); a joiner after a separator, or alone, makes no token (Decision 7).
- Judgement call, stated honestly: in Devanagari a joiner after a virama can change the rendered shape (a half form or an explicit virama; Unicode core specification, chapter 12 and the layout controls in chapter 23). The reason for dropping it is that two systems emit joiners inconsistently, and the Phase 3 question is whether two *words* differ, not two glyph shapes. Whether a Marathi reader treats the eyelash-ra form (RA + virama + ZWJ + YA) as the same word as the plain RA + virama + YA is **unconfirmed** (BL-132). The fixtures `ref-mr-006` and `joiner-eyelash-ra-unconfirmed` record the current behaviour only.
- Rejected: **keep the joiners in the key** (flags a difference that a reader of the plain text may not see; the SCOPE Q11 rejection). Trade-off: a genuine spelling distinction that carries meaning would be lost. Rejected: **drop the joiners from the display too.** Trade-off: it would break ADR 0017 (no stripping of stored text) and hide what the export really holds.

### 6. Bidi controls and other invisible characters (rules R05, R06, R08)
- **Bidi controls** U+061C, U+200E, U+200F, U+202A to U+202E and U+2066 to U+2069 are *ignorable*: dropped from the key, they do not split a token. They stay in the stored text and in the display; the reader sees them only as `display_safe()` markers where ADR 0017 requires them. `contains_bidi_controls` (ADR 0017 Decision 2) is a separate signal: this normaliser neither raises it nor hides it. An override inside a number (`1234` U+202E `9876`) therefore gives the key `12349876` and one token, and the attack is still visible through the flag and the markers.
- **Other invisible characters** U+00AD (soft hyphen), U+2060 (word joiner) and U+FEFF (byte order mark) are ignorable in the same way.
- **U+200B (zero-width space)** is a *separator*: it is an invisible word break (the fixture `cc-005` of Phase 1 uses it between two words), and UAX #29 does not class it as a Format character.
- Ignorable characters inside a token are inside its span. An ignorable character before the first letter or digit of a run, or after the last letter, mark, digit or joiner, is outside the span (Decision 7).
- Rejected: **treat bidi controls as separators.** Trade-off: an override inside a word would split it and produce flags for what is a display attack, not a speech difference. Rejected: **refuse text that contains bidi controls.** Trade-off: real exports can contain them (ADR 0017 Rejected alternatives: "Reject bidi controls in free text"), and the evaluation must not fail on an adversarial line (P3-24). Rejected: **drop every Default_Ignorable_Code_Point.** Trade-off: the set contains variation selectors and tag characters, which this ADR has no evidence about; they stay what their category says (marks are token characters, Cf are separators). Not folded further: this is a judgement call that a reviewer may widen in `mr-Deva/2`.

### 7. Tokens (rules R07, R08, R09, R15, R16)
Characters fall into three classes, tested in this order:
1. **Ignorable (I):** U+00AD, U+061C, U+200C, U+200D, U+200E, U+200F, U+202A to U+202E, U+2060, U+2066 to U+2069, U+FEFF.
2. **Token characters (T):** general category Lu, Ll, Lt, Lm, Lo (letters), Mn, Mc, Me (marks) or Nd (decimal digits).
3. **Separators (S):** everything else: all punctuation (categories Pc, Pd, Ps, Pe, Pi, Pf, Po), including the danda U+0964, the double danda U+0965 and the Devanagari abbreviation sign U+0970; white space and separators (Zs, Zl, Zp, and Cc such as tab, line feed, U+0000 and U+0085); symbols (Sm, Sc, Sk, So); numbers that are not decimal digits (Nl, No); format characters not listed above (Cf, including U+200B); private use (Co); unassigned (Cn).

A **token** is found in each maximal run of T and I characters:
- it starts at the first letter (L*) or decimal digit (Nd) of the run;
- it ends after the last T character or joiner of the run;
- a run with no letter and no digit has no token (an orphan vowel sign or nukta after a separator, or ignorables alone, are skipped);
- characters of any script in one run are one token: there is no split at a script boundary, and a digit run stays whole (`test_numbers_tokenised_whole`).

Consequences:
- **Grapheme clusters are never split** in Devanagari text: a virama, nukta, vowel sign, ZWJ or ZWNJ never ends up on the other side of a token edge from its base. A token start is always a letter or digit, and a token end is always after a T character or joiner; the characters that stay outside a token (separators, orphans, ignorables) are not Extend characters of a neighbouring cluster (`test_token_edges_never_split_a_grapheme_cluster`). **Documented exception:** a Prepend character (UAX #29 Grapheme_Cluster_Break=Prepend, for example the Arabic number signs U+0600 to U+0605) is a separator by R09 and may be cut off from the letter it precedes. Marathi text has none.
- **Latin-script words inside a Marathi sentence** (mr-009) are ordinary tokens, key = case-folded Latin letters (Decision 8). Two Latin tokens match when their keys are equal. There is no transliteration, so a Latin token never matches a Devanagari spelling of the same word (the exact-match-only rule of ADR 0005; the canonical script of the comparison is the native one, and a transliteration variant is a disagreement) (`test_latin_tokens_inside_marathi_kept_as_tokens`, `test_alignment_mixed_script_latin_tokens`).
- **A Devanagari-Latin boundary inside one run** (`WhatsAppवर`, written without a space) is one token with the key `whatsappवर`. It differs from the two tokens of the spaced spelling. A space is a visible spelling difference, and splitting at the script boundary would invent word boundaries that neither text has (compare UAX #29 rules WB5, WB9 and WB10, which also do not break between letters or digits).
- **Romanised Marathi** (mr-010) is tokenised like any Latin text, with no transliteration. The script check for metrics belongs to the metric layer (ADR 0005 Decision 7).
- Hyphens, apostrophes, slashes and the low line separate tokens: `Wi-Fi` is `wi` and `fi`, and `don't` is `don` and `t`. A hyphenated Marathi compound is two tokens.
- Currency signs, percent signs and other symbols are separators, so a difference in `₹` or `%` alone is not flagged. Known limitation.
- Rejected: **split at script boundaries.** Trade-off: spaced and unspaced spellings would align, at the cost of an invented boundary. Rejected: **keep punctuation as tokens.** Trade-off: more information, but a comma or danda difference would dominate the flags of a task about words, and the speech-to-text output has no reliable punctuation. Rejected: **a mark or joiner after a separator starts a token.** Trade-off: no data is skipped, but a stray combining mark would count as a word.

### 8. Case folding (rule R10)
Every character of the token is case-folded with full Unicode case folding (Python `str.casefold`, CaseFolding.txt statuses C and F). Devanagari has no case and is unchanged. Latin letters match regardless of case: `WhatsApp` and `whatsapp`, `OTP` and `otp`.
- Folding is locale independent: U+0130 (dotted capital I) becomes `i` + U+0307; sharp s becomes `ss`; the Kelvin sign (U+212A) becomes `k`; the ligature U+FB01 becomes `fi`. These come from case folding, not from NFKC. The fullwidth capital A (U+FF21) becomes the fullwidth small a (U+FF41) and stays distinct from ASCII `a`.
- Rejected: **simple case folding or `lower()`.** Trade-off: `lower()` leaves sharp s alone and is not the Unicode caseless-matching operation; full folding makes the key one function of the Unicode data. Rejected: **fold only ASCII letters.** Trade-off: a rule that treats Latin letters in other forms (accented) differently from ASCII has no basis in the Marathi data.

### 9. What is never folded (rules R11, R12, R13, R14, R18)
The key keeps these distinctions. Each is a possible source of false flags and is documented as such:
- **R11: LLA (U+0933, ळ) and LA (U+0932, ल) are never merged.** They are different letters (the pair `काळ`/`काल` of mr-007 and mr-008). LLA + nukta (U+0934 after NFC) is also a different key from both (`test_normaliser_never_merges_lla_and_la`).
- **R12:** chandrabindu (U+0901), anusvara (U+0902) and visarga (U+0903) are not folded or dropped, and not equated with a nasal consonant + virama.
- **R13:** the nukta (U+093C) is never stripped. A speech-to-text output often omits it (`ज़रूर` against `जरूर`); the two keys differ.
- **R14:** no folding of number words against digits and no folding of digits other than U+0966 to U+096F.
- **R18:** no other spelling-variant folding: short and long vowels, `ङ्ग` against `ंग`, schwa spellings, suffix attachment, spacing of postpositions.
- Rejected: **fold the nukta, the nasal marks or vowel length** (fewer false flags, expected from an honest-spelling STT). Trade-off: each fold merges forms that a reader may distinguish, with no native reviewer to say which; each is a candidate for `mr-Deva/2` after review, with the false-flag counts of the P3-31 ablation as evidence.

### 10. Invalid input (rule R17 and rule R08)
- **U+0000 and other control characters** (Cc) are valid text (ADR 0017 and the Phase 1 storage cases keep U+0000). They are separators. The normaliser never raises on them.
- **Lone surrogates** (U+D800 to U+DFFF alone) are **refused**: the normaliser raises a typed error and returns no tokens. The Phase 1 text types reject them and importers replace them before storing (ADR 0017 Decision 1). A lone surrogate reaching the normaliser is therefore a caller defect, and silently replacing or skipping it would hide that the offset base is not the stored text. The pipeline calls `text.replace_lone_surrogates` on the speech-to-text body before it normalises.
- Rejected: **replace lone surrogates inside the normaliser.** Trade-off: the run would continue, but the offsets would refer to a text that is not the stored text. Rejected: **treat them as separators.** Trade-off: same hidden-defect problem, and a surrogate cannot be written as UTF-8. Rejected: **refuse text with U+0000 or controls.** Trade-off: an adversarial export line (P3-24) would stop a whole run.

### 11. Offsets
Offsets are **code points**, half-open `[start, end)`, on the original text exactly as stored (not on its NFC form, not in UTF-16 units). An astral character counts as one code point (`emoji-astral-offsets`). Every token slice `text[start:end]` is non-empty and, once the steps of Decision 2 are applied to it, equals the token's key (`test_tokens_carry_code_point_offsets_into_original_text`). Spans may contain ignorable characters and joiners but never start or end at a separator.

### 12. Rule-change policy
- Any change that can alter, for some input, a key, a token boundary, an offset, or the refusal behaviour is a **new normaliser id** (`mr-Deva/2`, then `/3`). It is never an edit in place. This includes fixing a defect: results already produced with `mr-Deva/1` must stay reproducible, so the old implementation, its rule table and its fixture file remain. The new id gets its own rule table (a new ADR or a dated revision of this one with a new table), its own fixture file, and its own test names.
- A change that cannot alter any output (a refactor, a comment, a new fixture case that the implementation already passes) does not change the id.
- The id is recorded in every result that used the normaliser. The Unicode data version of the running interpreter (`unicodedata.unidata_version`) is recorded next to it as metadata, because category and case-folding data of newly assigned characters can differ between versions. It is not part of the id (judgement call).
- The binding parameter `normaliser` has no default (Q8): a run names the id.
- Mechanism (dev's choice, qa pins it): the registry keeps, for each id, a digest of the rule table and of every fixture case's text and expected tokens; the test fails when the digest changes and the id does not (`test_normaliser_rule_change_requires_new_id`).
- Rejected: **edit `mr-Deva/1` in place and note the change.** Trade-off: no new code path, but stored results would no longer be reproducible and two runs with the same id would not be comparable. Rejected: **put the Unicode version in the id.** Trade-off: strictest reproducibility, but a routine interpreter upgrade would retire every id; recording the version in the result gives the same audit trail.

### 13. Review status
Every rule R01 to R18 and every fixture case is `needs_native_review`. This is a statement that no native speaker has looked at it, not a prediction that it is wrong. Results that use `mr-Deva/1` carry no Indic quality claim until the reviewer of BL-169 has signed off, and the fixture file is not a reference for any word error rate (BL-132).

## Rule table
This table is machine-read by `test_adr0025_rule_table_matches_normaliser`. Format:
- The table is the one under this heading whose header row is exactly `| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |`.
- One row per rule. The first cell is `R` plus two digits (pattern `^R[0-9]{2}$`), unique, in ascending order.
- The code point cell lists `U+XXXX` or `U+XXXX-U+YYYY` (four to six hex digits, upper case) and `cat:` followed by a Unicode general category or its one-letter class, separated by commas.
- The "Effect on display" cell begins with `none`: the display text is never changed.
- The fixture cell lists case ids of `packages/evalcore/tests/fixtures/normaliser_mr.json`, each in backticks, separated by commas. The single value `-` means no fixture case (the rule needs a text that JSON cannot hold; the test named in the effect cell covers it).
- The test checks: the rule ids of the table equal the rule ids implemented by the normaliser; every id in a case's `rules` array is in the table; every case listed in the table exists and names that rule in its `rules` array (and the other way round); every rule with a fixture cell other than `-` has at least one case; every case has `needs_native_review` true.

| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |
|---|---|---|---|---|
| R01 | U+0958-U+095F, U+0929, U+0931, U+0934, U+093C, U+094D, U+212A | NFC is the last step. U+0958-U+095F decompose to base + U+093C and never occur in a key. NA, RA and LLA followed by U+093C compose to U+0929, U+0931, U+0934. A nukta (class 7) sorts before a virama (class 9) that preceded it. | none | `nfc-precomposed-za-word`, `nfc-decomposed-za-word`, `nfc-precomposed-qa-to-yya-run`, `nfc-decomposed-qa-to-yya-run`, `nfc-composes-nnna-rra`, `nfc-precomposed-nnna-rra-unchanged`, `nfc-llla-from-lla-nukta`, `nfc-reorders-nukta-before-virama`, `nfc-joiner-between-base-and-nukta`, `nfc-latin-decomposed-offsets`, `case-kelvin` |
| R02 | U+FF10-U+FF19, U+FF21-U+FF3A, U+2460-U+2473 | NFKC is not applied. Fullwidth and circled forms are not folded to ASCII. | none | `nfkc-fullwidth-latin`, `nfkc-fullwidth-digits-not-folded`, `nfkc-circled-digit-separator` |
| R03 | U+0966-U+096F | Folded to U+0030-U+0039 in the key. | none | `ref-mr-004`, `ref-mr-005`, `digit-single`, `digit-run-123`, `digit-all-ten`, `digit-mixed-forms-one-token`, `digit-after-letter-one-token`, `digit-time-colon`, `number-word-vs-digits`, `symbols-currency-percent`, `script-latin-digits-native`, `script-digit-then-letters`, `sentence-bill`, `sentence-ticket`, `sentence-phone-digits` |
| R04 | U+200C, U+200D | Dropped from the key; inside the token; a trailing one is inside the span; a leading or lone one is outside every token. | none | `ref-mr-006`, `nfc-joiner-between-base-and-nukta`, `joiner-zwnj-conjunct`, `joiner-three-conjunct-forms`, `joiner-double-zwj`, `joiner-eyelash-ra-unconfirmed`, `joiner-trailing-kept-in-span`, `joiner-leading-after-space`, `joiner-alone-between-spaces`, `joiner-only-text` |
| R05 | U+061C, U+200E, U+200F, U+202A-U+202E, U+2066-U+2069 | Ignorable: dropped from the key, do not split a token; outside the span when leading or trailing. | none | `bidi-rlm-inside-token`, `bidi-rlo-inside-number`, `bidi-isolates-around-word`, `bidi-lrm-around-digits`, `bidi-controls-only-text`, `bidi-trailing-control-excluded-from-span`, `bidi-alm-inside-token` |
| R06 | U+00AD, U+2060, U+FEFF | Ignorable in the same way as R05. | none | `invisible-soft-hyphen`, `invisible-word-joiner-and-bom` |
| R07 | cat:P, U+0964, U+0965, U+0970 | Separator: ends a token and is not in any key. | none | `ref-mr-001`, `ref-mr-002`, `ref-mr-003`, `ref-mr-004`, `ref-mr-005`, `ref-mr-009`, `digit-time-colon`, `digit-grouping-comma-ascii`, `punct-danda-after-word`, `punct-double-danda-between`, `punct-abbreviation-sign`, `punct-ascii-set`, `punct-dash-slash-quote`, `punct-only-text`, `punct-next-to-virama-and-nukta`, `symbols-currency-percent`, `script-apostrophe`, `script-underscore`, `sentence-bill`, `sentence-otp`, `sentence-ticket`, `sentence-order`, `sentence-complaint`, `sentence-wifi` |
| R08 | cat:Z, cat:Cc, U+0000, U+200B | Separator: white space, line and paragraph separators, controls including U+0000, and the zero-width space. Never raises. | none | `white-space-kinds`, `control-nul`, `control-bel-nel`, `zwsp-separates`, `white-space-only` |
| R09 | cat:S, cat:Nl, cat:No, cat:Cf, cat:Co, cat:Cn | Separator: symbols, non-decimal numbers, unlisted format characters, private use, unassigned. | none | `nfkc-circled-digit-separator`, `symbols-currency-percent`, `other-numbers`, `emoji-astral-offsets`, `private-use`, `unlisted-format-char` |
| R10 | cat:Lu, cat:Lt, U+0130, U+00DF, U+FB01 | Full case folding of every character before the final NFC. | none | `ref-mr-009`, `ref-mr-010`, `nfc-latin-decomposed-offsets`, `nfkc-fullwidth-latin`, `case-latin-initialism`, `case-three-forms`, `case-sharp-s`, `case-dotted-capital-i`, `case-kelvin`, `case-ligature-fi`, `script-latin-glued-to-devanagari`, `script-latin-digits-native`, `script-apostrophe`, `sentence-otp`, `sentence-ticket`, `sentence-wallet-order`, `sentence-wifi` |
| R11 | U+0932, U+0933, U+0934 | Never merged: LA, LLA and LLA + nukta keep three different keys. | none | `ref-mr-002`, `ref-mr-007`, `ref-mr-008`, `nfc-llla-from-lla-nukta`, `lla-vs-la-in-one-text` |
| R12 | U+0901, U+0902, U+0903 | Not folded and not dropped. | none | `chandrabindu-vs-anusvara`, `visarga-present-vs-absent`, `anusvara-vs-nasal-conjunct` |
| R13 | U+093C | Not stripped; present and absent give different keys. | none | `nfc-decomposed-za-word`, `nfc-reorders-nukta-before-virama`, `punct-next-to-virama-and-nukta`, `nukta-present-vs-absent` |
| R14 | cat:Nd outside U+0966-U+096F | Not folded; number words are not folded against digits. | none | `nfkc-fullwidth-digits-not-folded`, `digit-grouping-comma-ascii`, `digit-other-script-gujarati`, `number-word-vs-digits` |
| R15 | cat:L, cat:M, cat:Nd | A token is a maximal run of letters, marks, decimal digits and ignorables; it starts at the first letter or digit and ends after the last token character or joiner; no split at a script boundary; a run without a letter or digit has no token. | none | `ref-mr-001`, `ref-mr-002`, `ref-mr-003`, `ref-mr-004`, `ref-mr-005`, `ref-mr-009`, `ref-mr-010`, `digit-after-letter-one-token`, `joiner-leading-after-space`, `joiner-alone-between-spaces`, `joiner-only-text`, `bidi-controls-only-text`, `punct-only-text`, `orphan-marks`, `empty-string`, `white-space-only`, `case-latin-initialism`, `script-latin-glued-to-devanagari`, `script-latin-digits-native`, `script-digit-then-letters`, `letters-outside-ordinary`, `sentence-bill` |
| R16 | U+093C, U+094D, U+200C, U+200D, cat:M | Token edges never split a grapheme cluster of Devanagari text: a virama, nukta, vowel sign or joiner stays with its base. | none | `ref-mr-006`, `nfc-reorders-nukta-before-virama`, `joiner-trailing-kept-in-span`, `joiner-leading-after-space`, `bidi-isolates-around-word`, `bidi-trailing-control-excluded-from-span`, `punct-next-to-virama-and-nukta`, `orphan-marks` |
| R17 | cat:Cs | Refused: a typed error, no tokens. Covered by `test_normaliser_refuses_lone_surrogate`. | none | - |
| R18 | U+0907, U+0908, U+0919, U+0902, U+094D | No other spelling-variant folding: vowel length and nasal spellings keep different keys. | none | `anusvara-vs-nasal-conjunct`, `vowel-length-not-folded` |

## Known sources of flags (documented, not fixed in `mr-Deva/1`)
Number words against digits (BL-192); digit-grouping separators; nukta present or absent; chandrabindu, anusvara and visarga; vowel length; nasal-letter spellings; postposition spacing; currency and percent signs; Latin-script words against their Devanagari spellings; a Latin word glued to a Marathi suffix. The P3-31 ablation (normaliser on and off, computed offline) measures how many flags each one produces. Reducing them needs native review (BL-169) and a new id.

## Consequences
- The comparison is insensitive to Unicode composition, digit form, joiners, bidi controls, punctuation and Latin case, and sensitive to everything else.
- Keys are not text to show: reports show the original span through `display_safe()` and may show the key as a separate, labelled value.
- Every other language needs its own normaliser id and rule table after native review (BL-158); `mr-Deva/1` is not applied to another language.
- Rule data is code-free in intent: the id, the rule table and the fixture are data; adding `mr-Deva/2` or another language's id adds a registry entry and a fixture file.
- No claim of Marathi correctness follows from the fixture file passing: its expected values were written by a language model from the rules above (the indic-linguistics agent) and have not been seen by a native reader.

## Invariants (testable)
- I1. The id is `mr-Deva/1`, has the form `<language>-<script>/<integer>`, and is recorded in every result. Test: `test_normaliser_id_is_versioned_and_recorded`.
- I2. Every fixture case yields exactly the expected tokens (key, start, end), every offset slice is non-empty, and the Decision 2 steps on the slice give the key. Test: `test_normaliser_mr_fixture_cases`.
- I3. The key is NFC and not NFKC (R01, R02). Test: `test_normaliser_is_nfc_not_nfkc`.
- I4. LLA, LA and LLA + nukta keep three keys (R11). Test: `test_normaliser_never_merges_lla_and_la`.
- I5. Devanagari digits fold to ASCII in the key only; the span and display keep the original (R03, R14). Test: `test_normaliser_folds_devanagari_digits_in_key_only`; the cross-text effect in `test_alignment_numbers_digits_vs_native_digits`.
- I6. ZWJ and ZWNJ (R04) and bidi controls (R05, R06) are dropped from the key and kept in the display. Test: `test_normaliser_drops_joiners_in_key_only_display_kept`; proposed addition: `test_normaliser_bidi_controls_dropped_in_key_only`.
- I7. Offsets are code points of the unchanged original text. Test: `test_tokens_carry_code_point_offsets_into_original_text`.
- I8. Token edges never split a grapheme cluster (R15, R16). Test: `test_token_edges_never_split_a_grapheme_cluster`.
- I9. Latin-script words inside Marathi are tokens with case-folded keys and no transliteration (R10, R15). Tests: `test_latin_tokens_inside_marathi_kept_as_tokens`, `test_alignment_mixed_script_latin_tokens`.
- I10. Numbers are tokenised whole as digit runs. Test: `test_numbers_tokenised_whole`.
- I11. Any rule change needs a new id. Test: `test_normaliser_rule_change_requires_new_id`.
- I12. The rule table of this ADR matches the normaliser, the fixture rules and the case ids, and every case is `needs_native_review`. Test: `test_adr0025_rule_table_matches_normaliser`.
- I13. Lone surrogates are refused with a typed error and U+0000 or controls never raise (R17, R08). Proposed additions to the P3-13 list: `test_normaliser_refuses_lone_surrogate`, `test_normaliser_control_characters_separate_tokens`.

The proposed test names are not yet in `docs/phase3/SCOPE.md`; the lead adds them to the P3-13 and P3-14 rows.

## Sources
- UAX #15, Unicode Normalization Forms (https://www.unicode.org/reports/tr15/), including canonical ordering by combining class and composition exclusions; `CompositionExclusions.txt` (Unicode 18.0.0, fetched 2026-10-25: U+0958 to U+095F listed, U+0929, U+0931, U+0934 not listed).
- UAX #29, Unicode Text Segmentation (https://www.unicode.org/reports/tr29/), revision 49 for Unicode 18.0.0, fetched 2026-10-25: rules GB9, GB9c, WB4, WB5; the note that U+200B is not in the Format class; rules WB6 to WB12 for the rejected number-separator option.
- UAX #44, Unicode Character Database: general categories (Python `unicodedata`); `CaseFolding.txt` statuses C and F (https://www.unicode.org/Public/UCD/latest/ucd/CaseFolding.txt), not re-fetched in this session.
- The Unicode Standard, chapter 12 (Devanagari, nukta, virama, ZWJ and ZWNJ) and chapter 23 (layout controls); not re-read in this session. The statement that a joiner after a virama can change the rendered shape rests on these chapters.
- UAX #9, Unicode Bidirectional Algorithm, for the explicit embedding, override and isolate controls (ADR 0017).
- ADR 0005 (native script, Latin tokens, `unsupported_script`), ADR 0017 (verbatim storage, bidi, `display_safe`), ADR 0018 (item 7: a provider label is kept verbatim; it says nothing about normalising text, and ADR 0007 on secrets says nothing about it either); Phase 1 fixtures `packages/evalschema/tests/fixtures/languages/mr.json` (mr-001 to mr-010), `cross_cutting.json` and `packages/evaldb/tests/fixtures/indic_storage.json`.

## Not verified here
- No native review of any rule or case (BL-132, BL-169). The decisions on joiners, the nukta, chandrabindu and anusvara, vowel length, the grouping comma and symbols are judgement calls.
- The expected tokens of `normaliser_mr.json` were derived by hand from the rules. A throwaway reference tokeniser outside the repository was written to check them; until it or P3-13 has run against the file, the offsets and keys have not been checked by a machine.
- The texts of the reference cases `ref-mr-001` to `ref-mr-010` were transcribed to escapes by hand from `mr.json`; the same throwaway checker compares them with `mr.json`.
