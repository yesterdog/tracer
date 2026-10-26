# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, finding R5 of the P3-28 re-audit: an instant with hour 24 is refused on every Python.

Contract: docs/phase3/api-surface-batch-i.md section 4. Python 3.14 reads ``T24:00:00Z`` as the
start of the next day; 3.11 to 3.13 refuse it. The parse must not depend on the interpreter, so
it is strict at two levels: the pattern ``tracer_cli.cli._INSTANT`` (the same on every version)
and the converter ``_instant`` / the command line (the result). The accepted instants are stored
exactly: the stored UTC instant equals the input instant.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli_batche_support import price_file_text  # type: ignore[import-not-found,unused-ignore]
from cli_batchg_support import (  # type: ignore[import-not-found,unused-ignore]
    cli_mod,
    g_args,
    new_inputs,
    stored_privacy_markers,
)
from cli_batchi_support import Captured, run_capture  # type: ignore[import-not-found,unused-ignore]

HOUR_24 = [
    "2026-10-20T24:00:00Z",
    "2026-10-20T24:00:00+05:30",
    "2026-10-20T24:00:00-08:00",
    "2026-10-20T24:00:00.000000Z",
    "2026-10-20T24:00:00.5Z",
    "2026-10-20T24:00:01Z",
    "2026-10-20T24:30:00Z",
    "2026-10-20T24:59:59Z",
    "2026-10-20T24:00:00+00:00",
    "2026-12-31T24:00:00Z",
    "2026-02-28T24:00:00Z",
    "2026-10-20T25:00:00Z",
    "2026-10-20T99:00:00Z",
]
#: Out of range in the time or the offset: the pattern refuses them too (the same on every Python).
TIME_OUT_OF_RANGE = [
    "2026-10-20T10:60:00Z",
    "2026-10-20T10:00:60Z",
    "2026-10-20T10:00:00+24:00",
    "2026-10-20T10:00:00-24:00",
    "2026-10-20T10:00:00+05:60",
    "2026-10-20T10:00:00-05:60",
    "2026-10-20T10:00:00+05:99",
    "2026-10-20T10:00:00+99:00",
]
#: Out of range in the date: only the calendar can say (Feb 30), so the converter decides.
DATE_OUT_OF_RANGE = [
    "2026-13-20T10:00:00Z",
    "2026-00-20T10:00:00Z",
    "2026-10-32T10:00:00Z",
    "2026-10-00T10:00:00Z",
    "2026-02-30T10:00:00Z",
]
OTHER_OUT_OF_RANGE = TIME_OUT_OF_RANGE + DATE_OUT_OF_RANGE
VALID = [
    ("2026-10-20T00:00:00Z", datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC)),
    ("2026-10-20T23:59:59Z", datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC)),
    ("2026-10-20T10:30:00+05:30", datetime(2026, 10, 20, 5, 0, 0, tzinfo=UTC)),
    ("2026-10-20T00:00:00+05:30", datetime(2026, 10, 19, 18, 30, 0, tzinfo=UTC)),
    ("2026-10-20T23:59:59.999999-12:00", datetime(2026, 10, 21, 11, 59, 59, 999999, tzinfo=UTC)),
    ("2026-10-20T23:59:59+14:00", datetime(2026, 10, 20, 9, 59, 59, tzinfo=UTC)),
    ("2026-10-20T12:00:00.5+00:00", datetime(2026, 10, 20, 12, 0, 0, 500000, tzinfo=UTC)),
    ("2026-10-20T10:00:00+23:59", datetime(2026, 10, 19, 10, 1, 0, tzinfo=UTC)),
    ("2026-10-20T10:00:00-23:59", datetime(2026, 10, 21, 9, 59, 0, tzinfo=UTC)),
    ("2026-10-20T09:00:00Z", datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)),
]


def _usage_error(result: Captured, option: str) -> None:
    assert result.error is None
    assert result.code == 64, result.err
    assert result.err.startswith("usage: "), result.err
    assert "unrecognized" not in result.err, result.err
    assert option in result.err.split("tracer: error: ", 1)[1], result.err


