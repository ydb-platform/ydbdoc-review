from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence


class Blocked(RuntimeError):
    """A run rejected before a safe overlay exists."""


class Model(Protocol):
    name: str

    def invoke(self, role: str, request: Mapping[str, object]) -> Mapping[str, object]: ...


class Op(str, Enum):
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    status: str
    previous_path: str | None = None


@dataclass(frozen=True)
class OverlayEntry:
    path: str
    op: Op
    content: bytes | None
    source_path: str

    @property
    def sha256(self) -> str | None:
        return hashlib.sha256(self.content).hexdigest() if self.content is not None else None


@dataclass(frozen=True)
class RunResult:
    verdict: str
    overlay: tuple[OverlayEntry, ...]
    report: str
    translator: str
    critics: tuple[str, ...]


@dataclass(frozen=True)
class Gates:
    actor: str
    allowed_actors: frozenset[str]
    merged: bool
    spent_rub: float
    budget_rub: float

    def check(self) -> None:
        if self.actor not in self.allowed_actors:
            raise Blocked(
                f"Перевод не выполнялся: у пользователя {self.actor} нет прав на doc_translate."
            )
        if not self.merged:
            raise Blocked("Перевод не выполнялся: doc_translate разрешён только для смерженного PR.")
        if self.spent_rub >= self.budget_rub:
            raise Blocked("Перевод не выполнялся: дневной бюджет YDBDOC_DAILY_BUDGET_RUB исчерпан.")


SnapshotRead = Callable[[str], bytes | None]


