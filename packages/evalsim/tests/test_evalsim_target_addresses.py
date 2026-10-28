# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-31 (target safety): the addresses a target name resolves to (ADR 0026 rule 16, BL-142).

Contract: docs/phase4/api-surface-batch-g.md section 5. The simulator is the project's first
dialer: a name on the allowlist is resolved once per call and every address of the answer is
checked before the connection. A private, loopback, link-local or unspecified address is reachable
only through an entry that names that literal address or ``localhost``; ``localhost`` must resolve
to loopback addresses only, so a public name that answers with a private address (DNS rebinding)
is refused. The resolver is injected; no test resolves a name. The race between the check and the
connection is not claimed closed (the judge's precedent, BL-326).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from evalsim_support_g import mod  # type: ignore[import-not-found,unused-ignore]

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def addresses() -> Any:
    return mod("addresses")


class Recorder:
    """A resolver that answers from a table and remembers what it was asked."""

    def __init__(self, answer: Sequence[str] | Exception) -> None:
        self.answer = answer
        self.asked: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> Sequence[str]:
        self.asked.append((host, port))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def code_of(host: str, port: int, answer: Sequence[str] | Exception) -> str:
    resolver = Recorder(answer)
    with pytest.raises(mod("errors").TargetRefusedError) as caught:
        addresses().check_target_addresses(host, port, resolver=resolver)
    assert str(caught.value).startswith("TargetRefusedError: code ")
    assert resolver.asked == [(host, port)]  # one lookup, never more
    return str(caught.value.code)


def test_the_new_codes_are_in_the_closed_sorted_list_of_refusals() -> None:
    codes = mod("errors").REFUSAL_CODES
    assert list(codes) == sorted(set(codes))
    assert {"target_address_refused", "target_unresolvable"} <= set(codes)


@pytest.mark.parametrize("answer", [[PUBLIC_V4], [PUBLIC_V6], [PUBLIC_V4, PUBLIC_V6]])
def test_a_public_name_with_only_public_addresses_passes_after_one_lookup(
    answer: list[str],
) -> None:
    resolver = Recorder(answer)
    addresses().check_target_addresses("agent.example.test", 443, resolver=resolver)
    assert resolver.asked == [("agent.example.test", 443)]


@pytest.mark.parametrize(
    "bad",
    [
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "127.0.0.1",
        "127.9.9.9",
        "169.254.169.254",  # the cloud metadata address
        "0.0.0.0",  # noqa: S104 - an unspecified address as a test input, not a bind
        "100.64.0.1",  # carrier-grade NAT, not public
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd12:3456:789a::1",
        "::ffff:127.0.0.1",  # a mapped loopback is loopback
        "::ffff:10.1.2.3",
        "::ffff:169.254.169.254",
        "fe80::1%eth0",  # a scoped link-local address
    ],
)
def test_a_public_name_that_resolves_to_a_non_public_address_is_refused(bad: str) -> None:
    """DNS rebinding: one bad address anywhere in the answer refuses the target, whatever else the
    answer holds and in whatever order."""
    assert code_of("agent.example.test", 443, [bad]) == "target_address_refused"
    assert code_of("agent.example.test", 443, [PUBLIC_V4, bad]) == "target_address_refused"
    assert code_of("agent.example.test", 443, [bad, PUBLIC_V4]) == "target_address_refused"


def test_localhost_is_checked_too_and_must_resolve_to_loopback_only() -> None:
    resolver = Recorder(["127.0.0.1", "::1"])
    addresses().check_target_addresses("localhost", 8765, resolver=resolver)
    assert resolver.asked == [("localhost", 8765)]
    assert code_of("localhost", 8765, ["127.0.0.1", "10.0.0.1"]) == "target_address_refused"
    assert code_of("localhost", 8765, [PUBLIC_V4]) == "target_address_refused"
    assert code_of("localhost", 8765, ["::ffff:10.0.0.1"]) == "target_address_refused"
    resolver = Recorder(["::ffff:127.0.0.1"])
    addresses().check_target_addresses("localhost", 8765, resolver=resolver)  # mapped loopback


@pytest.mark.parametrize(
    "literal",
    ["127.0.0.1", "::1", "10.1.2.3", "192.168.7.7", "169.254.1.1", "93.184.216.34"],
)
def test_a_literal_address_in_the_entry_is_not_resolved_and_is_allowed(literal: str) -> None:
    """The operator wrote that literal address: it is the narrow exception of rule 16 (an agent on
    the operator's own network can be called) and there is no name to resolve."""
    resolver = Recorder(AssertionError("a literal address was looked up"))
    addresses().check_target_addresses(literal, 8765, resolver=resolver)
    assert resolver.asked == []


@pytest.mark.parametrize("answer", [[], OSError("no such host"), TimeoutError("slow")])
def test_a_name_that_cannot_be_resolved_is_refused_not_assumed_safe(answer: Any) -> None:
    assert code_of("agent.example.test", 443, answer) == "target_unresolvable"


@pytest.mark.parametrize("bad", ["not an address", "", "999.1.1.1", "1.2.3", "1.2.3.4.5"])
def test_an_answer_that_is_not_an_address_is_refused(bad: str) -> None:
    assert code_of("agent.example.test", 443, [PUBLIC_V4, bad]) == "target_address_refused"


def test_the_refusal_names_no_host_and_no_address() -> None:
    resolver = Recorder(["10.9.8.7"])
    with pytest.raises(mod("errors").TargetRefusedError) as caught:
        addresses().check_target_addresses("secret-host.example.test", 443, resolver=resolver)
    text = str(caught.value) + repr(caught.value) + repr(vars(caught.value))
    assert "secret-host" not in text
    assert "10.9.8.7" not in text


def test_the_default_resolver_is_the_system_one_and_has_the_judge_shape() -> None:
    """``system_resolver(host, port)`` returns the text addresses of ``getaddrinfo`` (checked only
    by its signature here; the unit tests may not resolve a name: the guard raises)."""
    import inspect

    resolver = addresses().system_resolver
    assert list(inspect.signature(resolver).parameters) == ["host", "port"]
    with pytest.raises(AssertionError, match="network"):
        resolver("agent.example.test", 443)
