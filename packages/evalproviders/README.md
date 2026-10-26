# tracer-evalproviders

Speech-to-text provider adapters for Tracer. Bring your own key; nothing runs locally. The package
talks to providers over HTTP with `httpx` (`httpx>=0.28.1,<1`). Status: 0.0.1, not released; the Sarvam speech-to-text adapter is implemented and made its first real
requests on 2026-10-26, under the run book of ADR 0024 and the owner's key (Phase 3 P3-30 and
P3-31): 124 requests in eight runs (11, 11, 11, 11 and 15 for the first clip, 20, 20 and 25 for
the second), every one answered on its first attempt, so no retry happened and the billing of
retries is unverified. Counts only are in `docs/phase3/measurements/2026-10-26-noise-floor.md`. The rules
every adapter follows are in `docs/adr/0024-provider-adapters-byok-sarvam-rest.md`; changes are in
`CHANGELOG.md`.