class TranslationPipeline:
    """Compose an immutable overlay. This class never publishes repository data."""

    def __init__(self, translator: Model, critic: Model):
        self.translator = translator
        self.critic = critic

    def run(
        self,
        *,
        pr_number: int,
        gates: Gates,
        manifest: Sequence[ManifestEntry],
        read_current_main: SnapshotRead,
    ) -> RunResult:
        gates.check()
        if pr_number <= 0:
            raise Blocked("Некорректный номер исходного PR.")
        if not manifest:
            raise Blocked("GitHub не вернул файлы исходного PR.")

        expanded = self._expand(manifest)
        overlay: dict[str, OverlayEntry] = {}
        direct_redirect_seen = False
        handled = 0
        critic_names: list[str] = []

        for path, status in expanded:
            self._safe_path(path)
            if path == "ydb/docs/redirects.yaml":
                direct_redirect_seen = True
                handled += 1
                continue
            if not path.startswith("ydb/docs/ru/"):
                continue
            target = path.replace("ydb/docs/ru/", "ydb/docs/en/", 1)
            handled += 1

            if status == "removed":
                if read_current_main(target) is not None:
                    overlay[target] = OverlayEntry(target, Op.DELETE, None, path)
                continue

            source = read_current_main(path)
            if source is None:
                continue  # SUPERSEDED: do not restore historical content.
            current_target = read_current_main(target)
            candidate = self._translate(path, target, source, current_target)
            candidate, names = self._verify_and_repair(path, target, source, current_target, candidate)
            critic_names.extend(names)
            if candidate != current_target:
                overlay[target] = OverlayEntry(target, Op.WRITE, candidate, path)

        if handled != len(expanded):
            raise Blocked("В манифесте есть неподдерживаемые операции; безопасный перевод не опубликован.")
        self._validate_toc_deletes(overlay, read_current_main)
        self._validate_redirect(overlay, read_current_main, direct_redirect_seen)

        ordered = tuple(overlay[p] for p in sorted(overlay))
        report = self._report(pr_number, expanded, ordered, direct_redirect_seen)
        return RunResult("PASS", ordered, report, self.translator.name, tuple(critic_names))

    @staticmethod
    def _expand(manifest: Sequence[ManifestEntry]) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for item in manifest:
            if item.status == "renamed":
                if not item.previous_path:
                    raise Blocked("GitHub вернул rename без previous_filename.")
                result.append((item.previous_path, "removed"))
                result.append((item.path, "added"))
            elif item.status in {"added", "modified", "removed"}:
                result.append((item.path, item.status))
            else:
                raise Blocked(f"Неизвестный статус файла GitHub: {item.status}.")
        return tuple(result)

    @staticmethod
    def _safe_path(path: str) -> None:
        parsed = PurePosixPath(path)
        if path.startswith("/") or ".." in parsed.parts or "//" in path or "\x00" in path:
            raise Blocked(f"Небезопасный путь в манифесте: {path}")

    def _translate(self, source_path: str, target_path: str, source: bytes, target: bytes | None) -> bytes:
        request = {
            "source_path": source_path,
            "target_path": target_path,
            "source_locale": "ru",
            "target_locale": "en",
            "source_utf8": source.decode("utf-8"),
            "current_target_utf8": target.decode("utf-8") if target is not None else None,
            "instruction": (
                "Translate the COMPLETE source file into English. Preserve Markdown/YAML structure, "
                "code, links and templates exactly. For YAML translate title, description and comments only."
            ),
        }
        response = self._invoke_model(self.translator, "translator", request, target_path)
        text = response.get("candidate_utf8")
        if not isinstance(text, str):
            raise Blocked(f"Модель перевода не вернула полный текст для {target_path}.")
        candidate = text.encode("utf-8")
        self._validate_candidate(source_path, target_path, source, candidate)
        return candidate

    def _verify_and_repair(
        self, source_path: str, target_path: str, source: bytes, current: bytes | None, candidate: bytes
    ) -> tuple[bytes, list[str]]:
        names: list[str] = []
        for attempt in range(3):
            response = self._invoke_model(
                self.critic,
                "critic" if attempt == 0 else "repair",
                {
                    "source_path": source_path,
                    "target_path": target_path,
                    "source_utf8": source.decode("utf-8"),
                    "current_target_utf8": current.decode("utf-8") if current is not None else None,
                    "candidate_utf8": candidate.decode("utf-8"),
                    "instruction": "Check completeness, meaning, links, code blocks and remaining Cyrillic.",
                    "repair_attempt": attempt,
                },
                target_path,
            )
            names.append(self.critic.name)
            verdict = response.get("verdict")
            if verdict == "PASS":
                self._validate_candidate(source_path, target_path, source, candidate)
                prose = re.sub(
                    r"(?ms)^[ \t]*(```+|~~~+)[^\n]*\n.*?^[ \t]*\1\s*$", "", candidate.decode("utf-8")
                )
                if re.search(r"[А-Яа-яЁё]", prose):
                    raise Blocked(f"В {target_path} после проверки осталась кириллица; файл не опубликован.")
                return candidate, names
            repaired = response.get("candidate_utf8")
            if attempt < 2 and isinstance(repaired, str):
                candidate = repaired.encode("utf-8")
                self._validate_candidate(source_path, target_path, source, candidate)
                continue
            issues = response.get("issues")
            raise Blocked(
                f"Критик нашёл неисправленные проблемы в {target_path}: "
                f"{json.dumps(issues, ensure_ascii=False)}. Файл и связанный набор не опубликованы."
            )
        raise AssertionError("unreachable")

    @staticmethod
    def _invoke_model(
        model: Model, role: str, request: Mapping[str, object], target_path: str
    ) -> Mapping[str, object]:
        try:
            response = model.invoke(role, request)
        except Exception:
            # Provider exceptions may contain request headers, tokens or raw payloads.
            # They are deliberately not interpolated into user-visible reports.
            raise Blocked(
                f"Модель {model.name} не смогла обработать {target_path}. "
                "Секретные технические сведения скрыты. Попробуйте позже, снова запустив doc_translate."
            ) from None
        if not isinstance(response, Mapping):
            raise Blocked(
                f"Модель {model.name} вернула ответ неизвестного формата для {target_path}."
            )
        return response

    @classmethod
    def _validate_candidate(
        cls, source_path: str, target_path: str, source: bytes, candidate: bytes
    ) -> None:
        try:
            source_text = source.decode("utf-8")
            candidate_text = candidate.decode("utf-8")
        except UnicodeDecodeError:
            raise Blocked(f"Файл {target_path} после перевода не является корректным UTF-8.") from None
        if not candidate_text.strip():
            raise Blocked(f"Модель вернула пустой файл {target_path}; файл не опубликован.")
        source_lines = source_text.splitlines()
        candidate_lines = candidate_text.splitlines()
        if len(candidate_lines) == 1 and len(source_lines) > 3:
            raise Blocked(f"Модель заменила {target_path} одной строкой; файл не опубликован.")
        if len(candidate_lines) < max(2, len(source_lines) * 2 // 3):
            raise Blocked(f"Перевод {target_path} выглядит обрезанным; файл не опубликован.")
        if len(candidate) < len(source) // 3:
            raise Blocked(f"Перевод {target_path} потерял слишком много содержимого; файл не опубликован.")
        if source_path.endswith("toc_p.yaml"):
            cls._validate_toc(source_text, candidate_text, target_path)
        elif source_path.endswith(".md"):
            cls._validate_markdown(source_text, candidate_text, target_path)

    @staticmethod
    def _validate_markdown(source: str, candidate: str, target_path: str) -> None:
        headings = lambda text: [len(m.group(1)) for m in re.finditer(r"(?m)^(#{1,6})\s+", text)]
        if headings(source) != headings(candidate):
            raise Blocked(f"В {target_path} потеряны или добавлены заголовки; файл не опубликован.")
        fence = re.compile(r"(?ms)^[ \t]*(```+|~~~+)[^\n]*\n.*?^[ \t]*\1\s*$")
        source_blocks = [m.group(0).splitlines()[1:-1] for m in fence.finditer(source)]
        candidate_blocks = [m.group(0).splitlines()[1:-1] for m in fence.finditer(candidate)]
        if source_blocks != candidate_blocks:
            raise Blocked(f"В {target_path} потерян или изменён блок кода; файл не опубликован.")
        links = lambda text: sorted(re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text))
        if links(source) != links(candidate):
            raise Blocked(f"В {target_path} потеряна или изменена ссылка; файл не опубликован.")
        for marker in ("{%", "%}", "{{", "}}"):
            if source.count(marker) != candidate.count(marker):
                raise Blocked(f"В {target_path} потеряна служебная конструкция `{marker}`; файл не опубликован.")

    @staticmethod
    def _validate_toc(source: str, candidate: str, target_path: str) -> None:
        source_lines = source.splitlines(keepends=True)
        candidate_lines = candidate.splitlines(keepends=True)
        if len(source_lines) != len(candidate_lines):
            raise Blocked(f"В {target_path} изменена структура TOC; файл не опубликован.")
        forbidden = {"english", "translated", "translated title", "translation"}
        for original, translated in zip(source_lines, candidate_lines, strict=True):
            source_name = TranslationPipeline._parse_toc_name(original)
            candidate_name = TranslationPipeline._parse_toc_name(translated)
            if source_name is not None:
                if (
                    candidate_name is None
                    or source_name[:2] != candidate_name[:2]
                    or source_name[4] != candidate_name[4]
                ):
                    raise Blocked(f"В {target_path} изменены стиль или служебные байты строки name; файл не опубликован.")
                TranslationPipeline._validate_inline_toc_comment(
                    source_name[3], candidate_name[3], target_path
                )
                value = candidate_name[2]
                if not value or value.casefold() in forbidden or re.search(r"[А-Яа-яЁё]", value):
                    raise Blocked(f"В {target_path} поле name не переведено содержательно; файл не опубликован.")
                TranslationPipeline._validate_yaml_scalar(value, candidate_name[1], target_path)
            elif TranslationPipeline._is_toc_comment(original):
                source_comment = TranslationPipeline._parse_toc_comment(original)
                candidate_comment = TranslationPipeline._parse_toc_comment(translated)
                if candidate_comment is None or source_comment[0] != candidate_comment[0] or source_comment[2] != candidate_comment[2]:
                    raise Blocked(f"В {target_path} изменены отступ, `#` или окончание строки комментария; файл не опубликован.")
                if re.search(r"[А-Яа-яЁё]", candidate_comment[1]):
                    raise Blocked(f"В комментарии {target_path} осталась кириллица; файл не опубликован.")
            elif original != translated:
                raise Blocked(
                    f"В {target_path} изменены href, комментарии или служебные строки TOC; файл не опубликован."
                )

    @staticmethod
    def _parse_toc_name(line: str) -> tuple[str, str, str, str, str] | None:
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(newline)] if newline else line
        match = re.match(r"^(\s*-?\s*name:\s*)(.*)$", body)
        if not match:
            return None
        prefix, tail = match.groups()
        decoration = re.match(r"^((?:(?:!\S+|&\S+)\s+)*)", tail).group(1)
        prefix += decoration
        tail = tail[len(decoration):]
        style = "plain"
        scalar = tail
        suffix = ""
        if tail.startswith(("'", '"')):
            quote = tail[0]
            style = quote
            index = 1
            while index < len(tail):
                if quote == "'" and tail[index:index + 2] == "''":
                    index += 2
                    continue
                if quote == '"' and tail[index] == "\\":
                    index += 2
                    continue
                if tail[index] == quote:
                    break
                index += 1
            if index >= len(tail) or not re.fullmatch(r"\s*(?:#.*)?", tail[index + 1:]):
                return None
            scalar = tail[1:index]
            suffix = tail[index + 1:]
        else:
            comment = re.search(r"\s+#", tail)
            if comment:
                scalar = tail[:comment.start()].rstrip()
                suffix = tail[len(scalar):]
            else:
                scalar = tail.rstrip()
                suffix = tail[len(scalar):]
        # prefix and suffix bind indentation, tags/anchors, comment delimiter,
        # spacing and line ending. Only scalar content may differ.
        return prefix, style, scalar, suffix, newline

    @staticmethod
    def _validate_yaml_scalar(value: str, style: str, target_path: str) -> None:
        if style == "plain":
            lowered = value.casefold()
            yaml_words = {"null", "~", "true", "false", "yes", "no", "on", "off"}
            if (
                lowered in yaml_words
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .,'()/\-]*", value)
                or ":" in value
                or "#" in value
            ):
                raise Blocked(
                    f"Для {target_path} перевод name нельзя безопасно оставить без кавычек; файл не опубликован."
                )
        elif style == "'":
            if re.search(r"(?<!')'(?!')", value):
                raise Blocked(f"В {target_path} неверно экранирована одинарная кавычка YAML; файл не опубликован.")
        elif style == '"':
            if re.search(r"\\(?:[^0abtnvfre \"/\\N_LPuxU]|[xuU](?![0-9A-Fa-f]))", value):
                raise Blocked(f"В {target_path} неверно экранирована двойная кавычка YAML; файл не опубликован.")

    @staticmethod
    def _is_toc_comment(line: str) -> bool:
        return re.match(r"^\s*#", line) is not None

    @staticmethod
    def _parse_toc_comment(line: str) -> tuple[str, str, str] | None:
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(newline)] if newline else line
        match = re.match(r"^(\s*#\s?)(.*)$", body)
        return (match.group(1), match.group(2), newline) if match else None

    @staticmethod
    def _validate_inline_toc_comment(source: str, candidate: str, target_path: str) -> None:
        pattern = re.compile(r"^(\s*#\s?)(.*)$")
        source_match = pattern.match(source)
        candidate_match = pattern.match(candidate)
        if source_match is None:
            if source != candidate:
                raise Blocked(f"В {target_path} изменены пробелы после name; файл не опубликован.")
            return
        if candidate_match is None or source_match.group(1) != candidate_match.group(1):
            raise Blocked(f"В {target_path} изменены пробелы или `#` inline-комментария; файл не опубликован.")
        if re.search(r"[А-Яа-яЁё]", candidate_match.group(2)):
            raise Blocked(f"В inline-комментарии {target_path} осталась кириллица; файл не опубликован.")

    @staticmethod
    def _validate_toc_deletes(overlay: Mapping[str, OverlayEntry], read: SnapshotRead) -> None:
        deleted = [entry.path for entry in overlay.values() if entry.op is Op.DELETE]
        for path in deleted:
            name = PurePosixPath(path).name
            toc = str(PurePosixPath(path).parent / "toc_p.yaml")
            effective = overlay.get(toc)
            content = effective.content if effective else read(toc)
            if content and name.encode() in content:
                raise Blocked(
                    f"Удаляется {path}, но ссылка на {name} осталась в {toc}. "
                    "Исправьте через /ydbdoc continue."
                )

    @staticmethod
    def _validate_redirect(
        overlay: Mapping[str, OverlayEntry], read: SnapshotRead, direct_redirect_seen: bool
    ) -> None:
        if not any(entry.op is Op.DELETE for entry in overlay.values()):
            return
        redirects = overlay.get("ydb/docs/redirects.yaml")
        content = redirects.content if redirects else read("ydb/docs/redirects.yaml")
        if not direct_redirect_seen or not content:
            raise Blocked("Удаляется статья, но в исходном PR нет проверяемого redirects.yaml.")
        for entry in overlay.values():
            if entry.op is not Op.DELETE:
                continue
            old = entry.path.removeprefix("ydb/docs/en/core").removesuffix(".md") + ".md"
            if f"from: {old}".encode() not in content:
                raise Blocked(f"Для удаляемой статьи {entry.path} не найден редирект. Укажите его через /ydbdoc continue.")

    @staticmethod
    def _report(
        pr: int, expanded: Sequence[tuple[str, str]], overlay: Sequence[OverlayEntry], redirect_seen: bool
    ) -> str:
        lines = [
            "🟢 Перевод подготовлен безопасно.",
            f"Исходный PR: #{pr} (merged).",
            f"Проверено операций исходного PR: {len(expanded)}.",
            f"Изменений в Draft: {len(overlay)}.",
        ]
        lines.extend(
            f"- {'удалить' if item.op is Op.DELETE else 'записать'} `{item.path}`"
            for item in overlay
        )
        if redirect_seen:
            lines.append("- Редирект из удалённого пути найден в `ydb/docs/redirects.yaml`, новый дубль не добавлен.")
        lines.append("Перевод проверен другой моделью; публикация разрешена только для всего этого набора целиком.")
        return "\n".join(lines)
