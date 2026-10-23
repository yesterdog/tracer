# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Real-sample mapping converters F1, F3, F4, F6 and F7 (test-side code, ADR 0018).

The converters turn the captured outputs of Pipecat and Sarvam into schema records without
inventing a value: a field is filled only from a source field or a documented derivation, and
every filled field has a trace entry. They are pure (no network, no clock, no environment, no file
write) and never raise on content. Contract: docs/phase1/api-surface-mappings.md.
"""
