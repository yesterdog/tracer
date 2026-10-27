# Phase 4 batch C contract: the caller brain (P4-13, P4-14, P4-15)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (rows P4-13, P4-14, P4-15 of A.3, sections B, C, D, the amendments D-a to D-d and E-a/E-b, and D-b: the LLM brain receives each finished agent segment's text, transcribed DURING the call; scripted and replay need none), ADR 0028 (all 12 rules and I1 to I7), ADR 0026 (rules 4, 13 to 15, 22), ADR 0027 (`caller_text_origin` and the `caller` part of the `tracer.sim_profile/1` schema), the Marathi caller character table `mr-caller-charset/1` (V01 to V09) and the fixture `sim_mr.json` of P4-00f, the batch 0b contract `docs/phase4/api-surface-batch-0b.md` (sections 5.3 to 5.5: the engine and brain types this batch builds on), the Phase 3 modules and tests as models, and the lead's instructions of 2026-10-27. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, policy, workflow or earlier test except the one named spec change of section 10. Every statement below is pinned by a test named in section 9, unless it says "not pinned".

## 0. Where the tests are, and how to move them

IMPORTANT (Phase 2 lesson, twice): a directory under `packages/` without a `pyproject.toml` breaks `uv run --locked` for the whole workspace. So nothing was created under `packages/`. Staged at

```text
docs/phase4/evidence/red/batch-c/staging/tests/evalcore/ -> packages/evalcore/tests/   (7 new files, 1 replaced file)
docs/phase4/evidence/red/batch-c/staging/tests/cli/      -> packages/cli/tests/        (1 new file)
docs/phase4/evidence/red/batch-c/staging/data/caller_prompt_1.txt
                                                         -> packages/evalcore/tests/fixtures/caller_prompt_1.txt   (new; the frozen template, section 3.1)
docs/phase4/evidence/red/batch-0/staging/data/sim_mr.json
                                                         -> packages/evalcore/tests/fixtures/sim_mr.json           (staged in batch 0; the batch C tests load it from this final place)
(nothing is staged for evalsim, evalproviders, evalschema or evaldb: see section 1)
```

The eight evalcore files: `evalcore_phase4_batchc_support.py`, `test_caller_line_validator.py`, `test_caller_prompt.py`, `test_llm_brain.py`, `test_scripted_and_replay_brains.py`, `test_caller_injection_suite.py`, `test_caller_brain_purity.py` (all new) and `evalcore_phase4_adr_support.py` (REPLACES the existing file: the named spec change S-C, section 10). The cli file: `test_sim_adversarial_report_store.py` (new). No `__init__.py`, no `conftest.py`. Test module names are unique across all test directories. All files are ASCII (Devanagari and every other non-ASCII character are `\uXXXX` or `\UXXXXXXXX` escapes), pass `ruff check`, `ruff format --check` and `mypy --strict` at their final path (they were formatted there in the oracle), open no socket, read no key, no clock and neither the private fixtures nor any path outside `tmp_path`. The three new modules of `tracer-evalcore` are imported lazily (`importlib`), so before they exist each test fails on its own with `ModuleNotFoundError` and the directory still collects.

The dev's first acts, in this order:

```text
1. git mv docs/phase4/evidence/red/batch-0/staging/data/sim_mr.json        packages/evalcore/tests/fixtures/sim_mr.json
   git mv docs/phase4/evidence/red/batch-c/staging/data/caller_prompt_1.txt packages/evalcore/tests/fixtures/caller_prompt_1.txt
   git mv docs/phase4/evidence/red/batch-c/staging/tests/evalcore/*.py     packages/evalcore/tests/   (replaces evalcore_phase4_adr_support.py)
   git mv docs/phase4/evidence/red/batch-c/staging/tests/cli/*.py          packages/cli/tests/
2. packages/evalcore/src/tracer_evalcore/{callerline,prompt,brains}.py (sections 2 to 6; the oracle's files in oracle-src.patch are a working starting point)
```

Commands (after the move and the modules):

```text
Batch C:     uv run --locked --all-groups pytest packages/evalcore/tests/test_caller_line_validator.py packages/evalcore/tests/test_caller_prompt.py \
               packages/evalcore/tests/test_llm_brain.py packages/evalcore/tests/test_scripted_and_replay_brains.py \
               packages/evalcore/tests/test_caller_injection_suite.py packages/evalcore/tests/test_caller_brain_purity.py \
               packages/cli/tests/test_sim_adversarial_report_store.py
ADR readers: uv run --locked --all-groups pytest packages/evalcore/tests -k "adr0026 or adr0027 or adr0028 or adr0029 or phase4"
everything:  uv run --locked --all-groups pytest
```

## 1. Where the code lives

