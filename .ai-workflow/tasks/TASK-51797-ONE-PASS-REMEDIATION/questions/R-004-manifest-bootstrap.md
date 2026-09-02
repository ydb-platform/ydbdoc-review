---
type: implementation-question
status: open
tags: [task-51797, remediation, policy-gate, manifest]
---

R-004 requires this order: implement the gate, remove `uv.lock`, run the
prescribed tests, capture the immutable baseline snapshot, then validate using
the required whole-delta implementation manifest. The approved plan defines
the manifest's final schema and the validation command, but does not define
how the initial 114-entry manifest is populated at bootstrap, nor a command
that creates it. The manifest path did not exist before capture.

The snapshot is now captured at the exact prescribed path and is immutable.
The gate and its self-tests are GREEN. I have not invented a manifest generator
or hand-authored ownership/requirement mappings for the pre-existing delta,
because this would be an unreviewed implementation decision. Please provide an
approved bootstrap manifest construction rule or the initial manifest artifact.
