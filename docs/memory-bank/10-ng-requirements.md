# Memory Bank: simplified NG requirements

> Part of the [Memory Bank index](../../MEMORY_BANK.md).
> Authoritative product contract for the new simplified translation pipeline.

---

## 23. Status and authority

This section contains the requirements confirmed with the user on 2026-08-27.
It supersedes conflicting historical implementation decisions for the NG work.
Historical §6 entries remain useful as regression evidence, but are not authority
for NG behavior when they disagree with §23.

Requirements discovery must finish before deciding whether to retrofit the current
code or write a clean implementation. No NG implementation starts before that gap
analysis and explicit user approval.

### Requirements handoff process

- This project Memory Bank is the only authoritative requirements source. Do not
  maintain a separate assistant-specific knowledge bank for this project.
- Every confirmed decision from requirements discussion is added to §23.
- After the initial discussion is complete, start a new analyst in a clean context
  and provide only the project Memory Bank.
- The analyst must study the complete contract, identify contradictions or missing
  decisions and ask questions until the implementation task is unambiguous.
- When the analyst has no unresolved requirements, perform a separate gap analysis
  comparing retrofit of the current code with a clean rewrite.
- Present that comparison and recommendation to the user. The user chooses the
  implementation strategy.
- Only after that choice is the final task handed to a developer. Requirements
  analysis and strategy selection must not silently turn into implementation.

Return point before NG work: immutable tag `pre-ng-2026-08-27` at
`1f04ab1c71488f53c4ad547c20c7e635d59696ad`.

## 23.1 Commands and lifecycle

### `doc_translate`

- Production translation starts by applying the `doc_translate` label.
- Eliza is a local fallback, not the default label workflow.
- Only a merged source PR may run `doc_translate`.
- Open and closed-unmerged PRs are rejected before scope planning, budget use and
  model calls.
- The official merge snapshot is mandatory. PR HEAD and ambient worktree are not
  substitutes.
- The source PR selects paths and initial direction. Content is taken from an
  immutable snapshot of current `main` at run start.
- The output is always a Draft PR. Automation never changes Draft to Ready and
  never merges it.

The source operation manifest is derived identically for merge, squash and rebase:

1. GitHub API must report the source PR as merged and provide final
   `merge_commit_sha`, `base.sha` and `head.sha`.
2. Fetch every page of the GitHub Pull Request Files API.
3. Persist each path, file status and `previous_filename` for a rename together
   with those three SHAs as the immutable manifest.
4. Expand `renamed` into independent delete of `previous_filename` and add of the
   new path.
5. Local checkout or git diff may validate data but cannot add or remove manifest
   operations.

Missing required SHA, incomplete pagination, rename without `previous_filename`
or contradictory API data blocks the run before model calls and produces a clear
Russian operational report.

A repeated `doc_translate` on an unfinished translation is an explicit clean
restart:

1. Run ACL and budget gates before destructive actions.
2. Close the old Draft with a clear replacement comment.
3. Delete its remote translation branch.
4. Discard accumulated `doc_continue` decisions and its continue counter.
5. Create a new lineage, branch and Draft from current `main`.
6. Retain the old transcript for the 14-day audit period.

The translation branch name is deterministic: `ydbdoc-review/pr-N`, where `N`
is the merged source PR number. A clean restart deletes the old branch and then
creates a new branch with the same name. There can be only one active translation
branch and one active Draft for a source PR, but the new Draft is a new PR and a
new lineage.

If the previous translation PR is already merged, the lineage is terminal.
Another `doc_translate` creates nothing and reports that the translation was
already merged. A new source PR is required for a new translation.

### `doc_continue`

- `/ydbdoc continue` continues an active translation lineage.
- A continue run is a destructive rebuild. It discards every translation-branch
  file and commit, including manual changes.
- It starts from a new immutable snapshot of latest `main`.
- It reuses the original merged PR scope and directions.
- It replays all accumulated structured operator decisions and adds the new
  natural-language instruction.
- It force-updates the existing Draft branch.
- Human edits should be made only after the last intended continue run.
- A lineage permits at most three continue runs.
- One continue comment may resolve multiple reported questions.
- Missing or expired lineage context never degrades into a context-free continue.

### `doc_verify`

- `doc_verify` is allowed only on open PRs.
- It is read-only for repository content.
- It never creates or deletes a branch or PR, never commits, never pushes and
  never applies a proposed repair.
- It always posts a report, including on a technical verifier failure.

### Concurrency

- At most one job may run for one source PR or translation lineage at a time.
- A concurrent `doc_translate` or `doc_continue` is not queued and makes no model
  calls or branch changes.
- The bot replies in clear Russian that work is already running and shows the job
  type, start time and workflow link.
- The user may repeat the command after completion.
- A stale per-source lock expires after two hours.
- Work on an unrelated source PR is not blocked by this lock.

### Translation lineage state table

- Open Draft with existing branch: `doc_continue` is allowed.
- Open PR changed by a human from Draft to Ready: `doc_continue` is blocked to
  prevent force-pushing unreviewed bytes into a Ready PR. The bot asks the human
  to convert it back to Draft first.
- Draft closed without merge: continue is blocked; a new `doc_translate` performs
  a clean restart.
- Open translation PR with missing branch: lineage is damaged; continue is
  blocked, while a new translate closes the stale PR and rebuilds from scratch.
- Merged translation PR: lineage is terminal forever; translate and continue make
  no content or branch changes.
- Valid lineage without a Draft: continue remains allowed in the merged source PR.
- Open Draft with manual commits: continue is allowed and, after its explicit
  destructive warning, discards those commits during full rebuild.
- Multiple active translation PRs for one source PR: every destructive command is
  blocked. The report asks a human to close duplicates and retry.
- Read-only `doc_verify` is allowed for every open PR, including a Ready PR.

### Command labels

- `doc_translate`, `doc_verify` and `doc_continue` are one-shot command labels,
  not persistent PR state.
- The bot removes the command label immediately after receiving its labeled event.
- It removes the label both when the job is accepted and when a gate rejects it.
- Removing the label does not cancel or alter an accepted job.
- A later run requires the user to apply the label again, which guarantees a new
  labeled event.