SCOPE puts "prompt builder and output validator" (and the `CallerBrain` implementations) in `tracer-evalcore`, which is pure: stdlib and `tracer-evalschema`, no network, no clock, no randomness (ADR 0026 rule 2). The replay brain reads files with `os` and `pathlib`; that is allowed there (it opens no socket). Three new modules, each with the two SPDX lines and an `__all__` in the order ruff keeps (`CONSTANTS`, then `Classes`, then `functions`; the tests use `all_order`):

| Module | Holds | Section |
|---|---|---|
| `tracer_evalcore.callerline` | the output validator (`mr-caller-charset/1`, the sentinel, one line, the cap) | 2 |
| `tracer_evalcore.prompt` | the versioned template, the cleaning of the agent's text, the fence, `PromptInputs`, the request messages | 3 |
| `tracer_evalcore.brains` | `ScriptedBrain`, `ReplayBrain`, `LlmBrain`, `ReplayDirectoryError` | 4 to 6 |

`tracer_evalsim` gets nothing in this batch (the call loop that drives a brain is P4-21). `simtypes.py`, `brain.py`, `llm.py`, `tts.py` are unchanged; `BrainDecision` and `CallerBrain` are used as they are. The three modules import only: `__future__`, `ast`-free stdlib (`collections`, `dataclasses`, `hashlib`, `json`, `math`, `os`, `pathlib`, `re`, `stat`, `typing`, `unicodedata`), `tracer_evalcore` and `tracer_evalschema`. They use none of `socket ssl select selectors asyncio http urllib subprocess multiprocessing threading ctypes pickle marshal shelve time datetime random secrets uuid logging warnings sys`, none of the names `environ getenv putenv system popen getpass`, and none of the built-in calls `print input eval exec compile __import__` (`test_brain_modules_import_only_the_allowed_and_name_nothing_banned`, section 8).

## 2. `tracer_evalcore.callerline`: the output validator (ADR 0028 rules 6 and 7)

```text
CHARSET_ID        = "mr-caller-charset/1"                   (equals the id in sim_mr.json)
ALLOWED_LANGUAGES = ("mr",)
REFUSAL_CODES     = ("empty", "multiple_lines", "too_long", "not_nfc", "character_not_allowed", "no_script_letter")
@dataclass(frozen=True) ReplyCheck(kind, line, refusal)
    kind "line":     line a non-empty str, refusal None
    kind "end_call": line None, refusal None
    kind "refused":  line None, refusal in REFUSAL_CODES            (anything else: ValueError)
def check_caller_reply(reply: str, *, language: str, max_line_chars: int) -> ReplyCheck
def require_max_line_chars(value: object) -> int                    (helper; not pinned by name)
```

Rules, in this order:

1. `max_line_chars` is required (no default, keyword-only) and is an `int` (not `bool`) from 1 to 2,500 (`MAX_LINE_CHARS`), else `ValueError`. `language` is required, keyword-only, and must be in `ALLOWED_LANGUAGES`, else `ValueError` (Phase 4 is mr only, SCOPE O9). `reply` that is not a `str` is a `TypeError`. No message quotes the offending value.
2. `stripped = reply.strip()` (Python `str.strip()`: NBSP, U+3000 and U+0085 go; U+200B stays). Nothing else is removed.
3. `stripped == "END_CALL"` gives `kind="end_call"`, whatever the cap.
4. Otherwise, the first rule that fails gives the refusal, in this order: `empty`; `multiple_lines` (a U+000A in the stripped reply); `too_long` (more than `max_line_chars` code points); `not_nfc` (`unicodedata.is_normalized("NFC", ...)` false, V08); `character_not_allowed` (a character not allowed, below); `no_script_letter` (no character in U+0904 to U+0939, V09).
5. A character is allowed when some allow range holds it and no deny hits it. Allow: V01 U+0900-U+097F, V03 U+0030-U+0039, V04 U+0020-U+0022, U+0027-U+0029, U+002C-U+002E, U+003A-U+003B, U+003F. Deny (wins): V02 U+0966-U+0970, V05 Latin letters, V06 U+00AD, U+200B-U+200F, U+202A-U+202E, U+2060-U+2069, U+FEFF, V07 categories Cc, Cf, Cs, Co, Cn. The tests read the table from `sim_mr.json` (`reference_char_allowed`) and compare the code with it on every BMP code point and a stride of the other planes.
6. An accepted line is the stripped reply, unchanged (no NFC, no trimming of interior space, no truncation). A refusal holds a code, never the text; `repr` and `asdict` of a verdict never contain the reply.

Pinned by `test_caller_output_length_cap_required`, `test_validator_agrees_with_the_fixture_table_on_every_bmp_code_point_and_astral_samples` and the rule tests of `test_caller_line_validator.py` (V02, V05, V06, V07, V08, V09, the sentinel, one line, no repair, precedence, no quoting, the language, the record).

## 3. `tracer_evalcore.prompt`: the template, the cleaning and the messages (ADR 0028 rules 2 to 5)

