# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Project: the owner of agent targets, evaluators and provider configurations (ADR 0012).

A record of fact (ADR 0009 Decision 5: not versioned). ``provider_configs`` holds the
``ProviderConfigRef`` entries a run uses to reach the user's keys; it is unique per
``(provider, model)``, because a run resolves a ``ModelRef`` to the one entry with the same pair
(ADR 0007 Decision 1). The model hides its input in validation errors (ADR 0007 Decision 8),
because it embeds provider references.
"""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import AfterValidator, ConfigDict, Field

from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema.base import EntityBase
from tracer_evalschema.provider import ProviderConfigRef
from tracer_evalschema.text import HumanLabel

__all__ = ["Project"]


def _unique_provider_models(configs: list[ProviderConfigRef]) -> list[ProviderConfigRef]:
    seen: set[tuple[str, str]] = set()
    for config in configs:
        pair = (config.provider, config.model)
        if pair in seen and values_hidden():
            raise ValueError(
                "provider_configs holds two entries for one provider and model; a run resolves a "
                "model to exactly one entry"
            )
        if pair in seen:
            raise ValueError(
                f"provider_configs holds two entries for provider {config.provider!r} and model "
                f"{config.model!r}; a run resolves a model to exactly one entry"
            )
        seen.add(pair)
    return configs


class Project(HidingModel, EntityBase):
    """A project (prefix ``prj``): a name and the provider configurations of its runs."""

    model_config = ConfigDict(hide_input_in_errors=True)

    id_prefix: ClassVar[str] = "prj"

    name: HumanLabel
    provider_configs: Annotated[
        list[ProviderConfigRef],
        AfterValidator(_unique_provider_models),
    ] = Field(default_factory=list)
