# Phase 4 batch 0 contract: the ADR tests (P4-00a to P4-00e)

Author: qa, 2026-10-27. Source: `docs/phase4/SCOPE.md` (A.0 rows, gate items 2 and 13), ADRs 0026 to 0029, the Phase 4 amendments of ADRs 0023 and 0024, ADR 0004 Decision 11 and I43, `docs/adr/README.md` revision 50. These are documentation-reading tests (no `tracer_*` import). They are in the format of `test_adr0023_rules_are_tested`: rules and invariants cite tests; a cited name either exists in a `packages/*/tests` file or is on a pending set that only shrinks.

## 1. Where the files go

Nothing was created under `packages/`. Ten test files and one support module are staged; all go to one existing directory, `packages/evalcore/tests/` (one directory because the pending sets live in one shared support module; the ADR 0024 amendment test therefore sits in evalcore, not evalproviders, and the ADR 0004 test in evalcore, not evalschema, so that evalschema stays byte-unchanged, gate item 3):

```text
docs/phase4/evidence/red/batch-0/staging/tests/evalcore/ -> packages/evalcore/tests/   (11 files, ASCII)
```

The dev's first act: `git mv docs/phase4/evidence/red/batch-0/staging/tests/evalcore/*.py packages/evalcore/tests/`. No `__init__.py`, no conftest, no data file. The files were linted (ruff check, ruff format, mypy strict) at their final location in a scratch mirror, so they need no edit after the move. If `pyproject.toml` `extend-exclude` is to keep `docs/phase4/evidence/red` out of ruff as `docs/phase3/evidence/red` is, that is the lead's edit; the staged files themselves are clean.

| File | Task | Collected |
|---|---|---|
| `evalcore_phase4_adr_support.py` | readers, `check_adr`, pending sets, mutation helpers (not collected) | - |
| `test_adr0026_rules_are_tested.py` | P4-00a | 20 |
| `test_adr0027_rules_are_tested.py` | P4-00b | 18 |
| `test_adr0027_documents_match_json_schemas.py` | P4-00b | 35 |
| `test_adr0028_rules_are_tested.py` | P4-00c | 18 |
| `test_adr0029_rules_are_tested.py` | P4-00e | 19 |
| `test_adr0024_phase4_amendment_is_tested.py` | P4-00d | 19 |
| `test_adr0023_phase4_amendment_is_tested.py` | P4-00d | 19 |
| `test_adr0004_simulated_agent_transcript_rule_cites_test.py` | P4-00d | 14 |
| `test_phase4_adrs_cite_only_existing_or_pending_tests.py` | whole-ADR reader | 17 |
| `test_phase4_adr_index_and_gate.py` | index and gate | 13 |
| total | | 192 (191 pass, 1 opt-in skip) |

The existing tests `test_adr0023_rules_are_tested` and `test_adr0024_rules_are_tested` are not edited. Their Decision-block counts (19 and 10; 17 and 9) are re-asserted by the new amendment modules, which also prove no `A<n>` or `IA<n>` label leaks into the old blocks.

## 2. What each test pins

```text
check_adr(spec, text, defined, pending, scope) returns the list of problems; [] means the ADR holds:
  labels exactly 1..N (A1..An, IA1..IAm for amendments), I1..Ik, counts pinned:
    0026: 24 rules, I1-I10    0027: 17, I1-I10    0028: 12, I1-I7    0029: 10, I1-I4
    0024 amendment: A1-A9, IA1-IA4        0023 amendment: A1-A3, IA1-IA2
  every rule and invariant cites >= 1 backticked test name, naming rule test_[a-z0-9_]+
  every cited name is defined in packages/*/tests or is on PENDING[key]
  PENDING[key] subset of SCOPE section A names, subset of names the ADR still cites,
    disjoint from defined tests (a landed name must leave the set)
  the last invariant of 0026..0029 cites that ADR's own test; the test exists
```

The amendment rules are cut at the `### Invariants of the Phase 4 amendment` line, so rule A9 cannot be satisfied by the invariants below it. `Dialect specifications` of ADR 0026 is never read by the rule tests (they end at that heading); the whole-ADR reader reads it only to require that any undefined name there is one SCOPE section A names. No test pins its wording.

`test_adr0027_documents_match_json_schemas` reads the three schema blocks from the ADR text (never hard-coded): `check_schema` under draft 2020-12; `title` and `$schema` pinned; no `$id` in the ADR (a present `$id` must be a non-empty string); every object closed (`additionalProperties: false`); `required` inside `properties`; no `const`; every integer has a minimum and a maximum of at most `2**53 - 1` (or an enum); patterns compile; cross-document agreement (the three dialects in all three documents and in ADR 0026 rule 5; the eleven end reasons and eight states in ADR 0026 rule 9; caps vs `cap_reached`; caller modes vs `caller_text_origin`; the `END_CALL` sentinel with ADR 0028 rule 7; the 2,500 line cap with ADR 0028 rule 10; `mr-IN` only). The ADR gives no example documents, so the examples are built in the test from the ADR's cross-field rules with invented values and are completed from the ADR's own schema: any key the schema requires and the example lacks (the new required `connection` object of the sim profile was handled this way) is synthesised minimally, and a schema-driven test drops every required key of every example in turn and expects a refusal. 3 + 3 + 2 good documents validate; the hand-written violation lists (at least 17 per document) plus all required-key drops are refused; 19 text mutants of the ADR block are refused.

## 3. The pending sets and how the dev moves them

