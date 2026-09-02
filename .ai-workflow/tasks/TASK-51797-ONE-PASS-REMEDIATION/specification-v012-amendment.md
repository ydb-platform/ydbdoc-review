## R-004 clean-at-capture approved-item baseline v012

This amendment changes only R-004 baseline derivation for approved item files
that were clean at snapshot capture and were changed later by the executor.
Every other approved requirement and fail-closed rule remains unchanged.

### Root cause

The immutable snapshot is a complete inventory of the worktree delta against
base at capture time, not an inventory of every repository file. Therefore a
tracked file that matched the base commit at capture is correctly absent from
the snapshot. `src/ydbdoc_review/parsing/yfm_plugins/tabs.py` was such a file;
it was later modified under the literal R-006 `allowed_files` entry. The current
bootstrap permits a missing snapshot baseline only for protocol, snapshot and
manifest outputs, so it rejects this otherwise approved transition.

### Exact baseline rule

For a current-delta path absent from the immutable snapshot, bootstrap may
derive a baseline from the approved base commit only when all conditions hold:

1. The path equals, byte-for-byte, at least one literal string in an approved
   `items[].allowed_files` list. Entries containing glob metacharacters do not
   authorize this rule. Pattern-only matches remain RED.
2. The path is not a protocol artifact, snapshot output or manifest output,
   which retain their earlier higher-precedence rules.
3. The immutable snapshot has already passed its v008 digest, base, count,
   uniqueness and status verification. Its absence of the path then proves the
   path matched base state at capture.
4. Read the exact Git object at `<approved base commit>:<path>` without using the
   working tree. If it is a blob, baseline is `present` with SHA-256 of the raw
   blob bytes. If the path does not exist at base, baseline is `absent` with an
   empty digest. Tree, submodule, lookup error or ambiguous object is RED.
5. Requirement IDs are the lexical unique union of only the items containing
   that exact literal path. Ownership remains the existing deterministic
   path-prefix ownership. The current final state comes from delta enumeration.
6. A deleted final state is allowed only when the exact path also appears in an
   approved `allowed_deletions` list. Otherwise deletion is RED.
7. The derived baseline must differ from final state. Equality means the path
   is not a valid current delta and is RED.

This rule authorizes no path that is only matched by a wildcard, no path absent
from all exact item allowlists, and no additional operation beyond the existing
item contract.

### Implementation and tests

Implement the closed helper and its use only in
`scripts/remediation_policy_gate.py`; test only in
`tests/unit/test_remediation_policy_gate.py`. Tests must cover `tabs.py`, a base
absent exact new file, multiple exact-item requirement union, wildcard-only and
unlisted rejection, forbidden deletion, allowed deletion, base lookup failure,
non-blob rejection and unchanged-state rejection.

v012 must resolve v011 as its exact predecessor and append only its four own
protocol files as self-sealing control artifacts. Generic amendment discovery,
working-tree baseline reads, snapshot recapture, inferred authorization and
unrelated edits are forbidden.
