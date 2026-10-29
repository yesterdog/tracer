# Changelog: `tracer-evalsim`

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. The package (P4-01) holds the wire dialects, the call runner, the target safety files and the offline fake agent of `tracer simulate`; the pure engine boundaries (TTS, LLM, caller brain) live in `tracer-evalcore`. `tracer-cli` is its only caller. The package README is `packages/evalsim/README.md`, the offline quick start is in `packages/cli/README.md` and the concept page is `docs/concepts/simulated-calls.md`.

First version. Everything below is new.

### Package

- Dependencies: `tracer-evalschema` and `tracer-evalcore` (exact workspace pins) and `websockets>=15.0,<18`. The optional extra `webrtc` adds `aiortc>=1.15.0,<2`, which installs a third-party wheel (`av`) that bundles FFmpeg with GPL and LGPL parts; the conditions of use are in `docs/security/P4-02-licenses.md`. The package is the only importer of `websockets` and `aiortc`.
- Errors under one base of the package; they hold facts only (a code, a count), never a payload, a header or a secret.

### Wire dialects

- Three named, versioned dialects, each written by hand from public documentation: `pipecat-protobuf/1` (the Pipecat development runner's `/ws-client`, with a hand-written protobuf codec for its five frames), `twilio-media-stream/1` (a Twilio-compatible media stream) and `smallwebrtc/1` (WebRTC signalling and audio; `aiortc` is imported only when the dialect is used). A change of a message, a field or a meaning is a new version. The dialect id labels every timing the run reports.

### Call runner

- The asyncio call runner follows the call state machine of `tracer-evalcore`: the paced sender (20 ms frames in real time, backpressure, never a burst), the wall-clock watchdog that is independent of the state machine, and an injected clock that the tests replace with a virtual one. The line providers give the runner the caller brain as it sees it.
- `LlmLines` ends the call with the new end reason `voice_failed` when the caller's speech synthesis fails (BL-369), whether the engine returns the failure or raises it, with the typed facts `voice_failure_code` (seven closed codes in `VOICE_FAILURE_CODES`) and the HTTP status; a character cap is `spend_cap` on both paths (a raised cap used to be `transport_error`), and the brain's counts are kept on both paths.
- The runner reads the optional `activity_min_peak` of the turn-taking policy (BL-313) for its own activity rule, so the caller sees the pauses of an agent that streams a noise floor; the recording keeps every sample.

### Target safety

- The target allowlist reader (an exact entry, a file of mode 0600 that is not a link, checked before any key is read, with the name of a target resolved and the answers checked), the kill-switch file `kill-switch` in `--out` that hangs up within one frame, and the lock file `call.lock` that keeps a minimum interval between two calls.

### Fake agent

- The offline agent of `tracer simulate --agent fake:FILE`: a JSON file `tracer.fake_agent/1` of timed steps (waits, speech, digital silence, comfort noise, a hang-up, a dropped connection) that the simulator talks to over an in-memory connection on the injected clock, with no socket. It reacts to nothing the caller says; the `text` of a speech step is what the file's author says the agent speaks, and `--stt fake` returns it, so it is never a recognition result.

### Documentation

- `packages/evalsim/README.md` describes the package, its parts and what it does not do; `docs/concepts/simulated-calls.md` says what a simulated call reports and does not claim; the inputs of the offline quick start are in `docs/examples/simulated-mr`.

## Dialect transports (P4-16 to P4-20)

Technical bounds of ADR 0026 rule 22 (not measured values):

- `MAX_INBOUND_MESSAGE_BYTES`: 65536 bytes per inbound message (text measured in UTF-8 bytes); a
  larger message ends the call with `transport_error`.
- `MAX_INBOUND_MESSAGES_PER_SECOND`: 1000 inbound messages in any sliding second; more ends the
  call with `transport_error`.
- `WATCHDOG_GRACE_NS`: one second added to the maximum call duration before the watchdog hangs up.
