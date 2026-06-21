# Memory Bank — Pipeline & reporting

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 15. Pipeline data flow (detailed, Phase D+)

### 15.1. Per-file pipeline

```
INPUT: source_text (str), source_lang, target_lang, glossary, models
   raw_source_text = source_text  # for heuristics / ru_source_bugs on original PR RU

0. RU NORMALIZE (translate only, ru→en)
   source_text = normalize_ru_source_for_translation(source_text)
   # e.g. `--config-dir/opt` → `--config-dir /opt` before parse

1. PARSE
   doc = parse_markdown(source_text)
   # FencedCode / IndentedCode: NOT segmented — never sent to translate LLM

2. EXTRACT
   segments = extract_segments(doc)
   # Each segment has id, kind, path, text (with ⟦C1⟧ markers), placeholders, ast_path.
   # Front matter → SegmentKind.FRONT_MATTER for title / description (B.4).

3. CHUNK
   batches = chunk_segments(segments, max_chars=4000)

4. TRANSLATE (parallel batches, limit 3)
   for batch in batches:
       response = llm_client.chat(translate_model, build_translate_messages(...))
       translations = parse_json(response)
       repair_translation_placeholders()  # per segment, before validate
       validate: placeholder order + roles (V not in link URL unless source is)
                 + cli_tokens + fence count per segment
       # On batch failure: per-segment retry; on segment failure: repair-pass LLM
       #   (translation/repair.py, prompts/v1/repair.md), then table fail-soft

5. REINSERT + EN FINALIZE (preserves AST structure)
   translated_doc = reinsert_segments(doc, segments, translations)
   localize_links_in_document(translated_doc, target_lang)  # §6.34, §6.37
   rendered = render_markdown(translated_doc)
   _finalize_en_target(rendered, source_text):
     enforce_source_fenced_blocks  # verbatim fence bodies from normalized RU
     translate_cyrillic_fence_comments_with_client  # ``//`` / ``#`` / ``--`` lines (§6.39, §6.46)
     translate_cyrillic_prose_with_client  # residual Cyrillic in prose/backticks (§6.45)
     localize_links_in_text      # Wikipedia langlinks safety net (§6.37)
     postprocess_en_target_markdown  # homoglyphs + `<строка>`→`<string>` (§6.28)

6. CRITIC PASS 1 (batched segment pairs)
   batches = chunk_segments(segments, max_chars=4000)
   for batch in batches:
       critic_response = llm_client.chat(critic_model, build_critic_batch_prompt(batch, translations))
       issues += parse_critic_response(critic_response).issues
   # issues = [{segment_id, severity, category, comment, suggested_text}]

7. APPLY CRITIC FIXES
   for issue in issues:
       if issue.suggested_text:
           translations[issue.segment_id] = issue.suggested_text
   translated_doc = reinsert_segments(doc, segments, translations)

8. CRITIC PASS 2 (batched re-validate)
   for batch in batches:
       verify_response = llm_client.chat(critic_model, build_verify_batch_prompt(...))
       unresolved += parse_critic_response(verify_response).issues

9. ROUND-TRIP GATE (translate + verify — same code)
   translations, err = gate_round_trip(segments, final_text)
   # fail → segment_alignment_error, critic skipped, verdict blocked

10. HEURISTICS (classified; raw RU + normalized RU for parity checks)
    blocking / warnings / info = run_file_heuristics_classified(...)
    # info: ru_source (fix in RU PR). blocking: prose cyrillic, parity, missing_anchor, …
    # warnings: cyrillic_in_fence (if comment translate missed), borderline length, …
    verdict = compose_file_verdict(critic, alignment, heuristics, manual_actions)

10. OUTPUT TEXT
    final_text = translated_text after step 5 (render + link locale + EN postprocess)

OUTPUT: final_text, file_report = {
    file_path,
    verdict,                          # ok / warnings / blocked
    critic_issues,                    # initial issues
    unresolved_issues,                # after critic pass 2
    heuristic_warnings,
    cost,                             # tokens + latency
    models_used,
    prompt_version,
}
```

