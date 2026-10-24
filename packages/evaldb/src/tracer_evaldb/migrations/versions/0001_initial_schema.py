# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Initial schema of the store (revision 0001): the 37 tables with their constraints and indexes,
the immutability triggers and the single ``tracer_meta`` row.

Frozen text: the DDL below is written out, never derived from the live table definitions, so a later
change to them cannot change what this revision does (ADR 0021).
"""

from __future__ import annotations

from importlib.metadata import version

import sqlalchemy as sa
from alembic import op

from tracer_evaldb import triggers

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables whose rows a downgrade destroys (tracer_meta holds only this migration's own row).
DOWNGRADE_DROPS: tuple[str, ...] = (
    "agent_target",
    "artifact",
    "config_snapshot",
    "cost_record",
    "cost_summary",
    "dataset",
    "dataset_item",
    "dataset_member",
    "deletion_audit",
    "evaluator",
    "exchange",
    "export_bundle",
    "export_bundle_item",
    "export_bundle_profile",
    "export_bundle_run",
    "external_ref_index",
    "metric_binding",
    "metric_definition",
    "metric_result",
    "persona",
    "pricing_catalog",
    "project",
    "rubric",
    "run",
    "run_bundle",
    "run_bundle_member",
    "run_bundle_withheld",
    "run_verdict",
    "snapshot_member",
    "snapshot_profile_ref",
    "stage_span",
    "test_profile",
    "tool_call_event",
    "transcript",
    "turn",
    "usage_record",
)

# Creation order: a table comes after the tables its foreign keys name.
TABLES: tuple[str, ...] = (
    "agent_target",
    "config_snapshot",
    "dataset",
    "dataset_item",
    "deletion_audit",
    "evaluator",
    "export_bundle",
    "metric_binding",
    "metric_definition",
    "persona",
    "pricing_catalog",
    "project",
    "rubric",
    "test_profile",
    "tracer_meta",
    "dataset_member",
    "export_bundle_item",
    "export_bundle_profile",
    "export_bundle_run",
    "run",
    "snapshot_member",
    "snapshot_profile_ref",
    "artifact",
    "cost_record",
    "cost_summary",
    "exchange",
    "external_ref_index",
    "metric_result",
    "run_bundle",
    "run_verdict",
    "stage_span",
    "tool_call_event",
    "transcript",
    "turn",
    "usage_record",
    "run_bundle_member",
    "run_bundle_withheld",
)

DDL: dict[str, tuple[str, ...]] = {
    "postgresql": (
        (
            "CREATE TABLE agent_target (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            '\ttransport VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT transport_closed CHECK (transport IN ('ws', 'webrtc', 'sip"
            "')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE config_snapshot (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tevaluator_id UUID NOT NULL, \n"
            "\tevaluator_version BIGINT NOT NULL, \n"
            "\trubric_id UUID NOT NULL, \n"
            "\trubric_version BIGINT NOT NULL, \n"
            "\tpricing_catalog_id UUID, \n"
            "\tpricing_catalog_version BIGINT, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT catalog_pair CHECK (pricing_catalog_id IS NULL AND pricing"
            "_catalog_version IS NULL OR pricing_catalog_id IS NOT NULL AND pricing"
            "_catalog_version IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32), \n"
            "\tCONSTRAINT evaluator_id_uuid_v7 CHECK (substr(evaluator_id::text, 15,"
            " 1) = '7'), \n"
            "\tCONSTRAINT rubric_id_uuid_v7 CHECK (substr(rubric_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT pricing_catalog_id_uuid_v7 CHECK (substr(pricing_catalog_i"
            "d::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE dataset (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE dataset_item (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\tartifact_id UUID, \n"
            "\ttranscript_id UUID, \n"
            "\tsource_run_id UUID, \n"
            '\tlanguage VARCHAR COLLATE "C" NOT NULL, \n'
            "\thuman_subject BOOLEAN, \n"
            '\thuman_subject_reason VARCHAR COLLATE "C", \n'
            '\tconsent_status VARCHAR COLLATE "C", \n'
            "\tretain_until TIMESTAMP WITH TIME ZONE, \n"
            "\tdelete_on_expiry BOOLEAN, \n"
            '\tpolicy_ref VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS true AND consent_status IS NOT NULL AND delete_on_expiry IS NOT"
            " NULL OR human_subject IS false AND human_subject_reason IS NULL AND c"
            "onsent_status IS NULL AND retain_until IS NULL AND delete_on_expiry IS"
            " NULL AND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT artifact_id_uuid_v7 CHECK (substr(artifact_id::text, 15, 1"
            ") = '7'), \n"
            "\tCONSTRAINT transcript_id_uuid_v7 CHECK (substr(transcript_id::text, 1"
            "5, 1) = '7'), \n"
            "\tCONSTRAINT source_run_id_uuid_v7 CHECK (substr(source_run_id::text, 1"
            "5, 1) = '7'), \n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (TRUE, FALSE)),"
            " \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (TRUE, FA"
            "LSE))\n"
            ")"
        ),
        (
            "CREATE INDEX ix_dataset_item_retain_until ON dataset_item (retain_unti"
            "l) WHERE retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE deletion_audit (\n"
            "\taudit_uuid UUID NOT NULL, \n"
            "\tdeleted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            '\tactor VARCHAR COLLATE "C" NOT NULL, \n'
            '\treason VARCHAR COLLATE "C" NOT NULL, \n'
            '\troot_kind VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcounts TEXT NOT NULL, \n"
            "\tPRIMARY KEY (audit_uuid), \n"
            "\tCONSTRAINT reason_closed CHECK (reason IN ('operator_request', 'reten"
            "tion_policy', 'data_subject_request')), \n"
            "\tCONSTRAINT root_kind_closed CHECK (root_kind IN ('run', 'project', 't"
            "est_profile', 'agent_target', 'persona', 'evaluator', 'metric_definiti"
            "on', 'metric_binding', 'rubric', 'pricing_catalog', 'dataset', 'datase"
            "t_item', 'export_bundle', 'config_snapshot')), \n"
            "\tCONSTRAINT audit_uuid_uuid_v7 CHECK (substr(audit_uuid::text, 15, 1) "
            "= '7')\n"
            ")"
        ),
        (
            "CREATE TABLE evaluator (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tpersona_id UUID NOT NULL, \n"
            "\tpersona_version BIGINT NOT NULL, \n"
            "\ttest_profile_id UUID, \n"
            "\ttest_profile_version BIGINT, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT profile_pair CHECK (test_profile_id IS NULL AND test_profi"
            "le_version IS NULL OR test_profile_id IS NOT NULL AND test_profile_ver"
            "sion IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32), \n"
            "\tCONSTRAINT persona_id_uuid_v7 CHECK (substr(persona_id::text, 15, 1) "
            "= '7'), \n"
            "\tCONSTRAINT test_profile_id_uuid_v7 CHECK (substr(test_profile_id::tex"
            "t, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE export_bundle (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tpurpose VARCHAR COLLATE "C" NOT NULL, \n'
            "\tbyte_length BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT purpose_closed CHECK (purpose IN ('internal', 'shared_expo"
            "rt', 'benchmark')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            ")\n"
            ")"
        ),
        (
            "CREATE TABLE metric_binding (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tevaluator_id UUID NOT NULL, \n"
            "\tmetric_id UUID NOT NULL, \n"
            "\tmetric_version BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32), \n"
            "\tCONSTRAINT evaluator_id_uuid_v7 CHECK (substr(evaluator_id::text, 15,"
            " 1) = '7'), \n"
            "\tCONSTRAINT metric_id_uuid_v7 CHECK (substr(metric_id::text, 15, 1) = "
            "'7')\n"
            ")"
        ),
        (
            "CREATE TABLE metric_definition (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            '\tkey VARCHAR COLLATE "C" NOT NULL, \n'
            '\tkind VARCHAR COLLATE "C" NOT NULL, \n'
            '\tvalue_type VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('deterministic', 'llm_judge', "
            "'audio', 'latency')), \n"
            "\tCONSTRAINT value_type_closed CHECK (value_type IN ('boolean', 'rating"
            "', 'enum', 'numeric')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE persona (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            '\tlanguage VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE pricing_catalog (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE project (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tname VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            ")\n"
            ")"
        ),
        (
            "CREATE TABLE rubric (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE test_profile (\n"
            "\tid UUID NOT NULL, \n"
            "\tversion BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            '\tcontent_digest_scheme VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcontent_digest_value BYTEA NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (octet_length(conten"
            "t_digest_value) = 32)\n"
            ")"
        ),
        (
            "CREATE TABLE tracer_meta (\n"
            "\tsingleton BIGINT NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            '\tevaldb_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (singleton), \n"
            "\tCONSTRAINT singleton_is_1 CHECK (singleton = 1), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1')\n"
            ")"
        ),
        (
            "CREATE TABLE dataset_member (\n"
            "\tdataset_id UUID NOT NULL, \n"
            "\tdataset_version BIGINT NOT NULL, \n"
            "\tordinal BIGINT NOT NULL, \n"
            "\titem_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (dataset_id, dataset_version, ordinal), \n"
            "\tCONSTRAINT fk_dataset_member_dataset_id_dataset_version FOREIGN KEY(d"
            "ataset_id, dataset_version) REFERENCES dataset (id, version) ON DELETE"
            " CASCADE, \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT dataset_id_uuid_v7 CHECK (substr(dataset_id::text, 15, 1) "
            "= '7'), \n"
            "\tCONSTRAINT item_id_uuid_v7 CHECK (substr(item_id::text, 15, 1) = '7')"
            "\n"
            ")"
        ),
        ("CREATE INDEX ix_dataset_member_item_id ON dataset_member (item_id)"),
        (
            "CREATE TABLE export_bundle_item (\n"
            "\texport_id UUID NOT NULL, \n"
            "\titem_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (export_id, item_id), \n"
            "\tCONSTRAINT fk_export_bundle_item_export_id FOREIGN KEY(export_id) REF"
            "ERENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK (substr(export_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT item_id_uuid_v7 CHECK (substr(item_id::text, 15, 1) = '7')"
            "\n"
            ")"
        ),
        ("CREATE INDEX ix_export_bundle_item_item_id ON export_bundle_item (item_id)"),
        (
            "CREATE TABLE export_bundle_profile (\n"
            "\texport_id UUID NOT NULL, \n"
            "\tprofile_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (export_id, profile_id), \n"
            "\tCONSTRAINT fk_export_bundle_profile_export_id FOREIGN KEY(export_id) "
            "REFERENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK (substr(export_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT profile_id_uuid_v7 CHECK (substr(profile_id::text, 15, 1) "
            "= '7')\n"
            ")"
        ),
        ("CREATE INDEX ix_export_bundle_profile_profile_id ON export_bundle_profile (profile_id)"),
        (
            "CREATE TABLE export_bundle_run (\n"
            "\texport_id UUID NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (export_id, run_id), \n"
            "\tCONSTRAINT fk_export_bundle_run_export_id FOREIGN KEY(export_id) REFE"
            "RENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK (substr(export_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        ("CREATE INDEX ix_export_bundle_run_run_id ON export_bundle_run (run_id)"),
        (
            "CREATE TABLE run (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\tproject_id UUID NOT NULL, \n"
            "\tconfig_snapshot_id UUID NOT NULL, \n"
            "\tagent_target_id UUID NOT NULL, \n"
            "\tagent_target_version BIGINT NOT NULL, \n"
            '\tmode VARCHAR COLLATE "C" NOT NULL, \n'
            '\tcaller_origin VARCHAR COLLATE "C" NOT NULL, \n'
            '\tobserved_tier VARCHAR COLLATE "C" NOT NULL, \n'
            "\tstarted_at TIMESTAMP WITH TIME ZONE, \n"
            "\tcaller_human_subject BOOLEAN, \n"
            '\tcaller_human_subject_reason VARCHAR COLLATE "C", \n'
            '\tcaller_consent_status VARCHAR COLLATE "C", \n'
            "\tcaller_retain_until TIMESTAMP WITH TIME ZONE, \n"
            "\tcaller_delete_on_expiry BOOLEAN, \n"
            '\tcaller_policy_ref VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT mode_closed CHECK (mode IN ('simulated', 'recorded')), \n"
            "\tCONSTRAINT caller_origin_closed CHECK (caller_origin IN ('simulated',"
            " 'human')), \n"
            "\tCONSTRAINT observed_tier_closed CHECK (observed_tier IN ('T0', 'T1', "
            "'T2')), \n"
            "\tCONSTRAINT caller_human_subject_reason_closed CHECK (caller_human_sub"
            "ject_reason IN ('marked_before_role_evidence', 'real_person_likeness',"
            " 'manual')), \n"
            "\tCONSTRAINT caller_consent_status_closed CHECK (caller_consent_status "
            "IN ('obtained', 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_run_project_id FOREIGN KEY(project_id) REFERENCES proje"
            "ct (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT fk_run_config_snapshot_id FOREIGN KEY(config_snapshot_id) "
            "REFERENCES config_snapshot (id) ON DELETE RESTRICT, \n"
            "\tCONSTRAINT caller_privacy_projection CHECK (caller_human_subject IS N"
            "ULL AND caller_human_subject_reason IS NULL AND caller_consent_status "
            "IS NULL AND caller_retain_until IS NULL AND caller_delete_on_expiry IS"
            " NULL AND caller_policy_ref IS NULL OR caller_human_subject IS true AN"
            "D caller_consent_status IS NOT NULL AND caller_delete_on_expiry IS NOT"
            " NULL OR caller_human_subject IS false AND caller_human_subject_reason"
            " IS NULL AND caller_consent_status IS NULL AND caller_retain_until IS "
            "NULL AND caller_delete_on_expiry IS NULL AND caller_policy_ref IS NULL"
            "), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT project_id_uuid_v7 CHECK (substr(project_id::text, 15, 1) "
            "= '7'), \n"
            "\tCONSTRAINT config_snapshot_id_uuid_v7 CHECK (substr(config_snapshot_i"
            "d::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT agent_target_id_uuid_v7 CHECK (substr(agent_target_id::tex"
            "t, 15, 1) = '7'), \n"
            "\tCONSTRAINT caller_human_subject_bool CHECK (caller_human_subject IN ("
            "TRUE, FALSE)), \n"
            "\tCONSTRAINT caller_delete_on_expiry_bool CHECK (caller_delete_on_expir"
            "y IN (TRUE, FALSE))\n"
            ")"
        ),
        (
            "CREATE INDEX ix_run_caller_retain_until ON run (caller_retain_until) W"
            "HERE caller_retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE snapshot_member (\n"
            "\tsnapshot_id UUID NOT NULL, \n"
            '\tkind VARCHAR COLLATE "C" NOT NULL, \n'
            "\tordinal BIGINT NOT NULL, \n"
            "\tdef_id UUID NOT NULL, \n"
            "\tdef_version BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (snapshot_id, kind, ordinal), \n"
            "\tCONSTRAINT fk_snapshot_member_snapshot_id FOREIGN KEY(snapshot_id) RE"
            "FERENCES config_snapshot (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('evaluator', 'persona', 'rubri"
            "c', 'metric_definition', 'metric_binding')), \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT def_version_ge_1 CHECK (def_version >= 1), \n"
            "\tCONSTRAINT snapshot_id_uuid_v7 CHECK (substr(snapshot_id::text, 15, 1"
            ") = '7'), \n"
            "\tCONSTRAINT def_id_uuid_v7 CHECK (substr(def_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE INDEX ix_snapshot_member_kind_def_id_def_version ON snapshot_me"
            "mber (kind, def_id, def_version)"
        ),
        (
            "CREATE TABLE snapshot_profile_ref (\n"
            "\tsnapshot_id UUID NOT NULL, \n"
            "\tprofile_id UUID NOT NULL, \n"
            "\tprofile_version BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (snapshot_id), \n"
            "\tCONSTRAINT fk_snapshot_profile_ref_snapshot_id FOREIGN KEY(snapshot_i"
            "d) REFERENCES config_snapshot (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT profile_version_ge_1 CHECK (profile_version >= 1), \n"
            "\tCONSTRAINT snapshot_id_uuid_v7 CHECK (substr(snapshot_id::text, 15, 1"
            ") = '7'), \n"
            "\tCONSTRAINT profile_id_uuid_v7 CHECK (substr(profile_id::text, 15, 1) "
            "= '7')\n"
            ")"
        ),
        (
            "CREATE INDEX ix_snapshot_profile_ref_profile_id_profile_version ON sna"
            "pshot_profile_ref (profile_id, profile_version)"
        ),
        (
            "CREATE TABLE artifact (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tsha256 BYTEA NOT NULL, \n"
            "\tturn_ref UUID, \n"
            "\thuman_subject BOOLEAN NOT NULL, \n"
            '\thuman_subject_reason VARCHAR COLLATE "C", \n'
            '\tconsent_status VARCHAR COLLATE "C", \n'
            "\tretain_until TIMESTAMP WITH TIME ZONE, \n"
            "\tdelete_on_expiry BOOLEAN, \n"
            '\tpolicy_ref VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_artifact_run_id FOREIGN KEY(run_id) REFERENCES run (id)"
            " ON DELETE CASCADE, \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS true AND consent_status IS NOT NULL AND delete_on_expiry IS NOT"
            " NULL OR human_subject IS false AND human_subject_reason IS NULL AND c"
            "onsent_status IS NULL AND retain_until IS NULL AND delete_on_expiry IS"
            " NULL AND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7'), "
            "\n"
            "\tCONSTRAINT sha256_sha256_len CHECK (octet_length(sha256) = 32), \n"
            "\tCONSTRAINT turn_ref_uuid_v7 CHECK (substr(turn_ref::text, 15, 1) = '7"
            "'), \n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (TRUE, FALSE)),"
            " \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (TRUE, FA"
            "LSE))\n"
            ")"
        ),
        (
            "CREATE INDEX ix_artifact_retain_until ON artifact (retain_until) WHERE"
            " retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE cost_record (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tstatus VARCHAR COLLATE "C" NOT NULL, \n'
            '\tbasis VARCHAR COLLATE "C", \n'
            "\tamount NUMERIC, \n"
            '\tcurrency VARCHAR COLLATE "C", \n'
            '\tna_reason VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('available', 'not_availabl"
            "e')), \n"
            "\tCONSTRAINT basis_closed CHECK (basis IN ('reported', 'computed')), \n"
            "\tCONSTRAINT currency_closed CHECK (currency IN ('USD')), \n"
            "\tCONSTRAINT na_reason_closed CHECK (na_reason IN ('no_price_for_model'"
            ", 'unit_unpriced', 'no_usage', 'ambiguous_price', 'billing_rules_missi"
            "ng', 'usage_not_reported', 'unsupported_currency', 'currency_not_repor"
            "ted', 'amount_unparseable', 'currency_conflict')), \n"
            "\tCONSTRAINT fk_cost_record_run_id FOREIGN KEY(run_id) REFERENCES run ("
            "id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT amount_non_negative CHECK (amount >= 0), \n"
            "\tCONSTRAINT not_available_is_empty CHECK (status != 'not_available' OR"
            " amount IS NULL AND basis IS NULL AND currency IS NULL), \n"
            "\tCONSTRAINT currency_with_amount CHECK (amount IS NULL AND currency IS"
            " NULL OR amount IS NOT NULL AND currency IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE cost_summary (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tcompleteness VARCHAR COLLATE "C" NOT NULL, \n'
            "\ttotal NUMERIC, \n"
            '\tcurrency VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT completeness_closed CHECK (completeness IN ('complete', 'p"
            "artial', 'none')), \n"
            "\tCONSTRAINT currency_closed CHECK (currency IN ('USD')), \n"
            "\tCONSTRAINT fk_cost_summary_run_id FOREIGN KEY(run_id) REFERENCES run "
            "(id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT total_non_negative CHECK (total >= 0), \n"
            "\tCONSTRAINT currency_with_total CHECK (total IS NULL AND currency IS N"
            "ULL OR total IS NOT NULL AND currency IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE exchange (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tindex BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT fk_exchange_run_id FOREIGN KEY(run_id) REFERENCES run (id)"
            " ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE external_ref_index (\n"
            '\tentity_kind VARCHAR COLLATE "C" NOT NULL, \n'
            "\tentity_id UUID NOT NULL, \n"
            "\tkey_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (entity_kind, entity_id, key_sha256), \n"
            "\tCONSTRAINT fk_external_ref_index_run_id FOREIGN KEY(run_id) REFERENCE"
            "S run (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT entity_kind_closed CHECK (entity_kind IN ('run', 'span', '"
            "transcript', 'usage_record')), \n"
            "\tCONSTRAINT entity_id_uuid_v7 CHECK (substr(entity_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT key_sha256_sha256_len CHECK (octet_length(key_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE INDEX ix_external_ref_index_run_id_entity_kind_key_sha256 ON ex"
            "ternal_ref_index (run_id, entity_kind, key_sha256)"
        ),
        (
            "CREATE UNIQUE INDEX ux_external_ref_index_run_key ON external_ref_inde"
            "x (entity_kind, key_sha256) WHERE entity_kind = 'run'"
        ),
        (
            "CREATE TABLE metric_result (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tmetric_id UUID NOT NULL, \n"
            "\tmetric_version BIGINT NOT NULL, \n"
            '\tstatus VARCHAR COLLATE "C" NOT NULL, \n'
            '\tna_reason VARCHAR COLLATE "C", \n'
            "\tpassed BOOLEAN, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('scored', 'not_applicable'"
            ", 'error')), \n"
            "\tCONSTRAINT na_reason_closed CHECK (na_reason IN ('missing_input', 'ag"
            "ent_channel_unknown', 'language_not_observed', 'unsupported_language',"
            " 'unsupported_script')), \n"
            "\tCONSTRAINT fk_metric_result_run_id FOREIGN KEY(run_id) REFERENCES run"
            " (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT na_has_no_passed CHECK (status != 'not_applicable' OR pass"
            "ed IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7'), "
            "\n"
            "\tCONSTRAINT metric_id_uuid_v7 CHECK (substr(metric_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT passed_bool CHECK (passed IN (TRUE, FALSE))\n"
            ")"
        ),
        (
            "CREATE TABLE run_bundle (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT fk_run_bundle_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE run_verdict (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tstatus VARCHAR COLLATE "C" NOT NULL, \n'
            "\trequired_total BIGINT NOT NULL, \n"
            "\tpassed BIGINT NOT NULL, \n"
            "\tfailed BIGINT NOT NULL, \n"
            "\tnot_applicable BIGINT NOT NULL, \n"
            "\terrored BIGINT NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('pass', 'fail', 'inconclus"
            "ive')), \n"
            "\tCONSTRAINT fk_run_verdict_run_id FOREIGN KEY(run_id) REFERENCES run ("
            "id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE stage_span (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tstage VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT stage_closed CHECK (stage IN ('stt', 'llm', 'tts', 'judge'"
            ", 'sim_caller_llm', 'sim_caller_tts', 'realtime', 'other')), \n"
            "\tCONSTRAINT fk_stage_span_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE tool_call_event (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tsource VARCHAR COLLATE "C" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('agent_export', 'agent_tel"
            "emetry', 'observer')), \n"
            "\tCONSTRAINT fk_tool_call_event_run_id FOREIGN KEY(run_id) REFERENCES r"
            "un (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE transcript (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tsource VARCHAR COLLATE "C" NOT NULL, \n'
            '\tdetected_language VARCHAR COLLATE "C", \n'
            "\thuman_subject BOOLEAN NOT NULL, \n"
            '\thuman_subject_reason VARCHAR COLLATE "C", \n'
            '\tconsent_status VARCHAR COLLATE "C", \n'
            "\tretain_until TIMESTAMP WITH TIME ZONE, \n"
            "\tdelete_on_expiry BOOLEAN, \n"
            '\tpolicy_ref VARCHAR COLLATE "C", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('our_stt', 'agent_export',"
            " 'agent_telemetry', 'observer', 'script', 'human_reference')), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_transcript_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS true AND consent_status IS NOT NULL AND delete_on_expiry IS NOT"
            " NULL OR human_subject IS false AND human_subject_reason IS NULL AND c"
            "onsent_status IS NULL AND retain_until IS NULL AND delete_on_expiry IS"
            " NULL AND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7'), "
            "\n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (TRUE, FALSE)),"
            " \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (TRUE, FA"
            "LSE))\n"
            ")"
        ),
        (
            "CREATE INDEX ix_transcript_retain_until ON transcript (retain_until) W"
            "HERE retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE turn (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            "\ttranscript_id UUID NOT NULL, \n"
            "\tseq BIGINT NOT NULL, \n"
            '\trole VARCHAR COLLATE "C" NOT NULL, \n'
            "\tstart_ms BIGINT, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT role_closed CHECK (role IN ('caller', 'agent', 'unknown'))"
            ", \n"
            "\tCONSTRAINT fk_turn_run_id FOREIGN KEY(run_id) REFERENCES run (id) ON "
            "DELETE CASCADE, \n"
            "\tCONSTRAINT uq_turn_transcript_seq UNIQUE (transcript_id, seq), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7'), "
            "\n"
            "\tCONSTRAINT transcript_id_uuid_v7 CHECK (substr(transcript_id::text, 1"
            "5, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE usage_record (\n"
            "\tid UUID NOT NULL, \n"
            '\tschema_version VARCHAR COLLATE "C" NOT NULL, \n'
            "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BYTEA NOT NULL, \n"
            "\trun_id UUID NOT NULL, \n"
            '\tdomain VARCHAR COLLATE "C" NOT NULL, \n'
            '\tsource VARCHAR COLLATE "C" NOT NULL, \n'
            "\toccurred_at TIMESTAMP WITH TIME ZONE, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT domain_closed CHECK (domain IN ('evaluation', 'agent_under"
            "_test')), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('provider_reported', 'tele"
            "metry', 'measured')), \n"
            "\tCONSTRAINT fk_usage_record_run_id FOREIGN KEY(run_id) REFERENCES run "
            "(id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK (substr(id::text, 15, 1) = '7'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (octet_length(doc_sha256) = 32"
            "), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK (substr(run_id::text, 15, 1) = '7')\n"
            ")"
        ),
        (
            "CREATE TABLE run_bundle_member (\n"
            "\tbundle_id UUID NOT NULL, \n"
            '\tkind VARCHAR COLLATE "C" NOT NULL, \n'
            "\tordinal BIGINT NOT NULL, \n"
            "\trecord_id UUID NOT NULL, \n"
            "\trecord_version BIGINT, \n"
            "\tPRIMARY KEY (bundle_id, kind, ordinal), \n"
            "\tCONSTRAINT fk_run_bundle_member_bundle_id FOREIGN KEY(bundle_id) REFE"
            "RENCES run_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('run', 'snapshot', 'pricing_ca"
            "talog', 'test_profile', 'artifact', 'transcript', 'turn', 'exchange', "
            "'tool_call', 'span', 'result', 'verdict', 'usage_record', 'cost_record"
            "', 'cost_summary')), \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT record_version_for_versioned_kinds CHECK (kind IN ('pricin"
            "g_catalog', 'test_profile') AND record_version IS NOT NULL OR (kind NO"
            "T IN ('pricing_catalog', 'test_profile')) AND record_version IS NULL),"
            " \n"
            "\tCONSTRAINT bundle_id_uuid_v7 CHECK (substr(bundle_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT record_id_uuid_v7 CHECK (substr(record_id::text, 15, 1) = "
            "'7')\n"
            ")"
        ),
        ("CREATE INDEX ix_run_bundle_member_kind_record_id ON run_bundle_member (kind, record_id)"),
        (
            "CREATE TABLE run_bundle_withheld (\n"
            "\tbundle_id UUID NOT NULL, \n"
            "\tordinal BIGINT NOT NULL, \n"
            '\tkind VARCHAR COLLATE "C" NOT NULL, \n'
            "\trecord_id UUID NOT NULL, \n"
            "\tPRIMARY KEY (bundle_id, ordinal), \n"
            "\tCONSTRAINT fk_run_bundle_withheld_bundle_id FOREIGN KEY(bundle_id) RE"
            "FERENCES run_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('artifact', 'transcript', 'tur"
            "n', 'exchange', 'tool_call', 'span', 'result', 'usage_record', 'cost_r"
            "ecord')), \n"
            "\tCONSTRAINT bundle_id_uuid_v7 CHECK (substr(bundle_id::text, 15, 1) = "
            "'7'), \n"
            "\tCONSTRAINT record_id_uuid_v7 CHECK (substr(record_id::text, 15, 1) = "
            "'7')\n"
            ")"
        ),
    ),
    "sqlite": (
        (
            "CREATE TABLE agent_target (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            '\ttransport TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT transport_closed CHECK (transport IN ('ws', 'webrtc', 'sip"
            "')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE config_snapshot (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tevaluator_id BLOB NOT NULL, \n"
            "\tevaluator_version INTEGER NOT NULL, \n"
            "\trubric_id BLOB NOT NULL, \n"
            "\trubric_version INTEGER NOT NULL, \n"
            "\tpricing_catalog_id BLOB, \n"
            "\tpricing_catalog_version INTEGER, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT catalog_pair CHECK (pricing_catalog_id IS NULL AND pricing"
            "_catalog_version IS NULL OR pricing_catalog_id IS NOT NULL AND pricing"
            "_catalog_version IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32), \n"
            "\tCONSTRAINT evaluator_id_uuid_v7 CHECK ((evaluator_id IS NULL OR (leng"
            "th(evaluator_id) = 16 AND substr(hex(evaluator_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT rubric_id_uuid_v7 CHECK ((rubric_id IS NULL OR (length(rub"
            "ric_id) = 16 AND substr(hex(rubric_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT pricing_catalog_id_uuid_v7 CHECK ((pricing_catalog_id IS N"
            "ULL OR (length(pricing_catalog_id) = 16 AND substr(hex(pricing_catalog"
            "_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE dataset (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE dataset_item (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\tartifact_id BLOB, \n"
            "\ttranscript_id BLOB, \n"
            "\tsource_run_id BLOB, \n"
            '\tlanguage TEXT COLLATE "BINARY" NOT NULL, \n'
            "\thuman_subject INTEGER, \n"
            '\thuman_subject_reason TEXT COLLATE "BINARY", \n'
            '\tconsent_status TEXT COLLATE "BINARY", \n'
            "\tretain_until TEXT, \n"
            "\tdelete_on_expiry INTEGER, \n"
            '\tpolicy_ref TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS 1 AND consent_status IS NOT NULL AND delete_on_expiry IS NOT NU"
            "LL OR human_subject IS 0 AND human_subject_reason IS NULL AND consent_"
            "status IS NULL AND retain_until IS NULL AND delete_on_expiry IS NULL A"
            "ND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT artifact_id_uuid_v7 CHECK ((artifact_id IS NULL OR (length"
            "(artifact_id) = 16 AND substr(hex(artifact_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT transcript_id_uuid_v7 CHECK ((transcript_id IS NULL OR (le"
            "ngth(transcript_id) = 16 AND substr(hex(transcript_id), 13, 1) = '7'))"
            "), \n"
            "\tCONSTRAINT source_run_id_uuid_v7 CHECK ((source_run_id IS NULL OR (le"
            "ngth(source_run_id) = 16 AND substr(hex(source_run_id), 13, 1) = '7'))"
            "), \n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (0, 1)), \n"
            "\tCONSTRAINT retain_until_instant_shape CHECK (length(retain_until) = 2"
            "7 AND retain_until GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0"
            "-2][0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (0, 1))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_dataset_item_retain_until ON dataset_item (retain_unti"
            "l) WHERE retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE deletion_audit (\n"
            "\taudit_uuid BLOB NOT NULL, \n"
            "\tdeleted_at TEXT NOT NULL, \n"
            '\tactor TEXT COLLATE "BINARY" NOT NULL, \n'
            '\treason TEXT COLLATE "BINARY" NOT NULL, \n'
            '\troot_kind TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcounts TEXT NOT NULL, \n"
            "\tPRIMARY KEY (audit_uuid), \n"
            "\tCONSTRAINT reason_closed CHECK (reason IN ('operator_request', 'reten"
            "tion_policy', 'data_subject_request')), \n"
            "\tCONSTRAINT root_kind_closed CHECK (root_kind IN ('run', 'project', 't"
            "est_profile', 'agent_target', 'persona', 'evaluator', 'metric_definiti"
            "on', 'metric_binding', 'rubric', 'pricing_catalog', 'dataset', 'datase"
            "t_item', 'export_bundle', 'config_snapshot')), \n"
            "\tCONSTRAINT audit_uuid_uuid_v7 CHECK ((audit_uuid IS NULL OR (length(a"
            "udit_uuid) = 16 AND substr(hex(audit_uuid), 13, 1) = '7'))), \n"
            "\tCONSTRAINT deleted_at_instant_shape CHECK (length(deleted_at) = 27 AN"
            "D deleted_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z')\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE evaluator (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tpersona_id BLOB NOT NULL, \n"
            "\tpersona_version INTEGER NOT NULL, \n"
            "\ttest_profile_id BLOB, \n"
            "\ttest_profile_version INTEGER, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT profile_pair CHECK (test_profile_id IS NULL AND test_profi"
            "le_version IS NULL OR test_profile_id IS NOT NULL AND test_profile_ver"
            "sion IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32), \n"
            "\tCONSTRAINT persona_id_uuid_v7 CHECK ((persona_id IS NULL OR (length(p"
            "ersona_id) = 16 AND substr(hex(persona_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT test_profile_id_uuid_v7 CHECK ((test_profile_id IS NULL OR"
            " (length(test_profile_id) = 16 AND substr(hex(test_profile_id), 13, 1)"
            " = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE export_bundle (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tpurpose TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tbyte_length INTEGER NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT purpose_closed CHECK (purpose IN ('internal', 'shared_expo"
            "rt', 'benchmark')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE metric_binding (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tevaluator_id BLOB NOT NULL, \n"
            "\tmetric_id BLOB NOT NULL, \n"
            "\tmetric_version INTEGER NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32), \n"
            "\tCONSTRAINT evaluator_id_uuid_v7 CHECK ((evaluator_id IS NULL OR (leng"
            "th(evaluator_id) = 16 AND substr(hex(evaluator_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT metric_id_uuid_v7 CHECK ((metric_id IS NULL OR (length(met"
            "ric_id) = 16 AND substr(hex(metric_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE metric_definition (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            '\t"key" TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tkind TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tvalue_type TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('deterministic', 'llm_judge', "
            "'audio', 'latency')), \n"
            "\tCONSTRAINT value_type_closed CHECK (value_type IN ('boolean', 'rating"
            "', 'enum', 'numeric')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE persona (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            '\tlanguage TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE pricing_catalog (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE project (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tname TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE rubric (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE test_profile (\n"
            "\tid BLOB NOT NULL, \n"
            "\tversion INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            '\tcontent_digest_scheme TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcontent_digest_value BLOB NOT NULL, \n"
            "\tPRIMARY KEY (id, version), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT version_ge_1 CHECK (version >= 1), \n"
            "\tCONSTRAINT content_digest_scheme_closed CHECK (content_digest_scheme "
            "IN ('sha256-jcs/v1', 'hmac-sha256-jcs/v1')), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT content_digest_value_sha256_len CHECK (length(content_dige"
            "st_value) = 32)\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE tracer_meta (\n"
            "\tsingleton INTEGER NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tevaldb_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (singleton), \n"
            "\tCONSTRAINT singleton_is_1 CHECK (singleton = 1), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1')\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE dataset_member (\n"
            "\tdataset_id BLOB NOT NULL, \n"
            "\tdataset_version INTEGER NOT NULL, \n"
            "\tordinal INTEGER NOT NULL, \n"
            "\titem_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (dataset_id, dataset_version, ordinal), \n"
            "\tCONSTRAINT fk_dataset_member_dataset_id_dataset_version FOREIGN KEY(d"
            "ataset_id, dataset_version) REFERENCES dataset (id, version) ON DELETE"
            " CASCADE, \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT dataset_id_uuid_v7 CHECK ((dataset_id IS NULL OR (length(d"
            "ataset_id) = 16 AND substr(hex(dataset_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT item_id_uuid_v7 CHECK ((item_id IS NULL OR (length(item_id"
            ") = 16 AND substr(hex(item_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        ("CREATE INDEX ix_dataset_member_item_id ON dataset_member (item_id)"),
        (
            "CREATE TABLE export_bundle_item (\n"
            "\texport_id BLOB NOT NULL, \n"
            "\titem_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (export_id, item_id), \n"
            "\tCONSTRAINT fk_export_bundle_item_export_id FOREIGN KEY(export_id) REF"
            "ERENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK ((export_id IS NULL OR (length(exp"
            "ort_id) = 16 AND substr(hex(export_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT item_id_uuid_v7 CHECK ((item_id IS NULL OR (length(item_id"
            ") = 16 AND substr(hex(item_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        ("CREATE INDEX ix_export_bundle_item_item_id ON export_bundle_item (item_id)"),
        (
            "CREATE TABLE export_bundle_profile (\n"
            "\texport_id BLOB NOT NULL, \n"
            "\tprofile_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (export_id, profile_id), \n"
            "\tCONSTRAINT fk_export_bundle_profile_export_id FOREIGN KEY(export_id) "
            "REFERENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK ((export_id IS NULL OR (length(exp"
            "ort_id) = 16 AND substr(hex(export_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT profile_id_uuid_v7 CHECK ((profile_id IS NULL OR (length(p"
            "rofile_id) = 16 AND substr(hex(profile_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        ("CREATE INDEX ix_export_bundle_profile_profile_id ON export_bundle_profile (profile_id)"),
        (
            "CREATE TABLE export_bundle_run (\n"
            "\texport_id BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (export_id, run_id), \n"
            "\tCONSTRAINT fk_export_bundle_run_export_id FOREIGN KEY(export_id) REFE"
            "RENCES export_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT export_id_uuid_v7 CHECK ((export_id IS NULL OR (length(exp"
            "ort_id) = 16 AND substr(hex(export_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        ("CREATE INDEX ix_export_bundle_run_run_id ON export_bundle_run (run_id)"),
        (
            "CREATE TABLE run (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\tproject_id BLOB NOT NULL, \n"
            "\tconfig_snapshot_id BLOB NOT NULL, \n"
            "\tagent_target_id BLOB NOT NULL, \n"
            "\tagent_target_version INTEGER NOT NULL, \n"
            '\tmode TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tcaller_origin TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tobserved_tier TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tstarted_at TEXT, \n"
            "\tcaller_human_subject INTEGER, \n"
            '\tcaller_human_subject_reason TEXT COLLATE "BINARY", \n'
            '\tcaller_consent_status TEXT COLLATE "BINARY", \n'
            "\tcaller_retain_until TEXT, \n"
            "\tcaller_delete_on_expiry INTEGER, \n"
            '\tcaller_policy_ref TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT mode_closed CHECK (mode IN ('simulated', 'recorded')), \n"
            "\tCONSTRAINT caller_origin_closed CHECK (caller_origin IN ('simulated',"
            " 'human')), \n"
            "\tCONSTRAINT observed_tier_closed CHECK (observed_tier IN ('T0', 'T1', "
            "'T2')), \n"
            "\tCONSTRAINT caller_human_subject_reason_closed CHECK (caller_human_sub"
            "ject_reason IN ('marked_before_role_evidence', 'real_person_likeness',"
            " 'manual')), \n"
            "\tCONSTRAINT caller_consent_status_closed CHECK (caller_consent_status "
            "IN ('obtained', 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_run_project_id FOREIGN KEY(project_id) REFERENCES proje"
            "ct (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT fk_run_config_snapshot_id FOREIGN KEY(config_snapshot_id) "
            "REFERENCES config_snapshot (id) ON DELETE RESTRICT, \n"
            "\tCONSTRAINT caller_privacy_projection CHECK (caller_human_subject IS N"
            "ULL AND caller_human_subject_reason IS NULL AND caller_consent_status "
            "IS NULL AND caller_retain_until IS NULL AND caller_delete_on_expiry IS"
            " NULL AND caller_policy_ref IS NULL OR caller_human_subject IS 1 AND c"
            "aller_consent_status IS NOT NULL AND caller_delete_on_expiry IS NOT NU"
            "LL OR caller_human_subject IS 0 AND caller_human_subject_reason IS NUL"
            "L AND caller_consent_status IS NULL AND caller_retain_until IS NULL AN"
            "D caller_delete_on_expiry IS NULL AND caller_policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT project_id_uuid_v7 CHECK ((project_id IS NULL OR (length(p"
            "roject_id) = 16 AND substr(hex(project_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT config_snapshot_id_uuid_v7 CHECK ((config_snapshot_id IS N"
            "ULL OR (length(config_snapshot_id) = 16 AND substr(hex(config_snapshot"
            "_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT agent_target_id_uuid_v7 CHECK ((agent_target_id IS NULL OR"
            " (length(agent_target_id) = 16 AND substr(hex(agent_target_id), 13, 1)"
            " = '7'))), \n"
            "\tCONSTRAINT started_at_instant_shape CHECK (length(started_at) = 27 AN"
            "D started_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT caller_human_subject_bool CHECK (caller_human_subject IN ("
            "0, 1)), \n"
            "\tCONSTRAINT caller_retain_until_instant_shape CHECK (length(caller_ret"
            "ain_until) = 27 AND caller_retain_until GLOB '[0-9][0-9][0-9][0-9]-[0-"
            "1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-"
            "9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT caller_delete_on_expiry_bool CHECK (caller_delete_on_expir"
            "y IN (0, 1))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_run_caller_retain_until ON run (caller_retain_until) W"
            "HERE caller_retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE snapshot_member (\n"
            "\tsnapshot_id BLOB NOT NULL, \n"
            '\tkind TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tordinal INTEGER NOT NULL, \n"
            "\tdef_id BLOB NOT NULL, \n"
            "\tdef_version INTEGER NOT NULL, \n"
            "\tPRIMARY KEY (snapshot_id, kind, ordinal), \n"
            "\tCONSTRAINT fk_snapshot_member_snapshot_id FOREIGN KEY(snapshot_id) RE"
            "FERENCES config_snapshot (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('evaluator', 'persona', 'rubri"
            "c', 'metric_definition', 'metric_binding')), \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT def_version_ge_1 CHECK (def_version >= 1), \n"
            "\tCONSTRAINT snapshot_id_uuid_v7 CHECK ((snapshot_id IS NULL OR (length"
            "(snapshot_id) = 16 AND substr(hex(snapshot_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT def_id_uuid_v7 CHECK ((def_id IS NULL OR (length(def_id) ="
            " 16 AND substr(hex(def_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_snapshot_member_kind_def_id_def_version ON snapshot_me"
            "mber (kind, def_id, def_version)"
        ),
        (
            "CREATE TABLE snapshot_profile_ref (\n"
            "\tsnapshot_id BLOB NOT NULL, \n"
            "\tprofile_id BLOB NOT NULL, \n"
            "\tprofile_version INTEGER NOT NULL, \n"
            "\tPRIMARY KEY (snapshot_id), \n"
            "\tCONSTRAINT fk_snapshot_profile_ref_snapshot_id FOREIGN KEY(snapshot_i"
            "d) REFERENCES config_snapshot (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT profile_version_ge_1 CHECK (profile_version >= 1), \n"
            "\tCONSTRAINT snapshot_id_uuid_v7 CHECK ((snapshot_id IS NULL OR (length"
            "(snapshot_id) = 16 AND substr(hex(snapshot_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT profile_id_uuid_v7 CHECK ((profile_id IS NULL OR (length(p"
            "rofile_id) = 16 AND substr(hex(profile_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_snapshot_profile_ref_profile_id_profile_version ON sna"
            "pshot_profile_ref (profile_id, profile_version)"
        ),
        (
            "CREATE TABLE artifact (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\tsha256 BLOB NOT NULL, \n"
            "\tturn_ref BLOB, \n"
            "\thuman_subject INTEGER NOT NULL, \n"
            '\thuman_subject_reason TEXT COLLATE "BINARY", \n'
            '\tconsent_status TEXT COLLATE "BINARY", \n'
            "\tretain_until TEXT, \n"
            "\tdelete_on_expiry INTEGER, \n"
            '\tpolicy_ref TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_artifact_run_id FOREIGN KEY(run_id) REFERENCES run (id)"
            " ON DELETE CASCADE, \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS 1 AND consent_status IS NOT NULL AND delete_on_expiry IS NOT NU"
            "LL OR human_subject IS 0 AND human_subject_reason IS NULL AND consent_"
            "status IS NULL AND retain_until IS NULL AND delete_on_expiry IS NULL A"
            "ND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT sha256_sha256_len CHECK (length(sha256) = 32), \n"
            "\tCONSTRAINT turn_ref_uuid_v7 CHECK ((turn_ref IS NULL OR (length(turn_"
            "ref) = 16 AND substr(hex(turn_ref), 13, 1) = '7'))), \n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (0, 1)), \n"
            "\tCONSTRAINT retain_until_instant_shape CHECK (length(retain_until) = 2"
            "7 AND retain_until GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0"
            "-2][0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (0, 1))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_artifact_retain_until ON artifact (retain_until) WHERE"
            " retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE cost_record (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tstatus TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tbasis TEXT COLLATE "BINARY", \n'
            "\tamount TEXT, \n"
            '\tcurrency TEXT COLLATE "BINARY", \n'
            '\tna_reason TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('available', 'not_availabl"
            "e')), \n"
            "\tCONSTRAINT basis_closed CHECK (basis IN ('reported', 'computed')), \n"
            "\tCONSTRAINT currency_closed CHECK (currency IN ('USD')), \n"
            "\tCONSTRAINT na_reason_closed CHECK (na_reason IN ('no_price_for_model'"
            ", 'unit_unpriced', 'no_usage', 'ambiguous_price', 'billing_rules_missi"
            "ng', 'usage_not_reported', 'unsupported_currency', 'currency_not_repor"
            "ted', 'amount_unparseable', 'currency_conflict')), \n"
            "\tCONSTRAINT fk_cost_record_run_id FOREIGN KEY(run_id) REFERENCES run ("
            "id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT amount_non_negative CHECK (amount NOT GLOB '-*'), \n"
            "\tCONSTRAINT not_available_is_empty CHECK (status != 'not_available' OR"
            " amount IS NULL AND basis IS NULL AND currency IS NULL), \n"
            "\tCONSTRAINT currency_with_amount CHECK (amount IS NULL AND currency IS"
            " NULL OR amount IS NOT NULL AND currency IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT amount_decimal_shape CHECK ((amount GLOB '[0-9]*' OR amoun"
            "t GLOB '-[0-9]*') AND amount GLOB '*[0-9]' AND amount NOT GLOB '*[^0-9"
            ".-]*' AND amount NOT GLOB '?*-*' AND amount NOT GLOB '*.*.*')\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE cost_summary (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tcompleteness TEXT COLLATE "BINARY" NOT NULL, \n'
            "\ttotal TEXT, \n"
            '\tcurrency TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT completeness_closed CHECK (completeness IN ('complete', 'p"
            "artial', 'none')), \n"
            "\tCONSTRAINT currency_closed CHECK (currency IN ('USD')), \n"
            "\tCONSTRAINT fk_cost_summary_run_id FOREIGN KEY(run_id) REFERENCES run "
            "(id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT total_non_negative CHECK (total NOT GLOB '-*'), \n"
            "\tCONSTRAINT currency_with_total CHECK (total IS NULL AND currency IS N"
            "ULL OR total IS NOT NULL AND currency IS NOT NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT total_decimal_shape CHECK ((total GLOB '[0-9]*' OR total G"
            "LOB '-[0-9]*') AND total GLOB '*[0-9]' AND total NOT GLOB '*[^0-9.-]*'"
            " AND total NOT GLOB '?*-*' AND total NOT GLOB '*.*.*')\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE exchange (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\t"index" INTEGER NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT fk_exchange_run_id FOREIGN KEY(run_id) REFERENCES run (id)"
            " ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE external_ref_index (\n"
            '\tentity_kind TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tentity_id BLOB NOT NULL, \n"
            "\tkey_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (entity_kind, entity_id, key_sha256), \n"
            "\tCONSTRAINT fk_external_ref_index_run_id FOREIGN KEY(run_id) REFERENCE"
            "S run (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT entity_kind_closed CHECK (entity_kind IN ('run', 'span', '"
            "transcript', 'usage_record')), \n"
            "\tCONSTRAINT entity_id_uuid_v7 CHECK ((entity_id IS NULL OR (length(ent"
            "ity_id) = 16 AND substr(hex(entity_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT key_sha256_sha256_len CHECK (length(key_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_external_ref_index_run_id_entity_kind_key_sha256 ON ex"
            "ternal_ref_index (run_id, entity_kind, key_sha256)"
        ),
        (
            "CREATE UNIQUE INDEX ux_external_ref_index_run_key ON external_ref_inde"
            "x (entity_kind, key_sha256) WHERE entity_kind = 'run'"
        ),
        (
            "CREATE TABLE metric_result (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\tmetric_id BLOB NOT NULL, \n"
            "\tmetric_version INTEGER NOT NULL, \n"
            '\tstatus TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tna_reason TEXT COLLATE "BINARY", \n'
            "\tpassed INTEGER, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('scored', 'not_applicable'"
            ", 'error')), \n"
            "\tCONSTRAINT na_reason_closed CHECK (na_reason IN ('missing_input', 'ag"
            "ent_channel_unknown', 'language_not_observed', 'unsupported_language',"
            " 'unsupported_script')), \n"
            "\tCONSTRAINT fk_metric_result_run_id FOREIGN KEY(run_id) REFERENCES run"
            " (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT na_has_no_passed CHECK (status != 'not_applicable' OR pass"
            "ed IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT metric_id_uuid_v7 CHECK ((metric_id IS NULL OR (length(met"
            "ric_id) = 16 AND substr(hex(metric_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT passed_bool CHECK (passed IN (0, 1))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE run_bundle (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT fk_run_bundle_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE run_verdict (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tstatus TEXT COLLATE "BINARY" NOT NULL, \n'
            "\trequired_total INTEGER NOT NULL, \n"
            "\tpassed INTEGER NOT NULL, \n"
            "\tfailed INTEGER NOT NULL, \n"
            "\tnot_applicable INTEGER NOT NULL, \n"
            "\terrored INTEGER NOT NULL, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT status_closed CHECK (status IN ('pass', 'fail', 'inconclus"
            "ive')), \n"
            "\tCONSTRAINT fk_run_verdict_run_id FOREIGN KEY(run_id) REFERENCES run ("
            "id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE stage_span (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tstage TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT stage_closed CHECK (stage IN ('stt', 'llm', 'tts', 'judge'"
            ", 'sim_caller_llm', 'sim_caller_tts', 'realtime', 'other')), \n"
            "\tCONSTRAINT fk_stage_span_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE tool_call_event (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tsource TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('agent_export', 'agent_tel"
            "emetry', 'observer')), \n"
            "\tCONSTRAINT fk_tool_call_event_run_id FOREIGN KEY(run_id) REFERENCES r"
            "un (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE transcript (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tsource TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tdetected_language TEXT COLLATE "BINARY", \n'
            "\thuman_subject INTEGER NOT NULL, \n"
            '\thuman_subject_reason TEXT COLLATE "BINARY", \n'
            '\tconsent_status TEXT COLLATE "BINARY", \n'
            "\tretain_until TEXT, \n"
            "\tdelete_on_expiry INTEGER, \n"
            '\tpolicy_ref TEXT COLLATE "BINARY", \n'
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('our_stt', 'agent_export',"
            " 'agent_telemetry', 'observer', 'script', 'human_reference')), \n"
            "\tCONSTRAINT human_subject_reason_closed CHECK (human_subject_reason IN"
            " ('marked_before_role_evidence', 'real_person_likeness', 'manual')), \n"
            "\tCONSTRAINT consent_status_closed CHECK (consent_status IN ('obtained'"
            ", 'not_obtained', 'unknown')), \n"
            "\tCONSTRAINT fk_transcript_run_id FOREIGN KEY(run_id) REFERENCES run (i"
            "d) ON DELETE CASCADE, \n"
            "\tCONSTRAINT privacy_projection CHECK (human_subject IS NULL AND human_"
            "subject_reason IS NULL AND consent_status IS NULL AND retain_until IS "
            "NULL AND delete_on_expiry IS NULL AND policy_ref IS NULL OR human_subj"
            "ect IS 1 AND consent_status IS NOT NULL AND delete_on_expiry IS NOT NU"
            "LL OR human_subject IS 0 AND human_subject_reason IS NULL AND consent_"
            "status IS NULL AND retain_until IS NULL AND delete_on_expiry IS NULL A"
            "ND policy_ref IS NULL), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT human_subject_bool CHECK (human_subject IN (0, 1)), \n"
            "\tCONSTRAINT retain_until_instant_shape CHECK (length(retain_until) = 2"
            "7 AND retain_until GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0"
            "-2][0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT delete_on_expiry_bool CHECK (delete_on_expiry IN (0, 1))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE INDEX ix_transcript_retain_until ON transcript (retain_until) W"
            "HERE retain_until IS NOT NULL"
        ),
        (
            "CREATE TABLE turn (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            "\ttranscript_id BLOB NOT NULL, \n"
            "\tseq INTEGER NOT NULL, \n"
            '\trole TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tstart_ms INTEGER, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT role_closed CHECK (role IN ('caller', 'agent', 'unknown'))"
            ", \n"
            "\tCONSTRAINT fk_turn_run_id FOREIGN KEY(run_id) REFERENCES run (id) ON "
            "DELETE CASCADE, \n"
            "\tCONSTRAINT uq_turn_transcript_seq UNIQUE (transcript_id, seq), \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT transcript_id_uuid_v7 CHECK ((transcript_id IS NULL OR (le"
            "ngth(transcript_id) = 16 AND substr(hex(transcript_id), 13, 1) = '7'))"
            ")\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE usage_record (\n"
            "\tid BLOB NOT NULL, \n"
            '\tschema_version TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tcreated_at TEXT NOT NULL, \n"
            "\tdoc TEXT NOT NULL, \n"
            "\tdoc_sha256 BLOB NOT NULL, \n"
            "\trun_id BLOB NOT NULL, \n"
            '\tdomain TEXT COLLATE "BINARY" NOT NULL, \n'
            '\tsource TEXT COLLATE "BINARY" NOT NULL, \n'
            "\toccurred_at TEXT, \n"
            "\tPRIMARY KEY (id), \n"
            "\tCONSTRAINT schema_version_pinned CHECK (schema_version = '0.0.1'), \n"
            "\tCONSTRAINT domain_closed CHECK (domain IN ('evaluation', 'agent_under"
            "_test')), \n"
            "\tCONSTRAINT source_closed CHECK (source IN ('provider_reported', 'tele"
            "metry', 'measured')), \n"
            "\tCONSTRAINT fk_usage_record_run_id FOREIGN KEY(run_id) REFERENCES run "
            "(id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT id_uuid_v7 CHECK ((id IS NULL OR (length(id) = 16 AND subs"
            "tr(hex(id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT created_at_instant_shape CHECK (length(created_at) = 27 AN"
            "D created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-"
            "9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'), \n"
            "\tCONSTRAINT doc_sha256_sha256_len CHECK (length(doc_sha256) = 32), \n"
            "\tCONSTRAINT run_id_uuid_v7 CHECK ((run_id IS NULL OR (length(run_id) ="
            " 16 AND substr(hex(run_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT occurred_at_instant_shape CHECK (length(occurred_at) = 27 "
            "AND occurred_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2]"
            "[0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z')\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        (
            "CREATE TABLE run_bundle_member (\n"
            "\tbundle_id BLOB NOT NULL, \n"
            '\tkind TEXT COLLATE "BINARY" NOT NULL, \n'
            "\tordinal INTEGER NOT NULL, \n"
            "\trecord_id BLOB NOT NULL, \n"
            "\trecord_version INTEGER, \n"
            "\tPRIMARY KEY (bundle_id, kind, ordinal), \n"
            "\tCONSTRAINT fk_run_bundle_member_bundle_id FOREIGN KEY(bundle_id) REFE"
            "RENCES run_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('run', 'snapshot', 'pricing_ca"
            "talog', 'test_profile', 'artifact', 'transcript', 'turn', 'exchange', "
            "'tool_call', 'span', 'result', 'verdict', 'usage_record', 'cost_record"
            "', 'cost_summary')), \n"
            "\tCONSTRAINT ordinal_ge_0 CHECK (ordinal >= 0), \n"
            "\tCONSTRAINT record_version_for_versioned_kinds CHECK (kind IN ('pricin"
            "g_catalog', 'test_profile') AND record_version IS NOT NULL OR (kind NO"
            "T IN ('pricing_catalog', 'test_profile')) AND record_version IS NULL),"
            " \n"
            "\tCONSTRAINT bundle_id_uuid_v7 CHECK ((bundle_id IS NULL OR (length(bun"
            "dle_id) = 16 AND substr(hex(bundle_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT record_id_uuid_v7 CHECK ((record_id IS NULL OR (length(rec"
            "ord_id) = 16 AND substr(hex(record_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
        ("CREATE INDEX ix_run_bundle_member_kind_record_id ON run_bundle_member (kind, record_id)"),
        (
            "CREATE TABLE run_bundle_withheld (\n"
            "\tbundle_id BLOB NOT NULL, \n"
            "\tordinal INTEGER NOT NULL, \n"
            '\tkind TEXT COLLATE "BINARY" NOT NULL, \n'
            "\trecord_id BLOB NOT NULL, \n"
            "\tPRIMARY KEY (bundle_id, ordinal), \n"
            "\tCONSTRAINT fk_run_bundle_withheld_bundle_id FOREIGN KEY(bundle_id) RE"
            "FERENCES run_bundle (id) ON DELETE CASCADE, \n"
            "\tCONSTRAINT kind_closed CHECK (kind IN ('artifact', 'transcript', 'tur"
            "n', 'exchange', 'tool_call', 'span', 'result', 'usage_record', 'cost_r"
            "ecord')), \n"
            "\tCONSTRAINT bundle_id_uuid_v7 CHECK ((bundle_id IS NULL OR (length(bun"
            "dle_id) = 16 AND substr(hex(bundle_id), 13, 1) = '7'))), \n"
            "\tCONSTRAINT record_id_uuid_v7 CHECK ((record_id IS NULL OR (length(rec"
            "ord_id) = 16 AND substr(hex(record_id), 13, 1) = '7')))\n"
            ")\n"
            " WITHOUT ROWID,\n"
            " STRICT"
        ),
    ),
}


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for statement in triggers.function_statements():
            op.execute(statement)
    for statement in DDL[dialect]:
        op.execute(statement)
    for table in TABLES:
        for statement in triggers.create_statements(table, dialect):
            op.execute(statement)
    op.execute(
        sa.text(
            "INSERT INTO tracer_meta (singleton, schema_version, evaldb_version) "
            "VALUES (1, '0.0.1', :evaldb_version)"
        ).bindparams(evaldb_version=version("tracer-evaldb"))
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE "{table}"')
    if dialect == "postgresql":
        op.execute(f"DROP FUNCTION {triggers.UPDATE_TRIGGER_FUNCTION}()")
        op.execute(f"DROP FUNCTION {triggers.DELETE_TRIGGER_FUNCTION}()")
