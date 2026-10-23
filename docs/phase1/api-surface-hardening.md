# Phase 1 API surface: security hardening (batch 25, P1-21 audit decisions)

Owner: qa. Tests: `packages/evalschema/tests/test_security_hardening.py` (123 tests), helpers in
`packages/evalschema/tests/_support_b16.py`. Source of the rules: the decision log entries "P1-21
SECURITY AUDIT DELIVERED" and "AUTO-APPROVED audit decisions" (findings C3a, C3b, m1, m2, m4, m6).
Readings marked P are qa's pinned readings; ambiguities are marked A and carry a recommendation.
Nothing here edits src, schemas, ADRs, SCOPE, HANDOFF, api-surface.md or an existing test.

## 1. Pinned rules

C3a, `DigestKeyUnavailable` quotes no key id.
- P1. The message is a function of the failure kind only. Two different `key_id`, `key_version` or
  `provider_hint` values give the same text. It keeps the opening words `no key available`. Covered:
  `str`, `repr`, `args`, the exception report, and a `ValidationError` a caller builds from it
  (`errors()`, `errors(include_input=False)`, `json(include_input=False)`, `str`, `repr`).
- P2. Two failure kinds (resolver raised; resolver returned a non-`bytes` or `None`) and the
  no-resolver case. The cause stays chained (`__cause__` is the resolver's exception,
  `__suppress_context__` true); nothing of its text, arguments or class name is in the message.
- P2b. For C3b, `VersionedEntity` and `EntityBase` are abstract and exempt from "is a `HidingModel`"
  but `VersionedEntity` must carry `hide_input_in_errors=True`; every other model of the walk
  subclasses `HidingModel`.

C3b, `KeyRef.key_id`, `key_version` (so `DekWrap.kek_ref`, `ContentDigest.key_ref`, both references
of `SealedPayload`) reject credential-shaped values.
- P3. One error, type `key_ref_credential`, at the location of the field (for example
  `dek_wrap.kek_ref.key_id`), message contains the word `credential`. No 8-character window of the
  value is in `str`, `repr`, `errors(include_input=False)` (with or without context) or
  `json(include_input=False)`; plain `str` has no `input_value`. Two values of one shape give an
  identical error. Rejected shapes: any `credentials.CREDENTIAL_PATTERNS` match at a boundary
  (sk-, sk_live_, rk_test_, ghp_/gho_, github_pat_, AKIA/ASIA, AIza, glpat-, xox, hf_, JWT, PEM
  header, also after `/` or `_`), the high-entropy rule applied to each segment of the value split
  at `/` and `:` (A5: a segment of 32 or more of `[A-Za-z0-9-_+=]` with upper, lower, digit, 16
  distinct), and any value that contains `=`. Accepted and stored verbatim: vault names, KMS aliases
  and ARNs, GCP KMS resource names, URN-like ids, UUIDs, 32 and 40 hex digits in either case (A1),
  segments of 31 characters joined by separators, a long name without a digit, non-ASCII names.
  `min_length=1`, `extra=forbid`, strict `str` and frozen are unchanged.
- P3b. `KeyRef` and `ContentDigest` set `hide_input_in_errors=True` and subclass `HidingModel`; so
  does every registered model that is, or embeds at any depth (inherited fields included), `KeyRef`,
  `ContentDigest` or `DekWrap`. Today that is 31 models, 16 of which do not hide yet (section 3).
  Consequence pinned by the tests: a rejected `ContentDigest.scheme` inside any model no longer names
  the scheme (the registered names are still listed), and `ContentDigest.value` is not quoted.
- P11b. Outside a hiding model (`verify_digest` on a `model_construct` record) the unknown scheme is
  still named, cut like any echo (P8).

