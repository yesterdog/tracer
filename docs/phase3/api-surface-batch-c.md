# Phase 3 batch C contract: normaliser, tokenizer, alignment, spans, flags (P3-13 to P3-17)

Author: qa, 2026-10-25. Source: `docs/phase3/SCOPE.md` (task table A.3, Decisions D1 to D8, findings F4 to F6), ADR 0023 (rules 5 to 10, the `tracer.flags/1` section), ADR 0025 (Decisions 1 to 13, the 18-row rule table), ADR 0005 and ADR 0017 (verbatim text, lone surrogates), the fixture `packages/evalcore/tests/fixtures/normaliser_mr.json` (94 cases, sha256 pinned by the batch A test `test_normaliser_fixture_moved_unchanged`), the batch A contract `docs/phase3/api-surface-batch-a.md` and the implemented `packages/evalcore/src` (errors, importers, audio) for naming and style. qa owns this file and the staged tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test. Every statement below is pinned by a test named in section 9, unless it says "not pinned".

Everything in this batch is pure logic: standard library plus `tracer_evalschema` only. `tracer_evalcore` imports no network module and no `pydantic` (the batch A tests `test_evalcore_runtime_imports_whitelist` and `test_evalcore_imports_no_network_module` walk every module under `src/`, so the five new modules are covered without any change). The five modules import nothing from `tracer_evalschema` at all; the pipeline (batch D) maps `Turn`/`Segment` records into the plain types below.

## 0. Where the tests are, and how to run them

