#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# Re-capture every scenario into the fixture tree. Run from anywhere; needs ~3 GB of cached models.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="$root/packages/evalschema/tests/fixtures/real/pipecat-1.13.0"
scratch="${TMPDIR:-/tmp}/tracer-capture-scratch"
for scenario in en en_tool hi; do
  (cd "$here" && uv run python -m tracer_capture.run \
    --scenario "scenarios/$scenario.yaml" --out "$out/$scenario" --scratch "$scratch/$scenario")
done
