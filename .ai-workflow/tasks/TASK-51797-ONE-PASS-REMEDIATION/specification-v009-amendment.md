## R-004 immutable snapshot self-path correction v009

This amendment changes only classification and validation of the immutable
snapshot output path in the R-004 manifest bootstrap contract approved through
v008. Every other v005/v006/v008 requirement, mapping, command, allowlist and
prohibition remains unchanged.

### Root cause

`baseline-snapshot-remediation-v005.yaml` is the output of the one-time capture.
It correctly inventories the delta that existed immediately before that output
was created. Therefore it cannot contain its own path without an impossible
self-referential recapture. The path is now an untracked current-delta path, but
v006 excluded it from mutable implementation-report classification and did not
define a separate mapping for the immutable snapshot output. Bootstrap therefore
correctly reports it as unmapped.

### Normative correction

1. The snapshot path has one dedicated classification with precedence after
   `manifest-self` and before `post-capture-control`:
   - path: `.ai-workflow/tasks/TASK-51797-ONE-PASS/baseline-snapshot-remediation-v005.yaml`;
   - mapping source: `immutable-snapshot-output`;
   - ownership class: `implementation_report`;
   - requirement IDs: `[R-004]`;
   - baseline state: `{kind: absent, sha256: ""}`;
   - final state: `{kind: present, sha256: <literal SHA-256 of current snapshot bytes>}`;
   - git status: `??`.
2. The snapshot path is included exactly once in the generated implementation
   manifest even though it is absent from its own 138 entries.
3. The literal final SHA-256 is not self-referential because the snapshot does
   not contain the implementation manifest or this digest. It must equal the
   externally verified v008 digest
   `7b04e01da5ee9762548b3c1c58c9e37a9135312e55100e3939d6389439d12336`.
4. Bootstrap and validation must continue to verify the snapshot byte digest,
   base commit, 138 unique entries and status counts before applying this special
   path rule. They must never recapture, rewrite or append the snapshot path to
   the snapshot.
5. Add the v009 specification, plan, request and eventual response to
   `post_capture_control_paths`, with ownership `control_artifact`, requirement
   `[workflow-protocol-provenance]` and absent baseline state.

### Exact implementation delta

Only these code/test edits are authorized:

- in `scripts/remediation_policy_gate.py`, add the exact
  `immutable-snapshot-output` classification and baseline/final-state handling;
- in `tests/unit/test_remediation_policy_gate.py`, prove the snapshot output is
  mapped exactly once with the values above, tampering remains RED, absence is
  RED, no self-inclusion/recapture occurs, and all v009 protocol paths map as
  post-capture control artifacts.

No snapshot recapture, snapshot edit, synthesized snapshot self-entry,
normalized snapshot self-hash, developer-selected mapping, fallback
classification or unrelated source/test change is authorized.
