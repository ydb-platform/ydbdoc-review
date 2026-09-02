# TASK-51797-ONE-PASS v010: bounded acquisition and bounded local critic repair

## Objective

Replace the current incremental/full translation architecture with one RU-authoritative algorithm:

1. determine the complete bounded RU file queue;
2. protect every non-prose atom in each RU file;
3. acquire at most one accepted whole-file prose payload per queued RU file;
4. deterministically restore protected atoms and render once;
5. run bounded one-block critic repair for eligible RED findings;
6. validate the resulting EN files without consulting old EN content.

Existing EN bytes must have no influence on translation, ordering, prose,
links, structure, or rendering. EN path existence and blob identity may be read
only for dependency planning and the publication provenance guard. EN content
is never passed to a model and is never a rendering template.

## Mandatory removal of the old architecture

Delete, rather than deprecate, every production branch that selects or executes:

- incremental versus full translation;
- change-magnitude thresholds;
- differential seeds from old EN;
- low-magnitude splice or patching into old EN;
- reconstruction assembled from old EN segments;
- verify-time or critic-driven full-file retranslation;
- unbounded, cross-block, or post-acceptance full translation retries;
- hidden fallback to old EN after parse, model, placeholder, or rendering failure.
- EN-to-RU translation, bilingual-pair action selection, `critic_only` as a
  translation-plan substitute, semantic-noop preservation, and RU Markdown
  skip-globs or allowlists that can bypass the universal RU queue.
- every post-translation LLM writer, including fence-comment, text-fence, and
  Cyrillic-prose translation from `harness/render.py`;
- every post-translation EN mutation that repairs or invents structure, hrefs,
  fragments, or anchors, including `repair_en_fragments`,
  `prefer_baseline_href_when_fragment_missing`,
  `add_explicit_ascii_fragment_anchor`, and
  `repair_en_structure_from_ru` production call paths;
- hidden client-level retry loops, dynamically extended model chains,
  environment-configured fallback lists, voting, response mixing, and any
  fallback outside the bounded acquisition policy defined below.

Remove the corresponding configuration fields, environment parsing, state fields, log modes, and dead helpers. Replace tests that assert these modes with one-pass tests. A compatibility alias that still enters an old path is not allowed.

The critic is read-only: it emits findings and never mutates document bytes.
Only the bounded local repair controller defined below may request and insert a
replacement, and only for one eligible prose block or constrained English
anchor proposal at a time. Verification outside that controller is read-only.

There is one production action for an added or modified RU Markdown document:
`translate_ru_to_en_once`. Remove action selectors that route such a file to
`skip`, `critic_only`, EN-to-RU, bilingual merge, semantic-noop, navigation
preserve, or any legacy pair mode. A source PR's added or modified EN-only file
is outside automatic RU-to-EN generation and receives read-only QA; it must not
be used to seed, suppress, or redirect a RU translation. Files explicitly
classified as navigation by path and file type remain in the deterministic
navigation workflow, but no `.md` path under `ydb/docs/ru/` may be classified as
navigation or skipped from the one-pass queue.

## Authoritative inputs

For a merged source PR, use the existing authoritative PR-files API list and
the immutable merge-commit tree. For an open source PR, use its immutable head
commit tree. Call this single commit `source_tree_sha`. Resolve it once from PR
metadata before planning and log it. Both RU content reads and EN
path-existence checks use exactly `source_tree_sha`. Do not read EN existence
from a moving default branch, translation branch, working tree, merge base, or
a separately resolved ref. Thus the existence snapshot is the set of
`ydb/docs/en/**` paths present in the same immutable source tree that supplies
RU bytes; file contents are never loaded for translation.

Also resolve and pin before planning:

- `source_base_sha`: first parent of the merge commit for a merged PR, or the
  PR's recorded base commit for an open PR;
- `publication_tree_sha`: current default-branch head selected as the proposed
  publication base.

These three SHAs are immutable for the job. If any ref changes while the job is
running, the pinned values still govern; publication uses
`publication_tree_sha` or fails and requires a new job.

### Publication provenance guard

Run this guard after dependency planning and before the first model call. It
uses path existence, blob OIDs, ancestry, and commit history only. It never
uses EN text as a translation input.

For a merged PR, require `source_tree_sha` to be an ancestor of
`publication_tree_sha`. For an open PR, require `source_base_sha` to be an
ancestor of `publication_tree_sha`. Otherwise block with `history_diverged`.

For each initial RU output:

- merged PR: the RU blob at `publication_tree_sha` must equal the RU blob at
  `source_tree_sha`;
- open PR: the RU blob at `publication_tree_sha` must equal the RU blob at
  `source_base_sha`; the changed RU blob at `source_tree_sha` remains the
  translation input.

For every auto-added RU dependency, the RU blob at `publication_tree_sha` must
equal its blob at `source_tree_sha`. For every mapped EN output, including
initial and auto-added files, the EN path existence and blob OID at
`publication_tree_sha` must equal those at `source_base_sha`. A mapped EN path
also must not be added, modified, renamed, or deleted by the source PR itself.