- The bot never removes unrelated persistent labels.
- Every accepted or rejected event receives a clear comment, so label removal
  cannot be mistaken for a successful launch.

A `/ydbdoc continue ...` comment supplies instruction but does not start CI by
itself. An allowed actor writes the comment and then an allowed actor applies the
`doc_continue` label. The workflow consumes the latest applicable unconsumed
continue instruction for that lineage.

## 23.2 Direction and full overwrite

Direction is determined independently for every RU/EN locale pair in the
original merged PR:

- only RU changed: translate current RU to EN;
- only EN changed: translate current EN to RU;
- both changed: run bilingual semantic classification to select no translation,
  RU authority, EN authority or an operator-required ambiguity;
- one PR may contain different directions for different paths.

For a pair where both locales changed, the cheap classifier compares the complete
current RU and EN files:

- `NO_TRANSLATION`: versions are equivalent, create no Draft for this pair and
  report that translation is not required;
- `RU_AUTHORITY`: RU is clearly complete or correct, perform the normal complete
  RU-to-EN overwrite;
- `EN_AUTHORITY`: EN is clearly complete or correct, perform the normal complete
  EN-to-RU overwrite;
- `AMBIGUOUS`: both contain unique meaning or authority is unclear, make no guess
  and ask the tech writer to select authority through continue.

A selected overwrite enters the normal shared verification and two-repair loop.
If all pairs are `NO_TRANSLATION`, no Draft is created and the source PR receives
the short verification report. If a pair is ambiguous and no safe diff exists,
lineage remains on the source PR until continue supplies authority.

For add and update, the complete current source file is translated and the target
is completely overwritten. NG does not compute a historical delta, perform a
three-way merge, preserve current target prose or retain target-only manual
content. Later source changes already present in current `main` are intentionally
included.

For an original add or update, if the source path selected by the original
direction no longer exists in current `main`, the operation is `SUPERSEDED` and
changes nothing. Historical source is not restored. Explicit deletes use the
separate rule in §23.7. Remaining orphan or TOC defects are left to QA and the
external documentation build.

If the final safe diff is empty, no translation PR is created. The source PR gets
a clear comment explaining why translation is not required, with reasons per
path.

Some blocked runs may create lineage but no Draft because no safe diff exists yet.
Until a Draft exists, `/ydbdoc continue` is accepted only in the merged source PR
and lineage is resolved by that source PR number. The source comment must clearly
explain:

- that translation was started but no Draft could be created;
- the exact blocking reason and affected files;
- that the lineage is stored for 14 days;
- that continue must be written in this source PR;
- a ready command or concrete answer example;
- that the first continue producing a safe diff will create a Draft;
- the remaining continue attempts.

After a Draft is created, later continue commands are accepted only in the active
translation PR. A command posted in the wrong PR performs no model work and replies
with a clear Russian explanation and a link to the correct comment location.

## 23.3 Semantic no-translation classifier

After deterministic gates, a cheap model may decide with high confidence that
there is no user-facing text to translate.

- A confident semantic no-op creates persistent lineage, posts the explanation
  on the source PR and creates no Draft.
- Timeout, malformed response, model error or uncertainty means translation
  continues. The filter is fail-open.
- For a one-locale source operation, `/ydbdoc continue всё равно переводи`
  records `force_translation=true`, skips the negative semantic verdict and
  spends one continue attempt. Its translation direction is already known.
- The override is retained by subsequent rebuilds.
- It overrides only the semantic classifier. It does not override merged-only,
  single-language rules, an unresolved bilingual authority choice, `SUPERSEDED`, ACL, budget,
  safety gates or continue limits.

For a bilingual pair classified as `NO_TRANSLATION`, `doc_translate` creates a
continue-capable no-Draft lineage retained for 14 days. The source-PR report
presents exactly two ready choices for each affected canonical locale pair. Each
choice includes the exact source path, for example:

```text
/ydbdoc continue всё равно переводи с русского ydb/docs/ru/core/a.md
/ydbdoc continue всё равно переводи с английского ydb/docs/en/core/a.md
```

The operator chooses only after reading that report. The selected locale is
stored with `force_translation=true` for that exact canonical pair. Every later
`doc_continue` in the same lineage replays both authority and force flag, so an
unrelated later decision cannot cause the pair to return to `NO_TRANSLATION` or
disappear from the rebuilt Draft. The following `doc_continue` performs the
destructive rebuild and translation without another authority question. A bilingual command
without a path is accepted only when exactly one bilingual pair in the lineage is
waiting for this decision. With multiple waiting pairs it is rejected before
snapshot, model or branch work, and the bot repeats the path-specific commands.
One continue comment may contain decisions for multiple exact paths. A rejected
ambiguous instruction does not consume one of the three continue attempts.

The same scoping rule applies to one-locale semantic no-op pairs: reports provide
path-specific force commands, and a pathless force command is accepted only when
exactly one pair in the entire lineage is waiting for any force-translation
decision. A fresh clean `doc_translate` discards all pair-specific force and
authority decisions with the old lineage.

Cheap semantic classification uses a configured ordered fallback chain. A timeout,
malformed response or technical failure advances to the next classifier model.
For a one-direction pair, exhaustion remains fail-open because direction is known
and normal translation can proceed. For a bilingual pair, exhaustion cannot pick
authority safely: the pair is red, no candidate is generated, and the source
comment clearly says that every classifier failed. The operator may retry through
continue. A valid `AMBIGUOUS` answer is a product verdict, not a technical failure,
and produces the authority question without cycling through models merely to find
a preferred answer.

## 23.4 Add, update, delete and redirects

NG v1 has no logical move operation. GitHub rename and `removed + added` are
processed as independent delete and add operations.

For a source delete:

- delete the target mirror;
- remove the exact target href from the corresponding target TOC;
- create a redirect for every href removed from a TOC;
- do not scan every Markdown file for inbound links;
- try to determine an old-to-new redirect only from clear evidence;
- never guess a redirect destination.

If old-to-new mapping is uncertain, publish every safe independent change in a
Draft and produce a red report with a concrete question. A tech writer may answer
through `/ydbdoc continue`. The answer becomes an exact structured mapping for
this lineage and is replayed on the next full rebuild.

