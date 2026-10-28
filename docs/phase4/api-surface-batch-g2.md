<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# Phase 4 batch G2: the fixes for the security audit P4-32 / P4-J26 (BL-352, BL-353, BL-354, BL-355, BL-339, BL-362)

Status: red-first contract from `qa`, written against HEAD 5c8c18a. Source: the audit delivered on
2026-10-28 (decision log entry "SECURITY AUDIT P4-32 + P4-J26 DELIVERED") and the BACKLOG rows
BL-339, BL-352, BL-353, BL-354, BL-355 and BL-362. Every claim of the audit was checked in the code
before a test was written (section 8 lists what was confirmed and where the audit is imprecise). The
tests are named after the finding id in their docstrings (`BL-352 / A-F3`, ...), and each docstring
carries the section number of this file. The oracle (the minimal implementation the tests were proved
against) is `oracle-src.patch` next to the evidence; the hunks that touch `runner.py` are in
`oracle-src-runner.patch` (section 0). They are a reference for the dev, not code to copy blindly.

No test sends a request, opens a socket (no `loopback` marker is used) or holds a key: canaries are
built from parts, the clock is virtual, the providers are fakes or `httpx.MockTransport`.

## 0. Where the tests are, and how to move them

Staged under `docs/phase4/evidence/red/batch-g2/staging/tests/<package>/`; no `__init__.py`; the dev
does not edit them. The dev's first acts, in order:

1. Copy each staged file to `packages/<package>/tests/` (same name). There is no new support module:
   the new tests import the support modules that exist (`evalsim_support_*`, `cli_p4e_support`,
   `cli_p4j2_support`, `evalproviders_p4j_support`, `evalcore_p4j_support`).
2. Apply `spec-change.diff` (section 7): exact-string edits to four existing tests in three files and
   nothing else. It does not touch `evalcore_phase4_adr_support.py`, `simcalllog.py`, `simsummary.py` or the
   end-reason pins that batch G (min-interval, kill switch, `brain_failed`) edits.
3. Apply `oracle-src.patch` as a reference, or implement from sections 1 to 6. The patch adds ONE new
   module (`tracer_evalcore/privatefile.py`, section 4.3) and no dependency (`uv.lock` unchanged).
4. `runner.py` is also edited by batch G. The G2 hunks are in `oracle-src-runner.patch`, kept apart
   and minimal (40 changed lines): the import line; three new attributes in `_Run.__init__`
   (`audio_cap`, `watchdog`, `released`); one comparison in `_on_event`; in `run()` a `try/finally`
   around the one line `ended = await self._call(connect_ended)`; in `_call` the line
   `self.watchdog = watchdog` and the body of its existing `finally` replaced by
   `await self._release()`; and the new method `_release`. They do not touch `_turns`, `_send_line`,
   the counts or the result, which is where `brain_failed` lands.
5. Run `uv sync --locked --all-groups && uv run --locked pytest -q`.

Files (9 staged test files, no support module):

| Package | File | Tests | Finding |
|---|---|---|---|
| evalsim | `test_evalsim_audit_hostile_json.py` | 29 | BL-352 / A-F3 |
| evalsim | `test_evalsim_audit_audio_bounds.py` | 39 | BL-353 / A-F4 |
| evalsim | `test_evalsim_audit_auth_env_reserved.py` | 5 | BL-354 / A-F5 (target side) |
| evalsim | `test_evalsim_audit_allowlist_reader.py` | 23 | BL-355 / A-F6 (overlaps batch G section 6, see 4.5) |
| evalsim | `test_evalsim_audit_task_hygiene.py` | 21 | BL-339 / A-F7 |
| evalcore | `test_audit_private_file_reader.py` | 28 | BL-355 / A-F6 (the shared reader) |
| evalcore | `test_judge_prompt_turn_separator.py` | 18 | BL-362 / J-F1 |
| evalproviders | `test_judge_hosts_key_routing.py` | 18 | BL-354 / A-F5 (judge side), BL-355 |
| cli | `test_cli_audit_key_routing.py` | 9 | BL-354 / A-F5, BL-355 (the commands) |