m1, keyed digest key length.
- P4. `compute_digest(..., scheme="hmac-sha256-jcs/v1")` with a key of 0, 1, 16 or 31 bytes raises
  one error class that derives from both `ValueError` and `DigestKeyUnavailable` (A4; the name is
  not pinned) whose message contains `at least 32 bytes` and the scheme, and no key bytes, hex or
  text. 32, 33, 64 and 4096 bytes are accepted. The plain scheme and the `TypeError` for a
  non-`bytes` key (a `bytearray` too) are unchanged; the "needs both key and key_ref" order is
  unchanged (the length check comes after it).
- P5. `verify_digest` applies the same rule to the key the resolver returns: the same class (same
  words, no key material, no key id), raised before any content is hashed (never `DigestMismatch`),
  resolver asked once. A wrong key of 32 bytes is still `DigestMismatch`.

m2, key separation.
- P6. The digest key is the KEK when the `key_id` strings are equal, whatever the versions
  (`1`/absent, absent/`1`, `1`/`2` are all rejected). Error: one error at
  `plaintext_digest.key_ref`, type `key_separation`, no key id in any rendering. `key_id` is
  compared verbatim (case, trailing space, NFC against NFD differ and are accepted).
- P7. The message names `key_id` and no longer says "same key_id and key_version".

m4, truncated echo.
- P8. A quoted value longer than 64 characters (code points, P9) is cut to its first 64 characters
  followed by three ASCII dots, either `'<64>'...` or `'<64>...'` (both accepted), in the message and
  in `ctx["value"]`. Up to and including 64 characters nothing changes (exact old text).
  Applies where the old code quoted `repr(value)` outside a hiding model: `FullMatch`, `ClosedSet`
  (required by the decision), and, as P11, typed IDs, language tags, UTC instants, decimal strings
  (required, A3 decided).
- P10. Bound: `str`, `repr`, `errors(include_input=False)` and `json(include_input=False)` of a
  5 MB invalid value are each under 2,000 characters for one error, Python and JSON entry points.
- Hiding models: unchanged (nothing is quoted at any length).

m6, `env:` names.
- P12. `ProviderConfigRef.config_ref = "<scheme>:<name>"` is rejected, for every scheme (A6: `env`,
  `vault`, `keyring`, any other), when `<name>` is high-entropy (32 or more characters, mixed case
  with a digit): one error, type `config_ref_credential`, at `config_ref` (nested location inside
  `Project`), the same error text for every scheme, nothing quoted. The known-prefix rule still
  covers the whole value, scheme part included. 31 characters, all-upper and all-lower names,
  `env:OPENAI_API_KEY`, `keyring:tracer/sarvam` and `vault:secret/data/tracer/sarvam` are accepted.

## 2. Ambiguities and their decisions (decision log, AUTO-APPROVED A1-A10)

- A1 decided: 40 hex digits are NOT a rejected shape (an OpenPGP v4 fingerprint is a legitimate key
  name); lower-case and upper-case 40-hex ids and versions are accepted (guard test). The other
  shapes stay.
- A2 as pinned: any `=` in a key id or version is rejected.
- A3 decided: the 64-character cut is required for `FullMatch`, `ClosedSet`, typed IDs, language
  tags, UTC instants and decimal strings (P11 is required, one shared helper). Other echo sites
  (`events` evidence ids, `bundle` ids, `transcript` and `metrics` keys) are not tested; the helper
  should cover them too.
- A4 decided: the weak-key error derives from both `ValueError` and `DigestKeyUnavailable`; the
  tests assert both `isinstance` checks in `compute_digest` and `verify_digest`.
- A5 decided: the high-entropy rule runs per segment split at `/` and `:` for `key_id` and
  `key_version`; GCP KMS and URN-like ids are accepted, a credential-shaped segment is rejected.
  (The `[A-Za-z0-9+/=_-]` run class already stops at `:`; the `/` split is what changes.)
- A6 decided: the entropy rule applies to the name part of every `config_ref` scheme.
- A7 ASCII `...` ellipsis; A8 `provider_hint` gets no credential rule; A9 an unknown scheme from a
  validated model never names the scheme; A10 `VersionedEntity` takes the config flag only.

## 3. Named requests

