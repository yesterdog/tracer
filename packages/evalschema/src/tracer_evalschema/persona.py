# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Persona: who the simulated caller is (ADR 0005, ADR 0009, ADR 0012).

``Persona`` (``per``, versioned) holds ``language`` (a configured language: native script only,
one language, no mixed-language input in 0.0.1) and an optional ``accent`` label (a
``HumanLabel``, not a language tag). The plan's further persona fields are not adopted in 0.0.1
(api-surface 19.6 N11). ``PersonaRef`` is the typed ``{id, version, content_digest?}`` reference
to a persona.

``Persona`` hides its input in validation errors (ADR 0007 Decision 8 (b), finding N41 decided by
the owner): its accent is user-typed text.
"""

from __future__ import annotations

from typing import ClassVar

from tracer_evalschema._hiding import HidingModel
from tracer_evalschema.base import VersionedEntity, VersionedRef
from tracer_evalschema.ids import PersonaId
from tracer_evalschema.language import SupportedLanguage
from tracer_evalschema.text import HumanLabel

__all__ = ["Persona", "PersonaRef"]


class Persona(HidingModel, VersionedEntity):
    """The simulated caller (prefix ``per``): a configured language and an optional accent."""

    id_prefix: ClassVar[str] = "per"

    language: SupportedLanguage
    accent: HumanLabel | None = None


class PersonaRef(VersionedRef):
    """``{id, version, content_digest?}`` of a ``Persona`` (ADR 0009 Decision 4)."""

    id: PersonaId
