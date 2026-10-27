# Phase 4 batch D contract: transports of the simulated caller (P4-16 to P4-20)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (rows P4-16 to P4-20 with the amendments of 2026-10-27, sections B, C, D, the amendments D-a to D-d and E-a/E-b), ADR 0026 (rules 1, 3, 5 to 10, 14 to 17, 20, 22, 23, the section "Dialect specifications" with the Twilio message set, `frames.proto` at tag `v1.14.0` and the SmallWebRTC signalling), ADR 0027 (rules 4, 10, 13 and the `tracer.sim_profile/1` `connection` object), the decision log entries "BL-291 PARTIALLY CLOSED" and "ADR 0026 AMENDED", the scaffold of `packages/evalsim` (P4-01), the batch 0b contract (`docs/phase4/api-surface-batch-0b.md`) and the lead's instructions of 2026-10-27. qa owns this file and the tests; it changes no source, `pyproject.toml`, ADR, SCOPE, workflow or earlier test except the two named spec changes of section 9. Every statement below is pinned by a test named in section 10, unless it says "not pinned".

Facts about third-party libraries below were observed with the locked versions (`websockets` 17.3, `aiortc` 1.15.0) in the oracle, on 2026-10-27. They are marked "observed". Items that ADR 0026 marks "not verified" are still not verified, and NO test pins them (sections 4.2, 5.3 and 7 name them).

## 0. Where the tests are, and how to move them

IMPORTANT (Phase 2 lesson, twice): nothing was created under `packages/`. The tests are staged at

```text
docs/phase4/evidence/red/batch-d/staging/tests/evalsim/    -> packages/evalsim/tests/   (20 files: 13 test files, 6 support modules, 1 conftest; 349 new test items)
docs/phase4/evidence/red/batch-d/staging/tests/evalcore/   -> packages/evalcore/tests/  (1 file, a named spec change)
```

One of the 21 staged files REPLACES an existing file (`evalcore_phase4_adr_support.py`, spec change S-D) and one replaces `packages/evalsim/tests/conftest.py` (spec change S-D1); the other 19 are new. No `__init__.py`, no data file. Test module names are unique across all test directories. All are ASCII, pass `ruff check`, `ruff format --check` and `mypy --strict` at their final path, and none reads a key or the private fixtures. The product modules are imported lazily (`importlib`, helper `evalsim_support_imports.mod`), so before they exist each test fails on its own with `ModuleNotFoundError` and the directory still collects.

The dev's first acts, in this order:

```text
1. git mv docs/phase4/evidence/red/batch-d/staging/tests/evalsim/*.py   packages/evalsim/tests/   (replaces conftest.py)
   git mv docs/phase4/evidence/red/batch-d/staging/tests/evalcore/*.py  packages/evalcore/tests/  (replaces evalcore_phase4_adr_support.py)
2. NOTICE (all seven copies, section 8), packages/evalsim/CHANGELOG.md (section 3.5)
3. the modules of sections 2 to 7, in this order: errors, limits, clock, dialects/base, dialects/protobuf_wire,
   dialects/protobuf_dialect, dialects/twilio, ws, sender, watchdog, webrtc
```

Commands (after the move and the changes):

```text
everything:          uv run --locked --all-groups pytest
Phase 4 batch D:     uv run --locked --all-groups pytest packages/evalsim/tests
the ADR readers:     uv run --locked --all-groups pytest packages/evalcore/tests -k "adr0026 or adr0027 or phase4"
only the sockets:    uv run --locked --all-groups pytest packages/evalsim/tests -m loopback
no sockets at all:   uv run --locked --all-groups pytest packages/evalsim/tests -m "not loopback"
```

## 1. The `loopback` marker (named conftest change S-D1)

### 1.1 Decision (for the lead to auto-approve; recommended option first)

The dialect tests need a real WebSocket, a real TLS handshake and a real HTTP exchange against a fake server: the things that matter (the `Authorization` header on the wire, a redirect that is not followed, a proxy that is not used, a certificate that is verified, a frame cap enforced by the library) cannot be observed with an in-memory pair. The conftest of `packages/evalsim/tests` has no loopback exemption (P4-01: "a later task that needs one adds it for its own marked tests"). The minimal, disclosed way:

* A new marker `loopback`, registered by `packages/evalsim/tests/conftest.py` itself (`pytest_configure`, `addinivalue_line`), so no repository file changes and `--strict-markers` stays on.
* A test that carries the marker (on the function, the class or the module) runs with `install_loopback_only` of the new support module `evalsim_loopback_guard.py`: the same adversarial guard as `packages/cli/tests/cli_network_guard.py` (the `postgres` precedent) and, because the transports are asyncio, the same rules on the asyncio paths. Every other test keeps the full Phase 3 guard, loopback included.
* The marker is NOT deselected by default (`addopts` is unchanged). The tests have no external resource, no key and no second process, so they run in every leg of the normal test job and in the lowest-direct leg, and in a plain `pytest` on a developer machine.

The guard (`evalsim_loopback_guard.py`, 8 rules, each with a guard test):

1. a destination host is `localhost` or a literal loopback IP (127.0.0.0/8, ::1, ::ffff:127.x); any other host, an IP outside loopback, a name that is not `localhost`, is refused with an `AssertionError` before any DNS query (the real resolver is not called);
2. `localhost` is resolved with the real resolver and every address must be loopback, otherwise refused (a hosts file that points `localhost` away does not open the network);
3. a server may BIND a literal loopback address only: `''`, `0.0.0.0`, `::`, a name, `host=None` and a host list that contains a non-loopback address are refused;
4. only TCP/IP (AF_INET, AF_INET6) with a `(host, port)` address; Unix sockets, UDP (`create_datagram_endpoint`) and `create_unix_*` are refused;
5. the paths guarded: `socket.socket.connect`, `connect_ex`, `bind`, `socket.create_connection`, `socket.getaddrinfo`, `asyncio.BaseEventLoop.create_connection`, `create_server`, `sock_connect`, `create_datagram_endpoint`, `create_unix_connection`, `create_unix_server`;
6. the refusal is an `AssertionError`, never an `OSError` (retry code must not swallow it);
7. the real functions are captured when the guard module is imported (before any test patches the socket) and the guard tests replace them by spies, so a broken guard calls a spy and sends no packet;
8. an unmarked test keeps the full guard exactly as before (connect, connect_ex, create_connection, getaddrinfo, `loop.create_connection`, `loop.sock_connect` all refuse, loopback included).

### 1.2 Rejected options and trade-offs

* Deselect by default, like `postgres` (selected only by `-m loopback` in one CI job). Rejected: `postgres` is deselected because it needs an external server; here nothing is external, and the tests are the core of batch D. Deselected tests are not run by `uv run pytest` locally or in the 3.11 to 3.14 legs, and the first failure would arrive in a special job. Trade-off of the chosen option: every leg binds loopback ports (port 0, so no clash) and the suite needs a working loopback interface (hosted runners have one); a sandbox without loopback fails these tests by design and the `-m "not loopback"` command above runs the rest.
* Reuse `integration` (SCOPE categories: loopback only, a separate CI job, against `tools/samplebot`). Rejected: `integration` means "talks to a Pipecat process", is deselected, and P4-30's `test_integration_tests_loopback_only` would then have to tell an in-process fake from a real bot. Keeping the two markers apart keeps both contracts simple. Recommendation for P4-27: its conftest extension applies the same `install_loopback_only` to tests marked `integration` (it already allows exactly the three loopback names) and `integration` stays deselected.
* In-memory transports only. Rejected: the real findings of this batch (section 11) are about the real libraries.
* A mock of `websockets`. Rejected: it would prove the mock.
* A loopback exemption for the whole directory. Rejected: a unit test that opens a socket by accident must still fail.