190 new tests in 9 files; with the 15 tests of the three edited files (section 7) the targeted set is
205. Whole workspace with the oracle: 15,445 passed (the 15,255 of HEAD plus 190).

## 1. BL-352 / A-F3: a hostile nested message is an ignored message

`json.loads("[" * 60000)` raises `RecursionError` (a `RuntimeError`, not a `ValueError`); the message
is 60 KB, inside the 64 KiB cap. `TwilioMediaStreamDialect.decode` caught only `ValueError`, so the
error left `InboundReader.feed`, the reader task and `run_call`: the run ended with exit 3 and the
recording and the bundle were lost.

1.1 `TwilioMediaStreamDialect.decode(message)` returns `None` (an ignored message) when `json.loads`
raises ANY `Exception` (a `RecursionError`, a `MemoryError`, an error of a future parser). The
state of the dialect object is not changed by it: the next message decodes normally. Nested objects
(`'{"a":' * 12000`), nested arrays, a message exactly at the cap (65536 `[`), and `{"event":"media",
"media":` followed by 60000 `[` are the pinned shapes; well-formed deep arrays at any depth are
`None`, as before (a list is not an object).

1.2 A `BaseException` (`KeyboardInterrupt`, `SystemExit`, `asyncio.CancelledError`) is never
swallowed by `decode`.

1.3 Through `InboundReader.feed` the message counts as one message in the rate window and one in
`stats.ignored`, `feed` returns `None`, and the call goes on: end to end through `run_call` with a
fake agent the result has the same end reason as the same call without the message
(`script_complete`, or `agent_hangup` when the agent then closes), `failure_code is None`,
`counts["inbound_messages_ignored"]` is the number of hostile messages, `frames_received` and the
arrival instants of the audio around the message are unchanged.

1.4 The other readers of agent-controlled input, checked and pinned:

* `pipecat-protobuf/1`: the hand-written codec is flat (no nested message is parsed; a group tag is
  refused). Hostile shapes (60000 start-group tags, 30000 empty members, a varint that never ends)
  give `None` or a stated `TransportError`, never another exception class, and a call that receives
  them goes on. Green on the current tree; kept as a regression guard.
* `smallwebrtc/1` data frames have no parser: raw PCM by length. Same guard.
* the signalling answer of `smallwebrtc/1` (`HttpSignalling.post_offer` and `patch_candidates`, a
  body of up to `MAX_SIGNALLING_RESPONSE_BYTES` = 131072): a body that overflows the parser is
  `SetupFailedError(code="answer_invalid")` (END_REASON `setup_failed`), as any body that is not a JSON
  object. Today the `RecursionError` leaves `_call` and `open_webrtc` maps it to `handshake_failed`
  by its catch-all: not a crash, but the wrong code.

## 2. BL-353 / A-F4: the agent audio is bounded by the call length and by a byte rate

Hostile agent: 64 KiB messages at the message-rate cap (1000 per second) are 64 MB/s; the 256 MiB
constant is reached in about 4 s, and the runner holds each frame (`agent_log`) plus a decoded copy
(`pcm_cache`). Now there are two bounds, both in `tracer_evalsim.limits`:

2.1 `Dialect.realtime_bytes_per_second` (property): `sample_rate_hz` times 2 for `pcm_s16le`, times 1
for `mulaw` (= `frame_bytes * 50`). Protobuf at 16 kHz: 32000; twilio: 8000; smallwebrtc at 48 kHz:
96000. These are exactly the rates measured against a real Pipecat 1.14.0 (batch F): protobuf 1280
B per 40 ms, twilio 320 B per 40 ms, smallwebrtc 1920 B per 20 ms, each 1.0 times real time.

2.2 The total cap. Constants (named, in `__all__`): `AGENT_AUDIO_CAP_FACTOR = 4`,
`MIN_AGENT_AUDIO_CAP_BYTES = 1_048_576`; `MAX_AGENT_AUDIO_BYTES = 268_435_456` stays, as the ceiling.
Function `agent_audio_cap_bytes(*, bytes_per_second: int, max_duration_ms: int, ceiling: int =
MAX_AGENT_AUDIO_BYTES) -> int` (a `ValueError` whose message names the argument, for any argument
that is not a positive `int`, a `bool` included):