IMPORTANT (Phase 2 and batch A lesson): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` for the whole workspace. Nothing was created under `packages/` and nothing under it was edited. The tests are staged at

```
docs/phase3/evidence/red/batch-c/staging/tests/    -> packages/evalcore/tests/   (8 files, all ASCII)
```

No `__init__.py`, no `conftest.py`, no data file (the fixture `normaliser_mr.json` is already in `packages/evalcore/tests/fixtures/`). The dev's first act, before any code:

```
git mv docs/phase3/evidence/red/batch-c/staging/tests/*.py packages/evalcore/tests/
```

Files (test counts are collected items on the oracle; "functions" counts `def test_` and differs where a test is parametrised):

| File | Task | Functions | Collected |
|---|---|---|---|
| `evalcore_batchc_support.py` | helpers (not collected): lazy module loaders, reference edit distance, exhaustive aligner, hypothesis text strategies | - | - |
| `test_normaliser_mr.py` | P3-13 | 14 | 107 (94 fixture cases) |
| `test_tokens.py` | P3-14 | 15 | 15 |
| `test_alignment.py` | P3-15 | 18 | 18 |
| `test_spans.py` | P3-16 | 12 | 12 |
| `test_flags.py` | P3-17 | 21 | 21 |
| `test_adr0025_rule_table.py` | ADR 0025 | 3 | 3 |
| `test_adr0023_recorded_scoring.py` | ADR 0023 | 5 | 9 |
| total | | 88 | 185 |

Each test module loads `tracer_evalcore.<module>` lazily with `importlib` (inside the helper `normalise_mod()`, `tokens_mod()`, `align_mod()`, `spans_mod()`, `flags_mod()`), so before the modules exist every test fails on its own with `ModuleNotFoundError` and the directory still collects. The helper is imported as a sibling module (`from evalcore_batchc_support import ...`, with `# type: ignore[import-not-found,unused-ignore]` exactly as `packages/evaldb/tests/test_evaldb_codecs.py` does for `evaldb_support`); its name is unique across all test directories. `ruff check`, `ruff format --check` and `mypy --strict` pass on the eight files as staged (checked on the oracle with the repository configuration; the three `# noqa: PT011 (message deliberately not pinned)` follow the Phase 1 convention). Nothing opens a socket, reads the clock for a result, or reads any private fixture. All text is invented; every non-ASCII character is an escape.

Commands (after the move): `uv run --locked --all-groups pytest packages/evalcore/tests` (everything), or only this batch: `uv run --locked --all-groups pytest packages/evalcore/tests/test_normaliser_mr.py packages/evalcore/tests/test_tokens.py packages/evalcore/tests/test_alignment.py packages/evalcore/tests/test_spans.py packages/evalcore/tests/test_flags.py packages/evalcore/tests/test_adr0025_rule_table.py packages/evalcore/tests/test_adr0023_recorded_scoring.py`.

Hypothesis is used deterministically, as in Phase 1 and 2 (`packages/evaldb/tests/test_evaldb_codecs.py`): every property test carries `@settings(max_examples=80, deadline=None, derandomize=True, phases=[Phase.explicit, Phase.generate], suppress_health_check=[too_slow, function_scoped_fixture])` (defined once per file as `HSETTINGS`, because a settings object imported from the untyped helper makes mypy report `untyped-decorator`). `hypothesis==6.170.5` is already in the pinned `test` group.

## 1. Module layout of `tracer_evalcore`

```
tracer_evalcore/normalise.py   comparison key, normaliser registry, rule ids, errors      (P3-13)
tracer_evalcore/tokens.py      Token, tokenise, TextTurn, TimedSegment, TokenSequence     (P3-14)
tracer_evalcore/align.py       OpKind, AlignOp, Alignment, align_keys, align, MER, cap    (P3-15)
tracer_evalcore/spans.py       Span, extract_spans                                         (P3-16)
tracer_evalcore/flags.py       Signal, Confidence, flag_spans, threshold, FLAGS_JSON_SCHEMA (P3-17)
```

Dependencies inside the package (no cycles): `normalise` <- `tokens` <- `align`/`spans` <- `flags`. All error types derive from the batch A bases in `tracer_evalcore/errors.py`: `InputRefusedError(EvalcoreError, ValueError)` for a refused input, `EvalcoreError` alone for a computation that is undefined. Messages carry sizes, counts, indices and codes, never any part of the text, any key or any sequence value (tested with a marker string in every refusal path).

## 2. `tracer_evalcore.normalise` (P3-13, ADR 0025)

```python
NORMALISER_ID: str = "mr-Deva/1"
SUPPORTED_NORMALISERS: tuple[str, ...] = ("mr-Deva/1",)
IMPLEMENTED_RULES: frozenset[str]          # exactly {"R01", ..., "R18"}

@dataclass(frozen=True)
class NormaliserInfo:
    id: str
    unicode_version: str                    # == unicodedata.unidata_version of the interpreter

def normaliser_info(normaliser: str) -> NormaliserInfo
def comparison_key(text: str, normaliser: str) -> str

class NormaliserError(InputRefusedError)
class UnknownNormaliserError(NormaliserError)
class LoneSurrogateError(NormaliserError)
```

2.1 Id (`test_normaliser_id_is_versioned_and_recorded`). The id has the form `^[a-z]{2,3}-[A-Z][a-z]{3}/[1-9][0-9]{0,8}$` (the pattern of the `tracer.flags/1` schema); the fixture's `normaliser` field equals `NORMALISER_ID`. `normaliser` is a required argument everywhere (inspect: no default on `comparison_key`, `tokenise`, `normaliser_info`, `tokenise_turns`). An id the package does not ship (`mr-Deva/2`, `mr-Deva/0`, `mr-deva/1`, `hi-Deva/1`, `mr-Deva`, empty, `mr-Deva/01`) raises `UnknownNormaliserError`, never maps to the nearest id; a non-`str` id raises `TypeError`. `normaliser_info(...).unicode_version` is `unicodedata.unidata_version` (ADR 0025 Decision 12: recorded next to the id in the result, not part of the id).

2.2 `comparison_key(text, normaliser)` applies ADR 0025 Decision 2 to the text it is given, in this order: drop the ignorable characters (U+00AD, U+061C, U+200C, U+200D, U+200E, U+200F, U+202A-U+202E, U+2060, U+2066-U+2069, U+FEFF), fold U+0966-U+096F to `0`-`9`, full case folding (`str.casefold`), NFC. Tested by hand cases for R01 to R14 and R18 (`test_normaliser_never_merges_lla_and_la`, `..._folds_devanagari_digits_in_key_only`, `..._drops_joiners_in_key_only_display_kept`, `..._is_nfc_not_nfkc`, `..._bidi_controls_dropped_in_key_only`), by the 94 fixture cases (`test_normaliser_mr_fixture_cases`: for every expected token the slice is non-empty and `comparison_key(slice)` equals the expected key) and by a property: the key of the NFD spelling equals the key of the NFC spelling for strings over Devanagari consonants, nukta, virama, signs, ZWJ/ZWNJ and Latin with combining accents. The order matters and is pinned by the fixture case `nfc-joiner-between-base-and-nukta` (a nukta after a joiner composes with its base only if the joiner is dropped before NFC; mutant N22).

2.3 Refusals (R17, `test_normaliser_refuses_lone_surrogate`). A lone surrogate (any of U+D800 to U+DFFF as a code point of the `str`, including the two halves of a UTF-16 pair written as two code points) raises `LoneSurrogateError` from `comparison_key`, `tokenise` and `tokenise_turns`; no token is returned; the message holds the index at most and never the text. A character outside the BMP is one code point and is accepted. U+0000 and every other control character never raise (`test_normaliser_control_characters_separate_tokens`: all 65 `Cc` code points separate tokens). A non-`str` text raises `TypeError`.

2.4 Rule change policy (`test_normaliser_rule_change_requires_new_id`, ADR 0025 Decision 12 "mechanism: dev's choice, qa pins it"). The mechanism pinned is a test-side digest, not registry code: the test file holds `PINNED["mr-Deva/1"]` with three sha256 values, recomputed on every run from (a) the ADR 0025 rule table rows (cells joined by TAB, rows by LF), (b) the fixture's `[id, text, expected_tokens, rules]` per case as canonical JSON, (c) the outputs of `comparison_key` and `tokenise` over a deterministic corpus (every code point of U+0000-U+017F, U+0900-U+097F, U+2000-U+206F, U+FF10-U+FF19, U+FF21-U+FF3A, U+2460-U+2473, U+FB01 and U+FEFF, each alone and in three contexts; blocks whose Unicode data is the same on Python 3.11 to 3.14, verified). The test also requires `set(PINNED) == set(SUPPORTED_NORMALISERS)` (a new id needs a new pinned entry and keeps the old one) and `"mr-Deva/1" in SUPPORTED_NORMALISERS` forever. A change to any output of `mr-Deva/1` therefore fails the test; the remedy is a new id (`mr-Deva/2`), a new fixture file and new test names, never an edit of the pin. `test_normaliser_digest_pins_are_sensitive_to_the_inputs` checks that the digests really move when a fixture value or a table row changes. Consequence stated openly: an editorial edit of the ADR 0025 rule table also moves digest (a); see ambiguity 14.

## 3. `tracer_evalcore.tokens` (P3-14, ADR 0023 rule 5, ADR 0025 Decisions 7 and 11)

```python
@dataclass(frozen=True, slots=True)
class Token:                       # fields in this order
    key: str                       # comparison key of text[start:end]; never empty; NFC
    start: int                     # code point index, half-open
    end: int

def tokenise(text: str, normaliser: str) -> tuple[Token, ...]

@dataclass(frozen=True, slots=True)
class TimedSegment:
    text: str
    start_ms: int | None
    end_ms: int | None             # ints are not bool, >= 0; start <= end when both are present

@dataclass(frozen=True, slots=True)
class TextTurn:
    seq: int                       # int, not bool, >= 0
    text: str
    segments: tuple[TimedSegment, ...] = ()

@dataclass(frozen=True, slots=True)
class TurnToken:                   # fields in this order
    turn_index: int                # position of the turn in TokenSequence.turns
    turn_seq: int                  # the turn's own seq
    key: str
    start: int                     # offsets into THAT turn's text
    end: int
    segment_index: int | None      # index into turns[turn_index].segments, or None

@dataclass(frozen=True)
class TokenSequence:
    turns: tuple[TextTurn, ...]
    tokens: tuple[TurnToken, ...]  # all turns, in turn order, global indices 0..n-1
    @property
    def keys(self) -> tuple[str, ...]

def tokenise_turns(turns: Sequence[TextTurn], normaliser: str) -> TokenSequence
class TurnOrderError(InputRefusedError)
```

3.1 `tokenise` implements ADR 0025 Decision 7 over the ORIGINAL text. A token is found in each maximal run of token characters (L*, M*, Nd) and ignorables; it starts at the first letter or decimal digit of the run, ends after the last token character or joiner (U+200C, U+200D), and a run with no letter or digit has no token. Everything else (all punctuation including U+0964, U+0965, U+0970; Z*; Cc; symbols; Nl/No; Cf not listed, among them U+200B; Co; Cn) separates. Offsets are Python string indices (code points): not UTF-16 units, not bytes, not positions in the NFC form. The key is `comparison_key(text[start:end])`. Tests: `test_tokens_carry_code_point_offsets_into_original_text`, `test_token_edges_never_split_a_grapheme_cluster` (virama, nukta, joiner, vowel signs, orphan marks), `test_latin_tokens_inside_marathi_kept_as_tokens` (mr-009, `Wi-Fi`, `don't`, a Latin word glued to a Devanagari suffix is one token), `test_numbers_tokenised_whole` (`1,00,000` is three tokens, `3.5` two, a 1,000-digit run one), `test_normaliser_control_characters_separate_tokens`, the fixture test, and the properties `test_tokens_properties_over_mixed_text` (sorted, non-overlapping, slice non-empty, starts at a letter or digit, ends at a token character or joiner, no separator inside, a separator between consecutive tokens, key equals `comparison_key(slice)`, key is NFC) and `test_token_edges_coincide_with_cluster_edges_by_construction` (strings built from known Devanagari clusters and separators, with the expected spans computed by construction, independent of any class table).

3.2 Adversarial: instruction-like text is data (`test_tokeniser_handles_adversarial_text_without_error`); a 20,000-repeat control/bidi/joiner storm and a 400,000-character single token tokenise in well under the 30 s bound (the tokeniser must be linear).

3.3 `tokenise_turns` (`test_tokenise_turns_*`). The turns must already be in strictly increasing `seq` order; anything else (descending, equal, a gap-free permutation) raises `TurnOrderError` (an `InputRefusedError`), it never sorts. Turns with no token are kept in `turns`, contribute no token and still count in `turn_index`. Segment mapping (ambiguity 1): when a turn has `segments`, each segment's text is tokenised with the same normaliser; if the concatenation of the segments' token keys equals the turn's token keys exactly, token i gets the index of the segment it came from; otherwise EVERY token of that turn has `segment_index=None`. Never a guess. `TimedSegment` and `TextTurn` validate their fields at construction (`TypeError` for a wrong type or a bool, `ValueError` for a negative number or `start_ms > end_ms`; the message holds no text).

## 4. `tracer_evalcore.align` (P3-15, ADR 0023 rules 6 and 7)

```python
ALIGN_MAX_TOKENS: int                      # named constant; >= 2000; changing it changes the scorer version

class OpKind(enum.StrEnum):                # members in this order
    MATCH = "match"; SUBSTITUTION = "substitution"; DELETION = "deletion"; INSERTION = "insertion"

@dataclass(frozen=True, slots=True)
class AlignOp:
    kind: OpKind
    export_index: int | None               # None for an insertion
    stt_index: int | None                  # None for a deletion

@dataclass(frozen=True)
class Alignment:                           # fields in this order
    ops: tuple[AlignOp, ...]               # in sequence order
    hits: int; substitutions: int; deletions: int; insertions: int
    # __post_init__: ValueError unless the four counts equal the tallies of ops
    @property export_tokens -> int         # hits + substitutions + deletions
    @property stt_tokens    -> int         # hits + substitutions + insertions
    @property edits         -> int         # substitutions + deletions + insertions
    @property mer           -> float       # edits / (hits + edits); UndefinedMerError when that is 0

def align_keys(export_keys: Sequence[str], stt_keys: Sequence[str], *, max_tokens: int = ALIGN_MAX_TOKENS) -> Alignment
def align(export: TokenSequence, stt: TokenSequence, *, max_tokens: int = ALIGN_MAX_TOKENS) -> Alignment   # = align_keys(export.keys, stt.keys)

class AlignmentTooLargeError(InputRefusedError):   # attributes export_tokens, stt_tokens, max_tokens (ints)
class UndefinedMerError(EvalcoreError)             # NOT a ValueError: nothing the caller can fix by an argument
```

4.1 Cost pair and tie-break, exactly ADR 0023 rule 6. Keys are compared for equality only (case, NFC and everything else belong to the normaliser: `["A"]` against `["a"]` is a substitution). The dynamic program minimises the pair (edits, deletions plus insertions), lexicographically, added component by component: match (0, 0), substitution (1, 0), deletion (1, 1), insertion (1, 1). A deletion is an EXPORT token with no STT token (`export_index` set, `stt_index` None); an insertion an STT token with no export token. The backtrace from the end prefers, at each step and among steps that stay on an optimal path: a match, then a substitution, then a deletion, then an insertion. A substitution always has two different keys, a match two equal keys. Hand-fixed examples that distinguish every wrong choice: `[a,b]` against `[b,c]` is two substitutions (H0 S2, MER 1.0; not delete + match + insert); `[a,b,a]` against `[b,c,a,b]` is H1 S2 I1 (MER 3/4; plain edit distance with the same tie-break finds H2 D1 I2, MER 3/5; found by exhaustive search; pairs of total length 7 over three letters are the shortest that differ); `[a,b,a]` against `[a]` deletes the first two (the LAST `a` is matched); `[a]` against `[a,b,a]` inserts the first two; `[a,b]` against `[c]` is deletion then substitution; `[c]` against `[a,b]` is insertion then substitution.

4.2 Exhaustive and property proof of the rule (`test_alignment_equals_exhaustive_reference_on_every_small_pair`, `test_alignment_tie_break_is_deterministic`, `test_alignment_ops_are_a_consistent_walk_over_both_sequences`). The reference enumerates every alignment (Delannoy paths) of two small lists, keeps those of minimal cost pair, and picks the one whose REVERSED op list is smallest under the rank match < substitution < deletion < insertion; this is exactly what a backtrace with that preference yields (the kind sequence fixes the path). It is compared with the implementation on every pair of key lists over {a,b} up to length 4 and over {a,b,c} up to length 3 (2,561 pairs), asserting also that all minimal alignments share H, S, D and I (the uniqueness claim), and on random lists up to length 6 over {a,b,c,d}. The ops of every alignment form a consistent walk: export indices 0..n-1 once each in order, STT indices 0..m-1 once each in order, `edits` equals the unit-cost Levenshtein distance of the two lists (independent two-row implementation in the helper).

4.3 MER (`test_mer_in_unit_interval_and_exact_counts`, `test_alignment_is_symmetric_in_mer`, `test_mer_counts_are_exact_on_a_hand_computed_pair`): `mer == (S+D+I) / (H+S+D+I)` as Python true division of two ints (the correctly rounded float; also `== float(Fraction(...))`), no rounding, in [0, 1], `0.0` iff the lists are equal, `1.0` iff H is 0. Swapping the lists swaps D and I and changes nothing else, so the MER is equal (the op lists need not mirror). The unit `ratio` and the absence of `wer`/`cer` are batch D (P3-18).

4.4 Empty sides (`test_alignment_empty_side_cases`, ambiguity 3). Both empty: `ops == ()`, all counts 0, and `.mer` raises `UndefinedMerError` (never 0.0, never 1.0, never `None`). One side empty: all deletions or all insertions, MER 1.0. The aligner does not refuse empty sides; the scorer (batch D, ADR 0023 rule 15) turns an empty side into `error` `no_tokens` before it uses the alignment.

4.5 Size cap (`test_alignment_size_cap_typed_error`). The cap is per side: `len(export_keys) > max_tokens` or `len(stt_keys) > max_tokens` raises `AlignmentTooLargeError` before any table is built (both sides over the cap: tracemalloc peak under 4 MiB and under 5 s; one side over the cap and the other holding a single key or none also raises). Exactly `max_tokens` on each side is accepted. `max_tokens` is keyword-only, an `int` that is not a `bool` (`TypeError` otherwise) and at least 1 (`ValueError`, and not `AlignmentTooLargeError`, even for empty lists). The default is `ALIGN_MAX_TOKENS`, which must be at least 2000 (the measurement size of P3-15 and well above a real call); its value is the dev's, from the measurement, stated in the CHANGELOG (not pinned beyond that). Recommendation in section 10.

4.6 Types (`test_alignment_refuses_non_sequences_of_str`): a `str`, `bytes`, `None`, an `int`, a list holding a non-`str` raises `TypeError`; tuples are accepted. `test_alignment_measurement_2000_tokens` aligns 2,000 against 1,970 unique-keyed tokens with exactly known counts (H1750 S200 D50 I20) and records the elapsed time with `record_property("align_2000_tokens_seconds", ...)` and a `print`; the only assertion on time is a 120 s alarm (no threshold, as SCOPE says).

4.7 Mixed script and digits (`test_alignment_mixed_script_latin_tokens`, `test_alignment_numbers_digits_vs_native_digits`): Latin case does not matter; a Devanagari spelling of a Latin word is a substitution; `WhatsApp` + suffix glued differs from the spaced form; native digits equal ASCII digits; `1,00,000` against `100000` is S1 D2; number word against digit and Gujarati digit against `1` are substitutions.

## 5. `tracer_evalcore.spans` (P3-16, ADR 0023 rule 8)

```python
@dataclass(frozen=True, slots=True)
class Span:                                # fields in this order
    turn_index: int                        # index into export.turns
    turn_seq: int                          # that turn's seq
    start: int                             # half-open code points of THAT turn's text
    end: int
    export_token_start: int                # half-open indices into export.tokens
    export_token_end: int                  #   (equal for a zero-width span: the index of the first export token after the gap)
    stt_token_start: int                   # half-open indices into stt.tokens (equal for a pure deletion: the position of the gap)
    stt_token_end: int
    substitutions: int; deletions: int; insertions: int
    audio_start_ms: int | None
    audio_end_ms: int | None
    @property export_tokens -> int         # substitutions + deletions

def extract_spans(alignment: Alignment, export: TokenSequence, stt: TokenSequence) -> tuple[Span, ...]
class SpanInputError(InputRefusedError)
```

5.1 Spans (`test_spans_are_maximal_non_match_runs`). A span is a maximal run of consecutive non-match ops, split where its export tokens belong to different export turns. Substitutions and deletions go with their export token. Equal-adjacent runs separated by one match are two spans; an alignment with no non-match op gives `()`. `start` is the start of the first span token, `end` the end of the last (`export.turns[turn_index].text[start:end]` is shown exactly as written, punctuation, white space, joiners and bidi controls between the tokens included; leading and trailing ignorables are outside), so `start == export.tokens[export_token_start].start` and `end == export.tokens[export_token_end - 1].end` (`test_span_maps_back_to_exact_export_substring`: NFD, joiners, bidi, astral characters, second turn). Spans are in op order, which equals sort order by (`turn_seq`, `start`, `end`, `stt_token_start`).

5.2 Turn boundaries (`test_spans_never_cross_turn_boundaries`): a run over the last token of turn 0 and the first of turn 1 gives two spans; `turn_seq` is the turn's own `seq` (not its position); an empty turn between does not merge sides.

5.3 Insertions inside a run (`test_insertions_in_a_split_run_follow_the_nearest_export_token`): an insertion goes with the span of the nearest export token BEFORE it in the run; with none before it in the run, with the first export token AFTER it; the STT range of that span includes it. Example: ops `m s i s m` over two turns puts the insertion with the first turn's span; ops `m i s s m` puts it with the first substituted token.

5.4 Insertion-only runs (`test_insertion_flag_is_zero_width_at_gap`): one zero-width span per run, `start == end`, `export_token_start == export_token_end == k` where k is the number of export tokens before the gap, `insertions` = the run length, `stt_token_start/end` = the inserted tokens, `substitutions == deletions == 0`. Position: at the END offset of export token k-1, in that token's turn (`turn_seq` of the token before the gap, also when the next token is in the next turn); when k is 0, at the START offset of the first export token. The end offset of a token is the token's end (after a trailing joiner, before trailing ignorables).

5.5 Spans only on the export (`test_spans_only_on_export_never_on_stt`): no field holds STT text or STT offsets; the STT side is addressed by token indices only.

5.6 Audio range (`test_span_audio_range_from_stt_segments_or_absent`, ambiguity 1 and 5): both fields are `None` unless every STT token of the span (`stt.tokens[stt_token_start:stt_token_end]`, at least one) has a `segment_index`, every segment holding one has both bounds, and `first.start_ms <= last.end_ms`; then `audio_start_ms` is the start of the segment of the FIRST such token and `audio_end_ms` the end of the segment of the LAST. A zero is a real time. No STT token (a pure deletion): both `None`. Never extrapolated, never taken from the turn, never 0.

5.7 Refusals (`SpanInputError`, message without text): the alignment's counts disagree with the token counts (`export_tokens != len(export.tokens)` or `stt_tokens != len(stt.tokens)`), or the ops do not walk the indices in order, or an insertion-only run exists while the export has no token (nothing to anchor to). Both sequences empty with an empty alignment give `()`.

5.8 Property proof (`test_spans_equal_the_reference_and_hold_every_invariant`): for random export/STT texts (1 to 3 turns, 0 to 5 words, mixed separators incl. danda, tab and ZWSP, leading and trailing punctuation), the spans equal a reference extraction written in the test (groups ops per run, assigns insertions, splits by turn, anchors zero-width spans), and: sorted, non-overlapping in a turn, op counts sum to the alignment's, `export_tokens == S + D`, `stt range == S + I`, zero-width iff `export_tokens == 0`, all tokens of a span in one turn, the union of the span export ranges is exactly the set of non-matched export tokens, a zero-width span is never inside another span. A 2,000-token call yields exactly the 286 isolated substitution spans; instruction-like export text maps back like any other.

## 6. `tracer_evalcore.flags` (P3-17, ADR 0023 rules 9, 10 and the `tracer.flags/1` section)

```python
SIGNAL_NAME: str = "stt_disagreement"
FLAGS_FORMAT: str = "tracer.flags/1"
FLAGS_JSON_SCHEMA: dict[str, Any]          # == the JSON block of ADR 0023, key for key

def levenshtein(a: str, b: str) -> int                                  # unit costs over code points, no normalisation
@dataclass(frozen=True, slots=True)
class Confidence:                          # fields: edit_distance, key_length
    edit_distance: int; key_length: int    # ints (not bool) with 0 <= edit_distance <= key_length, key_length >= 1
    @property display -> str               # canonical decimal of edit_distance/key_length, half-even, 4 digits
    def meets(self, threshold: Decimal) -> bool   # exact: Fraction(edit_distance, key_length) >= Fraction(threshold)

@runtime_checkable
class Signal(Protocol):
    @property
    def name(self) -> str: ...
    def confidence(self, span: Span, export: TokenSequence, stt: TokenSequence) -> Confidence: ...

class SttDisagreementSignal:  name = "stt_disagreement"
STT_DISAGREEMENT: SttDisagreementSignal

def parse_threshold(value: int | float | Decimal) -> Decimal
def format_threshold(threshold: Decimal) -> str
class ThresholdError(InputRefusedError)

@dataclass(frozen=True, slots=True)
class SpanFlag:                            # fields in this order
    span: Span; signal: str; edit_distance: int; key_length: int
    confidence: str                        # Confidence.display
    flagged: bool; near_chunk_boundary: bool

def near_chunk_boundary(span: Span, stt: TokenSequence) -> bool
def flag_spans(spans: Sequence[Span], export: TokenSequence, stt: TokenSequence, *, signal: Signal, flag_min_confidence: int | float | Decimal) -> tuple[SpanFlag, ...]
```

6.1 Confidence of `SttDisagreementSignal` (rule 9; `test_confidence_in_unit_interval_and_deterministic`, `test_confidence_follows_adr_rule_9_on_random_spans`). Export key = the KEYS of the span's export tokens joined by one U+0020; STT key likewise over `stt.tokens[stt_token_start:stt_token_end]`; `edit_distance = levenshtein(a, b)`; `key_length = max(len(a), len(b), 1)`. Hand values: `abcd`/`abce` 1/4 -> `0.25`; `gh`/`xh` 1/2; two-letter word fully different 2/2 -> `1`; `abcdefghijk`/`abcdefghijx` -> `0.0909`; `WHATSAPP`/`whatsapq` 1/8 (keys, not text); native digit `1234` against `1239` 1/4; `ab cd` against `xb` 4/5; the LLA/LA pair 1/3 -> `0.3333`. The result is a `Confidence`; the call is pure and repeatable.

6.2 Pure insertion or deletion (`test_confidence_is_one_for_pure_insertion_or_deletion`): one key is empty, so `edit_distance == key_length` and `display == "1"` (deleting `bb cc` gives 5/5, inserting `xyz` 3/3, `xy zzz` 6/6, a one-letter token 1/1).

6.3 `Confidence.display` (`test_confidence_record_and_rounding_half_to_even_to_four_digits`): the exact fraction rounded half to even to 4 fractional digits, then written canonically: no sign, no exponent, no trailing zeros, `0` and `1` bare. It matches the schema pattern `^(?:0|1|0\.[0-9]{0,3}[1-9])$` for every input. Table: 1/2 `0.5`, 1/3 `0.3333`, 2/3 `0.6667`, 1/8 `0.125`, 1/16 `0.0625`, 1/32 `0.0312` (tie, 2 is even), 3/32 `0.0938` (tie, 7 is odd), 1/20000 `0`, 3/20000 `0.0002`, 5/20000 `0.0002`, 7/20000 `0.0004`, 9/20000 `0.0004`, 99999/100000 `1`, 0/5 `0`. The record validates its integers (`ValueError` outside the range, `TypeError` for a bool, float, str or None).

6.4 Exact comparison (`test_confidence_compares_exactly_never_on_the_rounded_display_value`, `test_flag_kept_iff_confidence_at_or_above_threshold`, `test_flagged_iff_exact_fraction_at_or_above_threshold_on_random_spans`): flagged iff `edit_distance >= flag_min_confidence * key_length` in exact arithmetic. 24999/25000 displays `1` but is not flagged at threshold 1 and is flagged at 0.99996; 1/3 is flagged at 0.3333 and at 0.333333333333333333 (18 digits) and not at 0.3334 or 0.333333333333333334; 3/10 at 0.3, 7/10 at 0.7, 1/2 at 0.5 and 1/1 at 1 are flagged (at or above); threshold 0 flags everything, 1 only `edit_distance == key_length`. A float or low-precision Decimal comparison fails these (mutants F4, F5). `meets` takes a `Decimal` only (`TypeError` otherwise). `flag_spans` returns a flag for EVERY span, flagged or not, in span order.

6.5 Threshold (`test_flag_threshold_has_no_default`, `test_threshold_is_read_as_the_decimal_of_the_shortest_round_trip_spelling`, `test_threshold_is_written_in_the_canonical_form_of_the_record_schema`, `test_threshold_round_trip_through_the_record_text`). `flag_spans(..., *, signal, flag_min_confidence)`: both are keyword-only and have NO default (inspect and a call without them is a `TypeError`); no public function of the module has a default for a parameter whose name contains `confidence` or `threshold`, and the module has no name with `default` next to `threshold`/`confidence`/`flag`, no `FLAG_MIN_CONFIDENCE`. `parse_threshold`: `Decimal(repr(value))` for a float (`0.1` is `Decimal("0.1")`, not the binary expansion; `1e-05` is `0.00001`), `Decimal(value)` for an int, a `Decimal` as is. A `bool`, `str`, `None`, `bytes`, list or tuple raises `TypeError`. `ThresholdError` (an `InputRefusedError`) for: NaN, infinities, below 0, above 1, and more than 18 fractional digits after normalising (`1e-19`, `1e-30`, `0.1234567890123456789`): ambiguity 6. `format_threshold(Decimal)` writes the canonical string of the schema pattern `^(?:0|1|0\.[0-9]{0,17}[1-9])$` (`0.50` -> `0.5`, `1.0` -> `1`, `0E-5` -> `0`, `1E-18` -> `0.000000000000000001`); out-of-range or too many digits raise `ThresholdError`, a non-`Decimal` raises `TypeError`. Invalid thresholds stop `flag_spans` before the signal is asked about any span.

6.6 Signal interface (`test_signal_interface_accepts_second_signal_stub`). `Signal` is a `runtime_checkable` structural Protocol (no base class needed): `isinstance(STT_DISAGREEMENT, Signal)`, a stub class with a `name` and a `confidence(span, export, stt) -> Confidence` is accepted and its `name` appears in `SpanFlag.signal`; an object without `confidence` is not a Signal and raises `TypeError` in `flag_spans`. The name must be a machine key `^[a-z][a-z0-9_]{0,63}$` (`TypeError`/`ValueError` for `""`, `Bad Name`, `UPPER`, 65 characters, `1abc`, `x-y`, `None`, `5`); a `confidence` that returns anything but a `Confidence` raises `TypeError`. The `tracer.flags/1` schema allows only `stt_disagreement`; a second signal needs its own format version (BL-173 to BL-175), which is not this batch.

6.7 `near_chunk_boundary` (`test_near_chunk_boundary_marked`, rule 10): for a span with STT tokens, true iff `stt.tokens[stt_token_start:stt_token_end]` contains the first or the last token of any STT turn (`turn_index` runs; a one-token turn is both; the first token of the first turn and the last of the last count); for a span without STT tokens (a pure deletion), true iff its gap position j has `0 < j < len(stt.tokens)` and the tokens j-1 and j belong to different STT turns (an empty turn between still separates them). Position only: no time is read. A span whose STT range exceeds the sequence raises `ValueError`. `SpanFlag.near_chunk_boundary` equals the function.

6.8 The record (`test_span_flag_record_is_frozen_with_pinned_fields`, `test_a_marathi_pair_end_to_end_with_exact_values`): for the Marathi pair `... KA-AA-LLA ...` (U+0915 U+093E U+0933) against `... KA-AA-LA ...` (U+0915 U+093E U+0932) (kaal, LLA against LA) the pipeline of the five modules gives H3 S1, MER 0.25, one span at (12, 15) whose substring is the export word, edit distance 1, key length 3, confidence `0.3333`, flagged at 0.3333 and not at 0.3334 (recorded in both cases). `test_the_pure_functions_log_and_print_nothing` runs a whole pass and five refusals with a marker string and asserts no log record, nothing on stdout or stderr, and the marker in no message.

## 7. What the ADR tests parse

`test_adr0025_rule_table_matches_normaliser` (file `test_adr0025_rule_table.py`) reads `docs/adr/0025-marathi-comparison-normaliser.md`. The table is the one whose header line equals `| Rule | Code points or category | Effect on key | Effect on display | Fixture cases |` (exactly one such line; the same text appears inside the prose above the table and is skipped because the match is on the whole stripped line). It asserts: rule ids match `^R[0-9]{2}$`, are unique and ascending and are exactly `R01`..`R18`; the set equals `tracer_evalcore.normalise.IMPLEMENTED_RULES`; every code point cell item is `U+XXXX` or `U+XXXX-U+YYYY` (4 to 6 upper-case hex digits, low <= high <= U+10FFFF) or `cat:` plus a Unicode category or class (`L`, `Lu`, ...), comma separated (R14 reads `cat:Nd outside U+0966-U+096F`: the test accepts a category followed by `outside U+XXXX-U+YYYY`, ambiguity 13); the display cell begins with `none`; the fixture cell is either `-` (only R17, and no case may name R17) or backticked case ids separated by `, `, each existing in the fixture and naming that rule in its `rules`, and every case's `rules` entry is in the table and lists the case; every rule except R17 has at least one case; R17's key cell cites `test_normaliser_refuses_lone_surrogate`; every case has `needs_native_review` true; the fixture's `normaliser` equals `NORMALISER_ID`. Two extra tests: `test_adr0025_rule_table_code_points_are_real_where_they_name_a_category` (single code points of R06, R07, R08, R11, R13 are listed and have the stated category) and `test_adr0025_invariants_cite_tests_that_exist` (I1 to I13 each cite a test that exists, nothing pending).

`test_adr0023_rules_are_tested` (file `test_adr0023_recorded_scoring.py`), in the style of `test_evaldb_adr0021.py` and `test_evaldb_adr0022.py`: the rules are the lines `N. **` of "## Decision" (19, numbered 1 to 19), the invariants the lines `- IN. ` of "## Invariants (testable)" (I1 to I10); each cites at least one name in backticks matching `test_\w+`; every cited name is defined as a `def test_` in some `packages/*/tests/test_*.py` (AST scan) or is in `PENDING`. `PENDING` is a literal frozenset of 39 names, exactly the names ADR 0023 cites that no test defines at this point; every one must be named in a row of SCOPE section A (`| P3-xx |` lines), none may be named by a row of P3-01 to P3-05 or P3-13 to P3-17, none may be defined (`assert not PENDING & defined`: the dev removes a name from `PENDING` in the commit that adds its test, so the set shrinks to empty by the gate P3-G), and each must still be cited by the ADR. Also: `test_every_test_named_by_the_scope_rows_of_batch_c_exists[P3-13..P3-17]` (every name in the five rows is defined) and `test_the_batch_c_rows_name_the_exact_tests_of_this_batch` (the rows name 9, 5, 10, 6 and 6 tests, and the three ADR tests exist).

`test_adr0023_flags_format_matches_json_schema`: the single ```json block of the section "## The `tracer.flags/1` format" of ADR 0023 is parsed; `tracer_evalcore.flags.FLAGS_JSON_SCHEMA` must equal it as a dict (and by `json.dumps(sort_keys=True)`); `FLAGS_FORMAT` equals its `title` and the `format` and `signal` enums. `test_flags_schema_accepts_the_documented_document_and_refuses_each_violation` additionally validates the ADR block with `jsonschema` (Draft 2020-12, already in the pinned `test` group; the schema is itself checked with `check_schema`): one good document with a zero-width span passes, and 26 single mutations are refused (unknown key, wrong format/signal/normaliser pattern, `model_source`, provider pattern, threshold with a trailing zero or as a number or above one, alignment unit, negative or float count, a count above 2^53-1, zero tokens, span extra key, edit distance 0, key length 0, 5-digit or trailing-zero or numeric confidence, string boolean, an audio bound without its pair, a missing required key). The cross-field rules of the ADR are batch D (P3-19).