### 1.3 Consequence for ADR 0026 rule 23 and ADR 0024 rule 17 (proposed amendment; the architect applies it)

ADR 0026 rule 23 says unit tests "never open a socket" and tests marked `integration` "connect to 127.0.0.1, ::1 or localhost only". Proposed addition (one sentence, no rule or invariant count changes): "A third class, tests marked `loopback`, start a fake server of their own in the same process on 127.0.0.1 or ::1 and connect to it; they run by default, use no key, no external process and no host name other than `localhost`, and run under the loopback-only guard of `packages/evalsim/tests/evalsim_loopback_guard.py`, which refuses every other destination, every non-loopback bind, UDP and Unix sockets (guard tests: `test_loopback_marked_test_cannot_connect_anywhere_else`, `test_loopback_marked_test_can_bind_only_a_literal_loopback_address`, `test_unmarked_test_keeps_the_full_guard_loopback_included`)." ADR 0024 rule 17 (the guard of Phase 3) gets the same cross-reference. These guard tests are not in SCOPE section A and therefore must not be cited by an ADR rule until they exist (they exist from the move); no pending set changes.

P4-30 (`test_unit_tests_open_no_socket (extended)`, `test_integration_tests_loopback_only`) must treat `loopback` as the third class: the extension scans that every test without the marker keeps the full guard (proved here by `test_the_real_conftest_applies_the_marker_exemption_and_nothing_wider`) and that the marker is confined to the four declared modules (`test_the_loopback_marker_is_confined_to_the_declared_modules`; a new module carrying the marker is a decision, not an accident).

## 2. `tracer_evalsim.clock` and `tracer_evalsim.limits`

```text
clock.Clock (Protocol):    def monotonic_ns(self) -> int
                           async def sleep_until_ns(self, deadline_ns: int) -> None   # returns at once if the deadline is not in the future
clock.SystemClock          time.monotonic_ns; sleep_until_ns never returns before the deadline (it loops until monotonic_ns() >= deadline)
__all__ = ["Clock", "SystemClock"]

limits (all `Final` ints):
  NS_PER_S = 1_000_000_000      FRAME_NS = 20_000_000
  MAX_INBOUND_MESSAGE_BYTES = 65_536               # ADR 0026 rule 22; text is measured in UTF-8 bytes
  MAX_INBOUND_MESSAGES_PER_SECOND = 1_000          # ADR 0026 rule 22; a sliding one-second window
  WATCHDOG_GRACE_NS = NS_PER_S                     # ADR 0026 rule 8
  MAX_AUTH_TOKEN_CHARS = 4096   MAX_SDP_BYTES = 65_536   MAX_SIGNALLING_RESPONSE_BYTES = 131_072
```

The two caps are "technical bounds, not measured values" and are fixed by this contract from the dialect frame size: one 20 ms frame is 640 bytes at 16 kHz and 1,920 bytes at 48 kHz in 16-bit PCM, so 65,536 bytes is 34 frames at 48 kHz (about 0.7 s) and 2.0 s at 16 kHz; 1,000 messages a second is 20 times the real-time frame rate and leaves room for a reply that the agent buffered and sends faster than real time. The tests pin only the ranges `4 * 1,920 <= MAX_INBOUND_MESSAGE_BYTES <= 1 MiB` and `200 <= MAX_INBOUND_MESSAGES_PER_SECOND <= 100,000` and that the values equal the numbers in the package CHANGELOG (3.5); the behaviour at the exact values is tested through the constants, so changing a value is one edit in `limits.py` and one in the CHANGELOG.

## 3. `tracer_evalsim.dialects.base`: the interface, the reader, `hang_up`

```text
AGENT_EVENT_KINDS = ("audio", "clear", "mark", "interruption", "text", "transcription", "message")
@dataclass(frozen=True) CloseInfo(code: int | None, clean: bool)       # clean = the peer sent a Close frame
class WireConnection (Protocol):  async send(message: bytes | str) -> None;  async recv() -> bytes | str | None;
                                  async close(code: int = 1000) -> None;  close_info: CloseInfo | None  (property)
@dataclass(frozen=True) Decoded(kind: str, audio: bytes | None = None)
@dataclass(frozen=True) AgentEvent(kind: str, arrival_ns: int, audio: bytes | None = None)

class Dialect (ABC):
    dialect_id: ClassVar[str]          wire_codec: ClassVar[str]   ("pcm_s16le" | "mulaw")
    spec_sources: ClassVar[tuple[str, ...]]     spec_retrieved: ClassVar[str]      sample_rate_hz: int
    frame_bytes -> int                 # 20 ms of wire audio: rate * 20 // 1000 * (2 if pcm_s16le else 1)
    opening_messages() -> tuple[bytes | str, ...]
    encode_audio(frame: bytes, *, stream_ms: int) -> bytes | str     # frame must be exactly frame_bytes of `bytes`, else ValueError
    closing_messages() -> tuple[bytes | str, ...]
    decode(message: bytes | str) -> Decoded | None                   # None = not a message of this dialect (ignored)

@dataclass(frozen=True) InboundStats(messages, audio_frames, audio_bytes, ignored, after_close: int,
                                     by_kind: dict[str, int], clear_arrival_ns: tuple[int, ...])
@dataclass(frozen=True) InboundEnd(clean: bool, close_code: int | None);   .end_reason -> "agent_hangup" if clean else "transport_error"
class InboundReader(dialect, clock, *, max_message_bytes=MAX_INBOUND_MESSAGE_BYTES, max_messages_per_second=MAX_INBOUND_MESSAGES_PER_SECOND):
    stats -> InboundStats (a snapshot; later feeds do not change it; by_kind is a copy)
    mark_closed() -> None
    feed(message) -> AgentEvent | None
    async run(conn: WireConnection, on_event: Callable[[AgentEvent], None]) -> InboundEnd
async def hang_up(conn, dialect, *, close_code: int = 1000) -> None
```

`feed` order and semantics (each pinned):

1. After `mark_closed()` (or after `run` saw the close) a message is not interpreted: `stats.after_close` counts it, nothing else changes, and nothing is raised, whatever its size (a frame after the close is not even size-checked).
2. A message over `max_message_bytes` raises `TransportError(code="message_too_large")`; the cap is inclusive (exactly the cap is accepted); text is measured in UTF-8 bytes.
3. The sliding window: the messages whose arrival instant `a` satisfies `a > now - 1 s` count; if `max_messages_per_second` are already inside, `TransportError(code="inbound_rate_exceeded")`. One nanosecond short of a second the first batch still counts; exactly one second later it has left the window. Garbage and ignored messages count toward the rate (a flood of garbage is stopped too).
4. `dialect.decode(message)`: `None` -> `stats.ignored += 1`, returns `None`, never an exception (rule 5: "ignored and counted"). A `Decoded` -> `by_kind[kind] += 1`, and for `audio` the frame and byte counts; for `clear` the arrival instant is appended to `stats.clear_arrival_ns`. Returns `AgentEvent(kind, arrival_ns=clock.monotonic_ns() at feed, audio)`.
5. `stats.messages` counts every message that passed steps 1 to 3 (ignored ones included).
6. Text, transcription, message, interruption and mark events carry NO payload: `audio is None`, and no text of the agent is kept anywhere (ADR 0027 rule 7, BL-275; ADR 0026 rule 22: agent text never reaches a log line, a file name or a record). `test_agent_words_never_reach_a_log_record` checks the log and the output over a real socket.