Remaining inbound links are discovered by QA or the external docs build. A tech
writer may use continue to name exact locations that must be removed or retargeted.

Accepted automatic redirect evidence is limited to:

1. An exact old-to-new mapping explicitly added by the source PR in
   `ydb/docs/redirects.yaml`.
2. An unambiguous replacement of the exact old href by the exact new href in the
   same source TOC position.
3. An exact mapping supplied by an authorized tech writer through continue.

Text similarity, filename similarity and topic similarity are not evidence. The
resolved destination must exist in the immutable current-main snapshot or be
created by the same safe operation bundle.

An identical existing target-locale redirect is a no-op. A conflicting existing
destination is never overwritten and produces an operator question. NG does not
collapse redirect chains. It does not remove an old redirect unless the original
source PR removed that exact mapping and the target copy still matches it.

Every NG-generated TOC href removal requires a resolved redirect. Redirect,
target-file deletion and TOC removal belong to one atomic bundle. If destination
cannot be resolved, the complete removal bundle is omitted and the Russian report
asks the tech writer for the exact destination. Independent safe bundles may still
be published in the red Draft.

## 23.5 TOC

Only minimal scoped TOC edits are allowed:

- add the missing target href for a scoped add;
- remove the exact target href for a delete;
- translate only the label of that entry;
- place it under a target parent only when the parent is unambiguous.

NG never rebuilds or mirrors a whole TOC. Ambiguity creates a red structured
question in clear Russian. The question names the exact TOC, candidate parents,
label or duplicate entries. The operator answers in natural language through
continue. The decision is stored only in that lineage and replayed on rebuild.

There is one explicit exception when the corresponding target TOC file does not
exist. If a source TOC exists, NG unconditionally creates the target TOC at the
same locale-relative path. It mirrors the complete source hierarchy and order,
translates labels and mirrors hrefs to the target locale. Missing target TOC is
therefore not an operator conflict.

If the target TOC already exists, NG does not overwrite or fully mirror it. Only
scoped entries are changed. For an insertion, NG selects the mirrored source TOC
path and exact mirrored ancestor href chain. It inserts after the nearest previous
source sibling already present in target, otherwise before the nearest following
sibling, otherwise at the end of the one unambiguous parent. Multiple target TOCs,
parents, duplicate hrefs or contradictory sibling order produce an operator
question.

When a newly created target TOC contains a source entry whose target page does not
exist and is outside translation scope, NG retains a working source-locale href.
This is yellow, not red. The Russian report names the TOC, line and href, permits
merge, and recommends a separate PR to replace the link after that page becomes
available in the target language. A link into `single_language_patterns` is
preserved without a warning.

## 23.6 Single-language manifest

The central manifest initially contains only:

```yaml
single_language_patterns:
  - public-materials/*
```

Patterns apply to locale-relative paths and the complete subtree in both locales.
Matching pages are not translated, do not require mirrors and do not require
RU/EN TOC parity. They are reported as `SKIPPED: single_language`.

Matching pages are completely opaque to NG. It does not copy or validate their
content, follow their includes or assets, inspect Cyrillic, check links or
reachability, or validate their TOC entries. Links from other scoped documents to
these pages are preserved exactly as written and produce no red or yellow issue.
The technical report may show only a count of paths skipped by the manifest,
without analyzing them. NG does not infer or add patterns automatically.

## 23.7 Dependencies, images and companion files

The dependency closure starts from every scoped source document and follows only
explicit locale-local include and image references. It does not scan neighboring
directories. Canonical paths are processed once and cycles stop without error.

A companion file enters scope only when at least one of these conditions holds:

1. The file is explicitly added, modified or deleted by the original merged PR.
2. A parsed local `{% include %}` points to it.
3. A parsed ordinary local Markdown link points to it and its extension is in the
   approved companion allowlist.

Plain-text path mentions, comments, code-fence strings, HTML and unknown syntaxes
do not expand scope and are not guessed with regular expressions. An ordinary
link to another Markdown article does not expand scope.

Recursive traversal follows this exact matrix:

- a root Markdown/YFM document and a Markdown documentation include may follow
  parsed `{% include %}` nodes, parsed image references, and parsed ordinary local
  links whose destination extension is in the companion allowlist;
- an ordinary link to another Markdown article never expands scope;
- YAML, JSON, TXT, C and C++ companions are leaves and never introduce another
  dependency, even when their contents resemble paths or includes;
- images are leaves;
- TOC files use the separate navigation contract and do not expand article
  dependency closure;
- HTML, code fences and plain-text path mentions introduce nothing.

Every followed include, image or companion edge increments depth by one. A
companion changed directly by the original source PR is a root at depth `0`.

- The source article has depth `0`.
- Default maximum depth is `3`.
- A depth overflow is red and shows the complete path chain in Russian.
- The report includes a ready `/ydbdoc continue` example to permit a larger
  depth for that exact article and chain.
- A depth exception is stored as a numeric `max_depth` for the complete dependency
  closure of one exact root article in the current lineage. It does not raise the
  global default or affect another root.
- Maximum unique dependency files per source article comes from
  `YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`, currently `100`.
- The file-count limit is hard and cannot be overridden by continue.

The count is calculated separately for each root article. The root article itself,
TOC files and redirect files do not consume the limit. Every unique Markdown
include, image and companion file does consume it. Repeated occurrences in one
closure count once. A dependency shared by two roots counts once in each root's
closure. A standalone companion changed directly by the source PR is its own root
and does not consume another article's dependency allowance.

When traversal exceeds the effective depth, the user-facing Russian report must
show the exact chain, default and required depth, root article and a ready command,
for example:

```text
/ydbdoc continue разреши глубину 5 для ydb/docs/ru/core/article.md
```

The next rebuild applies that value to the whole closure of this root. If it is
still insufficient, a later continue may set a larger number. The hard file-count
limit remains unchanged.

Images `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` and `.svg` are copied from source
locale to target locale byte-for-byte in either direction. OCR is not used. Text
inside raster images and SVG is not analyzed or translated. The report lists
source path, target path, size and content hash. Byte and hash equality are
mandatory.

