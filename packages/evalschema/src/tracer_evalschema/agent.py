# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""AgentTarget: the AI voice agent under evaluation and how to reach it (ADR 0007, 0009, 0012).

- ``WebSocketEndpoint{transport: ws, url}``, ``WebRtcEndpoint{transport: webrtc, url}`` and
  ``SipEndpoint{transport: sip, uri}``; ``Endpoint`` is their union, discriminated by
  ``transport``. Each address is checked by ``url_rules`` (no userinfo or SIP password, no
  credential parameter names, no credential patterns).
- ``DeclaredStackEntry{stage, provider, model}``: one component of the agent's stack as the user
  declares it (``stage`` in ``stt``, ``llm``, ``tts``, ``realtime``).
- ``AgentTarget`` (``agt``, versioned): ``name``, ``endpoint``, and the declarations
  ``languages``, ``declared_capabilities`` and ``declared_stack``. What a run detects lives on
  ``Run`` and is never written back here (P-23); no not-applicable logic reads a declaration
  (ADR 0004 Decision 2).
- ``AgentTargetRef{id, version, content_digest?}``: the typed reference a Run holds (N35).

Every model here that can reach a URL hides its input in validation errors (ADR 0007
Decision 8), and the URL messages name the rule and the URL part only. No message inside them
quotes a rejected value (Decision 8 (c), finding N42): the endpoint union reports the allowed
transports, never the transport found.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Discriminator, Field, Tag

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema.base import VersionedEntity, VersionedRef
from tracer_evalschema.ids import AgentTargetId
from tracer_evalschema.language import SupportedLanguage
from tracer_evalschema.metrics import InputKind
from tracer_evalschema.text import HumanLabel, MachineKey
from tracer_evalschema.url_rules import (
    SIP_URI_PATTERN,
    WEBRTC_URL_PATTERN,
    WEBSOCKET_URL_PATTERN,
    UrlRule,
    check_sip_uri,
    check_webrtc_url,
    check_websocket_url,
)

__all__ = [
    "DECLARED_STAGES",
    "TRANSPORTS",
    "AgentTarget",
    "AgentTargetRef",
    "DeclaredStackEntry",
    "Endpoint",
    "SipEndpoint",
    "WebRtcEndpoint",
    "WebSocketEndpoint",
]

TRANSPORTS: Final = ("ws", "webrtc", "sip")
DECLARED_STAGES: Final = ("stt", "llm", "tts", "realtime")

_HIDING = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WebSocketEndpoint(HidingModel):
    """A WebSocket endpoint: ``ws://`` or ``wss://`` with a host, no credentials, no fragment."""

    model_config = _HIDING

    transport: Annotated[str, ClosedSet(("ws",))]
    url: Annotated[str, UrlRule(check_websocket_url, WEBSOCKET_URL_PATTERN)]


class WebRtcEndpoint(HidingModel):
    """A WebRTC signalling endpoint: ``http://`` or ``https://`` with a host, no credentials."""

    model_config = _HIDING

    transport: Annotated[str, ClosedSet(("webrtc",))]
    url: Annotated[str, UrlRule(check_webrtc_url, WEBRTC_URL_PATTERN)]


class SipEndpoint(HidingModel):
    """A SIP endpoint: ``sip:`` or ``sips:``; the user part is the address, a password is not."""

    model_config = _HIDING

    transport: Annotated[str, ClosedSet(("sip",))]
    uri: Annotated[str, UrlRule(check_sip_uri, SIP_URI_PATTERN)]


def _transport_of(value: Any) -> str | None:
    transport = (
        value.get("transport") if isinstance(value, dict) else getattr(value, "transport", None)
    )
    return transport if isinstance(transport, str) else None


Endpoint = Annotated[
    Annotated[WebSocketEndpoint, Tag("ws")]
    | Annotated[WebRtcEndpoint, Tag("webrtc")]
    | Annotated[SipEndpoint, Tag("sip")],
    Discriminator(
        _transport_of,
        custom_error_type="union_tag",
        custom_error_message="an endpoint must be an object whose transport is one of ws, webrtc, "
        "sip",
        custom_error_context={"allowed": ", ".join(TRANSPORTS)},
    ),
]
"""How to reach the agent, discriminated by ``transport`` (ADR 0007 I7)."""


class DeclaredStackEntry(BaseModel):
    """One declared component of the agent's stack (ADR 0012 I15)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Annotated[str, ClosedSet(DECLARED_STAGES)]
    provider: MachineKey
    model: MachineKey


def _no_repeats(what: str) -> Any:
    def check(values: list[str]) -> list[str]:
        seen: set[str] = set()
        for position, value in enumerate(values):
            if value in seen:
                if values_hidden():
                    raise ValueError(f"{what} lists one value twice (again at position {position})")
                raise ValueError(f"{what} lists {value!r} twice")
            seen.add(value)
        return values

    return check


class AgentTarget(HidingModel, VersionedEntity):
    """The agent under evaluation (prefix ``agt``), versioned for its declared fields.

    ``languages`` may be empty, which means "no language declared", never "all languages"; a tag
    appears once (after case canonicalization). ``declared_capabilities`` is a unique list of
    ``InputKind``. ``declared_stack`` keeps its order.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    id_prefix: ClassVar[str] = "agt"

    name: HumanLabel
    endpoint: Endpoint
    languages: Annotated[
        list[SupportedLanguage],
        AfterValidator(_no_repeats("languages")),
        Field(json_schema_extra={"uniqueItems": True}),
    ] = Field(default_factory=list)
    declared_capabilities: Annotated[
        list[InputKind],
        AfterValidator(_no_repeats("declared_capabilities")),
        Field(json_schema_extra={"uniqueItems": True}),
    ] = Field(default_factory=list)
    declared_stack: list[DeclaredStackEntry] = Field(default_factory=list)


class AgentTargetRef(VersionedRef):
    """``{id, version, content_digest?}`` of an ``AgentTarget`` (ADR 0009 Decision 4, N35): what a
    Run holds. What the run detects is recorded on the Run, never written back (P-23)."""

    id: AgentTargetId