## 8. Fixed numbers and what is not pinned

Pinned by value: the id `mr-Deva/1`; the 18 rule ids; the cost pair; the rank match < substitution < deletion < insertion; the rounding (half to even, 4 digits); 18 fractional digits for a threshold; the machine-key pattern `^[a-z][a-z0-9_]{0,63}$` for a signal name; `ALIGN_MAX_TOKENS >= 2000`; the pinned digests of section 2.4.

Not pinned (the dev chooses and documents): the value of `ALIGN_MAX_TOKENS`; the algorithm of `levenshtein` (only that it is exact and finishes a 400-token span, about 1,900 characters each side, within 60 s); the representation of the DP table; the wording of any message (tests assert only that no input text appears in it); attribute order beyond the dataclass field lists above.

## 9. Test inventory (every SCOPE name, exact)

| Task | Test names | Where |
|---|---|---|
| P3-13 | `test_normaliser_id_is_versioned_and_recorded`, `test_normaliser_mr_fixture_cases` (94 cases), `test_normaliser_never_merges_lla_and_la`, `test_normaliser_folds_devanagari_digits_in_key_only`, `test_normaliser_drops_joiners_in_key_only_display_kept`, `test_normaliser_is_nfc_not_nfkc`, `test_normaliser_rule_change_requires_new_id`, `test_normaliser_refuses_lone_surrogate`, `test_normaliser_bidi_controls_dropped_in_key_only` | `test_normaliser_mr.py` |
| P3-13 extra | `test_normaliser_error_types_are_value_errors_with_a_common_base`, `test_normaliser_fixture_has_all_cases_and_the_rules_are_exercised`, `test_normaliser_key_is_indifferent_to_nfc_and_nfd_spelling` (property), `test_normaliser_refuses_a_lone_surrogate_anywhere_in_the_text` (property), `test_normaliser_digest_pins_are_sensitive_to_the_inputs` | same |
| P3-14 | `test_tokens_carry_code_point_offsets_into_original_text`, `test_token_edges_never_split_a_grapheme_cluster`, `test_latin_tokens_inside_marathi_kept_as_tokens`, `test_numbers_tokenised_whole`, `test_normaliser_control_characters_separate_tokens` | `test_tokens.py` |
| P3-14 extra | `test_token_is_a_frozen_record_of_key_and_half_open_offsets`, `test_tokens_properties_over_mixed_text`, `test_token_edges_coincide_with_cluster_edges_by_construction`, `test_a_control_character_always_separates`, `test_tokeniser_handles_adversarial_text_without_error`, `test_tokenise_refuses_non_text_arguments`, `test_tokenise_turns_concatenates_tokens_in_seq_order_with_global_indices`, `test_tokenise_turns_refuses_turns_out_of_order_and_never_echoes_text`, `test_tokenise_turns_maps_tokens_to_segments_only_when_the_texts_agree`, `test_text_turn_and_token_sequence_are_frozen_and_the_signature_is_pinned` | same |
| P3-15 | `test_alignment_identical_texts_all_matches`, `test_alignment_single_substitution_insertion_deletion`, `test_alignment_tie_break_is_deterministic`, `test_alignment_is_symmetric_in_mer` (property + documented pairs), `test_mer_in_unit_interval_and_exact_counts` (property), `test_alignment_mixed_script_latin_tokens`, `test_alignment_numbers_digits_vs_native_digits`, `test_alignment_empty_side_cases`, `test_alignment_size_cap_typed_error`, `test_alignment_measurement_2000_tokens` | `test_alignment.py` |
| P3-15 extra | `test_alignment_types_are_frozen_and_pinned`, `test_alignment_counts_must_match_the_ops`, `test_alignment_equals_exhaustive_reference_on_every_small_pair`, `test_alignment_ops_are_a_consistent_walk_over_both_sequences`, `test_alignment_is_symmetric_in_mer_on_the_documented_pairs`, `test_mer_counts_are_exact_on_a_hand_computed_pair`, `test_alignment_compares_keys_exactly`, `test_alignment_refuses_non_sequences_of_str` | same |
| P3-16 | `test_spans_are_maximal_non_match_runs`, `test_spans_never_cross_turn_boundaries`, `test_insertion_flag_is_zero_width_at_gap`, `test_span_maps_back_to_exact_export_substring`, `test_span_audio_range_from_stt_segments_or_absent`, `test_spans_only_on_export_never_on_stt` | `test_spans.py` |
| P3-16 extra | `test_span_is_a_frozen_record_with_pinned_fields`, `test_insertions_in_a_split_run_follow_the_nearest_export_token`, `test_spans_refuse_an_insertion_that_has_no_export_token_to_anchor_to`, `test_spans_refuse_an_alignment_that_does_not_belong_to_the_sequences`, `test_spans_equal_the_reference_and_hold_every_invariant` (property), `test_spans_on_a_huge_call_and_on_injection_text` | same |
| P3-17 | `test_flag_threshold_has_no_default`, `test_flag_kept_iff_confidence_at_or_above_threshold`, `test_confidence_in_unit_interval_and_deterministic`, `test_confidence_is_one_for_pure_insertion_or_deletion`, `test_signal_interface_accepts_second_signal_stub`, `test_near_chunk_boundary_marked` | `test_flags.py` |
| P3-17 extra | `test_levenshtein_is_the_unit_cost_distance_over_code_points`, `test_levenshtein_matches_the_reference_and_is_a_metric` (property), `test_levenshtein_is_fast_enough_for_a_span_of_a_few_hundred_tokens`, `test_confidence_record_and_rounding_half_to_even_to_four_digits`, `test_confidence_compares_exactly_never_on_the_rounded_display_value`, `test_confidence_follows_adr_rule_9_on_random_spans` (property), `test_threshold_is_read_as_the_decimal_of_the_shortest_round_trip_spelling`, `test_threshold_is_written_in_the_canonical_form_of_the_record_schema`, `test_threshold_round_trip_through_the_record_text` (property), `test_span_flag_record_is_frozen_with_pinned_fields`, `test_flagged_iff_exact_fraction_at_or_above_threshold_on_random_spans` (property), `test_near_chunk_boundary_refuses_a_span_that_does_not_belong_to_the_sequence`, `test_flags_on_a_large_span_and_with_hostile_text`, `test_a_marathi_pair_end_to_end_with_exact_values`, `test_the_pure_functions_log_and_print_nothing` | same |
| ADR | `test_adr0025_rule_table_matches_normaliser` (+2 extra), `test_adr0023_rules_are_tested`, `test_adr0023_flags_format_matches_json_schema` (+ `test_flags_schema_accepts_the_documented_document_and_refuses_each_violation`, the five `test_every_test_named_by_the_scope_rows_of_batch_c_exists[P3-xx]`, `test_the_batch_c_rows_name_the_exact_tests_of_this_batch`) | `test_adr0025_rule_table.py`, `test_adr0023_recorded_scoring.py` |

