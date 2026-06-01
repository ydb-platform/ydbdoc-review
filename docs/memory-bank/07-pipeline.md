# Memory Bank — Pipeline & reporting

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 15. Pipeline data flow (detailed, Phase D+)

### 15.1. Per-file pipeline

```
INPUT: source_text (str), source_lang, target_lang, glossary, models

1. PARSE
   doc = parse_markdown(source_text)

2. EXTRACT
   segments = extract_segments(doc)
   # Each segment has id, kind, path, text (with ⟦C1⟧ markers), placeholders, ast_path.
   # Front matter → SegmentKind.FRONT_MATTER for title / description (B.4).

3. CHUNK
   batches = chunk_segments(segments, max_chars=4000)

4. TRANSLATE (parallel batches, limit 3)
   async for batch in batches:
       request = build_translate_prompt(batch, glossary, path_context)
       response = await llm_client.chat(translate_model, request)
       translations[batch] = parse_json(response)
       validate_placeholders(batch, translations[batch])
       validate_cli_tokens(batch, translations[batch])
       # On failure: retry per-segment

5. REINSERT (preserves AST structure)
   translated_doc = reinsert_segments(doc, segments, translations)

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

9. HEURISTICS (deterministic)
   warnings = run_file_heuristics(source_text, translated_text, ...)
   # length_ratio, cyrillic_in_en, fence/heading/list_tab parity
   verdict = bump_verdict_for_heuristics(verdict, warnings)  # ok → warnings

10. RENDER
    final_text = render_markdown(translated_doc)

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
   git.create_branch_from(source_pr_head_ref, branch)  # fetch from PR head (fork OK)
   git.commit_all(branch, message=build_commit_message(reports))
   git.push(branch, remote=upstream)  # always ydb-platform/ydb, not contributor fork

5. GITHUB
   tr_pr = github.open_pr(
       head=branch,
       base=translation_pr_base(ctx),  # same-repo: source head; fork: source base (main)
       title=f"Auto-translate docs from PR #{pr_number}",
       body=build_pr_description(reports),
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

- Branch: `ydbdoc-review/pr-<source_pr_number>`.
- **Push target:** always the upstream repository (`ydb-platform/ydb`), not the
  contributor fork. Content is still fetched from the source PR head (fork or
  same-repo).
- Branch base when building: the HEAD of the source PR (not `main`).
- One commit per run. Message:
  ```
  Auto-translate docs from PR #N

  Translated K files (X new, Y updated):
  - <path>
  ...
  Translator: <model>
  Critic: <model>
  ydbdoc-review v0.2.0
  ```
- Translation PR base:
  - **Same-repo PR:** the source PR's HEAD branch (stacked PR).
  - **Fork PR:** the source PR's base branch on upstream (`main`, etc.) — the
    contributor feature branch does not exist on upstream.
- Translation PR title: "Auto-translate docs from PR #N".
- Translation PR body: short summary + link to source PR.
- Committer/author: GitHub Actions bot (uses `GITHUB_TOKEN` / `GITHUB_PUSH_TOKEN`
  with `contents: write` on upstream).

### 16.4. Verify mode commits

- When critic proposes fixes:
  ```
  Apply critic fixes from doc_verify run on <timestamp>

  Critic: <model>
  Fixed segments: K
  ydbdoc-review v0.2.0
  ```

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

## Вердикт: 🟡 4 OK, 1 требует ревью

| Файл | Статус | Critic issues | Heuristic warnings |
|---|---|---|---|
| `…/foo.md` | 🟢 OK | 0 | 0 |
| `…/bar.md` | 🟢 OK | 0 | 1 (length ratio borderline) |
| `…/new.md` | 🟡 Warnings | 2 fixed, 0 unresolved | 1 (cyrillic in EN) |

## Сводка
- Сегментов переведено: 234 (auto-translated)
- Critic fixes auto-applied: 12
- Critic fixes unresolved: 0
- Heuristic warnings: 3
- Retry total: 3 (1.3%)
- Время: 2m 14s
- Tokens: translator 12,341/4,102; critic 8,221/1,503
- Cost: ~$0.42
- Models: translator=`yandexgpt-5.1`, critic=`qwen3.6-35b-a3b`
- Prompt version: v1

## Детали по файлам

### 🟡 `…/new.md`

**Critic issues (auto-applied: 2, unresolved: 0)**
- `s0042` (paragraph, in "Usage examples")
  - Category: terminology
  - "command" → "директива" (glossary mismatch)
  - 🟢 auto-applied

**Heuristic warnings**
- `cyrillic_in_en`: 1 occurrence at line 87 ("Sample")

<details>
<summary>Glossary used (12 entries)</summary>

- параметризованный запрос → parameterized query
- …
</details>

---

Generated by ydbdoc-review v0.2.0
```

### 17.3. Subsequent `doc_verify` runs

Each `doc_verify` run posts a NEW comment of the same format, with a header
`🤖 ydbdoc-review — отчёт #N (doc_verify, <timestamp>)`. Previous comments
remain visible for history.

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
