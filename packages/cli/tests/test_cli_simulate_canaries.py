# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-24 / P4-30 (the command's half): the key canary across every output channel of a simulated
run, and the authorisation reference as a hash.

Contract: docs/phase4/api-surface-batch-e.md section 12.2. The reusable check
(``assert_sim_three_keys_never_in_outputs`` in ``cli_p4e_support``) is also called at the end of the
Phase 3 test ``test_key_never_in_store_and_report_outputs`` (named spec change S-E4), which SCOPE
P4-24 extends "to three keys".
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    assert_sim_three_keys_never_in_outputs,
)


def test_three_provider_roles_one_key_and_a_bearer_token_never_leave_through_any_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert_sim_three_keys_never_in_outputs(tmp_path / "sim", caplog)
    assert logging.getLogger().level == logging.DEBUG
