# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, the output directory findings of the P3-28 re-audit: R3 and R6.

Contract: docs/phase3/api-surface-batch-i.md sections 3 and 5.

- R3: an existing ``--out`` that group or other users can reach earns one warning line on stderr
  (the mode only, never the path) and the run goes on; an owner-only one earns nothing; a leaf
  directory the command creates is 0700 and the parents it creates follow the umask (this last
  test pins the wording of the documents: if parents are ever forced to 0700, change both).
- R6: a link in place of ``tracer.sqlite`` (to a file, to a directory, dangling) is refused with
  exit 4 ``output_unavailable`` before any engine is built; the target of the link is never
  opened for writing.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import (  # type: ignore[import-not-found,unused-ignore]
    base_bodies,
    load_stored_run,
    store_mod,
    write_replay,
)
from cli_batchg_support import g_args  # type: ignore[import-not-found,unused-ignore]
from cli_batchi_support import (  # type: ignore[import-not-found,unused-ignore]
    CALL_LOG,
    EMBED_WARNING,
    RECOVERY_AT,
    REPORT_AT,
    STORE,
    SarvamScript,
    mode_warning,
    names,
    new_inputs,
    run_capture,
    runs_in_store,
    sarvam_capture,
)

needs_file_modes = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores file modes"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _existing_out(tmp_path: Path, name: str, mode: int) -> Any:
    inputs = new_inputs(tmp_path, name)
    inputs.out.mkdir()
    inputs.out.chmod(mode)
    return inputs


# ============================================================ R3: the mode of an existing --out


@needs_file_modes
@pytest.mark.parametrize(
    "mode", [0o755, 0o750, 0o705, 0o770, 0o777, 0o775, 0o701, 0o710, 0o740, 0o704]
)
def test_r3_an_existing_out_with_group_or_other_bits_earns_exactly_one_warning(
    tmp_path: Path, mode: int
) -> None:
    inputs = _existing_out(tmp_path, "a", mode)
    result = run_capture(g_args(inputs))
    assert result.code == 0, result.err
    assert result.err == mode_warning(mode)
    assert result.err.count("\n") == 1
    assert str(inputs.out) not in result.err
    assert str(tmp_path) not in result.err
    assert _mode(inputs.out) == mode, "the command never changes the mode of an existing directory"
    assert len(result.engine.calls) == 1


@needs_file_modes
def test_r3_the_warning_is_the_same_over_the_sarvam_engine_and_a_replay(tmp_path: Path) -> None:
    sarvam_inputs = _existing_out(tmp_path, "a", 0o755)
    script = SarvamScript()
    result, _ = sarvam_capture(sarvam_inputs, script=script)
    assert result.code == 0, result.error
    assert result.err == mode_warning(0o755)
    assert len(script.requests) == 2
    replay_inputs = _existing_out(tmp_path, "b", 0o750)
    replay = write_replay(tmp_path / "replay", base_bodies())
    replayed = run_capture(
        g_args(replay_inputs, options={"--stt": f"fake:{replay}"}), production=True
    )
    assert replayed.code == 0, replayed.err
    assert replayed.err == mode_warning(0o750)


@needs_file_modes
def test_r3_an_owner_only_existing_out_is_silent(tmp_path: Path) -> None:
    inputs = _existing_out(tmp_path, "a", 0o700)
    result = run_capture(g_args(inputs))
    assert result.code == 0, result.err
    assert result.err == ""
    script = SarvamScript()
    again = _existing_out(tmp_path, "b", 0o700)
    produced, _ = sarvam_capture(again, script=script)
    assert produced.code == 0
    assert produced.err == ""


@needs_file_modes
def test_r3_the_dry_run_touches_no_output_and_says_nothing_about_it(tmp_path: Path) -> None:
    inputs = _existing_out(tmp_path, "a", 0o777)
    result = run_capture(g_args(inputs, extra=["--dry-run"]))
    assert result.code == 0, result.err
    assert result.err == ""
    assert result.factory_calls == 0
    assert names(inputs.out) == []
    assert _mode(inputs.out) == 0o777