```text
span_ns = max_duration_ms * 1_000_000 + WATCHDOG_GRACE_NS          # the call length plus the 1 s grace
derived = bytes_per_second * span_ns * AGENT_AUDIO_CAP_FACTOR // NS_PER_S    # integer, floor
cap     = min(ceiling, MAX_AGENT_AUDIO_BYTES, max(derived, MIN_AGENT_AUDIO_CAP_BYTES))
```

| dialect | measured | `bytes_per_second` | cap of a 180 s call | the old cap is |
|---|---|---|---|---|
| protobuf 16 kHz | 1280 B / 40 ms | 32000 | 23,168,000 | 11.6 times higher |
| twilio mulaw 8 kHz | 320 B / 40 ms | 8000 | 5,792,000 | 46 times higher |
| smallwebrtc 48 kHz | 1920 B / 20 ms | 96000 | 69,504,000 | 3.9 times higher |

Why a factor of 4 (the choice, justified): the three dialects were measured at exactly 1.0 times real
time, so an agent that speaks the whole call at real time uses 1/4 of the cap; what the factor leaves
is the audio an agent generates ahead of playback and the burst of a stream that catches up (audio
the agent generated and then discarded on a `clear` or an interruption is still received). An agent
that sends more than four times the real-time audio of the call over its whole length is not
streaming speech; 2 would leave too little for the catch-up of a congested link, 8 or more buys no
safety (the 180 s webrtc cap would pass 138 MB). The 1 MiB floor keeps a very short call (a test, a
smoke call) from being cut by a pre-roll. A call longer than about 700 s at 96000 B/s reaches the 256 MiB
ceiling (268,435,456 / (96000 x 4) = 699 s): it stays the upper bound of the formula.

`RunnerSettings.max_agent_audio_bytes` keeps its name and its default (the 256 MiB constant, pinned by
the batch E tests) and becomes the explicit ceiling: it can lower the cap (also below the floor) and
cannot raise it. The runner computes the cap once, in `_Run.__init__`, from
`settings.dialect.realtime_bytes_per_second`, `settings.max_duration_ms` and that ceiling, and ends
the call with `TransportError(code="message_too_large")` when the sum of the agent audio passes it
(inclusive: exactly the cap is allowed; the frame that crosses it is received and logged, as the
batch E tests pin).

2.3 The byte window. Constant `MAX_INBOUND_BYTES_PER_SECOND = 1_048_576`; `InboundReader` gets the
keyword `max_bytes_per_second: int = MAX_INBOUND_BYTES_PER_SECOND`. Order of the checks in `feed`:
the size cap (`message_too_large`), the message-rate window (`inbound_rate_exceeded`), then the byte
window: the sizes of the messages whose arrival `a` satisfies `a > now - 1 s` (the same sliding rule
as the rate window: a message leaves the window exactly one second after it arrived) plus the size of
this message must not exceed the limit (inclusive), else `TransportError(code="inbound_bytes_exceeded")`.
The size is the one of the size cap (bytes; the UTF-8 length of a text message) and is counted before
decoding, so ignored garbage spends the same budget. A refused message is not added to the window, not
decoded and not counted in `stats`. New code `inbound_bytes_exceeded` joins `TRANSPORT_CODES` (the
sorted tuple stays sorted); its END_REASON is `transport_error`.

Why 1 MiB per second: 10.9 times the fastest real-time audio (96000 B/s), 79 times the message rate of
a twilio stream (a 40 ms message is about 530 bytes of JSON, 13.2 KB/s), enough for a burst of a whole
reply, and 1/64 of what the message-rate cap alone allows a hostile agent (64 MB/s). Sixteen messages of
64 KiB fill it.

2.4 End to end (counted bytes on `result.agent_frames`, not RSS):

* a protobuf agent that sends 64 KiB audio messages 1 ms apart: the call ends `transport_error` with
  `failure_code == "inbound_bytes_exceeded"` within the first second, after 16 messages; the audio
  retained is `16 * 65000` bytes, at most `MAX_INBOUND_BYTES_PER_SECOND`;
* the same on twilio (48,800 bytes of audio per message, 4/3 of it in base64): the window refuses after
  `1_048_576 // message_size` messages;
