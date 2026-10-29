# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-369 (batch voice): a failure of the caller's speech synthesis during a call, in
``tracer simulate`` (auto-approved design: the new end reason ``voice_failed``, exit code 2, typed
facts only; symmetric to ``brain_failed`` of BL-338).

Contract: docs/phase4/api-surface-batch-voice.md sections 3 to 7. Before: a 401, a 500, a transport
error, a timeout or a reply that is not audio from the caller's text-to-speech in the middle of a
call ended it as ``transport_error`` with exit 0, which reads as a successful test run and hides
who failed. Now the end reason says what happened, the exit code is 2 whatever the other results
are, one line names the failure by its class (a stable code) and its HTTP status when it has one,
the report says the caller failed and nothing about the agent, and nothing the provider sent (a
message, a body, a header, the key it was shown) reaches any output. The provider is a scripted
``httpx.MockTransport``; its failures are scripted responses and raised ``httpx`` errors; the
retries use the injected sleep, so no test waits.

Reachability (contract section 6): only the LLM caller synthesises during the call. The scripted
and the replayed caller speak audio that was synthesised before the call, so a failure of their
synthesis is the existing refusal before the call (exit 1), never ``voice_failed``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    DENY,
    HEARD_0,
    LINE_0,
    LINE_1,
    REF_TEXT,
    SimRun,
    all_text,
    mod,
    real_args,
    real_env,
    real_files,
    run_sim,
)
from cli_p4g_support import (  # type: ignore[import-not-found,unused-ignore]
    PROVIDER_MESSAGE_CANARY,
    ChatSim,
    llm_args,
    llm_files,
)
from cli_p4j2_support import section_text  # type: ignore[import-not-found,unused-ignore]
from cli_p4v_support import FAILURES, VoiceSim  # type: ignore[import-not-found,unused-ignore]

NOTE = (
    "The simulated caller's voice synthesis failed during the call, so the caller stopped "
    "speaking before it had finished. This is a failure of the test caller, not a result about "
    "the agent under test."
)
BRAIN_NOTE = (
    "The simulated caller's language model failed during the call, so the caller stopped "
    "speaking before it had finished. This is a failure of the test caller, not a result about "
    "the agent under test."
)


def failing_run(root: Path, failure: str, *, good: int = 0) -> tuple[SimRun, VoiceSim, Any]:
    """The brain answers every request; the voice says the first ``good`` lines and then fails."""
    files = llm_files(root)
    sim = VoiceSim(failure, good=good, chat=[LINE_0, LINE_1])
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    return run, sim, files


def line_of(run: SimRun) -> str:
    return str(run.value("voice failure"))


def expected_line(code: str, status: str | None) -> str:
    return code if status is None else f"{code} ({status})"


@pytest.mark.parametrize(("failure", "code", "status"), FAILURES, ids=[f[0] for f in FAILURES])
def test_a_failure_of_the_voice_at_the_first_line_is_not_a_successful_run(
    tmp_path: Path, failure: str, code: str, status: str | None
) -> None:
    """The case of BL-369: the call is up, the brain has a first line and the voice cannot say it,
    zero caller turns. It is not ``transport_error`` and not exit 0: the end reason is
    ``voice_failed``, the exit code is 2, one line says which kind of failure it was, and the call
    is still stored and reported. The brain request was answered and paid, so its usage stays; the
    failed synthesis has none."""
    run, sim, files = failing_run(tmp_path, failure)
    assert run.code == 2, (run.out, run.err)
    assert run.err == ""
    assert run.value("result") == "call established"
    assert run.value("end reason") == "voice_failed"
    assert run.value("end reason") != "transport_error"
    assert line_of(run) == expected_line(code, status)
    assert run.value("caller turns") == "0 started, 0 completed"
    assert run.value("tts usage") == "none"  # a failed request has no usage
    assert run.value("llm usage") != "none"  # the brain's answer was paid and is kept
    assert run.value("outcome") == "not evaluated"
    assert sim.tts_attempts >= 1
    assert "brain failure: " not in run.out
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    assert outcome["end_reason"] == "voice_failed"
    assert outcome["call_established"] is True
    assert outcome["cap_reached"] is None
    assert outcome["caller_text_origin"] == "llm"
    assert outcome["counts"]["brain_requests"] == 1
    assert outcome["counts"]["llm_requests"] == 1
    assert outcome["counts"]["caller_turns_started"] == 0
    assert [u.stage for u in run.bundle().usage_records] == ["sim_caller_llm"]
    assert list(files.out.glob("usage-recovery-*.json")) == []  # the run was stored whole
    assert list(files.out.glob("report-*.html"))
    assert list(files.out.glob("recording-*.wav"))  # the call was up: its recording exists
    assert (files.out / "tracer.sqlite").is_file()
    assert list((files.out / "tts-cache").iterdir()) == []  # a failed reply is never cached


