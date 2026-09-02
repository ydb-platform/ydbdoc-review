# TASK-51797-ONE-PASS-CHAT-ONCE v003: one-request shared client primitive

## Objective, scope, and authority

Add one public, provider-neutral `chat_once` method to each concrete shared LLM
client. It performs exactly one provider request against one explicit model and
returns or raises exactly once. It exists so higher-level controllers can own
retry and fallback policy without bypassing established authentication,
transport, telemetry, TLS, parsing, or redaction behavior.

This is a prerequisite specification only. It is independent of any
unreviewed developer implementation. It does not authorize implementation and
does not alter one-pass v010 or llm-wiring-v002.

## Exact public API

Define this method on the common public client protocol and implement it on
`YandexLLMClient` and `ElizaLLMClient`:

```python
def chat_once(
    self,
    messages: Sequence[ChatCompletionMessageParam],
    *,
    explicit_model: str,
    role: Literal["analyze", "translate", "critic", "repair"] | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatResult:
    ...
```

`messages` is copied to an immutable request-local list before the network
call. `explicit_model` is stripped and must be non-empty. It is the sole model
identifier; no role/config/environment lookup may substitute or append a
model. `role` is telemetry metadata only. `None` remains allowed for existing
generic callers, while translation passes a concrete role. Temperature and
max tokens use the existing client scalar defaults only when `None`; explicit
values are validated exactly as existing `chat` validates them. The method
does not accept a model list, fallback, retry policy, parser, repair callback,
cache key, or old response.

Provider mapping is unchanged:

- Yandex uses the existing folder/model URI mapping and OpenAI-compatible
  request construction for exactly `explicit_model`;
- Eliza uses its existing internal URL mapping, OAuth header, TLS verification,
  JSON payload, and response parser for exactly `explicit_model`; as today,
  the Eliza request body contains no `model` field.

## Cardinality and forbidden behavior

One invocation has cardinality `provider_requests in {0, 1}`:

- zero only when local validation, pre-dispatch cancellation, or request
  construction fails before transport dispatch;
- one after transport dispatch, regardless of success, timeout, connection
  failure, HTTP error, malformed response, empty content, or telemetry failure.

It must never perform a second provider request. In particular it performs no:

- retry, backoff, sleep, rate-limit retry, or timeout retry;
- fallback, model chain lookup, model-unavailable advance, or role-based model
  selection;
- cache read/write, response replay, voting, merging, parsing repair, response
  repair, prompt repair, or content-quality retry;
- recursive call through `chat`, or callback capable of issuing another model
  request.

`chat_once` may share private request-building and single-dispatch helpers with
`chat`, but those helpers must be mechanically incapable of loops or model
selection. `chat` may call `chat_once` only if its externally observable
behavior remains byte-for-byte/exception-for-exception compatible; this
refactor is optional. `chat_once` must never call `chat`.

## Typed result and error contract

Success returns the existing `ChatResult` with:

- non-empty `content` under the provider's existing success rules;
- `model_slug == stripped explicit_model`;
- existing provider-specific `model_uri`;
- one successful `LLMUsage` record with `retries == 0`, the supplied role,
  measured latency, and provider token counts when available.

Failures preserve the existing public error taxonomy and original exception as
`__cause__`. Add this closed typed classification in `llm/errors.py`:

```python
class ChatOnceFailureKind(str, Enum):
    TRANSIENT = "transient"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROTOCOL_INVALID = "protocol_invalid"
    PERMANENT = "permanent"

class LLMProtocolResponseError(LLMRequestError):
    chat_once_kind = ChatOnceFailureKind.PROTOCOL_INVALID

# Existing class and existing parent remain unchanged:
class LLMModelUnavailableError(LLMRequestError):
    chat_once_kind = ChatOnceFailureKind.MODEL_UNAVAILABLE
```

Every non-cancellation exception raised by `chat_once` must be an existing
`LLMRequestError`/`LLMConfigError` compatible instance with a non-optional
`chat_once_kind`. Existing retryable transport errors receive
`TRANSIENT`; existing permanent request/config errors receive `PERMANENT`.
`LLMModelUnavailableError` is not recreated and its MRO is not changed; only
the typed class attribute is added. The new protocol-response subclass
preserves `LLMRequestError` compatibility.
Normalize once, then raise without wrapping in `LLMRetryExhaustedError`:

