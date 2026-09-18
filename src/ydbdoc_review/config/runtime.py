"""Technical model configuration; no product profiles or implicit tariffs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ydbdoc_review.config.defaults import default_runtime_data
from ydbdoc_review.config.loader import SettingsError
from ydbdoc_review.config.tokenization import ProviderTokenCounter
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.store import rub_resolver


@dataclass(frozen=True)
class Runtime:
    choices: dict[str, ModelChoice]
    budget: RequestBudget
    resolver: object
    timeout_s: float
    glossary: tuple[tuple[str, str], ...]
    secrets: tuple[str, ...]


def load_runtime(path: Path | None = None, *, token_counter=None) -> Runtime:
    """Load operator supplied endpoints, capacities and trusted RUB/token rates.

    Tokenization uses the configured models, including alternatives. Construction
    performs no requests; capacity checks use the provider tokenizer lazily.
    context_tokens must fit every configured endpoint.
    """
    try:
        data = json.loads(path.read_text()) if path is not None else default_runtime_data()
        if set(data) - {'models', 'context_tokens', 'max_output_tokens', 'timeout_s',
                        'tariffs_rub_per_million', 'glossary'}:
            raise ValueError('Unknown technical configuration field')
        secrets = []

        def endpoint(value):
            if set(value) - {'provider', 'base_url', 'model', 'token_env', 'folder_id', 'reasoning_effort'}:
                raise ValueError('Unknown endpoint field')
            token = os.environ.get(value['token_env'], '')
            secrets.append(token)
            return Endpoint(value['provider'], value['base_url'], value['model'], token,
                            value.get('folder_id'), value.get('reasoning_effort'))

        choices = {}
        if set(data['models']) != {'translation', 'critic', 'repair'}:
            raise ValueError('Configure translation, critic and repair models')
        for role, value in data['models'].items():
            if set(value) - {'main', 'alternative'}:
                raise ValueError('Only one alternative is supported')
            choices[role] = ModelChoice(endpoint(value['main']),
                                        endpoint(value['alternative']) if value.get('alternative') else None)
        context, output = data['context_tokens'], data['max_output_tokens']
        if type(context) is not int or type(output) is not int or not 0 < output < context:
            raise ValueError('Invalid model capacity')
        tariffs = {(row['provider'], row['model']):
                   (Decimal(str(row['input'])), Decimal(str(row['output'])))
                   + ((Decimal(str(row['cached_input'])),) if 'cached_input' in row else ())
                   for row in data.get('tariffs_rub_per_million', [])}
        timeout = float(data.get('timeout_s', 120))
        if not 0 < timeout <= 600:
            raise ValueError('Invalid HTTP timeout')
        glossary = tuple(tuple(pair) for pair in data.get('glossary', []))
        if any(len(p) != 2 or not all(isinstance(x, str) and x for x in p) for p in glossary):
            raise ValueError('Invalid glossary')
        count = token_counter if token_counter is not None else ProviderTokenCounter(
            [endpoint for choice in choices.values()
             for endpoint in (choice.main, choice.alternative) if endpoint is not None],
            timeout_s=timeout)
        if not callable(count) or not callable(getattr(count, "count_output", None)):
            raise ValueError("Model tokenizer must count complete messages and raw output")
        return Runtime(choices, RequestBudget(context, output, count, count.count_output), rub_resolver(tariffs=tariffs),
                       timeout, glossary, tuple(secrets))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise SettingsError('Invalid technical model configuration; check endpoints, credentials, '
                            'capacity and explicit RUB tariffs.') from exc