```text
TEMPLATE_IDS      = ("tracer.caller_prompt/1",)         TEMPLATE_ID = TEMPLATE_IDS[-1]
FENCE_OPEN        = "<<<UNTRUSTED_AGENT_TEXT_BEGIN>>>"
FENCE_CLOSE       = "<<<UNTRUSTED_AGENT_TEXT_END>>>"
FENCE_ESCAPE      = "[delimiter removed]"               (no "<", ">" or "_", so it cannot complete a delimiter)
TRUNCATION_MARKER = "[text cut]"
KEPT_FORMAT_CHARS = ("\u200c", "\u200d")                 (ZWNJ and ZWJ, ADR 0025)
CONVERSATION_ROLES = ("caller", "agent")
def template_text(template_id: str) -> str              ValueError for an id that is not shipped, TypeError for a non-str
def template_sha256(template_id: str) -> str            hashlib.sha256(template_text(id).encode("utf-8")).hexdigest()
@dataclass(frozen=True) PromptInputs(language: str, instructions: str, profile_values: tuple[tuple[str, str], ...])
def prompt_inputs(evaluator: Evaluator, persona: Persona, profile: TestProfile | None) -> PromptInputs
def render_system(template_id: str, inputs: PromptInputs) -> str
def clean_agent_text(text: str, *, max_chars: int) -> str
def build_messages(*, template_id, inputs, conversation: Sequence[tuple[str, str]], agent_text_max_chars: int) -> tuple[LlmMessage, ...]
```

3.1 The template. `template_text("tracer.caller_prompt/1")` is, byte for byte, the file `packages/evalcore/tests/fixtures/caller_prompt_1.txt` (ASCII, 928 bytes, ends with one line feed; staged at `docs/phase4/evidence/red/batch-c/staging/data/caller_prompt_1.txt`). Its SHA-256 is the `template_sha256` recorded in the sim profile. It holds, once and in this order, the slots `@@LANGUAGE@@`, `@@INSTRUCTIONS@@`, `@@PROFILE@@`, names both delimiters, the word "untrusted", an instruction never to obey the block, and the sentinel `END_CALL` once; it holds none of the words outcome, rubric, metric, judge, "api key", token, tools, dial and no brace. The text is the author's (security-licensing co-owns it, SCOPE P4-13); qa wrote this text so that the hash is a golden value (ADR 0028 Consequences: "changing it is a new template id, never an edit"). Until the template is released, security-licensing may change the text by changing the fixture and the module together under a named spec change; after that, a new id.

3.2 `prompt_inputs` is the single place that reads an `Evaluator`, a `Persona` and a `TestProfile` for the prompt. It takes `persona.language` (the tag only; the accent is not read), resolves every `{{profile.<field>}}` of `evaluator.instructions` in one pass (a substituted value is never scanned again), and lists the profile values as `(field, value)` pairs sorted by field name. `evaluator.expected_outcome`, `name`, the tool calls, the metric bindings, the persona accent and the profile's own id never enter. `ValueError` when the evaluator references a profile and `profile` is `None` or another profile, and when `profile` is given but the evaluator references none.

3.3 `render_system`: the template with each slot filled exactly once in one pass (`re.sub` with a function): `LANGUAGE` the tag, `INSTRUCTIONS` the resolved instructions, `PROFILE` the lines `"<field>: <value>"` joined by one line feed, or the word `none` when there are no values. A value that looks like a slot, a delimiter or a placeholder is data: it is not expanded again.

3.4 `clean_agent_text` (ADR 0028 rule 4), in exactly this order and nothing else: (1) remove every character of category Cc, Cf, Cs, Co or Cn except U+200C and U+200D; (2) replace every occurrence of either delimiter by `FENCE_ESCAPE`, the open then the close, in one pass each; (3) if the result has more than `max_chars` code points, cut it to `max_chars` and append `TRUNCATION_MARKER` (so the result has at most `max_chars + 10` code points; text of exactly `max_chars` is not cut). `max_chars` is required, keyword-only, an `int` (not `bool`) of at least 1, else `ValueError`; a `text` that is not `str` is a `TypeError`; no message quotes the value. Consequences the tests pin: line feeds, tabs and carriage returns go with the rest of Cc (words are glued, ambiguity A2); U+2028, U+2029, no-break space and U+3000 stay; a delimiter split by an invisible character is still replaced; a delimiter broken by a joiner is not a delimiter and is left; cleaning twice equals cleaning once; no delimiter survives any composition of fragments (400 seeded random compositions).

3.5 `build_messages`: the system message `render_system(...)`, then the conversation in order: `("agent", raw_text)` becomes a `user` message whose content is exactly `FENCE_OPEN + "\n" + clean_agent_text(raw_text, max_chars=cap) + "\n" + FENCE_CLOSE` (so a block has exactly two line feeds; an empty text gives `OPEN\n\nCLOSE`, never invented text); `("caller", line)` becomes an `assistant` message with the line as it was said. Any other role, a pair of another length, an empty caller line: `ValueError`; a non-str text: `TypeError`; `agent_text_max_chars` required, keyword-only, an `int` of at least 1. The parameter set of `build_messages`, `prompt_inputs` and `render_system` and the fields of `PromptInputs` contain no name with outcome, rubric, metric, key, token, target, allow, path or auth in it: there is nowhere to put a forbidden item (ADR 0028 rule 3).

