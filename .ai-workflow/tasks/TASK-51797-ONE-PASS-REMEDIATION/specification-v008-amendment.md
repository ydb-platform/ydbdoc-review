## R-004 bootstrap snapshot-count correction v008

This amendment changes only the R-004 manifest bootstrap contract approved in
v006/v007. Every other v005/v006 requirement, mapping, command, allowlist and
prohibition remains unchanged.

### Root cause

`114` and `138` describe two different worktree observations and were
incorrectly conflated in v006:

- `implementation-plan.yaml` records only the question-time aggregate
  `observed_start_inventory`: 85 tracked paths plus 29 untracked paths, total
  114. No immutable 114-path inventory was saved.
- The later capture required by v005 produced the immutable
  `baseline-snapshot-remediation-v005.yaml`. That artifact contains exactly 138
  unique entries: 50 modified, 43 deleted and 45 untracked paths, total 93
  tracked plus 45 untracked. Its base commit is the approved
  `9ff8edec9a26d3975306e20adca325c6eb9f77e6` and its SHA-256 is
  `7b04e01da5ee9762548b3c1c58c9e37a9135312e55100e3939d6389439d12336`.

The increase of 24 paths occurred between the question-time observation and
the later snapshot capture. Because v005 retained only the earlier aggregate,
not its exact path list, assigning those 24 paths individually to the earlier
observation would be invented provenance and is forbidden. The immutable
snapshot is the authoritative path-level baseline.

### Normative correction

1. Replace every normative v006 expectation of exactly 114 snapshot entries
   with exactly 138 snapshot entries.
2. Bootstrap and validate must load the snapshot, require its base commit to
   equal the command `--base`, require exactly 138 unique `entries[].path`
   values, and never modify the snapshot.
3. `implementation_manifest.observed_start_inventory` remains historical
   evidence only. It must not be used as the expected snapshot count or as a
   path inventory.
4. Add these already-published v007 artifacts to
   `post_capture_control_paths.exact_paths`, with ownership
   `control_artifact`, requirement `[workflow-protocol-provenance]`, and absent
   baseline state:
   - `.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/review-request-v007.md`
   - `.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v007.yaml`
5. Add the v008 specification, plan, request and eventual response to the same
   post-capture classification. This prevents the act of reviewing this
   correction from creating another unmapped path.
6. Classification precedence, manifest schema, normalized self-hash,
   `required_absent: uv.lock`, output path, overwrite refusal and all other
   v006 behavior remain unchanged.

### Exact implementation delta

Only these code/test edits are authorized:

- in `scripts/remediation_policy_gate.py`, replace the v006 bootstrap/validate
  snapshot-count expectation with the amendment value 138; retain base, unique
  path and immutability checks;
- in `tests/unit/test_remediation_policy_gate.py`, replace the 114-entry fixture
  expectation with 138 and add exact coverage that both v007 paths and all v008
  protocol paths map as post-capture control artifacts.

No developer-selected mapping, fallback classification, snapshot rewrite,
recapture, or unrelated source/test change is authorized.
