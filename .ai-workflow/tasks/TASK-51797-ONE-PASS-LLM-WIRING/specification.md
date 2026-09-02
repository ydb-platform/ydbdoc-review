# TASK-51797-ONE-PASS-LLM-WIRING v002: translation-local model wiring

## Status and authority

This amendment resolves only the LLM wiring ambiguity in approved one-pass
v010. It does not authorize implementation. Every v010 invariant remains
mandatory. If this amendment conflicts with v010 on wiring, this amendment
controls; otherwise v010 controls.

## Decision

Choose option 2. Do not change the semantics, configuration shape, retry logic,
or dynamic chains of the shared LLM client for analyzer, verifier, or any other
non-production-translation consumer.

Add translation-local typed configuration and controllers that call the
already available single-network-call primitive `chat_once(explicit_model)`.
Production translation must never call shared `chat(...)`,
`model_chain_for_role(...)`, `_model_chain_for_role(...)`, or any shared
automatic chain/fallback helper.

### Exact single-call boundary

`chat_once` is a prerequisite public capability of each runtime LLM client in
the implementation base supplied to development. This amendment does not add,
edit, or wrap `llm/client.py`. Production translation receives only this
translation-local structural protocol, defined in
`ydbdoc_review.translation.model_policy`:

```python
class TranslationChatOnce(Protocol):
    def chat_once(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        explicit_model: str,
        role: Literal["translate", "critic", "repair"],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...
```

One invocation means exactly one provider network request: no retry, fallback,
chain lookup, or environment/config reread. It may record usage/transcripts and
classify its one response/exception. Bootstrap fails closed before translation
if the injected client lacks this callable contract. Do not emulate it with
shared `chat`, call private `_call_once`, duplicate provider SDK logic in
translation, or modify the shared client to manufacture it. If an
implementation base lacks this already-approved primitive, report a
prerequisite blocker rather than broadening this task.

This is the narrow safe boundary: v010 forbids dynamic selection inside the
production translation closure, not shared behavior used by unrelated jobs.
Changing shared client/config would create unrequired broad regressions and is
therefore prohibited by this amendment.

## Translation-local typed configuration

Create a translation-owned module, named
`ydbdoc_review.translation.model_policy`, containing exactly these immutable
types (equivalent naming is not allowed):

```python
@dataclass(frozen=True)
class ModelPair:
    primary: str
    fallback: str

@dataclass(frozen=True)
class TranslationModelPolicy:
    translate: ModelPair
    critic: ModelPair
    repair: ModelPair
```

Validation at job construction must reject empty slugs and equal primary and
fallback slugs within a pair. Slugs may coincide across roles, as v010 allows.
Resolve the six slugs exactly once before the first model request and copy them
into the immutable job manifest. No controller may reread environment or
shared config after manifest construction.

The translation command's adapter reads only the dedicated serialized
namespace defined below, and the resolved value is exactly two slugs per role.
Missing `repair` configuration is a configuration error. It must not
inherit, alias, or derive a dynamic chain at runtime. Configuration migration
must introduce explicit translation-local `translate`, `critic`, and `repair`
pairs without changing shared `LLMConfig.models` or `ModelChoice` semantics.

The sole serialized namespace and exact keys are:

```yaml
translation_model_policy:
  translate: {primary: <slug>, fallback: <slug>}
  critic: {primary: <slug>, fallback: <slug>}
  repair: {primary: <slug>, fallback: <slug>}
```

No aliases, list form, inherited defaults, environment-only overrides, or
legacy `models.*.chain` fallback are accepted. A translation-owned
`load_translation_model_policy(raw_mapping) -> TranslationModelPolicy` parses
only this namespace. Application bootstrap passes the raw subtree to this
loader and stores its typed result in the manifest. Shared `load_config`,
`LLMConfig`, `ModelChoice`, and role-chain resolution do not parse, normalize,
default, or validate these keys. Unknown keys in the namespace, missing keys,
non-string values, empty strings, and equal values within a pair fail before
any RU/EN read or model call. Shared configuration remains independently
available to non-translation commands.

