"""Technical defaults used by the existing YDB Actions integration."""
from __future__ import annotations

import os


def default_runtime_data() -> dict:
    # The deployed workflow uses Yandex Cloud. Other endpoints remain configurable
    # through the optional trusted JSON; product limits always come from Actions.
    provider = os.environ.get('YDBDOC_MODEL_PROVIDER', '').strip() or 'yandex_cloud'
    if provider not in ('yandex', 'yandex_cloud'):
        raise ValueError('For other providers supply the technical JSON configuration')
    folder = os.environ.get('YANDEX_CLOUD_FOLDER_DOC_REVIEW', '').strip()

    def endpoint(model):
        return dict(provider='yandex_cloud', base_url='https://ai.api.cloud.yandex.net/v1',
                    model=model, token_env='YANDEX_CLOUD_API_KEY_DOC_REVIEW', folder_id=folder)

    def choice(variable, main, alternative):
        selected = os.environ.get(variable, '').strip() or main
        result = {'main': endpoint(selected)}
        if selected != alternative:
            result['alternative'] = endpoint(alternative)
        return result

    # No guessed reasoning control: DeepSeek V4 Flash reasoning-off support in YC is unresolved.
    # See docs/model-response-controls.md before changing model or output reserve.
    translation = choice('YDBDOC_MODEL_TRANSLATE', 'deepseek-v4-flash', 'yandexgpt-5-pro')
    critic = choice('YDBDOC_MODEL_CHECK', 'yandexgpt-5.1', 'yandexgpt-5-lite')
    return dict(models={'translation': translation, 'critic': critic, 'repair': translation},
                context_tokens=32768, max_output_tokens=8000, timeout_s=240,
                # Yandex AI Studio synchronous RUB tariffs, checked 2026-09-19:
                # https://aistudio.yandex.ru/ru/docs/ai-studio/pricing
                tariffs_rub_per_million=[
                    dict(provider='yandex_cloud', model=model, input=inp, output=out,
                         **({'cached_input': cached} if cached is not None else {}))
                    for model, inp, out, cached in (
                        ('deepseek-v4-flash', '300', '500', '75'),
                        ('yandexgpt-5.1', '800', '800', None),
                        ('yandexgpt-5-pro', '1200', '1200', None),
                        ('yandexgpt-5-lite', '200', '200', None),
                    )])