Any mismatch blocks the whole transaction before LLM use with
`stale_source_or_newer_translation`. The report must list the RU source path,
mapped EN path, baseline/current blob OIDs or absence, and every intervening
commit touching either path. It must distinguish `newer_ru`, `newer_en`,
`en_created`, `en_deleted`, and `source_pr_en_conflict`.

Never resolve this by translating `publication_tree_sha` RU, merging current EN,
or expanding the source scope. Translating the current RU tip requires a new,
explicitly scoped source request/job. This preserves newer RU and EN work
without silently changing what the old PR authorized.

Initial queue entries are every added or modified Markdown file under `ydb/docs/ru/` in the source PR. Deleted files are handled by the existing deletion workflow and are not translation calls. Navigation files follow their existing deterministic navigation workflow and are not silently treated as prose documents.

Each initial RU Markdown path enters the translation queue exactly once. Initial files do not consume the dependency budget.

## Queue and dependency closure

Before any translation call, compute a deterministic recursive dependency
closure from internal Markdown-link atoms and direct YFM `{% include ... %}`
Markdown targets in the authoritative RU bytes. Both edge kinds use one queue,
one `seen_ru_paths` set, and one shared auto-added budget of 20.

### Canonical target resolution

For each Markdown link, separate `path`, query if present, and `fragment`. Resolve only the path when determining file identity. Preserve the original query and fragment verbatim for restoration.

For each parser-recognized YFM include directive, extract its direct include
target without recursively reading it outside the queue. A direct include
whose normalized target ends in `.md` is a dependency edge. Resolve relative,
docs-rooted, and absolute `/ru/` include paths with the same rules as Markdown
links, and map its EN counterpart with the same locale rule. Preserve the
entire original directive, spacing, quoting, parameters, and target spelling as
one source-owned atom for restoration. Non-`.md` includes are protected but do
not enter the dependency queue.

- Relative `.md` paths resolve against the source file's POSIX parent directory.
- Repository/docs-rooted paths resolve under the configured docs root.
- Absolute locale paths beginning with `/ru/` map to the identical `/en/` suffix for counterpart existence checks. Relative paths map by replacing the source file's `ydb/docs/ru/` root with `ydb/docs/en/` after POSIX normalization.
- `.` and `..` are normalized. A target escaping the RU docs root is invalid and produces a blocking `invalid_internal_target` QA record.
- A fragment does not create a distinct queue node. Ten hrefs to `page.md#a` and `page.md#b` enqueue `page.md` once while retaining all original href occurrences for restoration and reporting.
- Fragment-only links such as `#section` stay local atoms and never enqueue a file.
- External schemes, protocol-relative URLs, `mailto:`, `tel:`, and non-Markdown targets never enqueue files.
- Images and other assets never enqueue files. Their complete syntax and target are protected atoms.
- URL-encoded path characters are decoded only for canonical filesystem resolution. The original spelling is retained for restoration.

An internal `.md` target is auto-added only when all are true:

1. its canonical EN counterpart does not exist in the path set captured from
   `source_tree_sha`;
2. it is not already an initial source-PR file or queued dependency;
3. its canonical RU file exists at the same authoritative RU source ref.

If RU does not exist, do not enqueue. Record `missing_source` for every referring href.

These conditions apply identically to Markdown-link and YFM-include targets.
An include and a link resolving to the same canonical RU path enqueue one file
and consume one budget slot. Discovery recurses only when the target has been
admitted to the normal BFS queue; there is no separate ambient include-graph
walk and no include-specific budget.

### Traversal and budget

Use breadth-first traversal. Sort newly discovered canonical RU targets lexicographically within each source file and process referring source files in queue order. This defines a repeatable choice when more than 20 dependencies are reachable.

Maintain two disjoint counters:

- `initial_count`: source-PR RU Markdown files, unlimited by this rule;
- `auto_added_count`: recursively discovered RU dependency files, hard maximum `20` for the entire job.

Files 1 through 20 in the auto-added order enter the same queue and are recursively scanned. The twenty-first and later unique missing-EN targets are not queued and produce `budget_exceeded` records. Duplicate links, links to a file already queued, and cycle edges do not increment the counter.

Maintain a canonical `seen_ru_paths` set before enqueueing. Thus `A -> B -> A`, self-links, aliases normalized to the same path, and repeated fragments terminate without extra calls.

Compute this bounded closure completely before translation starts. Queue planning performs no LLM calls.

## One-pass file translation protocol

For every queued RU file, including all auto-added dependencies, execute this
single-result state machine:

```text
RU bytes -> parse -> protect -> bounded candidate acquisition
         -> accept first protocol-valid prose payload or block
         -> deterministic restore -> one render -> deterministic QA
         -> staged EN bytes
```