### 15.2. PR-level orchestrator

```
INPUT: pr_number, source_repo, target_branch_base

1. ENUMERATE
   changed_md = github.list_changed_md_files(pr_number, target_branch_base)
   pairs = pair_ru_en(changed_md)
   # pairs: [{ru_path, en_path, ru_exists, en_exists, ru_changed, en_changed}]

2. PLAN (deterministic — §6.30)
   plans = plan_pairs(contents)   # no LLM analyze in CI
   # RU changed → translate_to_en (full render, overwrite EN)
   # EN changed only → translate_to_ru
   # both changed → translate_to_en from RU when RU text exists

3. NAVIGATION (when PR touches RU ``toc*.yaml`` / redirect YAML — §6.17)
   nav_pairs = build_navigation_pairs(changes)
   scope = toc_translate_scope(ru_base, ru_pr) ∪ new_md_hrefs
   merge_en_toc_yaml / merge_en_redirects_yaml → write EN mirror
4. COMPLETENESS (§6.32)
   completeness_gaps = expected_en_mirrors(diff) − committed_en_paths

5. PER-FILE TRANSLATION (sequential)
   per_pr_cache = {}
   reports = []
   for pair in needs_translate:
       try:
           translated, report = translate_file(
               source_text=read(pair.source),
               target_lang=pair.target_lang,
               cache=per_pr_cache,
           )
           write(pair.target, translated)
           reports.append(report)
       except APIError as e:
           reports.append(failed_report(pair, e))
           continue  # don't fail the whole PR

4. GIT
   branch = f"ydbdoc-review/pr-{pr_number}"
   upstream_url = repo_https_clone_url(owner, repo)
   start_ref = translation_branch_base(ctx)
   # fork or merged source PR → upstream base_ref (main); open same-repo → head_ref
   git.fetch(upstream_url, start_ref)
   git.create_branch_from(start_ref, branch)
   git.commit_all(branch, message=build_commit_message(reports))
   git.push(branch, remote=upstream_url)

5. GITHUB
   tr_pr = github.open_pr(
       head=branch,
       base=translation_pr_base(ctx),
       ...
   )
   github.post_comment(
       pr_number,
       body=f"Translation PR ready: #{tr_pr.number}. See report there.",
   )
   github.post_comment(
       tr_pr.number,
       body=build_full_report(reports, heuristics, cost),
   )

OUTPUT: exit code 0 unless infrastructure failure.
```

### 15.3. Verify mode

```
INPUT: translation_pr_number

1. Discover source PR number from translation PR description
2. Read **EN** from translation PR checkout; **RU** from source PR head (§6.31)
3. Run critic + heuristics (no translator)
4. Apply critic fixes (suggested_text per segment_id)
5. If any fixes applied: commit + push to translation PR branch
6. Post a NEW comment on the translation PR with the report
   (do NOT replace previous; history is valuable)
```

---

---

## 16. PR-level behavior

### 16.1. File pairing

For each changed `.md` under `ydb/docs/`:

- `ydb/docs/ru/X` ↔ `ydb/docs/en/X` (mirror), including `ru/…/_includes/*.md`
  ↔ `en/…/_includes/*.md` (locale-specific fragments: tables, auth snippets, …).
- `ydb/docs/_includes/Y` and other paths **outside** `docs/ru` / `docs/en` —
  language-neutral (images, shared assets); not translated.
- Non-`.md` under `_includes/` (png, svg, …) — never sent to the translator.

If RU changed (EN changed or not) → **full** translate RU→EN; commit replaces EN
entirely (render from RU AST — §6.30). Existing EN on `main` is ignored.
If EN changed and RU did not → full translate EN→RU (overwrite RU).
If both changed and RU text exists → full RU→EN (RU is default source).
If both changed and RU missing → full EN→RU.
If RU exists but EN doesn't → create EN from RU.
If EN exists but RU doesn't → create RU from EN.
`critic_only` is **not** used in `doc_translate` (verify mode only).

