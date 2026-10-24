# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Access-pattern indexes (revision 0002): 25 plain indexes, no table or row changes.

Frozen text: the index names and columns are written out below, never derived from the live table
definitions, so a later change to them cannot change what this revision does (ADR 0021). ``key``
is quoted because it is a reserved word and the live metadata writes it that way.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# An index holds no row and no value: a downgrade through this revision loses no data.
DOWNGRADE_DROPS: tuple[str, ...] = ()

# (index name, table, key columns as written in CREATE INDEX)
_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_run_project_id_id", "run", "project_id, id"),
    ("ix_run_agent_target_id_agent_target_version", "run", "agent_target_id, agent_target_version"),
    ("ix_run_mode", "run", "mode"),
    ("ix_run_observed_tier", "run", "observed_tier"),
    ("ix_run_started_at", "run", "started_at"),
    ("ix_run_config_snapshot_id", "run", "config_snapshot_id"),
    ("ix_artifact_run_id", "artifact", "run_id"),
    ("ix_artifact_sha256", "artifact", "sha256"),
    ("ix_transcript_run_id", "transcript", "run_id"),
    ("ix_turn_run_id", "turn", "run_id"),
    ("ix_exchange_run_id", "exchange", "run_id"),
    ("ix_tool_call_event_run_id", "tool_call_event", "run_id"),
    ("ix_stage_span_run_id", "stage_span", "run_id"),
    ("ix_metric_result_run_id", "metric_result", "run_id"),
    (
        "ix_metric_result_metric_id_metric_version_status",
        "metric_result",
        "metric_id, metric_version, status",
    ),
    ("ix_run_verdict_run_id", "run_verdict", "run_id"),
    ("ix_run_verdict_status", "run_verdict", "status"),
    ("ix_usage_record_run_id", "usage_record", "run_id"),
    ("ix_cost_record_run_id", "cost_record", "run_id"),
    ("ix_cost_summary_run_id", "cost_summary", "run_id"),
    ("ix_run_bundle_run_id", "run_bundle", "run_id"),
    ("ix_config_snapshot_content_digest_value", "config_snapshot", "content_digest_value"),
    ("ix_metric_definition_key", "metric_definition", '"key"'),
    ("ix_dataset_item_artifact_id", "dataset_item", "artifact_id"),
    ("ix_dataset_item_transcript_id", "dataset_item", "transcript_id"),
)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.execute(f"CREATE INDEX {name} ON {table} ({columns})")


def downgrade() -> None:
    for name, _table, _columns in reversed(_INDEXES):
        op.execute(f"DROP INDEX {name}")
