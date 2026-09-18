"""Provider prompt cache must not be charged as ordinary input."""
from decimal import Decimal

import pytest

from ydbdoc_review.model import Endpoint
from ydbdoc_review.store import rub_resolver


def test_yc_cached_prompt_rate():
    endpoint = Endpoint('yandex_cloud', 'https://example.test/v1', 'deepseek-v32', 'test', 'folder')
    resolver = rub_resolver(tariffs={('yandex_cloud', 'deepseek-v32'):
                                    (Decimal(500), Decimal(800), Decimal(130))})
    response = {'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                          'prompt_tokens_details': {'cached_tokens': 600}}}
    assert resolver(endpoint, response) == Decimal('0.358')
    del response['usage']['prompt_tokens_details']
    assert resolver(endpoint, response) == Decimal('0.580')


@pytest.mark.parametrize('cached', [-1, 1001, True, None, '10'])
def test_invalid_cached_usage_is_unknown(cached):
    endpoint = Endpoint('yandex_cloud', 'https://example.test/v1', 'deepseek-v32', 'test', 'folder')
    resolver = rub_resolver(tariffs={('yandex_cloud', 'deepseek-v32'):
                                    (Decimal(500), Decimal(800), Decimal(130))})
    assert resolver(endpoint, {'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                                       'prompt_tokens_details': {'cached_tokens': cached}}}) is None
