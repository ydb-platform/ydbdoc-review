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

5. REINSERT (preserves AST structure)
   translated_doc = reinsert_segments(doc, segments, translations)
   localize_links_in_document(translated_doc)
   postprocess_en_target_markdown(rendered)  # homoglyphs + fence angle placeholders
   enforce_source_fenced_blocks(rendered, source_text)  # verbatim fence bodies from RU

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

9. HEURISTICS (deterministic; source = raw_source_text)
   warnings = run_file_heuristics(raw_source_text, translated_text, ...)
   # ru_source: `--config-dir/opt` on PR RU; fence_body_copy; fence_path_stripped
   # missing_anchor (e.g. web.pem test); length_ratio; cyrillic_in_en; fence_parity
   verdict = bump_verdict_for_heuristics(verdict, warnings)  # ok → warnings

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

2. PRE-ANALYZE (cheap model, batched)
   needs_translate = pre_analyze_pairs(pairs, analyze_model)
   # For each pair: {action: translate_to_en | translate_to_ru | skip}

3. PER-FILE TRANSLATION (sequential)
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
2. Read ru + en files from translation PR head (NOT main)
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

- `ydb/docs/ru/X` ↔ `ydb/docs/en/X` (mirror).
- `ydb/docs/_includes/Y` — language-neutral; not translated.

If RU changed and EN did not → translate to EN (overwrite).
If EN changed and RU did not → translate to RU (overwrite).
If both changed:
  - Pre-analyze decides: if they look like a synced manual edit, skip
    translation, but still run critic.
  - Otherwise: re-translate from source language (RU is default source).
If RU exists but EN doesn't → create EN from RU.
If EN exists but RU doesn't → create RU from EN.

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

**Default (after ydb workflow change):** only `secrets.GITHUB_TOKEN`.

| Step | Token | Workflow `permissions` |
|------|--------|-------------------------|
| Action: API (PR, comments, `documentation` label) | `GITHUB_TOKEN` | `pull-requests: write`, `issues: write` |
| Action: `git push` branch `ydbdoc-review/pr-N` | same (`GITHUB_PUSH_TOKEN` unset → falls back to `GITHUB_TOKEN`) | `contents: write` |
| Post-step: `rebuild_docs` on translation PR | `GITHUB_TOKEN` in `github-script` | `issues: write` |

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

### 17.1. Short comment in source PR (after `doc_translate`)

```markdown
🤖 **ydbdoc-review** — перевод готов

| | |
|---|---|
| Translation PR | #M |
| Файлов переведено | 5 (3 новых, 2 обновлено) |
| Статус QA | 🟡 4 OK, 1 требует ревью |
| Время | 2m 14s |
| Стоимость | ~$0.42 |

👉 Полный отчёт в translation PR #M.
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

---

Generated by ydbdoc-review v0.2.0
```

Отчёт **не** содержит сводку «N сегментов / M auto-applied» и не перечисляет
уже автоматически исправленные critic issues — только то, что ревьюеру нужно
проверить или доправить руками. Локация берётся из `segment.path` + `segment_id`.

### 17.3. Subsequent `doc_verify` runs

Each `doc_verify` run posts a NEW comment of the same format, with a header
`🤖 ydbdoc-review — отчёт #N (doc_verify, <timestamp>)` and optional
`Checkout: \`<sha>\``. Previous comments remain visible for history.

**Not a diff against the prior report:** each run re-parses RU + current EN,
re-runs critic (with verdict alias normalization), heuristics, and optional repair
commit. Results differ when EN changed, segment alignment fails, LLM batches vary,
or critic JSON parse fails for a batch.

**`doc_verify` alignment:** `_align_translations` must match segment counts; on
failure → `segment_alignment_error`, critic skipped, 🔴 in report (§6.26).

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