## One bounded acquisition controller

Create/use a translation-owned generic `AcquisitionController[T]`. Its input
is one `ModelPair`, immutable request bytes, a role label, and a strict parser.
It alone implements this exhaustive v010 outcome table independently per role:

| Single network outcome | Next transition |
|---|---|
| timeout or registered transient transport/rate-limit error on the model's first request | retry that model exactly once |
| the same class on the model's second request | advance to fallback, or exhaust on fallback |
| registered model-unavailable error on either request | advance immediately to fallback without spending the remaining same-model request, or exhaust on fallback |
| authentication, authorization, invalid endpoint/model slug, malformed local configuration, or other permanent configuration error | block immediately; neither retry nor advance |
| empty/refusal, invalid JSON/schema, missing/extra/duplicate prose ID, changed/lost/crossed token, or other protocol-invalid response | advance immediately to fallback without same-model retry, or exhaust on fallback |
| protocol-valid parsed payload | accept and stop acquisition |

The maximum is four network requests: primary up to two, then fallback up to
two. Immediate transitions may use fewer. Classification is deterministic and
registered; an unknown error fails closed as permanent. The first
protocol-valid parsed value is the only accepted value. Each request invokes only
`chat_once(..., explicit_model=selected_slug)`; the low-level primitive must not
select another model or retry internally. No voting, merging, quality ranking,
or response-fed repair is allowed.

Instantiate the same controller with the manifest's `translate`, `critic`, or
`repair` pair. Role-specific parsers remain separate. Repair logical-attempt
accounting wraps acquisition exactly as v010 specifies: issuing a repair
request consumes one logical repair attempt even if its internal acquisition
exhausts.

## Exact production wiring

The only allowed production path is:

```text
production translate entrypoint
  -> build and freeze TranslationModelPolicy in job manifest
  -> one-pass RU queue/dependency planner
  -> protect RU atoms and extract prose
  -> AcquisitionController(translate pair) -> chat_once(explicit model)
  -> strict translation parser -> one accepted base prose payload
  -> deterministic restore and single RU-derived full render
  -> AcquisitionController(critic pair) -> chat_once(explicit model)
  -> read-only structured critic
  -> deterministic repair for source-owned structure/link/fragment/anchor rules
  -> for eligible prose or constrained English-anchor proposal only:
       local one-block repair controller
       -> AcquisitionController(repair pair) -> chat_once(explicit model)
       -> range/immutable-atom guard -> insert one block
       -> whole-document re-critic with critic pair
  -> all global invariants and v010 caps
  -> provenance recheck -> atomic publication, or warning-only failure artifact
```

Connect the already isolated local repair controller at the indicated point.
It must not be reachable from base translation, deterministic restoration, or
non-eligible findings. The critic never mutates or invokes repair itself; the
translation controller owns the transition.

## Physical deletion versus retained shared symbols

Delete these production-translation implementations and their tests, exports,
imports, flags, and configuration keys rather than leaving dormant fallbacks:

- incremental/full selection, magnitude thresholds, differential seeds,
  old-EN splice/reconstruction, and old-EN rendering inputs;
- `_translate_batch_once`, `_translate_batch_with_model`, and any production
  wrapper that obtains `model_chain_for_role("translate")`;
- production use of `translation.repair.repair_segment_translation` and
  `translation.critic_retranslate`; remove the modules if no non-production
  caller remains, otherwise rename/move them out of the production translation
  package and prove no production reachability;
- model-driven post-render Cyrillic prose/fence writers, including production
  calls to `translate_cyrillic_fence_comments_with_client` and any equivalent
  `validation.prose_cyrillic` translator;
- fragment, placeholder, or structural repair writers that mutate staged EN
  outside the v010 deterministic atom/restoration and constrained anchor/link
  owners. Validators may remain read-only.

