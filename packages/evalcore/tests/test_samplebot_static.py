# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018, SIM102
"""P4-26: static checks of ``tools/samplebot`` (the Pipecat sample agents of the loopback interop
tests, ADR 0026 rules 6 and 20; contract docs/phase4/api-surface-batch-f.md section 1).

Nothing here imports Pipecat or starts a process (the real round trips are P4-27). The checks read
the source of the tool project: its layout and own lock, that the pipeline of ``scripted_bot`` holds
no model (no service, no voice-activity or turn model, no network client, no key), that its end of
the caller is a timer on the arrival of frames, that the Twilio-compatible route runs with
``auto_hang_up`` off and no telephony credential (ADR 0026 rule 6, BL-299: with it on and real
credentials Pipecat posts ``Status=completed`` to Twilio's REST API for the ``callSid`` it was
given), that it binds loopback only, and that no tracer package, test or script imports it. The three
pure modules of the tool (scenario, tones, call summary) use only the standard library, so they are
also tested here, by value. The two rules ``samplebot_model_problems`` and
``samplebot_twilio_problems`` are shown to refuse each weakening (the check has teeth).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
SAMPLEBOT = ROOT / "tools" / "samplebot"
PACKAGE = SAMPLEBOT / "samplebot"
PACKAGES = ROOT / "packages"

REQUIRED_FILES = (
    "__init__.py",
    "scenario.py",
    "replies.py",
    "summary.py",
    "end_of_caller.py",
    "scripted_bot.py",
)
STDLIB_ONLY = ("__init__.py", "scenario.py", "replies.py", "summary.py")
#: the third-party top-level modules the pipeline module may import
BOT_THIRD_PARTY = frozenset({"pipecat", "fastapi", "uvicorn", "starlette", "loguru"})
#: what a model-free pipeline never imports (prefixes of dotted names, and top-level names)
DENIED_PIPECAT_PREFIXES = (
    "pipecat.services",
    "pipecat.audio.vad",
    "pipecat.audio.turn",
    "pipecat.audio.filters",
    "pipecat.audio.mixers",
    "pipecat.processors.aggregators",
    "pipecat.processors.frameworks",
    "pipecat.processors.transcript_processor",
    "pipecat.extensions",
    "pipecat.observers",
)
DENIED_TOP_LEVEL = frozenset({
    "requests", "httpx", "urllib3", "aiohttp", "openai", "anthropic", "google", "sarvamai",
    "onnxruntime", "torch", "transformers", "numba", "huggingface_hub", "cartesia", "deepgram",
    "elevenlabs", "numpy", "scipy", "soundfile", "audioop", "tracer_evalschema", "tracer_evalcore",
    "tracer_evalproviders", "tracer_evaldb", "tracer_evalsim", "tracer_cli",
})  # fmt: skip
#: names that betray a model, a voice-activity detector or a turn model when imported or called
DENIED_NAME_PARTS = ("silero", "vad", "smartturn", "smart_turn", "llm", "stt", "tts")
DENIED_KEYWORDS = frozenset({
    "vad_analyzer", "vad_enabled", "vad_audio_passthrough", "turn_analyzer", "audio_in_filter",
    "llm", "stt", "tts", "api_key", "model",
})  # fmt: skip
#: Twilio-compatible route: what the serializer may be given
TWILIO_ALLOWED_KEYWORDS = frozenset({"stream_sid", "call_sid", "params"})
TWILIO_DENIED_KEYWORDS = frozenset({"account_sid", "auth_token", "region", "edge"})
LOOPBACK_CHOICES = {"127.0.0.1", "::1", "localhost"}
WILDCARD_HOSTS = {"0.0.0.0", "::"}  # noqa: S104 - the values the tool must never hold


# ------------------------------------------------------------------------------ the rules


def _tree(source: str, name: str) -> ast.Module:
    return ast.parse(source, filename=name)


def imported(tree: ast.AST) -> list[str]:
    """Every dotted module name imported (relative imports of the tool's own package excluded)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
            found += [f"{node.module}.{alias.name}" for alias in node.names]
    return found


def imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            names += [alias.asname or alias.name for alias in node.names]
    return names


def strings(tree: ast.AST) -> list[str]:
    """The string constants of the code, docstrings excluded (prose may name what it forbids)."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def samplebot_model_problems(sources: Mapping[str, str]) -> list[str]:
    """Why the pipeline of ``scripted_bot`` would hold a model, a network client or a key."""
    problems: list[str] = []
    for name, source in sorted(sources.items()):
        if name == "llm_bot.py":
            continue
        tree = _tree(source, name)
        modules = imported(tree)
        tops = {m.split(".")[0] for m in modules}
        stdlib = set(sys.stdlib_module_names) | {"__future__"}
        if name in STDLIB_ONLY and not tops <= stdlib:
            problems.append(f"{name}: not stdlib only: {sorted(tops - stdlib)}")
        if name not in STDLIB_ONLY:
            extra = tops - stdlib - BOT_THIRD_PARTY - {"samplebot"}
            if extra:
                problems.append(f"{name}: imports {sorted(extra)}")
        for module in modules:
            if module.split(".")[0] in DENIED_TOP_LEVEL:
                problems.append(f"{name}: imports {module}")
            if any(module == p or module.startswith(p + ".") for p in DENIED_PIPECAT_PREFIXES):
                problems.append(f"{name}: imports {module}")
            if module.startswith("urllib.request") or module == "http.client":
                problems.append(f"{name}: imports a network client {module}")
        for imported_name in imported_names(tree):
            lowered = imported_name.lower()
            if any(part in lowered for part in DENIED_NAME_PARTS):
                problems.append(f"{name}: imports the name {imported_name}")
        for call in _calls(tree):
            for keyword in call.keywords:
                if keyword.arg in DENIED_KEYWORDS:
                    problems.append(f"{name}: keyword {keyword.arg}")
        for text in strings(tree):
            if "https://" in text:
                problems.append(f"{name}: an https URL literal")
            if text in WILDCARD_HOSTS:
                problems.append(f"{name}: wildcard host {text!r}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                if name not in STDLIB_ONLY:
                    problems.append(f"{name}: reads the environment")
            if isinstance(node, ast.Name) and node.id == "getenv":
                problems.append(f"{name}: reads the environment")
    bot = sources.get("scripted_bot.py")
    timer = sources.get("end_of_caller.py")
    if bot is None or timer is None:
        return [*problems, "scripted_bot.py or end_of_caller.py is missing"]
    # the end of the caller is a timer on frame arrival: a class of the pipeline, in the pipeline
    timer_tree = _tree(timer, "end_of_caller.py")
    classes = [n for n in ast.walk(timer_tree) if isinstance(n, ast.ClassDef)]
    detector = [c for c in classes if c.name == "EndOfCallerTimer"]
    if len(detector) != 1:
        problems.append("end_of_caller.py: no single class EndOfCallerTimer")
    else:
        bases = [ast.unparse(b) for b in detector[0].bases]
        if not any("FrameProcessor" in b for b in bases):
            problems.append("EndOfCallerTimer is not a FrameProcessor")
        inits = [
            n for n in detector[0].body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        ]
        params = [a.arg for i in inits for a in i.args.args + i.args.kwonlyargs]
        if "end_of_caller_ms" not in params:
            problems.append("EndOfCallerTimer takes no end_of_caller_ms")
    timer_names = (
        {n.attr for n in ast.walk(timer_tree) if isinstance(n, ast.Attribute)}
        | {n.id for n in ast.walk(timer_tree) if isinstance(n, ast.Name)}
        | set(imported_names(timer_tree))
        | {n.name for n in ast.walk(timer_tree) if isinstance(n, ast.FunctionDef)}
    )
    if not timer_names & {"call_later", "sleep", "create_task", "wait_for", "monotonic"}:
        problems.append("end_of_caller.py: no timer")
    for forbidden in ("rms", "energy", "threshold", "audioop", "frombuffer", "unpack", "array"):
        if forbidden in timer_names:
            problems.append(f"end_of_caller.py: looks at samples ({forbidden})")
    bot_tree = _tree(bot, "scripted_bot.py")
    pipelines = [c for c in _calls(bot_tree) if _call_name(c) == "Pipeline"]
    if not pipelines:
        problems.append("scripted_bot.py: no Pipeline")
    elif not any("EndOfCallerTimer" in ast.unparse(arg) for call in pipelines for arg in call.args):
        problems.append("scripted_bot.py: EndOfCallerTimer is not in the Pipeline")
    # the host is checked at the command line: loopback choices only, no default
    choices_ok = False
    for call in _calls(bot_tree):
        if (
            _call_name(call) == "add_argument"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            if call.args[0].value == "--host":
                keywords = {k.arg: k.value for k in call.keywords}
                choices = keywords.get("choices")
                required = keywords.get("required")
                if (
                    isinstance(choices, ast.List | ast.Tuple | ast.Set)
                    and {ast.literal_eval(e) for e in choices.elts} <= LOOPBACK_CHOICES
                    and isinstance(required, ast.Constant)
                    and required.value is True
                    and "default" not in keywords
                ):
                    choices_ok = True
    if not choices_ok:
        problems.append("scripted_bot.py: --host is not required with loopback choices only")
    return problems


def samplebot_twilio_problems(sources: Mapping[str, str]) -> list[str]:
    """Why the Twilio-compatible route could reach Twilio's REST API or hold a credential."""
    problems: list[str] = []
    bot = sources.get("scripted_bot.py", "")
    tree = _tree(bot, "scripted_bot.py")
    serializers = [c for c in _calls(tree) if _call_name(c) == "TwilioFrameSerializer"]
    if not serializers:
        problems.append("no TwilioFrameSerializer")
    for call in serializers:
        if call.args:
            problems.append("TwilioFrameSerializer gets a positional argument")
        for keyword in call.keywords:
            if keyword.arg in TWILIO_DENIED_KEYWORDS:
                problems.append(f"TwilioFrameSerializer is given {keyword.arg}")
            elif keyword.arg not in TWILIO_ALLOWED_KEYWORDS:
                problems.append(f"TwilioFrameSerializer keyword {keyword.arg}")
    hang_ups = [k for c in _calls(tree) for k in c.keywords if k.arg == "auto_hang_up"]
    if not hang_ups:
        problems.append("auto_hang_up is not set (its default is on)")
    for keyword in hang_ups:
        if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False):
            problems.append("auto_hang_up is not the constant False")
    if "parse_telephony_websocket" not in {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Name | ast.Attribute)
    }:
        problems.append("the identifiers do not come from parse_telephony_websocket")
    for name, source in sorted(sources.items()):
        module = _tree(source, name)
        words = {n.id for n in ast.walk(module) if isinstance(n, ast.Name)}
        words |= {n.attr for n in ast.walk(module) if isinstance(n, ast.Attribute)}
        words |= {k.arg for c in _calls(module) for k in c.keywords if k.arg}
        for word in sorted(words):
            if "account_sid" in word.lower() or "auth_token" in word.lower():
                problems.append(f"{name}: uses the name {word}")
        for text in strings(module):
            lowered = text.lower()
            if "api.twilio.com" in lowered:
                problems.append(f"{name}: mentions api.twilio.com")
            if "twilio" in lowered and re.search(r"token|sid|secret|account", lowered):
                problems.append(f"{name}: a credential-shaped string {text!r}")
    return problems


