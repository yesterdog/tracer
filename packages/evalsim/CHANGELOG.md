# Changelog: `tracer-evalsim`

## 0.0.1 (unreleased)

Status: not released. No version tag exists and nothing is published to PyPI. Scaffold (P4-01):
the package, its error base and the optional extra `webrtc`. The pure engine boundaries (TTS, LLM,
caller brain) live in `tracer-evalcore`.

## Dialect transports (P4-16 to P4-20)

Technical bounds of ADR 0026 rule 22 (not measured values):

- `MAX_INBOUND_MESSAGE_BYTES`: 65536 bytes per inbound message (text measured in UTF-8 bytes); a
  larger message ends the call with `transport_error`.
- `MAX_INBOUND_MESSAGES_PER_SECOND`: 1000 inbound messages in any sliding second; more ends the
  call with `transport_error`.
- `WATCHDOG_GRACE_NS`: one second added to the maximum call duration before the watchdog hangs up.