# ============================================================ the pattern


@pytest.mark.parametrize("value", HOUR_24 + TIME_OUT_OF_RANGE)
def test_r5_the_pattern_refuses_an_hour_24_and_the_other_time_ranges_on_every_python(
    value: str,
) -> None:
    pattern = cli_mod()._INSTANT
    assert isinstance(pattern, re.Pattern)
    assert pattern.fullmatch(value) is None


@pytest.mark.parametrize(("value", "expected"), VALID)
def test_r5_the_pattern_accepts_a_valid_instant(value: str, expected: datetime) -> None:
    assert cli_mod()._INSTANT.fullmatch(value) is not None


# ============================================================ the converter


@pytest.mark.parametrize("value", HOUR_24 + OTHER_OUT_OF_RANGE)
def test_r5_the_converter_refuses_with_an_argument_type_error(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli_mod()._instant(value)


@pytest.mark.parametrize(("value", "expected"), VALID)
def test_r5_the_converter_returns_the_input_instant_in_utc(value: str, expected: datetime) -> None:
    got = cli_mod()._instant(value)
    assert got == expected
    assert got.utcoffset() == timedelta(0)
    assert got.tzinfo is not None
    # an independent reading of the same text
    match = re.search(r"([+-])([0-9]{2}):([0-9]{2})$", value)
    if match is not None:
        sign = 1 if match.group(1) == "+" else -1
        offset = timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))
        local = datetime.fromisoformat(value[: match.start()]).replace(tzinfo=offset)
        assert local.astimezone(UTC) == got


# ============================================================ the command line


@pytest.mark.parametrize("value", HOUR_24)
def test_r5_consent_recorded_at_with_hour_24_is_a_usage_error(tmp_path: Path, value: str) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs, options={"--consent-recorded-at": value}))
    _usage_error(result, "--consent-recorded-at")
    assert result.factory_calls == 0
    assert not inputs.out.exists()


@pytest.mark.parametrize("value", OTHER_OUT_OF_RANGE)
def test_r5_consent_recorded_at_out_of_range_is_a_usage_error(tmp_path: Path, value: str) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs, options={"--consent-recorded-at": value}))
    _usage_error(result, "--consent-recorded-at")
    assert not inputs.out.exists()


@pytest.mark.parametrize("kind", ["keep-until", "delete-after"])
@pytest.mark.parametrize("value", HOUR_24[:6])
def test_r5_a_retention_instant_with_hour_24_is_a_usage_error(
    tmp_path: Path, kind: str, value: str
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs, options={"--retention": f"{kind}:{value}"}))
    _usage_error(result, "--retention")
    assert not inputs.out.exists()


@pytest.mark.parametrize("value", HOUR_24[:6])
def test_r5_priced_at_with_hour_24_is_a_usage_error(tmp_path: Path, value: str) -> None:
    inputs = new_inputs(tmp_path, "a")
    price = tmp_path / "price.json"
    price.write_text(price_file_text(), "utf-8")
    result = run_capture(g_args(inputs, options={"--price-file": str(price), "--priced-at": value}))
    _usage_error(result, "--priced-at")
    assert not inputs.out.exists()


@pytest.mark.parametrize(("value", "expected"), VALID)
def test_r5_the_stored_instant_equals_the_input_instant(
    tmp_path: Path, value: str, expected: datetime
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs, options={"--consent-recorded-at": value}))
    assert result.code == 0, result.err
    markers = stored_privacy_markers(inputs.out, result.value("bundle"))
    assert markers
    for marker in markers:
        assert marker.consent.recorded_at == expected
        assert marker.consent.recorded_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(("value", "expected"), VALID)
def test_r5_a_retention_instant_is_stored_as_given(
    tmp_path: Path, value: str, expected: datetime
) -> None:
    inputs = new_inputs(tmp_path, "a")
    result = run_capture(g_args(inputs, options={"--retention": f"keep-until:{value}"}))
    assert result.code == 0, result.err
    markers = stored_privacy_markers(inputs.out, result.value("bundle"))
    assert markers
    for marker in markers:
        assert marker.retention.retain_until == expected
