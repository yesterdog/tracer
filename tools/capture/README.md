<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The Tracer contributors -->
# tools/capture

Capture harness for the real Pipecat samples (SCOPE P1-28, ADR 0018). It is a standalone uv
project with its own `pyproject.toml` and `uv.lock`; it is not a member of the repository
workspace and `tracer-evalschema` never depends on it.

It pins `pipecat-ai==1.13.0` and runs a local pipeline: Whisper small (STT), Qwen2.5-1.5B-Instruct
4-bit behind a loopback `mlx_lm.server` (LLM) and Kokoro (TTS) on a scripted transport, with a
synthetic caller voiced by Kokoro. No network call, no API key. Apple Silicon only (MLX).

    ./capture.sh                       # all scenarios
    cd tools/capture && uv run python -m tracer_capture.run \
        --scenario scenarios/en.yaml --out <dir> --scratch <dir>

Per scenario it writes `f1_spans.otlp.json`, `f4_conversation.json`, `f6_stereo.wav`,
`f7_turn_events.jsonl` and `provenance.json` (versions, model licences and sha256 of every model
file, command, sha256 of every output). Captures are not bit-reproducible: LLM and VAD timing vary.

First run downloads the pinned model files (public, unauthenticated). Model files are never
committed.

## Sarvam STT (F3)

`tracer_capture.sarvam_capture` calls the Sarvam REST API (BYOK, key read from a git-ignored dotenv
file into memory only) with synthetic clips and stores raw responses under
`packages/evalschema/tests/fixtures/real/sarvam-<date>/`. Every call is logged to
`sarvam_call_log.jsonl` (no key); the cap is 60 calls in total. Findings:
`SARVAM_OBSERVATIONS.md`.

    cd tools/capture && uv run python -m tracer_capture.sarvam_capture --env-file ../../.env.local
