## LLM-WIRING v002 implementation report

Status: implemented, not committed.

Implemented:

- added immutable `ModelPair`, `TranslationModelPolicy`, and
  `TranslationJobManifest`, plus the exact `TranslationChatOnce` protocol;
- added the strict `load_translation_model_policy` parser for the sole
  `translation_model_policy` namespace and six required scalar slugs;
- kept shared `LLMConfig`, `ModelChoice`, client retry, and role-chain semantics
  unchanged; the shared loader excludes the dedicated namespace before shared
  validation;
- moved acquisition ownership to `translation.acquisition`; production calls
  only `chat_once(explicit_model=...)` and classifies only registered
  `ChatOnceFailureKind` values;
- froze request bytes once per logical acquisition and enforced the exhaustive
  two-model/four-request transition table;
- propagated the frozen manifest through the real translate workflow,
  orchestrator, file translator, transaction, base translation, critic, and
  bounded repair;
- removed the production fallback to `model_chain_for_role("translate")`;
- connected the existing isolated local repair controller after deterministic
  render. Critic and re-critic use only the critic pair; repair uses only the
  repair pair; permanent/unknown repair acquisition errors now block instead
  of being converted into another logical repair attempt;
- added AST forbidden-edge/inverse-allowlist checks and runtime wiring,
  acquisition matrix, strict configuration, frozen manifest, and shared-chain
  spy tests.

Verification:

- LLM-WIRING plus stage-1 `chat_once`: 40 passed;
- shared config/client/Eliza regressions: 81 passed;
- focused Ruff: passed;
- `git diff --check`: passed;
- Python compileall for changed production modules: passed.

Known pre-existing migration work outside this stage:

- older one-pass/orchestrator/transaction tests still call the superseded
  tuple/mapping APIs and omit the required frozen manifest. They must be
  replaced as part of the v010 legacy-test deletion/migration stage, not kept
  through a compatibility fallback in production.