### 16.2. New / deleted / renamed

- **New file in RU**: create EN.
- **Deleted file in RU**: also delete EN.
- **Renamed file**: not auto-detected from git rename info in MVP;
  treat as delete+add. (Tracked in backlog if needed.)

### 16.3. Translation branch and PR

- Branch name: `ydbdoc-review/pr-<source_pr_number>` on **upstream** (`ydb-platform/ydb`).
- **Branch creation:** always on upstream, never on the contributor fork.
  - **Fork PR:** new branch from upstream `base_ref` (`main`, etc.) — the branch
    the source PR targets / merges into. RU content comes from the PR checkout;
    only translated EN paths are committed. Do **not** base on the fork head
    (foreign history breaks push / triggers workflow-scope errors).
  - **Same-repo PR:** new branch from the source PR head on upstream (stacked PR).
- One commit per run. Message:
  ```
  Auto-translate docs from PR #N
  ...
  ```
- **Translation PR** on upstream: `head=ydbdoc-review/pr-N`, `base` = same ref
  the translation branch was created from (fork → `main`; same-repo → feature branch).
- Translation PR title: "Auto-translate docs from PR #N".
- Committer/author: GitHub Actions bot (`github-actions[bot]`), push/API via job
  `GITHUB_TOKEN` when workflow grants `contents: write`.

### 16.7. GitHub tokens in `ydb` CI (2026-06)

