## Ruff artifact mapping closure v021

This amendment closes only `REMEDIATION-V020-001`. The externally reviewed v020
Ruff design remains unchanged.

Extend resolved R-004 `allowed_files` with exactly:

- `scripts/remediation_ruff_gate.py`;
- `tests/unit/test_remediation_ruff_gate.py`;
- `.ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v020.json`.

Extend R-004 `allowed_symbols` only with the symbols enumerated by v020 for the
script, test suite and generated baseline.

The manifest resolver uses these exact, highest-priority literal mappings:

| Path | Ownership | Requirement IDs | Baseline state | Final state | Mapping source |
|---|---|---|---|---|---|
| `scripts/remediation_ruff_gate.py` | `script` | `[R-004]` | absent, empty SHA | present, actual SHA | `v021_exact_R004_Ruff_artifact_mapping` |
| `tests/unit/test_remediation_ruff_gate.py` | `test` | `[R-004]` | absent, empty SHA | present, actual SHA | `v021_exact_R004_Ruff_artifact_mapping` |
| `.ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v020.json` | `control_artifact` | `[R-004, workflow-protocol-provenance]` | absent, empty SHA | present, actual SHA | `v021_exact_R004_Ruff_artifact_mapping` |

For the JSON control artifact, manifest fields
`authoritative_task=TASK-51797-ONE-PASS-REMEDIATION` and
`authoritative_version=one-pass-remediation-v020` are mandatory. Its sole writer
is the approved v020 `capture-baseline` command. It is capture-once: output must
be absent before capture and byte-immutable afterward. Hand editing, overwrite,
refresh or a second capture is RED.

The exact-v021 mapping runs before generic item or `.ai-workflow` mapping. A
missing path, different ownership/requirement/state, wildcard match or fourth
Ruff artifact is RED. Add policy-gate tests proving all three exact mappings and
rejection of any extra artifact.

v021 resolves exact predecessor v020 and seals its own four protocol files plus
the exact `response-v020.yaml` predecessor-review artifact. Final
order remains: Ruff validate GREEN, v021 refresh-manifest, immediate policy
validate with no intervening write.