Each request contains all prose records for one file in one structured payload.
It is never split into per-segment calls. A file may make bounded technical
acquisition attempts, but has zero or one accepted prose payload and zero or
one final RU-derived render. Exactly one render occurs only when a payload is
accepted and all pre-render checks pass; acquisition or pre-render failure
renders zero times. Rejected candidate responses are never rendered, merged,
voted on, compared for quality, or used to edit another candidate.

### Fixed acquisition policy

At job start resolve exactly two distinct translation model slugs from required
immutable configuration: `translate_primary_model` and
`translate_fallback_model`. Log them with the job manifest. No third model,
environment-appended list, dynamic discovery, or chain expansion is allowed.

The maximum is four network attempts per file: two attempts on the translation
primary, then two on the translation fallback. The exact original protected payload, prompt version,
temperature, seed when supported, and response schema are identical across all
attempts; only the model slug and attempt metadata differ.

Classify outcomes as follows:

| Outcome | Action | Maximum |
|---|---|---:|
| Timeout, connection reset, DNS/TLS transport failure, HTTP 408, 429, or 5xx | Retry the same model once; after its second failure advance to the next fixed model | 2 attempts/model |
| Model slug unavailable/not found for that provider | Advance immediately to the next fixed model; no same-model retry | 1 response/model |
| HTTP 400, 401, 403, invalid request, auth/config error | Block the file and transaction; fallback cannot repair configuration | 1 total |
| Empty/refusal response, invalid JSON/schema, missing/extra/duplicate prose ID, changed/lost/crossed token, or other protocol violation | Discard the candidate and advance immediately to the next fixed model; no retry of that model | 1 response/model |
| Protocol-valid payload | Stop all acquisition immediately; this is the sole candidate eligible for acceptance | First valid payload only |
| No protocol-valid payload after the fixed chain | Block with `translation_acquisition_exhausted` | 4 network attempts maximum |
| Deterministic pre-render prose gate failure | Block; do not enter local repair because no safe rendered block exists | 0 further calls |

An attempt that returns no technically usable payload is not an accepted
translation. The first protocol-valid payload terminates acquisition even if
later quality QA blocks publication. The attempt report records model slug,
ordinal, classification, and sanitized error, but never stores a rejected
candidate as output.

### Base acceptance and content quality

Before render, deterministic checks enforce response schema, prose-ID parity,
token grammar, non-empty prose, and no Cyrillic in fields classified as prose.
After the single base render, deterministic heuristics and the structured
critic may report terminology, fluency, completeness, residual Cyrillic prose,
or semantic concerns. The base translation remains the only full-document
translation. Eligible findings may enter bounded local repair; all other
findings block or use deterministic source-owned-atom handling below.

If acquisition or acceptance fails, mark the file `translation_failed`, stage
no EN bytes for it, and make the whole transaction non-publishable. Never
substitute old EN or raw RU as a successful translation.

The acquisition table is the sole normative failure classifier. Mark a file
`translation_failed` only when that table reaches a terminal blocking outcome
or when acceptance/restoration/QA blocks after acquisition. Never substitute
old EN or raw RU as a successful translation.

## Protection grammar

The parser must produce a lossless document representation containing prose slots and source-owned atoms. Generate opaque, collision-resistant internal tokens scoped to one file. Tokens are protocol-only and must never be committed or included in final EN Markdown.

Protect these source-owned atoms before the model call:

- complete Markdown link wrappers, hrefs, optional titles, and destination escaping; expose only the label's prose slots for translation while retaining link nesting structurally;
- autolinks and bare URLs;
- every `path#fragment`, query, and anchor identifier;
- explicit heading anchors and fragment declarations;
- inline code, code spans, fenced and indented code blocks, including fence delimiter and info string;
- configuration/YAML/YQL/JSON/XML/shell examples as complete code atoms;
- HTML tags/comments and attributes;
- images, asset paths, dimensions, and titles;
- YFM variables, includes, conditions, tabs, notes, cuts, tables, and directive delimiters/arguments; translate only prose bodies or prose labels that the AST explicitly exposes;
- template syntax, macros, entities, reference definitions, and other parser-recognized non-prose syntax.

Code blocks and configuration bodies are restored byte-for-byte from RU. Cyrillic inside them is not prose and is not sent to the translator. Any policy that requires English code comments or metavariables is a separate source-authoring concern, not a hidden second translation path.

The translation payload contains stable prose IDs and prose text containing only protocol tokens needed to preserve inline placement. It contains no real href, path, fragment, code body, directive argument, or configuration body.

The response must contain exactly one translation for every prose ID, no unknown IDs, and the exact token multiset and valid structural order for each record. Link boundary tokens must be balanced, paired by identity, non-crossing, and non-nested unless the source AST itself contains that nesting. Any violation fails the file.

