# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: PT018
"""BL-313: ``tracer simulate`` with the optional ``turn_taking.activity_min_peak`` of the sim
profile file (ADR 0026 rule 10, ADR 0027 rules 10 and 13; contract
docs/phase4/api-surface-batch-activity.md section 10).

The value lives in the sim profile JSON file only, like ``wait_for_agent_silence_ms``: there is no
command line option. It is read at step 1, recorded in the stored sim profile, in the timing
document and in the plan of the agent segments, and named in every timing label. The claim stays
"observed at the simulator over <dialect>"; the wording of the activity is ``activity: peak >= N``
(``activity: any non-zero sample`` for N = 1) and is a statement of the rule, never of speech.

The call is the field case in miniature (``cli_p4act_support``): comfort noise in every pause,
speech at 500 to 680 ms and at 3700 to 3780 ms, one scripted line of 14 frames. By hand, with the
threshold 2: the first speech is the first activity (500 ms); the caller waits 300 ms of silence
after its last frame (680), so the line starts at 980 and ends with its last frame at 1240 (edge
1260); the second speech ends at 3780, so the call ends at 3780 + 300 = 4080 with the script
complete; the gap after the line is 3700 - 1260 = 2440 ms; nothing active overlaps the line.
"""

from __future__ import annotations

import html
import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from cli_batche_support import parse  # type: ignore[import-not-found,unused-ignore]
from cli_p4act_support import (  # type: ignore[import-not-found,unused-ignore]
    control_run,
    field_files,
    field_run,
)
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    HEARD_1,
    base_args,
    mod,
    profile_doc,
    run_sim,
    write_inputs,
)

DIALECT = "pipecat-protobuf/1"
NOTE = f"observed at the simulator over {DIALECT}, quantum 20 ms"
ACT2 = "activity: peak >= 2"


def activity_min_peak_of(document: Any) -> int:
    """The reader of the contract (section 2), loaded lazily so a missing one fails its own test."""
    value: int = importlib.import_module("tracer_evalcore.turntaking").activity_min_peak_of(
        document
    )
    return value


def stored(run: Any) -> dict[str, Any]:
    document: dict[str, Any] = run.bundle().snapshot.metadata["tracer.sim_profile"]
    return document


def timing(run: Any) -> dict[str, Any]:
    document: dict[str, Any] = run.bundle().run.metadata["tracer.timing"]
    return document


def plan(run: Any) -> dict[str, Any]:
    (our,) = [t for t in run.bundle().transcripts if t.source == "our_stt"]
    document: dict[str, Any] = our.metadata["tracer.chunk_plan"]
    return document


# ------------------------------------------------------------------------- the field problem


def test_a_noise_floor_agent_completes_the_call_with_a_threshold_above_the_noise(
    tmp_path: Path,
) -> None:
    run = field_run(tmp_path, 2)
    assert run.code == 0, (run.out, run.err)
    assert run.err == ""
    assert run.value("end reason") == "script_complete"
    assert run.value("caller turns") == "1 started, 1 completed"
    assert run.value("agent segments") == "2"
    assert run.value("first agent audio") == f"500 ms ({NOTE}, {ACT2})"
    assert run.value("gap after the caller's line (median)") == f"2440 ms ({NOTE}, {ACT2}, N 1)"
    assert run.value("overlap") == f"0 ms ({NOTE}, {ACT2})"
    assert run.value("answered caller turns") == f"1 ({NOTE}, {ACT2})"
    document = timing(run)
    assert document["first_agent_audio_ms"] == 500
    assert document["gap_median_ms"] == 2440
    assert document["overlap_ms"] == 0
    assert document["turns"][0]["gap_ms"] == 2440
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    assert outcome["end_reason"] == "script_complete"
    assert outcome["connected_ms"] == 4080
    # the words of the two agent segments are the scenario's own (the fake speech-to-text)
    (our,) = [t for t in run.bundle().transcripts if t.source == "our_stt"]
    texts = [t.text for t in run.bundle().turns if t.transcript_id == our.id]
    assert texts == [HEARD_0, HEARD_1]


def test_the_same_call_without_the_member_is_the_field_problem(tmp_path: Path) -> None:
    """The regression guard of the field case: the noise is activity, the wait for a silence never
    ends, the call ends ``max_silence`` at 3000 ms with no caller turn. Nothing about it changes:
    no caller turn means no turn timing, so the four values are not measured, and the document
    shows the agent as active from the first frame (0 ms), the noise."""
    run = control_run(tmp_path)
    assert run.value("end reason") == "max_silence"
    assert run.value("caller turns") == "0 started, 0 completed"
    assert run.value("agent segments") == "1"
    assert run.value("first agent audio") == "not measured (missing input turn_timing)"
    assert run.bundle().run.metadata["tracer.sim_outcome"]["connected_ms"] == 3000
    assert timing(run)["first_agent_audio_ms"] == 0
    assert "activity" not in run.out
    assert "activity_min_peak" not in timing(run)
    assert "activity_min_peak" not in stored(run)["turn_taking"]