All pending sets are in `evalcore_phase4_adr_support.py` and nowhere else (`PENDING["0026"]` ... `PENDING["0004"]`, and `PENDING_OUTSIDE_RULES` for names an ADR cites only outside its rules). Current sizes: 0026 69 names, 0027 42, 0028 19, 0029 14, 0024 amendment 27, 0023 amendment 7, 0004 1, outside the rules 1 (`test_tdeg_sim_agent_silent_timing_not_measured`). Each is grouped by the SCOPE task that owns the name (comment lines `# P4-nn`).

Rule for the dev: in the commit that adds a test, delete its name from every set that holds it. If the name stays, `check_adr` fails with "exists now; remove it from the pending set". If an ADR is edited to cite a new name, the name must be added to a SCOPE section A row and to the set (otherwise: "no test defines it, not pending" or "not named by SCOPE section A"). Gate P4-G: all sets empty. `test_phase4_adr_pending_sets_are_empty_at_the_gate` asserts that; it is skipped unless `TRACER_PHASE4_GATE=1` (the lead runs it with that variable at the gate; when the last set is emptied the dev deletes the skip line).

## 4. Result on the current tree (2026-10-27 02:51 IST)

All 191 tests pass on the current docs (1 opt-in gate test skipped), on Python 3.11, 3.12, 3.13 and 3.14, and twice on 3.13 with identical results (the parallel edits of ADR 0026 and 0027 landed between the runs). They pass because the batch 0 ADRs are complete: the ADRs now cite only names that exist after the move (the tests of this batch) or that SCOPE section A names. The red state exists only before the move: run in place from `staging/`, the tests fail because their own names are not yet defined in `packages/*/tests` (the rules tests assert their own test is defined). Logs: `docs/phase4/evidence/red/batch-0/current.py3.11.log`, `.py3.12.log`, `.py3.13.run1.log`, `.py3.13.run2.log`, `.py3.14.log` (run in a scratch mirror of the tree with the staged files at their final path). `mutants.txt`: 31 mutants of the ADRs, README and SCOPE (a dropped rule, a renumbered rule, a nonexistent cited name, an uncited invariant, a dropped invariant, an extra rule, a broken JSON block, an open object, a bound over `2**53 - 1`, `connection` dropped from `required`, a dialect missing, an end reason missing, ADR 0029 flipped to Accepted in the ADR only, a README count, a pending name removed from SCOPE) all killed, plus 3 controls (edits to the dialect section, and to an Ambiguities intro) that correctly survive.

## 5. Named spec changes the dev or the lead will make

```text
P4-40 (ADR 0029 becomes Accepted for the provider the owner names, after O5):
  before: EXPECTED_STATUS = "Proposed" in test_adr0029_rules_are_tested.py, README row "Proposed",
          README Changes bullet "**Proposed**", and STATUS["0029"] = "Proposed" in
          test_phase4_adr_index_and_gate.py
  after:  "Accepted" in all four places in the same commit; also delete
          test_adr0029_rules_bind_no_code_while_proposed (the sentence it pins goes with Proposed)
  A flip in the ADR alone fails test_adr0029_is_proposed_until_p4_40 on purpose.
Shipped schemas: ADR 0027 says tracer-evalcore ships the same schemas and the documents test
  compares the two. No module exists yet, so the test checks the ADR side only. The task that ships
  FLAGS-style constants (P4-06/P4-10/P4-21, the lead decides) adds, to
  test_adr0027_documents_match_json_schemas.py, a comparison of each shipped schema with
  extract_schemas(...) (an addition, not a weakening).
```

## 6. Ambiguities and ADR notes for the architect

1. The ADR gives no example documents (ADR 0027), so "example documents the ADR gives" has nothing to read; examples are test-built (section 2). Suggest adding one example per document to the ADR if the architect wants the ADR to be the source.
2. The ADR 0027 schemas carry no `$id`, only `title`; the test pins `title` and tolerates a future `$id`.
3. Earlier in this session ADR 0027 invariant I5 cited `test_no_timing_value_without_both_edges_is_not_applicable`, renamed by SCOPE D-a to `test_timing_value_without_both_edges_is_error_never_zero`; the architect fixed it before the final run. The tests treat the old names as history only (`RENAMED`): allowed outside rules while SCOPE records the rename, a defect inside a rule or invariant, and a bare unbackticked `test_*` name is refused unless it is a `RENAMED` key (ADR 0027 Ambiguity 1 still names the old T-DEG test without backticks).
4. The README bullet counts are checked ("24 numbered rules and invariants I1 to I10", and so on); a changed rule count must change the README in the same edit.
5. `protobuf` is not pinned anywhere. The twelve new test names of the ADR 0026 amendment are not listed by hand: the pending sets list exactly the cited-and-undefined names (0026 holds 69). Re-run `regen` equivalent: the lead should re-ask qa to regenerate the sets when the architect adds names; the check fails loudly otherwise.
6. In the scratch mirror the existing `test_adr0025_rule_table_matches_normaliser` fails only because the mirror has no `fixtures/` directory (not part of this batch); `test_adr0024_rules_are_tested`, `test_adr0023_rules_are_tested`, the ADR 0016 amendment test and the decided-items test pass there. Run the whole workspace after the move.

## 7. Commands (after the move)

```text
uv run --locked --all-groups pytest packages/evalcore/tests -k "adr0004 or adr0023_phase4 or adr0024_phase4 or adr0026 or adr0027 or adr0028 or adr0029 or phase4_adr"
TRACER_PHASE4_GATE=1 uv run --locked --all-groups pytest packages/evalcore/tests/test_phase4_adr_index_and_gate.py
```
