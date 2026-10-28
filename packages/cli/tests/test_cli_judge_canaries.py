# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J25: the judge key canaries and the no-socket guard (ADR 0030 rules 15 and 25; ADR 0024
amendment A4 and A12; ADR 0007; the batch E ``assert_sim_three_keys_never_in_outputs`` as model).

Contract: docs/phase4/api-surface-batch-j2.md section 13. The judge key is a canary read from the
variable the matched allowlist entry names (from the environment, or from an env file for that name
only). No byte of any output channel holds it: standard output and error, the exception text, the
representations of the key and of the objects that hold it, the DEBUG log, the exchange files, the
store, the page, the call log, the recovery file, the labels files and the agreement report. The
authorisation reference text is in none of them either. Every run here uses an
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli_p4e_support import REF_TEXT, run_sim, write_inputs  # type: ignore[import-not-found,unused-ignore]
from cli_p4j2_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY,
    ENV_VAR,
    PUBLIC_IP,
    PUBLIC_URL,
    JudgeSim,
    all_text,
    assert_judge_tests_open_no_socket,
    chat_response,
    interrupt,
    jmod,
    pmod,
    public_add,
    public_files,
    resolver,
    run_cmd,
    two_answers,
)
from tracer_evalcore.llm import LlmMessage
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalschema.ids import RunId

SECRETS = (CANARY, REF_TEXT)


def _run(
    root: Path,
    *,
    mode: str,
    via_file: bool,
) -> tuple[Any, Any, JudgeSim, str]:
    files = write_inputs(root, scenario=two_answers())
    jf = public_files(files)
    args = public_add(jf)
    environ: dict[str, str] = {}
    if via_file:
        env_file = root / "keys.env"
        env_file.write_text(f"DECOY=1\n{ENV_VAR}={CANARY}\n", "utf-8")
        env_file.chmod(0o600)
        args += ["--env-file", str(env_file)]
    else:
        environ[ENV_VAR] = CANARY
    echo = chat_response(
        None,
        status=401,
        extra={"error": {"code": "invalid_api_key", "message": f"bad key {CANARY}"}},
    )
    boom = httpx.ConnectError(f"cannot reach the host with {CANARY}")

    def refuse(request: httpx.Request, index: int) -> httpx.Response:
        raise boom

    scripts = {
        "ok": [],
        "unauthorised": [echo],
        "transport": [refuse] * 8,
        "interrupted": [chat_response("x"), interrupt],
    }
    sim = JudgeSim(scripts[mode])
    result = run_cmd(
        [
            "simulate",
            "--agent",
            f"fake:{files.scenario}",
            "--evaluator",
            str(files.evaluator),
            "--persona",
            str(files.persona),
            "--sim-profile",
            str(files.sim_profile),
            "--tts",
            "fake",
            "--caller",
            "scripted",
            "--stt",
            "fake",
            "--authorisation-reference",
            REF_TEXT,
            "--out",
            str(files.out),
            *args,
        ],
        environ=environ,
        transport=sim,
        world_kw={"judge_resolver": resolver(PUBLIC_IP), "run_blocking": _inline},
    )
    streams = result.out + result.err
    return files, jf, sim, streams


async def _inline(function: Any) -> Any:
    return function()