Restoration uses only the token-to-source-atom table produced from that same RU file. It cannot infer an href from translated words, use an old EN atom, renumber across files, or repair with model output. It may only substitute exact protected atoms back into their recorded AST slots and serialize the recorded Markdown/YFM containers. Fences, directives, hrefs, fragments, code, and configuration are restored byte-for-byte except the explicitly defined absolute locale-prefix mapping. ASCII/non-Cyrillic anchors are restored byte-for-byte. Cyrillic explicit anchors follow only the declared English-anchor localization exception below. If exact slot/cardinality/nesting restoration is impossible, block before render. Render from the restored RU-derived AST exactly once.

There is no legacy fragment remap, inferred anchor insertion outside the
declared Cyrillic-anchor rule, structural synthesis, or Markdown normalization
that changes source-owned atoms. Exact atom restoration and the constrained
anchor-localization/link-update engine are the only deterministic source-owned
writers. The bounded one-block controller is the only permitted post-render
model path and cannot write source-owned atoms.

After rendering, scan for every internal token form, including literal, escaped, HTML-escaped, and percent-encoded spellings. Any occurrence is a blocking `unrestored_protect_token`; the file is not staged.

## Bounded local critic-repair

Local repair begins only after a valid base payload has been restored and the
document has completed its one full RU-derived render. It never reruns the
full-file translation and never combines prose from different blocks.

### Structured critic contract

Each critic evaluation receives the whole rendered document plus the protected
RU-derived atom manifest read-only. It returns a strict list of findings:

```yaml
finding_id: <model-supplied stable id>
rule_id: <registered rule>
severity: RED | YELLOW | INFO
block_id: <stable AST prose-block id>
range:
  start: <UTF-8 byte offset within block prose>
  end: <UTF-8 byte offset within block prose>
atom_ids: [<exact implicated atom ids>]
message: <specific defect>
required_rule: <machine-known acceptance rule>
context: <concise evidence>
repair_class: prose | english_anchor | deterministic_atom | not_repairable
```

Code validates every field and replaces the model-supplied `finding_id` with a
canonical ID derived from `rule_id`, stable `block_id`, normalized range,
sorted `atom_ids`, and normalized defect fingerprint. Findings with unknown
rules, invalid ranges, mismatched atoms, or unstable block identity are RED and
not model-repairable. The canonical ID, not free-form wording, is used across
re-critics.

The critic never writes a replacement and has no access to mutation tools.
Separating diagnosis from mutation keeps severity/order auditable, prevents a
critic response from bypassing range guards, and lets deterministic validators
reject malformed or self-serving findings before any repair call.

### Repair eligibility

Model repair is allowed only for:

- `prose`: mistranslation, omitted meaning, grammar, terminology, or residual
  Cyrillic inside an AST field classified as translatable prose;
- `english_anchor`: proposing one ASCII English anchor for a heading whose
  explicit RU anchor contains Cyrillic, only after deterministic slugging
  cannot produce a unique valid anchor.

Model repair is forbidden for Markdown/YFM structure, links, hrefs, URL/path,
fragments, ASCII/non-Cyrillic anchor parity, fences, code, configuration,
directives, includes, variables, images, HTML, protected-token placement, or
any other source-owned atom. Those findings use exact deterministic
restoration/parity code. If deterministic code cannot restore them losslessly,
publication blocks; the repair model is never asked to invent structure.

YELLOW and INFO findings never trigger a model call. Only RED findings whose
registered `repair_class` is `prose` or the constrained `english_anchor` case
are eligible. Any other RED must be resolved deterministically or block.

| Finding class | Resolution owner | Model allowed? | Terminal condition |
|---|---|---:|---|
| Grammar, terminology, mistranslation, omission, residual Cyrillic in translatable prose | Local repair controller | Yes, one block, max 2 logical repair attempts/finding | Re-critic clears finding and global invariants pass |
| Cyrillic RU explicit anchor requiring a new English anchor | Deterministic slugger first; constrained anchor proposal only on invalid/collision result | Yes, anchor token only | All inbound links updated in scope and global integrity passes |
| ASCII/non-Cyrillic RU anchor mismatch | Exact parity restorer | No | Exact bytes restored or block |
| Link, href, path, fragment, inbound-link update, protected atom | Deterministic atom/link engine | No | Lossless restoration and global parity or block |
| Fence, code, configuration, Markdown/YFM/directive/container structure | Deterministic AST restorer | No | Lossless restoration or block |
| Unknown rule, invalid range, overlapping atom identity, non-local semantic rewrite | None | No | Block with RED report |

### Anchor parity is a blocking rule

Anchor parity is not stylistic:

- if the explicit RU anchor contains only ASCII/non-Cyrillic characters, EN
  must declare that exact anchor byte-for-byte; mismatch or a Cyrillic EN
  replacement is RED;
- if the explicit RU anchor contains Cyrillic, EN must declare a unique
  explicit English ASCII anchor. Deterministic code first derives it from the
  accepted English heading using the registered slug algorithm and collision
  suffix rule. A repair model may propose only the anchor string if that
  deterministic result is invalid or collides;
