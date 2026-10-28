# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 / P4-23: ``tracer simulate`` over the real engines (Sarvam TTS, chat v2 and STT), driven
through a scripted ``httpx.MockTransport`` (no socket): usage per request, the shared call log, the
TTS cache, the LLM caller and the spend caps that end a call with the right end reason.

Contract: docs/phase4/api-surface-batch-e.md section 10.3. ADR 0024 amendments A2 to A9, ADR 0026
rules 13 and 15, ADR 0028 rules 3, 9 and 10, ADR 0027 rules 15 and 17.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from cli_batchf_support import KEY_CANARY  # type: ignore[import-not-found,unused-ignore]
from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    LINE_0,
    LINE_1,
    NOW,
    STAMP,
    SarvamSim,
    all_text,
    base_args,
    llm_caller,
    profile_doc,
    real_args,
    real_env,
    real_files,
    run_sim,
    scenario_doc,
    write_inputs,
)


def stages(bundle: Any) -> list[str]:
    return [u.stage for u in bundle.usage_records]


def quantities(bundle: Any, stage: str) -> list[dict[str, Any]]:
    return [
        {q.unit: q.count for q in u.quantities} for u in bundle.usage_records if u.stage == stage
    ]


def llm_files(root: Path, *, caller: dict[str, Any] | None = None, **kw: Any) -> Any:
    return write_inputs(
        root,
        sim_profile=profile_doc(tts="sarvam", stt="sarvam", caller=caller or llm_caller(), **kw),
    )


def llm_args(files: Any, **caps: str) -> list[str]:
    args = real_args(files)
    args[args.index("--caller") + 1] = "sarvam"
    values = {"--max-llm-tokens": "500", **caps}
    for option, value in values.items():
        if option in args:
            args[args.index(option) + 1] = value
        else:
            args += [option, value]
    return list(args)


# ------------------------------------------------------------------------- usage and the log


def test_every_answered_request_is_one_usage_record_with_its_stage(tmp_path: Path) -> None:
    """ADR 0027 rule 15 and ADR 0024 A7: ``sim_caller_tts`` (characters, requests) and ``stt``
    (audio seconds, requests), domain evaluation, one record per answered request; cost is never
    invented (``not_available``) and the stored project names the key by reference only."""
    files = real_files(tmp_path)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert run.code == 0, (run.out, run.err)
    bundle = run.bundle()
    assert stages(bundle) == ["sim_caller_tts", "sim_caller_tts", "stt", "stt"]
    assert quantities(bundle, "sim_caller_tts") == [
        {"input_characters": 7, "requests": 1},
        {"input_characters": 7, "requests": 1},
    ]
    assert {u.domain for u in bundle.usage_records} == {"evaluation"}
    assert run.value("tts usage") == "2 requests, 14 characters"
    assert run.value("llm usage") == "none"
    assert run.value("stt usage") == "2 requests, 0.3 audio seconds"
    assert run.value("cost") == "not available (no_price_for_model)"
    assert {c.amount for c in bundle.cost_records if c.amount is not None} == set()
    assert all(c.status == "not_available" for c in bundle.cost_records)


def test_requests_carry_only_the_line_and_the_explicit_voice(tmp_path: Path) -> None:
    """ADR 0028 rule 3 and ADR 0024 A2: the TTS body is the caller line and the voice parameters of
    the profile, nothing else; the expected outcome, the instructions and the key are in no body."""
    files = real_files(tmp_path)
    sim = SarvamSim()
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=sim)
    assert run.code == 0
    bodies = sim.bodies("/text-to-speech")
    assert [b["text"] for b in bodies] == [LINE_0, LINE_1]
    for body in bodies:
        assert set(body) == {
            "text", "language_code", "speaker", "model", "pace", "speech_sample_rate",
            "output_audio_codec",
        }  # fmt: skip
        assert (body["model"], body["speaker"], body["language_code"]) == (
            "bulbul:v3",
            "test-voice",
            "mr-IN",
        )
        assert (body["speech_sample_rate"], body["output_audio_codec"], body["pace"]) == (
            16000,
            "wav",
            1,
        )
    sent = b"".join(r.content for r in sim.requests).decode("latin-1")
    for forbidden in ("OUTCOMECANARY", "Ask two short questions", KEY_CANARY, "synthetic helpline"):
        assert forbidden not in sent
    keyed = [r for r in sim.requests if r.headers.get("api-subscription-key") == KEY_CANARY]
    assert len(keyed) == len(sim.requests) == 4


