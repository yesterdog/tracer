# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21: the run bundle of a simulated call (ADR 0027 rules 2, 3, 5, 6, 8, 14 and 16; ADR 0004
Decision 11 and invariant I43; ADR 0016 Decisions 5 and 7a; finding F6).

Contract: docs/phase4/api-surface-batch-e.md section 7. The pure builder
``tracer_evalcore.simbundle.build_simulated_bundle`` turns the facts of a call (a recording, the
caller lines that were sent, the timing and outcome documents, the usage), the speech-to-text
outcome of the agent channel and the optional agent export into a bundle that passes
``check_run_bundle``. The cases are synthetic calls built from invented words.
"""

from __future__ import annotations

import ast
import re
from typing import Any

import pytest

from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    CREATED_AT,
    LINE_0,
    LINE_1,
    ROOT,
    STARTED_AT,
    TTS_LANGUAGE,
    build_bundle,
    export_read,
    mod,
    profile_file_doc,
    results_by_key,
    stored_profile,
    timing_document,
    usage_record,
)
from tracer_evalschema.bundle import RunBundle, check_run_bundle

SRC = ROOT / "packages" / "evalcore" / "src" / "tracer_evalcore"


def sources_of(bundle: Any) -> set[str]:
    return {t.source for t in bundle.transcripts}


def test_simulated_bundle_passes_check_run_bundle() -> None:
    """ADR 0027 rule 2 and invariant I1: every produced bundle passes ``check_run_bundle`` -- the
    shape of the Run, the artifact, the declared channel map, the three transcripts, the documents
    in the metadata -- and survives a JSON round trip."""
    export = export_read()
    built = build_bundle(export=export)
    bundle = built.bundle
    check_run_bundle(bundle)

    run = bundle.run
    assert (run.mode, run.caller_origin, run.observed_tier) == ("simulated", "simulated", "T0")
    assert run.started_at == STARTED_AT  # the instant the connection was established
    assert run.caller_external_id is None
    assert run.external_refs == []
    assert run.available_inputs == [
        "audio",
        "agent_audio",
        "transcript",
        "agent_transcript",
        "turn_timing",
    ]
    assert set(run.metadata) == {"tracer.sim_outcome", "tracer.timing"}
    assert run.metadata["tracer.sim_outcome"]["outcome"] == "not_evaluated"
    assert run.metadata["tracer.timing"]["format"] == "tracer.timing/1"

    (artifact,) = bundle.artifacts
    fmt = artifact.audio_format
    assert (fmt.container, fmt.codec, fmt.sample_rate_hz, fmt.channels, fmt.bits_per_sample) == (
        "wav",
        "pcm_s16le",
        16000,
        2,
        16,
    )
    (channel_map,) = run.channel_maps
    assert channel_map.artifact_id == artifact.id
    assert channel_map.determination == "declared"
    assert channel_map.confidence is None
    assert channel_map.method == "tracer_evalsim_stereo/1"
    assert [(c.index, c.role) for c in channel_map.channels] == [(0, "caller"), (1, "agent")]

    assert sources_of(bundle) == {"script", "our_stt", "agent_export"}
    script = next(t for t in bundle.transcripts if t.source == "script")
    assert script.provider is None
    assert script.model is None
    assert script.timeline_origin.kind == "artifact"
    assert script.timeline_origin.ref == artifact.id
    assert script.metadata == {"tracer.caller_text_origin": "scripted"}
    lines = sorted((t for t in bundle.turns if t.transcript_id == script.id), key=lambda t: t.seq)
    assert [(t.seq, t.role, t.source, t.text) for t in lines] == [
        (0, "caller", "script", LINE_0),
        (1, "caller", "script", LINE_1),
    ]
    assert [(t.timing_source, t.start_ms, t.end_ms, t.language) for t in lines] == [
        ("audio_alignment", 0, 200, TTS_LANGUAGE),
        ("audio_alignment", 1000, 1200, TTS_LANGUAGE),
    ]
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    heard = [t for t in bundle.turns if t.transcript_id == stt.id]
    assert [t.role for t in heard] == ["agent", "agent"]
    assert stt.metadata["tracer.chunk_plan"]["format"] == "tracer.chunk_plan/2"

    # the three documents hold no text, so no metadata flag is ever set (ADR 0027 invariant I1)
    for record in (run, bundle.snapshot, script, stt, artifact):
        assert record.metadata_flags == [], type(record).__name__

    again = RunBundle.model_validate_json(bundle.model_dump_json(by_alias=True))
    check_run_bundle(again)
    assert again == bundle


@pytest.mark.parametrize(
    "case",
    [
        "no_export",
        "stt_failed",
        "agent_never_spoke",
        "twilio",
        "partial_last_line",
        "unestablished",
        "with_test_profile",
        "llm_origin",
        "replay_origin",
        "barge_in",
    ],
)
def test_every_shape_of_call_gives_a_valid_bundle(case: str) -> None:
    """The degradation matrix at record level (ADR 0027 rule 16): whatever happened, the bundle
    is valid and has the same result keys as the base case of its configuration."""
    options: dict[str, Any] = {}
    if case == "stt_failed":
        options = {"export": export_read(), "stt_failed": True}
    elif case == "agent_never_spoke":
        options = {"stt_texts": None, "timing": timing_document(agent_starts_ms=())}
    elif case == "twilio":
        profile = stored_profile(profile_file_doc("twilio-media-stream/1"))
        options = {"profile": profile, "timing": timing_document(dialect="twilio-media-stream/1")}
    elif case == "partial_last_line":
        options = {"complete_last_line": False, "end_reason": "kill_switch"}
    elif case == "unestablished":
        options = {"established": False}
    elif case == "with_test_profile":
        options = {"with_test_profile": True}
    elif case == "llm_origin":
        options = {"mode": "llm"}
    elif case == "replay_origin":
        options = {"mode": "replay"}
    elif case == "barge_in":
        profile = stored_profile(
            profile_file_doc(barge_ins=[{"turn_index": 1, "barge_in_after_ms": 100}])
        )
        options = {"profile": profile, "barge": {1: 100}, "timing": timing_document(barge={1: 100})}
    built = build_bundle(**options)
    check_run_bundle(built.bundle)
    keys = set(results_by_key(built))
    expected = set(results_by_key(build_bundle())) | (
        {"sim_barge_in_stop_ms"} if case == "barge_in" else set()
    )
    assert keys == expected
    assert built.bundle.verdict is None


def test_an_unestablished_call_has_no_artifact_no_map_no_transcript_and_no_start() -> None:
    """ADR 0027 rules 2 and 3: a call that was never established has no ``started_at``, no
    artifact and no channel map; nothing is invented for it."""
    bundle = build_bundle(established=False).bundle
    assert bundle.run.started_at is None
    assert bundle.artifacts == []
    assert bundle.run.channel_maps == []
    assert bundle.transcripts == []
    assert bundle.turns == []
    assert bundle.run.available_inputs == []
    assert bundle.run.metadata["tracer.sim_outcome"]["call_established"] is False
    assert bundle.run.metadata["tracer.timing"]["setup_ms"] == 25


def test_the_builder_refuses_a_recording_that_does_not_match_the_call() -> None:
    sb = mod("simbundle")
    built = build_bundle()
    for kwargs in (
        {"established": True, "recording": None},
        {"established": False, "recording": built.call.recording},
    ):
        call = sb.SimulatedCall(
            run_id=built.call.run_id,
            established=kwargs["established"],
            started_at=STARTED_AT,
            recording=kwargs["recording"],
            caller_lines=(),
            caller_text_origin="scripted",
            timing=built.call.timing,
            outcome=built.call.outcome,
            usage=(),
        )
        with pytest.raises(ValueError, match="recording"):
            sb.build_simulated_bundle(
                configuration=built.configuration,
                project=sb.simulated_project(name="p", created_at=CREATED_AT),
                agent_target=sb.simulated_agent_target(
                    name="a", transport="ws", url="ws://127.0.0.1:1/ws", created_at=CREATED_AT
                ),
                call=call,
                stt=None,
                export=None,
                costs=None,
                created_at=CREATED_AT,
            )


def test_a_cut_short_line_keeps_its_frames_and_says_so() -> None:
    """ADR 0027 rule 5: a line cut short by the end of the call is a turn of the frames sent, with
    ``tracer.caller_line_complete`` false; a complete line carries no such key."""
    bundle = build_bundle(complete_last_line=False).bundle
    turns = sorted((t for t in bundle.turns if t.source == "script"), key=lambda t: t.seq)
    assert turns[0].metadata == {}
    assert turns[1].metadata == {"tracer.caller_line_complete": False}
    assert turns[1].text == LINE_1  # the text that was synthesised, whole


def test_available_inputs_never_lists_agent_transcript_without_agent_record() -> None:
    """ADR 0027 rule 8, ADR 0004 Decision 11 and invariant I43, finding F6: the agent-channel
    speech-to-text turns have role ``agent`` but are OUR transcript; ``agent_transcript`` is listed
    only with an agent-produced transcript that has an agent-role turn."""
    # our speech-to-text of the agent channel alone: turns of role agent exist, the kind is absent
    alone = build_bundle().bundle
    assert any(t.role == "agent" and t.source == "our_stt" for t in alone.turns)
    assert "agent_transcript" not in alone.run.available_inputs
    assert "transcript" in alone.run.available_inputs
    # an export with caller lines only is an agent record without an agent line
    caller_only = export_read([("user", LINE_0), ("user", LINE_1)])
    assert "agent_transcript" not in build_bundle(export=caller_only).bundle.run.available_inputs
    # an export with an agent line is the agent's own record
    with_agent = build_bundle(export=export_read()).bundle
    assert "agent_transcript" in with_agent.run.available_inputs
    # the consequence for scoring: without the export the agreement misses exactly that input
    for key, result in results_by_key(build_bundle()).items():
        if key.startswith("stt_export_agent"):
            assert result.missing_kinds == ["agent_transcript"]
    # an export with an agent line and a failed transcription: no `transcript`, still the export
    failed = build_bundle(export=export_read(), stt_failed=True).bundle.run.available_inputs
    assert "transcript" not in failed
    assert "agent_transcript" in failed


def test_an_export_that_is_not_the_agents_own_record_lists_no_agent_transcript() -> None:
    """ADR 0027 rule 8: only a transcript the agent produced (``agent_export`` or
    ``agent_telemetry``) is an agent record. The same turns under another source (here
    ``human_reference``, a reference written by a person) do not make the kind available."""
    export = export_read()
    assert export.transcript.source in ("agent_export", "agent_telemetry")
    reference = export.transcript.model_copy(update={"source": "human_reference"})
    foreign = mod("simbundle").ExportRead(reference, export.turns, export.warnings)
    held = build_bundle(export=foreign).bundle.run.available_inputs
    assert "agent_transcript" not in held


def test_available_inputs_are_derived_from_the_records_held() -> None:
    """ADR 0027 rule 8: ``audio`` and ``agent_audio`` iff the artifact exists; ``transcript`` iff
    an ``our_stt`` transcript exists (the script transcript makes none available); ``turn_timing``
    iff a turn has both edges; never ``usage`` or ``spans``."""
    base = build_bundle().bundle.run.available_inputs
    assert base == ["audio", "agent_audio", "transcript", "turn_timing"]
    # the agent never spoke: no speech-to-text transcript, but the caller lines are timed
    silent = build_bundle(stt_texts=None).bundle.run.available_inputs
    assert silent == ["audio", "agent_audio", "turn_timing"]
    # nothing sent and nothing heard: audio only, no turn is timed
    nothing = build_bundle(lines=0, stt_texts=None).bundle.run.available_inputs
    assert nothing == ["audio", "agent_audio"]
    assert build_bundle(established=False).bundle.run.available_inputs == []
    for inputs in (base, silent, nothing):
        assert "usage" not in inputs
        assert "spans" not in inputs
        assert "agent_telemetry" not in inputs


def test_privacy_marker_false_only_with_role_evidence() -> None:
    """ADR 0027 rule 14, ADR 0016 Decisions 5 and 7a: after the call, with the declared channel
    map and turns of only the roles caller and agent, every payload is ``human_subject=false`` with
    no consent and no retention (P-46); the marker is ``true`` (consent ``unknown``, retention
    keep) exactly where the derivation is true."""
    from tracer_evalschema.privacy import derive_human_subject

    built = build_bundle(export=export_read())
    bundle = built.bundle
    assert derive_human_subject(bundle.run, bundle.turns) is False
    markers = [a.privacy for a in bundle.artifacts] + [t.privacy for t in bundle.transcripts]
    assert len(markers) == 4  # the stereo recording and the three transcripts
    for marker in markers:
        assert marker.human_subject is False
        assert marker.consent is None
        assert marker.retention is None
        assert marker.human_subject_reason is None

    # one export turn of an unknown role: the derivation is true, so every marker is
    export = export_read()
    odd = tuple(
        turn.model_copy(update={"role": "unknown"}) if turn.seq == 1 else turn
        for turn in export.turns
    )
    export = mod("simbundle").ExportRead(export.transcript, odd, export.warnings)
    tainted = build_bundle(export=export).bundle
    assert derive_human_subject(tainted.run, tainted.turns) is True
    for marker in [a.privacy for a in tainted.artifacts] + [t.privacy for t in tainted.transcripts]:
        assert marker.human_subject is True
        assert marker.consent is not None
        assert marker.consent.status == "unknown"
        assert marker.retention is not None
        assert marker.retention.delete_on_expiry is False
    check_run_bundle(tainted)


def test_the_provisional_marker_the_readers_are_given_is_replaced() -> None:
    """The reader and the engine need a marker before the roles are known: it is the conservative
    one (true, consent unknown, keep), and the bundle holds the derived one, never the provisional
    one, on every payload."""
    provisional = mod("simbundle").provisional_privacy()
    assert provisional.human_subject is True
    assert provisional.consent.status == "unknown"
    assert provisional.retention.delete_on_expiry is False
    export = export_read()
    assert export.transcript.privacy == provisional
    bundle = build_bundle(export=export).bundle
    assert all(t.privacy != provisional for t in bundle.transcripts)
    ids = {t.id for t in bundle.transcripts}
    assert {t.transcript_id for t in bundle.turns} <= ids


def test_a_failed_transcription_is_a_warning_and_the_usage_is_kept() -> None:
    """ADR 0004 Decision 10a and ADR 0027 rule 16: no ``our_stt`` transcript, a Run warning, and the
    usage of what was answered stays in the bundle."""
    built = build_bundle(export=export_read(), stt_failed=True)
    bundle = built.bundle
    assert "our_stt" not in sources_of(bundle)
    codes = [w.code for w in bundle.run.ingest_warnings]
    assert "stt_failed" in codes
    stt_warning = next(w for w in bundle.run.ingest_warnings if w.code == "stt_failed")
    assert stt_warning.path == "/stt"
    assert stt_warning.message


def test_usage_records_are_the_calls_ledger_in_order_and_not_added_twice() -> None:
    """The command's ledger (TTS, brain, speech-to-text, in the order answered) is the bundle's
    usage
    with its ids intact; the builder adds nothing of its own, so a transcription's usage records
    (already in the ledger) are not counted twice."""
    run_id = ids_mod().RunId.new()
    first = usage_record(run_id, "sim_caller_tts", input_characters=12, requests=1)
    second = usage_record(run_id, "stt", requests=1)
    built = build_bundle(usage=(first, second), run_id=run_id)
    assert [u.id for u in built.bundle.usage_records] == [first.id, second.id]
    assert built.stt is not None  # the transcription's own records are in `stt.usage`
    assert not {u.id for u in built.stt.usage} & {u.id for u in built.bundle.usage_records}
    assert [u.stage for u in built.bundle.usage_records] == ["sim_caller_tts", "stt"]


def ids_mod() -> Any:
    import importlib

    return importlib.import_module("tracer_evalschema.ids")


def test_the_module_follows_the_pure_core_rules() -> None:
    """ADR 0026 rule 2: the bundle builder reads no clock, no file and no network."""
    banned = {"time", "datetime_now", "os", "sys", "socket", "asyncio", "random", "secrets", "uuid"}
    for name in (
        "simbundle.py",
        "simscore.py",
        "simprofile.py",
        "simoutcome.py",
        "simspec.py",
        "sttparts.py",
    ):
        tree = ast.parse((SRC / name).read_text("utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert not imported & banned, (name, imported & banned)
        text = (SRC / name).read_text("utf-8")
        assert not re.search(r"\.now\(|\.utcnow\(|monotonic|urandom|getenv|environ", text), name