3.6 Nothing of a request is built from anything else (rule 2): the language tag, the instructions, the profile values, the caller's own lines, the fenced agent texts, the fixed template. The order is the template's.

## 4. `tracer_evalcore.brains.LlmBrain` (ADR 0028 rules 5, 6, 7, 10)

```text
class LlmBrain:   mode = "llm"
    def __init__(self, *, engine, context: LlmContext, on_usage: UsageSink, provider: str, model: str, max_tokens: int,
                 seed: int | None, reasoning_effort: str | None, max_line_chars: int, max_turns: int,
                 template_id: str, agent_text_max_chars: int, inputs: PromptInputs) -> None
    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision
    def profile_block(self) -> dict[str, Any]
```

4.1 Construction. All 13 parameters are keyword-only and none has a default. Refused with `ValueError` (or `TypeError` for a wrong type of `engine`, `on_usage`, `inputs`): a `provider` or `model` outside `[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}`, `max_tokens` outside 1 to 2**53-1 (`bool` included; 2**53-1 itself is accepted), a `seed` that is not an int in 0 to 2**53-1 or `None`, a `reasoning_effort` outside `[a-z][a-z0-9_]{0,31}` or `None` (these are the rules of `LlmRequest`), `max_line_chars` outside 1 to 2,500, `max_turns` or `agent_text_max_chars` below 1 or above 2**53-1 (I-JSON, ADR 0027; or a `bool`), a `context` that is not an `LlmContext` (`TypeError`), a `template_id` that is not shipped, `on_usage` not callable, `engine` without a `complete` method, `inputs` not a `PromptInputs`, and `inputs.language` with no allowed character set (not `mr`). Construction sends nothing. Two brains share no state.

4.2 `next_line(turn_index, agent_text)`. `agent_text` is `None` (no finished agent segment before this turn) or the text of the last finished agent segment, raw or already cleaned: the brain always cleans it (3.4 is idempotent). The brain is sequential: `turn_index` must be the next turn, starting at 0, else `ValueError`; after any end decision, and after it raised, every call is a `ValueError`; a call whose `agent_text` is neither `None` nor `str` is a `TypeError` that changes no state. In order:

- `turn_index >= max_turns`: `BrainDecision(None, "max_turns", 0, 0)`, no request.
- Build the messages (3.5) from the conversation so far plus `("agent", agent_text)` when it is not `None`.
- Send an `LlmRequest(index=<count of requests so far, from 0>, messages, model, max_tokens, seed, reasoning_effort)` through `complete_once(engine, request, context, on_usage=on_usage)`: the usage of each answered request reaches `on_usage` before the next request is built, for a plain engine and a streaming one, and the same `context` object is passed every time. There is no `tools`, `stream` or other parameter: `LlmRequest` has none, and the six fields are exactly what is sent.
- An engine failure that is an `LlmCapReachedError` ends the call: `BrainDecision(None, "spend_cap", invalid, regenerations)`. Any other failure is raised as it is (the same object, outside any handler, so it carries no cause and no context), and the brain is ended.
- A reply is checked with `check_caller_reply(reply, language=inputs.language, max_line_chars=...)`. `end_call`: `BrainDecision(None, "caller_end_call", invalid, regenerations)`. `line`: `BrainDecision(line, None, invalid, regenerations)`, and the line joins the conversation as the caller's. `refused`: `invalid += 1` and the same messages are sent once more (a second request with `index + 1` and every other field equal); a second refusal gives `BrainDecision(None, "brain_invalid_output", 2, 1)`. The counts are of this decision: `(0, 0)`, `(1, 1)` (one refusal, then an accepted line or the sentinel) or `(2, 1)`; a regeneration that the cap kept from being sent is not counted (`(1, 0)` with `spend_cap`). The sentinel is accepted on the regeneration.
- The text of a refused reply is never kept: not in the next request, not in the brain's state, not in a decision, not in an exception, not printed, not logged. A call has at most `max_turns` requests plus one regeneration each.

4.3 `profile_block()` returns exactly the `llm` member of the `caller` part of `tracer.sim_profile/1` (ADR 0027): `mode`, `provider`, `model`, `max_tokens`, `seed`, `reasoning_effort`, `max_line_chars`, `max_turns`, `max_regenerations` (always 1), `end_sentinel` (`"END_CALL"`) and `prompt{template_id, template_sha256, agent_text_max_chars}`; it validates against the JSON Schema printed in ADR 0027 (also with every integer at 2**53-1) and holds no text of the run; each call returns a new dict, so changing it changes nothing in the brain. The seed is sent as given on every request and recorded; the module says nothing about determinism (the provider promises none).

