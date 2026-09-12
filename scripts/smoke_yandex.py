"""Smoke test: directly call Yandex AI Studio for chat completions.

Prints raw response so we can see what we get.
"""

from __future__ import annotations

import json
import os
import sys
from textwrap import dedent

from openai import OpenAI


def get_creds() -> tuple[str, str, str]:
    folder = (
        os.environ.get("YDBDOC_YC_FOLDER_ID")
        or os.environ.get("YANDEX_CLOUD_FOLDER_DOC_REVIEW")
        or os.environ.get("YANDEX_CLOUD_FOLDER")
        or os.environ.get("YANDEX_CLOUD_FOLDER_2")
    )
    api_key = (
        os.environ.get("YDBDOC_YC_API_KEY")
        or os.environ.get("YANDEX_CLOUD_API_KEY_DOC_REVIEW")
        or os.environ.get("YANDEX_CLOUD_API_KEY")
        or os.environ.get("YANDEX_CLOUD_SECRET_KEY")
    )
    if not folder or not api_key:
        print("ERROR: missing folder id or api key in env.", file=sys.stderr)
        print("Tried YDBDOC_YC_FOLDER_ID, YANDEX_CLOUD_FOLDER_*, YANDEX_CLOUD_SECRET_KEY")
        sys.exit(1)
    base_url = os.environ.get(
        "YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1"
    )
    return folder, api_key, base_url


def call(client: OpenAI, folder: str, model_slug: str, system: str, user: str) -> str:
    model_uri = f"gpt://{folder}/{model_slug}"
    print(f"\n{'=' * 70}")
    print(f"Model: {model_uri}")
    print(f"System: {system[:200]}")
    print(f"User: {user[:200]}")
    print(f"{'-' * 70}")

    resp = client.chat.completions.create(
        model=model_uri,
        temperature=0.1,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content
    print(f"Response:\n{content}")
    print(f"{'=' * 70}\n")
    return content or ""


def main() -> None:
    folder, api_key, base_url = get_creds()
    client = OpenAI(api_key=api_key, base_url=base_url)

    # Test 1: trivial translation.
    call(
        client,
        folder,
        "yandexgpt-5.1",
        system="You are a professional technical translator.",
        user="Translate to English: Используйте параметризованные запросы для повышения производительности.",
    )

    # Test 2: JSON request — see if the model returns clean JSON.
    json_system = dedent(
        """
        You are a translator. Translate each input segment to English.
        Return ONLY a JSON object in this exact format, with no prose around it,
        no markdown fences, no explanation:

        {"translations": [{"id": "s0001", "text": "..."}, ...]}
        """
    ).strip()
    json_user = dedent(
        """
        {"segments": [
          {"id": "s0001", "text": "Привет, мир."},
          {"id": "s0002", "text": "Используйте ⟦C1⟧ для запуска."}
        ]}
        """
    ).strip()
    raw = call(client, folder, "yandexgpt-5.1", json_system, json_user)
    print("Parsing attempt:")
    try:
        parsed = json.loads(raw.strip().strip("`").lstrip("json").strip())
        print("  OK:", json.dumps(parsed, ensure_ascii=False))
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test 3: Same JSON request to DeepSeek (different family).
    raw = call(client, folder, "deepseek-v32", json_system, json_user)
    print("Parsing attempt:")
    try:
        parsed = json.loads(raw.strip().strip("`").lstrip("json").strip())
        print("  OK:", json.dumps(parsed, ensure_ascii=False))
    except Exception as e:
        print(f"  FAILED: {e}")


if __name__ == "__main__":
    main()