- code, not the model, applies the chosen anchor and rewrites every inbound
  link inside the staged transaction. It then scans the complete pinned docs
  tree and validates every inbound reference;
- if an inbound link outside the authorized staged file set would require a
  mutation, block with exact path/href provenance. Never expand scope silently.

### Minimal replacement protocol

Process one finding at a time. The repair request contains only:

- the stable target block and the smallest self-contained Markdown/YFM
  container required to parse it;
- the one canonical finding, rule, severity, exact range, and acceptance
  condition;
- immutable protected tokens standing for all source-owned atoms;
- only the glossary, heading ancestry, adjacent sentence needed to resolve a
  pronoun, and source RU prose corresponding to this block.

It contains no other editable block. The repair model returns exactly:

```yaml
finding_id: <canonical id>
block_id: <same id>
replacement: <complete replacement prose for that one block>
```

The validator requires the same block ID, exact finding ID, valid schema,
unchanged protected-token sequence/multiset/nesting, and no edit outside that
block. For ranged prose findings, a diff guard additionally requires every
changed byte to intersect the declared range plus at most one complete
sentence on either side included in the request. For `english_anchor`, the
replacement field contains only one valid ASCII anchor token; it contains no
prose or link edit. Any violation consumes the attempt and inserts nothing.

After a valid replacement, insert only that block, deterministically restore
its protected atoms, run all global atom/Markdown/YFM/link/fragment/anchor
invariants, then run a new whole-document critic evaluation. The original
canonical finding must disappear and no new RED finding may overlap the
repaired block or its atoms. Otherwise the attempt failed.

### Limits, ordering, conflicts, and oscillation

At job start also pin two distinct slugs for each local role:
`critic_primary_model`/`critic_fallback_model` and
`repair_primary_model`/`repair_fallback_model`. All six configured role slots
are immutable in the manifest; slugs may coincide across roles, but primary
and fallback within one role must differ. Each logical translate, critic, or
repair call uses the same four-network-attempt classification table with only
its own role pair. There is no cross-role fallback, dynamic chain, or use of a
critic response as a repair response.

- maximum two logical repair attempts per canonical finding. A logical attempt
  begins when its repair request is issued and is consumed whether acquisition
  exhausts, the response violates schema, a range/atom guard rejects it, global
  invariants reject it, re-critic retains the finding, or it succeeds;
- maximum four distinct model-repairable findings per document;
- maximum eight repair-model logical calls per document;
- initial critic plus one whole-document re-critic after each inserted
  candidate: maximum nine accepted critic evaluations per document;
- technical network retries inside one repair logical attempt do not create an
  additional repair attempt, but the logical attempt is consumed even if no
  protocol-valid candidate is acquired;
- acquisition exhaustion for critic or repair blocks the document.

Order the current finding set by severity (`RED` first), then stable document
block order, `range.start`, `rule_id`, and canonical `finding_id`. Process only
the first eligible finding. After every insertion discard the remaining queue
as stale, re-critic the whole document, canonicalize again, and recompute order.
Never repair overlapping findings together and never ask one replacement to
solve multiple canonical IDs.

Keep a per-block set of normalized content hashes and a per-finding sequence of
`(canonical_finding_id, block_hash)`. A proposed replacement equal to any prior
block state, a repeated pair, A→B→A, or reappearance of the same finding when
its second logical repair attempt has been consumed is oscillation and blocks
immediately. A newly introduced
RED in the same block counts as failure of the current attempt, not a new
budget that can evade the two-attempt limit.

Success requires all RED findings resolved, global invariants green, and all
limits respected. After two unsuccessful logical repair attempts for a finding, or
after any document cap, conflict, oscillation, non-lossless restoration, or
out-of-scope anchor-link mutation, publication blocks. The red report lists the
canonical finding, zero to two actually issued repair attempts and their block
hashes/outcomes, violated rule, exact range/atoms, and required manual action.
An early terminal condition does not synthesize an attempt record.

The blocking repair report schema is:

```yaml
category: local_repair_failed
source_file: <RU path>
output_file: <EN path>
finding_id: <canonical id>
rule_id: <registered rule>
severity: RED
block_id: <stable block>
range: {start: <byte>, end: <byte>}
atom_ids: [<ids>]
attempts: [] # append exactly one record per issued request; length 0..2
terminal_reason: attempts_exhausted | document_cap | conflict | oscillation | global_invariant | out_of_scope_link
manual_action: <specific prose/anchor/link action>
```

Every member actually present in `attempts` has exactly `ordinal` (1 or 2),
`before_hash`, `candidate_hash` (hash or null), and `outcome` (one of
`acquisition`, `schema`, `range`, `atom`, `invariant`, `recritic`, or
`oscillation`). Ordinals are contiguous and equal issuance order. Zero members
is required when the terminal condition precedes any repair request; one is
required after one issued request; two is required after two issued requests.

## Unresolved dependency representation

