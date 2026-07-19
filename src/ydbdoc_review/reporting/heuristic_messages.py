"""Human-readable Russian labels for deterministic heuristic warnings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from ydbdoc_review.validation.wikipedia_links import (
    format_wikipedia_href,
    parse_wikipedia_href,
    resolve_wikipedia_href,
)

_FENCE_BODY_COPY = re.compile(
    r"^fence_body_copy: block (\d+) body changed by pipeline "
    r"\(first line: «(.+)»\)$"
)
_FENCE_BODY_COUNT = re.compile(
    r"^fence_body_copy: block count source (\d+) vs target (\d+)$"
)
_CYRILLIC_IN_FENCE = re.compile(
    r"^cyrillic_in_fence: block (\d+) line (\d+): «(.+)»$"
)
_CYRILLIC_IN_FENCE_MORE = re.compile(
    r"^cyrillic_in_fence: … и ещё (\d+) строк с кириллицей в комментариях$"
)
_FENCE_PARITY = re.compile(r"^fence_parity: source (\d+) fenced blocks vs target (\d+)$")
_HEADING_PARITY = re.compile(r"^heading_parity: source (\d+) headings vs target (\d+)$")
_LIST_TAB_PARITY = re.compile(r"^list_tab_parity: source (\d+) tab blocks vs target (\d+)$")
_LENGTH_RATIO = re.compile(
    r"^length_ratio: (.+?) ratio ([\d.]+) \((.+)\)$"
)
_FENCE_PATH = re.compile(
    r"^fence_path_stripped: block (\d+) line (\d+): (.+)$"
)
_LINK_LOCALE = re.compile(r"^link_locale: (.+)$")
_LINK_LOCALE_WIKI_RU_SLUG = re.compile(
    r"^link_locale: en\.wikipedia\.org uses Russian article slug "
    r"\(use English title\): (https?://\S+)$"
)
_LINK_LOCALE_RU_HOST = re.compile(
    r"^link_locale: RU-locale URL in EN document: (https?://\S+)$"
)
_LINK_LOCALE_CYRILLIC_PATH = re.compile(
    r"^link_locale: Cyrillic path on EN-locale URL: (https?://\S+)$"
)
_MD_LINK_PARITY = re.compile(r"^md_link_parity: EN missing RU links: (.+)$")
_NAV_KIND = re.compile(
    r"^(scope_not_applied|missing_href|unexpected_href|empty_toc|collapsed_toc|"
    r"inconsistent_indent|missing_toc_target|orphan_toc_page|toc_structure_parity|"
    r"toc_en_only_legacy): (.+)$"
)


@dataclass(frozen=True)
class HeuristicReviewerDetail:
    problem: str
    suggestion: str | None = None


def heuristic_location_label(message: str) -> str:
    """Short location column for a heuristic line in the PR report."""
    if message.startswith("cyrillic_in_fence:"):
        return "комментарии в коде"
    if message.startswith("fence_body_copy:") or message.startswith("fence_path_stripped:"):
        return "блок кода"
    if message.startswith("fence_parity:"):
        return "блоки кода"
    if message.startswith("link_locale:") or message.startswith("md_link_parity:"):
        return "ссылки"
    if message.startswith("Кириллица в EN-тексте") or message.startswith("… и ещё"):
        return "текст"
    if message.startswith("heading_parity:"):
        return "заголовки"
    if message.startswith("list_tab_parity:"):
        return "вкладки YFM"
    if message.startswith("length_ratio:"):
        return "объём перевода"
    if message.startswith(
        (
            "scope_not_applied:",
            "missing_href:",
            "unexpected_href:",
            "empty_toc:",
            "collapsed_toc:",
            "missing_toc_target:",
            "orphan_toc_page:",
            "toc_structure_parity:",
            "toc_en_only_legacy:",
        )
    ):
        return "навигация (toc/redirect)"
    if message.startswith("ru_source"):
        return "исходник RU"
    return "автопроверка"


def _wikipedia_manual_suggestion(href: str, *, line_hint: str = "") -> str:
    parsed = parse_wikipedia_href(href)
    if parsed is None:
        base = "Подберите EN-статью на en.wikipedia.org и замените URL в переводе."
    else:
        _wiki_lang, title, _fragment = parsed
        ru_href = format_wikipedia_href("ru", title)
        resolved = resolve_wikipedia_href(ru_href, target_lang="en")
        if resolved and unquote(resolved) != unquote(href):
            base = f"Замените ссылку на: {resolved}"
        else:
            base = (
                f"Автоперевод Wikipedia не нашёл EN-статью для «{title}» "
                f"(langlink на ru.wikipedia.org отсутствует). "
                f"Найдите подходящую статью на en.wikipedia.org вручную "
                f"(например, по теме «{title}») и пропишите "
                f"https://en.wikipedia.org/wiki/English_title"
            )
    if line_hint:
        return f"{base} {line_hint}"
    return base


def format_heuristic_reviewer_detail(message: str) -> HeuristicReviewerDetail:
    """Turn internal heuristic codes into reviewer-facing problem + optional advice."""
    if message.startswith("Кириллица в EN-тексте") or message.startswith("… и ещё"):
        return HeuristicReviewerDetail(problem=message)

    m = _LINK_LOCALE_WIKI_RU_SLUG.match(message.strip())
    if m:
        href = m.group(1)
        parsed = parse_wikipedia_href(href)
        title = parsed[1] if parsed else unquote(href.rsplit("/", 1)[-1])
        return HeuristicReviewerDetail(
            problem=(
                f"Ссылка на Wikipedia не переведена на EN: в URL остался "
                f"русский slug «{title}» на en.wikipedia.org."
            ),
            suggestion=_wikipedia_manual_suggestion(href),
        )

    m = _LINK_LOCALE_RU_HOST.match(message.strip())
    if m:
        href = m.group(1)
        return HeuristicReviewerDetail(
            problem=f"В EN-документе остался URL русской локали: {href}",
            suggestion=(
                "Замените домен/путь на EN-версию (например en.wikipedia.org "
                "или /ydb/docs/en/…) в файле перевода."
            ),
        )

    m = _LINK_LOCALE_CYRILLIC_PATH.match(message.strip())
    if m:
        href = m.group(1)
        return HeuristicReviewerDetail(
            problem=f"URL EN-локали содержит кириллицу в пути: {href}",
            suggestion=(
                "Исправьте slug/путь на латиницу или замените ссылку "
                "на корректный EN-URL."
            ),
        )

    return HeuristicReviewerDetail(problem=_humanize_heuristic_problem(message))


def humanize_heuristic(message: str) -> str:
    """Turn internal heuristic codes into reviewer-facing Russian text."""
    return format_heuristic_reviewer_detail(message).problem


def _humanize_heuristic_problem(message: str) -> str:
    m = _FENCE_BODY_COPY.match(message)
    if m:
        block, preview = m.group(1), m.group(2)
        return (
            f"Блок кода №{block} отличается от русского оригинала "
            f"(первая строка: «{preview}»). Проверьте, что команды и синтаксис "
            "не искажены — допустимо менять только переводимые комментарии `//` и `#`."
        )

    m = _FENCE_BODY_COUNT.match(message)
    if m:
        src, tgt = m.group(1), m.group(2)
        return (
            f"Число блоков кода в EN ({tgt}) не совпадает с RU ({src}). "
            "Возможно, пропущен или лишний фрагмент ```…```."
        )

    m = _CYRILLIC_IN_FENCE.match(message)
    if m:
        block, line, snippet = m.group(1), m.group(2), m.group(3)
        return (
            f"В комментарии внутри блока кода №{block} (строка ~{line}) "
            f"осталась кириллица: «{snippet}». Переведите комментарий на английский."
        )

    m = _CYRILLIC_IN_FENCE_MORE.match(message)
    if m:
        return (
            f"… и ещё {m.group(1)} строк с кириллицей в комментариях кода "
            "(см. выше)."
        )

    m = _FENCE_PARITY.match(message)
    if m:
        return (
            f"В EN {m.group(2)} блоков кода ```, в RU — {m.group(1)}. "
            "Структура примеров должна совпадать."
        )

    m = _HEADING_PARITY.match(message)
    if m:
        return (
            f"Число заголовков в EN ({m.group(2)}) не совпадает с RU ({m.group(1)})."
        )

    m = _LIST_TAB_PARITY.match(message)
    if m:
        return (
            f"Число блоков вкладок {{% list tabs %}} в EN ({m.group(2)}) "
            f"не совпадает с RU ({m.group(1)})."
        )

    m = _LENGTH_RATIO.match(message)
    if m:
        direction, ratio, note = m.group(1), m.group(2), m.group(3)
        if "borderline" in note:
            return (
                f"Объём EN-текста подозрительно отличается от RU ({direction}, "
                f"коэффициент {ratio}). Возможны пропуски или лишние вставки."
            )
        return (
            f"Объём EN-текста сильно отличается от RU ({direction}, коэффициент {ratio})."
        )

    m = _FENCE_PATH.match(message)
    if m:
        return (
            f"В блоке кода №{m.group(1)} (строка {m.group(2)}) потерян абсолютный "
            f"путь `/opt/ydb/…`: {m.group(3)}"
        )

    m = _LINK_LOCALE.match(message)
    if m:
        return f"Ссылка не подходит для EN-локали: {m.group(1)}"

    m = _MD_LINK_PARITY.match(message)
    if m:
        return (
            f"В EN нет ссылок на страницы, которые есть в RU: {m.group(1)}. "
            "Добавьте те же ``.md``-ссылки или обновите путь, если RU переехал."
        )

    m = _NAV_KIND.match(message)
    if m:
        kind, detail = m.group(1), m.group(2)
        if kind == "scope_not_applied":
            return f"Пункт меню из scope перевода не попал в EN toc: {detail}"
        if kind == "missing_href":
            return f"В EN toc нет href из RU PR: {detail}"
        if kind == "unexpected_href":
            return (
                f"В EN toc лишний href (нет в diff RU PR и нет в EN main): {detail}"
            )
        if kind == "empty_toc":
            return f"EN toc пустой: {detail}"
        if kind == "collapsed_toc":
            return f"EN toc сильно урезан относительно EN main: {detail}"
        if kind == "inconsistent_indent":
            return f"Смешанные отступы в inline toc: {detail}"
        if kind == "missing_toc_target":
            return f"В EN toc ссылка на отсутствующий файл: {detail}"
        if kind == "orphan_toc_page":
            return (
                f"Переведённая EN-страница не связана ни с одним EN toc: {detail}"
            )
        if kind == "toc_structure_parity":
            return (
                f"Структура RU/EN toc не совпадает (href/include): {detail}"
            )
        if kind == "toc_en_only_legacy":
            return (
                f"В EN toc есть пункты без RU-зеркала (legacy): {detail}"
            )

    if message.startswith("ru_source"):
        return message.replace("ru_source", "Исходник RU", 1)

    return message