def test_the_voice_line_sits_between_the_summary_and_the_paths(tmp_path: Path) -> None:
    run, _, _ = failing_run(tmp_path, "500")
    lines = run.lines
    at = lines.index("voice failure: tts_server_error (HTTP 500)")
    assert lines[at - 1].startswith("ingest warnings: ")
    assert lines[at + 1].startswith("bundle: ")
    assert sum(1 for x in lines if x.startswith("voice failure: ")) == 1


@pytest.mark.parametrize(("failure", "code", "status"), FAILURES, ids=[f[0] for f in FAILURES])
def test_a_voice_failure_after_a_turn_keeps_everything_recorded_and_exits_2_alone(
    tmp_path: Path, failure: str, code: str, status: str | None
) -> None:
    """The first line is spoken, the agent answers, the transcription is paid, the brain gives a
    second line (paid) and then its synthesis fails. Every result is scored or not applicable,
    none is an error, so nothing but the end reason makes the exit code 2. The usage of the
    answered requests is in the stored run in the order they were answered (the brain, the TTS,
    the transcription, the brain again); the recording has the line and the agent's reply."""
    run, sim, files = failing_run(tmp_path, failure, good=1)
    assert run.code == 2, (run.out, run.err)
    assert run.value("end reason") == "voice_failed"
    assert line_of(run) == expected_line(code, status)
    assert run.value("caller turns") == "1 started, 1 completed"
    assert run.value("agent segments") == "1"
    bundle = run.bundle()
    assert [u.stage for u in bundle.usage_records] == [
        "sim_caller_llm",
        "sim_caller_tts",
        "stt",
        "sim_caller_llm",
    ]
    assert all(r.status in ("scored", "not_applicable") for r in bundle.results)
    assert all(r.status != "error" for r in bundle.results)
    assert run.value("first agent audio").startswith("400 ms")
    assert run.value("tts usage") == "1 requests, 7 characters"  # the failed request is not there
    assert run.value("llm usage") == "2 requests, 20 input tokens, 10 output tokens"
    assert sim.paths.count("/speech-to-text") == 1  # the segment heard before the failure
    assert sim.paths.count("/v2/chat/completions") == 2
    outcome = bundle.run.metadata["tracer.sim_outcome"]
    assert outcome["counts"]["llm_requests"] == 2
    assert outcome["counts"]["brain_requests"] == 2
    assert outcome["counts"]["caller_turns_started"] == 1
    assert list(files.out.glob("recording-*.wav"))
    assert list(files.out.glob("usage-recovery-*.json")) == []
    assert HEARD_0.encode("utf-8") in (files.out / "tracer.sqlite").read_bytes()
    assert len(list((files.out / "tts-cache").iterdir())) == 1  # only the good reply