**Two-job split** ([ydb #43126](https://github.com/ydb-platform/ydb/pull/43126), merged 2026-06-10):

| Job | What | Token |
|-----|------|--------|
| `ydbdoc-review` / `ydbdoc-verify` | checkout PR code, run action, push branch / repair commit | `GITHUB_TOKEN` |
| `trigger-translation-ci` / `trigger-verify-ci` | **no checkout** — add labels on translation PR | `YDBOT_TOKEN` |

Why: events from `GITHUB_TOKEN` **do not cascade** into other workflows (PR-check,
docs rebuild). Translation PR author is `github-actions[bot]` — PR-check needs
`ok-to-test`. PAT labels from a job without fork code avoid exposing `YDBOT_TOKEN`
next to untrusted PR content.

| Step | Token | Workflow `permissions` |
|------|--------|-------------------------|
| Action: API (PR, comments, `documentation` label) | `GITHUB_TOKEN` | `pull-requests: write`, `issues: write` |
| Action: `git push` branch `ydbdoc-review/pr-N` | same (`GITHUB_PUSH_TOKEN` unset → falls back to `GITHUB_TOKEN`) | `contents: write` |
| `trigger-translation-ci`: `rebuild_docs` + `ok-to-test` | `YDBOT_TOKEN` in `github-script` | (job has no checkout) |
| `trigger-verify-ci`: `ok-to-test` + `rebuild_docs` | `YDBOT_TOKEN` | same |

`trigger-translation-ci` runs only when `needs.ydbdoc-review.result == 'success'`.
Therefore `run_doc_translate` must not exit 1 after push when only the source-PR
comment fails — see §6.48 (`_safe_post_issue_comment`).

`doc_verify` on **contributor fork PRs** cannot push the repair commit to the head
ref (GITHUB_TOKEN has no rights on forks). Instead it opens a separate fixup PR on
upstream and posts a link comment on the source PR — see §6.50.

Do **not** set `GITHUB_PUSH_TOKEN` / `YDBDOC_PUSH_PAT` in env unless `git push` returns 403
(org policy blocking default `GITHUB_TOKEN`).

**Legacy:** `YDBDOC_PUSH_PAT` secret + `GITHUB_PUSH_TOKEN` env still work (`entrypoint.sh`
maps `YDBDOC_PUSH_PAT` → `GITHUB_PUSH_TOKEN` for older workflows).

**Local CLI:** use a personal PAT in `GITHUB_TOKEN` (or classic `repo` scope) in `.env`.

Examples: [`examples/ydb-github-doc-translate-on-label.yml`](../../examples/ydb-github-doc-translate-on-label.yml),
[`examples/ydb-github-doc-verify-on-label.yml`](../../examples/ydb-github-doc-verify-on-label.yml).

### 16.4. Verify mode commits

- When critic proposes fixes:
  ```
  Apply critic fixes from doc_verify run on <timestamp>

  Critic: <model>
  Fixed segments: K
  ydbdoc-review v0.2.0
  ```

### 16.5. Repair-pass and EN postprocess

- **Repair-pass** (`translation/repair.py`): after `TranslationValidationError`
  on a single segment (placeholder order/roles, fence count, CLI tokens), one
  focused LLM call with `prompts/v1/repair.md` (up to 2 attempts). Used before
  table fail-soft.
- **Placeholder repair** (`validation/placeholder_repair.py`): deterministic fixes
  before validation — restore `⟦U⟧`/`⟦V⟧`/`⟦C⟧`, swap V↔U when the model puts
  `⟦V⟧` in `[text](...)`, move «on the ⟦V⟧ server» before «Used if […]» when
  source has variable before link (`placeholder_roles.py` enforces roles).
- **EN postprocess** (`homoglyphs.postprocess_en_target_markdown`): after render;
  homoglyphs, `<строка>`→`<string>` in fences (incl. indented `` ``` ``), and
  **MD031** blank lines around fences (`markdown_layout.fix_blanks_around_fences`).
- **Renderer MD031** (`markdown_renderer._join_blocks`): prevents missing blank
  lines after `` ``` `` in tight lists when re-rendering translated AST (root cause
  of PR #42404 markdownlint warnings).

### 16.6. Fail-soft policy for table segments

- Table cells (`table_header_cell`, `table_body_cell`) are the most fragile
  segments for placeholder parity.
- If translation keeps failing with `placeholder mismatch` on a table segment,
  pipeline does **not** fail the whole file:
  - keep the source RU table segment text in output as-is;
  - continue translating other segments/files;
  - add an explicit manual-action note to the report: table was not translated
    and must be translated manually.
- File verdict is bumped to `warnings` when such manual actions exist.

---

---

## 17. Reporting format

### 17.0. Comment posting order (`doc_translate`)

After push and translation PR open:

1. **Translation PR** — full QA report (`build_full_report`). Primary deliverable
   for reviewers (§6.48).
2. **Source PR** — short summary (`build_source_pr_comment`). Best-effort; failures
   are logged as warnings and do not fail the job (fork source PRs may return HTTP
   401 on `issues/{n}/comments` while translation-PR API calls succeed).

`doc_verify` posts only the translation PR report (same `_safe_post_issue_comment`).

### 17.1. Short comment in source PR (after `doc_translate`)

```markdown
🤖 **ydbdoc-review** — перевод готов

| | |
|---|---|
| Translation PR | #M |
| Файлов переведено | 5 (3 новых, 2 обновлено) |
| Статус QA | 🟡 4 OK, 1 требует ревью |
| Время | 2m 14s |
| Стоимость | ~₽10.50 |

Список оставшихся проблем — в комментарии к translation PR #M.
```

### 17.2. Full report in translation PR (after `doc_translate` or `doc_verify`)

```markdown
🤖 **ydbdoc-review** — отчёт #1 (doc_translate, 2024-11-05 14:23 UTC)

## Рекомендация: 🟡 требует правок перед merge

## Что исправить

### 🟡 `…/parameterized-query-execution.md`

1. **Overview (`s0003`)** — (terminology) в ссылке осталась кириллица «командой YQL»
   - 💡 Совет: via the YQL `DECLARE` command

2. **эвристика** — Кириллица в EN-тексте (строка ~12): «…командой YQL DECLARE…»

## Без замечаний

- 🟢 `…/other.md`

## Стоимость и токены

- Токены (перевод): 7,298 / 4,325
- Токены (критик): 7,055 / 1,957
- Токены (всего): 14,353 / 8,592
- Оценка стоимости: ~₽10.6
- Модели: перевод=`deepseek-v32`, критик=`deepseek-v32`

---

Generated by ydbdoc-review v0.1.0 @ `<sha>`
```

**All-green path** («По всем файлам открытых замечаний нет») includes the same
«Стоимость и токены» block (§6.38) — not only the «Что исправить» layout.

Отчёт **не** содержит сводку «N сегментов / M auto-applied» и не перечисляет
уже автоматически исправленные critic issues — только то, что ревьюеру нужно
проверить или доправить руками. Локация берётся из `segment.path` + `segment_id`.

### 17.3. Subsequent `doc_verify` runs

Each `doc_verify` run posts a NEW comment of the same format, with a header
`🤖 ydbdoc-review — отчёт #N (doc_verify, <timestamp>)` and optional

Navigation YAML (§6.35): EN ``toc*.yaml`` / redirect files changed in the
translation PR are validated against RU from source PR head; listed in the
report as ``(навигация)`` like ``doc_translate``. Inline TOC lines keep the
list-entry prefix from EN ``main`` (§6.36); ``inconsistent_indent`` is blocking.

``doc_verify`` validates navigation only — it does **not** rewrite YAML on disk.
To fix a bad ``toc_i.yaml`` already on the translation branch, re-run
``doc_translate`` or edit the file manually, then ``doc_verify``.
`Checkout: \`<sha>\``. Previous comments remain visible for history.

**Not a diff against the prior report:** each run re-parses RU + current EN,
re-runs critic (with verdict alias normalization), heuristics, and optional repair
commit. Results differ when EN changed, segment alignment fails, LLM batches vary,
or critic JSON parse fails for a batch.

**`doc_verify` alignment:** `_align_translations` must match segment counts; on
failure → `segment_alignment_error`, critic skipped, 🔴 in report (§6.26).
Diagnostics name the first structural diff (segment kind + id + path) — §6.56
``describe_segment_alignment_mismatch``; see §6.58 ``glossary.md`` example.

**Critic noise filters (verify path):** after ``run_critic`` and ``apply_critic_fixes``,
``drop_spurious_placeholder_issues`` runs on actionable issues; after ``run_verify``,
``filter_critic_response(..., skipped=critic_skipped)`` drops spurious placeholder
issues *and* verify echoes of apply-rejected fixes (§6.56–§6.57). Verdict is
computed from filtered ``critic_unresolved`` only.

**Report tiers (§6.56–§6.57):**

- **«Что исправить»** — unresolved critic (minus skipped duplicates), manual
  actions, blocking heuristics, alignment errors.
- **«Без замечаний»** — files with no open items (🟢).
- **«Автоисправление не применено»** (collapsed) — ``critic_skipped`` only;
  does not inflate 🔴 or duplicate main-list numbering
  (``reporting.include_skipped_critic``, default ``true``).

**Canonical human-translation verify case:** [ydb #40466](https://github.com/ydb-platform/ydb/pull/40466)
— five-file EN PR; post-§6.57 only ``glossary.md`` remains 🔴 (§6.58).

**Canonical auto-translate case:** [ydb #43365](https://github.com/ydb-platform/ydb/pull/43365)
— OTel metrics/tracing docs from [#41691](https://github.com/ydb-platform/ydb/pull/41691);
§6.59 fixes critic apply, ``text`` fences, TOC/index parity (re-run ``doc_translate``).

**Canonical auto-translate case (observability):** [ydb #44103](https://github.com/ydb-platform/ydb/pull/44103)
from [#43530](https://github.com/ydb-platform/ydb/pull/43530); §6.62 fixes ``text`` fence
QA noise and parent ``toc_p.yaml`` ``include.path`` merge (+ ``extra_toc_hrefs_for_pair``
``KeyError``).

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
