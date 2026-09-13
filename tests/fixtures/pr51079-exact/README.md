# Exact offline PR #51079 replay

This fixture freezes the historical input authorities, not a synthetic approximation
or the current upstream branch. It is separate from the eight-Markdown R-GL-16
fixture and `frozen_pr51079_candidate` in `test_source_preserving_translation.py`.

| Snapshot | Immutable upstream authority | Purpose |
| --- | --- | --- |
| H0 | `0aa50f3ab4688eb53d04888eba9fb3c36968ff14` | Original source base |
| H | `673b924813e35646535e20d917b014094bf7de14` | Source PR #51079 merge |
| B | `7886ff84e31c2f52c99adf8fbeb908fda38e4192` | Current RU authority and EN baseline |
| K | `a1be7c4f2521da74f759a4a2277f9a9e442ae840` | Unmodified defective translation PR #52948 |

## Provenance and integrity

Provisioning happened outside pytest through read-only `gh api --method GET`
using the environment's default authentication. Full-SHA commit/tree lookups
resolved tree entries and full-SHA blob GETs supplied exact UTF-8 bytes. Blob
payloads were checked against their Git object SHA-1. Non-truncated immutable
trees established explicit missing paths. The full-SHA H0...H comparison confirmed
exactly two `modified` entries in `source-pr-files.json`:

- `ydb/docs/ru/core/reference/configuration/auth_config.md`
- `ydb/docs/ru/core/security/authentication.md`

No branch name, live PR head, workflow invocation or GitHub write was used.
For an independent provenance audit, use immutable endpoints such as
`repos/ydb-platform/ydb/git/trees/<full-tree-or-commit-SHA>` and
`repos/ydb-platform/ydb/git/blobs/<full-blob-SHA>`. There is no acquisition helper
or network fallback inside the test.

`snapshots.json` contains 1,353 explicitly recorded reads: H0 2, H 2, B 1,335,
K 14. Eighteen records are authentic absences. K contains the six complete RU/EN
Markdown pairs and both locale security TOCs; its English defects are untouched.
B includes the complete scope and the read-only root TOC/include/link closure.
The large baseline context is required because the real orphan gate traverses
the EN root graph; it is not additional translation scope. Unknown snapshot,
path or model segment is an assertion failure, never empty or identity fallback.

`manifest.json` hashes every present snapshot text with SHA-256 over its exact
UTF-8 bytes, distinguishes null from empty text, and hashes the three data files.
The loader validates duplicate/missing inventory entries and all hashes before
use. JSON escaping preserves newlines and original text; tests use binary reads
and UTF-8 decoding, not newline-normalizing text reads.

## Reviewed model-response oracle

`model-translations.json` is a source-segment response dictionary, not a golden
document produced by the renderer under test. During provisioning the RU source
units and historical K English units were extracted separately, compared by
kind/count, and independently aligned by their protected atom values. Exact
source marker identities were then mapped into reviewed English units without
calling the production renderer or production source/target aligner. Localized
link destinations were individually matched, not accepted through fuzzy aliases.
Every bilingual unit was read for meaning, including LDAP configuration, token
lifetimes and retry behavior, JWT/OIDC checks, client/device certificate behavior,
and IAM modes. Unrelated historical wording was left intact.

The 357 unique file/source keys include 346 deduplicated segment responses and
11 residual inline/include-label spans. Duplicate `Формирование SID` headings
use the same reviewed `SID generation` response. Corrections are confined to:

- Both diagrams' human-readable labels, using the independently authored complete
  English diagrams in `../pr51079-mermaid/`; identifiers, arrows, control syntax
  and technical tokens remain fixed. Their RU bytes are explicitly compared to
  the prior fixture and neither prior file is changed.
- The complete certificate Subject notation `Name=Value,...@<domain>`.
- `ldaps` scheme terminology and the two padded `use_tls` / `StartTls` labels.
- Necessary include captions: `Creating and using a user token` and
  `Life cycle of a record in the cache`, and the two navigation labels.

The glossary request contains only the required `user-token` section. The real
coverage plan preserves its surrounding B English bytes. The transport returns
only an exact keyed English response, never copies unknown input or invents
new response text. Unknown requests are recorded as well as raised so a model
client's error handling cannot silently swallow fixture incompleteness.

## Actual local execution and negative controls

