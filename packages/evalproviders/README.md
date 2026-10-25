# tracer-evalproviders

Speech-to-text provider adapters for Tracer. Bring your own key; nothing runs locally. The package
talks to providers over HTTP with `httpx` (`httpx>=0.28.1,<1`). Status: 0.0.1, not released; the Sarvam speech-to-text adapter is in progress (Phase 3). The rules
every adapter follows are in `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`; changes are in
`CHANGELOG.md`.