Spec changes to existing tests (qa does not edit them; staff-reviewer decides; the scratch oracle
shows each failing for the stated reason):
- SC-1 `test_digest.py::test_digest_key_unavailable_never_echoes_what_a_resolver_says`: replaces
  `assert "vault-key-1" in str(error)` ("the message names the key_id") and the two
  `assert "vault-key-1" in report` with the reverse (reading P1). Also ADR 0006 I24 and ADR 0015 I14
  say the message names the key id.
- SC-2 `test_digest.py::test_hmac_digest_golden`: the "different key" `b"another-key-0000000000000000000"`
  is 31 bytes; use a 32-byte different key.
- SC-3 `test_digest.py::test_unknown_digest_scheme_rejected`: `assert scheme in str(caught.value)`
  after `ContentDigest(scheme=...)` (P3b); assert the scheme only on the `verify_digest` path of a
  `model_construct` record, and that the validated path lists the registered names.
- SC-4 `test_entity_base.py::test_digest_errors_propagate_unwrapped_through_every_entry_point`:
  `"md5-jcs/v1" in str(caught.value)` (same cause as SC-3).
- SC-5 `test_sealed_payload.py::test_digest_key_ref_differs_from_kek_ref`: the case "same key_id,
  another key_version is accepted" is now rejected (m2); keep the different-key_id case.
- SC-6 `test_sealing_adversarial.py::test_hostile_key_resolvers_never_verify_a_digest`: wrong keys
  `b"\x00"` and `DIGEST_KEY[:-1]` now raise `ValueError` (m1) instead of `DigestMismatch`; use
  32-byte wrong keys for the mismatch cases (the weak-key cases are covered in the new file).
- SC-7 `test_adversarial_registry.py::test_covering_tests_run_and_pass[A-41]` and `[X-08]`: red only
  through SC-6 and SC-3; no edit of its own.

Closure test seeds: `test_project_agent.py::test_hide_input_set_closed_under_embedding` add
`"KeyRef"` and `"ContentDigest"` to `seeds` (`DekWrap` is already covered by `sealed_nested`). The
models the walk then flags, to be made hiding: AgentTargetRef, ContentDigest, Dataset, KeyRef,
MetricBindingRef, MetricDefinitionRef, MetricResult, PersonaRef, PricingCatalogRef,
PricingSnapshot, RubricItem, RunVerdict, TestProfileRef, VerdictCheck, VersionedEntity,
VersionedRef.

Regenerations: none expected. In the scratch oracle `test_schema_lock`, `test_schema_agreement`,
`test_schema_export`, `test_golden_fixtures`, `test_docs_drift` and the field-doc checks all stay
green: the rules are code-only and `hide_input_in_errors` does not change JSON Schema. Doc text
that goes stale (for the doc owners): SCHEMA_POLICY sections 6, 9 (remove the S-N83 known gap),
11 (DigestKeyUnavailable sentence), ADR 0015 S-N83, I14, Decision 6b, ADR 0006 I24, Decision 4,
ADR 0007 Decisions 1 and 8, `docs/schema/value-objects.md` text for KeyRef if it lists the fields.
The code-only register (SCHEMA_POLICY 2.2) should list the new `KeyRef` rule.

## 4. Evidence and validation

`docs/phase1/evidence/red/batch25-hardening/`: `red-hardening.py3.11..3.14.log` (99 failed, 24
passed on the real tree; the 24 are guards for accepted inputs and for rules the code already has),
`green-hardening.py3.11..3.14.log` (123 passed in the scratch oracle), `oracle-src.patch` (the
source change that turns the file green, not applied), `oracle-existing-suite-failures.txt` (the
existing tests the oracle breaks: the set in section 3, unchanged by A1-A10). 27 mutants of the
oracle: 25 killed, 2 equivalent (M22, M26: removing or narrowing the whole-value prefix check changes
nothing because the per-segment check covers the same text). Tooling on the two new files: ruff,
ruff format, mypy --strict, check_headers, guard_private_fixtures, check_licenses clean;
`test_tdrift.py` green.
