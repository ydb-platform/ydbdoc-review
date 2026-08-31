# Memory Bank — ydbdoc-review v2 (doc-translate-ng)

> Living, opinionated document. Treat it as authoritative for design intent.  

**Last updated:** 2026-08-31
**Current focus:** §6.229 — tip+overlay final-tree reader + Docker Hub base fallback (#40385).

The Memory Bank is split into parts below. Section numbers (`§6.12`, `§22.3`, …) are
stable cross-references — use them when linking between files.

## Contents

| Part | File | § | Topics |
|------|------|---|--------|
| Overview & architecture | [01-overview](docs/memory-bank/01-overview.md) | 0–3 | Goals, v1 lessons, data flow |
| Codebase reference | [02-codebase](docs/memory-bank/02-codebase.md) | 4–5 | Package layout, AST / IR |
| Design decisions | [03-design-decisions](docs/memory-bank/03-design-decisions.md) | 6 | Trade-offs, historical fixes |
| Development guide | [04-development](docs/memory-bank/04-development.md) | 7, 9–11 | Tests, backlog, env, agreements |
| Roadmap | [05-roadmap](docs/memory-bank/05-roadmap.md) | 8 | Phases A–J checklist |
| LLM & config | [06-llm-config](docs/memory-bank/06-llm-config.md) | 12–14, 18 | Models, YAML config, prompts |
| Pipeline & reporting | [07-pipeline](docs/memory-bank/07-pipeline.md) | 15–17 | Per-file flow, PR workflow, reports |
| Operations | [08-operations](docs/memory-bank/08-operations.md) | 19–21 | Action runtime, cost, glossary |
| Navigation scope | [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) | 22 | TOC planner + **§22.14 regression catalog** |

## Recent changes

| When | What |
|------|------|
| 2026-08-31 | **§6.229** — late repair + link gate use tip+overlay tree; Docker Hub base fallback on ECR 429 (#40385) |
| 2026-08-31 | **§6.228** — merged `doc_translate` loads tip EN (not merge-commit checkout); gate ignores ambient tip link debt (#40385) |
| 2026-08-31 | **§6.227** — repair deterministic preserves; follow redirect from→to for RU/EN fragment mapping; prefer post-repair disk bytes and valid same-slot EN baseline hrefs (#40385 / #51711) |
| 2026-08-31 | **§6.226** — post-apply EN ``en_link_target`` gate (file+fragment existence); blocks href-only bypass / critic-green false negatives (#51711) |
| 2026-08-31 | **§6.225** — remap RU legacy translit fragments (e.g. `#vklyuchenie-…`) to EN auto-slugs; late disk repair after apply/retarget (#45949 / #51711) |
| 2026-08-31 | **§6.224** — skip EN at redirect from-paths; ignore pending toc seed; exclude from redirect inbound retarget + verify scope gaps (#45949 / #51703 / #51709) |
| 2026-08-31 | **§6.223** — merged `doc_translate` uses real translation again; verify-only routing skipped missing EN / deleted RU (#45949 / #51696) |
| 2026-08-24 | **§6.222** — remap LLM-invented ASCII link fragments via RU source path lookup (#40385 / #50976) |
| 2026-08-24 | **§6.221** — remap RU autogen link fragments to EN explicit anchors; fix ``Имя=Значение`` notation; reject critic EN fixes that reintroduce Cyrillic (#40385 / #50976) |
| 2026-08-24 | **§6.220** — critic malformed JSON: repair once on primary, then retry original prompt on configured fallback; retain fail-closed verdict (#50904) |
| 2026-08-23 | **§6.204** — buildable legacy-source normalization: canonical fence closers, missing peer-tab closers, unmatched YFM closers, empty overlay include indentation; exact #50741 `./ya make ydb/docs` is green |
| 2026-08-23 | **§6.203** — preserve per-line RU indentation inside stable translated fenced blocks (#37673 / #50741) |
| 2026-08-23 | **#50741 verify outcome** — critic 27/27 green at `c7d4fca`; production still rejects legacy TTL/vector YFM and debug-logs MD009, plus unrelated RU YFM003 |
| 2026-08-22 | **§6.198** — final pair-level repair restores matching fence indentation, YFM (`if` included), unchanged technical-line indentation, and MD009-safe whitespace (#37673 / #50741) |
| 2026-08-22 | **§6.198 follow-up** — sequence indentation must be code-body-only: global matching moved `Native SDK` into the wrong tab and caused 92↔93 segment alignment after the final authorized retry |
| 2026-08-22 | **§6.198 bounded retry** — exclude Markdown structural lines, especially tab/list labels, from unchanged-line indentation repair |
| 2026-08-22 | **§6.198 bounded retry** — normalize list/tab-label indentation from canonical EN parse→render, independently of RU technical-line indentation |
| 2026-08-22 | **#50741 outcome** — legacy layout/alignment files green; report now 🟡 only on one `coordination.md` Go fence-body difference |
| 2026-08-21 | **§6.197** — fence parity/body validation uses canonical parse→render source when malformed legacy RU changes its block count; enables verify-only recovery of #50741 |
| 2026-08-21 | **§6.196** — project Codex hook blocks staged commits unless both the Memory Bank index and a detailed Memory Bank note are staged |
| 2026-08-21 | **§6.195** — reject unsafe low-magnitude splices on technical tab/segment drift; ignore fallback YFM controls and exact SDK labels; exclude stable anchors and identical English source from false critic blockers (#37673 / #50729) |
| 2026-08-21 | **§6.194** — parse ``{% list tabs group=… %}`` as YfmTabs; queue toc siblings when EN file exists but EN toc dropped the href (#37673 / #50684) |
| 2026-08-21 | **§6.193** — refuse low-magnitude EN patch on fence/tab-pane drift; ``verify_realign_partial`` info; alt tab whitelist (#37673 / #50684) |
| 2026-08-21 | **§6.192** — keep RU `{#id}` on EN; restore glued code markers; queue EN-absent toc siblings (#37673 / #50684) |
| 2026-08-18 | **§6.191** — restore heading anchors + signature blocks; partial verify realign for large files (#49957) |
| 2026-08-11 | **§6.190** — mermaid ``["…"]`` labels ignore hyphen/word-count drift in fence_body_copy (#49578) |
| 2026-08-11 | **§6.189** — verify skips writing ``glossary.md`` EN to disk (no hybrid auto-commit); **§6.188** skip glossary critic on verify (#49578) |
| 2026-08-06 | **§6.177** — `broken_inline_code` only path+``.ext`` split; allow ``**Box `workflow`**`` ([ydb#49059](https://github.com/ydb-platform/ydb/pull/49059)) |
| 2026-08-05 | **§6.176** — heading LCS seed requires matching `{#anchor}`; block mangled bold/backtick / `( extension)` ([ydb#49040](https://github.com/ydb-platform/ydb/pull/49040) / [#48968](https://github.com/ydb-platform/ydb/pull/48968)) |
| 2026-08-05 | **§6.175** — bilingual-only ``doc_translate`` posts «перевод не требуется» ([ydb#48751](https://github.com/ydb-platform/ydb/pull/48751)) |
| 2026-08-04 | **§6.174** — href/anchor 1:1 parity + inbound fragment check; stop remapping to EN-only ids ([ydb#48792](https://github.com/ydb-platform/ydb/pull/48792)) |
| 2026-08-04 | **§6.173** — blocking heuristic for leftover ``yfmvar-N-yfmvarend`` in EN ([ydb#48812](https://github.com/ydb-platform/ydb/pull/48812)) |
| 2026-08-03 | **§6.172** — placeholder-only segments (config table keys) copy as-is; reject prose elaboration ([ydb#48785](https://github.com/ydb-platform/ydb/pull/48785) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-08-03 | **§6.171** — no LCS seed on high structure drift or empty/V-only paragraphs ([ydb#48780](https://github.com/ydb-platform/ydb/pull/48780) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-08-03 | **§6.170** — partial seed only when placeholder multiset matches; LCS key includes ph signature ([ydb#48773](https://github.com/ydb-platform/ydb/pull/48773) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-08-03 | **§6.169** — LCS partial seed (not prefix/suffix); reinsert percent-encoded ``⟦…⟧`` ([ydb#48764](https://github.com/ydb-platform/ydb/pull/48764) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-08-03 | **§6.168** — partial differential seed on align failure; cert YAML angle map ([ydb#48762](https://github.com/ydb-platform/ydb/pull/48762) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-08-03 | **§6.167** — skip `public-materials/*` + `guide-to-public-material.md`; keep EN toc slots ([ydb#48760](https://github.com/ydb-platform/ydb/pull/48760) / [#48411](https://github.com/ydb-platform/ydb/pull/48411)) |
| 2026-08-03 | **§6.166** — re-`doc_translate` uses `--force` on `ydbdoc-review/pr-*` (lease = stale info without fetch) ([ydb#46798](https://github.com/ydb-platform/ydb/pull/46798) / [#48411](https://github.com/ydb-platform/ydb/pull/48411)) |
| 2026-08-03 | **§6.165** — toc extras from translated docs only; bilingual keep EN menu labels ([ydb#48411](https://github.com/ydb-platform/ydb/pull/48411) / [#48589](https://github.com/ydb-platform/ydb/pull/48589)) |
| 2026-08-03 | **§6.164** — residual EN Cyrillic (any fence) + protect markers always blocking; critic hard-block ([ydb#48595](https://github.com/ydb-platform/ydb/pull/48595)) |
| 2026-08-03 | **§6.163** — differential seed needs kind-aligned EN (not only equal count); block unrestored `⟦…⟧` / `%E2%9F%A6…` ([ydb#48595](https://github.com/ydb-platform/ydb/pull/48595) / [#46798](https://github.com/ydb-platform/ydb/pull/46798)) |
| 2026-07-31 | **§6.162** — live `ydbdoc-verify.yml` must pass ACL/quota env (`YDBDOC_ALLOWED_ACTORS`) like translate ([ydb#48518](https://github.com/ydb-platform/ydb/pull/48518)) |
| 2026-07-31 | **§6.161** — ydb CI: rebuild_docs dispatch cancels PR `build-docs` check (attaches to main SHA); fix in [#48439](https://github.com/ydb-platform/ydb/pull/48439) |
| 2026-07-30 | **§6.160** — toc merge: never emit empty nested `items:` shell for scoped parent (#48409 / #44466) |
| 2026-07-30 | **§6.159** — ydb CI: restore dispatch-only `rebuild_docs` after #48223 mangled it ([#48410](https://github.com/ydb-platform/ydb/pull/48410)) |
| 2026-07-30 | **§6.158** — fragment repair: linking-page-relative paths; auto-slug + includes; no bare toc basenames (#48223 / #48272) |
| 2026-07-30 | **§6.157** — copy locale `_assets` (svg/png/…) RU→EN on translate+verify (#45185 / #48187) |
| 2026-07-29 | **§6.156** — heading AST (YfmIf); strip basenames → md_link ignore; fence trailing blank (#30237 / #48202) |
| 2026-07-29 | **§6.155** — section href+include merge; queue sibling pages for absent EN toc (#46446 / #48183) |
| 2026-07-28 | **§6.154** — verify include_parity uses merge-commit RU; empty ``{% include %}`` enters scope (#38700 / #48133) |
| 2026-07-28 | **§6.153** — fragment repair finds sibling via toc when RU+EN baseline both stale (#48012) |
| 2026-07-28 | **§6.152** — ``strip_unreachable_links:`` is info (not 🔴) after Variant A strip (#46889 / #48123) |
| 2026-07-28 | **§6.151** — nav-only QA recommendation is 🟢 (not ⚪) (#47856 / #48124) |
| 2026-07-28 | **§6.150** — mirror RU toc reshuffles into EN; EN page on disk fills missing toc slot (#47856) |
| 2026-07-28 | **§6.149** — fence QA: trailing YAML `#` + angle placeholder translation (#47164 host_configs) |
| 2026-07-28 | **§6.148** — include_parity blocking + auto-insert missing ``{% include %}`` (#48103 career) |
| 2026-08-23 | **§6.211** — ``doc_continue`` on translation PRs rebuilds complete source scope; verify fixups stay inline (#50840) |
| 2026-08-23 | **§6.213** — differential segment keys include inline atom payloads; anchorless tiny changes fall back to full reconstruction (#40385 / #50852) |
| 2026-08-23 | **§6.214** — href parity treats raw Unicode and percent-encoded internal fragments as URL-equivalent (#50854) |
| 2026-08-23 | **§6.215** — green translation QA remains separate from source-document build failures; #50854 exposed a stale RU node-authorization link |
| 2026-08-23 | **§6.216** — project skill «Проверка перевода»: independent semantic review + current-SHA green critic + green CI + mergeability |
| 2026-08-24 | **§6.217** — formatting-only RU diffs preserve EN through every pipeline boundary; production #49933 rerun creates no branch/PR |
| 2026-08-24 | **§6.218** — #45949 incremental href repair changes only RU-delta positions; merged-PR TOC uses historical RU base and honors removals |
| 2026-08-24 | **§6.219** — structural href/redirect changes bypass LLM; redirect impact closure; critic fail-closed; post-fixup verify reruns on current SHA |
| 2026-08-23 | **§6.212** — doc_continue loads bounded parent transcript into translation and inline-critic prompts; context is consumed, not only existence-checked |
| 2026-08-23 | **§6.210** — old merged PR differential uses merge parent as RU base; prevents zero-delta no-op omissions (#40385 / #50840) |
| 2026-08-23 | **§6.209** — green critic requires complete source scope; merged PRs use API-only changes and verify blocks omitted EN files (#40385 / #50838) |
| 2026-08-23 | **§6.208** — preserve semantic href ownership when translated list items reorder; #50797 critic false-negative regression |
| 2026-08-23 | **§6.207** — length ratio normalizes legacy fences first; removes #50741 false 0.51 warning after buildability repair |
| 2026-08-23 | **§6.206** — #50741 vector: move premature empty-fence closer after unchanged code body; 92/92 segments and buildable markdown |
| 2026-08-23 | **§6.205** — #50741 exact merge-ref build: translated EN is clean; current YDB `main` independently breaks RU authentication with an unreachable legacy node-authorization link |
| 2026-07-28 | **§6.147** — verify: odd toc indent, self-link parity, YFM tables, verify realign (#46742) |
| 2026-07-28 | **§6.146** — bilingual verify: full QA on fixup PR; ``doc_continue`` on ``verify-*`` (#46742) |
| 2026-07-28 | **§6.145** — verify fixup comment: bilingual ≠ «ветка перевода» (#46742 / #48045) |
| 2026-07-28 | **§6.144** — nav merge no-op (`target_text=None`, ok) counts as complete; unblocks #47091 re-translate |
| 2026-07-28 | **§6.143** — Docker action forwards `YDB_SA_KEY`/ops env; continue misconfig ≠ TTL; mount SA key file |
| 2026-07-28 | **§6.142** — repair EN ``path#fragment`` (stale sessions path + ldap→ldap-auth-provider); wired into translate/verify |
| 2026-07-28 | **§6.141** — nav merge no-op when EN==main; honest source comment (no false «перевод готов»); skip wasted gap-label LLM (#47856) |
| 2026-07-28 | **§6.140** — EN scope/orphan baseline = ``origin/main`` tip (not stale merge-base); orphan blocks translate push |
| 2026-07-27 | **§6.139** — walk ``YfmIf.branches`` in fence collect; parse mixed inline+block toc (``with.md`` / #48009) |
| 2026-07-27 | **§6.138** — verify verdict after finalize (no false 🟡 when auto-fix / main already clean) |
| 2026-07-27 | **§6.137** — report ``№N`` (no GitHub PR autolink); content checkout SHA before prepare |
| 2026-07-27 | **§6.136** — verify always finalizes EN fence comments; delete ``verify-*`` on re-run |
| 2026-07-27 | **§6.135** — ``doc_verify`` on bilingual source PRs (completeness + checkout/self RU/EN) |
| 2026-07-22 | **Phase K code** — ACL/quota/YDB ledger/YDB transcripts/`doc_continue` (`ops/`, wired into workflow) |
| 2026-07-22 | **§20.11** — transcripts default to YDB `run_objects` until S3 quota; flip via `YDBDOC_TRANSCRIPT_BACKEND` |
| 2026-07-22 | **§20.10** — S3 bucket `ydb-prs-translations-context` + static-key secrets; note public-read + cloud size quota |
| 2026-07-22 | **§20.8–§20.9** — YDB `runs` DDL; S3 TTL 14d + expired-continue fallback UX |
| 2026-07-22 | **§20.7** — YDB ledger: serverless endpoint/DB + SA key (`YDB_SA_KEY`, `ydb[yc]`) |
| 2026-07-21 | **§6.134** — ACL (variable), YDB daily ₽ quota, S3 full transcripts, `doc_continue` label (Phase K) |
| 2026-07-21 | **§6.133** — verify EN toc from HEAD; orphan BFS seed; safe placeholder reorder |
| 2026-07-21 | **§6.132** — differential translation: seed unchanged EN segments, LLM only diffs |
| 2026-07-20 | **§6.131** — additive TocMergeScope / TocEntryMapping (gradual TOC refactor) |
| 2026-07-20 | **§6.130** — Wikipedia resolve: langlink→wikidata→offline→None; expanded map |
| 2026-07-20 | **§6.129** — offline Wikipedia RU→EN titles for glossary/json-index (#47104 TLS) |
| 2026-07-20 | **§6.128** — merged-PR RU: overlay `#fragment` autotitles from main (#47104 YFM010 Sessions) |
| 2026-07-20 | **§6.127** — translate/critic never share a model (YC + Eliza defaults + runtime strip) |
| 2026-07-20 | **§6.126** — empty translate scope: no full-menu `only_ru` → `toc_structure_parity` (#47104 red report) |
| 2026-07-20 | **§6.125** — force_exact `{#T}` restore after critic_only verify; fragment remap (#47104)
| 2026-07-20 | **§6.124** — scope-aware `toc_structure_parity` for only_ru; soft legacy does not yellow-block (#47108) |
| 2026-07-20 | **§6.123** — always merge toc when RU changed even if EN also changed (#41271 / #47104 orphan) |
| 2026-07-20 | **§6.122** — EN toc reachability from main; no bare `{#T}` after strip; restore bare autotitle (#47108) |
| 2026-07-19 | **§6.121** — RU/EN toc structure parity; toc orphan audit script; cleanup [#47107](https://github.com/ydb-platform/ydb/pull/47107) |
| 2026-07-19 | **§6.120** — merged source PR: ``doc_translate`` RU from ``merge_commit_sha``; force exact ``{#T}`` hrefs RU→EN (#47100 YFM010) |
| 2026-07-19 | **§6.119** — `supplement_only` must not expand to all RU−EN missing hrefs (#46878) |
| 2026-07-19 | **§22.14** — TOC PR regression catalog: `test_toc_pr_regressions.py` covers validate/planner/merge/QA kinds from failing PRs |
| 2026-07-19 | **§6.118** — parse/validate keep `include_path` on href+include toc entries (#47100 false `scope_not_applied`) |
| 2026-07-19 | **§6.117** — blocking `orphan_toc_page` when translated EN `.md` is not reachable from EN toc graph |
| 2026-07-19 | **§6.116** — queue parent toc when it `include.path`s a needed child sidebar (#46569 pages translated but off EN nav tree) |
| 2026-07-17 | **§6.111–§6.115** — EN toc baseline on main; harness strip wiring; Table/YfmIf walkers; strip↔verify alignment (#39856) |
| 2026-07-15 | **§6.110** — `doc_verify` pick RU among head/merge/local (#46674); offline DDL/DML Wikipedia map |
| 2026-07-15 | **§6.108** — fix EN-only toc BFS for link strip (no RU toc pollution); strip all scoped EN md, not glossary-only (#46637) |
| 2026-07-15 | **§6.107** — glossary profile + Wikipedia Wikidata langlinks; glossary YFM003 variant A (strip unreachable internal links); re-run [#44457](https://github.com/ydb-platform/ydb/pull/44457) |
| 2026-07-15 | **§6.106** — `doc_verify` RU from merge commit + fence-body tie-break for merged source PR (#43997/#46609 false `fence_body_copy`) |
| 2026-07-15 | **§6.104–§6.105** — scope BFS gate + no cross-section absent-EN mirror (`case_43997`); Cyrillic `#fragment` remap via heading anchor map + link_locale validator |
| 2026-07-15 | **§6.103** — Eliza ordered model chains (translate/critic); env `YDBDOC_ELIZA_*_FALLBACKS` + YAML `llm.eliza` |
| 2026-07-15 | **§6.102** — drop redundant «автоисправления в этой ветке» comment on translation PR; QA report only |
| 2026-07-14 | **§6.101** — fix `format_heuristic_location` (`file_url` → `format_line_ref`); #46475 CI crash after translate OK |
| 2026-07-14 | **§6.96–§6.100** — report UX; Eliza 429 fallback; TLS split; CLI shutdown; pytest conftest isolates provider |
| 2026-07-14 | **`v0.1.0` tag moved** — includes §6.101 + Eliza/TLS hardening (after `203956a`) |
| 2026-07-14 | **§22 rollout** — re-run [#44457](https://github.com/ydb-platform/ydb/pull/44457); local debug [#43010](https://github.com/ydb-platform/ydb/pull/43010) via Eliza (`job --dry-run`) |
| 2026-07-14 | **§22 Phase J** — `scope_planner.py`; translate + verify share `TranslationScopePlan`; removed supplement modules (`d68812f` on `main`) |
| 2026-07-13 | §6.90 include closure after toc-href pass (#46393) |
| 2026-07-13 | §6.89 toc-href page supplementation (#46386) |
| 2026-07-12 | §6.85–§6.86 absent-EN toc mirror + indented `href` parse (#46349, #46346) |
| 2026-07-11 | §6.84 child toc via `include.path` (#46338) |

Older §6.x entries remain in [03-design-decisions](docs/memory-bank/03-design-decisions.md).

## Deploy status (navigation redesign)

| Artifact | State |
|----------|--------|
| `main` | §22 planner + §6.101–§6.106 (tagged `v0.1.0`) |
| Tag `v0.1.0` | **moved** on 2026-07-15 — §6.106 verify RU authority + §6.104–§6.105 |
| Tag `v0.2.0` | Unchanged — Reactor/Nirvana schedulers only |
| ydb CI `doc_translate` | **Yandex Cloud** (`YANDEX_CLOUD_*` secrets); default `YDBDOC_MODEL_PROVIDER=yandex_cloud` — **not** Eliza |
| Local `job` / Reactor | **Eliza** when `YDBDOC_MODEL_PROVIDER=eliza` + `ELIZA_OAUTH_TOKEN` (typically `~/.zshrc`) |
| Validation | [#46609](https://github.com/ydb-platform/ydb/pull/46609): re-run **`doc_verify`** after tag @ §6.106 (expect ~8 fewer false fence 🟡) |

## For AI assistants

1. Start with [01-overview](docs/memory-bank/01-overview.md) and [05-roadmap](docs/memory-bank/05-roadmap.md).
2. Open the part that matches your task (table above).
3. **Navigation / TOC work:** read [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) §22 first. It supersedes §6.71–§6.90; historical rationale stays in §6.

Cross-reference cheat sheet: `§6.*` → 03-design-decisions · `§13.*` → 06-llm-config · `§15–17` → 07-pipeline · `§22` → 09-navigation-scope.

---

**End of Memory Bank index.**
