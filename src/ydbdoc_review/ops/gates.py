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


def retention_notice() -> str:
    return (
        "_Контекст LLM (промпты/ответы) хранится **14 дней**, затем удаляется. "
        "После этого `doc_continue` недоступен._"
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
