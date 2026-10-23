# Privacy markers, consent and sealing

Recordings and transcripts of real calls contain real people. The schema records who may be in
the data, what consent exists, and what protection an export needs. Decisions:
[ADR 0016](../adr/0016-roles-caller-origin-privacy.md),
[ADR 0006](../adr/0006-snapshot-pii-and-content-digest.md),
[ADR 0015](../adr/0015-sealed-payload.md). Rules in full: `SCHEMA_POLICY.md` sections 6 to 9.

## Who is in the data

- Roles are exactly `caller`, `agent` and `unknown`. Only the AI agent is evaluated; the caller is
  a human or a simulator (`Run.caller_origin`, required, no default). There is no IVR role and no
  hand-off to a human agent.
- `privacy.derive_human_subject(run, turns)` is **true** when the caller is human, when any turn
  or channel role is `unknown`, or when the run has no role evidence at all. It is false only for
  a simulated run whose roles show just the caller and the agent. Missing evidence never becomes
  a claim that no person is present.

## The marker

Every `Artifact`, `Transcript` and recording- or transcript-based `DatasetItem` carries a
`PrivacyMarker`. When `human_subject` is true it also carries a consent record (`obtained`,
`not_obtained` or `unknown`) and a retention policy; when false it carries neither.

- **Stricter, never looser.** A stored marker may say true where the derivation says false, with
  a reason: `marked_before_role_evidence` (written before roles were known), `real_person_likeness`
  (for example a simulated voice cloned from a real person) or `manual`. A stored false where the
  derivation says true is rejected; the fix is a new record, never an edit.
- **Re-derived at export time (P-57).** A marker set only as a precaution
  (`marked_before_role_evidence`) is re-derived from the role evidence in the exported bundle by
  `privacy.export_human_subject(marker, run, turns)`. The stored record is not changed.

## Export rules

| Purpose | Human-subject payloads | Consent |
| --- | --- | --- |
| `internal` | allowed in the clear | not checked |
| `shared_export` | each payload sealed or redacted (audio, text, tool arguments, tool results separately) | any status |
| `benchmark` | sealed or redacted | `obtained` required; `not_obtained` and `unknown` block |

In every purpose the run's caller identifier is refused. In `shared_export` and `benchmark`,
profile digests are dropped or keyed, profiles travel only sealed, and local `file:` or loopback
URIs are refused.

## Sealing

A `SealedPayload` (format `tracer.sealed.v1`) encrypts one payload (a test profile, the audio of
an artifact, the text of a transcript, tool arguments or a tool result) with AES-256-GCM under a
fresh data key that is wrapped by a key-encryption key named in a `KeyRef`. The authenticated
data binds the ciphertext to its subject. A digest of the plaintext is absent or keyed, never
plain. When an exporter seals an artifact's audio it must replace the artifact's `uri` with the
location of the sealed copy; the schema cannot check this. The package holds no cryptography;
the reference implementation is test-only.

## Profiles and digests

Test profiles hold personal data and are never embedded in a snapshot; the snapshot holds a
reference with a digest. Internal data uses a plain SHA-256 digest, which can be brute-forced for
a low-entropy profile by anyone holding the internal record; shared exports drop or key it.
Profile values in evaluator text are `{{profile.<field>}}` placeholders, and a lint warns about
literal profile values, within the limits `SCHEMA_POLICY.md` section 7 states.

## Tests

`test_human_subject_defaults_true_when_unknown`, `test_human_subject_looser_marker_rejected`,
`test_human_subject_stricter_marker_requires_reason`, `test_precautionary_marker_uses_derivation_at_export`,
`test_export_rederivation_leaves_stored_marker_unchanged`, `test_shared_export_human_requires_seal_or_redaction`,
`test_benchmark_rejects_not_obtained_consent`, `test_benchmark_rejects_unknown_consent`,
`test_shared_export_allows_not_obtained_consent_when_sealed_or_redacted`,
`test_shared_export_drops_or_keys_profile_digest`, `test_sealed_payload_plaintext_digest_absent_or_keyed_all_types`,
`test_sealed_audio_protects_the_recording`, `test_policy_documents_low_entropy_digest_limitation`,
`test_policy_documents_free_text_lint_limitation`.