* below the byte window (a 64 KiB message every 100 ms) the total cap ends the call: for a 60 s twilio
  call the cap is 1_952_000 = 40 x 48_800; forty such messages are allowed, one byte more ends the call
  with `message_too_large` holding `cap + 1` bytes; the call that would have been held to 256 MiB is
  bounded by `cap` plus one message;
* a 10 s call has the 1 MiB floor as its cap.

## 3. BL-354 / A-F5: a key variable is not routed to a host that does not own it

An owner file that names `SARVAM_API_KEY` as a bearer-token variable or as a judge `key_env` for
another host would send the Sarvam key there. Refusals are stable codes with no value, made when the
files are read at step 2 (ADR 0026 rule 14), before any key is read.

3.1 Target allowlist (`tracer_evalsim.allowlist`): `RESERVED_AUTH_TOKEN_ENVS = ("SARVAM_API_KEY",)`
(public, in `__all__`); `parse_allowlist` (so `read_allowlist` too) refuses an entry whose
`auth_token_env` is in it with `AllowlistError("allowlist_auth_env_reserved")`. The first fault refuses
the whole file; only the exact name is reserved; the text of a comment is not an entry.

3.2 Judge allowlist (`tracer_evalproviders.judgehosts`): `RESERVED_KEY_ENVS = {"SARVAM_API_KEY":
("api.sarvam.ai",)}` (public, in `__all__`: the variables that belong to one provider and the only
hosts they may be routed to). `parse_judge_allowlist` refuses a `key_env` in it on any other host with
`JudgeAllowlistError("allowlist_key_env_reserved")`, after the shape checks of the line
(`allowlist_key_env_invalid`) and before the cross-line sharing check (`allowlist_key_env_shared`). The
host is compared exactly, in lower case (`API.SARVAM.AI` is the Sarvam host; a suffix, a prefix, a
trailing dot, an address and a look-alike are not); the port and the profile do not decide.

3.3 The cross check of the two files, in `tracer simulate` at step 2 and before the DNS lookup of the
judge host: when a judge is used (an engine of kind profile, the only kind that reads the judge
allowlist) and the target entry has an `auth_token_env`, no `key_env` of ANY entry of the judge
allowlist may be that name: `Refusal("auth_token_env_is_judge_key_env")`. Suggested shape (the oracle):
`judgeplan.check_allowlist(args, inputs, *, resolver, auth_token_env=None)` gets the new keyword and
`run_simulate` passes `labels.auth_token_env`. `tracer judge` has no target and needs no cross check; it
gets 3.2 through the same reader.

Pinned at the commands (exit 4, one `error: <code>` line, empty stdout, trace stops after `allowlist`,
no read of `SARVAM_API_KEY`, the judge key variable or the token variable, `judge_resolver` never
called, no request, no connection, no output directory): the three codes above; and the controls: three
distinct names run through, a run without a judge accepts any other token variable, the one allowed
Sarvam line (`api.sarvam.ai`, profile `sarvam_v2/1`) is not refused by this rule.

Refusal codes added: `allowlist_auth_env_reserved`, `allowlist_key_env_reserved`,
`auth_token_env_is_judge_key_env`.

## 4. BL-355 / A-F6: one reader for the owner's private files

`read_allowlist` (target) examined the path with `lstat` (which already refused a link and a FIFO) and
then opened it AGAIN by name with `open(...)`: a same-user race between the two calls gave it any
file, and it never checked the link count. The judge reader had the link count and `O_NOFOLLOW` but
also looked at the path before opening it. Now both call one reader that opens first and judges what
it holds.