4.4 `repr(brain)` and `str(brain)` contain none of the instructions, the profile values or any canary; the brain has `__dict__` (the tests walk it). The brain writes no log, prints nothing, reads no environment variable.

## 5. `tracer_evalcore.brains.ScriptedBrain` (ADR 0028 rule 1, I1)

```text
class ScriptedBrain:  mode = "scripted"
    def __init__(self, scripted_turns: Sequence[ScriptedTurn]) -> None
    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision
    def profile_block(self) -> dict[str, Any]          == {"mode": "scripted"}
```

The single parameter is the script: the brain has no engine, no context and no sink, and its source names none of them. The brain keeps its own copy of the script. Construction refuses (`TypeError`) anything that is not a sequence of `ScriptedTurn`, and (`ValueError`) turns whose `index` values are not exactly `0, 1, 2, ...` in order or whose text is longer than 2,500 characters (2,500 is accepted). `next_line` is stateless and random-access: `BrainDecision(turns[n].text, None, 0, 0)` for `n < len`, `BrainDecision(None, "script_complete", 0, 0)` for `n >= len` (also for an empty script), whatever `agent_text` is (it is ignored, never read). `turn_index` that is not an `int` at least 0 (a `bool` is not) is a `ValueError`. The scripted lines are the evaluator's own and are not judged by the output validator.

## 6. `tracer_evalcore.brains.ReplayBrain` (ADR 0028 rule 1, I1; ADR 0026 rule 14 step 1)

```text
REPLAY_FILE_NAME      = "turn-{:04d}.json"       REPLAY_FILE_MAX_BYTES = 65536
class ReplayDirectoryError(EvalcoreError, ValueError):   keyword-only (turn: int | None, code: str); attributes turn, code
class ReplayBrain:  mode = "replay"
    def __init__(self, directory: Path | str, *, language: str, max_turns: int, max_line_chars: int) -> None
    def next_line(self, turn_index: int, agent_text: str | None) -> BrainDecision
    def profile_block(self) -> dict[str, Any]   == {"mode": "replay", "max_line_chars": ..., "max_turns": ..., "end_sentinel": "END_CALL"}
```

6.1 The file. `turn-NNNN.json` (four digits, from `0001`; the file of turn N is read for `turn_index` N-1) is one JSON object with exactly the key `"line"` and a string value (strict JSON: no `NaN`, no `Infinity`, no duplicate key, UTF-8, at most `REPLAY_FILE_MAX_BYTES` bytes, a regular file that is not a link). Other names in the directory and files beyond `max_turns` are ignored.

6.2 Step 1. The constructor reads the directory once, in order, from `turn-0001.json` up to `turn-<max_turns>.json`, or up to and including the first file whose line, stripped, is `END_CALL`: the call cannot need a file after the sentinel, so that file and the ones after it are neither required nor read. A refusal is `ReplayDirectoryError(turn=<N or None>, code=...)`, raised when the brain is built, before any key is read, before any socket is opened (the tests record every read of the environment and make `socket.socket()` fail while the brain is built). Codes: `directory_missing` (turn None; the path does not exist or is not a directory; a directory reached through a link is fine), `missing`, `not_a_regular_file` (a link, a directory, any other non-regular file), `too_large`, `unreadable`, `not_json` (not UTF-8, not JSON, `NaN`, too deeply nested), `bad_shape` (not an object, a key other than `line`, extra keys, a duplicate key, a value that is not a string). The message is `ReplayDirectoryError: <turn-NNNN.json | replay directory>: <code>`: never the directory, never a path, never a line; `__cause__` is None. The validity of a line (its script) is not checked at step 1 (the ADR says "exist and parse"); the contents are kept in memory, so nothing touches the file system after step 1 (the tests remove the directory after construction).

6.3 `next_line`: random-access and stateless. `turn_index >= max_turns` gives `BrainDecision(None, "max_turns", 0, 0)`; an index beyond the files read (after the sentinel) is a `ValueError`; otherwise the file's line goes through `check_caller_reply` with the brain's `language` and `max_line_chars`: a line is returned as the validator returns it (stripped), the sentinel gives `caller_end_call`, and a refusal gives `BrainDecision(None, "brain_invalid_output", 1, 0)` (no regeneration: a replayed line that fails is refused). `agent_text` is ignored. Constructor parameters are validated as in 4.1 (`language` must have an allowed set; `max_turns` is at most 2**53-1, and a replay that ends with the sentinel in its first file builds with that cap).

## 7. The adversarial injection suite (P4-14; ADR 0028 rules 3, 4, 6 to 8, 11)

