# ruff: noqa: RUF001 -- Russian diagnostics and test text are intentional.
import itertools
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import (
    MarkerError,
    RequestBudget,
    chunk_document,
    protect,
    restore,
    translate_document,
    translate_files,
    translation_messages,
)
from ydbdoc_review.model import Endpoint, ModelChoice

CHOICE = ModelChoice(Endpoint("eliza", "https://example.test", "test", "test"))


def COUNT(ms):
    return sum(len(m["content"]) + 13 for m in ms) + 7


class Fake:
    def __init__(self, actions=()):
        self.actions = iter(actions)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        t = messages[-1]["content"].split("\n\n", 1)[1]
        action = next(self.actions, lambda t: t)
        if isinstance(action, Exception):
            raise action
        return SimpleNamespace(content=action(t), finish_reason="stop")


def run(source, mutation=lambda t: t):
    f = Fake([mutation])
    r = translate_document(
        source,
        path="p.md",
        source_lang="ru",
        target_lang="en",
        client=f,
        choice=CHOICE,
        budget=RequestBudget(50000, 10000, COUNT),
    )
    assert len(f.calls) == 1
    return r


@pytest.mark.parametrize(
    "source",
    [
        '---\ntitle: "Заголовок"\ndescription: >-\n  Описание\n  продолжение\nconfig: "raw"\n---\nBody.\n',
        "> - **Текст** [ссылка](../ru/a.md#x)\r\n>   следующий\r\n",
        "Literal ⟦C1⟧ ⟦CC1⟧ ⟦x⟧ unclosed ⟦ and ⟧. `same` [same](same)\n",
        "```cpp\nint x = 1; // Текст\n/* Текст */\n```\n",
        "```mermaid\nflowchart LR\n  A[Текст] --> B[Текст]\n```\n",
        "- first `x`.\n- second `y`.\n\n| A | B |\n|--|--|\n| [x](a.md) | `y` |\n",
    ],
)
def test_independent_corpus_roundtrip(source):
    p = protect(source)
    assert restore(p, p.text) == source
    for a in p.atoms:
        assert source[a.start : a.end] == a.raw


@pytest.mark.parametrize("mode", ["drop", "duplicate", "swap", "unknown", "fragment"])
def test_marker_attack(mode):
    p = protect("⟦C1⟧ `foo` текст `bar`\n")
    ids = [a.marker for a in p.atoms]
    t = p.text
    if mode == "drop":
        t = t.replace(ids[0], "")
    elif mode == "duplicate":
        t = t.replace(ids[0], ids[0] * 2)
    elif mode == "swap":
        t = t.replace(ids[0], "TEMP").replace(ids[1], ids[0]).replace("TEMP", ids[1])
    elif mode == "unknown":
        t += "⟦new⟧"
    else:
        t += "⟦fragment"
    with pytest.raises(MarkerError):
        restore(p, t)


@pytest.mark.parametrize("limit", [40, 75, 130, 500])
def test_chunk_boundaries(limit):
    s = "".join(f"Абзац {i} с `x{i}`. Ещё {i}.\r\n\r\n" for i in range(60))
    p = protect(s)
    chunks = chunk_document(p, lambda t: len(t) <= limit)
    assert "".join(c.text for c in chunks) == p.text
    assert "".join(restore(p, c.text.strip(), expected=c.text) for c in chunks) == s
    assert all(a.end == b.start for a, b in itertools.pairwise(chunks))
    assert chunks[0].start == 0 and chunks[-1].end == len(p.text)


def test_full_request_exact_capacity_and_single_request():
    s = "| A | B |\n|---|---|\n" + "| Текст | `value` |\n" * 40
    p = protect(s)
    count = COUNT(translation_messages(p.text, source_lang="ru", target_lang="en", path="p.md"))
    b = RequestBudget(count + 10000, 10000, COUNT)
    f = Fake()
    r = translate_document(
        s, path="p.md", source_lang="ru", target_lang="en", client=f, choice=CHOICE, budget=b
    )
    assert len(f.calls) == 1 and r.text == s and not r.issues
    assert not RequestBudget(count + 9999, 10000, COUNT).fits(f.calls[0][0])


@pytest.mark.parametrize(
    "source,injection",
    [
        ("```python\nx = 1 # Текст\n```\n", "Comment\nx = 999 # injected"),
        ("```cpp\n/* Текст */\nint x = 1;\n```\n", "Comment */ int injected=999; /*"),
        ("```mermaid\nflowchart LR\nA[Текст] --> B[End]\n```\n", "Text] --> X[Injected"),
    ],
)
def test_code_injection_is_flagged(source, injection):
    r = run(source, lambda t: t.replace("Текст", injection))
    assert r.issues and r.text


@pytest.mark.parametrize(
    "body,replacement",
    [
        ("title: 'Текст'\n", "User's guide"),
        ('title: "Текст"\n', 'The "best" guide'),
        ('config: safe\ntitle: "Текст"\n', 'Text"\nconfig: hacked\nextra: "'),
        ("config: safe\ntitle: |-\n  Текст\n", "Text\nconfig: hacked"),
        ("config: safe\ntitle: >-\n  Текст\n", "Text\nconfig: hacked"),
    ],
)
@pytest.mark.parametrize("key", ["title", "description"])
def test_yaml_scalar_boundaries_flag_or_preserve(body, replacement, key):
    body = body.replace("title:", key + ":")
    s = "---\n" + body + "---\nBody.\n"
    r = run(s, lambda t: t.replace("Текст", replacement))
    assert r.text
    try:
        parsed = yaml.safe_load(r.text.split("---\n")[1])
        valid = parsed.get(key) == replacement and parsed.get("config", "safe") == "safe"
    except yaml.YAMLError:
        valid = False
    assert valid or r.issues, (r.text, r.issues)


def test_protected_values_comments_and_urls_translation():
    s = '---\ntitle: "Текст"\nconfig: "Текст"\n---\n[Текст](../ru/Текст.md) `Текст` https://host/Текст\n```yaml\nx: "Текст" # Текст\n```\n'
    r = run(s, lambda t: t.replace("Текст", "Text"))
    assert (
        r.text
        == '---\ntitle: "Text"\nconfig: "Текст"\n---\n[Text](../ru/Текст.md) `Текст` https://host/Текст\n```yaml\nx: "Текст" # Text\n```\n'
    )
    assert not r.issues


def test_partial_damaged_failure_no_retries():
    f = Fake([lambda t: t.replace("⟦C1⟧", ""), RuntimeError("offline"), lambda t: "Third."])
    snapshots = []
    rs = translate_files(
        [("one", "First `atom`."), ("two", "Second."), ("three", "Third.")],
        source_lang="ru",
        target_lang="en",
        client=f,
        choice=CHOICE,
        budget=RequestBudget(50000, 10000, COUNT),
        on_progress=snapshots.append,
    )
    assert len(f.calls) == 3 and rs["one"].text and rs["one"].issues
    assert rs["two"].unfinished and rs["three"].text == "Third."
    assert snapshots[0].path == "one" and snapshots[0].text