def test_the_call_log_is_one_file_for_every_engine_without_text_or_key(tmp_path: Path) -> None:
    """ADR 0024 A8: ``sim_call_log.jsonl`` holds a ``before`` and an ``after`` line per attempt of
    every engine, one ``connect`` line and one ``end`` line with the end reason and counts; no text,
    no number, no key."""
    files = real_files(tmp_path)
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert run.code == 0
    path = files.out / "sim_call_log.jsonl"
    lines = [json.loads(x) for x in path.read_text("ascii").splitlines()]
    events = [(x["event"], x["stage"]) for x in lines]
    assert events == [
        ("before", "tts"), ("after", "tts"), ("before", "tts"), ("after", "tts"),
        ("connect", "call"), ("before", "stt"), ("after", "stt"), ("before", "stt"),
        ("after", "stt"), ("end", "call"),
    ]  # fmt: skip
    assert lines[4]["dialect"] == "pipecat-protobuf/1"
    end = lines[-1]
    assert end["end_reason"] == "script_complete"
    assert end["counts"]["tts_requests"] == 2
    assert end["counts"]["stt_requests"] == 2
    assert end["counts"]["frames_sent"] == 5 * 2  # the scripted tone is 100 ms a line
    text = path.read_text("ascii")
    for forbidden in (KEY_CANARY, "OUTCOMECANARY", "ticket 17", "ws://", "mr-IN"):
        assert forbidden not in text, forbidden
    assert (path.stat().st_mode & 0o777) == 0o600


def test_a_re_run_spends_no_tts_request_for_a_cached_line(tmp_path: Path) -> None:
    """ADR 0026 rule 13 and ADR 0024 A9: the caller audio of a scripted run is content-addressed;
    a re-run makes no TTS request and no usage record, counts two cache hits, and the caller side
    of the recording is byte-identical."""
    files = real_files(tmp_path)
    first = run_sim(files, args=real_args(files), environ=real_env(), transport=SarvamSim())
    assert first.code == 0
    cache = files.out / "tts-cache"
    assert cache.is_dir()
    assert (cache.stat().st_mode & 0o777) == 0o700
    assert len(list(cache.iterdir())) == 2
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in cache.iterdir())
    sim = SarvamSim()
    second = run_sim(
        files,
        args=real_args(files),
        environ=real_env(),
        transport=sim,
        instant=NOW + timedelta(seconds=5),
    )
    assert second.code == 0, (second.out, second.err)
    assert sim.bodies("/text-to-speech") == []
    assert second.value("tts usage") == "none"
    outcome = second.bundle().run.metadata["tracer.sim_outcome"]
    assert outcome["counts"]["tts_cache_hits"] == 2
    assert outcome["counts"]["tts_requests"] == 0
    one = (files.out / f"recording-{STAMP}.wav").read_bytes()
    two = (files.out / "recording-20261027T120005Z.wav").read_bytes()
    assert one[44::4] == two[44::4]
    assert one[45::4] == two[45::4]
    assert (files.out / "sim_call_log.jsonl").read_text("ascii").count('"event":"end"') == 2


def test_the_key_can_come_from_an_env_file_only(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"SARVAM_API_KEY={KEY_CANARY}\n")
    env_file.chmod(0o600)
    sim = SarvamSim()
    run = run_sim(
        files, args=[*real_args(files), "--env-file", str(env_file)], environ={}, transport=sim
    )
    assert run.code == 0, (run.out, run.err)
    assert all(r.headers["api-subscription-key"] == KEY_CANARY for r in sim.requests)
    assert KEY_CANARY not in run.out + run.err + all_text(files.out)
    assert str(env_file) not in run.out + run.err + all_text(files.out)