`run`: reads `conn.recv()` until it returns `None`, calling `on_event` for each event; a `TransportError` from `feed` or from `recv` propagates after the earlier events were delivered. On `None` it calls `mark_closed()` and returns `InboundEnd(clean=info.clean, close_code=info.code)` from `conn.close_info` (`clean=False, close_code=None` if the connection has no close info). The reading of a close: a Close frame while the call runs is `agent_hangup`; a connection that vanished with no Close frame is `transport_error`; the close code is kept (the Pipecat runner closes with 4003 when a token or an Origin check fails; how a call that ends so is classified, a hang-up or a setup failure, is the runner's decision at P4-21, see section 12, ambiguity 6).

`hang_up`: sends `dialect.closing_messages()` (the Twilio `stop`), then `conn.close(close_code)`; never waits for the peer to react; a peer that is already gone (`TransportError` on the send) does not stop the close and is not an error. The protobuf dialect has no closing message, so it only closes.

## 4. `pipecat-protobuf/1`: the codec and the dialect

Modules (contract section 8 explains the names): `tracer_evalsim.dialects.protobuf_wire` (the hand-written codec) and `tracer_evalsim.dialects.protobuf_dialect` (class `PipecatProtobufDialect`; the module name has no `pipecat` because the scaffold test refuses any wheel member whose name contains it).

### 4.1 The codec

```text
frozen dataclasses with the proto3 defaults:  TextFrame(id=0, name="", text="")
  AudioRawFrame(id=0, name="", audio=b"", sample_rate=0, num_channels=0, pts=None)
  TranscriptionFrame(id=0, name="", text="", user_id="", timestamp="")   MessageFrame(data="")   InterruptionFrame(id=0, name="")
  UnknownFrame()                         # a Frame with no recognised oneof member
Frame = the union;  encode_frame(frame) -> bytes;  decode_frame(bytes | bytearray | memoryview) -> Frame
ProtobufDecodeError(EvalsimError, ValueError) with `.code` (machine code `[a-z][a-z0-9_]{0,63}`), facts only
__all__ sorted
```

The message set and the field numbers are those of `frames.proto` at `v1.14.0` as ADR 0026 records them (`test_protobuf_codec_field_table_matches_adr_proto_facts` compares the test table with the ADR text). One binary WebSocket message is one serialised `Frame`; the top-level `Frame` is a oneof: 1 text, 2 audio, 3 transcription, 4 message, 5 interruption.

Encoding (golden vectors, hand-computed, in `test_evalsim_protobuf_codec.py`; a separately written reference encoder reproduces them): fields in ascending number; proto3 defaults omitted (`0`, empty string, empty bytes); `pts` is `optional`, so it is written whenever it is not `None`, `pts=0` included (`30 00`); an empty member is still written (`0a 00`, `2a 00`); varints are base-128 little-endian; lengths are varints (the vectors cross 127/128 and 16383/16384). `encode_frame` raises `ValueError` for an integer outside its range (`id`, `pts` at most 2**64-1; `sample_rate`, `num_channels` at most 2**32-1), a `bool`, a negative number, a `str` where bytes are expected and the reverse, an `UnknownFrame` and any other object.

Decoding: fields in any order; the last oneof member on the wire wins; unknown fields (varint, fixed64, length-delimited, fixed32) inside a member and at the top level are skipped; nothing recognised is `UnknownFrame()`, not an error. Refused with `ProtobufDecodeError`: a truncated varint, a varint over ten bytes or whose tenth byte is above 1, a length past the end of the data (also inside a member), a group (wire types 3 and 4), wire types 6 and 7, field number 0, a string that is not UTF-8, a `uint32` above 2**32-1 (stricter than most parsers, which truncate; hostile input), a fixed field cut short. The error message never quotes the bytes. Not pinned: a field whose wire type does not match its number (skip or refuse), the exact `.code` of each case, the repetition of the same member (merge or replace).

### 4.2 The dialect

```text
PipecatProtobufDialect(*, sample_rate_hz: int)    # no default; ValueError unless an int (not bool) in 8000..48000 and a multiple of 50
dialect_id = "pipecat-protobuf/1"   wire_codec = "pcm_s16le"   spec_sources, spec_retrieved = (the ADR's URLs, "2026-10-27")
opening_messages() == ()   closing_messages() == ()
encode_audio(frame, *, stream_ms) -> bytes:  encode_frame(AudioRawFrame(audio=frame, sample_rate=<rate>, num_channels=1))   # no id, no name, no pts; stream_ms is not on the wire
decode(message):
  str message (a WebSocket text message)                   -> None  (ignored)
  protobuf decode error, UnknownFrame                      -> None
  AudioRawFrame, sample_rate != rate or num_channels != 1  -> TransportError(code="audio_format_mismatch")     # ADR 0027 rule 4: never resampled
  AudioRawFrame, odd number of audio bytes                 -> TransportError(code="audio_frame_invalid")
  AudioRawFrame ok (also empty and all-zero audio)         -> Decoded("audio", audio)      # whether it is "active" is P4-05's rule, not decided here
  InterruptionFrame / TextFrame / TranscriptionFrame / MessageFrame -> Decoded("interruption" / "text" / "transcription" / "message")   # no payload
```

Not pinned (ADR 0026: "not verified"): that the bytes are 16-bit little-endian PCM, which rate a deployed bot expects, which serializer a deployed agent uses behind `/ws-client`.

## 5. The WebSocket client, labels, token and the Twilio dialect

### 5.1 `tracer_evalsim.errors` (extends the scaffold; `EvalsimError` unchanged)

```text
TransportFactError(EvalsimError):  __init__(self, *, code: str, http_status: int | None = None, close_code: int | None = None)
    code: [a-z][a-z0-9_]{0,63};  http_status: int 100..599 (not bool);  close_code: int 0..65535 (not bool); ValueError otherwise
    attributes exactly (code, http_status, close_code); message "<ClassName>: code <code>[, HTTP <n>][, close <n>]" (+ " (<fixed hint>)" for webrtc_extra_missing);
    args == (message,); copy, deepcopy and pickle keep type and facts; END_REASON: str | None class attribute
TargetRefusedError(TransportFactError, ValueError)   END_REASON None             # refused before any socket (exit 4)
SetupFailedError(TransportFactError)                 END_REASON "setup_failed"    # ADR 0026 rule 9: an outcome, not a crash
TransportError(TransportFactError)                   END_REASON "transport_error"
WatchdogError(EvalsimError)
REFUSAL_CODES, SETUP_CODES, TRANSPORT_CODES: sorted tuples of the codes below (disjoint)
```

Codes. Refusal: `auth_env_name_invalid`, `auth_token_invalid`, `auth_token_missing`, `bot_credentials_field_missing`, `bot_holds_telephony_credentials`, `dialect_unknown`, `ice_server_invalid`, `ice_server_required`, `insecure_target`, `labels_inconsistent`, `origin_invalid`, `target_invalid`, `webrtc_extra_missing`. Setup: `answer_invalid`, `connect_timeout`, `connection_failed`, `connection_refused`, `handshake_failed`, `handshake_rejected`, `redirect_refused`, `tls_failed`. Transport: `audio_format_mismatch`, `audio_frame_invalid`, `connection_closed`, `connection_lost`, `inbound_rate_exceeded`, `message_too_large`, `send_failed`. (`connection_lost` and `send_failed` are reserved: a dropped connection is `connection_closed` on a send; the tests accept any code of the transport set where the cause is a drop.) An error built for a failure is raised OUTSIDE the `except` block that caught the library error: `__cause__` and `__context__` are `None` (finding F-B2 of Phase 3; the library exception can carry the response, and the response the request).

### 5.2 `tracer_evalsim.ws`

```text
ConnectionLabels(auth_token_env: str | None, origin: str | None, bot_holds_telephony_credentials: bool | None)   # frozen, no defaults
    auth_token_env: [A-Z_][A-Z0-9_]{0,63}  else TargetRefusedError(auth_env_name_invalid)
    origin: (?:https?|wss?)://[a-z0-9.-]+(?::[0-9]{1,5})?   else origin_invalid     (the ADR 0027 pattern: lower case, no path, no query, no user information)
    as_profile_connection() -> {"auth_token_env", "origin", "bot_holds_telephony_credentials"}   # exactly the ADR 0027 `connection` object
WebSocketTarget(url: str, labels: ConnectionLabels)
BearerToken     # opaque: __slots__, repr/str/format "BearerToken(<redacted>)", copy/deepcopy/pickle raise TypeError, immutable;
                # only authorization_header_value() -> "Bearer <token>" gives the value out
read_bearer_token(labels, *, environ: Mapping[str, str]) -> BearerToken | None      # environ is required (no ambient os.environ)
check_connection_labels(dialect_id, labels) -> None
prepare_connection(dialect_id, labels, *, environ) -> BearerToken | None            # check_connection_labels FIRST, then the key read
parse_target_url(url, *, schemes: tuple[str, ...]) -> ParsedTarget(scheme, host, port, path, loopback)
async open_websocket(target, dialect, *, token: BearerToken | None, open_timeout_s: float, close_timeout_s: float,
                     ssl_context: ssl.SSLContext | None = None) -> WebSocketConnection        # the two timeouts have no default
WebSocketConnection: send, recv, close(code=1000), close_info  (the WireConnection of section 3)
```

Rules, each pinned:

* Token. `read_bearer_token`: no variable named -> `None` without reading the environment; named but missing or empty -> `auth_token_missing`; not `[A-Za-z0-9._~+/-]+=*` or longer than 4,096 characters -> `auth_token_invalid` (RFC 6750 `b64token`; a space, a line break, a quote or a non-ASCII character could split or inject a header). No error, repr, label or profile document holds the value (the NAME only). The token is sent only as an `Authorization: Bearer <token>` header of the handshake: not in the path, not in a query, not in any other header, not in a subprotocol, not in a cookie. `open_websocket` refuses a token for an entry that names no variable (`labels_inconsistent`) and an entry that names a variable but gets no token (`auth_token_missing`).
* Origin. The header is exactly `labels.origin` (one header); without it NO `Origin` header is sent.
* Order. `prepare_connection` runs the label checks BEFORE it reads the environment: a Twilio entry with `bot_holds_telephony_credentials=true` is refused without one environment lookup (step 2 before step 6).
* Labels per dialect: `twilio-media-stream/1` needs the field: `None` -> `bot_credentials_field_missing`, `True` -> `bot_holds_telephony_credentials` (refused in Phase 4), `False` passes. `pipecat-protobuf/1` and `smallwebrtc/1` need it `None` (`labels_inconsistent` otherwise). An unknown dialect id -> `dialect_unknown`.
* Target URL. ASCII, printable, no space; no `?`, no `#`, no user information (`@` in the authority), port 1..65535 (default 80/443), a non-empty host: else `target_invalid`. The path is kept byte for byte (no percent-decoding; an empty path is `/`). Loopback means exactly the written names `localhost`, `127.0.0.1`, `::1` (`127.0.0.2` and `::ffff:127.0.0.1` are NOT loopback here: ADR 0026 rule 16). `ws://` (or `http://`) to anything else is `insecure_target`; `wss://` and `https://` pass the URL rule for every host. All of it happens before a socket and before a DNS query (the conftest guard proves it). The allowlist's own normalisation and its exact matching, and the rule that a resolved host name must not point to a private address (DNS rebinding), are P4-31 (batch G), not here; this layer is the defence in depth.
* TLS. `wss://` verifies the certificate chain and the host name with `ssl.create_default_context()`; the optional `ssl_context` is a test seam (a context that trusts a throw-away certificate) and the CLI never passes one. A failed verification is `tls_failed`, the server never sees an HTTP request.
* Handshake outcomes (all `SetupFailedError`, facts only, the response body is never quoted): connection refused -> `connection_refused`; other `OSError` -> `connection_failed`; `open_timeout_s` exceeded -> `connect_timeout`; HTTP 3xx (301, 302, 303, 307, 308) -> `redirect_refused` with `http_status`, and the redirect target is never contacted; any other non-101 status -> `handshake_rejected` with `http_status`; an invalid HTTP reply -> `handshake_failed`.
* Connection hardening: no extension and no subprotocol offered (no `permessage-deflate`: no decompression bomb); `max_size = MAX_INBOUND_MESSAGE_BYTES` handed to the library so that a message over the cap closes the connection (close code 1009) while it is assembled; the environment's proxy settings are ignored; the library's own logging is switched off (observed, section 11).
* `WebSocketConnection.recv()`: returns `None` after the peer closed or the connection dropped and keeps returning `None`; raises `TransportError(code="message_too_large", close_code=1009)` when the library closed because of the cap. `close_info`: `CloseInfo(code, clean)` with `clean` true iff the peer sent a Close frame; a dropped TCP connection is `(1006, False)`; after the client's own `close(1000)` it is `(1000, True)`. `send()` after the close raises `TransportError(code="connection_closed")`. `close()` returns within `close_timeout_s` even if the server never answers.

### 5.3 `tracer_evalsim.dialects.twilio`: `TwilioMediaStreamDialect(*, counter: int)`

`counter` is an int (not bool) in 1..999,999,999, no default. `dialect_id = "twilio-media-stream/1"`, `wire_codec = "mulaw"`, `sample_rate_hz = 8000`, `frame_bytes = 160`. Identifiers: `account_sid = "tracer-sim-account-<n>"`, `stream_sid = "tracer-sim-stream-<n>"`, `call_sid = "tracer-sim-call-<n>"` (the fixed prefix and the counter; never the shape of a provider identifier, never 32 hexadecimal digits). The messages are WebSocket TEXT messages holding one JSON object (key order and spacing are not pinned):

```text
opening_messages() -> (connected, start)      # once; a second call is RuntimeError
  {"event":"connected","protocol":"Call","version":"1.0.0"}
  {"event":"start","sequenceNumber":"1","streamSid":S,"start":{"accountSid":A,"streamSid":S,"callSid":C,"tracks":["inbound"],
    "mediaFormat":{"encoding":"audio/x-mulaw","sampleRate":8000,"channels":1},"customParameters":{"tracer_synthetic":"true"}}}
encode_audio(frame, *, stream_ms)             # RuntimeError before opening and after closing
  {"event":"media","sequenceNumber":"<2,3,...>","streamSid":S,"media":{"track":"inbound","chunk":"<1,2,...>","timestamp":"<stream_ms>","payload":"<base64 of the 160 bytes>"}}
closing_messages() -> (stop,)                 # once, only after opening
  {"event":"stop","sequenceNumber":"<next>","streamSid":S,"stop":{"accountSid":A,"callSid":C}}
```

`sequenceNumber`, `chunk` and `timestamp` are strings (as Twilio's pages show them); the sequence counts every message after `connected`. Tracer never sends `mark` or `dtmf`. `customParameters` holds the one marker for the bot's operator and never a phone number. Receiving (`decode`): a binary message, invalid JSON, a non-object, an unknown or missing `event`, and the events `connected`, `start`, `stop`, `dtmf` (not agent-to-telephony messages) -> `None` (ignored and counted); `media` with a base64 string `media.payload` (strict: invalid base64 -> ignored; any size; empty allowed) -> `Decoded("audio", bytes)`; `clear` -> `Decoded("clear")`; `mark` with a string `mark.name` -> `Decoded("mark")` (without a name -> ignored). Marks are optional: counted when they come, never waited for, never echoed by the pinned tests (an echo is not pinned either way); the call ends by `hang_up` (`stop`, then the socket is closed) and never depends on the agent's reaction (BL-299). An agent `clear` is barge-in evidence with its arrival instant (`InboundStats.clear_arrival_ns`). The 20 ms frame is Tracer's own choice; nothing pins Twilio's payload size.

## 6. `tracer_evalsim.sender` and `tracer_evalsim.watchdog` (P4-20)

```text
sender.FrameRecord(line_index, frame_index, scheduled_ns, sent_ns, size);  .lateness_ns = sent_ns - scheduled_ns
sender.LineResult(line_index, frames_sent, stopped)
sender.split_frames(audio: bytes, *, sample_rate_hz: int, codec: str) -> list[bytes]
sender.PacedSender(clock, send_frame: Callable[[bytes], Awaitable[None]]):  .log -> tuple[FrameRecord, ...];
    async send_line(frames: Sequence[bytes], *, should_stop: Callable[[], bool] | None = None) -> LineResult
watchdog.Watchdog(clock, *, max_duration_ns: int, hang_up: Callable[[], Awaitable[None]], grace_ns: int = WATCHDOG_GRACE_NS):
    start(); async aclose(); .fired; .fired_at_ns; .deadline_ns
```

### 6.1 The pacing math

* A line is a sequence of whole 20 ms frames. Its ORIGIN is the clock reading when `send_line` starts (a pause between lines is silence, not lateness; each line has its own origin and its own `line_index`, which counts every call including an empty one). Frame `k` is SCHEDULED at `origin + k * 20 ms`; `FrameRecord.scheduled_ns` is always this original schedule.
* Frame 0 leaves at once. Frame `k >= 1` never leaves before its schedule: the sender sleeps until the (possibly moved) target with ONE `sleep_until_ns` per waiting frame and no polling.
* Backpressure: the write of frame `k` is awaited to completion before frame `k+1` is scheduled (at most one write in flight).
* A write that is late: with `target_k = anchor + k * 20 ms` (`anchor` starts at the origin) and `now` the clock when frame `k` is due: if `k >= 1` and `now >= target_k + 20 ms` (one whole frame late or more) the anchor moves by `now - target_k` and the frame leaves at `now`; every later frame follows the moved anchor. Below one frame of lateness the original schedule holds (the late frame leaves at `now`, the next one is on its original instant, so two frames can be less than 20 ms apart by less than one frame; this is the documented price of not accumulating drift). Hence the sender never catches up with a burst after a stall: after a 200 ms stall the next frames are 20 ms apart, and the lateness (180 ms) stays visible in the log. Exact vectors: `test_sender_does_not_burst_after_a_stall_it_moves_the_schedule` (`0 20 40 60 260 280 ...`), `test_sender_absorbs_a_lateness_below_one_frame` (`0 20 40 70 80 100 ...`), `test_sender_reanchors_exactly_at_one_frame_of_lateness` (`... 79 80 100` against `... 80 100 120`).
* `sent_ns` is the clock reading just before the write is called. A frame is logged only after its write completed (a failed write raises, is not logged, and propagates unchanged: the log is the truth of what was sent).
* `should_stop` (the kill-switch hook of P4-31, "within one frame"): called before every frame and once more directly before the write if the sender had to wait; if it returns true no further frame is sent and `LineResult(stopped=True)` reports the frames already sent. Cancelling the task propagates `CancelledError` and leaves the log with the completed writes; the sender can be reused.
* `split_frames`: `rate * 20 // 1000` samples per frame (2 bytes each for `pcm_s16le`, 1 for `mulaw`); the last partial frame is padded with digital silence (`0x00` in PCM, `0xff` in mu-law); an empty input gives `[]`; an unknown codec, a rate that is not a multiple of 50 or zero -> `ValueError`. What is sent is what the recorder records (batch A), padding included.

### 6.2 The watchdog

It knows a clock, a maximum duration and a `hang_up` coroutine function, and nothing else (`test_watchdog_module_depends_on_nothing_that_could_stall_it`: it imports only the standard library and `tracer_evalsim.clock/errors/limits`). `start()` records `deadline_ns = clock.monotonic_ns() + max_duration_ns + grace_ns` and schedules ONE sleep until it; at the deadline (not one nanosecond earlier) it sets `fired`, `fired_at_ns` and awaits `hang_up()` once. It fires whatever any other task is doing: a stalled state machine, a sender blocked on a write. `aclose()` cancels it if it has not fired, is idempotent, and raises `WatchdogError` (facts only, `__cause__` None) if `hang_up` raised, so a failed hang-up is never silent. `max_duration_ns` must be a positive int (not bool); `start()` twice is `RuntimeError`; `max_duration_ns` and `hang_up` are required. Not covered, by construction of asyncio: a task that blocks the whole event loop with synchronous code; the transports contain none, and a blocking call in the runner is a defect (recommendation in section 12).

## 7. `tracer_evalsim.webrtc` (P4-18; the `[webrtc]` extra)

```text
DIALECT_ID = "smallwebrtc/1"   SPEC_SOURCES (the ADR's three URLs)   SPEC_RETRIEVED = "2026-10-27"   OFFER_SUFFIX = "/api/offer"
parse_ice_server(text) -> str          # stun:, stuns:, turn:, turns: + host[:port] [?transport=udp|tcp]; no credentials, no '@', lower case; else ice_server_invalid
make_webrtc_plan(offer_url, ice_servers) -> WebRtcPlan(offer_url, ice_servers: tuple[str, ...], loopback: bool)
load_aiortc() -> module                # TargetRefusedError(webrtc_extra_missing) with the fixed hint "install the optional extra: tracer-evalsim[webrtc]"
build_rtc_configuration(plan) -> aiortc.RTCConfiguration      # iceServers is an EMPTY LIST when the plan has none, never None
offer_request_body(sdp, *, pc_id=None, restart_pc=None, request_data=None) -> dict
patch_request_body(pc_id, candidates: Sequence[(candidate, sdp_mid, sdp_mline_index)]) -> dict
parse_answer(payload) -> Answer(sdp, type, pc_id)              # strict; SetupFailedError(answer_invalid)
HttpSignalling(plan, *, labels, token, timeout_s, ssl_context=None): async post_offer(body) -> dict; async patch_candidates(body) -> dict
```

* The target is the signalling URL itself, used exactly as allowlisted for both calls: http or https, no query, no user information, the path ends with `/api/offer` (the proxy form `/sessions/{id}/api/offer` is accepted), plain `http` only for the three loopback names. A non-loopback target needs at least one explicit ICE server (`ice_server_required`); the servers are the plan's and are what the call log lists (no credentials; a URL with `user:pw@` is refused). No default STUN server anywhere: `iceServers=[]` (observed: aiortc turns `None` into `stun:stun.l.google.com:19302`, which would disclose the host's addresses to a third party; `test_webrtc_no_stun_by_default` checks the configuration, aiortc's own gatherer built from it, the control case of `None`, and that no source file names that server).
* `POST /api/offer`, JSON body `{"sdp": <str>, "type": "offer"}` plus, only when given, `pc_id`, `restart_pc` (a `False` is sent) and `request_data`; `PATCH /api/offer`, body `{"pc_id": <safe id>, "candidates": [{"candidate", "sdp_mid", "sdp_mline_index"}]}`. These are the fields of ADR 0026 (`SmallWebRTCRequest`, `SmallWebRTCPatchRequest` at `v1.14.0`); the test reads the field names from the ADR text. The reply is `{"sdp", "type": "answer", "pc_id"}` (extra keys ignored); a non-answer, an empty or over-65,536-byte SDP, a missing or unsafe `pc_id` is `answer_invalid`.
* `HttpSignalling` uses the standard library only (`http.client` in a thread), sends `Content-Type: application/json`, the `Authorization: Bearer` header only if a token was given, the `Origin` header only if the entry has one, never follows a redirect (`redirect_refused`, the second server is not contacted), maps a non-2xx to `handshake_rejected` with the status, refuses a reply over 131,072 bytes or non-JSON or a non-object (`answer_invalid`), and maps refusal, timeout and TLS failure to `connection_refused`, `connect_timeout`, `tls_failed`.
* Without the extra: `load_aiortc()` raises the refusal above; `tracer_evalsim.webrtc` imports without `aiortc` and without `av` (tested in a fresh interpreter with both blocked). `test_webrtc_no_stun_by_default` skips cleanly when `aiortc` is not installed (a `skipif` marker on `importlib.util.find_spec("aiortc") is None`; `pytest.importorskip` is NOT usable: the earlier test `test_no_test_skips_or_guards_on_a_missing_jsonschema` flags every call to it in any test file, found by the full-suite run of the oracle); every other test of the file runs without the extra, and the run with `aiortc` and `av` blocked (`oracle-without-webrtc-extra.py3.12.log`) gives 30 passed and 1 skipped.
* Not here: a media round trip with aiortc. ICE host candidates are gathered from every interface of the machine, which a loopback-only guard cannot confine; the loopback proof of WebRTC is `test_integration_webrtc_against_pipecat_loopback` (P4-27, against `tools/samplebot`). Not pinned (ADR 0026 "not verified"): the codec, the rate, whether audio needs the client's data channel, whether a client whose offer holds all candidates needs the PATCH route.

## 8. Names, documents and licences (P4-16 to P4-18)

* Modules of `packages/evalsim/src/tracer_evalsim`: `__init__`, `errors`, `limits`, `clock`, `sender`, `watchdog`, `ws`, `webrtc`, `dialects/{__init__,base,protobuf_wire,protobuf_dialect,twilio}`. No file name contains `pipecat`, `_pb2`, `.proto` or `.pyi` (the scaffold test `test_evalsim_wheel_contains_no_tests` already refuses `pipecat` in a wheel member name; this is why the dialect module is `protobuf_dialect`, and the class keeps the name `PipecatProtobufDialect`). The only imports are the standard library, `websockets`, `aiortc` (lazy, inside a function), `tracer_evalschema` and `tracer_evalcore` (`test_evalsim_runtime_imports_whitelist`, unchanged).
* `test_protobuf_codec_is_hand_written_no_generated_or_pipecat_module`: no tracer package contains a generated-code marker (`Generated by the protocol buffer compiler`, `DO NOT EDIT`, `_descriptor_pool`, `frames_pb2`, `pipecat.frames.protobufs`), a `*_pb2*` or `*.proto` file, or an import of `google`, `pipecat`, `protobuf` or `grpc` (a computed import name is an offence in `tracer-evalsim`); importing the codec and both dialects in a fresh `python -I` loads none of those modules; no `pyproject.toml` of `packages/` mentions `protobuf`; none of the batch D tests imports `google`; and the scan itself flags 12 violating snippets and passes 2 ordinary ones.
* `test_dialect_spec_cites_docs_with_retrieval_date`: ADR 0026 "Dialect specifications" has exactly the three subsections of `simtypes.DIALECTS`, each with an `https://` source, a retrieval date and the word "retrieved"; Pipecat sources are at a tag (`/v1.14.0/`), never `main`; each dialect states `spec_sources` (every URL occurs in its ADR subsection) and `spec_retrieved` (that date occurs in it); the section still contains "not verified". The unverified items are not pinned.
* `test_notice_carries_pipecat_bsd2_attribution`: the seven NOTICE files (the root one and the six packages') stay byte-identical (earlier tests pin that) and all contain `pipecat-ai/pipecat`, `frames.proto`, `v1.14.0`, `Copyright (c) 2024-2026, Daily`, `BSD-2-Clause`, `hand-written` and the sentence "No Pipecat source file is copied" (case-insensitive). The oracle's text, to copy:

```text
- Wire format facts of the Pipecat protobuf dialect: the names, types and field numbers of the
  five frame messages of `src/pipecat/frames/frames.proto` at tag v1.14.0 of
  https://github.com/pipecat-ai/pipecat (BSD-2-Clause, Copyright (c) 2024-2026, Daily) are
  recorded in docs/adr/0026-simulated-caller-architecture.md and implemented by a hand-written
  encoder and decoder (tracer_evalsim/dialects/protobuf_wire.py). No Pipecat source file is
  copied, nothing is generated from the `.proto` file, and no Pipecat module is imported.
```

* 3.5 `packages/evalsim/CHANGELOG.md` must state the two caps, each as a line that has the constant name followed by its value before any other digit, e.g. ``- `MAX_INBOUND_MESSAGE_BYTES`: 65536 bytes per inbound message ...`` and ``- `MAX_INBOUND_MESSAGES_PER_SECOND`: 1000 ...`` (ADR 0026 rule 22: "stated in its CHANGELOG"; `test_inbound_caps_are_named_constants_and_stated_in_changelog`).

## 9. Named spec changes (before and after in `spec-change.diff`; tightening or enabling only, no test weakened, skipped or deleted)

```text
S-D   packages/evalcore/tests/evalcore_phase4_adr_support.py : the pending set PENDING["0026"] loses 12 names (deletions only):
      test_dialect_spec_cites_docs_with_retrieval_date, test_protobuf_dialect_roundtrip_in_memory,
      test_protobuf_dialect_unknown_message_ignored_and_counted, test_twilio_dialect_agent_clear_message_is_barge_in_evidence,
      test_twilio_dialect_ids_are_synthetic_and_marked, test_twilio_dialect_message_sequence_exact,
      test_webrtc_extra_absent_gives_clear_refusal, test_webrtc_no_stun_by_default, test_webrtc_signalling_body_matches_pinned_pipecat,
      test_fake_agents_are_deterministic, test_sender_paces_real_time_with_injected_clock,
      test_watchdog_hangs_up_even_if_state_machine_stalls     (new size of PENDING["0026"]: 57 -> 45)
S-D1  packages/evalsim/tests/conftest.py : the no-network fixture installs the loopback-only guard for tests that carry the `loopback`
      marker, registers the marker, and is otherwise byte-for-byte the old fixture (its docstring says where the exemption is)
```

The 10 added tests of the 12 that the BL-298..301 amendment names and that land in these rows (`test_protobuf_codec_is_hand_written_...`, `test_notice_carries_...`, the three bearer-token tests, `test_origin_header_...`, the four Twilio ones; the other two, the sample-bot and the allowlist-fields tests, belong to P4-26 and P4-31) are cited by no ADR and are in no pending set, so nothing else changes. `evalsim_support_*.py`, `evalsim_loopback_guard.py` and the 13 test files are new files.

## 10. Test inventory

10.1 SCOPE names (EXACTLY these 22: P4-16 three plus the six added on 2026-10-27, P4-17 three plus four, P4-18 three, P4-19 one, P4-20 two; the sample-bot and allowlist names of the same amendment belong to P4-26 and P4-31):

| Task | Name | File |
|---|---|---|
| P4-16 | `test_protobuf_dialect_roundtrip_in_memory` | `test_evalsim_pipecat_protobuf.py` |
| P4-16 | `test_protobuf_dialect_unknown_message_ignored_and_counted` | `test_evalsim_pipecat_protobuf.py` |
| P4-16 | `test_dialect_spec_cites_docs_with_retrieval_date` | `test_evalsim_dialect_policy.py` |
| P4-16 | `test_protobuf_codec_is_hand_written_no_generated_or_pipecat_module` | `test_evalsim_dialect_policy.py` |
| P4-16 | `test_notice_carries_pipecat_bsd2_attribution` | `test_evalsim_dialect_policy.py` |
| P4-16 | `test_bearer_token_from_env_never_stored` | `test_evalsim_connection_labels.py` |
| P4-16 | `test_bearer_token_only_in_authorization_header_never_path_or_query` | `test_evalsim_websocket_loopback.py` (loopback) |
| P4-16 | `test_missing_auth_token_env_refused_before_socket` | `test_evalsim_connection_labels.py` |
| P4-16 | `test_origin_header_from_entry_or_absent` | `test_evalsim_websocket_loopback.py` (loopback) |
| P4-17 | `test_twilio_dialect_message_sequence_exact` | `test_evalsim_twilio.py` |
| P4-17 | `test_twilio_dialect_ids_are_synthetic_and_marked` | `test_evalsim_twilio.py` |
| P4-17 | `test_twilio_dialect_agent_clear_message_is_barge_in_evidence` | `test_evalsim_twilio.py` |
| P4-17 | `test_twilio_target_with_bot_credentials_refused` | `test_evalsim_twilio.py` |
| P4-17 | `test_twilio_entry_without_credentials_field_refused` | `test_evalsim_twilio.py` |
| P4-17 | `test_twilio_call_ends_by_socket_close_after_stop` | `test_evalsim_websocket_loopback.py` (loopback) |
| P4-17 | `test_twilio_dialect_mark_optional_never_awaited` | `test_evalsim_twilio.py` |
| P4-18 | `test_webrtc_no_stun_by_default` | `test_evalsim_webrtc.py` (skips without aiortc) |
| P4-18 | `test_webrtc_signalling_body_matches_pinned_pipecat` | `test_evalsim_webrtc.py` |
| P4-18 | `test_webrtc_extra_absent_gives_clear_refusal` | `test_evalsim_webrtc.py` |
| P4-19 | `test_fake_agents_are_deterministic` (parametrised `protobuf`, `twilio`) | `test_evalsim_fake_agents.py` |
| P4-20 | `test_sender_paces_real_time_with_injected_clock` | `test_evalsim_pacing.py` |
| P4-20 | `test_watchdog_hangs_up_even_if_state_machine_stalls` | `test_evalsim_watchdog.py` |

Added by qa (not in the SCOPE table; each has a reason in its docstring) are all other tests of the 12 files, grouped: the codec (golden vectors, reference teeth, 400-frame property test, malformed wire, tolerance, range refusals); the dialects and the reader (caps, window, after-close, stats, end reasons); the labels, token and URL rules; the hostile servers; the pacing vectors and the watchdog independence; WebRTC plan, ICE and answer rules and the signalling client; the fake agents and the virtual clock; and the guard tests of S-D1 (27 items).

10.2 The five hard cases that the oracle found in the libraries and that tests pin (section 11): the bearer token in the library's DEBUG log, redirects followed by default, the proxy environment honoured by default, an extension offered by default, aiortc's default STUN server.

## 11. Observed library behaviour that shapes the implementation (websockets 17.3, aiortc 1.15.0)

1. `websockets` logs every request header at DEBUG on its own logger: `> Authorization: Bearer <token>` (observed). The client must pass a silent `logger=` (a `Logger` with `propagate=False`, a `NullHandler` and a level above CRITICAL); `test_bearer_token_never_in_any_log_record_or_output` runs with DEBUG on every logger. The test server of the support module is silenced the same way, otherwise the test could not tell whose log it reads.
2. `websockets.asyncio.client.connect` FOLLOWS redirects by default, also to another host and port (it strips `Authorization` and `Cookie` on a cross-origin redirect; observed in `process_redirect`). There is no `follow_redirects` option at 17.3 (passing one reaches `loop.create_connection` and raises `TypeError`). The oracle subclasses `connect` and overrides `process_redirect` to return the exception. `test_a_redirect_in_the_handshake_is_never_followed` (5 statuses) checks that the second server sees no connection.
3. `connect(proxy=True)` is the default and reads `WS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` and the system proxy settings; the oracle passes `proxy=None`. `test_the_proxy_environment_is_ignored_the_connection_goes_direct` sets all of them to a stand-in and checks it is never contacted.
4. `compression='deflate'` is the default (offers `permessage-deflate`); the oracle passes `compression=None`.
5. `max_size` is enforced while a (fragmented) message is assembled and closes with 1009; its default is 1 MiB, so the cap must be passed.
6. `aiortc.RTCConfiguration(iceServers=None)` (the default) means the Google STUN server; `iceServers=[]` means none (observed with `RTCIceGatherer`). `RTCPeerConnection` has no public `configuration` attribute, so the test checks the configuration object and the gatherer built from it.
7. A bare `RTCPeerConnection.setLocalDescription` gathers host candidates on every interface (observed 4 candidates): no such call is made in a unit test.

The lower bound `websockets>=15.0`: items 2, 3 and 4 name APIs (`process_redirect`, `proxy`, `compression`) that this batch could not verify at 15.0 (no network in the oracle; the lowest-direct leg could not be run). The first hosted run of that leg is the check. See section 12, ambiguity 3.

## 12. Evidence

`docs/phase4/evidence/red/batch-d/` (every log under 5,000 lines, `/Users/...`, `<session>`, `pytest-of-<user>` and macOS temp paths scrubbed):

```text
red.py3.11.log, red.py3.12.log, red.py3.13.run1.log, red.py3.13.run2.log, red.py3.14.log
    the staged files at their final path in a mirror of the CURRENT tree (the scaffold of commit d940404; environments synced
    from the current lock): the whole packages/evalsim/tests directory plus the three ADR readers of evalcore that use the pending
    set (406 items): 298 failed, 107 passed, 1 skipped (the opt-in gate test) on every version, 3.13 twice identical
red-reasons.py3.13.txt        the reason of each of the 298 failures: a missing tracer_evalsim module or attribute, or the missing NOTICE and
                              CHANGELOG text; no failure is an import error of a test itself; the 107 passes hold on any tree (the teeth tests
                              of the reference encoder and the scan, the harness tests, the guard tests, the earlier scaffold and ADR tests)
oracle.py3.11.log .. oracle.py3.14.log      the same 406 items on the oracle (minimal implementations of sections 2 to 7): 405 passed, 1 skipped
oracle-full-suite.py3.11.log .. oracle-full-suite.py3.14.log     the whole workspace on the oracle (counts.txt)
oracle-without-webrtc-extra.py3.12.log      test_evalsim_webrtc.py with aiortc and av blocked: 30 passed, 1 skipped
mutants.txt                   149 mutants of the oracle and of the test infrastructure, killed or survived, with the reason of each survivor
flaky-check.txt               12 repeated runs of the 76 loopback tests while three other test suites ran on the machine
oracle-src.patch              the oracle's non-test changes (lint-, format- and mypy-clean): a starting point for the dev, not a substitute
spec-change.diff              the two named spec changes of section 9, before and after
counts.txt                    the numbers per Python version
```

How the evidence was built: a scratch copy of the tree (without `.git`, `.env.local`, the private fixtures and `tools/capture`), a `git init` in it (31 earlier tests call `git check-ignore`), one uv environment per Python version synced `--locked --all-groups --offline` from the current lock (`websockets` 17.3, `aiortc` 1.15.0), one fresh `PYTHONPYCACHEPREFIX` per run. Not run, for lack of network: the lowest-direct leg (`websockets` 15.0.x), the Postgres legs, Python 3.15.

## 13. Ambiguities, with recommendations (the lead auto-approves)

1. **The "8-state call machine runner" is not in rows P4-16 to P4-20.** The state machine is P4-04 (pure, `tracer-evalcore`, batch A) and the asyncio runner that drives it is orchestration (P4-21). No batch D row names a runner test, and the batch A API does not exist yet, so nothing here pins it. What batch D pins instead are the building blocks the runner needs and their mapping to the 11 end reasons: `SetupFailedError.END_REASON == "setup_failed"`, `TransportError.END_REASON == "transport_error"`, `InboundEnd.end_reason` (`agent_hangup` or `transport_error`), and the watchdog / `hang_up` / `should_stop` hooks. Recommendation: the runner tests are written with batch E (P4-21) against the batch A machine; the end reasons `max_duration` (watchdog fired), `kill_switch` (`should_stop`) and `max_silence` are set by the runner, not by these pieces.
2. **Pacing has no wording for "never bursts" after a stall.** The ADR gives the rule ("never sends ahead to catch up"), not the math. Recommendation (pinned, section 6.1): one origin per line, a lateness of one whole frame or more moves the schedule, a smaller lateness is absorbed. Rejected: a fixed minimum gap between sends of 20 ms (jitter would accumulate into permanent drift: each late wake-up would delay all later frames); a global origin for the call (a pause between lines would be counted as lateness).
3. **The lower bound `websockets>=15.0`** may not have `proxy=`, `process_redirect` or `compression=` as used here (observed at 17.3 only). If 15.0 lacks one, the floor rises (it is a one-line change in `pyproject.toml`, the scaffold test, the policy and the lowest-direct leg), or the dev reaches the behaviour another way. Recommendation: the dev checks the 15.0 sources first; the hosted lowest-direct leg is the proof.
4. **Two codes are reserved and never emitted by the oracle** (`connection_lost`, `send_failed`): a dropped connection is `connection_closed` on a send and `(1006, False)` on a read. Recommendation: keep the names (a later transport, such as the SIP media leg of P4-41, may use them); the tests accept any code of the transport set for a drop.
5. **NOTICE is a byte-identical copy in seven places** (earlier tests), and one of them is `packages/evalschema/NOTICE`. The attribution edit therefore changes a file of `tracer-evalschema` that is NOT part of the baseline hashes (`schema.lock`, the source tree under `src/`, `pyproject.toml`) but is part of its sdist and wheel metadata. Recommendation: accept (NOTICE is not schema code and the baseline test is unaffected); rejected: a named spec change that lets `packages/evalsim/NOTICE` differ from the others (it would loosen six identical-copy tests).
6. **A close with code 4003 right after the connect** (the Pipecat runner's answer to a bad token or Origin) is reported as a clean close with its code; whether such a call is `setup_failed` or `agent_hangup` is the runner's decision. Recommendation for P4-21: a close before any caller frame was sent and before the agent produced any message is `setup_failed` (the call was never established in the sense of ADR 0027 rule 2), with the close code in the call log.
7. **Twilio `customParameters`.** The ADR requires synthetic ids "marked as such"; the contract reads that as the `tracer-sim-` prefix plus `customParameters == {"tracer_synthetic": "true"}` so the bot's operator can see it. Pipecat promotes only `from_number` and `to_number` out of `customParameters`, so the marker is inert for it. Recommendation: accept; rejected: no marker (the prefix alone would not appear in an agent's logs that print only the body).
8. **The Pipecat `ws_client` token also travels as `?token=` and in the path** (ADR 0026 rule 16 refuses both). A runner that accepts the token only there cannot be called. Pinned: the target URL has no query and the token is only in the header. Recommendation: unchanged.
9. **The watchdog is an asyncio task.** It covers a stalled machine and a blocked write. A synchronous call that blocks the loop would stall the watchdog too. Recommendation: no blocking call in the runner (a lint rule in P4-21 or a review item in P4-32); a watchdog thread is possible later if the audit finds a blocking path (it would need a thread-safe hang-up, which the transports do not have today).
10. **Frame log and the recorder.** `FrameRecord.sent_ns` is the instant before the write; the recorder (P4-06) places caller frames at their "exact send time". Recommendation: P4-06 uses `sent_ns` of the log; the instant after the write is not recorded.
11. **WebRTC media.** No media round trip is tested in batch D (section 7). Recommendation: P4-27's WebRTC test is the proof; the extra's `RTCPeerConnection` use stays untested here by design, and the dev must not add a unit test that gathers candidates.
12. **`hang_up` swallows only `TransportError`.** Any other exception from a send propagates and the close is not attempted. Recommendation: unchanged (an unexpected exception is a defect to see).
13. **`pytest.importorskip` is refused by an earlier test** (`test_no_test_skips_or_guards_on_a_missing_jsonschema` flags the call in any test file, not only for `jsonschema`). The "skip cleanly without the extra" is therefore a `skipif` on `find_spec`. Recommendation: unchanged; the lead may later narrow that earlier test to `jsonschema` (a named spec change) if `importorskip` is wanted elsewhere.