## 10. Ambiguities, with recommendations (the lead auto-approves; each is implemented as recommended in the oracle and pinned as stated)

1. Which STT Segment "holds" an STT token (ADR 0023 rule 8 gives no mapping; a `Segment` has text and times but no offsets). **Recommendation (pinned):** tokenise each segment's text with the same normaliser; if the segments' token keys, concatenated, equal the turn's token keys exactly, token i belongs to the segment it came from; otherwise no token of that turn is mapped and every span touching the turn has no audio range. Rejected: locating the segment text inside the turn text by `str.find` (breaks on any whitespace or punctuation difference between the phrase and the body, and would still need a fallback); proportional time interpolation (a fabricated time). Trade-off: if Sarvam's phrase texts do not concatenate to the transcript text, no span gets a range; batch B must make the mapper emit consistent segments, and the P3-31 measurement reports how many spans have one.
2. `near_chunk_boundary` for a deletion span at the very start or end of the STT sequence (gap position 0 or `len`). ADR text: "its gap lies between two STT turns". **Recommendation (pinned):** false at 0 and at `len`; the edges of the recording are not a chunk cut. An empty STT turn between two non-empty ones still separates them.
3. MER with both sides empty. **Recommendation (pinned):** the aligner returns an empty alignment and `.mer` raises `UndefinedMerError(EvalcoreError)` (not a `ValueError`); the scorer's `no_tokens` rule (ADR 0023 rule 15) fires before. Rejected: `0.0` or `1.0` (a silent input would read as perfect agreement or total disagreement), `None` (a value that could be serialised as a result).
4. Alignment API. **Recommendation (pinned):** `align_keys` over plain key lists (testable without a tokenizer and used by the property tests) plus the convenience `align(TokenSequence, TokenSequence)`.
5. Audio range when segments run backwards. **Recommendation (pinned):** absent, not repaired and not sorted (`audio_start_ms <= audio_end_ms` is a cross-field rule of `tracer.flags/1`).
6. Threshold longer than 18 fractional digits. ADR 0023 rule 4 says "a finite number in [0, 1]" and the schema pattern allows at most 18 digits; a threshold such as `1e-30` would pass rule 4 and then fail the record. **Recommendation (pinned):** `parse_threshold` raises `ThresholdError`, so the run stops before any send. Also pinned: strings are refused (a binding value is a JSON number); `-0.0` is not pinned.
7. Where the schema lives. **Recommendation (pinned):** `tracer_evalcore.flags.FLAGS_JSON_SCHEMA` (a Python dict literal, so no package data file is added to the wheel and the batch A wheel-contents test is untouched). If P3-19 introduces a validator module it re-exports the same object; the test imports it from `flags`.
8. Quadratic cost of the confidence. A span holding thousands of tokens has keys of tens of thousands of characters; a plain O(n*m) Levenshtein then takes minutes. ADR 0023 rule 9 sets no cap. **Recommendation:** implement the exact distance with a bit-parallel algorithm (Myers/Hyyro with Python integers) or a banded one; the test only requires 400-token spans (about 1,900 characters) in under 60 s. If the dev adds a named cap with a typed error (a technical limit, not a result), it is a new row in the CHANGELOG and a new test; nothing here forbids it.
9. Cap semantics for the aligner. The tests treat `max_tokens` as a per-side limit and require `ALIGN_MAX_TOKENS >= 2000`. A table of `cap * cap` cells is the memory cost: the oracle's list-of-lists with a cap of 20,000 would need gigabytes, so it is NOT a recommendation. **Recommendation:** measure P3-15 with a compact table (`array('I')` rows or `bytes` of back-pointers) and pick the cap from the measured memory; a quadratic cell budget stated as a second constant is fine as long as one side over `max_tokens` still raises before any allocation (pinned) and the default admits 2,000 x 2,000.
10. Segments in `TextTurn` for the export side are allowed (the type is shared) but unused.
11. `Span.turn_index` is an addition to the ADR's field list (the record of `tracer.flags/1` has only `turn_seq`); it is needed to index `export.turns` and costs nothing. The pipeline drops it when it writes the record.
12. `PENDING` and the parallel batch B. `test_adr0023_rules_are_tested` asserts `not PENDING & defined`. If batch B (P3-06 to P3-11) lands first, its three names (`test_stt_transcript_maps_one_turn_per_nonempty_chunk`, `test_stt_turn_language_is_observed_from_unknown_request`, `test_detected_language_set_only_when_all_chunks_agree`) must be removed from `PENDING` in the same commit that moves them; if batch C lands first nothing is needed. This is a tightening only (the set is smaller), never a weakening.
13. ADR 0025 R14 code point cell is `cat:Nd outside U+0966-U+096F`, which the ADR's own format sentence ("`cat:` followed by a Unicode general category") does not allow literally. **Recommendation:** the test accepts the one documented extension (`cat:<category> outside U+XXXX-U+YYYY`); the ADR sentence should be amended in its next revision (docs only, no id change, because the normaliser behaviour does not change). Not blocking.
14. The digest of the rule table (section 2.4) moves with any edit of the table text, editorial or not. **Recommendation:** accept: a table edit is a rule edit until proven otherwise, and the lead's remedy for a typo fix is to re-pin with a logged reason, never to weaken the test. Alternative if the lead prefers: pin only the (rule id, code point cell, key effect cell) triples (a one-line change of `digest_rule_table`).
15. The behavioural digest covers only code points whose data is stable on Python 3.11 to 3.14 (measured equal on all four). The 3.15 leg (non-blocking) may differ if a newer Unicode release changes a category; that would be a real signal (ADR 0025 Decision 12 records the Unicode version for exactly this reason) and not a reason to widen the corpus.
16. No cap on the tokeniser input (it is linear). **Recommendation:** none; the importers already bound the export size and the aligner bounds the token count.