| Outcome | Required raised type/category |
|---|---|
| invalid local argument/model/config before dispatch | `LLMConfigError`, permanent |
| authentication/authorization, TLS verification, invalid endpoint/model, other permanent provider error | existing `LLMRequestError`, permanent, with status code when available |
| rate limit or retryable HTTP/transport status that does not mean model unavailable | existing `LLMRetryableRequestError`, `TRANSIENT`, retaining status and `retry_after_s` |
| provider/model unavailable or overloaded-model response recognized by the existing status/code predicates | existing `LLMModelUnavailableError` with unchanged `LLMRequestError` parent and `MODEL_UNAVAILABLE`; retain structured status/retry-after only when the existing instance supports them |
| connection reset/DNS/socket/transient SDK transport error | existing classified retryable request/transport error, transient |
| configured request deadline exceeded | existing classified timeout error, transient |
| HTTP success or completed provider response with malformed JSON/envelope, missing/empty choices/content | `LLMProtocolResponseError`, `PROTOCOL_INVALID`; never retryable |
| caller cancellation | original cancellation type, never converted to retryable failure |

The acquisition controller branches only on `chat_once_kind`; it must not infer
classification from exception messages. Mapping to `MODEL_UNAVAILABLE` uses
existing structured status/code predicates such as the current provider
unavailable/overloaded checks, never message substring matching. Any unmapped
non-cancellation error is normalized to a permanent compatible request error,
not guessed transient. In particular, current Eliza paths that classify
malformed/empty HTTP-200 content as `LLMRetryableRequestError` must emit
`LLMProtocolResponseError` from `chat_once`; existing `chat` retains its current
behavior on those same responses.

On every post-dispatch failure add exactly one unsuccessful `LLMUsage` record,
with the explicit slug, role, measured latency, zero/known token counts,
`retries == 0`, and `success == false`. A pre-dispatch validation/cancellation
failure adds no usage record. Telemetry or transcript-recorder failure is
logged/redacted exactly as today and cannot trigger a provider retry or replace
an otherwise successful/failed model outcome. Record a transcript only after a
successful provider response, exactly once.

## Authentication, transport, timeout, and cancellation

Reuse each client's existing credentials, headers, base URL, TLS CA/verify
policy, proxy/session behavior, secret redaction, request payload fields, and
configured scalar request timeout. `chat_once` introduces no credential or
transport configuration and never refreshes credentials by issuing a second
model request.

The configured timeout applies to the sole provider request. A timeout is
raised immediately after that request fails; this method does not sleep or
retry. The synchronous API has no new cancellation parameter. Cancellation is
defined as follows:

1. if the caller/runtime cancellation is observable before dispatch, raise it
   with zero provider requests;
2. if the HTTP/SDK layer raises cancellation during the request, propagate it
   unchanged and issue no replacement request;
3. once the provider has accepted an in-flight synchronous request, this API
   makes no stronger hard-cancellation guarantee than the existing transport;
4. the higher-level controller checks cancellation before every subsequent
   `chat_once`, so cancellation can never initiate a retry/fallback request.

## Concurrency and state

`chat_once` adds no mutable model-chain, retry, cache, or session-attempt state.
Its thread-safety guarantee is exactly the existing concrete client's
transport/usage/transcript guarantee, not stronger. Production translation
must serialize calls per client instance. Parallel workers require separate
client instances; they may use the existing supported shared `UsageTracker`
only if that tracker is already thread-safe. Document this constraint on the
public protocol. No global lock may change existing `chat` concurrency.

## Existing `chat` compatibility boundary

The public signature and behavior of `chat` remain unchanged:

- the same role/model selection, dynamic chains, retry counts, rate-limit
  handling, backoff, fallback, return values, error wrapping, telemetry totals,
  transcript behavior, and environment/config compatibility;
- existing analyzer, critic/verifier, CLI, and other non-translation callers
  require no configuration or call-site change;
- existing tests for shared chain behavior remain unchanged and green.