@pytest.mark.parametrize("via_file", [False, True], ids=["environment", "env-file"])
@pytest.mark.parametrize("mode", ["ok", "unauthorised", "transport", "interrupted"])
def test_judge_key_canaries_never_in_any_output_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str, via_file: bool
) -> None:
    """ADR 0030 rule 15 and ADR 0024 amendment A12: the key, which the provider is given in the
    ``Authorization`` header and nowhere else, is in no output of the run, whether the run
    succeeds, the provider refuses the key and echoes it, the transport fails with a message that
    holds it, or the run is interrupted. Nor is the text of the authorisation reference. The
    check is not vacuous: the provider did receive the key, and the text of the call is found in
    the store, the page and the exchange files."""
    caplog.set_level(logging.DEBUG)
    for name in ("", "httpx", "httpcore", "tracer_cli", "tracer_evalproviders", "tracer_evalcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    files, jf, sim, streams = _run(tmp_path, mode=mode, via_file=via_file)
    assert sim.requests, "the provider was asked"
    assert {r.headers["authorization"] for r in sim.requests} == {f"Bearer {CANARY}"}
    everything = streams + all_text(files.out) + caplog.text
    for secret in SECRETS:
        assert secret not in everything, (mode, secret[:12])
    assert ENV_VAR not in streams, "the variable name is not printed"
    assert "Authorization" not in all_text(files.out)
    assert "Bearer" not in everything
    if mode == "ok":
        exchange_files = sorted((files.out / "judge").rglob("item-*.json"))
        assert len(exchange_files) == 2
        assert "\u0939\u094b\u092f".encode() in (files.out / "tracer.sqlite").read_bytes()
        assert "authorization" not in "".join(p.read_text("utf-8").lower() for p in exchange_files)
    if mode == "interrupted":
        (recovery,) = list(files.out.glob("usage-recovery-*.json"))
        assert CANARY not in recovery.read_text("ascii")
    # the labels files and the agreement report of a judged run hold no key either
    if mode == "ok":
        bundle = re.search(r"bundle: (rbnd_\S+)", streams)
        assert bundle is not None
        packet = run_cmd(
            [
                "labels",
                "packet",
                "--out",
                str(files.out),
                "--bundle",
                bundle.group(1),
                "--golden",
                str(jf.golden),
            ],
        )
        assert packet.code == 0, (packet.out, packet.err)
        path = Path(packet.value("packet"))
        labels = tmp_path / "labels.json"
        labels.write_text(
            json.dumps(json.loads(path.read_text("utf-8"))["labels_template"]), "utf-8"
        )
        checked = run_cmd(["labels", "check", "--out", str(files.out), "--labels", str(labels)])
        report = run_cmd(
            ["labels", "agreement", "--out", str(files.out), "--bundle", bundle.group(1)]
        )
        assert checked.code == 0, (checked.err, report.err)
        assert report.code == 0, (checked.err, report.err)
        for text in (packet.out, checked.out, report.out, all_text(files.out / "labels")):
            assert CANARY not in text
            assert REF_TEXT not in text


def test_judge_key_is_not_in_the_representation_of_anything_that_holds_it(tmp_path: Path) -> None:
    """The key is held in a redacting wrapper; the bound endpoint, the engine and the errors a
    refused key produces show no part of it in ``repr``, ``str``, ``format``, a traceback or an
    attribute. (Part 1 proves the wrapper; this proves the objects the command builds.)"""
    import traceback

    judgehosts, openai_compat = pmod("judgehosts"), pmod("openai_compat")
    secrets = pmod("secrets")
    entries = judgehosts.parse_judge_allowlist(f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}\n")
    bound = judgehosts.bind_endpoint(
        entries,
        url=PUBLIC_URL,
        profile="openai/1",
        environ={ENV_VAR: CANARY},
        resolver=resolver(PUBLIC_IP),
        first_request_gate=True,
    )
    assert isinstance(bound.api_key, secrets.ApiKey)
    texts = [repr(bound), str(bound), repr(bound.api_key), str(bound.api_key), f"{bound.api_key}"]
    sim = JudgeSim(
        [chat_response(None, status=401, extra={"error": {"code": "x", "message": CANARY}})]
    )
    out = tmp_path / "log"
    log = SimCallLog.open(out, clock=lambda: datetime(2026, 10, 27, tzinfo=UTC))
    engine = openai_compat.create_judge_engine(
        bound, call_log=log, max_requests=3, max_tokens=1000, transport=sim, sleep=lambda s: None
    )
    texts += [repr(engine), str(engine)]
    judge = jmod("judge")
    request = judge.JudgeRequest(
        index=0, item_id="q0", attempt=1, match_mode="equivalent_answer", fact_count=0,
        messages=(LlmMessage(role="system", content="s"), LlmMessage(role="user", content="u")),
        params=judge.JudgeParams(model="m-1", max_tokens=10, temperature=None, seed=None,
                                 reasoning_effort=None, provider_slug=None, extra_headers=()),
    )  # fmt: skip
    context = judge.JudgeContext(run_id=RunId.new(), created_at=datetime(2026, 10, 27, tzinfo=UTC))
    outcome = engine.complete(request, context)
    failure = outcome.failure
    assert isinstance(failure, judge.JudgeAuthError)
    texts += [repr(failure), str(failure), "".join(traceback.format_exception(failure))]
    texts += [repr(vars(failure)), repr(outcome)]
    log.close()
    for text in texts:
        assert CANARY not in text
    assert all(part not in " ".join(texts) for part in (CANARY[:10], CANARY[-8:]))


# --------------------------------------------------------------------------- no socket


def test_judge_tests_open_no_socket_except_through_a_mock_transport() -> None:
    """The judge half of ``test_unit_tests_open_no_socket`` (the spec change makes the named test
    call the same check): the harness cannot reach the network, and no test builds a client."""
    assert_judge_tests_open_no_socket()
    import socket

    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("judge.example.test", 443)
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("api.openai.com", 443)
    # the default resolver of the allowlist check would ask the network: it fails closed here
    judgehosts = pmod("judgehosts")
    entries = judgehosts.parse_judge_allowlist(f"{PUBLIC_URL} profile=openai/1 key_env={ENV_VAR}\n")
    with pytest.raises(AssertionError, match="network"):
        judgehosts.check_endpoint(
            entries, url=PUBLIC_URL, profile="openai/1", first_request_gate=True
        )
    # and a run that names no resolver seam cannot resolve a public name either
    files = write_inputs(Path(tempfile.mkdtemp()), scenario=two_answers())
    jf = public_files(files)
    run = run_sim(files, add=public_add(jf), environ={ENV_VAR: CANARY}, transport=JudgeSim())
    assert run.code == 1
    assert run.err == "error: unexpected failure (AssertionError)\n"
    assert not files.out.exists()