Image pairs never enter the semantic or bilingual text classifiers. If exactly
one locale image changed in the source PR, that image is authoritative and is
copied byte-for-byte over the paired locale image. If both locale images changed,
equal content hashes require no candidate change. Different hashes are a red
authority question for that image bundle; NG does not inspect pixels or guess.
The report provides both ready commands with the exact path:

```text
/ydbdoc continue используй русское изображение <путь>
/ydbdoc continue используй английское изображение <путь>
```

The operator choice is stored in lineage and the selected bytes overwrite the
other locale on rebuild. Until then, that atomic bundle is omitted. A semantic
no-op or `force_translation` decision never changes image behavior.

Main Markdown/YFM pages and locale-specific Markdown includes are translated
fully in either direction.

Companion text allowlist:

```text
.yaml .yml .json .txt
.c .cc .cpp .cxx .h .hh .hpp .hxx .inc
```

For RU to EN companions, NG translates only the human-language locations allowed
for that file type without changing syntax, keys, identifiers, paths, URLs,
placeholders or technical literals. Residual Cyrillic is blocking only in a
location that the file-type policy says must be translated. Damaged syntax is
always blocking. After two failed safe attempts, the unsafe companion output is
omitted while independent safe changes may remain in the Draft.

For YAML, including Markdown YAML front matter, the only translatable locations
are comments and scalar values whose exact key is `title` or `description`. Every
other YAML key and value is preserved exactly, even if it contains Cyrillic. The
verifier treats such preserved values as intentional and does not report residual
Cyrillic there. Comments, `title` and `description` are translated RU to EN. For
EN to RU companion YAML the general as-is rule still applies. Markdown front
matter remains part of the documentation, so its comments, `title` and
`description` follow the document direction in both directions.

The remaining RU-to-EN companion matrix is explicit:

- `.json`: translate Cyrillic string values, never object keys;
- `.txt`: translate all natural-language Cyrillic text;
- C and C++ source or headers: translate comments and string literals that contain
  user-facing human text, never code or identifiers;
- `.yaml` and `.yml`: translate only comments and values of exact keys `title` and
  `description`;
- Markdown/YFM and documentation includes: perform the full document translation.

For EN to RU, every companion format is copied as-is. Markdown/YFM documents,
documentation includes and their front matter are the exception and are fully
translated according to document rules.

### Code fences inside Markdown

- Fence boundaries, declared language, commands, code, identifiers and technical
  values are preserved exactly.
- For RU to EN, Cyrillic comments inside a code fence are translated.
- A string literal is translated only when it is clearly a user-facing message,
  for example an error shown to a user.
- Example data, SQL values and ambiguous string literals remain unchanged and
  produce a yellow warning with exact file, line and fragment.
- For EN to RU, code-fence content remains in English.
- Verifier checks exact technical-token preservation and fence structure after
  every repair.

For EN to RU companions, the complete file is copied as-is. English comments and
technical prose are acceptable because programmers understand English. This
exception never applies to Markdown/YFM documentation or documentation includes.

An unknown required companion type is not guessed. It produces a red report with
the exact path.

Deletion is consistent across scoped Markdown documents, images and companion
files. If the original merged source PR deleted a locale-specific asset or
companion, NG deletes its target mirror. It checks references only inside the
current scoped dependency closure and never scans the full repository for inbound
links. Remaining references are left to the external docs build. Every deleted
target path is listed clearly in the report. A target already absent is a no-op.
The `public-materials/*` exclusion applies before this rule.

Every explicit delete in the original merged PR is a standalone root operation,
even though the deleted path cannot be discovered from current dependency
references. If that source path is still absent in current `main`, delete the
target mirror. If a later change recreated the exact source path, the old delete
is `SUPERSEDED` and changes nothing. Markdown delete also removes the exact target
TOC href. Image and companion delete removes only the target mirror. Reference
checking remains limited to closures of other current root documents.

## 23.8 External URLs

For RU to EN:

1. An already-English external URL is retained.
2. For a RU wiki URL, NG attempts an authoritative EN alternative only through
   the official Wikipedia interlanguage mapping from the canonical RU page.
3. If mapping is not found, or the URL is another RU external resource, NG places
   a stable placeholder in the translated Draft.
4. A placeholder is always red and cannot pass verification.
5. One exact original URL has one placeholder throughout the lineage.
6. The report lists every location and provides a command such as
   `/ydbdoc continue используй <EN URL> вместо <RU URL>`.
7. The answer becomes an exact source-to-target URL mapping for the lineage and
   is applied to every exact occurrence on rebuild.

The placeholder form is intentionally human-readable:

```text
https://ydbdoc.invalid/NEEDS-EN-URL-001
```

The reserved `.invalid` domain keeps Markdown syntactically valid while ensuring
the placeholder cannot resolve to an accidental real resource. The three-digit
number is assigned on first encounter in deterministic manifest and document
order, persisted in lineage and never renumbered by `doc_continue`. Further URLs
use `002`, `003` and so on; numbering may restart in a clean new lineage. One exact
original URL has one placeholder throughout a lineage. The original URL and every
occurrence remain in structured lineage and the human report. Any
`NEEDS-EN-URL-` placeholder is blocking and easy to find by text search.

For EN to RU, external English URLs are retained. Russian documentation may link
to English resources.

NG v1 automatically resolves only `ru.wikipedia.org` to `en.wikipedia.org` by
using Wikipedia's official interlanguage mapping. It does not infer an English
article from a translated title, matching slug or locale-domain substitution,
even if such an EN URL exists. Missing or ambiguous Wikipedia mapping produces
the normal unresolved URL placeholder. Every other wiki domain is treated as an
ordinary external resource and requires an exact operator mapping through
`doc_continue`.

Automatic Wikipedia mapping is confirmed only when the official Wikipedia API
returns an existing canonical EN page. Wikipedia redirects are followed to that
canonical URL. Timeout, `429`, `5xx` and network failure mean unresolved rather
than proof that a page is absent, so NG uses the normal placeholder and reports
the technical reason.

An exact URL supplied by an authorized tech writer through continue is
authoritative. NG validates only that it is a syntactically valid absolute
`https://` URL and does not make network availability a gate. Other external
links are not probed with generic `HEAD` or `GET` requests.