# ------------------------------------------------------------------------------ the real tree


def real_sources() -> dict[str, str]:
    assert PACKAGE.is_dir(), "tools/samplebot/samplebot does not exist yet (P4-26)"
    return {p.name: p.read_text("utf-8") for p in sorted(PACKAGE.glob("*.py"))}


def test_samplebot_layout_readme_and_own_lock() -> None:
    """P4-26 acceptance: the files of the tool project exist, it has its own README and its own
    lock (a separate uv project, not a member of the workspace, not in the root lock), the lock pins
    Pipecat exactly and holds no tracer package. The existing scaffold tests check the rest."""
    for name in REQUIRED_FILES:
        assert (PACKAGE / name).is_file(), name
    readme = (SAMPLEBOT / "README.md").read_text("utf-8")
    for needle in (
        "scripted_bot", "pipecat-ai==1.14.0", "auto_hang_up", "bot_holds_telephony_credentials=false",
        "tracer.samplebot_scenario/1", "ready", "loopback", "no model", "P4-02-licenses.md",
        "python -m samplebot.scripted_bot", "uv run --project tools/samplebot",
        "pytest -m integration",
    ):  # fmt: skip
        assert needle in readme, needle
    lock_path = SAMPLEBOT / "uv.lock"
    assert lock_path.is_file(), "tools/samplebot has no uv.lock of its own"
    lock = tomllib.loads(lock_path.read_text("utf-8"))
    packages = {p["name"]: p for p in lock["package"]}
    assert packages["pipecat-ai"]["version"] == "1.14.0"
    assert packages["tracer-samplebot"]["source"] == {"virtual": "."}
    assert not [n for n in packages if n.startswith("tracer-") and n != "tracer-samplebot"]
    assert "aiortc" in packages, "the webrtc extra of pipecat-ai is part of the closure"
    root_lock = (ROOT / "uv.lock").read_text("utf-8")
    assert "pipecat" not in root_lock.lower()
    assert "samplebot" not in root_lock
    project = tomllib.loads((SAMPLEBOT / "pyproject.toml").read_text("utf-8"))
    assert "pipecat-ai[websocket,runner,webrtc]==1.14.0" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["package"] is False


