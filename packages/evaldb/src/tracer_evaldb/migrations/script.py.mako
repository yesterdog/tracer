# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""${message}"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

# Tables (or "table.column" entries) whose rows or values the downgrade of this revision destroys.
DOWNGRADE_DROPS: tuple[str, ...] = ()


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
