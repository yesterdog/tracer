# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501
"""P4-J17 and P4-J18 (support), BL-337: what the commands of batch J part 2 need from the providers
package (ADR 0030 rules 14, 18, 19 and 20; contract docs/phase4/api-surface-batch-j2.md sections 5
and 6).

* the exchange directory levels (BL-337): an existing ``<out>/judge`` that is open to others is
  made private with a warning (the level Tracer itself creates); the user's output directory is
  never changed; a link or a file in its place is refused;
* ``planned_params`` and ``planned_fake_params``: the ``params`` of ``tracer.judge_profile/1``
  before any request exists, refusing a setting the profile does not support as the first request
  would;
* ``check_endpoint`` and ``bind_checked``: the allowlist check of step 2 (the entry, the profile,
  the DNS answers) and the key read of step 6, apart; ``bind_endpoint`` is their composition;
* two behaviours of the part 1 code that the batch pins: the usage of a paid reply is handed out
  before the exchange file is written, and a short read does not truncate a file.
"""

from __future__ import annotations

import os
import stat
import warnings
from pathlib import Path
from typing import Any

import pytest

from evalproviders_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    PROFILE_URLS,
    JudgeRig,
    context,
    core,
    jmod,
    openai_reply,
    params,
    request,
    verdict_text,
)
from tracer_evalschema.ids import RunId


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ------------------------------------------------------------------------------- BL-337


def test_exchange_store_tightens_an_existing_judge_directory_open_to_others(tmp_path: Path) -> None:
    """BL-337: ``<out>/judge`` is a level Tracer creates; when it already exists with a group or
    other bit (a 0755 made by another tool, a copied tree) it is made 0700 and one warning is
    issued that names no path; the run directory below it is created 0700; the output directory of
    the user is never changed; nothing is written before the level is private."""
    exchange = jmod("judgeexchange")
    assert issubclass(exchange.JudgeExchangeModeWarning, UserWarning)
    for loose in (0o755, 0o775, 0o777, 0o705, 0o750):
        out = tmp_path / f"out{loose:o}"
        (out / "judge").mkdir(parents=True)
        out.chmod(0o755)
        (out / "judge").chmod(loose)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            store = exchange.ExchangeStore(exchange.exchange_directory(out, RunId.new()))
        ours = [w for w in caught if issubclass(w.category, exchange.JudgeExchangeModeWarning)]
        assert len(ours) == 1, loose
        assert str(out) not in str(ours[0].message)
        assert "judge" in str(ours[0].message)
        assert mode(out / "judge") == 0o700, loose
        assert mode(store.directory) == 0o700
        assert mode(out) == 0o755, "the user's output directory is not changed"
        sha = store.write("mr_q00", 1, _exchange_doc())
        assert mode(store.directory / "item-mr_q00-1.json") == 0o600
        assert len(sha) == 64


def test_exchange_store_says_nothing_when_the_levels_are_private_or_new(tmp_path: Path) -> None:
    exchange = jmod("judgeexchange")
    private = tmp_path / "private"
    (private / "judge").mkdir(parents=True, mode=0o700)
    (private / "judge").chmod(0o700)
    fresh = tmp_path / "fresh"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exchange.ExchangeStore(exchange.exchange_directory(private, RunId.new()))
        exchange.ExchangeStore(exchange.exchange_directory(fresh, RunId.new()))
        exchange.ExchangeStore(exchange.exchange_directory(fresh, RunId.new()))
    assert mode(fresh / "judge") == 0o700
    # a directory that is not called `judge` is not one of ours: it is never changed
    other = tmp_path / "other" / "somewhere"
    other.mkdir(parents=True)
    other.chmod(0o755)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exchange.ExchangeStore(other / "run")
    assert mode(other) == 0o755
    assert mode(other / "run") == 0o700


def test_exchange_store_refuses_a_link_or_a_file_in_place_of_the_judge_directory(
    tmp_path: Path,
) -> None:
    exchange = jmod("judgeexchange")
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(real, out / "judge")
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        exchange.ExchangeStore(exchange.exchange_directory(out, RunId.new()))
    assert caught.value.code == "exchange_directory_not_private"
    assert list(real.iterdir()) == []
    assert mode(real) == 0o700
    other = tmp_path / "out2"
    other.mkdir()
    (other / "judge").write_text("x", "utf-8")
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        exchange.ExchangeStore(exchange.exchange_directory(other, RunId.new()))
    assert caught.value.code == "exchange_directory_not_private"
    # an existing run directory that is open to others is still refused, not changed
    third = tmp_path / "out3"
    run = exchange.exchange_directory(third, RunId.new())
    run.mkdir(parents=True)
    run.chmod(0o755)
    (third / "judge").chmod(0o700)
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        exchange.ExchangeStore(run)
    assert caught.value.code == "exchange_directory_not_private"
    assert mode(run) == 0o755