URL language and ownership are classified without downloading the destination:

- a relative or root-relative link that resolves below `ydb/docs/ru` or
  `ydb/docs/en` is an internal documentation link;
- an absolute `https://ydb.tech/docs/ru/...` or
  `https://ydb.tech/docs/en/...` link is also an internal documentation link and
  follows the same locale-mirror rules;
- an external URL is explicitly Russian only when its hostname starts with
  `ru.`, its path contains a complete `/ru/` segment, or its parsed query has
  `lang=ru` or `locale=ru`;
- an external URL is explicitly English only when its hostname starts with
  `en.`, its path contains a complete `/en/` segment, or its parsed query has
  `lang=en` or `locale=en`;
- `ru.wikipedia.org` then follows the dedicated Wikipedia rule; every other
  explicitly Russian external URL follows the unresolved-placeholder rule;
- URLs with an explicit English marker and language-neutral external URLs remain
  unchanged.

Matching is case-insensitive for hostnames, path-language segments and query
parameter names and values after standard URL parsing. Text fetched from an
external page and an LLM language guess never participate in this classification.

Internal `ydb.tech/docs/...` recognition is performed before external-language
classification. For every other external URL, NG collects all explicit language
markers from hostname, path and query. Only RU markers mean Russian; only EN
markers mean English; no markers mean language-neutral. If both RU and EN markers
occur, NG assigns neither a precedence nor a guessed language. The complete URL
uses the normal red unresolved placeholder and the report asks for one exact
replacement through `doc_continue`.

Wikipedia fragments are handled conservatively. A URL without a fragment uses
the resolved EN article. A fragment is retained only if that exact fragment
exists on the resolved EN page. NG does not translate or semantically match a RU
section title. If the exact fragment does not exist, the complete URL becomes an
unresolved placeholder and the report asks for a full EN URL including the
correct fragment.

### Internal documentation links

Internal Markdown links do not expand translation scope. Dependency traversal
continues to follow only includes and assets.

For RU to EN, NG rewrites a RU internal link to the mirrored EN path when that EN
target exists in the immutable current-main snapshot or is created by the same
translation candidate. If no EN target exists, NG keeps the working RU link. It
does not insert a placeholder and does not make the report red.

When an internal link has a fragment, NG rewrites the locale path only if the
mirrored target contains that exact fragment. If the mirrored page exists but the
exact fragment does not, NG keeps the complete working source-locale link,
including its original path and fragment. It never rewrites only the path and
never asks an LLM to match translated headings. The retained link is yellow and
the report gives its exact file, line, URL and a recommendation for a separate
link-fix PR after the target anchor exists.

The retained RU link produces a yellow warning with the exact target file, line
and URL. The Russian report says that the RU link was preserved because the EN
page is not available, merge is allowed, and recommends creating a separate PR
to replace the link after the EN page becomes available.

This rule intentionally prevents a deadlock when two independently merged source
PRs link to one another while both EN translation PRs are still unmerged. Both
translations remain mergeable in either order. NG v1 does not maintain a pending
link registry and does not automatically create a later cleanup PR.

For EN to RU, NG uses an existing RU mirror when available. If it is absent, the
EN link is retained because links from Russian documentation to English content
are allowed.

## 23.9 One verification core

Internal checking in `doc_translate` and `doc_continue`, and external read-only
`doc_verify`, must use exactly one pure verification core. Duplicate prompts,
validators, severity rules and verdict logic are prohibited.

The verifier receives a fully materialized immutable case containing source and
target snapshots, scope, directions, candidate overlay, pending deletes, manifest,
dependency closure, operator mappings and versioned configuration. It does not
read an ambient worktree or select another git ref.

The verifier performs no filesystem, Git or GitHub writes. It returns a stable
structured verdict, issues, evidence, suggested actions and metrics. Equal input
must produce equal verification semantics regardless of the caller.

The shared verification service has one explicit internal boundary. A pure
deterministic engine runs structural checks and interprets a materialized
structured critic response. A critic adapter performs the external LLM call and
returns that response. The pure engine itself never calls a model or the network.
All workflows use this same service and cannot substitute different interpretation
or severity logic.

`doc_verify` evaluates the actual bytes of the open PR. A hypothetical in-memory
repair can never make the unchanged PR green.

### Verification case identity and reuse

The verification case has a stable hash over exact source bytes, target bytes,
scope, directions, manifest, operator decisions, verifier rules, prompt versions
and model identifiers. The final structured critic response and interpreted report
are retained with that hash for 14 days.

If `doc_verify` receives the exact same case hash as the final internal verification
performed by `doc_translate` or `doc_continue`, it reuses that stored model result.
Deterministic validators still run, and the published report is identical for the
same case.

Any byte change in source or target, or any change to scope, configuration, prompt,
model or verifier version creates a new case hash. NG then runs the complete
verification scope again and does not reuse a partial per-file model verdict. This
full rerun is intentional and keeps the cache rule simple and safe. The new report
is bound to the new commit SHA.

## 23.9.1 Documentation glossary

The RU and EN documentation glossary pages are a mandatory locale pair and must
be maintained and harmonized. They are not an internal prompt-only word list.

- If only the RU glossary changed in the merged source PR, `doc_translate`
  completely synchronizes the current RU glossary into EN.
- If only the EN glossary changed, it completely synchronizes current EN into RU.
- If both changed in one PR, they are harmonized entry by entry rather than
  choosing one whole file as authority.
- A term present in only one glossary is blocking unless a future explicit
  single-language rule says otherwise.
- Verifier compares the term set, RU-to-EN names, meaning of definitions, links,
  placeholders and technical notation.
- NG does not carry forward historical special cases that skip glossary criticism,
  repair or writes.

The harmonized RU and EN documentation glossaries are also the terminology source
for translating every other article. NG derives term pairs from these pages. It
does not maintain a second manually duplicated terminology YAML as a separate
source of truth. The exact glossary snapshots used by a run are recorded in
reproducibility metadata.

Every translation opportunistically harmonizes glossary entries actually used by
its operation bundles. This is an explicit allowed scope expansion:

- for a RU-to-EN bundle, the current RU glossary entry is authoritative and the
  EN entry is added or fully synchronized from it;
- for an EN-to-RU bundle, the current EN entry is authoritative and the RU entry
  is added or fully synchronized from it;
- unrelated glossary drift is not changed by that run;
- glossary edits are included in the same Draft and pass the shared verifier;
- the report has a clear Russian section listing added and updated term entries;
- an unsafe glossary edit blocks only bundles that use that term.

If one term is required by opposite-direction bundles in the same run and the RU
and EN definitions differ, NG does not choose authority. It reports a red conflict
and asks the tech writer to select the authoritative locale through continue.
Repeated normal translations therefore harmonize the actively used glossary over
time without a separate mass rewrite.

Glossary usage is detected deterministically. An operation bundle uses an entry
only when a scoped Markdown/YFM document contains either:

- a parsed link to that entry's explicit glossary anchor; or
- the exact visible entry title from its glossary heading, compared with Unicode
  case folding and collapsed whitespace.

NG does not use stemming, morphology, fuzzy matching or an LLM to expand glossary
scope. Inflected forms such as `узел` and `узла` are different strings unless an
explicit glossary link identifies the entry. Bold terms, aliases and synonyms
inside an entry definition are not independently extracted as usage triggers.
Missing an opportunistic harmonization opportunity is not a verification issue.

When both glossary files changed and still differ, one RU/EN term-entry pair is
the atomic unit and an explicit exception to normal full-file overwrite:

- equal term entries are unchanged;
- an RU-only term is translated and added to EN;
- an EN-only term is translated and added to RU;
- when both definitions exist but differ, the cheap classifier may select the
  clearly more complete or correct entry as authority;
- an unambiguous entry is synchronized in the other locale;
- an ambiguous entry is omitted from changes and produces a precise authority
  question for continue;
- independent safe entry pairs may be published in a red Draft.

The shared verifier checks the resulting complete term set and definitions. This
entry-level exception prevents a bilingual glossary run from deleting useful
entries in either locale.

## 23.10 Two-model translation and repair loop

Translation and criticism use independent model roles:

1. Model A creates the initial translation.
2. Model B verifies it through the shared verifier.
3. If red issues exist, model B repairs only the reported problems.
4. Model A verifies the repaired candidate in a fresh context.
5. If red issues remain, model A performs the second and final repair.
6. Model B performs the third and final verification.

Model A and model B must have different model identifiers. Different prompts for
one model do not count as independence. Separate model families are preferred but
not required.

Translator and critic model identifiers come from configured ordered rotation
lists. At run start NG deterministically selects two distinct identifiers and
keeps that pair fixed for the complete run. A later `doc_translate` or
`doc_continue` advances the rotation and may use another pair. A technical
fallback is allowed only when the effective A and B identifiers remain distinct.
If no independent pair is available, the result is red and says in Russian that
independent verification is unavailable.

The report records every model call by role and pass: initial translator, first
critic and repair, second critic and repair, final critic, including any fallback.
It shows exact model identifiers, tokens and cost.

Every repair is followed by all deterministic checks and the same verifier.
There are at most two repair cycles and three verification passes. If red issues
remain, safe output is published in a red Draft and unsafe output is omitted.

Only issues explicitly classified as `model_repairable` enter this repair loop.
Repairable red issues include semantic loss or distortion, omitted prose,
incorrect terminology, residual translatable Cyrillic, and a lost Markdown
element, placeholder or technical token whose exact correct content is available
from the source.

Issues requiring an external fact or operator choice are never guessed by a
model. This includes unresolved external URL, ambiguous redirect, unknown TOC
parent or label, dependency-depth permission, direction conflict, missing source
or dependency, unsupported file type, ACL or budget rejection, expired context
and critic infrastructure failure. These issues do not start or consume automatic
repair attempts by themselves.

When repairable and operator-required red issues coexist, NG performs up to two
repairs for the repairable subset and retains the operator-required blockers in
the final report.

### Atomic safe publication

The atomic publication unit is an operation bundle rooted at one scoped document.
It contains the target document, every mandatory include, required images and
companion files, minimal TOC edits, and deletes or redirects belonging to that
root operation.

A bundle enters the Draft only as a complete deterministic-safe unit. NG never
publishes a TOC href without its page, a page without a mandatory include, or only
one half of a required delete and TOC update. If a mandatory member is unsafe, the
whole bundle is omitted. Independent safe bundles from the same source PR may be
published in the red Draft and are listed explicitly.

A dependency shared by multiple bundles is included when it is safe and required
by at least one published bundle. An unsafe shared dependency blocks every bundle
that requires it.

If the same canonical locale-pair dependency is reached from root bundles with
opposite directions, verifier first checks whether current RU and EN dependency
content is equivalent. Equivalent content requires no overwrite and creates no
conflict. If it differs, every bundle requiring that dependency is blocked. The
Russian report shows both dependency chains and the concrete difference. A model
never chooses authority. A tech writer selects the authoritative locale through
`/ydbdoc continue`, and that decision is retained only in the current lineage.

If a critic returns invalid structured output, NG asks that model once to repair
the format, then tries the configured fallback critic once. If no valid verdict
is produced, the result is red and clearly says that translation quality could
not be checked. It does not invent translation defects.

The bounded technical-failure state machine is:

- semantic classifier: try each configured classifier identifier once and stop at
  the first valid classification;
- initial translator: one selected model A call, then one eligible fallback
  translator call;
- critic: one primary call, one same-model format-repair call for malformed output,
  then one eligible fallback critic call;
- repairer: one selected repair call, then one eligible fallback repair call.

No hidden unbounded retries are allowed. If both translator calls fail, the
operation bundle is omitted and red. If a repair call and fallback fail, that
repair attempt is consumed, candidate bytes remain unchanged and the cycle moves
to the next verification pass. After the second consumed repair attempt, the safe
candidate remains in a red Draft. Critic exhaustion uses the existing red
unavailable-verification result. Every attempt, fallback, failure reason, returned
usage and actual cost is recorded in technical details.