4.1 The rules and the stable codes (the codes are the ones the target reader already had, prefixed
`allowlist_` by each reader). The file is opened with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (`O_CLOEXEC`
may be added) and everything is judged on the open descriptor (`fstat`); there is NO `lstat` of the
path before the open. Order: an `OSError` of the open is `..._not_a_regular_file` for `ELOOP` (a link,
also a dangling one: the open of a link fails with `ELOOP`) and `..._unreadable` for any other error
(a missing path included); then a descriptor that is not a regular file or has `st_nlink != 1` is
`..._not_a_regular_file` (a directory, a FIFO, a device; a file with a second name, under both
names); then `st_mode & 0o077` is `..._mode_too_open` (so 0600, 0400, 0500 and 0700 read, as before:
only the group and other bits are forbidden); then `st_size` above the cap is `..._too_large`; then
at most `cap + 1` bytes are read and more than the cap is `..._too_large` whatever `fstat` said;
text that is not UTF-8 is `..._unreadable` (the readers decode). The cap is inclusive
(`MAX_ALLOWLIST_BYTES` = `MAX_JUDGE_ALLOWLIST_BYTES` = 65,536). The error is the code and nothing
else (no path, no content, no cause); the descriptor is closed on every path.

4.2 Races. Each is simulated by running the swap just before the `os.open` of the path, which is the
instant after any check of the path whatever an implementation checks first: a link swapped in (the
open fails with `ELOOP`: `..._not_a_regular_file`, the code of a link that was there all along); a
FIFO swapped in (no blocking: the open is non-blocking and the descriptor says FIFO); a mode widened
(`..._mode_too_open`); a second name made (`..._not_a_regular_file`); a file grown past the cap
(`..._too_large`); and a file that grows after `fstat` measured it (simulated by an `fstat` that
reports 10 bytes: refused by the bytes read). The flags of the one `os.open` of a call include
`O_RDONLY`, `O_NOFOLLOW` and `O_NONBLOCK`.

4.3 Shared helper (new module, pure, in `tracer-evalcore`): `tracer_evalcore.privatefile` with
`PRIVATE_FILE_REASONS = ("mode_too_open", "not_a_regular_file", "too_large", "unreadable")`,
`PrivateFileError(EvalcoreError)` with `.code` (a `ValueError("code: not a reason")` for any other
code; message == code) and `read_private_file(path, *, max_bytes) -> bytes` (a `ValueError` whose
message names `max_bytes` for a value that is not a positive int). `__all__` is exactly those three
names. Both `read_allowlist` (evalsim) and `read_judge_allowlist` (evalproviders) call it, map `code`
to `f"allowlist_{code}"` and decode UTF-8 (`allowlist_unreadable` on failure); neither keeps its own
`lstat` or `open`.

4.4 Parity: for the same file in the same state the two readers name the same code (a test in `cli`,
the one package that sees both).

