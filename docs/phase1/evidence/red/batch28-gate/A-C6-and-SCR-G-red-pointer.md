# Where the red evidence of A-C6 and SCR-G1..G6 lives

The red runs of the re-gate specification tests (A-C6 and SCR-G1 to SCR-G6) are kept with
the green evidence of batch 28; they are not copied or moved here.

- Red logs, one per Python version, run on the pre-landing source:
  `docs/phase1/evidence/green/batch28-spec/red-spec-files.py3.11.log`,
  `red-spec-files.py3.12.log`, `red-spec-files.py3.13.log`, `red-spec-files.py3.14.log`
  (same directory).
- Reason for each failure: `docs/phase1/evidence/green/batch28-spec/red-reasons.txt`. It records
  34 failed and 622 passed in the five files on each of 3.11 to 3.14: SCR-G1 (16 cases), SCR-G5
  (1), SCR-G6 (1) and A-C6 (16 cases). SCR-G2, SCR-G3 and SCR-G4 pass on both sides because they
  are consistent with the old and the new rule.
- Green runs of the same files: `final-full-suite.py3.*.log` and `oracle-golden-fix-full-suite.py3.*.log`
  in the same directory.
