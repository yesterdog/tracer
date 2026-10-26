# tracer-evalsim

Simulated caller for Tracer: wire dialects and the asyncio call runner (Phase 4). Status: 0.0.1,
scaffold only. Dependencies: `websockets>=15.0,<18`. The optional extra `webrtc` adds
`aiortc>=1.15.0,<2`, which installs a third-party wheel (`av`) that bundles FFmpeg with GPL and
LGPL parts; the conditions of use are in `docs/security/P4-02-licenses.md`.