User-facing model-failure messages are plain Russian. They say either that the
translation could not be produced or that its quality could not be checked because
the configured models did not return a valid result. They recommend trying again
later by applying `doc_translate` to the merged source PR. The message explicitly
warns that repeated `doc_translate` is a clean restart which closes the current
Draft, deletes its translation branch and discards current continue decisions.

## 23.11 `doc_verify` scope

- A bot translation PR is checked against the exact source snapshot, paths and
  directions recorded in its lineage.
- In an ordinary PR where both RU and EN mirrors changed, verifier checks semantic
  and structural equivalence without declaring either locale authoritative.
- In an ordinary PR where only RU or only EN changed, verifier reports red
  `MISSING_LOCALE_TRANSLATION` and lists exact missing mirror paths.
- Single-language manifest paths are exempt from mirror requirements.
- Only the PR scope and its local dependencies are checked. NG does not scan the
  whole repository for legacy parity defects.

## 23.12 Severity

Verifier has three user-facing outcomes:

- red `BLOCKED`: merge is not allowed and the Action fails;
- yellow `PASS_WITH_WARNINGS`: merge is allowed and the Action succeeds;
- green `PASS`: no problems, Action succeeds.

Red includes lost or distorted meaning, incomplete translation, broken structure,
links, code, placeholders, TOC, unresolved dependencies, residual translatable
Cyrillic, unsafe output and unavailable critic.

Yellow includes style, readability, optional wording and punctuation suggestions
without semantic loss. Yellow alone does not start repair. If repair is already
running because of a red issue, a related yellow issue may be fixed with it.

Color never changes Draft to Ready. A human controls Ready and merge.

## 23.13 Report contract

One canonical bot comment is updated for each new run.

Comment ownership is explicit:

- the merged source PR has one canonical lifecycle comment for `doc_translate`,
  no-Draft reasons, active Draft link, lineage state and continue history;
- an active translation Draft has one canonical QA comment for its exact current
  commit and verification result;
- an ordinary open PR checked by `doc_verify` has one canonical QA comment in that
  PR.

The source lifecycle and Draft QA comments share a `run_id` and link to one
another. A later run updates the applicable canonical comments rather than adding
duplicates. When blockers exceed one GitHub comment, the canonical comment remains
the summary and links to numbered detail comments recreated for the current run.

A green report is short. It shows checked commit, direction, file and dependency
counts, check categories, repairs used and final result. Detailed files, copied
images, mappings, exceptions, models, tokens, cost and versions remain available
under technical details.

A red report is detailed and written in clear Russian. Every blocker contains:

- exact target file and source file when relevant;
- reliable line or line range on the exact checked commit;
- a short exact fragment;
- what existed in source and what changed or disappeared in target;
- a concrete action;
- a ready continue command when the lineage can resolve it.

Internal jargon such as `fenced block violation` is forbidden as the explanation.
The report says that a code block was lost, a marker was not closed, a command was
changed, or Cyrillic remained. Residual Cyrillic reports exact file, line and
fragment.

If a line cannot be determined reliably, use the heading, Markdown element,
YAML/JSON path, TOC hierarchy or exact fragment. Never present an approximate
line as exact. Rebuild recalculates every location against the new commit SHA.

Blocking issues cannot be hidden behind an `and N more` summary. Prefer one large
comment to many inline comments. If GitHub cannot fit all blockers, split the
report into numbered parts without dropping red issues.

For an active translation lineage, a red report includes attempts already made,
the exact next action and a ready `/ydbdoc continue` command. For an ordinary
human PR, it gives a manual fix instruction because the bot cannot modify that
branch.

## 23.14 ACL, budget and retention

All model-backed `doc_*` CI entry points and `/ydbdoc continue` are restricted by
the repository variable `YDBDOC_ALLOWED_ACTORS`.

Confirmed actors:

```text
sintjuri,SixOnMyface,nataliaboldyreva,ayakivosklznak
```

The allowlist is an anti-abuse gate and is checked before model work.

For every command label, ACL checks the human `sender.login` of that exact labeled
event. PR author, previous operator and workflow service account do not substitute
for the sender. For `doc_continue`, both the label-event sender and the author of
the consumed `/ydbdoc continue ...` comment must be in the allowlist. If either is
not allowed, no model or branch work occurs and the bot posts a clear Russian
denial.

Daily spending uses repository variable `YDBDOC_DAILY_BUDGET_RUB` and the Moscow
calendar day. The sum includes every paid LLM call made by `doc_translate`,
`doc_continue` and `doc_verify`. Keep the budget behavior intentionally simple:

- before a model-backed run, read actual cost already recorded for today;
- if actual spending is already at or above the limit, reject the run before
  model calls and before destructive branch actions;
- if spending is below the limit, allow the run;
- a small overrun caused by the last admitted run is acceptable;
- do not implement advance estimates, reservations or complex concurrency logic;
- record actual cost after every run and show it in the report.

Cost persistence happens immediately after every completed paid LLM response, not
only at job end. Each record is idempotent by `run_id + call_id`. A final job
summary aggregates those call records. If the job crashes later, all already
recorded calls still count toward the Moscow-day budget. When a timeout or provider
failure returns no usage data, NG does not invent an estimate; the operational
report shows only usage actually returned by the provider.

Run records, actual costs, lineage decisions, snapshots, reports and full model
transcripts are retained for 14 days. Secrets and token values are never stored or
printed. Expired context produces an explicit explanation and cannot be used by
continue.

Lineage stores an exact UTC `expires_at`. Initial `doc_translate` sets it to 14
days after that run completes. Every accepted `doc_continue`, including one that
finishes red, writes a new compact lineage snapshot containing all accumulated
operator decisions and sets a new `expires_at` to 14 days after that continue
completes. `doc_verify`, ACL or budget denial, and concurrent-lock rejection do not
refresh expiry. Full transcripts and old run artifacts expire independently 14
days after their own run; the latest compact lineage snapshot remains until its
current `expires_at`.

An open Draft whose lineage has expired after 14 days is no longer automatically
verifiable or continuable. `/ydbdoc continue` is rejected. `doc_verify` reports
red in clear Russian that the original snapshot and decisions are no longer
available, and shows the expiry date and source PR. The only automatic recovery
is to apply `doc_translate` to the merged source PR again. That clean restart
closes the expired Draft, deletes its branch and rebuilds everything from scratch.
NG never gives the expired Draft a green result.