`test_caller_injection_suite.py` drives a fake agent through `drive()`, which does only what the call loop does with a brain (ask for a line, send it to a recording TTS, stop on an end decision), and a `Dialer` that is called once with the configured target before the first brain call. Corpora in `evalcore_phase4_batchc_support.py`: `HOSTILE_AGENT_TEXTS` (38 texts: ignore previous instructions; ask for the system prompt, the expected outcome, the rubric, the key; dictate a number, a URL and a path; ask for the sentinel; role reversal and forged role lines; chat markup; tool-call and function-call syntax; a code fence; HTML, script and markdown; the fence delimiters, also split by a zero-width space or a bidi control; the template slots and ids; language switches to Hindi and Arabic/Chinese; bidi controls, zero-width characters, NUL and other controls; a line-feed storm; lone surrogates; private and unassigned code points; line separators; 10,000 and 1,000,000 characters; empty and white space; joiners) and `hostile_replies()` (a model's replies, 36 of them: English, an echo of the system prompt or of the last agent block, the sentinel in a sentence, in lower case, with a period, twice, fullwidth, with a zero-width space, quoted, tool-call JSON, a tag, a call, two lines, CRLF, Latin inside, native digits, the abbreviation sign, markup, markdown, backticks, joiners, bidi, NUL, tab, no-break space, digits only, punctuation only, a danda only, U+0958, empty, white space, 1,000,000 characters, one over the cap). What each named test proves:

- `test_injection_from_agent_never_changes_dial_target_or_tools`: through a whole call over all 38 texts the dialer is called exactly once with the configured target; no decision ends the call; TTS receives exactly the model's lines; the request records have exactly the six fields and a canonical body without `tools`, `tool_choice`, `functions`, `function_call` or `stream`; the dictated target, path and number are inside fenced `user` blocks and nowhere in a system or assistant message, a TTS text or a decision.
- `test_injection_cannot_leak_expected_outcome_or_key_canary`: canaries in the expected outcome, the evaluator's name and tool-call arguments, the persona's accent, the environment key (`SARVAM_API_KEY`), an authorisation-reference variable and a path; a hostile agent that asks for each; a model that returns every hostile reply (36 calls, each refused twice) and a well-behaved model under the whole corpus; none of the canaries is in any request (text and canonical body), any TTS text, any decision, `repr(brain)`, `str(brain)`, the profile document, any error text (bad types, bad configuration, a replay directory named with a canary), the log or the streams; the instructions canary (configured text) is only in the system message.
- `test_adversarial_agent_text_never_breaks_report_or_store`: in the CLI directory, section 7.1.

Extra tests (reason in each docstring): the agent saying the sentinel early or late ends nothing; the model saying it on the last permitted turn ends with `caller_end_call` and the next turn is `max_turns`; a model that obeys every injection is stopped by the validator and the key it was tricked into saying is nowhere; a dictated number that the model itself speaks inside a valid Devanagari line is said to the agent, not dialled (ambiguity A3); template slots in the instructions, the profile and the agent text change nothing (one-pass rendering); roles of a hostile call are strictly system, then user/assistant alternating, with only the brain's own lines as assistant; a language switch by the agent leaves the prompt language alone; 60 turns of 10,000 characters and a 1,000,000-character text stay capped.

7.1 The report and store clause. Batch C has no simulated run to store (P4-21 to P4-24), so `test_adversarial_agent_text_never_breaks_report_or_store` (in `packages/cli/tests/test_sim_adversarial_report_store.py`, 16 cases) sends each hostile text through the one path that stores speech-to-text text today, the Phase 3 `score` command with a replayed Sarvam body (`--stt fake:<dir>`): exit 0, 2, 3 or 4; the streams echo none of it; for 0 and 2 the report keeps every structure rule of the Phase 3 suite, the store reads back, and the stored `our_stt` text contains the text unchanged (ADR 0017); then the text as stored is handed to `build_messages` and comes out as one cleaned, capped, fenced block. This half passes on the current tree except for the prompt module, which is what makes it red. When P4-21 lands the lead files a BACKLOG row to re-point the first half at a simulated bundle (the second half stays); ADR 0026 rule 22 and ADR 0028 I6 cite the same name.

## 8. Purity and reproducibility

`test_caller_brain_purity.py`: the import and name rules of section 1 (AST); the three modules never mention `tracer_evalproviders`, `tracer_evalsim`, `tracer_cli`, `tracer_evaldb` or `httpx`; with `time.time/monotonic/perf_counter/process_time/monotonic_ns/sleep`, `random.*`, `secrets.token_*/randbelow/choice`, `uuid.uuid1/uuid4`, `os.getenv/getpid` made to fail the test and `os.environ` replaced by a recording view, all three brains play the three fixture scenarios (scripted, replay over files written from the scenario lines, LLM over a fake engine returning them and the agent replies of the fixture) and a hostile 38-turn LLM call, with the same decisions as unrigged and zero environment reads; two plays are identical; request bodies of identical inputs are byte-identical; an LLM run with seed 1234 sends 1234 on every request, records it, and the module text claims no determinism; the brains print nothing, log nothing and write no file.

## 9. Test inventory

9.1 SCOPE names (EXACTLY the 10 names of the rows P4-13 to P4-15: 5 + 3 + 2):

| Task | Name | File |
|---|---|---|
| P4-13 | `test_expected_outcome_never_in_any_llm_request` | `packages/evalcore/tests/test_caller_prompt.py` |
| P4-13 | `test_agent_text_only_inside_fenced_data_block` | `packages/evalcore/tests/test_caller_prompt.py` |
| P4-13 | `test_caller_output_outside_persona_script_refused_once_then_ends` | `packages/evalcore/tests/test_llm_brain.py` |
| P4-13 | `test_caller_output_length_cap_required` | `packages/evalcore/tests/test_caller_line_validator.py` |
| P4-13 | `test_prompt_template_hash_recorded_in_sim_profile` | `packages/evalcore/tests/test_caller_prompt.py` |
| P4-14 | `test_injection_from_agent_never_changes_dial_target_or_tools` | `packages/evalcore/tests/test_caller_injection_suite.py` |
| P4-14 | `test_injection_cannot_leak_expected_outcome_or_key_canary` | `packages/evalcore/tests/test_caller_injection_suite.py` |
| P4-14 | `test_adversarial_agent_text_never_breaks_report_or_store` (parametrised, 16 cases) | `packages/cli/tests/test_sim_adversarial_report_store.py` |
| P4-15 | `test_scripted_mode_makes_no_llm_request` | `packages/evalcore/tests/test_scripted_and_replay_brains.py` |
| P4-15 | `test_replay_brain_missing_file_is_a_refusal_before_connect` | `packages/evalcore/tests/test_scripted_and_replay_brains.py` |

9.2 Added by qa (each with its reason in the docstring): the other tests of the five evalcore test files (module constants; the template's frozen text, hash and slots; `prompt_inputs`; one-pass rendering; closed builder signature; cleaning rules, reference, delimiter order, cut and properties; messages and conversation; every hostile agent text as one block; byte-identical bodies; the construction of `LlmBrain`; the normal call, the first turn, the empty block, the sentinel; regeneration for the whole hostile corpus; the cap of the brain; no storing, logging or printing of a refused reply; each engine failure and the spend cap; `max_turns`; turn indexes; agent-text type; usage before the next request; a raising sink; the profile block and its schema; the scripted and replay brain behaviours; 18 replay-file shapes; the size limit; links and directories; the injection extras of section 7; purity). One test is a guard that holds on any tree: `test_the_reference_oracle_itself_has_teeth`.

## 10. Named spec change (extending only; before and after in `spec-change.diff`)

```text
S-C  packages/evalcore/tests/evalcore_phase4_adr_support.py : the pending sets shrink, deletions only (24 lines: 16 names of 10 tests, and 8 comment headers of sets that no longer list the task)
     PENDING["0026"] 57 -> 54: test_adversarial_agent_text_never_breaks_report_or_store, test_replay_brain_missing_file_is_a_refusal_before_connect, test_scripted_mode_makes_no_llm_request
     PENDING["0027"] 41 -> 39: test_prompt_template_hash_recorded_in_sim_profile, test_scripted_mode_makes_no_llm_request
     PENDING["0028"] 18 -> 8:  the ten names of 9.1
     PENDING["0029"] 14 -> 13: test_injection_from_agent_never_changes_dial_target_or_tools
     (the dev deletes the same lines in the same commit that moves the tests; a name that is both pending and defined is a failure of check_adr)
```

No existing test was skipped, deleted or weakened.

## 11. Evidence

`docs/phase4/evidence/red/batch-c/`: see `README.txt` there (the files, the counts per Python version, the mutant table, the commands). In short: the 200 new test items (16 + 6 + 11 + 36 + 62 + 29 + 40 over the seven files) are red on the current tree on Python 3.11, 3.12, 3.13 (twice) and 3.14 (199 fail with `ModuleNotFoundError` for `callerline`, `prompt` or `brains`; 1 guard passes), and green on the oracle together with the 191 ADR reader tests (391 passed, 1 skipped, the opt-in gate); the oracle passes the whole workspace on each version (13160 passed, 1 skipped, 1288 deselected, 0 failed); the mutant table lists every mutant of the oracle and the ones that are equivalent.

## 12. Ambiguities, with recommendations (the lead auto-approves)

A1. **Who cleans the agent's text.** Batch 0b section 5.5 says `agent_text` "is the cleaned text"; ADR 0028 rule 4 says the brain cleans before insertion. Recommendation (pinned): the brain always cleans, the call loop may pass the raw speech-to-text text, and cleaning is idempotent. Rejected: trusting the caller of the brain.
A2. **Line feeds are removed, so words are glued.** ADR 0028 rule 4 removes every character of Cc, and U+000A, U+0009 and U+000D are Cc: "a\nb" becomes "ab"; U+2028, U+2029 and U+00A0 stay (not in the listed categories) and could fake a line break for a model. Pinned literally. Recommendation for a later amendment: replace U+0009, U+000A and U+000D (and U+0085, U+2028, U+2029) by one space instead of removing them; this keeps the fence and the "two line feeds per block" property and gives the model readable words; three assertions of `test_caller_prompt.py` change. Not applied without an ADR change.
A3. **A number dictated by the agent can still be spoken.** SCOPE P4-14 says no agent-dictated number reaches TTS. The table allows ASCII digits (V03: account numbers are legitimate), so a model that repeats a number in a valid Devanagari line has spoken it; the validator cannot tell it from a legitimate one and never repairs. What holds, and is pinned: the brain adds nothing of the agent's text to a line, TTS receives exactly the model's lines, and the dialer is untouched. Recommendation: keep; reword the acceptance as "never reaches a dial action; the brain composes no line".
A4. **Which replay files the call "could need".** ADR 0028 rule 1: "every file the call could need (up to `max_turns`)". Pinned reading: files 1 to `max_turns`, or 1 to the first file that holds the sentinel (a call cannot go on after the sentinel). A strict reading would force padding files after the sentinel. A line that fails the validator is not caught at step 1 (the ADR says "exist and parse") but at call time with no regeneration.
A5. **The replay file format is not in the ADR.** Pinned: `{"line": "<text>"}`, the key `line` alone, strict JSON, 65,536 bytes at most, regular files only (links refused, as the cache files of ADR 0024 A9), duplicate keys refused. BL-296 (the command that writes the files from a stored run) must write this shape. Security-licensing may veto the link refusal.
A6. **Caller lines cannot hold ZWJ or ZWNJ.** V06 denies U+200C and U+200D (deny wins over V01), while Marathi spelling and ADR 0025 keep them in the agent's text and the normaliser. A model that writes a conjunct with a joiner is refused. Recommendation: keep for Phase 4 (indic-linguistics' judgment call), file a BL row asking IL whether a narrow allowance (a joiner only between Devanagari characters) is wanted.
A7. **Redundant rules in the table.** The deny rules V05 (Latin) and V06 (invisible characters) and the categories of V07 cannot change a verdict today: no allow range holds a Latin letter, a Cf, Cc, Co or Cs character, and the Devanagari block holds no unassigned code point in the Unicode versions of Python 3.11 to 3.14. Only V02 (U+0966-U+0970 inside the block) removes anything the allow ranges grant. The tests pin the table (every BMP code point) and name V02 and V05 as the rules asked for; mutants that remove V05, V06 or V07 are equivalent and are listed as such. Harmless; no change recommended.
A8. **Unicode version drift.** `clean_agent_text` removes code points that `unicodedata` calls Cn; a code point assigned in Unicode 16 (Python 3.14) is kept there and removed on Python 3.11, so request bodies of a replayed run could differ between Python versions for such a character. The tests use only stable code points (U+0378, U+FFFE, U+10FFFF, U+E000). Recommendation: state it in the timing/reproducibility documentation; no change.
A9. **The regeneration request.** "The same request again": messages, model, `max_tokens`, `seed` and `reasoning_effort` are equal and `index` is the next index (the call log needs distinct request ids). With a fixed seed a provider that honoured it would return the same reply; the provider documents no such promise.
A10. **Scripted is stateless, the LLM brain is sequential.** A scripted or replay brain answers any index at any time; the LLM brain holds a conversation and refuses a turn that is not the next. After an end decision or an engine failure the LLM brain refuses further calls (`ValueError`).
A11. **`agent_text` for the first turn.** `None` when the agent has not spoken (the request then holds only the system message; some chat APIs may require a `user` message: not verified for Sarvam, P4-09's adapter decides). The scripted-first case of `caller_speaks_first` is the call loop's.
A12. **The report and store clause of P4-14** is exercised through the Phase 3 path (section 7.1); BACKLOG row to re-point at P4-21, filed by the lead.
A13. **Template ownership.** The template text is qa's draft so that its hash is a golden value; security-licensing and the dev may improve it before the first release (section 3.1).
A14. **`provider`.** `LlmBrain` takes a `provider` label only to fill `profile_block()` (the schema requires it); the adapter in P4-09 is the real owner of the label.
A15. **Canary names.** `test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape` reads every module-level assignment to `KEY_CANARY` or `CANARY_KEY` in `packages/*/tests/*.py` and pins its value to `CANARYKEY-<6 to 8 characters>-<6 characters>`. The batch C canaries are made-up words with another prefix (`CANARYQA-...`) and the key canary is named `CANARY_KEY_VALUE`, so that the scan neither reads it nor widens the allowlist of `.gitleaks.toml`. Found by the full-suite run on the oracle; a later batch that wants a key canary of the Phase 3 shape can use those names.
A16. **Tests that read the repository's git state** (`test_gitignore_*`, `test_f8_*`, `test_private_fixtures_gitignored`, `test_spdx_headers_present_on_all_sources`, `test_uv_lock_committed_and_sync_locked`) need a git repository: the mirror of the oracle runs is a `git archive` of the commit with the staged files `git add`ed, as the dev's tree will be.