def test_a_threshold_of_1_is_the_old_rule_and_says_so(tmp_path: Path) -> None:
    """The caller speaks first here, so there is a turn to time: the noise is active from the
    first frame (first audio 0, gap 0, 14 frames of the line overlap it: 280 ms), as before."""
    run = control_run(tmp_path / "one", 1, first=True)
    assert run.value("end reason") == "max_silence"
    note = f"{NOTE}, activity: any non-zero sample"
    assert run.value("first agent audio") == f"0 ms ({note})"
    assert run.value("overlap") == f"280 ms ({note})"
    assert timing(run)["activity_min_peak"] == 1
    assert stored(run)["turn_taking"]["activity_min_peak"] == 1
    plain = control_run(tmp_path / "plain", first=True)
    assert plain.value("overlap") == f"280 ms ({NOTE})"
    assert plain.value("first agent audio") == f"0 ms ({NOTE})"


# ------------------------------------------------------------------------------- the records


def test_the_stored_profile_the_timing_document_and_the_plan_record_the_value_as_used(
    tmp_path: Path,
) -> None:
    run = field_run(tmp_path, 700)
    assert run.code == 0, (run.out, run.err)
    section = stored(run)["turn_taking"]
    assert section == {
        "caller_speaks_first": False,
        "wait_for_agent_silence_ms": 300,
        "max_silence_ms": 3000,
        "activity_min_peak": 700,
    }
    assert timing(run)["activity_min_peak"] == 700
    assert plan(run)["activity_min_peak"] == 700
    assert plan(run)["wait_for_agent_silence_ms"] == 300
    importlib.import_module("tracer_evalcore.simprofile").validate_sim_profile(
        stored(run), check_caps=False
    )
    importlib.import_module("tracer_evalcore.simtiming").validate_timing_document(timing(run))


def test_without_the_member_every_stored_document_is_the_one_of_the_earlier_batches(
    tmp_path: Path,
) -> None:
    files = write_inputs(tmp_path)
    run = run_sim(files)
    assert run.code == 0, (run.out, run.err)
    assert set(stored(run)["turn_taking"]) == {
        "caller_speaks_first",
        "wait_for_agent_silence_ms",
        "max_silence_ms",
    }
    assert "activity_min_peak" not in timing(run)
    assert "activity_min_peak" not in plan(run)
    assert "activity" not in run.out
    assert "activity" not in html.unescape(run.report())
    assert run.value("first agent audio") == f"400 ms ({NOTE})"


def test_a_stored_run_without_the_member_reads_as_1(tmp_path: Path) -> None:
    run = run_sim(write_inputs(tmp_path))
    assert activity_min_peak_of(stored(run)["turn_taking"]) == 1
    assert activity_min_peak_of(timing(run)) == 1


# --------------------------------------------------------------------------------- the labels


def test_every_timing_label_of_the_report_names_the_activity_rule(tmp_path: Path) -> None:
    run = field_run(tmp_path, 2)
    doc = parse(run.report())
    note = next(n for n in doc.all("p") if n.attrs.get("id") == "timing-note").text()
    assert f"observed at the simulator over {DIALECT}" in note
    assert "quantum 20 ms" in note
    assert ACT2 in note
    table = next(n for n in doc.all("table") if n.attrs.get("id") == "timing")
    shown = {
        n.all("th")[0].text(): n.all("td")[0].text()
        for n in table.all("tr")
        if "timing" in n.classes
    }
    assert shown["first agent audio"] == f"500 ms ({NOTE}, {ACT2})"
    assert shown["gap after the caller's line (median)"] == f"2440 ms ({NOTE}, {ACT2}, N 1)"
    assert shown["overlap"] == f"0 ms ({NOTE}, {ACT2})"
    assert shown["answered caller turns"] == f"1 ({NOTE}, {ACT2})"
    records = [n for n in table.all("tr") if "timing-record" in n.classes]
    assert len(records) == 4
    for row in records:
        assert row.attrs["data-activity-min-peak"] == "2"
        assert row.attrs["data-quantum-ms"] == "20"
        assert row.attrs["data-dialect"] == DIALECT


def test_the_report_without_the_member_has_no_activity_attribute(tmp_path: Path) -> None:
    run = run_sim(write_inputs(tmp_path))
    doc = parse(run.report())
    assert not any("data-activity-min-peak" in n.attrs for n in doc.all("tr"))
    note = next(n for n in doc.all("p") if n.attrs.get("id") == "timing-note").text()
    assert "activity" not in note