Retain shared `LLMConfig`, `ModelChoice`, `chat`,
`model_chain_for_role`, `_model_chain_for_role`, `_eliza_model_chain`, shared
retry/chain helpers, and their tests only because non-translation consumers use
them. They need not be physically deleted if all of the following are proven:

1. no import or call from the production translation closure;
2. no translation-local configuration adapter returns `ModelChoice` or an
   arbitrary-length list to a controller;
3. no generic callback injected into translation can call shared `chat`;
4. a static allowlist identifies the non-translation entrypoints that retain
   them, and CI fails on any new production-translation edge.

The source files may coexist in the repository. "Removed" in v010 means
removed from the production translate architecture and physically deleted when
translation-owned; it does not require breaking a shared non-translation API
whose call graph is proven disjoint.

## Required tests

### Static and reachability tests

Build the production closure from the real translate CLI/workflow entrypoints,
following imports, calls, callbacks, factories, and dependency injection. Fail
CI if that closure references any of:

- `chat`, `model_chain_for_role`, `_model_chain_for_role`,
  `_eliza_model_chain`, `ModelChoice.chain`, shared role-chain helpers, or an
  arbitrary-length model sequence;
- `_translate_batch_once`, `_translate_batch_with_model`,
  `repair_segment_translation`, `critic_retranslate`, model-driven fence or
  Cyrillic post-render translation;
- incremental/full/magnitude/differential/splice/old-EN-seed symbols;
- more than the approved deterministic source-owned writers.

Add an inverse test proving the retained shared-chain symbols still have at
least one allowlisted non-translation caller. A mere string grep or statement
that a branch is unused is insufficient. The test must parse the Python AST
and resolve local imports/aliases; add a runtime dependency spy for factory and
callback edges that static resolution cannot prove.

### Runtime wiring tests

- manifest contains exactly six frozen slugs and rejects equal slugs within a
  pair, empty slugs, or absent repair pair;
- each role sends the exact immutable payload through its own pair in the
  deterministic four-network-attempt order;
- protocol-invalid primary advances directly to fallback; transport failure
  retries the same slug once; no fifth network call is possible;
- accepted response stops acquisition and no other response is rendered,
  compared, or merged;
- spies make shared `chat`, chain lookup, config reread, and old-EN read raise;
  every production translation scenario still succeeds or fails with its
  declared v010 report;
- repair uses only the repair pair, consumes one logical attempt per issued
  request, respects 2/4/8/9 caps, and re-critic uses only the critic pair;
- analyzer and other allowlisted non-translation jobs retain their existing
  shared chain behavior unchanged.

### End-to-end v010 invariants

Retain and run all v010 acceptance cases: exactly one accepted base prose
payload and one full RU-derived render per file; protected non-prose atoms;
recursive link/include BFS with a shared 20-added-file budget; deterministic
anchor parity and global inbound-link integrity; stale RU/EN provenance block;
all-or-nothing publication; zero protect markers in inspectable output; and
warning-only structured artifacts on failure. Add an assertion that no staged
EN is published when any acquisition, critic, local repair, reachability,
global invariant, or provenance check fails.

## Migration and completion criteria

1. Introduce translation-local types/controller and manifest serialization.
2. Wire base acquisition and critic through explicit pairs.
3. Connect bounded local repair and deterministic writers.
4. Remove translation-owned legacy paths and tests; preserve shared APIs only
   behind the proven non-translation boundary.
5. Add static closure, runtime spy, acquisition matrix, and v010 end-to-end
   tests.
6. Delete old translation configuration keys and documentation only after no
   production or test fixture reads them.

Completion requires zero forbidden edges in the production closure, green
shared-consumer regression tests, green full v010 matrix, and no hidden
fallback selected by environment, provider, or model response.

## Decision gate

Implementation remains unauthorized until both conditions hold:

1. the user explicitly accepts this translation-local wiring boundary; and
2. the external file reviewer returns `APPROVED` for this amendment version.
