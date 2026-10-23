# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""References to providers and models, never to keys themselves (ADR 0007 Decision 1).

- ``ProviderConfigRef{provider, model, config_ref}``: where tracer itself calls a provider with
  the user's key (held by ``Project.provider_configs``). ``config_ref`` names the key for a
  runtime outside the schema (``env:SARVAM_API_KEY``, ``keyring:...``, ``vault:...``).
- ``ModelRef{provider, model}``: names a model and carries no key reference (a metric
  definition's judge, ADR 0010 Decision 1a). A run resolves it to the Project entry with the
  same pair.

``provider`` and ``model`` are open values (``MachineKey``): unseen providers are accepted.
``ProviderConfigRef`` hides its input in validation errors, and the ``config_ref`` messages name
the rule only, so a pasted key is never echoed (ADR 0007 Decision 8).

``config_ref`` is rejected (``config_ref_credential``) when the whole text holds a known
credential pattern or when the name part after the first ``:`` holds a high-entropy run, for
every scheme (ADR 0007 Decision 1; P1-21 finding m6, A6). One message for both, nothing quoted.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic_core import PydanticCustomError

from tracer_evalschema._constraints import FullMatch
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema.credentials import contains_credential_pattern, is_high_entropy_text
from tracer_evalschema.text import MACHINE_KEY_PATTERN, MachineKey

__all__ = ["CONFIG_REF_PATTERN", "ConfigRef", "ModelRef", "ProviderConfigRef"]

CONFIG_REF_PATTERN = "^[a-z][a-z0-9+.-]*:[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
"""``<scheme>:<name>``: a lowercase scheme and a name in the ``MachineKey`` characters."""

CONFIG_REF_MAX_LENGTH = 256

# A MachineKey that does not echo the rejected text (the key of a pasted credential could land in
# any field of a provider reference).
_QuietMachineKey = Annotated[
    str,
    FullMatch(
        MACHINE_KEY_PATTERN,
        "machine_key",
        "not a machine key ({pattern})",
        echo_value=False,
    ),
]


def _no_credential_pattern(value: str) -> str:
    """The known-prefix rule on the whole text (the scheme part included) and the high-entropy
    rule on the name part, whatever the scheme (m6, A6). Runs after the pattern, so the text is
    ``<scheme>:<name>`` with a non-empty name and no ``=``."""
    name = value.partition(":")[2]
    if contains_credential_pattern(value) or is_high_entropy_text(name):
        raise PydanticCustomError(
            "config_ref_credential",
            "config_ref looks like a credential (a known credential pattern, or a high-entropy "
            "name after the scheme; ADR 0007 Decisions 1 and 5a): store a reference to the key "
            "(for example env:NAME), never the key",
        )
    return value


ConfigRef = Annotated[
    str,
    FullMatch(
        CONFIG_REF_PATTERN,
        "config_ref",
        "config_ref must be <scheme>:<name> (lowercase scheme, then letters, digits or ._:/@+-; "
        "no '=' and at most 256 characters), for example env:SARVAM_API_KEY",
        max_length=CONFIG_REF_MAX_LENGTH,
        echo_value=False,
    ),
    AfterValidator(_no_credential_pattern),
]
"""A reference to a key (P-19): ``<scheme>:<name>``, never the key itself."""


class ProviderConfigRef(HidingModel):
    """A provider and model that tracer calls with the key named by ``config_ref``."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: _QuietMachineKey
    model: _QuietMachineKey
    config_ref: ConfigRef


class ModelRef(BaseModel):
    """A provider and model, without any key reference (ADR 0010 Decision 1a)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: MachineKey
    model: MachineKey
