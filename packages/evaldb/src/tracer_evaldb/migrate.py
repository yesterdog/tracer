# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Programmatic migrations (ADR 0021 rules 3, 4 and 13): ``upgrade``, ``downgrade`` and the
revision queries. Nothing here runs by itself and ``open_store`` never calls it.

Alembic is imported inside the functions: ``import tracer_evaldb`` loads none of it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from importlib import resources
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .errors import DowngradeDataLoss, StoreRevisionUnknown

if TYPE_CHECKING:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

__all__ = ["alembic_config", "current_revision", "downgrade", "head_revision", "upgrade"]


def _engine(engine: object) -> Engine:
    if not isinstance(engine, Engine):
        raise TypeError("the first argument must be the Engine of create_store_engine")
    return engine


def _revision(revision: object) -> str:
    if not isinstance(revision, str):
        raise TypeError("revision must be a str")
    if not revision:
        raise ValueError("revision must not be empty")
    return revision


def alembic_config(
    engine: Engine | None = None,
    *,
    version_locations: Sequence[str | os.PathLike[str]] = (),
) -> Config:
    """A ``Config`` for the shipped migrations plus any extra revision directories.

    The engine travels in ``config.attributes["engine"]``; there is no URL.
    """
    from alembic.config import Config

    config = Config()
    location = str(resources.files("tracer_evaldb") / "migrations")
    config.set_main_option("script_location", location)
    if version_locations:
        paths = [os.path.join(location, "versions"), *(os.fspath(p) for p in version_locations)]
        config.set_main_option("path_separator", "os")
        config.set_main_option("version_locations", os.pathsep.join(paths))
    if engine is not None:
        config.attributes["engine"] = _engine(engine)
    return config


def head_revision() -> str:
    """The head of the migrations this evaldb ships."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("the shipped migrations have no head revision")
    return head


def current_revision(engine: Engine) -> str | None:
    """The revision the database is at, or ``None`` when it was never migrated."""
    from alembic.migration import MigrationContext

    with _engine(engine).connect() as connection:
        found = MigrationContext.configure(connection).get_current_revision()
        connection.rollback()
    return found


def is_known(script: ScriptDirectory, revision: str) -> bool:
    """True when the shipped (and extra) revisions include ``revision``."""
    try:
        return script.get_revision(revision) is not None
    except Exception:
        return False


def _require_target(script: ScriptDirectory, revision: str, *, allow_base: bool) -> None:
    if allow_base and revision == "base":
        return
    try:
        script.get_revision(revision)
    except Exception:
        raise ValueError("unknown target revision") from None


def upgrade(
    engine: Engine,
    revision: str = "head",
    *,
    version_locations: Sequence[str | os.PathLike[str]] = (),
) -> None:
    """Migrate the database up to ``revision``; each revision runs in its own transaction."""
    from alembic import command
    from alembic.script import ScriptDirectory

    _engine(engine)
    revision = _revision(revision)
    config = alembic_config(engine, version_locations=version_locations)
    script = ScriptDirectory.from_config(config)
    current = current_revision(engine)
    if current is not None and not is_known(script, current):
        raise StoreRevisionUnknown(current)
    _require_target(script, revision, allow_base=True)
    command.upgrade(config, revision)


def _lost_tables(engine: Engine, script: ScriptDirectory, current: str, target: str) -> list[str]:
    """Tables whose rows (or column values) the revisions between ``current`` and ``target``
    would destroy, by the ``DOWNGRADE_DROPS`` each revision declares."""
    stop = None if target == "base" else script.get_revision(target)
    undone = [
        rev
        for rev in script.iterate_revisions(current, target)
        if stop is None or rev.revision != stop.revision
    ]
    lost: set[str] = set()
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for rev in undone:
            for entry in getattr(rev.module, "DOWNGRADE_DROPS", ()):
                table, _, column = entry.partition(".")
                if not inspector.has_table(table):
                    continue
                where = f' WHERE "{column}" IS NOT NULL' if column else ""
                count = connection.exec_driver_sql(
                    f'SELECT count(*) FROM "{table}"{where}'  # noqa: S608
                ).scalar()
                if count:
                    lost.add(table)
        connection.rollback()
    return sorted(lost)


def downgrade(
    engine: Engine,
    revision: str,
    *,
    allow_data_loss: bool = False,
    version_locations: Sequence[str | os.PathLike[str]] = (),
) -> None:
    """Migrate the database down to ``revision`` (``"base"`` for nothing).

    A downgrade that would destroy rows is refused whole, before its first step, unless
    ``allow_data_loss`` is ``True``. A database that was never migrated is left alone.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    _engine(engine)
    revision = _revision(revision)
    if type(allow_data_loss) is not bool:
        raise TypeError("allow_data_loss must be a bool")
    config = alembic_config(engine, version_locations=version_locations)
    script = ScriptDirectory.from_config(config)
    current = current_revision(engine)
    if current is None:
        return
    if not is_known(script, current):
        raise StoreRevisionUnknown(current)
    _require_target(script, revision, allow_base=True)
    if not allow_data_loss:
        lost = _lost_tables(engine, script, current, revision)
        if lost:
            raise DowngradeDataLoss(lost)
    command.downgrade(config, revision)
