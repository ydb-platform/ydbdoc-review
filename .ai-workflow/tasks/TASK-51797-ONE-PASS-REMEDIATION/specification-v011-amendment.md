## R-004 v010 control-artifact composition v011

This amendment changes only the R-004 resolver composition needed to classify
the four externally reviewed v010 protocol artifacts. Every other approved
requirement, mapping, command, allowlist and prohibition remains unchanged.

### Root cause

v010 correctly declares its four protocol files as post-capture control
artifacts, but v010 is an R-006 test amendment and the R-004 resolver currently
composes only the v006, v008 and v009 R-004 chain. Consequently the clean R-004
bootstrap sees
`implementation-plan-v010-amendment.yaml` and the other v010 protocol files in
the current delta but has no composed mapping for them.

### Normative correction

1. Extend `_resolved_amendment` with one exact v011 predecessor step. v011 must
   require `amends: one-pass-remediation-v009`, resolve
   `implementation-plan-v009-amendment.yaml`, and append the exact paths listed
   in `post_capture_control_paths_addition.exact_paths`.
2. The four subject paths are exactly the v010 specification, plan, request and
   response. Each maps to `control_artifact`, requirement
   `[workflow-protocol-provenance]`, baseline `{kind: absent, sha256: ""}`, and
   its ordinary current final state.
3. The v011 specification, plan, request and response receive the same exact
   control mapping. This is protocol self-sealing required by materializing the
   reviewed amendment; it does not authorize any further path or wildcard.
4. Bootstrap and validation must use v011 as the amendment input. v006, v008 and
   v009 semantics, including snapshot and manifest self handling, remain
   byte-for-byte unchanged after resolution except for the eight appended exact
   control paths.

### Exact implementation delta

Only `scripts/remediation_policy_gate.py::_resolved_amendment` and resolver
tests in `tests/unit/test_remediation_policy_gate.py` may change. Tests must
prove the exact predecessor, exact eight-path append, preservation of all v009
resolved values, rejection of a wrong predecessor, and absence of wildcard or
implicit classification.

No generic version discovery, directory glob, automatic trust of review files,
fallback mapping, snapshot change, manifest change or unrelated edit is
authorized.
