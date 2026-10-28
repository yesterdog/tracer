# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""Shared helpers of the Phase 4 batch J part 2 cli tests (``tracer simulate --golden``, ``tracer
judge``, ``tracer labels``; P4-J17 to P4-J25). Support module, not collected.
Contract: docs/phase4/api-surface-batch-j2.md.

The commands run in-process through ``tracer_cli.cli.run`` with an injected ``Environment`` (the
batch E harness of ``cli_p4e_support``). The judge provider is ``JudgeSim``, an
``httpx.MockTransport`` that answers from the request alone: no socket is opened by any test that
uses this module. Keys are canaries built from parts. All text is invented. Nothing of the batch J
modules is imported at module level, so before they exist each test fails on its own and the
directory still collects.
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from cli_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    HEARD_0,
    HEARD_1,
    LINE_0,
    LINE_1,
    NOW,
    Files,
    SimRun,
    VirtualClock,
    mod,
    run_sim,
    scenario_doc,
    write_inputs,
)

TESTS = Path(__file__).resolve().parent
#: the format members, written in two pieces: the repository guard refuses a tracked file that holds
#: one of them as a single string (it is how it recognises a labels file or a packet)
LABELS_FORMAT = "tracer.judge_" + "labels/1"
PACKET_FORMAT = "tracer.judge_" + "packet/1"
JUDGE_URL = "http://127.0.0.1:8123/v1/chat/completions"
PUBLIC_URL = "https://judge.example.test/v1/chat/completions"
PUBLIC_IP = "93.184.216.34"
ENV_VAR = "JUDGE_KEY_VAR"
#: a stand-in for a key, built from parts so that no scanner sees a key-shaped literal
CANARY = "judgecanary" + "-".join(["a1", "b2", "c3"]) + "".join(["d4", "e5"])
MODEL = "judge-model-1"
REF_TEXT = "Approved to send the synthetic call text, ticket 21"
FENCE_OPEN = "<<<JUDGE_INPUT_BEGIN>>>"
FENCE_CLOSE = "<<<JUDGE_INPUT_END>>>"
#: the claim words of ADR 0023 rule 1, A1 and A4 (whole words, case-insensitive)
CLAIM_WORDS = re.compile(
    r"(?i)(?<![\w])(?:accuracy|wer|cer|error rate|correct|incorrect|correctness|recall|precision"
    r"|latency|response time|quality|task success|realism|realistic|fidelity|deterministic"
    r"|determinism|factual|factuality|truthful|hallucination|ground truth|calibrated|probability"
    r"|confidence|pass|passed|fail|failed|score of the agent)(?![\w])"
)


def jmod(name: str) -> Any:
    return importlib.import_module(f"tracer_evalcore.{name}")


def pmod(name: str) -> Any:
    return importlib.import_module(f"tracer_evalproviders.{name}")


# ------------------------------------------------------------------------------------ inputs


def two_answers() -> dict[str, Any]:
    """A fake agent that answers each of the two caller lines outside the line (the answer window
    of item 0 holds the first segment and that of item 1 the second)."""
    return scenario_doc(  # type: ignore[no-any-return]
        [
            {"wait_ms": 600},
            {"speak": {"frames": 5, "level": 1000, "text": HEARD_0}},
            {"wait_ms": 1500},
            {"speak": {"frames": 5, "level": 1000, "text": HEARD_1}},
        ]
    )