The local temporary Git repository has real H0, H and B commits with local SHA
identities, distinct from upstream metadata. Real scope planning produces exactly
six Markdown outputs under `ydb/docs/en/core/`:

- `concepts/glossary.md`
- `reference/configuration/auth_config.md`
- `security/_assets/user-token-lifecycle.md`
- `security/_assets/user-token.md`
- `security/authentication.md`
- `security/caching-authentication-results.md`

The seventh output is `security/toc_p.yaml`. Real pair loading, source coverage,
translation harness, navigation merge, pending-output orphan/include gate,
result writer and late fragment/path reconciliation run before final link and
language checks. Removing the real caching page's two include directives makes
exactly the two diagram assets orphaned. Both Mermaid skeletons must equal RU.

The accepted output is committed locally. Verification uses actual pair
`critic_only`/VERIFY_PROFILE behavior and the final gates against `git show` of
that full 40-character SHA. Both report result text fields must equal those
immutable bytes. `ReportMeta.checkout_ref` retains the full SHA; the report names
it using the existing canonical 12-character checkout display. A stale baseline
SHA or uncommitted result text does not satisfy this contract.

Translation acceptance also uses the real deferred-outbound finding lifecycle,
not just the separate VERIFY result. It requires `PUBLISH_NORMAL` before apply
and again after the final tree gates, before committing. Authorized late repair
writers advance both QA text fields through the production preimage-checked
interface. The test never rebinds QA from disk. Both translation result fields
then equal `git show` at the exact full candidate SHA, and the immutable checkout
guard must return no mismatch before the independent VERIFY run.

An independent local commit of unchanged historical K English is non-green.
Seven isolated K2 cases each start from an accepted candidate: Russian actor,
Russian lifecycle Note, Russian inline notation, `ldaps` schema, each padded
label, and critic refusal on clean prose. Direct language/editorial checks run
in addition to the combined file/report gate; refusal remains a warning.
Each case first proves the canonical `Статус QA (K): 🟢 GREEN` report, then
requires an explicit RED (language) or YELLOW (editorial/refusal) status with
GREEN absent. Reports must not contain the obsolete merge recommendation.

Only model transport and the two external Wikipedia language lookups are stubbed.
The lookup map admits exactly RU LDAP and Argon2 to their English articles.
GitHub's request adapter raises if reached. `pytest-socket` disables sockets for
the entire test, including translation and VERIFY. No external job is invoked.
The local upstream-tip reader is cached by immutable SHA/path; its data still
comes from real local Git objects. Admission also happens below every imported
reader alias at the Git subprocess boundary, including `show`, `cat-file` and
path-specific `ls-tree` calls made by `read_text_at_commit`. Local H0/H/B SHAs
select their own recorded snapshot inventory. Candidate commits use B's admitted
paths, including the seven explicit output paths. A missing inventory entry raises
before Git can return a misleading absence from the sparse fixture checkout.
Unknown-read evidence survives caught exceptions and fails fixture teardown.

The full positive translate/late-reconciliation/VERIFY audit asserts no unrecorded
reads and specifically exercises the B RU contexts
`concepts/query_execution/execution_process.md` and
`reference/ydb-sdk/error_handling.md`. Their authentic B blobs are
`7aaeadefa092d7d6dbbb1b8296d7de6618a9e690` (19,421 bytes) and
`17a7466d19a337b45f522c8ee5f2562924b49cc7` (7,223 bytes).
A negative late-reconciliation case removes only the first path's admission,
leaves its real Git/worktree bytes intact, and proves the pre-imported reader
cannot bypass the guard. The upstream helper's two remote-style spellings of a
full SHA are allowed only for explicitly null paths after proving those local
fallback refs do not exist; no branch or network lookup supplies fixture bytes.

Run from the repository root:

```bash
XDG_CACHE_HOME=/private/tmp/ydbdoc-review-pr51079-cache .venv/bin/python -m pytest tests/harness/test_pr51079_exact_e2e.py --disable-socket -vv
```

The 180-second per-test limit accommodates the complete root-TOC traversal and
real Git subprocesses. Tests are not marked `integration` or `llm`, so they also
run in the default offline relevant suite. Offline acceptance does not claim a
production translation retry, independent production content review, or green
`doc_verify` and documentation build on the same production SHA.
