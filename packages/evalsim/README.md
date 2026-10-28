# tracer-evalsim

The simulated caller of Tracer (Phase 4): the wire dialects and the asyncio call runner that `tracer simulate` drives. Status: 0.0.1, not released. Dependencies: `tracer-evalschema` and `tracer-evalcore` (exact workspace pins) and `websockets>=15.0,<18`. The optional extra `webrtc` adds `aiortc>=1.15.0,<2`, which installs a third-party wheel (`av`) that bundles FFmpeg with GPL and LGPL parts; the conditions of use are in `docs/security/P4-02-licenses.md`. Changes are in [CHANGELOG.md](CHANGELOG.md).

This package is the part of the simulator that touches a connection. It places one call from a synthetic caller to an agent under test, sends the caller's audio in real time in 20 ms frames, records what the agent sends back, and hands the recording and the instants to `tracer-evalcore`, which builds and scores the run. It does not evaluate, score or judge anything itself, and it never evaluates the caller: only the agent is evaluated. The command line, the inputs and the offline quick start are in [packages/cli/README.md](../cli/README.md); what a simulated call reports and does not claim is on the concept page [simulated calls](../../docs/concepts/simulated-calls.md) (`docs/concepts/simulated-calls.md`).

## What is in it

| Part | What it does |
|---|---|
| Dialects (`tracer_evalsim.dialects`) | Three named, versioned wire dialects, each written by hand from public documentation: `pipecat-protobuf/1` (the Pipecat development runner's `/ws-client`), `twilio-media-stream/1` (a Twilio-compatible media stream) and `smallwebrtc/1` (signalling and an audio track). A change of a message or a field is a new version. Every timing is labelled with the dialect it was observed over. |
| Call runner (`runner`, `sender`, `watchdog`, `clock`) | The asyncio runner that follows the call state machine of `tracer-evalcore`, the paced sender (real-time frames, backpressure, never a burst), the wall-clock watchdog and the injected clock that the tests replace. |
| Target safety (`allowlist`, `addresses`, `guard`) | The target allowlist (an exact entry, mode 0600, checked before any key is read, with the name resolved and checked), the kill-switch file `kill-switch` in `--out` that hangs up within one frame, and the lock file `call.lock` that keeps a minimum interval between calls. |
| Fake agent (`fakeagent`) | The offline agent of `tracer simulate --agent fake:FILE`: a JSON file of timed steps (waits, speech, silence, comfort noise, a hang-up) that the simulator talks to over an in-memory connection, with no socket. It reacts to nothing the caller says, and the `text` of a speech step is what the file's author says the agent speaks, not a recognition result. |
| Bounds (`limits`) | Technical bounds, not measured values: the largest inbound message, the most inbound messages in a second and the watchdog grace. A hostile agent cannot exhaust the simulator by flooding it; the values are in the CHANGELOG. |

The caller's lines come from the scripted brain or from a language model (`tracer-evalcore`), and the speech and the transcripts come from the engines of `tracer-evalproviders`; none of them is imported here except through the boundaries of `tracer-evalcore`. This package is the only importer of `websockets` and `aiortc`.

## What it does not do

- It does not dial a telephone number. Telephony is moved to a later stage (BL-311).
- It does not run a batch or several calls at once: one call per invocation, concurrency 1 (BL-276).
- It does not decide whether an agent did what a test asked: the outcome is always "not evaluated".
- It opens no socket in its unit tests. Tests that carry the `loopback` marker connect to the loopback interface only, under a guard that refuses every other address.

## Tests

`uv run --locked pytest -q packages/evalsim/tests` runs the package offline. A real call is never a test: it is an owner-gated step with its own run book (ADR 0026).
