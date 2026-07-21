---
name: eliza-doc-translate
description: >-
  Run ydbdoc-review doc_translate locally via Eliza (internal models), not the
  GitHub doc_translate label. Use when the user asks to translate a YDB docs PR
  with Eliza, re-translate until green, local job --mode translate, or internal
  model translate. Launches the job, waits in the background, then checks the
  translation PR QA report and iterates toward 🟢.
---

# Eliza doc_translate (local)

## Goal

Translate a **source** YDB docs PR with **Eliza** until the translation PR is
**🟢** (ydbdoc-review recommendation «можно мержить»). Do **not** use the
GitHub label `doc_translate` (Yandex Cloud). Do **not** block the chat for
~1h `build-docs`.

## Defaults

| Item | Value |
|------|--------|
| Repo | `ydb-platform/ydb` |
| ydbdoc-review | `/Users/iuriisintiaev/projects/ydbdoc-review` |
| ydb checkout | `/Users/iuriisintiaev/projects/ydb-clone/ydb` |
| Merge base | `origin/main` |
| Provider | `YDBDOC_MODEL_PROVIDER=eliza` |
| Log | `/tmp/ydbdoc-translate-<PR>.log` |

Override paths only if the user gives different ones.

## Standing permission

The user already allows the full Eliza translate/verify loop (launch, poll,
force-push `ydbdoc-review/pr-*`, re-verify, iterate to 🟢). **Do not ask**
«можно ли запустить / продолжить?» and **do not** end the turn as if waiting
for approval while a job runs. See rule `eliza-translate-autonomy`.

## Hard rules

1. **Eliza only** — `python -m ydbdoc_review job --mode translate …` via
   `zsh -lic` (loads `ELIZA_OAUTH_TOKEN` / `GITHUB_TOKEN` from `~/.zshrc`).
2. **Never** add/toggle the `doc_translate` label for this workflow.
3. **Start → background → continue** — do not `gh run watch` / sleep through
   docs builds. Translation itself may take tens of minutes; wait on **log
   sentinel**, not on `build-docs`.
4. **Keep watching until DONE** — do **not** drop the job after the first
   turn. Arm **both**:
   - `notify_on_output` on `YDBDOC_ELIZA_TRANSLATE_DONE` /
     `YDBDOC_ELIZA_VERIFY_DONE` (or the helper `.done` file), **and**
   - a **5-minute heartbeat loop** (see § Monitoring) so the agent wakes even
     if the sentinel notification was missed or the chat went idle.
5. After the job finishes, **inspect the translation PR** and act until 🟢
   (or hit a real blocker you must report).
6. Keep `YDBDOC_ELIZA_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem`. Do **not**
   set `REQUESTS_CA_BUNDLE` to the internal CA alone.

## Monitoring (mandatory for long jobs)

Why agents look «stuck»: between user messages the model only wakes on tool
notifications. Relying only on DONE is fragile (missed pattern, early exit,
chat idle). **Always** add a 5-minute poll until the job ends.

After launching translate or verify:

```bash
# Loop every 5m: check Eliza job <PR>
while true; do
  sleep 300
  echo "AGENT_LOOP_TICK_eliza {\"prompt\":\"Check Eliza job for PR <N>: read /tmp/ydbdoc-translate-<N>.done or /tmp/ydbdoc-verify-<TRANSLATION_PR>.done if present; else tail -20 the matching log and report still-running vs errors. If DONE: handle completion per skill (QA report → iterate to green). If still running: one-line status then keep looping. If user asked to stop: kill this loop.\"}"
done
```

Shell tool:

- `block_until_ms: 0`
- `notify_on_output`: pattern `^AGENT_LOOP_TICK_eliza`
- Unique purpose suffix if several jobs run (`eliza_41271`, `eliza_verify_47104`)
- Smoke-check the loop started; first tick after ~5m
- On each tick: short status to the user if still running; on DONE → full
  completion handling and **kill the loop** (do not re-arm)
- Also keep `notify_on_output` on the job’s DONE sentinel — whichever fires
  first, handle completion once and stop the heartbeat

Stop the heartbeat when: DONE handled, unblockable failure reported, or user
says stop.

## Workflow

Copy and track:

```
Eliza translate → green:
- [ ] 1. Resolve source PR N (and existing translation PR if any)
- [ ] 2. Prepare ydb checkout (source PR head + fetch main)
- [ ] 3. Launch Eliza translate (background + DONE sentinel)
- [ ] 4. On DONE: read log exit code + translation PR report
- [ ] 5. If 🔴: fix (logic / toc / href) or re-run verify/translate
- [ ] 6. Stop when 🟢 or unblockable failure reported to user
```

### 1. Resolve PR numbers

- User says source PR (e.g. `41271`) → translate that.
- User says translation PR (`ydbdoc-review/pr-N` / `#47104`) → source is `N`
  from branch `ydbdoc-review/pr-<N>` or PR body «from PR #N».
- Translation PR branch: `ydbdoc-review/pr-<N>`.

### 2. Prepare checkout