@needs_file_modes
def test_r3_the_warning_comes_before_the_embed_warning(tmp_path: Path) -> None:
    inputs = _existing_out(tmp_path, "a", 0o755)
    result = run_capture(g_args(inputs, extra=["--embed-audio"]))
    assert result.code == 0, result.err
    assert result.err == mode_warning(0o755) + EMBED_WARNING


@needs_file_modes
def test_r3_the_warning_does_not_change_the_exit_code_of_a_failure_after_the_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("STOREFAILURE")

    monkeypatch.setattr(store_mod(), "store_run", boom)
    inputs = _existing_out(tmp_path, "a", 0o755)
    result = run_capture(g_args(inputs))
    assert result.code == 1
    assert result.err == mode_warning(0o755) + "error: unexpected failure (RuntimeError)\n"
    assert result.value("usage recovery") == str(inputs.out / RECOVERY_AT)
    assert (inputs.out / REPORT_AT).is_file()


@needs_file_modes
@pytest.mark.parametrize("umask", [0o000, 0o022, 0o077])
@pytest.mark.parametrize("production", [False, True])
def test_r3_a_leaf_the_command_creates_is_0700_and_earns_no_warning(
    tmp_path: Path, umask: int, production: bool
) -> None:
    inputs = new_inputs(tmp_path, "a")
    previous = os.umask(umask)
    try:
        if production:
            result, _ = sarvam_capture(inputs)
        else:
            result = run_capture(g_args(inputs))
    finally:
        os.umask(previous)
    assert result.code == 0, result.err
    assert _mode(inputs.out) == 0o700
    assert result.err == ""


@needs_file_modes
@pytest.mark.parametrize(("umask", "parent_mode"), [(0o022, 0o755), (0o027, 0o750)])
@pytest.mark.parametrize("production", [False, True])
def test_r3_only_the_leaf_is_forced_to_0700_the_parents_follow_the_umask(
    tmp_path: Path, umask: int, parent_mode: int, production: bool
) -> None:
    # Truth in wording: the documents say "the leaf directory is 0700" and "parents follow the
    # umask". This test pins that sentence (over the injected engine, where the store creates
    # the directories, and over the Sarvam engine, where the call log does); if the parents are
    # ever created 0700 too, change the test and the documents together.
    inputs = new_inputs(tmp_path, "a")
    nested = tmp_path / "a" / "p1" / "p2" / "out"
    previous = os.umask(umask)
    try:
        if production:
            result, _ = sarvam_capture(inputs, options={"--out": str(nested)})
        else:
            result = run_capture(g_args(inputs, options={"--out": str(nested)}))
    finally:
        os.umask(previous)
    assert result.code == 0, result.error
    assert _mode(nested) == 0o700
    assert _mode(nested.parent) == parent_mode
    assert _mode(nested.parent.parent) == parent_mode
    assert result.err == ""


@needs_file_modes
@pytest.mark.parametrize(("mode", "shown"), [(0o2755, "0755"), (0o1750, "0750")])
def test_r3_the_special_bits_are_not_shown(tmp_path: Path, mode: int, shown: str) -> None:
    inputs = _existing_out(tmp_path, "a", mode)
    result = run_capture(g_args(inputs))
    assert result.code == 0, result.err
    assert result.err == mode_warning(int(shown, 8))


@needs_file_modes
def test_r3_the_special_bits_alone_do_not_warn(tmp_path: Path) -> None:
    inputs = _existing_out(tmp_path, "a", 0o2700)
    result = run_capture(g_args(inputs))
    assert result.code == 0, result.err
    assert result.err == ""


# ============================================================ R6: a link in place of the store