`budget_exceeded` and `missing_source` affect dependency creation, not atom restoration. A Markdown link atom in the referring EN file is always restored to a human-readable Markdown link with:

- translated label prose;
- the original source-owned href spelling, including query and fragment;
- no `⟦...⟧`, UUID token, encoded token, or internal protocol syntax.

This preserved original link is the safe final representation. Do not invent a destination, remove the wrapper, convert it to a hidden token, or insert machine jargon into article prose.

An unresolved YFM include is likewise restored as its exact original,
human-readable directive. It is never replaced by a protect token or invented
EN path. Its blocking warning uses the include target as `original_href` and
sets `dependency_kind: yfm_include`; link warnings set
`dependency_kind: markdown_link`.

The job must additionally emit a blocking structured QA warning for every unresolved occurrence. The structured warning report is the only inspectable artifact from a failed or unresolved job. Staged EN files are never uploaded, attached to a PR, committed, pushed, or otherwise published.

Each warning must contain:

```yaml
category: unresolved_translation_dependency
dependency_kind: markdown_link | yfm_include
source_file: <referring RU file>
output_file: <mapped EN file>
original_href: <exact href spelling>
resolved_ru_target: <canonical RU path or null>
resolved_en_target: <canonical EN path>
reason: budget_exceeded | missing_source
manual_action: <translate/add the named RU target, fix the href, or explicitly add an EN counterpart>
```

Aggregate identical occurrences only when all fields except source location are identical; retain a list of every source line/location. The human report must list these fields, the `20/20` auto-added count when exhausted, and state that no protect token was published.

## Output transaction

Stage all generated EN files in memory or a temporary job-owned directory.
Commit/push only if every queued file accepted one protocol-valid payload,
passed pre-render checks, rendered once, restored every atom, contains no
protect token, completed bounded critic-repair within all caps, has no
unresolved RED finding, and passes all global atom/Markdown/YFM/link/fragment/
anchor invariants. Repair exhaustion, conflict, oscillation, or cap overflow is
a terminal transaction failure.

If any file fails or any unresolved dependency warning exists, do not commit a partial translation branch. Report all completed and failed queue entries and discard only the job-owned staging area. Existing repository files remain untouched.

The final touched EN set must equal the locale counterparts of the successfully planned initial plus auto-added RU queue, subject only to the existing explicit deletion/navigation workflow. Old EN content must not add extra touched paths.

## Migration plan

Implement in this order without a dual-runtime period:

1. Introduce the lossless atom/prose document contract and single-result file
   translator with bounded acquisition behind tests, without wiring production.
2. Introduce deterministic dependency planning, canonical resolution, the separate `initial_count` and `auto_added_count`, structured unresolved records, and transactional staging.
3. Replace the production translate entry point atomically with the new queue and one-pass translator.
4. Delete differential/full selection, magnitude, seed/alignment, splice,
   reconstruct-from-EN, unbounded/cross-block repair, post-acceptance full-file
   translation, and verify-retranslation production code and configuration.
   The bounded one-block repair controller is the sole post-render model writer.
   Delete all calls from
   `harness/render.py` to `translate_cyrillic_fence_comments_with_client`,
   `translate_cyrillic_text_fences_with_client`, and
   `translate_cyrillic_prose_with_client`; no equivalent post-render model call
   may replace them.
5. Delete EN-writing fragment and structural repair paths from translation and
   verification, including production calls to `repair_en_fragments`,
   `prefer_baseline_href_when_fragment_missing`,
   `add_explicit_ascii_fragment_anchor`, and
   `repair_en_structure_from_ru`. Replace them with read-only QA checks. Exact
   atom restoration plus the declared Cyrillic-anchor localization and
   deterministic inbound-link updater are the only source-owned writers.
6. Replace all existing model client retry/model-chain behavior with one
   role-aware acquisition controller implementing the exact
   two-model/four-attempt table above for each logical translate, critic, and
   repair call. Pin the six role slots in the job manifest. The low-level client performs exactly one network attempt for the
   explicit model slug it receives and contains no retry or fallback. Remove
   arbitrary fallback arrays, environment-appended fallbacks, role-chain
   expansion, error-dependent chain mutation, and backward-compatible aliases.
   Critic model configuration may remain only for the structured read-only
   critic. The critic cannot affect bytes or invoke translation/repair; only
   the controller may invoke the separate repair role after validating a
   finding.
7. Delete or rewrite tests, fixtures, documentation, logs, metrics, and Memory Bank sections that describe removed modes. Searches for the removed mode/config names must find no production references.
8. Keep verification read-only and update reports to show queue origin
   (`initial` or `auto_added`), every acquisition attempt, the sole accepted
   payload or terminal failure, render count (`0` or `1`), dependency budget
   use, and unresolved records.

Do not ship a flag that can reactivate the old architecture. Rollback is by reverting the release, not by retaining hidden fallback code.

## Acceptance criteria