## 11. Coverage gaps and risks against the phase gate

* Native review: every Indic expectation here comes from the fixture and the ADR, both `needs_native_review` (BL-169); passing the tests is not a Marathi quality claim.
* Timing tests: `test_alignment_measurement_2000_tokens` (120 s alarm, no threshold), `test_levenshtein_is_fast_enough_for_a_span_of_a_few_hundred_tokens` (60 s), `test_tokeniser_handles_adversarial_text_without_error` (30 s), the cap test (5 s) use generous bounds; the SIGALRM guards are POSIX only (as the chunker tests); on Windows a hang is caught by the CI job timeout only. The memory bound of the cap test (4 MiB tracemalloc peak) allocates its inputs before tracing starts.
* Hypothesis tests are derandomised (`derandomize=True`), so a failure reproduces; none writes the example database.
* Not covered here by design: result mapping, `tracer.flags/1` cross-field validation, scorer registry, language gate (batch D); the deny-list wording (batch E); the export size at import (batch A); the real-call measurement (P3-31).
* The existing 10,723 tests are untouched; the oracle run of the whole workspace is reported in the handoff message.

## 12. Evidence

`docs/phase3/evidence/red/batch-c/` (every log is scrubbed of home and scratch paths and under 5,000 lines):

* `red.py3.11.log` to `red.py3.14.log`: the staged tests against the repository without the five modules: 185 collected, 174 failed, each with `ModuleNotFoundError` for the module the test loads (113 `tokens`, 22 `flags`, 18 `align`, 11 `normalise`, 10 `spans`), 11 passed. The 11 are structure guards that read only ADR 0023, ADR 0025, SCOPE and the test files (`test_adr0023_rules_are_tested`, the five `test_every_test_named_by_the_scope_rows_of_batch_c_exists[...]`, `test_the_batch_c_rows_name_the_exact_tests_of_this_batch`, `test_flags_schema_accepts_the_documented_document_and_refuses_each_violation`, `test_adr0025_invariants_cite_tests_that_exist`, `test_adr0025_rule_table_code_points_are_real_where_they_name_a_category`, `test_normaliser_digest_pins_are_sensitive_to_the_inputs`). Of the three named ADR tests, `test_adr0025_rule_table_matches_normaliser` and `test_adr0023_flags_format_matches_json_schema` fail red (they import the new modules); `test_adr0023_rules_are_tested` passes red by design, because it only needs the batch C test names to be defined. `red-reasons.py3.13.txt` lists the counts and the 11 names.
* `oracle.py3.11.log` to `oracle.py3.14.log`: 185 passed against the scratch oracle on each of Python 3.11, 3.12, 3.13 and 3.14.
* `oracle-full-suite.py3.11.log`, `.py3.13.log`, `.py3.14.log`: the whole workspace with the oracle modules and the staged tests in a scratch copy of the repository (private fixtures, `.env.local` and `tools/` not copied; a scratch `git init` without any commit, because five existing contract tests call `git ls-files`): 10,908 passed and 1,287 deselected on each (the 10,723 earlier tests plus the 185 new, none changed).
* `oracle-src.patch`: the five modules and the schema text of the oracle, a minimal implementation written by hand to prove the contract is satisfiable. It is not the product: the dev is free to differ wherever section 8 says "not pinned" (the oracle's aligner table is a list of lists and its cap of 20,000 is not a recommendation, ambiguity 9).
* `mutants.txt`: 114 mutants of the oracle, 113 caught, 1 equivalent survivor (N21), with the test that caught each.