def _plant(tmp_path: Path, inputs: Any, kind: str) -> tuple[Path, Any]:
    """A link ``<out>/tracer.sqlite`` of the given kind; returns the target and a state probe."""
    inputs.out.mkdir(mode=0o700)
    link = inputs.out / STORE
    if kind == "file":
        # a real store of an earlier run, elsewhere: the most dangerous target
        earlier = new_inputs(tmp_path, "earlier")
        replay = write_replay(tmp_path / "earlier-replay", base_bodies())
        made = run_capture(g_args(earlier, options={"--stt": f"fake:{replay}"}), production=True)
        assert made.code == 0, made.err
        target = earlier.out / STORE
        link.symlink_to(target)
        return target, lambda: (
            hashlib.sha256(target.read_bytes()).hexdigest(),
            target.stat().st_mtime_ns,
            names(earlier.out),
        )
    if kind == "directory":
        target = tmp_path / "target-dir"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
        return target, lambda: names(target)
    target = tmp_path / "nowhere.sqlite"
    link.symlink_to(target)
    return target, lambda: (target.exists(), names(tmp_path))


KINDS = ["file", "directory", "dangling"]
REFUSED = "error: output_unavailable (store_file_is_a_link)\n"


@pytest.mark.parametrize("kind", KINDS)
def test_r6_a_link_in_place_of_the_store_is_refused_before_the_engine(
    tmp_path: Path, kind: str
) -> None:
    inputs = new_inputs(tmp_path, "a")
    _, state = _plant(tmp_path, inputs, kind)
    before = state()
    result = run_capture(g_args(inputs))
    assert result.code == 4, result.err
    assert result.err == REFUSED
    assert result.out == ""
    assert result.factory_calls == 0
    assert result.engine.calls == []
    assert state() == before, "the target of the link is never written"
    assert names(inputs.out) == [STORE]
    assert (inputs.out / STORE).is_symlink()


@pytest.mark.parametrize("kind", KINDS)
def test_r6_the_same_over_the_real_sarvam_engine_sends_and_creates_nothing(
    tmp_path: Path, kind: str
) -> None:
    inputs = new_inputs(tmp_path, "a")
    _, state = _plant(tmp_path, inputs, kind)
    before = state()
    script = SarvamScript()
    result, _ = sarvam_capture(inputs, script=script)
    assert result.code == 4, result.error
    assert result.err == REFUSED
    assert result.out == ""
    assert script.requests == []
    assert state() == before
    assert names(inputs.out) == [STORE], "no call log, no report, no recovery file"
    assert CALL_LOG not in names(inputs.out)


@pytest.mark.parametrize("kind", KINDS)
def test_r6_the_dry_run_probes_no_output_and_is_unchanged(tmp_path: Path, kind: str) -> None:
    inputs = new_inputs(tmp_path, "a")
    _, state = _plant(tmp_path, inputs, kind)
    before = state()
    result = run_capture(g_args(inputs, extra=["--dry-run"]))
    assert result.code == 0, result.err
    assert result.factory_calls == 0
    assert state() == before
    assert names(inputs.out) == [STORE]


@pytest.mark.parametrize("kind", KINDS)
def test_r6_the_store_itself_refuses_a_link_so_a_late_planted_one_is_caught(
    tmp_path: Path, kind: str
) -> None:
    inputs = new_inputs(tmp_path, "a")
    _, state = _plant(tmp_path, inputs, kind)
    before = state()
    with pytest.raises(OSError, match="link"):
        store_mod().open_sqlite_store(inputs.out)
    assert state() == before
    assert names(inputs.out) == [STORE]


def test_r6_a_regular_store_of_an_earlier_run_is_still_reused(tmp_path: Path) -> None:
    # The guard: a plain file is not a link. A second run into the same directory shares the store.
    inputs = new_inputs(tmp_path, "a")
    first = run_capture(g_args(inputs))
    assert first.code == 0, first.err
    (inputs.out / REPORT_AT).unlink()
    second = run_capture(g_args(inputs))
    assert second.code == 0, second.err
    assert second.err == ""
    assert runs_in_store(inputs.out) == 2
    assert load_stored_run(inputs.out, first.value("bundle")) is not None