`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE` controls the hard dependency count per
source article. Its confirmed current value is `100`.

If ACL or budget rejects a command, its one-shot label is still removed, no model
or semantic verifier runs, and the Action fails. The checked PR receives a short
red operational report which explicitly says that the translation was not
verified. This report is not labeled as a translation-quality defect.

For an ACL denial, the Russian text names the exact GitHub login and command, for
example: `Перевод не проверялся. Пользователь @login запустил doc_verify, но у
него недостаточно прав.` For a budget denial, it shows the daily limit, actual
Moscow-day spend and when the next Moscow day begins. A previous green report for
another commit is never reused as the current result.

## 23.15 External documentation build

NG is translation CI only. It does not start, wait for or interpret the external
documentation build. The tech writer separately reviews the NG report, the docs
build and the Draft content.

## 23.15.1 Canonical documentation paths and eligible files

The documentation root is exactly `ydb/docs`. The locale roots are exactly
`ydb/docs/ru` and `ydb/docs/en`. A normal locale pair is calculated only by
replacing the first locale component immediately below `ydb/docs`, `ru` with
`en` or `en` with `ru`. The remaining relative path and filename are preserved
byte-for-byte. NG does not infer pairs by filename similarity or content.

The documentation glossary pair is fixed at
`ydb/docs/ru/core/concepts/glossary.md` and
`ydb/docs/en/core/concepts/glossary.md`. The shared redirect registry is exactly
`ydb/docs/redirects.yaml`.

Within either locale root, NG recognizes `toc.yaml`, `toc_p.yaml` and
`toc_i.yaml` as navigation files. No other YAML file becomes a TOC merely
because it contains similar keys.

The eligible NG scope consists of:

- Markdown/YFM source documents below a locale root;
- the three recognized TOC filenames below a locale root;
- allowlisted image and textual companion files reached under the dependency
  rules in this contract;
- the exact shared redirect registry.

Files outside these categories are not translated, copied, deleted or used to
expand the dependency graph. The operational report names such source-PR files
as unsupported and says clearly that NG left them unchanged. An unsupported file
alone does not create a translation Draft.

A directly changed unsupported file under a locale root is yellow, not red. The
report names its exact path and says in Russian: `Тип файла не поддерживается,
файл оставлен без изменений.` If supported bundles also exist, NG publishes them
normally and includes this warning. If the source PR contains only unsupported
files, NG creates no Draft, the Action passes, and the source-PR report says that
translation was not performed because there are no supported files.

This becomes red only when a supported root document has a mandatory parsed
dependency on the unsupported file. In that case NG cannot construct a complete
atomic bundle, omits that bundle and reports the exact dependency chain and file
type. Independent safe bundles may still publish.

## 23.15.2 Mixed operations on one locale pair

NG classifies all source-PR operations that address the same canonical RU/EN
relative path together before creating bundles:

- an add or update in exactly one locale makes that locale authoritative; NG
  fully creates or overwrites the other locale from it;
- a delete in exactly one locale, while the paired locale is untouched by the
  source PR, is a mirrored deletion request and deletes the paired locale;
- an add or update in both locales uses the bilingual classifier defined above;
- a delete in both locales requires no translation change and is reported as
  already complete;
- a delete in one locale combined with an add or update in the other locale in
  the same source PR is ambiguous. NG does not guess. It creates a red Draft when
  any other safe bundle exists, asks whether both sides must be deleted or the
  remaining side is authoritative, and applies that stored answer on
  `doc_continue`;
- if a later change on current `main` makes an original operation inapplicable,
  the established `SUPERSEDED` rule wins. NG reports the stale operation and does
  not recreate historical content from the source PR snapshot.

These rules use the immutable source manifest to determine what the source PR
did and current `main` to determine whether that operation is still applicable.

## 23.15.3 Genuine deletion without a successor

NG never removes a published article and its TOC href without also creating a
valid redirect, even when the source PR genuinely deletes the article and names
no successor. The article deletion, exact TOC removal and redirect remain one
atomic bundle.

If no accepted redirect target can be established, NG omits that entire deletion
bundle and reports a red blocker. Other independent safe bundles may still be
published in the Draft. The Russian report asks the tech writer where the old URL
must lead and provides a ready `/ydbdoc continue` command. The operator may name
an existing parent, overview or other suitable page. On continue, NG validates
that target under the normal redirect rules and then applies the deletion, TOC
removal and redirect together.

There is no automatic "delete without redirect" exception.

## 23.15.4 Glossary entry identity and parsing

A glossary entry starts at a Markdown heading of level three or deeper and
continues until the next heading of the same or a higher level. The preferred
stable entry identity is the explicit YFM anchor `{#anchor}` on that heading.
Heading case, surrounding whitespace and Markdown presentation are not part of
the identity.

The following deterministic rules apply:

- the same explicit anchor appearing more than once in one glossary is a red
  structural error reported with every exact line;
- a heading rename with the same anchor is an update of the same entry;
- changing an explicit anchor is a deletion of the old entry and addition of a
  new entry;
- an entry without an explicit anchor is paired only by its ordinal position
  inside the interval bounded by the same neighboring stable anchored entries;
- if multiple anchorless entries are inserted, deleted or reordered inside such
  an interval and the pairing is no longer unique, NG does not use title
  similarity or an LLM guess to establish identity.

An ambiguous anchorless interval blocks only the affected glossary bundle. The
Russian report shows both heading lists and line ranges and asks the tech writer
either to provide the exact RU-to-EN entry correspondence or choose the
authoritative locale for that interval through `/ydbdoc continue`. The stored
answer is replayed on the destructive rebuild.

## 23.16 Open decisions

The following decisions are intentionally still open:

1. Direct TOC-only source operations.
2. Scope of glossary verification for unrelated drift.
3. Reclassification of an originally bilingual pair after one side becomes
   `SUPERSEDED` on current `main`.
4. Final retrofit-versus-rewrite choice after every requirement above is closed
    and a separate implementation gap analysis is complete.

---

[Back to Memory Bank index](../../MEMORY_BANK.md)