def test_samplebot_scripted_bot_uses_no_model() -> None:
    """ADR 0026 rule 20 and invariant I1: ``scripted_bot`` is a Pipecat 1.14.0 pipeline with no
    model: no service (speech-to-text, text-to-speech, LLM), no voice-activity or turn model, no
    network client, no key and no environment read, and the end of the caller is a timer on the
    arrival of frames (an energy threshold or a VAD would be a model or a number nobody measured,
    ADR 0026 rule 10). The pipeline module needs only Pipecat, FastAPI and uvicorn."""
    sources = real_sources()
    assert set(REQUIRED_FILES) <= set(sources), sorted(sources)
    assert samplebot_model_problems(sources) == []
    # a pipeline of Pipecat objects, with the timer in it
    bot = sources["scripted_bot.py"]
    assert "from pipecat" in bot or "import pipecat" in bot
    assert "Pipeline" in bot and "PipelineTask" in bot


def test_samplebot_twilio_runs_without_auto_hang_up_or_credentials() -> None:
    """ADR 0026 rule 6 (BL-299): Pipecat's Twilio serializer, with ``auto_hang_up`` on (its default)
    and real credentials, posts ``Status=completed`` to Twilio's REST API for the ``callSid`` it was
    given. The sample bot runs the route with it off, no account SID, no auth token, no region and
    no edge, and takes the stream and call identifiers from the opening messages of the caller
    (``parse_telephony_websocket``), so it never sends a request to Twilio."""
    assert samplebot_twilio_problems(real_sources()) == []
    readme = (SAMPLEBOT / "README.md").read_text("utf-8")
    assert "auto_hang_up" in readme and "bot_holds_telephony_credentials=false" in readme