def test_a_failing_tts_provider_before_the_call_is_exit_1_and_nothing_connects(
    tmp_path: Path,
) -> None:
    files = real_files(tmp_path)
    run = run_sim(
        files, args=real_args(files), environ=real_env(), transport=SarvamSim(tts_status=503)
    )
    assert run.code == 1
    assert run.err == "error: text-to-speech failed before the call (TtsServerError: HTTP 503)\n"
    assert "connect" not in run.trace
    assert not list(files.out.glob("usage-recovery-*"))
    assert not list(files.out.glob("report-*"))


# --------------------------------------------------------------------------------- the LLM caller


def test_the_llm_caller_flow_and_its_records(tmp_path: Path) -> None:
    """ADR 0028 rules 5, 9 and 12: one chat request per turn with the explicit model, ``max_tokens``
    and seed and no tool; the agent's text (transcribed during the call) reaches the brain inside
    the
    fence; each agent segment is transcribed once; the call ends on the sentinel."""
    files = llm_files(tmp_path)
    sim = SarvamSim(chat=[LINE_0, LINE_1, "END_CALL"])
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    assert run.code == 0, (run.out, run.err)
    assert run.value("end reason") == "caller_end_call"
    chat = sim.bodies("/v2/chat/completions")
    assert len(chat) == 3
    for body in chat:
        assert set(body) == {"model", "messages", "max_tokens", "seed"}
        assert (body["model"], body["max_tokens"], body["seed"]) == ("sarvam-105b", 50, 7)
        assert body["messages"][0]["role"] == "system"
    second = json.dumps(chat[1]["messages"], ensure_ascii=True)
    assert "UNTRUSTED_AGENT_TEXT_BEGIN" in second
    assert HEARD_0.encode("unicode_escape").decode() in second
    assert "OUTCOMECANARY" not in json.dumps(chat)
    assert KEY_CANARY not in json.dumps(chat)
    assert [b["text"] for b in sim.bodies("/text-to-speech")] == [LINE_0, LINE_1]
    assert sim.paths.count("/speech-to-text") == 2  # one request per finished agent segment
    bundle = run.bundle()
    assert (
        quantities(bundle, "sim_caller_llm")
        == [{"input_tokens": 10, "output_tokens": 5, "requests": 1}] * 3
    )
    script = next(t for t in bundle.transcripts if t.source == "script")
    assert script.metadata == {"tracer.caller_text_origin": "llm"}
    outcome = bundle.run.metadata["tracer.sim_outcome"]
    assert outcome["caller_text_origin"] == "llm"
    assert outcome["counts"]["brain_requests"] == 3
    assert outcome["counts"]["llm_requests"] == 3
    assert outcome["counts"]["stt_requests"] == 2
    document = bundle.snapshot.metadata["tracer.sim_profile"]["caller"]
    assert document["prompt"]["template_id"] == "tracer.caller_prompt/1"
    assert run.value("llm usage") == "3 requests, 30 input tokens, 15 output tokens"
    turns = bundle.run.metadata["tracer.timing"]["turns"]
    assert turns[0]["self"] == {
        "reply_ms": None,
        "wait_ms": None,
        "stt_ms": None,
        "brain_ms": 0,
        "tts_ms": 0,
    }
    assert turns[1]["self"]["wait_ms"] == 300
    assert turns[1]["self"]["stt_ms"] == 0
    stt_turns = [t for t in bundle.turns if t.source == "our_stt"]
    assert [t.role for t in stt_turns] == ["agent", "agent"]


def test_the_llm_token_cap_ends_the_call_with_spend_cap(tmp_path: Path) -> None:
    """ADR 0026 rule 15: a request is sent only while the reported tokens plus its ``max_tokens``
    stay within the cap (0 + 50 <= 60, then 15 + 50 > 60): the call ends with ``spend_cap`` and the
    outcome names the cap."""
    files = llm_files(tmp_path)
    sim = SarvamSim(chat=[LINE_0, LINE_1])
    run = run_sim(
        files, args=llm_args(files, **{"--max-llm-tokens": "60"}), environ=real_env(), transport=sim
    )
    assert run.code == 0, (run.out, run.err)
    assert run.value("end reason") == "spend_cap"
    assert run.value("cap reached") == "llm_tokens"
    assert len(sim.bodies("/v2/chat/completions")) == 1
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]
    assert (outcome["end_reason"], outcome["cap_reached"]) == ("spend_cap", "llm_tokens")


