## v023 active/frozen cardinality correction v024

This amendment closes only `REMEDIATION-V023-001`. Every other v023 fact and
Ruff rule remains unchanged.

Current pre-implementation facts are:

- present delta Python paths: 75;
- existing active paths: 4, all Ruff-clean;
- `tests/unit/test_remediation_ruff_gate.py`: absent and therefore not currently
  lintable or countable as a present active path;
- frozen current paths: 71 (`75 - 4`), with the already confirmed 108
  diagnostics.

The approved implementation creates exactly
`tests/unit/test_remediation_ruff_gate.py`. At capture time the required facts
become:

- complete delta Python paths: 111;
- present delta Python paths: 76, consisting of 56 base-present modified and 20
  base-absent new;
- deleted Python paths: 35;
- active paths: the exact five v023 paths, all present and Ruff-clean;
- frozen current paths: 71 (`76 - 5`), with exactly 108 diagnostics;
- base-untouched paths: 174, with exactly 198 diagnostics;
- total baseline diagnostics: 306.

Capture must require these exact post-creation cardinalities. Missing any active
path, an active diagnostic, a frozen count other than 71, or a complete delta
count other than 111 is RED and writes no artifact. The fifth path is described
as zero-diagnostic only after it is created and its focused test/Ruff run is
GREEN. No other v023 schema, partition, hash, enumeration, executable, multiset
or mapping rule changes.

v024 resolves exact predecessor v023, self-seals its own four protocol files
plus `response-v023.yaml`, and preserves the final capture, Ruff validate,
refresh and immediate policy-validate order.

