"""ACL and daily ₽ quota gates (§6.134 / Phase K)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str = ""
    status: str = "ok"  # ok | denied_acl | denied_quota | expired_context


def parse_allowed_actors(raw: str | None) -> frozenset[str]:
    """Parse comma-separated GitHub logins. Empty → empty set (ACL off)."""
    if not raw or not str(raw).strip():
        return frozenset()
    return frozenset(part.strip() for part in str(raw).split(",") if part.strip())


def check_acl(actor: str, allowed: frozenset[str]) -> GateResult:
    """If ``allowed`` is empty, allow everyone (local/tests). Else require membership."""
    if not allowed:
        return GateResult(ok=True, status="ok")
    actor_l = (actor or "").strip()
    if not actor_l:
        return GateResult(
            ok=False,
            reason="empty GITHUB_ACTOR",
            status="denied_acl",
        )
    allowed_l = {a.lower() for a in allowed}
    if actor_l.lower() in allowed_l:
        return GateResult(ok=True, status="ok")
    return GateResult(
        ok=False,
        reason=f"actor {actor_l!r} not in allowlist",
        status="denied_acl",
    )


def check_daily_quota(*, spent_rub: float, budget_rub: float) -> GateResult:
    if budget_rub < 0:
        return GateResult(ok=True, status="ok")
    if spent_rub >= budget_rub:
        return GateResult(
            ok=False,
            reason=f"daily spend {spent_rub:.2f}₽ >= budget {budget_rub:.2f}₽",
            status="denied_quota",
        )
    return GateResult(ok=True, status="ok")


def acl_deny_comment(actor: str) -> str:
    who = actor.strip() or "(unknown)"
    return (
        f"⛔ **ydbdoc-review:** запуск отклонен — пользователь `{who}` "
        "не в allowlist (`YDBDOC_ALLOWED_ACTORS`).\n\n"
        "Если вам нужен доступ, попросите владельца добавить логин в variable репозитория."
    )


def quota_deny_comment(*, spent_rub: float, budget_rub: float) -> str:
    return (
        "⛔ **ydbdoc-review:** дневная квота исчерпана "
        f"(~₽{spent_rub:.2f} из ₽{budget_rub:.2f} за сегодня, MSK).\n\n"
        "Повторите завтра или попросите поднять `YDBDOC_DAILY_BUDGET_RUB`."
    )


def retention_notice(
    *,
    completeness_only: bool = False,
    soft_keep_manual_repair: bool = False,
) -> str:
    """Footer under QA comments.

    When the only blocker is completeness (path missing from PR diff),
    ``doc_continue`` cannot invent a commit — prefer re-translate.
    """
    base = (
        "_Контекст LLM (промпты/ответы) хранится **14 дней**, затем удаляется — "
        "после этого continue недоступен._\n\n"
    )
    if completeness_only:
        return (
            base
            + "**Если блокер — путь отсутствует в diff PR:** `doc_continue` "
            "обычно не поможет. Закройте/удалите ветку перевода и снова "
            "повесьте **`doc_translate`** на исходный PR после фикса пайплайна, "
            "либо вручную добавьте EN в ветку `ydbdoc-review/pr-*` и "
            "**`doc_verify`**."
        )
    if soft_keep_manual_repair:
        return (
            base
            + "**Retained EN требует ручной правки:** обновите указанный EN-файл "
            "прямо в translation branch, затем запустите **`doc_verify`**."
        )
    return (
        base
        + "**Доработать перевод** (не более **3** раз на PR): в **translation PR** "
        "оставьте комментарий вида\n"
        "```\n"
        "/ydbdoc continue <что исправить>\n"
        "```\n"
        "и повесьте лейбл **`doc_continue`**."
    )


def expired_context_comment(source_pr: int) -> str:
    return (
        "⛔ **ydbdoc-review:** контекст предыдущего прогона (промпты/ответы модели) "
        "уже удалён (хранится **14 дней**). Continue недоступен.\n\n"
        "Что можно сделать:\n"
        f"1. Удалить ветку перевода `ydbdoc-review/pr-{source_pr}` "
        "(и закрыть translation PR) и заново повесить лейбл **`doc_translate`** "
        "на исходный PR — полный цикл.\n"
        "2. Или править EN вручную и повесить **`doc_verify`** на translation PR — "
        "без истории LLM."
    )


def store_unavailable_comment(source_pr: int, *, detail: str = "") -> str:
    """Continue denied because YDB/S3 transcript backend is not usable (not TTL)."""
    hint = (detail or "").strip()
    extra = f"\n\nДетали: `{hint}`" if hint else ""
    return (
        "⛔ **ydbdoc-review:** хранилище контекста LLM недоступно "
        "(нет `YDB_SA_KEY` / бэкенд не поднялся). Continue недоступен — "
        "это **не** истечение 14-дневного TTL."
        f"{extra}\n\n"
        "Что можно сделать:\n"
        "1. Проверить, что в workflow в контейнер попадают "
        "`YDB_SA_KEY` и `YDBDOC_TRANSCRIPT_BACKEND=ydb`, затем "
        f"заново повесить **`doc_translate`** на исходный PR `#{source_pr}`.\n"
        "2. Или править EN вручную и повесить **`doc_verify`** на translation PR."
    )
