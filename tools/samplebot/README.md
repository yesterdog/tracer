# tools/samplebot

Pipecat 1.14.0 sample agents for the loopback interop tests (P4-26, P4-27). Its own uv project:
not a member of the root workspace, not in the root `uv.lock`, not in any tracer wheel, never
imported by a tracer package. Its closure holds LGPL items (tool-only): see
`docs/security/P4-02-licenses.md`. The tool has its own lock (`uv.lock`, pinned `pipecat-ai==1.14.0`).

## scripted_bot

A Pipecat pipeline with no model: no speech-to-text, no text-to-speech, no LLM, no voice-activity or
turn model, no network client and no key. A timer on the arrival of active caller frames decides that
the caller has finished (`end_of_caller_ms` after the last frame that holds a non-zero byte; WebRTC
streams digital silence between lines and the WebSocket dialects send nothing); the replies are pure
sines of the scenario, started after known delays. It serves one dialect per process, calls one after
another:

| `--dialect` | route | transport and serializer |
|---|---|---|
| `pipecat-protobuf/1` | `ws://127.0.0.1:PORT/ws-client` | `FastAPIWebsocketTransport`, `ProtobufFrameSerializer` |
| `twilio-media-stream/1` | `ws://127.0.0.1:PORT/ws` | `FastAPIWebsocketTransport`, `TwilioFrameSerializer`, `auto_hang_up` off, no credential |
| `smallwebrtc/1` | `http://127.0.0.1:PORT/api/offer` | `SmallWebRTCTransport` through `SmallWebRTCRequestHandler`, no ICE server |

The Twilio-compatible route runs with `auto_hang_up` off and no account SID or auth token, so it never
sends a request to Twilio (ADR 0026 rule 6). The stream and call identifiers come from the `start`
message of the caller (`parse_telephony_websocket`), never from the bot. An allowlist entry for the
route says `bot_holds_telephony_credentials=false`. The bot binds loopback only (`--host` is
`127.0.0.1`, `::1` or `localhost`) and has no default for any value; `--port 0` picks a free port.
SIGTERM stops it with exit code 0.

    uv sync --locked --project tools/samplebot
    uv run --project tools/samplebot --locked python -m samplebot.scripted_bot \
        --dialect pipecat-protobuf/1 --scenario scenario.json --sample-rate 16000 \
        --host 127.0.0.1 --port 0

Run it from `tools/samplebot` (the tests do). Scenario file `tracer.samplebot_scenario/1` (strict JSON,
closed keys): `end_of_caller_ms` (20 to 5000), `replies` (1 to 64 of `{delay_ms, tone{hz, ms, level}}`;
reply `k` answers caller turn `k`) and `after_replies` (`stay_silent` or `hang_up`).

Stdout is one JSON object per line: `ready` once (`dialect`, `url`, `pipecat`; the Twilio-compatible
route adds `auto_hang_up` false and `credentials` false), `twilio_start` per Twilio-compatible call,
and `call_summary` per finished call (what the bot heard of each caller turn). Logs go to stderr.

The interop tests run with `uv run --locked --all-groups pytest -m integration packages/evalsim/tests`
(the `interop` job of `.github/workflows/ci.yml`); they fail, never skip, when this environment was
not built.