def golden_doc(items: Sequence[Mapping[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.golden_set/1",
        "key": "mr_p4j2_set",
        "language": "mr",
        "items": list(items)
        if items is not None
        else [
            {
                "id": "q0",
                "scripted_turn_index": 0,
                "question": LINE_0,
                "match_mode": "all_facts",
                "expected_facts": [HEARD_0],
                "needs_native_review": True,
            },
            {
                "id": "q1",
                "scripted_turn_index": 1,
                "question": LINE_1,
                "match_mode": "equivalent_answer",
                "expected_answer": HEARD_1,
                "needs_native_review": True,
            },
        ],
    }
    doc.update(changes)
    return doc


def config_doc(
    profile: str = "local_loopback/1", endpoint: str | None = JUDGE_URL, **changes: Any
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "tracer.judge_config/1",
        "profile": profile,
        "endpoint": endpoint,
        "model": MODEL,
        "max_tokens": 300,
        "temperature": None,
        "seed": None,
        "reasoning_effort": None,
        "provider_slug": None,
        "extra_headers": {},
    }
    doc.update(changes)
    return doc


@dataclass
class JudgeFiles:
    golden: Path
    config: Path
    allowlist: Path


def write_judge_files(
    files: Files,
    *,
    golden: Any = None,
    config: Any = None,
    allowlist_line: str | None = None,
) -> JudgeFiles:
    """The golden set, the judge configuration (default: the loopback profile) and the judge
    allowlist (mode 0600) beside the other inputs."""

    def put(name: str, doc: Any, mode: int = 0o644) -> Path:
        path = files.root / name
        path.write_text(
            doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=True), "utf-8"
        )
        path.chmod(mode)
        return path  # type: ignore[no-any-return]

    return JudgeFiles(
        golden=put("golden.json", golden if golden is not None else golden_doc()),
        config=put("judge.json", config if config is not None else config_doc()),
        allowlist=put(
            "judge-allowlist.txt",
            (
                allowlist_line
                if allowlist_line is not None
                else f"{JUDGE_URL} profile=local_loopback/1"
            )
            + "\n",
            0o600,
        ),
    )


def judge_add(jf: JudgeFiles, engine: str, *extra: str) -> list[str]:
    """The options that turn a ``tracer simulate`` run into a judged one."""
    return [
        "--golden", str(jf.golden),
        "--judge-config", str(jf.config),
        "--judge", engine,
        *extra,
    ]  # fmt: skip


def loopback_add(jf: JudgeFiles, requests: int = 10, tokens: int = 100_000) -> list[str]:
    """A judged run through the real client of the loopback profile (an ``httpx.MockTransport``)."""
    return judge_add(
        jf,
        "local_loopback/1",
        "--judge-allowlist", str(jf.allowlist),
        "--max-judge-requests", str(requests),
        "--max-judge-tokens", str(tokens),
    )  # fmt: skip


def public_files(
    files: Files, *, profile: str = "openai/1", config: Mapping[str, Any] | None = None
) -> JudgeFiles:
    """A real-provider entry on a public name; the key is read from ``ENV_VAR``."""
    return write_judge_files(
        files,
        config=config_doc(profile, PUBLIC_URL, **dict(config or {})),
        allowlist_line=f"{PUBLIC_URL} profile={profile} key_env={ENV_VAR}",
    )


def public_add(jf: JudgeFiles, profile: str = "openai/1", requests: int = 10) -> list[str]:
    return judge_add(
        jf,
        profile,
        "--judge-allowlist", str(jf.allowlist),
        "--max-judge-requests", str(requests),
        "--max-judge-tokens", "100000",
        "--judge-authorisation-reference", REF_TEXT,
        "--judge-first-request-gate",
    )  # fmt: skip


def resolver(*answers: str) -> Callable[[str, int], list[str]]:
    seen: list[tuple[str, int]] = []

    def resolve(host: str, port: int) -> list[str]:
        seen.append((host, port))
        return list(answers)

    resolve.seen = seen  # type: ignore[attr-defined]
    return resolve


# ------------------------------------------------------------------------- the judge provider


def chat_response(
    content: str | None,
    *,
    model: str = MODEL,
    rid: str = "chatcmpl-1",
    prompt: int = 120,
    completion: int = 15,
    finish: str = "stop",
    status: int = 200,
    extra: Mapping[str, Any] | None = None,
) -> httpx.Response:
    headers = {"content-type": "application/json", "date": "Tue, 27 Oct 2026 12:00:01 GMT"}
    if status != 200:
        return httpx.Response(status, headers=headers, json=dict(extra or {"error": {"code": "x"}}))
    body: dict[str, Any] = {
        "id": rid,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }  # fmt: skip
    body.update(extra or {})
    return httpx.Response(200, headers=headers, json=body)


def user_document(request: httpx.Request) -> dict[str, Any]:
    """The JSON document between the fences of the user message of a judge request."""
    body = json.loads(request.content)
    text = body["messages"][1]["content"]
    lines = text.split("\n")
    assert lines[0] == FENCE_OPEN, "the user message is fenced"
    assert lines[-1] == FENCE_CLOSE, "the user message is fenced"
    return json.loads(lines[1])  # type: ignore[no-any-return]


def verdict_for(document: Mapping[str, Any], verdict: str = "agrees") -> str:
    """A reply that is valid for the request's item (consistent fields) with the given verdict."""
    facts = document.get("expected_facts")
    table = {
        "agrees": ("all_facts_stated", "meaning_same", "stated"),
        "disagrees": ("fact_absent", "meaning_differs", "absent"),
        "cannot_tell": ("insufficient_information", "insufficient_information", "cannot_tell"),
    }
    all_code, equal_code, status = table[verdict]
    if facts is None:
        return json.dumps({"verdict": verdict, "reason_code": equal_code, "facts": []})
    return json.dumps(
        {
            "verdict": verdict,
            "reason_code": all_code,
            "facts": [{"index": i, "status": status} for i in range(len(facts))],
        }
    )


class JudgeSim(httpx.MockTransport):
    """A scripted OpenAI-compatible judge. ``script`` is a list of replies used in order (a string
    is the message content of a 200 reply, an ``httpx.Response`` is sent as it is, a callable gets
    the request and its index); once the list is exhausted, or if there is none, it answers
    ``default`` (``agrees`` by default) with a reply that is valid for the request. A handler that
    raises is how a test interrupts the run. Every request is kept."""

    def __init__(
        self,
        script: Sequence[Any] = (),
        *,
        default: str = "agrees",
        prompt: int = 120,
        completion: int = 15,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._script = list(script)
        self._default = default
        self._tokens = (prompt, completion)
        super().__init__(self._handle)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    def documents(self) -> list[dict[str, Any]]:
        return [user_document(r) for r in self.requests]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        index = len(self.requests)
        self.requests.append(request)
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404, json={})
        prompt, completion = self._tokens
        if index < len(self._script):
            step = self._script[index]
            if callable(step):
                step = step(request, index)
            if isinstance(step, httpx.Response):
                return step
            return chat_response(
                step, rid=f"chatcmpl-{index}", prompt=prompt, completion=completion
            )
        content = verdict_for(user_document(request), self._default)
        return chat_response(content, rid=f"chatcmpl-{index}", prompt=prompt, completion=completion)


# --------------------------------------------------------------------------------- running


def run_judged(
    root: Path,
    *,
    engine: str = "fake:agrees",
    scenario: Mapping[str, Any] | None = None,
    golden: Any = None,
    config: Any = None,
    sim: JudgeSim | None = None,
    extra: Sequence[str] = (),
    environ: Mapping[str, Any] | None = None,
    world_kw: Mapping[str, Any] | None = None,
    allowlist_line: str | None = None,
    drop: Sequence[str] = (),
    evaluator: Mapping[str, Any] | None = None,
    sim_profile: Mapping[str, Any] | None = None,
    instant: datetime = NOW,
) -> tuple[SimRun, Files, JudgeFiles]:
    """``tracer simulate`` offline (fake agent, tone synthesis, scenario speech-to-text) with a
    judge. ``engine`` ``fake:<scenario>`` needs no provider; ``local_loopback/1`` goes through
    ``sim`` (an ``httpx.MockTransport``) with the standard caps."""
    files = write_inputs(
        root,
        scenario=scenario if scenario is not None else two_answers(),
        evaluator=evaluator,
        sim_profile=sim_profile,
    )
    if engine == "fake:*":
        raise AssertionError("name a scenario")
    if config is None and engine.startswith("fake:"):
        config = config_doc("fake/1", None)
    jf = write_judge_files(files, golden=golden, config=config, allowlist_line=allowlist_line)
    add = loopback_add(jf) if engine == "local_loopback/1" else judge_add(jf, engine)
    run = run_sim(
        files,
        add=[*add, *extra],
        drop=drop,
        environ=environ,
        transport=sim,
        world_kw=world_kw,
        instant=instant,
    )
    return run, files, jf


@dataclass
class CmdRun:
    code: int | None
    out: str
    err: str
    raised: BaseException | None
    trace: list[str]

    def value(self, label: str) -> str:
        found = [x for x in self.out.splitlines() if x.startswith(f"{label}: ")]
        assert len(found) == 1, (label, self.out)
        return found[0][len(label) + 2 :]

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


def run_cmd(
    argv: Sequence[str],
    *,
    environ: Mapping[str, Any] | None = None,
    transport: Any = None,
    world_kw: Mapping[str, Any] | None = None,
    instant: datetime = NOW,
) -> CmdRun:
    """Any ``tracer`` command in-process. A ``BaseException`` (a Ctrl-C) is returned, not raised,
    so the streams written before it can be read."""
    cli = mod("cli")
    simulate = mod("simulate")
    trace: list[str] = []
    virtual = VirtualClock()
    seams: dict[str, Any] = {
        "clock": virtual,
        "run_async": virtual.runner(),
        "trace": trace.append,
    }
    seams.update(world_kw or {})
    out, err = io.StringIO(), io.StringIO()
    env = cli.Environment(
        stdout=out,
        stderr=err,
        environ=environ if environ is not None else {},
        clock=lambda: instant,
        sleep=lambda seconds: None,
        transport=transport,
        sim=simulate.SimWorld(**seams),
    )
    code: int | None = None
    raised: BaseException | None = None
    try:
        code = cli.run(list(argv), env)
    except BaseException as error:
        raised = error
    return CmdRun(code, out.getvalue(), err.getvalue(), raised, trace)


def judge_argv(out: Path, bundle: str, jf: JudgeFiles, engine: str, *extra: str) -> list[str]:
    return [
        "judge", "--out", str(out), "--bundle", bundle,
        "--golden", str(jf.golden), "--judge-config", str(jf.config), "--judge", engine,
        *extra,
    ]  # fmt: skip


def loopback_judge_extra(jf: JudgeFiles, requests: int = 10) -> list[str]:
    return [
        "--judge-allowlist", str(jf.allowlist),
        "--max-judge-requests", str(requests),
        "--max-judge-tokens", "100000",
    ]  # fmt: skip


# ---------------------------------------------------------------------------- reading results


def load_bundle(out: Path, bundle_id: str) -> Any:
    from tracer_evalschema.ids import RunBundleId

    store = mod("store")
    engine = store.open_sqlite_store(out)
    try:
        return store.load_run(engine, RunBundleId.parse(bundle_id))
    finally:
        engine.dispose()


def bundle_count(out: Path) -> int:
    """How many run bundles the store of ``out`` holds."""
    import sqlalchemy as sa

    engine = mod("store").open_sqlite_store(out)
    try:
        with engine.connect() as conn:
            return int(conn.execute(sa.text("select count(*) from run_bundle")).scalar() or 0)
    finally:
        engine.dispose()


def answer_with(verdict: str) -> Callable[[httpx.Request, int], httpx.Response]:
    """A scripted step: a reply that is valid for the request, with this verdict."""

    def step(request: httpx.Request, index: int) -> httpx.Response:
        return chat_response(verdict_for(user_document(request), verdict), rid=f"chatcmpl-{index}")

    return step


def interrupt(request: httpx.Request, index: int) -> httpx.Response:
    raise KeyboardInterrupt


def judge_results(bundle: Any) -> dict[str, Any]:
    """The judge results by item id, in the order of the snapshot's definitions."""
    keys = {d.id: d.key for d in bundle.snapshot.metric_definitions}
    found = {}
    for result in bundle.results:
        key = keys.get(result.metric.id, "")
        if key.startswith("judge_golden_"):
            found[key.removeprefix("judge_golden_")] = result
    return found


def judge_doc(result: Any) -> dict[str, Any]:
    document = (result.metadata or {}).get("tracer.judge")
    assert document is not None, "a judge result with no tracer.judge document"
    return dict(document)


def judge_lines(text: str) -> list[str]:
    """The lines of the output that belong to the judge."""
    return [line for line in text.splitlines() if line.startswith("judge")]


def scrub(text: str, *roots: Path) -> str:
    """The text with ids and paths replaced, for exact comparisons."""
    for root in roots:
        text = text.replace(str(root), "ROOT")
    return re.sub(r"rbnd_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "rbnd_ID", text)


class _Text(HTMLParser):
    """The text of one element by id, with the text of elements of the class ``ut`` (untrusted
    text, shown escaped) left out."""

    def __init__(self, element_id: str) -> None:
        super().__init__()
        self.wanted = element_id
        self.depth = 0
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        if self.depth == 0 and mapping.get("id") == self.wanted:
            self.depth = 1
            return
        if self.depth:
            self.depth += 1
            if "ut" in (mapping.get("class") or "").split():
                self.skip = self.depth

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            if self.skip == self.depth:
                self.skip = 0
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.skip:
            self.parts.append(data)


def section_text(page: str, element_id: str) -> str:
    parser = _Text(element_id)
    parser.feed(page)
    return " ".join(part.strip() for part in parser.parts if part.strip())


def all_text(out: Path) -> str:
    return "\n".join(
        p.read_bytes().decode("latin-1") for p in sorted(out.rglob("*")) if p.is_file()
    )


# ------------------------------------------------------------------------------ no socket


def _forbidden_calls(path: Path) -> list[str]:
    """Calls in a test module that could open a network connection of their own."""
    found = []
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            owner = (
                func.value.id
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else ""
            )
            if (owner, name) in {
                ("httpx", "Client"), ("httpx", "AsyncClient"), ("httpx", "HTTPTransport"),
                ("httpx", "AsyncHTTPTransport"), ("socket", "socket"), ("socket", "create_connection"),
            } or name in {"ThreadingHTTPServer", "HTTPServer", "open_connection", "start_server"}:  # fmt: skip
                found.append(f"{path.name}:{node.lineno} {owner}.{name}")
    return found


#: the modules that build sockets on purpose: the guard itself and the Postgres loopback guard
SOCKET_MODULES = {
    "test_cli_no_socket.py",
    "cli_network_guard.py",
    "test_cli_network_guard_postgres.py",
    "conftest.py",
}


def assert_judge_tests_open_no_socket() -> None:
    """ADR 0030 rule 25: no test of this directory builds an HTTP client, a server or a socket of
    its own (a judge test goes through ``httpx.MockTransport``); the judge tests of this batch use
    the transport the harness injects."""
    offenders = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name in SOCKET_MODULES:
            continue
        offenders += _forbidden_calls(path)
    assert offenders == [], offenders
    uses = (TESTS / "cli_p4j2_support.py").read_text("utf-8")
    assert "httpx.MockTransport" in uses
    assert "class JudgeSim(httpx.MockTransport)" in uses
