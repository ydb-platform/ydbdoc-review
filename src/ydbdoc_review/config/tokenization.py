"""Model-specific tokenization; never infer tokens from UTF-8 byte counts."""
from __future__ import annotations

import hashlib
import json

import requests

from ydbdoc_review.document import CapacityError
from ydbdoc_review.llm.tls import public_ca_bundle

# Exact model identities: native YC Tokenizer for YandexGPT; pinned official
# local tokenizer for DeepSeek V4 Flash. No alias/model substitution.
_VERIFIED_MODELS = frozenset({'yandexgpt-5-pro', 'yandexgpt-5.1', 'yandexgpt-5-lite',
                              'deepseek-v4-flash'})


class ProviderTokenCounter:
    """Count using each configured model's own tokenizer.

    The common budget must fit all configured primary/alternative endpoints.
    Construction is offline; requests are lazy and have no generation fallback.
    Only counts and digest keys are cached for this run, never prompt copies.
    """

    def __init__(self, endpoints, *, timeout_s=30):
        self.endpoints = tuple(dict.fromkeys(endpoints))
        self.timeout_s = min(timeout_s, 30)
        self._cache = {}

    def for_choice(self, choice):
        counter = ProviderTokenCounter(
            [e for e in (choice.main, choice.alternative) if e is not None],
            timeout_s=self.timeout_s)
        counter._cache = self._cache
        return counter

    def _validate_endpoints(self):
        if not self.endpoints:
            raise CapacityError('No configured models for tokenization')
        # Validate the entire choice before sending any document text: a valid
        # primary must not hide an unsupported alternative (or vice versa).
        for endpoint in self.endpoints:
            if (endpoint.provider != 'yandex_cloud'
                    or endpoint.base_url.rstrip('/') != 'https://ai.api.cloud.yandex.net/v1'
                    or endpoint.model not in _VERIFIED_MODELS):
                raise CapacityError('No verified tokenizer for configured provider/model; '
                                    'configure model-specific tokenization before generation')

            if endpoint.model == 'deepseek-v4-flash' and endpoint.reasoning_effort not in (None, 'none', 'high'):
                raise CapacityError('No verified DeepSeek V4 encoding for reasoning_effort')

    def _count(self, endpoint, method, payload):
        if endpoint.model == 'deepseek-v4-flash':
            from ydbdoc_review.config.deepseek_v4 import count_messages, count_output
            if method == 'tokenize':
                return count_output(payload['text'])
            return count_messages([{'role': m['role'], 'content': m['text']}
                                   for m in payload['messages']], endpoint.reasoning_effort)
        payload = dict(payload, modelUri=f'gpt://{endpoint.folder_id}/{endpoint.model}')
        key = (endpoint, method, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest())
        if key in self._cache:
            return self._cache[key]
        try:
            response = requests.post(
                f'https://llm.api.cloud.yandex.net/foundationModels/v1/{method}',
                json=payload,
                headers={'Authorization': f'Api-Key {endpoint.token}',
                         'x-folder-id': endpoint.folder_id, 'x-data-logging-enabled': 'false'},
                timeout=self.timeout_s, verify=public_ca_bundle(),
            )
            response.raise_for_status()
            tokens = response.json()['tokens']
            if not isinstance(tokens, list) or any(not isinstance(t, dict) for t in tokens):
                raise ValueError('Invalid tokenizer tokens')
            count = len(tokens)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            # Do not expose response bodies or exception URLs/headers containing credentials.
            raise CapacityError(f'Tokenizer unavailable for {endpoint.provider}/{endpoint.model}; '
                                'cannot establish request capacity before generation') from None
        self._cache[key] = count
        return count

    def __call__(self, messages):
        self._validate_endpoints()
        if any(set(m) != {'role', 'content'} or not isinstance(m['content'], str)
               for m in messages):
            raise CapacityError('Tokenizer supports text messages only')
        if any(e.model == 'deepseek-v4-flash' for e in self.endpoints):
            from ydbdoc_review.config.deepseek_v4 import render_messages
            render_messages(messages, thinking=False)
        payload = {'messages': [{'role': m['role'], 'text': m['content']} for m in messages]}
        return max(self._count(e, 'tokenizeCompletion', payload) for e in self.endpoints)

    def count_output(self, text):
        self._validate_endpoints()
        if not text:
            return 0
        return max(self._count(e, 'tokenize', {'text': text}) for e in self.endpoints)
