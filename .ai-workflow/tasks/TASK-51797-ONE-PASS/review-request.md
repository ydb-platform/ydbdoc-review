---
task_id: TASK-51797-ONE-PASS
specification_version: "one-pass-v003"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS/response.yaml"
---

# Review request: TASK-51797-ONE-PASS

Review `specification.md` as a replacement architecture. Do not treat `TASK-51797` attempt specifications or their implementation drafts as requirements. Read repository code only to verify that the removal and migration scope is complete. Do not modify production code or this specification.

Confirm explicitly:

1. every source-PR and auto-added RU Markdown file has exactly one translation model request and one RU-derived render;
2. all non-prose atoms listed in the specification are protected before translation and restored deterministically from the same RU file, while only prose reaches the model;
3. old EN bytes cannot affect translation or output and are used only as a boolean path-existence fact for dependency planning;
4. incremental/full selection, magnitude, differential seeds, splicing, reconstruct-from-old-EN, verify retranslation, retries, fallback models, and fallback content are deleted with no hidden compatibility route;
   specifically confirm EN-to-RU, bilingual, `critic_only`, semantic-noop,
   RU-Markdown skip-glob/allowlist, and navigation-preserve bypasses are removed;
5. recursive internal `.md` dependency discovery is deterministic, cycle-safe, fragment-aware, locale-aware, and limited to exactly 20 auto-added files across the job, excluding initial PR files;
6. duplicate targets and fragment variants do not consume budget or cause duplicate calls, while assets, external links, and fragment-only links never enter the queue;
7. `budget_exceeded` and `missing_source` restore a readable original Markdown link, never a protocol protect token, and emit the complete blocking structured warning and manual action;
8. the all-or-nothing transaction prevents partial commits, raw RU fallback, old EN fallback, and publication of protect markers;
9. the migration plan deletes old code/config/tests/docs rather than preserving a dual runtime;
10. the acceptance criteria and test matrix fully cover one-call semantics, atom round trip, EN independence, recursive closure, budget boundary, cycles, unresolved reporting, transactional failure, and removal of old modes;
11. the specification leaves no developer choice about traversal order, counters, target resolution, failure behavior, unresolved representation, or publication.
12. merged and open PRs resolve one immutable `source_tree_sha` and use that
    exact tree for both RU bytes and EN path existence, with no moving or
    separately resolved EN snapshot.
13. direct YFM `.md` include targets and Markdown links share the exact BFS,
    canonical seen set, recursive traversal, and single 20-file auto-added
    budget, with include duplicates and budget exhaustion fully tested.
14. all render-time fence/text/Cyrillic LLM calls, named fragment/structural EN
    repair writers, translation-role transport retries, model-chain iteration,
    cross-model fallback, and fallback configuration are explicitly deleted and
    guarded by static/call-graph tests.
15. on any unresolved or failed transaction, only the structured warning
    report is inspectable; staged EN is never uploaded, attached, committed,
    pushed, or published.

Write the verdict to `.ai-workflow/tasks/TASK-51797-ONE-PASS/response.yaml` using `APPROVED`, `CHANGES_REQUESTED`, or `BLOCKED`. `APPROVED` requires both functional completeness and confirmation that no old translation architecture remains reachable.