def _exchange_doc() -> dict[str, Any]:
    return core("judgerecords").build_exchange_document(  # type: ignore[no-any-return]
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        response_body="{}",
        request_id=None,
        status=200,
    )


# ------------------------------------------------------------------------ planned params


@pytest.mark.parametrize("name", sorted(PROFILE_URLS))
def test_planned_params_are_what_the_first_request_records(name: str) -> None:
    profiles = jmod("judgeprofiles")
    profile = profiles.get_profile(name)
    changes: dict[str, Any] = {"temperature": 0.25}
    if profile.seed:
        changes["seed"] = 11
    if profile.reasoning_effort == "required":
        changes["reasoning_effort"] = "low"
    if profile.provider_object:
        changes["provider_slug"] = "vendor-a"
    chosen = params(**changes)
    planned = profiles.planned_params(profile, chosen)
    assert planned == profiles.params_as_sent(profile, request(0, **changes))
    assert planned[profile.token_field] == 256
    assert planned["temperature"] == 0.25
    assert ("seed_guarantee" in planned) == profile.seed
    assert ("response_format" in planned) == profile.response_format
    bare = profiles.planned_params(
        profile,
        params(**{k: v for k, v in changes.items() if k in ("reasoning_effort", "provider_slug")}),
    )
    assert "temperature" not in bare, "not sent unless the configuration sets it"
    assert "seed" not in bare, "not sent unless the configuration sets it"


def test_planned_params_refuse_a_setting_the_profile_does_not_support() -> None:
    profiles = jmod("judgeprofiles")
    anthropic = profiles.get_profile("anthropic_compat/1")
    for changes, code in (
        ({"seed": 3}, "seed_not_supported"),
        ({"reasoning_effort": "low"}, "reasoning_effort_not_supported"),
        ({"provider_slug": "vendor-a"}, "provider_slug_not_supported"),
        ({"extra_headers": (("HTTP-Referer", "https://example.test"),)}, "header_not_supported"),
    ):
        with pytest.raises(profiles.JudgeProfileError) as caught:
            profiles.planned_params(anthropic, params(**changes))
        assert caught.value.code == code
    with pytest.raises(profiles.JudgeProfileError) as caught:
        profiles.planned_params(profiles.get_profile("sarvam_v2/1"), params())
    assert caught.value.code == "reasoning_effort_required"
    with pytest.raises(profiles.JudgeProfileError) as caught:
        profiles.planned_params(profiles.get_profile("openrouter/1"), params())
    assert caught.value.code == "provider_slug_required"
    ok = profiles.planned_params(
        profiles.get_profile("anthropic_compat/1"),
        params(extra_headers=(("anthropic-workspace-id", "wrk_1"),)),
    )
    assert "anthropic-workspace-id" not in str(ok), (
        "a header is not part of the recorded parameters"
    )


def test_planned_fake_params_are_what_the_fake_engine_records() -> None:
    exchange = jmod("judgeexchange")
    for changes in ({}, {"temperature": 0}, {"temperature": 1.5, "seed": 3}):
        chosen = params(**changes)
        planned = exchange.planned_fake_params(chosen)
        outcome = exchange.create_fake_judge("agrees").complete(request(0, **changes), context())
        assert planned == dict(outcome.params_sent)
    assert exchange.planned_fake_params(params()) == {"max_tokens": 256}
    assert exchange.planned_fake_params(params(temperature=0.5)) == {
        "max_tokens": 256,
        "temperature": 0.5,
    }
    assert exchange.planned_fake_params(params(temperature=0)) == {
        "max_tokens": 256,
        "temperature": 0,
    }
    assert "seed" not in exchange.planned_fake_params(params(seed=3))


# ------------------------------------------------------------------ check and bind apart


URL = PROFILE_URLS["openai/1"]
ENTRY = f"{URL} profile=openai/1 key_env=JUDGE_KEY_VAR\n"


class Spy(dict[str, str]):
    def __init__(self, items: dict[str, str]) -> None:
        super().__init__(items)
        self.reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return super().__contains__(key)


def test_check_endpoint_reads_no_key_and_bind_checked_reads_only_the_key_of_the_entry(
    tmp_path: Path,
) -> None:
    hosts = jmod("judgehosts")
    entries = hosts.parse_judge_allowlist(ENTRY)
    env = Spy({"JUDGE_KEY_VAR": CANARY_KEY, "OTHER": "x"})
    asked: list[tuple[str, int]] = []

    def resolver(host: str, port: int) -> list[str]:
        asked.append((host, port))
        return ["93.184.216.34"]

    entry = hosts.check_endpoint(
        entries, url=URL, profile="openai/1", resolver=resolver, first_request_gate=True
    )
    assert isinstance(entry, hosts.JudgeEndpoint)
    assert entry.key_env == "JUDGE_KEY_VAR"
    assert asked == [("api.openai.com", 443)]
    assert env.reads == []
    bound = hosts.bind_checked(entry, environ=env)
    assert set(env.reads) == {"JUDGE_KEY_VAR"}
    assert bound.endpoint == entry
    assert bound.profile.name == "openai/1"
    assert bound.api_key is not None
    assert bound.api_key.reveal() == CANARY_KEY
    assert asked == [("api.openai.com", 443)], "the key step asks the resolver nothing"
    # the same through bind_endpoint: the same entry and the same key
    together = hosts.bind_endpoint(
        entries, url=URL, profile="openai/1", environ=Spy({"JUDGE_KEY_VAR": CANARY_KEY}),
        resolver=resolver, first_request_gate=True,
    )  # fmt: skip
    assert together.endpoint == entry
    assert together.api_key.reveal() == CANARY_KEY
    # a missing variable is the typed failure of the key step only
    with pytest.raises(core("judge").JudgeKeyMissingError) as caught:
        hosts.bind_checked(entry, environ={})
    assert caught.value.reason == "key_missing"


