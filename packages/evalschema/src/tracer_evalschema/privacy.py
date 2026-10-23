# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Privacy markers and the human-subject derivation (ADR 0016 Decisions 3 to 6; P1-27).

- ``ConsentRecord{status, reference?, recorded_at?}``: ``status`` is obtained, not_obtained or
  unknown.
- ``RetentionPolicy{retain_until?, policy_ref?, delete_on_expiry}``.
- ``PrivacyMarker{human_subject, human_subject_reason?, consent?, retention?}``: consent and
  retention are present if and only if ``human_subject`` is true (P-46); a reason marks a marker
  stricter than the derivation (P-47) and exists only on a true marker.
- ``derive_human_subject(run, turns)``: true for a human caller, for any ``unknown`` role, and
  when there is no role evidence at all; false only for a simulated caller whose role evidence is
  caller, agent and mixed channels only (ADR 0016 Decision 5, N24). ``turns`` is required, and
  only a real sequence is accepted (O1): a caller that forgot the turns gets a ``TypeError``,
  never a silent "not human".
- ``check_human_subject_marker(marker, run, turns)``: a stored marker may be stricter than the
  derivation (with a reason), never looser (P-47). The marker is never changed.

The export-time functions (``export_human_subject``, P-57) belong to the bundles (P1-14).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Strict, model_validator

from tracer_evalschema._constraints import ClosedSet
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.primitives import UtcDatetime
from tracer_evalschema.text import MachineKey, VerbatimText

if TYPE_CHECKING:
    from tracer_evalschema.run import Run
    from tracer_evalschema.transcript import Turn

__all__ = [
    "CONSENT_STATUSES",
    "HUMAN_SUBJECT_REASONS",
    "ConsentRecord",
    "PrivacyMarker",
    "RetentionPolicy",
    "check_human_subject_marker",
    "derive_human_subject",
    "role_evidence",
]

CONSENT_STATUSES: Final = ("obtained", "not_obtained", "unknown")
HUMAN_SUBJECT_REASONS: Final = ("marked_before_role_evidence", "real_person_likeness", "manual")


class ConsentRecord(BaseModel):
    """Whether the person recorded consented (ADR 0016 Decision 3). ``unknown`` is a state, not a
    default: it counts as not obtained for benchmark exports (P-44, P1-14)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Annotated[str, ClosedSet(CONSENT_STATUSES)]
    reference: VerbatimText | None = None
    recorded_at: UtcDatetime | None = None


class RetentionPolicy(BaseModel):
    """How long human-subject data is kept, and whether it is deleted when the time is up."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    retain_until: UtcDatetime | None = None
    policy_ref: MachineKey | None = None
    delete_on_expiry: Annotated[bool, Strict()]


def _marker_conditions(schema: dict[str, Any]) -> None:
    """The P-46 presence rules as JSON Schema ``if``/``then`` keyed on ``human_subject``."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"human_subject": {"enum": [True]}},
                "required": ["human_subject"],
            },
            "then": {
                "required": ["consent", "retention"],
                "properties": {"consent": {"type": "object"}, "retention": {"type": "object"}},
            },
            "else": {
                "properties": {
                    "consent": {"type": "null"},
                    "retention": {"type": "null"},
                    "human_subject_reason": {"type": "null"},
                }
            },
        }
    ]


class PrivacyMarker(BaseModel):
    """Whether a payload is human-subject data, with its consent and retention (ADR 0016).

    Consent and retention are present if and only if ``human_subject`` is true (P-46): a consent
    record for a synthetic voice would read as a consent gap. ``human_subject_reason`` records why
    a marker is stricter than the derivation (P-47) and is rejected on a false marker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_marker_conditions)

    human_subject: Annotated[bool, Strict()]
    human_subject_reason: Annotated[str, ClosedSet(HUMAN_SUBJECT_REASONS)] | None = None
    consent: ConsentRecord | None = None
    retention: RetentionPolicy | None = None

    @model_validator(mode="after")
    def _presence_follows_human_subject(self) -> PrivacyMarker:
        problems: list[Problem] = []
        if self.human_subject:
            for name in ("consent", "retention"):
                if getattr(self, name) is None:
                    problems.append(
                        Problem(
                            (name,),
                            "privacy_marker",
                            f"a human-subject marker records its {name} (P-46)",
                        )
                    )
        else:
            for name in ("consent", "retention"):
                if getattr(self, name) is not None:
                    problems.append(
                        Problem(
                            (name,),
                            "privacy_marker",
                            f"a marker without human-subject data has no {name} (P-46)",
                        )
                    )
            if self.human_subject_reason is not None:
                problems.append(
                    Problem(
                        ("human_subject_reason",),
                        "privacy_marker",
                        "a reason marks a stricter (human_subject=true) marker; a false marker has "
                        "none",
                    )
                )
        raise_problems(type(self).__name__, problems)
        return self


# ------------------------------------------------------------------------------- derivation


def _require_sequence(turns: object) -> Sequence[Any]:
    if turns is None or isinstance(turns, (str, bytes)) or not isinstance(turns, Sequence):
        raise TypeError(
            "turns must be a sequence of the run's Turn records (an empty one means no turns); "
            f"got {type(turns).__name__}"
        )
    return turns


def role_evidence(run: Run, turns: Sequence[Turn]) -> frozenset[str]:
    """The roles found in the run's channel maps and in ``turns``. A ``mixed`` channel is evidence
    of the caller and the agent (P-31). The empty set means "no role evidence"."""
    roles: set[str] = {turn.role for turn in _require_sequence(turns)}
    for channel_map in run.channel_maps:
        for channel in channel_map.channels:
            roles.update(("caller", "agent") if channel.role == "mixed" else (channel.role,))
    return frozenset(roles)


def derive_human_subject(run: Run, turns: Sequence[Turn]) -> bool:
    """Whether the run's payloads are human-subject data (ADR 0016 Decision 5, N24, O1).

    True for ``caller_origin=human``; true when the role evidence (``role_evidence``) is empty;
    true when it holds ``unknown``; false otherwise (a simulated caller with caller and agent
    evidence only). ``turns`` are the Turn records of the run's transcripts; it has no default,
    and ``None`` or anything that is not a sequence raises ``TypeError``.
    """
    evidence = role_evidence(run, turns)
    if run.caller_origin == "human":
        return True
    return not evidence or "unknown" in evidence


def check_human_subject_marker(marker: PrivacyMarker, run: Run, turns: Sequence[Turn]) -> None:
    """Check a stored marker against the derivation (P-47); the marker is never changed.

    ``ValueError`` when the marker is looser (false where the derivation is true) or stricter
    without a ``human_subject_reason`` (true where the derivation is false). A too-loose record is
    corrected by a new record, never by editing this one.
    """
    derived = derive_human_subject(run, turns)
    if derived and not marker.human_subject:
        raise ValueError(
            "the stored privacy marker is looser than the derivation: human_subject=false, but the "
            "role evidence (a human caller, an unknown role, or no role evidence) derives true"
        )
    if marker.human_subject and not derived and marker.human_subject_reason is None:
        raise ValueError(
            "the stored privacy marker is stricter than the derivation (human_subject=true where "
            "it derives false) and needs a human_subject_reason"
        )