def test_the_wording_for_1_is_any_non_zero_sample_and_for_more_a_peak(tmp_path: Path) -> None:
    one = control_run(tmp_path / "a", 1)
    assert "activity: any non-zero sample" in parse(one.report()).tracer_text()
    assert "peak >=" not in one.out
    big = field_run(tmp_path / "b", 32767)  # nothing is that loud: no activity, no timing value
    assert big.value("end reason") == "max_silence"
    assert "activity: peak >= 32767" in html.unescape(big.report())
    assert timing(big)["activity_min_peak"] == 32767


@pytest.mark.parametrize("peak", [2, 1000])
def test_the_labels_keep_the_claim_and_add_no_claim_about_speech(tmp_path: Path, peak: int) -> None:
    run = field_run(tmp_path, peak)
    text = run.out + html.unescape(run.report())
    assert "observed at the simulator over" in text
    for word in (
        "silence is",
        "speech detected",
        "voice activity",
        "detected",
        "accuracy",
        "quality",
    ):
        assert word not in run.out, word
    assert run.out.isascii()


# --------------------------------------------------------------------------------- twilio


def test_the_threshold_applies_to_the_decoded_mulaw_of_the_twilio_dialect(tmp_path: Path) -> None:
    """The fake agent's comfort noise is the byte 0xFE, which decodes to 8; its speech at the level
    1000 is a larger byte that decodes to about 1000. 9 is above the noise, 8 is not."""
    quiet = field_run(tmp_path / "q", 9, dialect="twilio-media-stream/1")
    assert quiet.code == 0, (quiet.out, quiet.err)
    assert quiet.value("end reason") == "script_complete"
    assert quiet.value("first agent audio").endswith("quantum 20 ms, activity: peak >= 9)")
    assert "twilio-media-stream/1" in quiet.value("first agent audio")
    assert timing(quiet)["first_agent_audio_ms"] == 500
    noisy = field_run(tmp_path / "n", 8, dialect="twilio-media-stream/1")
    assert timing(noisy)["first_agent_audio_ms"] == 0
    assert noisy.value("end reason") == "max_silence"


# ----------------------------------------------------------------------------- the refusals


def _profile_with_raw(tmp_path: Path, raw: str) -> Any:
    files = write_inputs(tmp_path)
    document = profile_doc()
    document["turn_taking"]["activity_min_peak"] = "ACTIVITY_RAW"
    files.sim_profile.write_text(json.dumps(document).replace('"ACTIVITY_RAW"', raw), "utf-8")
    return files


@pytest.mark.parametrize(
    "raw", ["0", "-1", "32768", "1.5", "1.0", "1e2", "true", "null", '"5"', "[5]"]
)
def test_a_bad_value_is_refused_at_step_1_with_one_code_before_anything_else(
    tmp_path: Path, raw: str
) -> None:
    files = _profile_with_raw(tmp_path, raw)
    run = run_sim(files)
    assert run.code == 4, (run.out, run.err)
    assert run.out == ""
    assert run.err.startswith("error: ") and len(run.err.splitlines()) == 1, run.err
    assert "sim_profile_file_invalid_turn_taking" in run.err
    assert not files.out.exists()
    assert "connect" not in run.trace


def test_the_boundary_values_run(tmp_path: Path) -> None:
    """1 is the old rule. 32767 is above any speech of the scenario (level 1000): the caller hears
    nothing, the call ends ``max_silence`` and two timing edges are missing, which is exit 2."""
    one = run_sim(_profile_with_raw(tmp_path / "1", "1"))
    assert one.code == 0, (one.out, one.err)
    assert one.value("end reason") == "script_complete"
    top = run_sim(_profile_with_raw(tmp_path / "32767", "32767"))
    assert top.code == 2, (top.out, top.err)
    assert top.value("end reason") == "max_silence"


# ------------------------------------------------------------------------------- the surface


def test_there_is_no_command_line_option_for_the_threshold(tmp_path: Path) -> None:
    cli = mod("cli")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None))
    options = {o for a in sub.choices["simulate"]._actions for o in a.option_strings}
    assert not any(re.search(r"activity|peak|threshold|noise", o) for o in options), options
    files = write_inputs(tmp_path)
    run = run_sim(files, args=[*base_args(files), "--activity-min-peak", "5"])
    assert run.code == 64
    assert run.out == ""
    assert run.err.startswith("usage: ")


def test_the_dry_run_prints_the_same_plan_with_and_without_the_member(tmp_path: Path) -> None:
    plain = run_sim(field_files(tmp_path / "a"), add=["--dry-run"])
    given = run_sim(field_files(tmp_path / "b", 700), add=["--dry-run"])
    assert plain.code == given.code == 0
    assert plain.out == given.out
    assert "activity" not in given.out
    assert not (tmp_path / "b" / "out").exists()


def test_the_help_does_not_mention_the_threshold() -> None:
    cli = mod("cli")
    text = cli.build_parser().format_help()
    for action in cli.build_parser()._actions:
        choices = getattr(action, "choices", None)
        if choices and "simulate" in choices:
            text += choices["simulate"].format_help()
    assert "activity" not in text.lower()