@pytest.mark.parametrize("failure", [f[0] for f in FAILURES])
def test_nothing_the_voice_provider_sent_reaches_any_output(
    tmp_path: Path, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0024 rule 4 pattern: a message, a body and a header of the provider, the key (which an
    error body may echo) and the authorisation reference are in no stream, no file and no log
    record (at DEBUG, for the loggers of the HTTP stack and of Tracer); the call log holds
    statuses and counts only."""
    import logging

    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ("httpx", "httpcore", "tracer_cli", "tracer_evalproviders", "tracer_evalsim"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    run, sim, files = failing_run(tmp_path, failure)
    everything = run.out + run.err + all_text(files.out)
    assert sim.requests
    for secret in (PROVIDER_MESSAGE_CANARY, KEY_CANARY, REF_TEXT):
        assert secret not in everything, secret
        assert secret not in caplog.text, secret
    assert "Traceback" not in everything
    assert run.out.isascii()
    assert run.err == ""


def test_the_voice_line_with_a_status_never_exposes_more_than_the_status(
    tmp_path: Path,
) -> None:
    run, _, _ = failing_run(tmp_path, "401")
    assert line_of(run) == "tts_auth (HTTP 401)"
    assert len(line_of(run)) < 40
    assert line_of(run).isascii()


def test_the_call_log_ends_with_voice_failed_and_counts(tmp_path: Path) -> None:
    run, _, files = failing_run(tmp_path, "500", good=1)
    log = [
        json.loads(x) for x in (files.out / "sim_call_log.jsonl").read_text("ascii").splitlines()
    ]
    end = log[-1]
    assert end["event"] == "end"
    assert end["end_reason"] == "voice_failed"
    assert end["counts"]["llm_requests"] == 2
    assert end["counts"]["caller_turns_started"] == 1
    assert run.code == 2


def test_the_exit_code_table_says_what_two_means_with_the_voice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR 0026 rule 19 (amended again): 2 is also 'the caller's voice failed', in the table and
    in ``tracer simulate --help``. The Phase 3 table of ``tracer score`` does not change."""
    meaning = "a result was not scored, or the caller's language model or voice failed"
    simargs = mod("simargs")
    assert simargs.SIM_EXIT_CODES[2] == meaning
    assert simargs.SIM_EXIT_CODES.keys() == {0, 1, 2, 3, 4, 5, 64}
    assert mod("cli").EXIT_CODES[2] == "not scored"
    with pytest.raises(SystemExit) as caught:
        mod("cli").main(["simulate", "--help"])
    assert caught.value.code == 0
    assert meaning in " ".join(capsys.readouterr().out.split())


def test_exit_code_of_follows_the_precedence_with_voice_failed(tmp_path: Path) -> None:
    """Precedence 1, 5, 3, 2, 0 (ADR 0026 rule 19): a voice failure is a 2; a failed
    speech-to-text of the agent channel (3) outranks it."""
    from tracer_evalschema.events import IngestWarning

    run, _, _ = failing_run(tmp_path, "401", good=1)
    bundle = run.bundle()
    simsummary = mod("simsummary")
    assert simsummary.exit_code_of(bundle) == 2
    warned = bundle.model_copy(
        update={
            "run": bundle.run.model_copy(
                update={
                    "ingest_warnings": [IngestWarning(code="stt_failed", path="/stt", message="x")]
                }
            )
        }
    )
    assert simsummary.exit_code_of(warned) == 3


# ----------------------------------------------------------------- what is not changed


def test_a_spend_cap_of_the_voice_in_the_call_is_still_a_spend_cap(tmp_path: Path) -> None:
    """The regression guard of the old behaviour that stays: a text-to-speech cap reached in the
    call is ``spend_cap`` naming ``tts_chars`` (exit 0, a cap is not a failure), never
    ``voice_failed``. The cap of 10 characters admits the first line (7) and refuses the second."""
    files = llm_files(tmp_path / "cap")
    sim = VoiceSim("500", good=9, chat=[LINE_0, LINE_1])
    args = llm_args(files, **{"--max-tts-chars": "10"})
    run = run_sim(files, args=args, environ=real_env(), transport=sim)
    assert run.value("end reason") == "spend_cap"
    assert run.code == 0, (run.out, run.err)
    assert "voice failure: " not in run.out
    assert run.bundle().run.metadata["tracer.sim_outcome"]["cap_reached"] == "tts_chars"


def test_a_failure_of_the_brain_is_still_brain_failed_and_names_its_own_line(
    tmp_path: Path,
) -> None:
    files = llm_files(tmp_path / "brain")
    sim = ChatSim("500", good=0)
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    assert run.code == 2
    assert run.value("end reason") == "brain_failed"
    assert run.value("brain failure") == "llm_server_error (HTTP 500)"
    assert "voice failure: " not in run.out
    assert section_text(run.report(), "voice-failed-note") == ""


@pytest.mark.parametrize("failure", ["500", "401", "timeout", "headerless"])
def test_a_scripted_caller_cannot_lose_its_voice_in_the_call(tmp_path: Path, failure: str) -> None:
    """Reachability (contract section 6): the scripted caller's lines are synthesised before the
    call, so a failure of the synthesis is the existing refusal before the call: exit 1, one line
    on stderr, no connection, no stored run, and never ``voice_failed`` or its line."""
    files = real_files(tmp_path)
    sim = VoiceSim(failure, good=1)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=sim)
    assert run.code == 1
    assert run.err.startswith("error: text-to-speech failed before the call (Tts")
    assert "voice" not in run.out
    assert "connect" not in run.trace
    assert not list(files.out.glob("report-*"))
    assert "bundle:" not in run.out
    assert KEY_CANARY not in run.out + run.err
    assert PROVIDER_MESSAGE_CANARY not in run.out + run.err


def test_a_cache_entry_that_is_not_audio_is_a_voice_failure_with_no_status(
    tmp_path: Path,
) -> None:
    """A failure that no request causes: the cache holds an entry for the first line that is not
    audio of the stated codec (three bytes of 16-bit samples). The engine answers
    ``TtsResponseInvalid`` with the reason ``cache_entry_invalid`` and no HTTP status, so the line
    is the bare key. No request of the voice is sent; the brain's request is."""
    import importlib

    cache_key = importlib.import_module("tracer_evalproviders.ttscache").tts_cache_key
    files = llm_files(tmp_path)
    voice = json.loads(files.sim_profile.read_text("utf-8"))["voices"]["caller-a"]
    key = cache_key(
        provider=voice["provider"],
        model=voice["model"],
        speaker=voice["speaker"],
        language_code=voice["language_code"],
        sample_rate_hz=voice["sample_rate_hz"],
        codec=voice["codec"],
        pace=voice["pace"],
        text=LINE_0,
    )
    cache = files.out / "tts-cache"
    cache.mkdir(parents=True, mode=0o700)
    (cache / key).write_bytes(b"\x00\x01\x02")
    sim = VoiceSim("500", good=9, chat=[LINE_0, LINE_1])
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    assert run.code == 2, (run.out, run.err)
    assert run.value("end reason") == "voice_failed"
    assert line_of(run) == "tts_response_invalid"
    assert "/text-to-speech" not in sim.paths
    assert run.value("caller turns") == "0 started, 0 completed"
    assert run.err == ""