1. Every initial and auto-added RU file has zero or one accepted prose payload
   and zero or one RU-derived render. A file renders exactly once only after a
   payload is accepted and all pre-render checks pass. Acquisition or
   pre-render failure renders zero times. No file renders more than once. It
   may use only the bounded technical acquisition table, with no more than four
   network attempts.
2. For fixed `source_tree_sha`, `source_base_sha`, `publication_tree_sha`, model
   attempt outcomes, and EN existence/blob metadata, generated EN is
   independent of old EN text because that text is never loaded. Changing an
   EN blob changes provenance metadata and blocks before translation rather
   than changing generated output.
3. Creating, deleting, or modifying an EN counterpart between source base and
   publication tree blocks through the provenance guard. It never causes reuse
   of that EN, a different prompt, or a merged output.
4. All enumerated non-prose atoms round-trip exactly from authoritative RU bytes, except the defined `/ru/` to `/en/` locale mapping where the syntax explicitly requires an absolute locale counterpart.
5. No internal protect token, escaped token, or percent-encoded token can reach staged EN.
6. Recursive missing-EN dependencies are added breadth-first and deterministically, with at most 20 auto-added files. Initial files are not counted.
   Markdown links and direct YFM `.md` includes share that one counter.
7. Cycles, duplicates, aliases, and multiple fragments do not duplicate queue entries or translation calls.
8. At budget exhaustion or missing RU source, the readable original link is restored, the exact structured warning is emitted, and the transaction cannot publish or merge.
9. Acquisition exhaustion, deterministic quality failure, or restoration
   failure stages no file fallback and prevents commit of the entire
   translation transaction.
10. No incremental/full/magnitude/splice/old-EN-seed, full-file content rewrite,
    or post-acceptance full translation path remains. The only content edit is
    the bounded single-block repair protocol.
11. No post-render fence/Cyrillic model writer or legacy EN-mutating
    fragment/structural repair writer remains reachable. Model switching exists
    only inside the fixed role-scoped acquisition controller for a logical
    translate, critic, or repair call.
12. The provenance guard blocks every newer RU/EN collision before model use
    and never translates current tip or expands scope implicitly.
13. Critic repair is limited to two logical attempts per finding, four
    distinct repairable findings, eight repair logical calls, and nine accepted
    critic evaluations per document. It never edits two blocks together.
14. ASCII/non-Cyrillic RU anchor parity is exact and RED; Cyrillic RU anchors
    require an explicit English ASCII anchor plus globally consistent inbound
    links, with out-of-scope mutation blocked.

## Required test matrix

### One-call and old-EN independence

- one initial file with many prose segments: one payload per attempt, first
  protocol-valid result accepted, one render;
- transport timeout/429/5xx twice on primary then success on fallback: three
  attempts, one accepted payload, one render;
- one transient transport failure then primary success: two identical-payload
  attempts on primary, no fallback, one render;
- invalid protocol on primary then valid fallback: two attempts, rejected bytes
  never rendered, one accepted payload;
- invalid protocol on both models: two attempts and block; no same-model retry;
- four transport failures exhaust exactly two attempts per model and block;
- auth/400 configuration failure blocks immediately without fallback;
- protocol-valid base prose with a RED repairable prose finding enters only the
  bounded one-block controller; non-repairable quality findings block;
- multiple initial and 20 auto-added files independently obey the same bounded
  acquisition state machine;
- every acquisition/pre-render failure asserts accepted-payload count `0` and
  render count `0`; every success asserts counts `1` and `1`; no case can
  observe render count above `1`;
- critic emits malformed/unstable finding: no repair call and RED block;
- one eligible prose finding repaired on its first logical attempt, whole-document
  re-critic clears it, global invariants remain green;
- first logical attempt returns an invalid/out-of-range replacement and the
  second returns a valid replacement; exactly one insertion;
- two unsuccessful logical attempts, including acquisition/schema/guard
  failures, block with the exact red report;
- four distinct findings/eight repair calls/nine critic evaluations are hard
  document caps;
- overlapping findings are processed separately with queue rebuild; changed
  ordering after re-critic is deterministic;
- repeated block hash and A→B→A candidate sequence block oscillation;
- newly introduced RED in repaired block consumes current finding attempt and
  cannot acquire a fresh two-attempt budget;
- attempts to substitute arbitrary old-EN text without changing pinned blob
  metadata are impossible because EN content is never read; a real blob change
  is detected by provenance and blocks before any model payload is sent.

### Atom round trip

- Markdown links with translated labels, titles, nested inline code, query plus fragment, `[{#T}](...)`, external URLs, autolinks, and percent-encoded paths;
- explicit heading anchors and local fragment links;
- inline code, fenced/indented code, YAML/YQL/JSON/shell configs, fence info strings, and Cyrillic code metavariables restored byte-for-byte;
- YFM includes, variables, notes, tabs, cuts, conditions, tables, HTML, images, dimensions, assets, macros, and reference definitions;
- literal, escaped, HTML-escaped, and percent-encoded internal token leak detection.
- ASCII RU explicit anchor mismatch is deterministically restored and rechecked
  as RED, never sent to repair model;