```bash
cd /Users/iuriisintiaev/projects/ydb-clone/ydb
git fetch origin main
git fetch origin pull/<N>/head:pr-<N>
git checkout pr-<N>
git fetch origin main
```

Use editable ydbdoc-review: `source .venv/bin/activate` in that repo
(`pip show` → Editable). Prefer current `main` (includes latest §6.xxx fixes).

### 3. Launch (background)

Prefer the helper (writes log + sentinel):

```bash
/Users/iuriisintiaev/projects/ydbdoc-review/.cursor/skills/eliza-doc-translate/scripts/run_eliza_translate.sh <N>
```

Or equivalent `zsh -lic` + `block_until_ms: 0`:

```bash
zsh -lic '
export YDBDOC_MODEL_PROVIDER=eliza
export ELIZA_API_ROOT="${ELIZA_API_ROOT:-https://api.eliza.yandex.net}"
export YDBDOC_ELIZA_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem
export GITHUB_TOKEN="${GITHUB_TOKEN:-$YDB_GH_TOKEN}"
# Prefer working fallbacks; do not reuse the other role's model (§6.127)
export YDBDOC_ELIZA_TRANSLATE_FALLBACKS="${YDBDOC_ELIZA_TRANSLATE_FALLBACKS:-}"
export YDBDOC_ELIZA_CHECK_FALLBACKS="${YDBDOC_ELIZA_CHECK_FALLBACKS:-}"
cd /Users/iuriisintiaev/projects/ydbdoc-review && source .venv/bin/activate
python -m ydbdoc_review job \
  --mode translate \
  --repo ydb-platform/ydb \
  --pr <N> \
  --repo-path /Users/iuriisintiaev/projects/ydb-clone/ydb \
  --merge-base-with origin/main
'
```

Shell tool:

- `block_until_ms: 0` (background immediately).
- `notify_on_output`: pattern `YDBDOC_ELIZA_TRANSLATE_DONE` (or read
  `/tmp/ydbdoc-translate-<N>.done` when the helper finishes).
- **Also** start the § Monitoring 5-minute `AGENT_LOOP_TICK_eliza` heartbeat
  for this PR (same turn as launch).
- After start: smoke-check the log for `Starting Eliza translate` /
  `Scope plan for PR` within a few seconds. If missing tokens/CA, fix and
  relaunch — do not wait blindly.

Tell the user: PR N started; log path; you will report on DONE **and** every
~5m while it runs (no build wait).

### 4. On completion — verify what happened

1. Log: exit code, `Finished Eliza translate`, traceback?
2. Translation PR: `gh pr list --repo ydb-platform/ydb --head ydbdoc-review/pr-<N>`
   or known number.
3. Latest **ydbdoc-review** report comment:
   - 🟢 → done; summarize.
   - 🟡 soft-only (e.g. `toc_en_only_legacy`) with recommendation «можно мержить»
     → treat as success for this skill unless user wants zero warnings.
   - 🔴 → read blocking kinds (`orphan_toc_page`, `toc_structure_parity`,
     YFM010 hrefs, critic, completeness).

### 5. Iterate to green

| Symptom | Action |
|---------|--------|
| Logic bug in ydbdoc-review | Fix + commit + `git tag -f v0.1.0` + push; re-run Eliza translate or `job --mode verify` on translation PR |
| Stale Sessions / `{#T}` href | §6.125 force_exact; do not rely on manual-only fixes (critic can revert) |
| Unscoped toc parity false 🔴 | §6.124 scope-aware `only_ru`; bump tag; re-verify |
| Completeness gaps | Ensure scope planner queues missing EN; re-translate |
| Eliza 404 model | Drop dead fallback ids; keep `deepseek-v4-flash` + `gpt-oss-120b` |
| Job failed mid-way | Fix env/error; relaunch same `--pr <N>` |

Re-verify without full translate when only QA/critic needed:

```bash
# same zsh -lic + Eliza env
python -m ydbdoc_review job \
  --mode verify \
  --repo ydb-platform/ydb \
  --pr <TRANSLATION_PR> \
  --repo-path /Users/iuriisintiaev/projects/ydb-clone/ydb \
  --merge-base-with origin/main
```

Checkout the **translation** branch before verify.

Max **3** automatic translate/verify cycles per user request unless they ask to
continue. After that, report remaining 🔴 with links.

### 6. What not to wait on

- `build-docs` / docs preview (~1h) — optional background note only; user
  checks or pings later.
- GitHub Actions `ydbdoc-review` / `doc_translate` label jobs — out of scope.

## Quick checks

```bash
# Report emoji
gh api repos/ydb-platform/ydb/issues/<TRANSLATION_PR>/comments \
  --jq '[.[]|select(.body|test("Рекомендация"))][-1].body' | head -40

# Branch tip
gh api repos/ydb-platform/ydb/commits?sha=ydbdoc-review/pr-<N>&per_page=5 \
  --jq '.[]|"\(.sha[0:10]) \(.commit.message|split("\n")[0])"'
```

## See also

- Memory Bank: `docs/memory-bank/08-operations.md` §19.5, `04-development.md` §11.4
- Helper: [scripts/run_eliza_translate.sh](scripts/run_eliza_translate.sh)