# ----------------------------------------------------------------------------- the report


def test_the_report_says_the_voice_failed_and_says_nothing_about_the_agent(
    tmp_path: Path,
) -> None:
    run, _, _ = failing_run(tmp_path, "500", good=1)
    page = run.report()
    assert section_text(page, "voice-failed-note") == NOTE
    assert '<p id="voice-failed-note" class="warning">' in page
    assert not DENY.search(NOTE)
    assert section_text(page, "brain-failed-note") == ""
    assert section_text(page, "end-reason") == "end reason: voice_failed"
    assert 'data-end-reason="voice_failed"' in page
    assert PROVIDER_MESSAGE_CANARY not in page
    assert KEY_CANARY not in page
    assert section_text(page, "outcome-label").startswith("Outcome not evaluated")
    assert section_text(page, "synthetic-label").startswith("Synthetic caller audio")


def test_the_brain_note_is_not_in_a_voice_report_and_the_voice_note_not_in_a_brain_report(
    tmp_path: Path,
) -> None:
    assert NOTE != BRAIN_NOTE
    brain_files = llm_files(tmp_path / "brain")
    brain = run_sim(
        brain_files,
        args=llm_args(brain_files),
        environ=real_env(),
        transport=ChatSim("500", good=0),
    )
    assert section_text(brain.report(), "brain-failed-note") == BRAIN_NOTE
    assert "voice-failed-note" not in brain.report()
    voice, _, _ = failing_run(tmp_path / "voice", "500")
    assert "brain-failed-note" not in voice.report()


def test_other_reports_have_no_voice_note(tmp_path: Path) -> None:
    from cli_p4e_support import write_inputs  # type: ignore[import-not-found,unused-ignore]

    run = run_sim(write_inputs(tmp_path))
    assert "voice-failed-note" not in run.report()
    assert section_text(run.report(), "voice-failed-note") == ""


# ------------------------------------------------------------------------------- the store


def test_a_voice_failed_run_round_trips_through_the_store_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0027 invariant I9 for the new end reason: the stored run reads back equal, byte for
    byte; storing it again changes nothing; the bundle passes ``check_run_bundle``."""
    from tracer_evalschema.bundle import check_run_bundle

    store = mod("store")
    original = store.store_run
    seen: dict[str, Any] = {}

    def spy(engine: Any, *, project: Any, agent_target: Any, bundle: Any) -> Any:
        seen.update(project=project, target=agent_target, bundle=bundle)
        return original(engine, project=project, agent_target=agent_target, bundle=bundle)

    monkeypatch.setattr(store, "store_run", spy)
    run, _, files = failing_run(tmp_path, "401", good=1)
    assert run.code == 2
    bundle = seen["bundle"]
    check_run_bundle(bundle)
    engine = store.open_sqlite_store(files.out)
    try:
        loaded = store.load_run(engine, bundle.id)
        assert loaded == bundle
        assert loaded.model_dump_json().encode("utf-8") == bundle.model_dump_json().encode("utf-8")
        assert loaded.run.metadata["tracer.sim_outcome"]["end_reason"] == "voice_failed"
        again = store.store_run(
            engine, project=seen["project"], agent_target=seen["target"], bundle=bundle
        )
        assert again.created is False
        assert sum(again.inserted.values()) == 0
    finally:
        engine.dispose()


def test_the_stored_voice_outcome_is_the_adr_document_with_the_new_reason(tmp_path: Path) -> None:
    from tracer_evalcore.simoutcome import SIM_OUTCOME_JSON_SCHEMA, validate_sim_outcome

    run, _, _ = failing_run(tmp_path, "500")
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    validate_sim_outcome(outcome, brain_mode="llm")
    assert outcome["end_reason"] == "voice_failed"
    assert outcome["end_reason"] in SIM_OUTCOME_JSON_SCHEMA["properties"]["end_reason"]["enum"]
    assert outcome["caller_text_origin"] == "llm"
    assert outcome["outcome"] == "not_evaluated"