def test_the_tts_character_cap_ends_an_llm_call_with_spend_cap(tmp_path: Path) -> None:
    files = llm_files(tmp_path)
    sim = SarvamSim(chat=[LINE_0, LINE_1])
    run = run_sim(
        files, args=llm_args(files, **{"--max-tts-chars": "10"}), environ=real_env(), transport=sim
    )
    assert run.value("end reason") == "spend_cap"
    assert run.value("cap reached") == "tts_chars"
    assert [b["text"] for b in sim.bodies("/text-to-speech")] == [LINE_0]  # 7 fit, 7 + 7 do not


def test_the_stt_seconds_cap_ends_an_llm_call_with_spend_cap(tmp_path: Path) -> None:
    """The in-call transcription of a 2 s agent segment would cross a 1 s cap: nothing is sent."""
    long_agent = scenario_doc(
        [{"wait_ms": 300}, {"speak": {"frames": 100, "level": 1000, "text": HEARD_0}}]
    )
    files = write_inputs(
        tmp_path,
        sim_profile=profile_doc(tts="sarvam", stt="sarvam", caller=llm_caller()),
        scenario=long_agent,
    )
    sim = SarvamSim(chat=[LINE_0, LINE_1])
    run = run_sim(
        files, args=llm_args(files, **{"--max-stt-seconds": "1"}), environ=real_env(), transport=sim
    )
    assert run.value("end reason") == "spend_cap"
    assert run.value("cap reached") == "stt_seconds"
    assert sim.paths.count("/speech-to-text") == 0


def test_a_brain_that_breaks_the_script_twice_ends_the_call(tmp_path: Path) -> None:
    """ADR 0028 rule 6: Latin script is refused, one regeneration, then ``brain_invalid_output``;
    the refused text is stored nowhere."""
    files = llm_files(tmp_path)
    sim = SarvamSim(chat=["hello there", "hello again"])
    run = run_sim(files, args=llm_args(files), environ=real_env(), transport=sim)
    assert run.value("end reason") == "brain_invalid_output"
    outcome = run.bundle().run.metadata["tracer.sim_outcome"]["counts"]
    assert (outcome["brain_invalid_outputs"], outcome["brain_regenerations"]) == (2, 1)
    assert "hello there" not in all_text(files.out)
    assert "hello again" not in all_text(files.out)
    assert run.value("caller turns") == "0 started, 0 completed"


# -------------------------------------------------------------------- the speech-to-text cap


def test_the_stt_cap_before_the_post_call_transcription_is_a_failed_transcription(
    tmp_path: Path,
) -> None:
    """A scripted run whose agent audio would cross ``--max-stt-seconds``: nothing is sent, the
    transcription fails as a whole (exit 3), the call and the recording are kept."""
    long_agent = scenario_doc(
        [{"wait_ms": 300}, {"speak": {"frames": 100, "level": 1000, "text": HEARD_0}}]
    )
    files = write_inputs(
        tmp_path, sim_profile=profile_doc(tts="sarvam", stt="sarvam"), scenario=long_agent
    )
    sim = SarvamSim()
    args = real_args(files)
    args[args.index("--max-stt-seconds") + 1] = "1"
    run = run_sim(files, args=args, environ=real_env(), transport=sim)
    assert run.code == 3, (run.out, run.err)
    assert sim.paths.count("/speech-to-text") == 0
    bundle = run.bundle()
    warning = next(w for w in bundle.run.ingest_warnings if w.code == "stt_failed")
    assert "SttCapReachedError" in warning.message
    assert "stt_seconds" in warning.message
    assert list(files.out.glob("recording-*.wav"))
    assert run.value("end reason") != "spend_cap"


def test_a_scripted_caller_makes_no_llm_request(tmp_path: Path) -> None:
    files = real_files(tmp_path)
    sim = SarvamSim()
    run = run_sim(files, args=real_args(files), environ=real_env(), transport=sim)
    assert run.code == 0
    assert "/v2/chat/completions" not in sim.paths
    counts = run.bundle().run.metadata["tracer.sim_outcome"]["counts"]
    assert counts["brain_requests"] == 0
    assert counts["llm_requests"] == 0
    assert base_args(files)  # the plain arguments still parse