def test_check_endpoint_refuses_in_order_before_the_resolver_and_never_touches_the_environment() -> (
    None
):
    hosts, profiles = jmod("judgehosts"), jmod("judgeprofiles")
    entries = hosts.parse_judge_allowlist(ENTRY)
    asked: list[Any] = []

    def resolver(host: str, port: int) -> list[str]:
        asked.append(host)
        return ["10.0.0.5"]

    with pytest.raises(hosts.JudgeAllowlistError) as caught:
        hosts.check_endpoint(
            entries,
            url="https://elsewhere.example.test/v1/chat/completions",
            profile="openai/1",
            resolver=resolver,
        )
    assert caught.value.code == "host_not_allowed"
    assert asked == []
    with pytest.raises(hosts.JudgeAllowlistError) as caught:
        hosts.check_endpoint(entries, url=URL, profile="openrouter/1", resolver=resolver)
    assert caught.value.code == "profile_mismatch"
    assert asked == []
    with pytest.raises(profiles.JudgeProfileError) as caught:
        hosts.check_endpoint(entries, url=URL, profile="openai/1", resolver=resolver)
    assert caught.value.code == "profile_unverified"
    assert asked == []
    with pytest.raises(hosts.JudgeAllowlistError) as caught:
        hosts.check_endpoint(
            entries, url=URL, profile="openai/1", resolver=resolver, first_request_gate=True
        )
    assert caught.value.code == "dns_answer_not_public"
    assert asked == ["api.openai.com"]
    loopback = hosts.parse_judge_allowlist(
        "http://127.0.0.1:8123/v1/chat/completions profile=local_loopback/1\n"
    )
    entry = hosts.check_endpoint(
        loopback,
        url="http://127.0.0.1:8123/v1/chat/completions",
        profile="local_loopback/1",
        resolver=resolver,
    )
    assert entry.key_env is None, "a literal loopback address needs no lookup"
    assert asked == ["api.openai.com"], "a literal loopback address needs no lookup"


# ------------------------------------------------------- two behaviours of the part 1 code


def test_the_usage_of_a_paid_reply_is_handed_out_before_its_exchange_file_is_written(
    tmp_path: Path,
) -> None:
    """A file that cannot be written (here: one already there) must not lose the record of a request
    that was answered and paid for: ``on_usage`` has the record when the write fails."""
    exchange = jmod("judgeexchange")
    store = exchange.ExchangeStore(tmp_path / "judge" / "run")
    (store.directory / "item-mr_q00-1.json").write_text("{}", "utf-8")
    os.chmod(store.directory / "item-mr_q00-1.json", 0o600)
    rig = JudgeRig(
        tmp_path, [openai_reply(content=verdict_text(), reply_id="r-1")], exchanges=store
    )
    seen: list[Any] = []
    with pytest.raises(exchange.JudgeExchangeError) as caught:
        rig.complete(request(0), on_usage=seen.append)
    assert caught.value.code == "exchange_exists"
    assert [r.stage for r in seen] == ["judge"], "the usage of the paid request was handed out"
    assert seen[0].provider == "local_loopback/1"


def test_a_short_read_does_not_truncate_an_exchange_file_or_an_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.read`` may return fewer bytes than are there: both readers loop until the end."""
    exchange, hosts = jmod("judgeexchange"), jmod("judgehosts")
    store = exchange.ExchangeStore(tmp_path / "judge" / "run")
    document = _exchange_doc()
    store.write("mr_q00", 1, document)
    allowlist = tmp_path / "judge-allowlist.txt"
    allowlist.write_text(ENTRY + "# " + "x" * 300 + "\n", "utf-8")
    allowlist.chmod(0o600)
    real_read = os.read

    def short(descriptor: int, count: int) -> bytes:
        return real_read(descriptor, min(count, 7))

    monkeypatch.setattr(exchange.os, "read", short)
    monkeypatch.setattr(hosts.os, "read", short)
    assert store.read("mr_q00", 1) == document
    assert [e.profile for e in hosts.read_judge_allowlist(allowlist)] == ["openai/1"]
