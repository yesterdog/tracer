# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (one call per invocation, minimum interval): the lock file in ``--out`` (ADR 0026
rule 18).

Contract: docs/phase4/api-surface-batch-g.md section 3. The file ``call.lock`` records the
monotonic and the UTC instants of the last connect; a call earlier than the profile's
``min_call_interval_ms`` after it is refused; the claim is exclusive (concurrency 1) for the whole
invocation. Time is passed in (the command's injected clock), nothing sleeps, no file outside the
test's temporary directory is touched. A refusal is a ``GuardError`` whose message is its code:
never a path, never an instant.
"""

from __future__ import annotations

import json
import os
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evalsim_support_g import mod  # type: ignore[import-not-found,unused-ignore]

T0 = datetime(2026, 10, 27, 12, 0, 0, 123456, tzinfo=UTC)
T0_TEXT = "2026-10-27T12:00:00.123456Z"
MONO = 5_000_000_000
LOCK_BYTES = (
    b'{"format":"tracer.call_lock/1","monotonic_ns":5000000000,'
    b'"utc":"2026-10-27T12:00:00.123456Z"}\n'
)


def guard() -> Any:
    return mod("guard")


def lock_path(out: Path) -> Path:
    return Path(out / guard().CALL_LOCK_FILE_NAME)


def claim(out: Path, *, now: datetime = T0, mono: int = MONO, interval: int = 3000) -> Any:
    return guard().CallLock.claim(out, now_utc=now, monotonic_ns=mono, min_interval_ms=interval)


def fresh(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    return out


def code_of(function: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(guard().GuardError) as caught:
        function(*args, **kwargs)
    assert str(caught.value) == caught.value.code
    assert caught.value.code in guard().GUARD_CODES
    return str(caught.value.code)


def test_the_guard_codes_are_a_closed_sorted_list_of_machine_codes() -> None:
    codes = guard().GUARD_CODES
    assert list(codes) == sorted(set(codes))
    assert set(codes) == {
        "call_in_progress",
        "call_lock_clock_inconsistent",
        "call_lock_invalid",
        "kill_switch_file_present",
        "min_call_interval_not_elapsed",
    }
    assert issubclass(guard().GuardError, mod("errors").EvalsimError)
    assert issubclass(guard().GuardError, ValueError)
    error = guard().GuardError("call_in_progress")
    assert (str(error), error.code) == ("call_in_progress", "call_in_progress")
    with pytest.raises(ValueError, match="machine code"):
        guard().GuardError("not a machine code")


# ------------------------------------------------------------------------------- the claim


@pytest.mark.parametrize("umask", [0o000, 0o022, 0o077, 0o277])
def test_a_claim_writes_the_instants_of_the_connect_to_a_file_only_the_owner_can_use(
    tmp_path: Path, umask: int
) -> None:
    """The record is written before the claim returns (a crash during the call still leaves it):
    the exact bytes, one line, the UTC instant with microseconds and the monotonic instant of the
    injected clock. The mode is 0600 whatever the umask says (a 0277 umask would make 0400)."""
    out = fresh(tmp_path)
    old = os.umask(umask)
    try:
        held = claim(out)
    finally:
        os.umask(old)
    try:
        info = os.lstat(lock_path(out))
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert lock_path(out).read_bytes() == LOCK_BYTES
        assert LOCK_BYTES.decode("ascii").count("\n") == 1
        assert T0_TEXT in LOCK_BYTES.decode("ascii")
    finally:
        held.close()
    assert lock_path(out).read_bytes() == LOCK_BYTES  # closing keeps the record
    assert sorted(p.name for p in out.iterdir()) == [guard().CALL_LOCK_FILE_NAME]


def test_the_record_is_replaced_by_the_next_claim_and_not_appended(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    claim(out).close()
    later = T0 + timedelta(seconds=3)
    claim(out, now=later, mono=MONO + 3_000_000_000).close()
    text = lock_path(out).read_text("ascii")
    assert text.count("\n") == 1
    assert json.loads(text) == {
        "format": "tracer.call_lock/1",
        "monotonic_ns": MONO + 3_000_000_000,
        "utc": "2026-10-27T12:00:03.123456Z",
    }


def test_the_claim_is_a_context_manager_that_releases_on_exit(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    with claim(out, interval=0) as held:
        assert held is not None
        assert code_of(claim, out, interval=0) == "call_in_progress"
    claim(out, interval=0).close()  # released: a new claim is possible
    # an error inside the block releases too
    with pytest.raises(RuntimeError), claim(out, interval=0):
        raise RuntimeError
    claim(out, interval=0).close()


def test_a_second_claim_while_the_first_is_held_is_refused_as_in_progress(tmp_path: Path) -> None:
    """Concurrency 1 (ADR 0026 rule 18): two invocations on one output directory cannot be in a
    call at the same time, even when the interval is 0. The refusal changes nothing: the record of
    the first claim is intact."""
    out = fresh(tmp_path)
    first = claim(out, interval=0)
    try:
        assert code_of(claim, out, now=T0 + timedelta(seconds=9), interval=0) == "call_in_progress"
        assert lock_path(out).read_bytes() == LOCK_BYTES
    finally:
        first.close()
    claim(out, now=T0 + timedelta(seconds=9), interval=0).close()


def test_a_claim_refuses_a_call_too_soon_after_the_last_one(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    claim(out).close()
    before = lock_path(out).read_bytes()
    soon = T0 + timedelta(milliseconds=2999, microseconds=999)
    assert code_of(claim, out, now=soon) == "min_call_interval_not_elapsed"
    assert lock_path(out).read_bytes() == before  # a refusal records nothing
    claim(out, now=T0 + timedelta(milliseconds=3000)).close()  # exactly the interval is enough


def test_the_claim_checks_the_interval_itself_under_the_lock(tmp_path: Path) -> None:
    """The read-only check at step 2 and the claim at the connect are two moments: a second
    invocation may have connected between them. The claim re-reads the record it holds the lock
    on, so it is the claim that decides."""
    out = fresh(tmp_path)
    guard().check_call_interval(out, now_utc=T0, min_interval_ms=3000)  # nobody yet: fine
    lock_path(out).write_bytes(LOCK_BYTES)
    lock_path(out).chmod(0o600)  # another invocation connected just now
    assert code_of(claim, out, now=T0 + timedelta(seconds=1)) == "min_call_interval_not_elapsed"


def test_a_claim_never_follows_a_link_at_the_lock_path(tmp_path: Path) -> None:
    """The lock path is opened without following links (``O_NOFOLLOW``): a link planted in the
    output directory is refused, and what it points to is neither read, written nor created."""
    out = fresh(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_bytes(LOCK_BYTES)  # a valid, private, old record: following the link would pass
    victim.chmod(0o600)
    os.symlink(victim, lock_path(out))
    later = T0 + timedelta(days=1)
    assert code_of(claim, out, now=later) == "call_lock_invalid"
    assert victim.read_bytes() == LOCK_BYTES
    assert code_of(guard().check_call_interval, out, now_utc=later, min_interval_ms=3000) == (
        "call_lock_invalid"
    )
    assert victim.read_bytes() == LOCK_BYTES
    lock_path(out).unlink()
    os.symlink(tmp_path / "not-created", lock_path(out))  # dangling
    assert code_of(claim, out) == "call_lock_invalid"
    assert not (tmp_path / "not-created").exists()


# ------------------------------------------------------------------------------- the check


def test_the_check_is_read_only_and_creates_nothing(tmp_path: Path) -> None:
    """Step 2 of the command runs before the output directory may exist (steps 1 to 5 create
    nothing): an absent directory or an absent file means 'no call before'."""
    missing = tmp_path / "never-created"
    guard().check_call_interval(missing, now_utc=T0, min_interval_ms=3000)
    assert not missing.exists()
    out = fresh(tmp_path)
    guard().check_call_interval(out, now_utc=T0, min_interval_ms=3000)
    assert list(out.iterdir()) == []


def test_the_interval_is_exact_to_the_microsecond(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    lock_path(out).write_bytes(LOCK_BYTES)
    lock_path(out).chmod(0o600)
    check = guard().check_call_interval
    just_before = T0 + timedelta(milliseconds=3000) - timedelta(microseconds=1)
    assert code_of(check, out, now_utc=just_before, min_interval_ms=3000) == (
        "min_call_interval_not_elapsed"
    )
    check(out, now_utc=T0 + timedelta(milliseconds=3000), min_interval_ms=3000)
    check(out, now_utc=T0 + timedelta(days=400), min_interval_ms=3000)
    check(out, now_utc=T0, min_interval_ms=0)  # no interval: any time is enough
    assert code_of(check, out, now_utc=T0, min_interval_ms=1) == "min_call_interval_not_elapsed"
    # the local time zone of the caller is irrelevant: an offset instant compares by its UTC value
    for zone in (timezone_of(5, 30), timezone_of(-5, 0), timezone_of(-12, 0), timezone_of(14, 0)):
        check(out, now_utc=(T0 + timedelta(seconds=3)).astimezone(zone), min_interval_ms=3000)
        early = (T0 + timedelta(seconds=3) - timedelta(microseconds=1)).astimezone(zone)
        assert code_of(check, out, now_utc=early, min_interval_ms=3000) == (
            "min_call_interval_not_elapsed"
        )


def timezone_of(hours: int, minutes: int) -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=hours, minutes=minutes))


def test_a_record_in_the_future_cannot_be_compared_and_is_refused(tmp_path: Path) -> None:
    """The wall clock went back (or the file is forged): the interval cannot be known, so the call
    is refused rather than allowed (never assume). With no interval the question does not arise."""
    out = fresh(tmp_path)
    lock_path(out).write_bytes(LOCK_BYTES)
    lock_path(out).chmod(0o600)
    check = guard().check_call_interval
    past = T0 - timedelta(seconds=1)
    assert code_of(check, out, now_utc=past, min_interval_ms=3000) == (
        "call_lock_clock_inconsistent"
    )
    assert code_of(claim, out, now=past) == "call_lock_clock_inconsistent"
    check(out, now_utc=past, min_interval_ms=0)


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("not json", b"garbage\n"),
        ("empty object", b"{}\n"),
        ("array", b"[]\n"),
        ("wrong format", LOCK_BYTES.replace(b"call_lock/1", b"call_lock/2")),
        ("extra key", LOCK_BYTES.replace(b"\n", b"").rstrip(b"}") + b',"x":1}\n'),
        ("missing utc", b'{"format":"tracer.call_lock/1","monotonic_ns":5}\n'),
        ("negative monotonic", LOCK_BYTES.replace(b"5000000000", b"-5")),
        ("float monotonic", LOCK_BYTES.replace(b"5000000000", b"5.5")),
        ("boolean monotonic", LOCK_BYTES.replace(b"5000000000", b"true")),
        ("monotonic over 2**63", LOCK_BYTES.replace(b"5000000000", b"9223372036854775808")),
        ("utc without Z", LOCK_BYTES.replace(b"123456Z", b"123456")),
        ("utc with an offset", LOCK_BYTES.replace(b"123456Z", b"123456+00:00")),
        ("utc without microseconds", LOCK_BYTES.replace(b".123456", b"")),
        ("utc not a date", LOCK_BYTES.replace(b"2026-10-27", b"2026-13-45")),
        ("two records", LOCK_BYTES + LOCK_BYTES),
        ("one record on two lines", LOCK_BYTES.replace(b",", b",\n", 1)),
        (
            "newline at byte 4097 and more after it",
            LOCK_BYTES[:-1] + b" " * (4096 - len(LOCK_BYTES) + 1) + b"\nxxxxxxxxxx",
        ),
        ("not UTF-8", b'{"format":"\xff"}\n'),
        (
            "duplicate key (the last value is the valid one)",
            LOCK_BYTES.replace(b'{"format"', b'{"format":"x","format"', 1),
        ),
        ("too large", LOCK_BYTES + b" " * 5000),
    ],
)
@pytest.mark.parametrize("entry", ["check", "claim"])
def test_a_lock_file_that_is_not_ours_is_refused_never_guessed(
    tmp_path: Path, label: str, content: bytes, entry: str
) -> None:
    """Whatever is in the file, if it is not exactly the record this program writes, the interval
    is unknown and the call is refused with ``call_lock_invalid``: a corrupt or forged lock is
    never read as 'no earlier call' (that would turn a typo into an unlimited dialler)."""
    out = fresh(tmp_path)
    lock_path(out).write_bytes(content)
    lock_path(out).chmod(0o600)
    if entry == "check":
        got = code_of(guard().check_call_interval, out, now_utc=T0, min_interval_ms=3000)
    else:
        got = code_of(claim, out)
    assert got == "call_lock_invalid", label
    assert lock_path(out).read_bytes() == content  # the refusal does not repair or replace it


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o777])
def test_a_lock_file_with_group_or_other_permission_is_refused(tmp_path: Path, mode: int) -> None:
    out = fresh(tmp_path)
    lock_path(out).write_bytes(LOCK_BYTES)
    lock_path(out).chmod(mode)
    assert code_of(guard().check_call_interval, out, now_utc=T0 + timedelta(days=1),
                   min_interval_ms=3000) == "call_lock_invalid"  # fmt: skip
    assert code_of(claim, out, now=T0 + timedelta(days=1)) == "call_lock_invalid"


def test_a_lock_that_is_a_directory_is_refused(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    lock_path(out).mkdir()
    assert code_of(guard().check_call_interval, out, now_utc=T0, min_interval_ms=3000) == (
        "call_lock_invalid"
    )
    assert code_of(claim, out) == "call_lock_invalid"


def test_an_empty_lock_file_means_no_call_was_recorded(tmp_path: Path) -> None:
    """A crash between the creation and the write leaves an empty file: no connect happened (the
    record is written before the connect), so it is not an earlier call."""
    out = fresh(tmp_path)
    lock_path(out).write_bytes(b"")
    lock_path(out).chmod(0o600)
    guard().check_call_interval(out, now_utc=T0, min_interval_ms=3000)
    claim(out).close()
    assert lock_path(out).read_bytes() == LOCK_BYTES


@pytest.mark.parametrize("interval", [-1, True, 1.5, "3000", None])
def test_a_bad_interval_is_a_program_error_not_a_refusal(tmp_path: Path, interval: Any) -> None:
    out = fresh(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        guard().check_call_interval(out, now_utc=T0, min_interval_ms=interval)
    with pytest.raises((TypeError, ValueError)):
        claim(out, interval=interval)


def test_a_naive_instant_is_a_program_error(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    naive = datetime(2026, 10, 27, 12, 0, 0)
    with pytest.raises(ValueError, match="aware"):
        guard().check_call_interval(out, now_utc=naive, min_interval_ms=0)
    with pytest.raises(ValueError, match="aware"):
        claim(out, now=naive)


def test_nothing_in_the_guard_sleeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal is immediate: the interval is never waited out (that would be a dialler loop with
    a pause, ADR 0026 rule 18 'Rejected')."""

    def no_sleep(seconds: float) -> None:
        raise AssertionError("the guard slept")

    monkeypatch.setattr(time, "sleep", no_sleep)
    out = fresh(tmp_path)
    claim(out).close()
    assert code_of(claim, out, now=T0 + timedelta(milliseconds=1)) == (
        "min_call_interval_not_elapsed"
    )


def test_the_refusals_never_carry_a_path_or_an_instant(tmp_path: Path) -> None:
    out = fresh(tmp_path)
    claim(out).close()
    with pytest.raises(guard().GuardError) as caught:
        claim(out, now=T0 + timedelta(seconds=1))
    text = str(caught.value) + repr(caught.value)
    assert str(tmp_path) not in text
    assert "2026" not in text
    assert str(MONO) not in text