No existing `chat`, chain, retry, configuration, or provider symbol may be
deleted or deprecated in this prerequisite. The only public behavior addition
is explicit one-request dispatch.

## Implementation boundary

Permitted shared changes are narrowly limited to:

1. the common public client protocol/type export;
2. `YandexLLMClient.chat_once`;
3. `ElizaLLMClient.chat_once`;
4. a loop-free private single-dispatch helper extracted solely to avoid
   duplication, if necessary;
5. a narrow compatible error subtype/category in `llm/errors.py`, only if the
   existing taxonomy cannot express a required outcome;
6. focused tests and API documentation.

Do not add translation model pairs, acquisition policy, critic/repair logic, or
one-pass wiring here. Do not accept or inspect any unreviewed developer version
as the source of this contract; implementation must later be compared against
this specification line by line.

## Required tests

### Runtime provider-request spies

For both Yandex and Eliza, patch the lowest provider dispatch boundary and
assert exact call counts:

- valid response: one invocation, one provider request, one success usage, one
  transcript;
- timeout, connection error, 429, overload, model unavailable, 401/403, 404,
  5xx, malformed JSON/envelope, and empty content: one invocation, exactly one
  provider request, no sleep, no retry, no fallback;
- invalid/empty explicit model and invalid local parameters: zero provider
  requests and no usage;
- cancellation before dispatch: zero requests; cancellation raised during
  dispatch: one request and unchanged cancellation;
- transcript/telemetry recorder failure: never more than one request and does
  not alter the model result;
- explicit model B while role/config/environment names model A/C: only B is
  addressed and no model-chain resolver is called.

Spies must fail if `chat`, chain lookup, retry/backoff/sleep, cache, repair, or a
second provider dispatch is invoked. For Eliza additionally assert one POST,
model only in URL, unchanged OAuth/TLS/timeout, and no `model` body field. For
Yandex assert one SDK completion call with the exact existing model URI and
timeout-bearing client configuration.

### Typed contract and compatibility tests

- every outcome above has the exact required type, `chat_once_kind`, status,
  and cause; acquisition tests prove `PROTOCOL_INVALID` advances directly to
  fallback, `MODEL_UNAVAILABLE` advances directly, `TRANSIENT` alone receives
  one same-model retry, and `PERMANENT` blocks;
- a compatibility golden asserts the exact pre-change MRO and `isinstance`
  results for `LLMModelUnavailableError`, plus unchanged results from existing
  retry/model-unavailable predicates; adding `chat_once_kind` must not make it
  an `LLMRetryableRequestError` or alter existing `chat` branching;
- success/failure usage has explicit slug, role, `retries == 0`, and exactly one
  record after dispatch;
- messages supplied by the caller are not mutated;
- role changes telemetry only and never the selected explicit model;
- omitted scalar options use existing scalar defaults, not role/model config;
- all pre-existing `chat` unit/integration tests run unmodified and produce the
  same provider call sequence, retry/backoff sequence, errors, usage totals,
  and transcripts;
- a golden compatibility matrix covers Yandex and Eliza `chat` with explicit
  model, role chain, transient retries, rate-limit policy, fallback, permanent
  error, and exhaustion before and after this addition;
- two parallel client instances do not share model/retry state; a static test
  confirms `chat_once` contains no loop, recursive dispatch, `chat` call,
  chain/config/environment lookup, or repair/cache import.

## Acceptance criteria and handoff

The prerequisite is complete only when:

1. both concrete clients satisfy the exact API;
2. every `chat_once` test proves zero-or-one provider-request cardinality;
3. the closed four-value typed outcomes support llm-wiring-v002 without message
   parsing, with malformed/empty responses classified `PROTOCOL_INVALID` and
   provider/model unavailable classified `MODEL_UNAVAILABLE`;
4. all existing `chat` tests and compatibility goldens remain green;
5. no translation/acquisition behavior is added in this change;
6. an implementation conformance report compares any pre-existing developer
   worktree implementation against every section above and lists deviations.

## Decision gate

Implementation remains unauthorized until both conditions hold:

1. the user explicitly accepts this `chat_once` prerequisite contract; and
2. the external file reviewer returns `APPROVED` for this specification
   version.