def test_samplebot_is_never_imported_by_a_tracer_package_script_or_test() -> None:
    """ADR 0026 invariant I1: no package, test or script imports Pipecat or the tool, and the tool
    imports no tracer package (it is a separate project: it runs where Pipecat is installed)."""
    offenders: list[str] = []
    paths = [
        *PACKAGES.glob("*/src/*/*.py"),
        *PACKAGES.glob("*/src/*/*/*.py"),
        *PACKAGES.glob("*/tests/*.py"),
        *(ROOT / "scripts").glob("*.py"),
        *(ROOT / "scripts").glob("*/*.py"),
    ]
    for path in sorted(paths):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue
        for module in imported(tree):
            if module.split(".")[0] in ("pipecat", "samplebot"):
                offenders.append(f"{path.relative_to(ROOT)}: {module}")
    assert offenders == []
    for name, source in real_sources().items():
        assert not [m for m in imported(_tree(source, name)) if m.startswith("tracer")], name


def test_samplebot_binds_loopback_only_with_no_default_for_any_value() -> None:
    """The bot is a test fixture on the operator's machine: ``--host`` is required and can only be a
    loopback name or address; ``--sample-rate``, ``--scenario``, ``--dialect`` and ``--port`` are
    required too (no hidden default decides what a call does); nothing writes a wildcard address."""
    sources = real_sources()
    tree = _tree(sources["scripted_bot.py"], "scripted_bot.py")
    options: dict[str, dict[str, ast.expr]] = {}
    for call in _calls(tree):
        if (
            _call_name(call) == "add_argument"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            options[str(call.args[0].value)] = {k.arg: k.value for k in call.keywords if k.arg}
    assert set(options) == {"--dialect", "--scenario", "--sample-rate", "--host", "--port"}
    for option, keywords in options.items():
        required = keywords.get("required")
        assert isinstance(required, ast.Constant) and required.value is True, option
        assert "default" not in keywords, option
    assert "choices" in options["--dialect"]  # the closed set of dialects, written in the module
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert {"pipecat-protobuf/1", "twilio-media-stream/1", "smallwebrtc/1"} <= literals
    for name, source in sources.items():
        for node in ast.walk(_tree(source, name)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"0.0.0.0", "::"}, name  # noqa: S104


def test_samplebot_llm_bot_waits_for_the_owner_answer_o3() -> None:
    """P4-26 (b): the ``llm_bot`` follows the owner's answer O3 (the stack of the agent model) and is
    owner-gated to run; O3 is open, so nothing of it exists and nothing refers to it. A later batch
    adds the file with its own tests (BACKLOG)."""
    assert not (PACKAGE / "llm_bot.py").exists()
    assert "llm_bot" not in real_sources()["scripted_bot.py"]


# ------------------------------------------------------------------------ the pure modules


def _load(name: str) -> ModuleType:
    """A module of the tool, loaded by file path (they are stdlib only)."""
    spec = importlib.util.spec_from_file_location(f"samplebot_{name}", PACKAGE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scenario_text(**changes: Any) -> str:
    document: dict[str, Any] = {
        "format": "tracer.samplebot_scenario/1",
        "end_of_caller_ms": 120,
        "replies": [
            {"delay_ms": 100, "tone": {"hz": 440, "ms": 200, "level": 3000}},
            {"delay_ms": 250, "tone": {"hz": 880, "ms": 100, "level": 1000}},
        ],
        "after_replies": "stay_silent",
    }
    document.update(changes)
    return json.dumps(document)


def test_samplebot_scenario_parser_is_strict() -> None:
    """``tracer.samplebot_scenario/1``: strict JSON, closed keys, no default, a refusal that is a
    stable code (``ScenarioError.code``, ``str(error)`` is the code) and never a value."""
    module = _load("scenario")
    scenario = module.parse_scenario(scenario_text())
    assert scenario.end_of_caller_ms == 120
    assert scenario.after_replies == "stay_silent"
    assert [(r.delay_ms, r.hz, r.ms, r.level) for r in scenario.replies] == [
        (100, 440, 200, 3000),
        (250, 880, 100, 1000),
    ]
    assert module.FORMAT == "tracer.samplebot_scenario/1"
    assert module.parse_scenario(scenario_text(after_replies="hang_up")).after_replies == "hang_up"
    bad = {
        "scenario_not_json": ["", "{", "[]x", '{"format": "tracer.samplebot_scenario/1", "format": 1}'],
        "scenario_shape": [
            "[]", "{}", json.dumps({"format": "x"}),
            scenario_text(format="tracer.samplebot_scenario/2"),
            json.dumps({**json.loads(scenario_text()), "extra": 1}),
            json.dumps({k: v for k, v in json.loads(scenario_text()).items() if k != "after_replies"}),
            scenario_text(replies="x"),
            scenario_text(replies=[{"delay_ms": 1}]),
            scenario_text(replies=[{"delay_ms": 1, "tone": {"hz": 440, "ms": 20}}]),
            scenario_text(replies=[{"delay_ms": 1, "tone": {"hz": 440, "ms": 20, "level": 1, "x": 1}}]),
            scenario_text(replies=[{"delay_ms": 1, "tone": {"hz": 440, "ms": 20, "level": 1}, "x": 1}]),
        ],
        "scenario_value": [
            scenario_text(replies=[]),
            scenario_text(end_of_caller_ms=0),
            scenario_text(end_of_caller_ms=19),
            scenario_text(end_of_caller_ms=5001),
            scenario_text(end_of_caller_ms=True),
            scenario_text(end_of_caller_ms=120.0),
            scenario_text(replies=[{"delay_ms": False, "tone": {"hz": 440, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 20, "level": True}}]),
            scenario_text(end_of_caller_ms="120"),
            scenario_text(after_replies="stay"),
            scenario_text(replies=[{"delay_ms": -1, "tone": {"hz": 440, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 10_001, "tone": {"hz": 440, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 49, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 3401, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 30, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 10_020, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 20, "level": 0}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 20, "level": 32768}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440.5, "ms": 20, "level": 1}}]),
            scenario_text(replies=[{"delay_ms": 0, "tone": {"hz": 440, "ms": 20, "level": 1}}] * 65),
        ],
    }  # fmt: skip
    for code, texts in bad.items():
        for text in texts:
            with pytest.raises(module.ScenarioError) as raised:
                module.parse_scenario(text)
            assert raised.value.code == code, (code, text[:80])
            assert str(raised.value) == code
            assert "tracer.samplebot" not in str(raised.value)


@pytest.mark.parametrize("rate", [8000, 16000, 48000])
def test_samplebot_tone_is_deterministic_and_has_the_scenario_frequency(rate: int) -> None:
    """A reply is a pure sine (no file, no model): ``rate * ms / 1000`` samples, 16-bit little
    endian, never above the level, silent at sample 0, the same bytes on every call and every
    Python, and a frequency the zero crossings give back within 2 per cent."""
    replies = _load("replies")
    summary = _load("summary")
    pcm = replies.tone_pcm_s16le(hz=440, ms=500, level=3000, sample_rate_hz=rate)
    assert isinstance(pcm, bytes)
    assert len(pcm) == rate * 500 // 1000 * 2
    assert pcm == replies.tone_pcm_s16le(hz=440, ms=500, level=3000, sample_rate_hz=rate)
    samples = [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in range(0, len(pcm), 2)]
    assert samples[0] == 0
    assert max(abs(s) for s in samples) <= 3000
    assert max(abs(s) for s in samples) >= 2900  # reaches the level
    estimate = summary.estimate_hz(pcm, rate)
    assert estimate == pytest.approx(440, rel=0.02)
    other = replies.tone_pcm_s16le(hz=440, ms=500, level=1000, sample_rate_hz=rate)
    assert other != pcm
    frames = rate * 20 // 1000 * 2
    assert len(pcm) % frames == 0  # whole 20 ms frames for every scenario length


def test_samplebot_summary_estimates_the_frequency_by_zero_crossings() -> None:
    """The bot reports what it heard of each caller turn as a zero-crossing frequency; the test
    builds its own sine (an independent writer) and a flat signal."""
    summary = _load("summary")
    rate = 16000

    def sine(hz: int, ms: int) -> bytes:
        n = rate * ms // 1000
        return b"".join(
            round(8000 * math.sin(2 * math.pi * hz * i / rate)).to_bytes(2, "little", signed=True)
            for i in range(n)
        )

    assert summary.estimate_hz(sine(300, 400), rate) == pytest.approx(300, rel=0.02)
    assert summary.estimate_hz(sine(1000, 200), rate) == pytest.approx(1000, rel=0.02)
    # padding before and after the tone (a codec's pre-roll, digital silence) does not move it
    padded = b"\x00\x00" * (rate // 10) + sine(300, 200) + b"\x00\x00" * (rate // 10)
    assert summary.estimate_hz(padded, rate) == pytest.approx(300, rel=0.03)
    assert summary.estimate_hz((1000).to_bytes(2, "little", signed=True) * 1600, rate) is None
    assert summary.estimate_hz(b"", rate) is None
    assert summary.estimate_hz(b"\x00\x00" * 1600, rate) is None


def test_samplebot_call_summary_counts_turns_and_frequency() -> None:
    """``CallTally`` turns the audio the bot received into the ``call_summary`` object it prints:
    per caller turn the audio length in ms, the number of audio frames and the frequency; and the
    totals. The keys are exactly these (the interop tests read them)."""
    summary = _load("summary")
    rate = 8000
    n = rate * 20 // 1000

    def frame(hz: int, k: int) -> bytes:
        return b"".join(
            round(6000 * math.sin(2 * math.pi * hz * (k * n + i) / rate)).to_bytes(
                2, "little", signed=True
            )
            for i in range(n)
        )

    tally = summary.CallTally()
    for k in range(25):  # first turn: 25 frames of 300 Hz
        tally.on_audio(frame(300, k), rate)
    tally.on_turn_end()
    tally.on_reply_started()
    for k in range(10):  # second turn: 10 frames of 500 Hz
        tally.on_audio(frame(500, k), rate)
    tally.on_turn_end()
    tally.on_reply_started()
    document = tally.summary(call=3, dialect="twilio-media-stream/1", ended_by="caller_closed")
    assert set(document) == {
        "event", "call", "dialect", "ended_by", "caller_turns", "replies_started",
        "inbound_frames", "inbound_sample_rate",
    }  # fmt: skip
    assert document["event"] == "call_summary"
    assert (document["call"], document["dialect"], document["ended_by"]) == (
        3, "twilio-media-stream/1", "caller_closed",
    )  # fmt: skip
    assert document["replies_started"] == 2
    assert document["inbound_frames"] == 35
    assert document["inbound_sample_rate"] == 8000
    turns = document["caller_turns"]
    assert [(t["audio_ms"], t["frames"]) for t in turns] == [(500, 25), (200, 10)]
    assert turns[0]["hz"] == pytest.approx(300, rel=0.03)
    assert turns[1]["hz"] == pytest.approx(500, rel=0.03)
    json.dumps(document, sort_keys=True, ensure_ascii=True)  # one JSON line
    open_turn = summary.CallTally()
    for k in range(5):  # the connection closed in the middle of a turn: it is still reported
        open_turn.on_audio(frame(300, k), rate)
    (only,) = open_turn.summary(call=1, dialect="twilio-media-stream/1", ended_by="caller_closed")[
        "caller_turns"
    ]
    assert (only["audio_ms"], only["frames"]) == (100, 5)
    empty = summary.CallTally().summary(
        call=1, dialect="pipecat-protobuf/1", ended_by="bot_hang_up"
    )
    assert empty["caller_turns"] == [] and empty["inbound_frames"] == 0
    assert empty["inbound_sample_rate"] is None


# ------------------------------------------------------------------ the checks have teeth

GOOD_BOT = """
import argparse
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.runner.utils import parse_telephony_websocket
from samplebot.end_of_caller import EndOfCallerTimer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dialect", required=True, choices=["pipecat-protobuf/1"])
    parser.add_argument("--host", required=True, choices=["127.0.0.1", "::1", "localhost"])


async def run(websocket, scenario):
    kind, data = await parse_telephony_websocket(websocket)
    serializer = TwilioFrameSerializer(
        stream_sid=data["stream_id"],
        call_sid=data["call_id"],
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
    pipeline = Pipeline([EndOfCallerTimer(end_of_caller_ms=scenario.end_of_caller_ms)])
    return PipelineTask(pipeline)
"""
GOOD_TIMER = """
import asyncio
from pipecat.processors.frame_processor import FrameProcessor


class EndOfCallerTimer(FrameProcessor):
    def __init__(self, *, end_of_caller_ms):
        super().__init__()
        self._ms = end_of_caller_ms
        self._task = None

    def arm(self, loop):
        return loop.call_later(self._ms / 1000, self.fire)

    def fire(self):
        return None
"""
GOOD = {
    "__init__.py": "",
    "scenario.py": "import json\n",
    "replies.py": "import math\nimport struct\n",
    "summary.py": "import math\nimport struct\n",
    "end_of_caller.py": GOOD_TIMER,
    "scripted_bot.py": GOOD_BOT,
}


def _with(name: str, old: str, new: str) -> dict[str, str]:
    sources = dict(GOOD)
    assert old in sources[name], old
    sources[name] = sources[name].replace(old, new, 1)
    return sources


def test_the_model_rule_accepts_the_reviewed_shape() -> None:
    assert samplebot_model_problems(GOOD) == []
    assert samplebot_twilio_problems(GOOD) == []


@pytest.mark.parametrize(
    ("name", "old", "new", "fragment"),
    [
        ("scripted_bot.py", "import argparse\n", "import argparse\nimport openai\n", "openai"),
        ("scripted_bot.py", "import argparse\n", "import argparse\nimport httpx\n", "httpx"),
        ("scripted_bot.py", "import argparse\n", "import argparse\nimport numpy\n", "numpy"),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nimport urllib.request\n",
            "urllib.request",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nimport os\nKEY = os.environ\n",
            "environment",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nfrom tracer_evalsim import clock\n",
            "tracer_evalsim",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nfrom pipecat.services.openai.llm import OpenAILLMService\n",
            "pipecat.services",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nfrom pipecat.audio.vad.silero import SileroVADAnalyzer\n",
            "pipecat.audio.vad",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nfrom pipecat.audio.turn.smart_turn.base import BaseSmartTurn\n",
            "pipecat.audio.turn",
        ),
        (
            "scripted_bot.py",
            "import argparse\n",
            "import argparse\nfrom pipecat.processors.aggregators.llm_context import LLMContext\n",
            "aggregators",
        ),
        ("scripted_bot.py", "Pipeline([", "Pipeline(vad_analyzer=1, x=[", "vad_analyzer"),
        ("scripted_bot.py", "Pipeline([", "Pipeline(model='m', x=[", "model"),
        (
            "scripted_bot.py",
            'choices=["127.0.0.1", "::1", "localhost"]',
            'choices=["127.0.0.1", "0.0.0.0"]',
            "--host",
        ),
        (
            "scripted_bot.py",
            'parser.add_argument("--host", required=True,',
            'parser.add_argument("--host", required=False,',
            "--host",
        ),
        (
            "scripted_bot.py",
            "EndOfCallerTimer(end_of_caller_ms=scenario.end_of_caller_ms)",
            "object()",
            "not in the Pipeline",
        ),
        ("scripted_bot.py", "Pipeline([", "NotAPipeline([", "no Pipeline"),
        (
            "scripted_bot.py",
            "return PipelineTask(pipeline)",
            "return 'https://api.example.org'",
            "https URL",
        ),
        ("end_of_caller.py", "import asyncio\n", "import asyncio\nimport audioop\n", "audioop"),
        (
            "end_of_caller.py",
            "import asyncio\n",
            "import asyncio\nfrom struct import unpack\n",
            "unpack",
        ),
        (
            "end_of_caller.py",
            "    def fire(self):",
            "    def energy(self, pcm):\n        return abs(pcm)\n\n    def fire(self):",
            "energy",
        ),
        (
            "end_of_caller.py",
            "loop.call_later(self._ms / 1000, self.fire)",
            "self.fire()",
            "no timer",
        ),
        ("end_of_caller.py", "FrameProcessor):", "object):", "not a FrameProcessor"),
        (
            "end_of_caller.py",
            "def __init__(self, *, end_of_caller_ms):",
            "def __init__(self):",
            "end_of_caller_ms",
        ),
        ("scenario.py", "import json\n", "import json\nimport requests\n", "requests"),
        ("scenario.py", "import json\n", "import json\nimport pipecat\n", "not stdlib only"),
        ("summary.py", "import math\n", "import math\nimport numpy\n", "numpy"),
    ],
)
def test_the_model_rule_refuses_each_weakening(
    name: str, old: str, new: str, fragment: str
) -> None:
    problems = samplebot_model_problems(_with(name, old, new))
    assert problems, (name, old, new)
    assert re.search(fragment, "\n".join(problems)), (fragment, problems)


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        (
            "params=TwilioFrameSerializer.InputParams(auto_hang_up=False),",
            "params=TwilioFrameSerializer.InputParams(auto_hang_up=True),",
            "constant False",
        ),
        (
            "params=TwilioFrameSerializer.InputParams(auto_hang_up=False),",
            "params=TwilioFrameSerializer.InputParams(),",
            "auto_hang_up is not set",
        ),
        (
            "params=TwilioFrameSerializer.InputParams(auto_hang_up=False),",
            "params=TwilioFrameSerializer.InputParams(auto_hang_up=flag),",
            "constant False",
        ),
        (
            'call_sid=data["call_id"],',
            "call_sid=data[\"call_id\"], account_sid='x',",
            "account_sid",
        ),
        ('call_sid=data["call_id"],', "call_sid=data[\"call_id\"], auth_token='x',", "auth_token"),
        ('call_sid=data["call_id"],', "call_sid=data[\"call_id\"], region='us1',", "region"),
        (
            "TwilioFrameSerializer(\n        stream_sid",
            "TwilioFrameSerializer(\n        'x', stream_sid",
            "positional",
        ),
        (
            "kind, data = await parse_telephony_websocket(websocket)",
            "kind, data = None, {'stream_id': 'SM1', 'call_id': 'CA1'}",
            "parse_telephony_websocket",
        ),
        (
            "def main():",
            "URL = 'https://api.twilio.com/2010-04-01/Accounts'\n\n\ndef main():",
            "api.twilio.com",
        ),
        ("def main():", "SECRET = 'twilio auth token'\n\n\ndef main():", "credential-shaped"),
    ],
)
def test_the_twilio_rule_refuses_each_weakening(old: str, new: str, fragment: str) -> None:
    problems = samplebot_twilio_problems(_with("scripted_bot.py", old, new))
    assert problems, (old, new)
    assert re.search(fragment, "\n".join(problems)), (fragment, problems)
