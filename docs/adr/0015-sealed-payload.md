# ADR 0015: SealedPayload

- Status: Accepted (user decision Q7; revision 3 user decision 2: the plaintext digest of a sealed profile is dropped or keyed). P-42 and P-18 decided by the user in revision 5. Revision 7: `aad_fields.subject_id` is a typed ID (ADR 0001 I12); `aad_fields.schema_version` is one of the four allowed reference fields of ADR 0001 Decision 8. Open [Proposed] items: none.
- Date: 2026-10-22 (revision 5: drop-or-key applies to every payload type (P-42); keyed digests name their key in `key_ref` (P-18))
- Task: P1-00b (implemented by P1-26; production seal/unseal is out of Phase 1)

## Context
Snapshots never contain PII (ADR 0006). Some workflows still need to move sensitive content between parties: re-running a verification-flow evaluator elsewhere, or sharing human-subject recordings or transcripts with a research partner (ADR 0016). That content must be confidential, bound to the entity it belongs to (so a ciphertext cannot be moved to another profile), and portable across languages, without the schema ever holding key material. A digest stored in clear next to the ciphertext must not undo the confidentiality.

## Decision
1. **`SealedPayload`** (value object): `{format: "tracer.sealed.v1", alg: "A256GCM", dek_wrap{alg: "A256KW", kek_ref: KeyRef, wrapped_dek}, nonce, ciphertext, aad_fields{payload_type, subject_id, subject_version, schema_version}, plaintext_digest: ContentDigest?}`.
2. **Envelope encryption.** A fresh 256-bit data key (DEK) for every payload, used with AES-256-GCM. The DEK is wrapped with AES Key Wrap (RFC 3394, A256KW) under a key-encryption key (KEK) named by `KeyRef{key_id, key_version?, provider_hint?}`. KeyRef never carries key material (ADR 0007).
3. **Nonce:** exactly 12 bytes. With one DEK per payload, a random nonce cannot repeat under the same key.
4. **AAD** = the JCS canonical bytes of `aad_fields`. `subject_id` is a typed ID of the sealed subject's kind (a union of the sealable kinds, matched against `payload_type`, fixed in P1-26; revision 7). Whether `subject_version` is present for subjects that are not versioned (Artifact, Transcript, ToolCallEvent) is fixed by schema-architect at P1-26 (recommendation: present iff the subject kind is versioned). Decryption with any other subject, version, payload type or schema version fails authentication. `aad_fields.schema_version` is the schema version of the *plaintext*.
5. **Encoding:** binary fields are base64url without padding, in canonical form (no whitespace; non-canonical trailing bits rejected).
6. **`plaintext_digest` (user decision 2; extended to every payload type by P-42).** For every `payload_type` (test profiles, audio, transcript text, tool arguments and results), `plaintext_digest` is either **absent** or uses a **keyed** scheme (`hmac-sha256-jcs/v1`, key named by the digest's `key_ref` and never in the export, ADR 0006). A plain `sha256-jcs/v1` digest on any sealed payload is a validation error, because short sealed text (a one-word answer, a spoken date of birth, an account number in tool arguments) is as guessable as a profile. When present, it is checked after decryption through `verify_digest()` (with the key resolver), and a mismatch raises.
7. **Where sealed payloads may appear:** only inside an `ExportBundle`. They never appear in snapshots, runs or internal bundles.
8. Phase 1 ships the schema and a **test-only** reference seal/unseal implementation (`cryptography` is a test-only dependency). Production key management (KMS integrations) is out of Phase 1.

## Rejected alternatives
- **Encrypt the whole export bundle.** All-or-nothing: a partner cannot validate structure, metrics and verdicts without the key, and the non-sensitive majority of a benchmark becomes opaque.
- **JWE (RFC 7516) compact or JSON serialization.** It is a reasonable standard, but its protected header is not JCS, its many algorithm options widen the attack surface, and it has no natural place for typed subject binding. A closed single-algorithm format with explicit AAD fields is easier to verify correctly in any language. (Algorithm names follow JOSE for familiarity.)
- **Encrypting directly with the KEK (no DEK).** Every payload would share one key, so nonce-reuse risk grows with volume, and rotating the KEK means re-encrypting all data instead of re-wrapping DEKs.
- **AES-CBC + HMAC or ChaCha20-Poly1305.** CBC+HMAC is easy to compose wrongly. ChaCha20-Poly1305 is sound, but A256GCM with AES-KW is the more widely available pair in cloud KMS services. The algorithm set is closed now and can be extended under a new `format` value.
- **No AAD binding.** An attacker could move a sealed profile onto another evaluator's reference (A-19).
- **Keys or passwords in the payload.** That would violate the secrets rule.
- **A plain `plaintext_digest` in clear.** Anyone holding the export could confirm guesses of a low-entropy profile offline without any key, which defeats sealing for exactly the payloads it exists for (user decision 2). GCM already authenticates the ciphertext, so the digest adds only the link to the referenced profile, which a keyed digest preserves.

## Consequences
- Readers must reject unknown `format` or `alg` values. A new algorithm means a new `format` string.
- A recipient who can unseal but does not hold the HMAC key can still decrypt and authenticate the payload (GCM) but cannot check the profile digest link; that is the intended trade-off.
- Migrating a bundle to a newer schema version does not rewrite `aad_fields.schema_version`. The plaintext keeps its own version until it is unsealed and resealed.

## Invariants (testable)
- I1. The shape matches the spec. Test: `test_sealed_shape`.
- I2. The algorithm and format sets are closed. Tests: `test_alg_closed`, `test_unknown_format_rejected`.
- I3. The nonce length matches the algorithm (12 bytes). Test: `test_nonce_len_matches_alg`.
- I4. Base64url is canonical, with no padding. Test: `test_base64url_canonical_no_padding`.
- I5. KeyRef holds no key material. Test: `test_keyref_no_key_material`.
- I6. Seal then unseal returns the plaintext. Test: `test_seal_unseal_roundtrip`.
- I7. Tampering fails: ciphertext, nonce, wrapped DEK; a wrong KEK fails. Tests: `test_tamper_ciphertext_fails`, `test_tamper_nonce_fails`, `test_tamper_wrapped_dek_fails`, `test_unseal_rejects_wrong_kek`.
- I8. Swapping the AAD to another profile fails. Tests: `test_aad_swap_to_other_profile_fails`; Adversarial: A-19.
- I9. `aad_fields.schema_version` is the plaintext's version. Test: `test_aad_schema_version_is_plaintext_version`.
- I10. A digest mismatch after decryption raises. Test: `test_digest_mismatch_after_decrypt_raises`.
- I11. A sealed profile's `plaintext_digest` is absent or keyed; a plain one is rejected. Tests: `test_sealed_profile_plaintext_digest_absent_or_keyed`, `test_sealed_profile_plain_digest_rejected`; Adversarial: A-32.
- I11a. Every sealed payload type follows the same drop-or-key rule. Test: `test_sealed_payload_plaintext_digest_absent_or_keyed_all_types`.
- I12. Sealed payloads appear only in ExportBundle. Test: `test_sealed_only_in_export_bundle`.
- I13. A human-subject payload in a shared export is sealed or redacted. Test: `test_shared_export_human_requires_seal_or_redaction` (ADR 0016).

## Decided items (formerly [Proposed]; all decided by the user in revision 5)
- **P-42. Drop-or-key for every sealed payload type. Decided by the user (revision 5): accepted as recommended.** Decision: apply Decision 6 to every `payload_type` (audio, transcript text, tool arguments), not only profiles. Reason: every sealed payload is sealed because it holds personal data, and short text payloads (a one-word answer, a date of birth spoken in a turn, an account number in tool arguments) are as guessable as a profile; only long audio is high-entropy. Had it been decided otherwise: plain digests remain allowed for non-profile payloads, and `SCHEMA_POLICY.md` must state that sealed short text can be confirmed by guessing. Test (binding): `test_sealed_payload_plaintext_digest_absent_or_keyed_all_types`.
- **P-18. Keyed digest field name. Decided by the user (revision 5):** `hmac-sha256-jcs/v1` is registered in 0.0.1 and `ContentDigest.salt_ref` is renamed `key_ref` (ADR 0006); this applies to keyed `plaintext_digest` values here.