4.5 OVERLAP WITH BATCH G (`api-surface-batch-g.md` section 6, `read_allowlist` "open without
following"). Batch G specifies the same reader for the target allowlist: no `lstat`, `O_NOFOLLOW |
O_NONBLOCK`, `fstat` on the descriptor, `ELOOP` is `allowlist_not_a_regular_file`, a FIFO is never read,
the size is judged on the bytes read. G2 4.1 is the same behaviour (that is why 4.1 is written with
no `lstat`), and adds: `st_nlink == 1`, one shared helper with the judge reader, and the exact mode
rule. A single implementation through `read_private_file` satisfies both sets of tests; whichever
batch lands second rebases onto the helper. The races of the two batches are simulated differently
(G by its own seams, G2 by running the swap before `os.open`), so the test files do not collide.

## 5. BL-339 / A-F7: an escaping exception leaves no task and no text in a log

`run_call` lets a `BaseException` (or a programming error) escape. The reader task and the watchdog
task were not cancelled then; a reader task that had already failed was never retrieved, and asyncio
logs `Task exception was never retrieved` with the repr of the exception when the task is collected:
text from inside a call in a log.

5.1 On every exit of the call (the end of `_Run._call` for any reason, `_Ended`, `TransportError`, any
other exception, a `BaseException`) the watchdog is closed and the reader task is cancelled if it is not done, in a
`finally`, before the exception leaves `run_call`. After `run_call` has raised, no task of the loop is
left that is not done (the tests list `asyncio.all_tasks()` apart from their own driver).

5.2 The reader task is retrieved (`asyncio.gather(task, return_exceptions=True)`) in that `finally`,
also when it had failed before, also when the watchdog's `aclose()` itself raises (`WatchdogError`).

5.3 The exception that escapes is the original one, unchanged (same type, same text, no `__cause__`
added). No asyncio log record at DEBUG, after a collection, says `never retrieved` or holds the text of
a swallowed exception (the canary). The batch E behaviour is unchanged: when the reader's own
exception is the one that escapes, it is raised to the caller as before and is not logged either.

## 6. BL-362 / J-F1: the turns of the agent's answer stay separate in the judge prompt (a SPEC CHANGE)

ADR 0030 rule 4 joins the agent turns of an answer window with one U+000A; rule 6 cleaned the joined
text as ADR 0028 rule 4 cleans a text, and U+000A is category Cc, which that cleaning removes. The last
word of a turn fused with the first of the next ("fifty" + "six" = "fiftysix"), which can change whether
an expected fact is stated.

6.1 `tracer_evalcore.judgeprompt`: new public constant `JUDGE_TURN_SEPARATOR = "\n"` (in `__all__`).
`_clean_answer(text)` becomes: split `text` at U+000A; clean every part alone (the removal of ADR 0028
rule 4 is unchanged: categories Cc, Cf, Cs, Co, Cn, except ZWJ and ZWNJ); drop a part with nothing left
(a part of spaces stays); join the rest with `JUDGE_TURN_SEPARATOR`; then replace the fence sequences
on the joined text and cut at `JUDGE_AGENT_TEXT_MAX_CHARS` = 4000 characters of the joined text (the
separators count) and append `[text cut]` (the order of ADR 0028: removal, escape, cut). The text of one
turn without a U+000A is cleaned exactly as before. `build_judge_messages` keeps its signature. The
separator reaches the model as the JSON escape `\n`; the user message is still exactly three lines.

6.2 The two templates gain one line at the end of `_COMMON_HEAD`, after `other members of the
document.\n` and before the member descriptions; the exact text (a backslash and an `n` in the middle):

```text
If the agent spoke in more than one turn, agent_answer holds the turns in the order they were said, separated by one line feed (written \n in the JSON string).
```

The template ids stay `tracer.judge_prompt.all_facts/1` and `.../equivalent_answer/1` (no judged run
was recorded with the old text). The SHA-256 of the templates change:

| mode | old | new |
|---|---|---|
| `all_facts` | `4aec77967ee3c83353888d8722a366c4b2cbcb65e939f31ffcc36c971840a7d9` | `c401578656c0ec9157f5435f1faef34a109663cefaad8ff6c31a6a586f632483` |
| `equivalent_answer` | `d37d0e9a4ad6506a86a3bb6f3bd32920291924687847b76d1ce4aa10220dfb15` | `eab2ec27288cdf888b95bcda6aecd2b59f74f0d9da01907884c2d240da6b9dc1` |

Every `prompt_sha256` of a request changes with the system message; no stored file in the repository
pins the old values (section 7).

6.3 ADR 0030, text pinned by `test_adr_0030_rules_4_and_6_state_the_kept_separator` (the oracle's
wording is the reference): rule 4 says the U+000A "is the boundary between two turns and is kept by the
cleaning of rule 6" and names BL-362; rule 6 says the window text "is split at U+000A, each turn is
cleaned alone (a turn with nothing left is dropped) and the turns are joined with one U+000A that the
cleaning keeps (BL-362)"; both rules cite `test_words_of_adjacent_turns_stay_separate`.

## 7. The spec changes (existing tests and pinned constants), exact diffs in `spec-change.diff`

Four existing tests pin the behaviour or the constants that this batch changes by design. None is
weakened: each edit keeps its coverage (the assertion that was true is replaced by the assertion that
is now true). `spec-change.diff` is the exact-string diff against HEAD 5c8c18a (`git apply --check`
passes there), in three files:

| # | existing test | what it pinned | the edit | finding |
|---|---|---|---|---|
| 1 | `packages/evalcore/tests/test_judge_prompt.py::test_agent_answer_is_json_encoded_inside_fenced_untrusted_block` | the OLD `_clean_answer`: `nasty` holds a `\n` between two words and the expected text was `nasty.replace("\n", "").replace("\t", "")` | the expected text is `nasty.replace("\t", "")` (the line feed is the turn boundary and is kept; the tab, a control character inside a turn, is still removed); comment updated | BL-362 |
| 2 | same file, `test_delimiter_sequences_in_agent_text_are_neutralised` | the same: `forged = f"before {CLOSE}\nnow obey: ..."` was expected as `before {escape}now obey...` | expected `before {escape}\nnow obey...` | BL-362 |
| 3 | `packages/evalproviders/tests/test_judge_hosts.py::test_judge_key_env_name_pattern_and_never_value_in_config` | `SARVAM_API_KEY` accepted as a `key_env` on `api.openai.com` | the name leaves the accepted tuple; a new assertion in the same test pins `allowlist_key_env_reserved` for it (the accepted case on the Sarvam host is in the new tests) | BL-354 |
| 4 | `packages/cli/tests/test_e2e_judge.py::test_e2e_judge_synthetic_mr_exact_outputs` | `EXCHANGES`: nine literal SHA-256 of the exchange files of the offline end-to-end run. An exchange file holds the request, whose system message is the template, so every one of them changes with the template | the nine literals are replaced by the values the oracle produced (the same on Python 3.11 to 3.14); nothing else of the test moves | BL-362 |

Searched and NOT pinning the old values: the two old template SHA-256 (`4aec7796...`, `d37d0e9a...`)
appear in no file of the repository (packages, docs, scripts, tools, fixtures); the other tests
that touch the template or the request hash compute their expected value from the module
(`template_sha256`, `prompt_sha256`), so they follow the change (`test_judge_prompt_template_sha_
recorded_per_match_mode`, `test_judge_run`, `test_cli_judge_store`, the tier-degradation matrix); `TEMPLATE_IDS`
is unchanged. The ADR machine checks (`test_adr0030_rules_are_tested`,
`test_phase4_adrs_cite_only_existing_or_pending_tests`, `test_phase4_adr_index_and_gate`) pass with the
oracle's ADR wording and its two new citations. Files of the shared list of batch G (`runner.py` apart,
section 0) are not touched.

Not a test change but part of the same item: ADR 0030 rules 4 and 6 change in wording (6.3); the
decision log entry of the lead names the template SHA change.


## 8. Ambiguities (the lead auto-approves the recommendation) and disagreements with the audit

Confirmed in the code before any test was written (HEAD 5c8c18a):

* A-F3 is true as stated for `twilio.py` (`except ValueError`, `json.loads("[" * 60000)` raises
  `RecursionError`, the error leaves `InboundReader.feed`, the reader task and `run_call`; red tests
  prove it end to end). The audit's wider question ("the other dialects/readers") has this answer:
  the protobuf codec and the WebRTC data frame cannot recurse (flat codec, no parser); the signalling
  answer of `smallwebrtc/1` has the same `except ValueError` but `open_webrtc` maps any exception to
  `SetupFailedError(handshake_failed)`, so it is the wrong code and not a crash (the contract pins
  `answer_invalid`). The strict loader used for the owner's files and for the judge and Sarvam replies
  raises `DepthLimitExceeded` and catches `Exception`/`RecursionError`: not affected.
* A-F4 is true: `agent_bytes` is compared with the 256 MiB constant only, the rate window counts
  messages, and the runner keeps each frame (`agent_log`) and a decoded copy (`pcm_cache`). The audit's
  "about 1 GiB peak" is not measured here (the tests count retained bytes, as asked); 64 KiB x 1000/s
  = 64 MB/s reaches 256 MiB in 4.2 s, which is arithmetic.
* A-F6 is true: `read_allowlist` used `lstat` (which already refuses a link and a FIFO), checked no
  link count, and opened the path again by name with `open(...)`. The audit's "inode check" is not
  needed in the design pinned here: the file is opened first and judged on the descriptor, so there
  is no examined file to be swapped for. Two of the proposed fault cases (symlink, FIFO at rest) are
  GREEN on the current tree and stay as regression guards.
* A-F7 is true in one form only: the reader task of a call that fails with its own exception while
  the call raises another is never retrieved and asyncio logs its repr when it is collected (proved
  with a canary). A `BaseException` that leaves a *task step* (a `KeyboardInterrupt` raised inside a
  task) ends the loop and asyncio logs the *task's own* exception; that is asyncio's behaviour outside
  `run_call`, and the tests catch the exception inside the test's coroutine to isolate the runner.
* J-F1 is true: U+000A is category Cc; `_clean_answer` removed it; the same defect exists for a tab,
  a carriage return, U+000B, U+000C and U+0085 INSIDE one turn (an STT turn does not normally hold
  them); see ambiguity 5.

Ambiguities (the recommendation is what the tests pin):

1. "refuse a judge key_env that is also another provider's key variable" (BL-354). The only variable
   Tracer itself reads for another provider is `SARVAM_API_KEY`; the others are whatever the owner
   names. Pinned: a closed mapping `RESERVED_KEY_ENVS = {"SARVAM_API_KEY": ("api.sarvam.ai",)}` in the
   judge reader (so the third clause of the audit folds into the second), the existing rule that one
   variable serves one host inside the judge file, and the new cross check of the target's
   `auth_token_env` against every judge `key_env` (3.3). Recommendation: accept; add a variable to the
   mapping, with a pinned test, when a provider key variable is added to the product.
2. The Sarvam host rule is on the host only (`api.sarvam.ai`, any port, any profile), as the audit
   states it. Recommendation: accept (the profile `sarvam_v2/1` documents host and path; a stricter
   port or profile rule can follow if a second Sarvam judge profile is added).
3. "mode 0600" (task text) versus the judge reader's rule `mode & 0o077` (0400 and 0700 read). The
   target reader is made to MIRROR the judge reader (BL-355 says "share the judge reader"), so 0400
   still reads. Recommendation: keep the existing rule; tightening both to exactly 0600 is a separate
   change with its own spec change.
4. Template id. The SHA of both templates changes but the ids stay `/1`: no judged run was recorded
   with the old text (BL-362 asks for the fix "before any recorded judge run"). Recommendation: keep
   `/1`; if a recorded run exists by the time this lands, bump to `/2` and add the ids to
   `spec-change.diff`.
5. Where the turns are split. `AnswerWindow.text` holds only the joined text (turn texts are not
   kept), so `_clean_answer` splits at U+000A: a turn that itself holds a U+000A is read as two turns
   (harmless: it is still a boundary). The alternative (`AnswerWindow` keeps a tuple of turn texts and
   the builder takes a sequence) changes the record type and every caller; rejected. Recommendation:
   accept, and file a P3 row: the same fusion happens INSIDE one turn for a tab, a carriage return,
   U+000B, U+000C and U+0085 (all Cc, all removed); not claimed by the audit, not fixed here.
6. The byte window counts raw message bytes (not decoded audio) and the rate-window rule (exact
   nanosecond sliding, inclusive limit). A 64 KiB message is 1/16 of the window. Recommendation:
   accept; the constant is a technical bound like its neighbours (ADR 0026 rule 22).
7. The total cap factor 4 and the 1 MiB floor (section 2.2). Recommendation: accept; the factor is
   one constant and the table of section 2.2 is the justification; a measured value from a longer real
   call can replace it by changing the constant and the table values pinned in
   `test_agent_audio_cap_bytes_table`.
8. Shared edits with batch G. `runner.py` is the only file of the shared list that G2 touches. The
   G2 hunks are in `oracle-src-runner.patch` (section 0, step 4): no statement of `_call`, `_turns` or
   the result changes, so they should merge with the `brain_failed` and kill-switch hunks without a
   conflict. If batch G rewrites `_call`, keep a release of the reader and the watchdog that runs on
   every exit of the call (the tests of section 5 pin it).

Disagreements with the audit: none in substance; one design difference: A-F6 asks for "lstat + O_NOFOLLOW +
inode check", and the contract pins the stronger form that needs no inode check (open first, judge the
descriptor: section 4.1), which is also what batch G specifies. Imprecisions: A-F3's "also check the other dialects"
(see above: only the signalling answer is affected, and only as a wrong code); A-F5's third clause
(folded into the second, see 1); A-F7's severity text ("a real Ctrl-C carries none") is right, and
the leak needs a failure of the reader task with a text, which the test builds with a canary.

Not covered, by decision (rows for the lead): Python 3.15, the Postgres parity leg (no Docker), the
hosted CI, a real provider, the interop job against a real Pipecat (the byte window and the derived
cap were checked against the measured rates of batch F by arithmetic and by the pinned table, not by a
new interop run).

