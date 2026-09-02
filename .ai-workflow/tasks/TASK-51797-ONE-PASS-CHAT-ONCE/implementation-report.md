# CHAT-ONCE v003 implementation report

- Worktree: `/private/tmp/ydbdoc-review-one-pass-v003`
- Branch: `agent/task-51797-one-pass-v003`
- Commit/push: none
- Verdict implemented: `chat-once-v003`, externally `APPROVED`, explicitly authorized by the user

Implemented the exact provider-neutral `chat_once(messages, *, explicit_model,
role, temperature, max_tokens)` protocol. Yandex and Eliza each have a
loop-free single-dispatch implementation. Existing `chat` behavior and its
dynamic retry/model-chain configuration remain unchanged.

The implementation adds the closed `ChatOnceFailureKind` classification,
preserves `LLMModelUnavailableError` inheritance, adds the non-retryable
`LLMProtocolResponseError`, records exactly one usage item after dispatch, and
records a transcript only after success. Arguments are validated before
dispatch, caller messages are copied, and explicit model selection never reads
role/config/environment chains.

Conformance coverage is in `tests/unit/test_llm_chat_once.py`: Yandex and Eliza
provider spies, zero/one dispatch cardinality, typed failures, usage/transcript,
model isolation, cancellation, message immutability, static no-loop/no-chain
checks, and compatibility goldens.

Verification:

```text
pytest test_llm_chat_once.py + existing client/Eliza suites: 81 passed
ruff focused files: passed
git diff --check: passed
```

Sandbox note: Eliza tests used `XDG_CACHE_HOME=/private/tmp/ydbdoc-cache` so
certificate-cache writes stay inside the writable workspace. This does not
change application behavior.
