import pytest

from ydbdoc_review.validation.fence_comments import translate_cyrillic_fence_comments
from ydbdoc_review.validation.fence_integrity import (
    code_blocks_from_text,
    fence_content_matches_source,
)
from ydbdoc_review.validation.heuristics import _classify_heuristic, check_cyrillic_in_en_all_fences


@pytest.mark.parametrize(('lang', 'code', 'expected'), [
    ('python', 'print("Привет # строка") # Приветствие', 'print("Привет # строка") # Greeting'),
    ('python', '\"\"\"Докстрока # строка\"\"\"\n# Приветствие', '\"\"\"Докстрока # строка\"\"\"\n# Greeting'),
    ('cpp', 'auto x = "https://хост // строка"; /* Приветствие */', 'auto x = "https://хост // строка"; /* Greeting */'),
    ('cpp', '/* Приветствие\n * Приветствие\n */\nint x;', '/* Greeting\n * Greeting\n */\nint x;'),
    ('sql', "SELECT '-- строка'; -- Приветствие", "SELECT '-- строка'; -- Greeting"),
    ('xml', '<x title="Текст"><!-- Приветствие --></x>', '<x title="Текст"><!-- Greeting --></x>'),
    ('unknown-language', '# Приветствие', '# Приветствие'),
    ('json', '{"a": "# Приветствие"}', '{"a": "# Приветствие"}'),
])
def test_language_comments_preserve_code(lang, code, expected):
    seen = []
    def translate(text):
        seen.append(text)
        assert text == 'Приветствие'
        return 'Greeting'
    out = translate_cyrillic_fence_comments(f'```{lang}\n{code}\n```\n', translate)
    assert code_blocks_from_text(out)[0].content.rstrip('\n') == expected
    assert fence_content_matches_source(code, expected, fence_info=lang)


def test_comment_translation_cannot_inject_code():
    with pytest.raises(ValueError):
        translate_cyrillic_fence_comments('```cpp\n/* Привет */\n```', lambda _: 'Hello */ evil(); /*')


def test_cyrillic_string_warns_at_real_file_line():
    warnings = check_cyrillic_in_en_all_fences('# Title\n\n```python\nprint("Привет")\n```\n', target_lang='en')
    assert len(warnings) == 1
    assert 'line 4:' in warnings[0]
    assert _classify_heuristic(warnings[0]) == 'warnings'


def test_navigation_yaml_is_not_a_markdown_code_block():
    from ydbdoc_review.validation.final_language import check_final_en_language
    assert check_final_en_language("    name: Русское\n", markdown=False)


def test_multiple_inline_comments_and_literal_are_separate():
    seen = []
    def translate(body):
        seen.append(body)
        return {"Первый": "First", "Второй": "Second"}[body]
    code = '/* Первый */ const char *s = "Русский"; /* Второй */'
    out = translate_cyrillic_fence_comments('```cpp\n' + code + '\n```', translate)
    assert seen == ["Первый", "Второй"]
    assert code_blocks_from_text(out)[0].content.rstrip('\n') == (
        '/* First */ const char *s = "Русский"; /* Second */')


def test_source_normalization_does_not_change_string_literals():
    from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
    text = '```python\nprint("--config-dir/opt") # Привет\n```\n'
    assert normalize_ru_source_for_translation(text) == text