- Cyrillic RU explicit anchor gets deterministic English slug, all in-scope
  inbound links update, and out-of-scope inbound mutation blocks;
- deterministic slug collision permits only a constrained repair-model anchor
  token, followed by the same global link-integrity scan;

### Dependency closure

- relative, docs-rooted, and `/ru/` absolute Markdown links map to the exact canonical EN counterpart;
- fragment variants enqueue one target and preserve every href;
- duplicate links and normalized aliases enqueue and translate once;
- self-cycle, two-node cycle, and larger cycle terminate;
- existing EN target is not queued and its content is never read;
- merged and open PR fixtures prove that RU reads and EN existence checks use
  the same immutable merge/head `source_tree_sha`, even when default branch,
  translation branch, working tree, and merge base contain different EN paths;
- missing EN plus existing RU recursively queues breadth-first;
- direct YFM `.md` include with missing EN plus existing RU enters the same
  BFS; duplicate includes and include-plus-link aliases enqueue once;
- missing RU emits `missing_source` with every required report field;
- exactly 20 unique auto-added files succeed; the 21st emits `budget_exceeded`; initial files never affect the count;
- link and include dependencies compete for the same 20 slots in deterministic
  discovery order; the 21st include emits `budget_exceeded` with
  `dependency_kind: yfm_include`;
- external, protocol-relative, fragment-only, mail, assets, and non-`.md` targets never consume budget.

### Transaction and removal

- failure in the last queued file commits none of the staged files;
- unresolved dependency commits none and exposes readable restored links without protocol markers;
- failed or unresolved jobs expose only the structured warning report; tests
  prove no staged EN artifact upload, PR attachment, commit, or push occurs;
- final touched set equals planned locale counterparts;
- static searches and configuration-schema tests prove removed
  incremental/full/magnitude/splice/seed/full-file rewrite and dynamic fallback
  controls are absent; the bounded block controller is the only repair writer;
- static searches and action-planning tests prove no RU Markdown path can enter
  EN-to-RU, bilingual, `critic_only`, semantic-noop, skip-glob, allowlist, or
  navigation-preserve routes;
- static searches and call-graph tests prove the three render-time translation
  helpers and named fragment/structural repair writers are absent; low-level
  clients make one explicit-model network attempt; only the fixed acquisition
  role-scoped controller can retry or advance from primary to fallback for
  translate, critic, and repair logical calls; tests reject cross-role routing;
- critic cannot mutate bytes or invoke models; only the validated controller
  invokes repair. Verify outside the controller cannot invoke translation or
  repair and cannot modify final bytes.

### Provenance and stale-source protection

- merged source with unchanged RU and EN blobs at publication tip proceeds;
- open source PR whose publication RU equals `source_base_sha` proceeds using
  head RU as input;
- newer RU edit after a merged source blocks before model use and reports the
  touching commit;
- newer EN edit, newly created EN counterpart, deleted EN counterpart, and EN
  path changed by the source PR each block before model use;
- auto-added dependency changed on current main blocks before model use;
- diverged ancestry blocks deterministically;
- a blocked stale job never substitutes publication-tip RU and never adds its
  newer files to scope;
- an explicitly new source request for current RU tip is a distinct job with
  new pinned SHAs, not a continuation or fallback of the stale job.

## Verification commands

The implementation must add focused suites for the new planner, protector/restorer, one-pass runner, transaction, and migration removal, then run:

```bash
uv run pytest -q tests/unit/test_one_pass_translation.py tests/unit/test_translation_dependency_queue.py tests/unit/test_atom_round_trip.py tests/unit/test_translation_transaction.py
uv run pytest -q tests/unit
uv run ruff check src/ydbdoc_review tests
git diff --check
```

## Strict scope

No manual edits to generated YDB documentation, no filename/PR-specific
production rules, no unbounded dependency crawl, no translation of
code/config/directive atoms, no reuse of old EN bytes, no second full-file
translation, no cross-block/model-driven structural edit, and no partial
publication. Only fixed pre-acceptance acquisition and the bounded one-block
critic-repair protocol are permitted.

## Decision gate

This v010 document is a policy proposal. Implementation is not authorized by
the specification or reviewer verdict. Development may start only after both:

1. the user explicitly accepts bounded acquisition, stale-source protection,
   and the bounded local critic-repair policy with its 2/4/8/9 limits, repair
   eligibility, oscillation guard, and anchor-parity exception;
2. the external file reviewer returns `APPROVED` for v010.

## Memory Bank after accepted implementation

Replace the removed differential/full documentation with the single-accepted-result invariant, exact bounded acquisition table, protected-atom grammar, provenance guard, dependency closure and 20-auto-added-file counter, unresolved-link reporting contract, and all-or-nothing publication rule.
