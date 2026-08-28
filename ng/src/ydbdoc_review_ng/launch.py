from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol

from .pipeline import Blocked, Gates, ManifestEntry, Model, Op, RunResult, TranslationPipeline
from .state import (
    ClaimStatus, CommandReceipt, DryState, EffectCheckpoint, LeaseOwner, ModelCallIdentity, ModelCallReservation,
    RecordedModelResult, RepoIdentity, StatePort, UnknownModelOutcome, YdbConfig,
    YdbState, new_claim_nonce,
)

CORE_SHA = "8c962d8ff5042286428038d6fe2d5c485c527dee"
REPORT_MARKER = "<!-- ydbdoc-review-ng:canonical-report -->"


class GitHubPort(Protocol):
    def remove_label(self, pr: int, label: str) -> None: ...
    def pull(self, pr: int) -> Mapping[str, object]: ...
    def files(self, pr: int) -> tuple[ManifestEntry, ...]: ...
    def main_sha(self) -> str: ...
    def content(self, path: str, sha: str) -> bytes | None: ...
    def active_drafts(self, branch: str) -> list[Mapping[str, object]]: ...
    def close_pr(self, pr: int, comment: str) -> None: ...
    def delete_branch(self, branch: str) -> None: ...
    def publish(self, branch: str, base_sha: str, result: RunResult, title: str) -> int: ...
    def report(self, pr: int, body: str) -> None: ...


class GitHubApi:
    def __init__(self, repo: str, token: str, dry_run: bool = False):
        self.repo = repo
        self.token = token
        self.dry_run = dry_run

    def _request(self, method: str, path: str, body: object | None = None) -> tuple[object, Mapping[str, str]]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}", data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ydbdoc-review-ng/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return (json.loads(raw) if raw else {}), dict(response.headers)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {}, {}
            raise Blocked("GitHub API недоступен. Перевод не выполнялся; попробуйте doc_translate позже.") from None
        except Exception:
            raise Blocked("GitHub API недоступен. Перевод не выполнялся; попробуйте doc_translate позже.") from None

    def remove_label(self, pr: int, label: str) -> None:
        if not self.dry_run:
            self._request("DELETE", f"/issues/{pr}/labels/{urllib.parse.quote(label, safe='')}")

    def pull(self, pr: int) -> Mapping[str, object]:
        value, _ = self._request("GET", f"/pulls/{pr}")
        if not isinstance(value, dict):
            raise Blocked("GitHub вернул некорректные данные исходного PR.")
        return value

    def files(self, pr: int) -> tuple[ManifestEntry, ...]:
        rows: list[ManifestEntry] = []
        page = 1
        while True:
            value, _ = self._request("GET", f"/pulls/{pr}/files?per_page=100&page={page}")
            if not isinstance(value, list):
                raise Blocked("GitHub вернул некорректный список файлов исходного PR.")
            rows.extend(ManifestEntry(x["filename"], x["status"], x.get("previous_filename")) for x in value)
            if len(value) < 100:
                break
            page += 1
            if page > 100:
                raise Blocked("Список файлов исходного PR превышает безопасный лимит.")
        return tuple(rows)

    def main_sha(self) -> str:
        value, _ = self._request("GET", "/git/ref/heads/main")
        try:
            return str(value["object"]["sha"])
        except (KeyError, TypeError):
            raise Blocked("Не удалось зафиксировать точный SHA ветки main.") from None

    def content(self, path: str, sha: str) -> bytes | None:
        value, _ = self._request("GET", f"/contents/{urllib.parse.quote(path)}?ref={sha}")
        if not value:
            return None
        try:
            if value["type"] != "file":
                raise ValueError
            return base64.b64decode(value["content"])
        except Exception:
            raise Blocked(f"GitHub не вернул точное содержимое {path} на SHA main.") from None

    def active_drafts(self, branch: str) -> list[Mapping[str, object]]:
        owner = self.repo.split("/", 1)[0]
        query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "per_page": 100})
        value, _ = self._request("GET", f"/pulls?{query}")
        return list(value) if isinstance(value, list) else []

    def close_pr(self, pr: int, comment: str) -> None:
        if self.dry_run:
            return
        self.report(pr, comment)
        self._request("PATCH", f"/pulls/{pr}", {"state": "closed"})

    def delete_branch(self, branch: str) -> None:
        if not self.dry_run:
            self._request("DELETE", f"/git/refs/heads/{urllib.parse.quote(branch, safe='')}")

    def publish(self, branch: str, base_sha: str, result: RunResult, title: str) -> int:
        if self.dry_run:
            return 0
        with tempfile.TemporaryDirectory(prefix="ydbdoc-ng-") as directory:
            remote = f"https://x-access-token:{self.token}@github.com/{self.repo}.git"
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", remote, directory], check=True, capture_output=True)
            subprocess.run(["git", "-C", directory, "checkout", "--detach", base_sha], check=True, capture_output=True)
            for entry in result.overlay:
                path = Path(directory, entry.path)
                if entry.op is Op.DELETE:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(entry.content or b"")
            subprocess.run(["git", "-C", directory, "add", "--all"], check=True, capture_output=True)
            subprocess.run(["git", "-C", directory, "-c", "user.name=ydbdoc-review", "-c", "user.email=ydbdoc-review@users.noreply.github.com", "commit", "-m", title], check=True, capture_output=True)
            subprocess.run(["git", "-C", directory, "push", remote, f"HEAD:refs/heads/{branch}", f"--force-with-lease=refs/heads/{branch}"], check=True, capture_output=True)
        drafts = self.active_drafts(branch)
        if drafts:
            return int(drafts[0]["number"])
        value, _ = self._request("POST", "/pulls", {"title": title, "head": branch, "base": "main", "draft": True, "body": "Автоматический перевод документации."})
        return int(value["number"])

    def report(self, pr: int, body: str) -> None:
        if self.dry_run:
            return
        canonical = f"{REPORT_MARKER}\n{body}"
        page = 1
        while page <= 100:
            value, _ = self._request("GET", f"/issues/{pr}/comments?per_page=100&page={page}")
            comments = value if isinstance(value, list) else []
            for comment in comments:
                if isinstance(comment, dict) and str(comment.get("body", "")).startswith(REPORT_MARKER):
                    self._request("PATCH", f"/issues/comments/{int(comment['id'])}", {"body": canonical})
                    return
            if len(comments) < 100:
                break
            page += 1
        self._request("POST", f"/issues/{pr}/comments", {"body": canonical})


class OpenAiCompatibleModel(Model):
    def __init__(self, name: str, url: str, api_key: str, state: StatePort, cost_rub: float, budget_rub: float, folder_id: str = ""):
        self.name, self.url, self.api_key, self.state, self.cost_rub, self.budget_rub, self.folder_id = name, url, api_key, state, cost_rub, budget_rub, folder_id
        self.sequence = 0
        self.run_id = "unbound"

    def invoke(self, role: str, request: Mapping[str, object]) -> Mapping[str, object]:
        self.sequence += 1
        call_id = f"{self.run_id}:{role}:{self.sequence}:{self.name}"
        if self.state.has_unknown_for_current_moscow_day():
            raise Blocked("Есть вызов модели с неизвестным итогом оплаты. Новые платные вызовы сегодня заблокированы.")
        if self.state.actual_spend_current_moscow_day() >= Decimal(str(self.budget_rub)):
            raise Blocked("Дневной бюджет перевода исчерпан до вызова модели.")
        identity = ModelCallIdentity(call_id)
        normative_role = {
            "classifier": "CLASSIFIER", "translator": "TRANSLATOR_A",
            "critic": "CRITIC_B", "repair": "REPAIR_B",
        }.get(role)
        if normative_role is None:
            raise Blocked("Получена неизвестная роль модели; вызов не выполнялся.")
        reservation = ModelCallReservation(
            identity=identity, reservation_nonce=new_claim_nonce(), lineage_id=self.run_id,
            run_receipt_identity=self.run_id, provider="openai-compatible", model=self.name,
            role=normative_role, verification_pass=0, attempt=self.sequence,
        )
        reservation_claim = self.state.reserve_model_call(reservation)
        if reservation_claim.status is not ClaimStatus.CREATED:
            raise Blocked("Этот вызов модели уже был принят ранее; повторная отправка заблокирована.")
        model_uri = f"gpt://{self.folder_id}/{self.name}" if self.folder_id else self.name
        payload = json.dumps({"model": model_uri, "messages": [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}], "temperature": 0, "response_format": {"type": "json_object"}}).encode()
        auth = f"Api-Key {self.api_key}" if self.folder_id else f"Bearer {self.api_key}"
        http = urllib.request.Request(self.url, data=payload, method="POST", headers={"Authorization": auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(http, timeout=300) as response:
                outer = json.loads(response.read())
            result = json.loads(outer["choices"][0]["message"]["content"])
            usage = outer.get("usage", {}) if isinstance(outer, dict) else {}
            self.state.record_model_result(identity, RecordedModelResult(
                provider_outcome="SUCCESS", provider_request_id=str(outer.get("id")) if outer.get("id") else None,
                input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"), actual_cost_rub=None,
            ))
            return result
        except Exception:
            self.state.mark_model_unknown(identity, UnknownModelOutcome())
            raise RuntimeError("model call failed") from None


def execute(event: Mapping[str, object], github: GitHubPort, state: StatePort, translator: Model, critic: Model, *, allowed: frozenset[str], budget: float, dry_run: bool = False) -> RunResult:
    pr_number = int(event["pull_request"]["number"])
    actor = str(event["sender"]["login"])
    delivery = str(event.get("delivery_id", event.get("action_run_id", "unknown")))
    github.remove_label(pr_number, "doc_translate")
    pull = github.pull(pr_number)
    merged = bool(pull.get("merged"))
    gates = Gates(actor, allowed, merged, float(state.actual_spend_current_moscow_day()), budget)
    gates.check()
    identity = (pull.get("merge_commit_sha"), pull.get("base", {}).get("sha"), pull.get("head", {}).get("sha"))
    if not all(isinstance(x, str) and x for x in identity):
        raise Blocked("GitHub не вернул обязательные SHA исходного PR.")
    run_id = str(event["github_run_id"])
    run_attempt = int(event["github_run_attempt"])
    event_name = str(event["github_event_name"])
    timeline_id = int(event["label_timeline_event_id"])
    receipt_identity = f"{run_id}:{run_attempt}:{event_name}:{timeline_id}"
    receipt = CommandReceipt(
        receipt_identity=receipt_identity, github_run_id=run_id,
        github_run_attempt=run_attempt, github_event_name=event_name,
        github_event_action=str(event.get("action", "labeled")),
        label_timeline_event_id=timeline_id, payload_sha256=str(event["payload_sha256"]),
        command="DOC_TRANSLATE", actor=actor, source_pr=pr_number,
    )
    receipt_claim = state.receive_command(receipt)
    if not receipt_claim.won:
        raise Blocked("Этот запуск уже был принят ранее.")
    lease_owner = LeaseOwner(receipt_identity, new_claim_nonce())
    if not state.acquire_source_lease(pr_number, lease_owner).won:
        raise Blocked("Для этого PR уже выполняется перевод. Попробуйте позже.")
    for model in (translator, critic):
        if isinstance(model, OpenAiCompatibleModel):
            model.run_id = delivery
    main_sha = github.main_sha()
    manifest = github.files(pr_number)
    branch = f"ydbdoc-review/pr-{pr_number}"
    old = github.active_drafts(branch)
    if len(old) > 1:
        raise Blocked("Найдено несколько активных переводов. Закройте дубликаты и повторите запуск.")
    if old:
        if not bool(old[0].get("draft")):
            raise Blocked("Существующий перевод уже переведён из Draft в Ready. Верните его в Draft перед чистым перезапуском.")
    effect_specs: list[tuple[str, str]] = []
    if old:
        old_pr = int(old[0]["number"])
        effect_specs.append(("CLOSE_OLD_DRAFT", f"pr:{old_pr}"))
    effect_specs.extend((
        ("DELETE_OLD_BRANCH", f"branch:{branch}"),
        ("PUSH_BRANCH", f"branch:{branch}"),
        ("CREATE_DRAFT", f"draft:{pr_number}"),
        ("POST_OR_UPDATE_COMMENT", f"comment:{pr_number}"),
    ))
    checkpoints = tuple(
        EffectCheckpoint(
            ordinal, kind, "PLANNED", target,
            hashlib.sha256(json.dumps({"kind": kind, "target": target}, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        )
        for ordinal, (kind, target) in enumerate(effect_specs)
    )
    if not state.put_effect_checkpoints(receipt_identity, checkpoints).won:
        raise Blocked("Не удалось надёжно записать план внешних действий. Перевод не выполнялся; повторите doc_translate позже.")

    def checkpoint(ordinal: int, status: str, external_id: str | None = None) -> None:
        nonlocal checkpoints
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        checkpoints = tuple(
            EffectCheckpoint(
                item.ordinal, item.kind, status if item.ordinal == ordinal else item.state,
                item.target_identity, item.payload_sha256,
                (now if item.ordinal == ordinal and status == "INTENT_RECORDED" else item.intent_recorded_at),
                (external_id if item.ordinal == ordinal and status == "CONFIRMED" else item.confirmation_external_id),
                (now if item.ordinal == ordinal and status == "CONFIRMED" else item.confirmed_at),
            ) if item.ordinal == ordinal else item
            for item in checkpoints
        )
        if not state.put_effect_checkpoints(receipt_identity, checkpoints).won:
            raise Blocked("Не удалось надёжно записать состояние внешнего действия. Перевод остановлен; повторите doc_translate позже.")

    offset = 0
    if old:
        checkpoint(0, "INTENT_RECORDED")
        github.close_pr(int(old[0]["number"]), "Этот черновик закрыт: новый doc_translate выполняет чистый перевод с нуля.")
        checkpoint(0, "CONFIRMED", str(old[0]["number"]))
        offset = 1
    checkpoint(offset, "INTENT_RECORDED")
    github.delete_branch(branch)
    checkpoint(offset, "CONFIRMED", branch)
    result = TranslationPipeline(translator, critic).run(pr_number=pr_number, gates=gates, manifest=manifest, read_current_main=lambda path: github.content(path, main_sha))
    try:
        checkpoint(offset + 1, "INTENT_RECORDED")
        checkpoint(offset + 2, "INTENT_RECORDED")
        draft = github.publish(branch, main_sha, result, f"docs: translate PR #{pr_number}")
        checkpoint(offset + 1, "CONFIRMED", branch)
        checkpoint(offset + 2, "CONFIRMED", str(draft or pr_number))
    except Blocked:
        raise
    except Exception:
        raise Blocked(
            "Безопасный перевод подготовлен, но Draft не удалось опубликовать. "
            "Технические сведения и секреты скрыты; повторите doc_translate позже."
        ) from None
    report_target = draft or pr_number
    checkpoint(offset + 3, "INTENT_RECORDED")
    github.report(report_target, result.report + f"\n\nПереводчик: `{result.translator}`. Критик: `{', '.join(result.critics)}`.")
    checkpoint(offset + 3, "CONFIRMED", str(report_target))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    event = json.loads(args.event.read_text())
    dry = args.dry_run
    repo_owner, repo_name = str(event["repository"]["full_name"]).split("/", 1)
    repository = RepoIdentity(repo_owner, repo_name)
    if dry:
        state: StatePort = DryState(repository, lambda: datetime.now(timezone.utc))
    else:
        ydb_state = YdbState(YdbConfig(
            endpoint=os.environ["YDBDOC_YDB_ENDPOINT"], database=os.environ["YDBDOC_YDB_DATABASE"],
            sa_key_file=os.environ["YDBDOC_YDB_SA_KEY_FILE"],
        ), repository)
        ydb_state.ensure_schema()
        state = ydb_state
    github = GitHubApi(event["repository"]["full_name"], os.environ["GITHUB_TOKEN"], dry)
    translator_name, critic_name = os.environ["YDBDOC_MODEL_TRANSLATE"], os.environ["YDBDOC_MODEL_CHECK"]
    if translator_name == critic_name:
        raise SystemExit("translator and critic models must be distinct")
    budget = float(os.environ["YDBDOC_DAILY_BUDGET_RUB"])
    api_url = os.environ.get("YDBDOC_LLM_BASE_URL", "https://ai.api.cloud.yandex.net/v1").rstrip("/") + "/chat/completions"
    api_key = os.environ.get("YDBDOC_YC_API_KEY") or os.environ["YANDEX_CLOUD_API_KEY_DOC_REVIEW"]
    folder_id = os.environ.get("YDBDOC_YC_FOLDER_ID") or os.environ["YANDEX_CLOUD_FOLDER_DOC_REVIEW"]
    translator = OpenAiCompatibleModel(translator_name, api_url, api_key, state, float(os.environ.get("YDBDOC_TRANSLATOR_CALL_RUB", "1")), budget, folder_id)
    critic = OpenAiCompatibleModel(critic_name, api_url, api_key, state, float(os.environ.get("YDBDOC_CRITIC_CALL_RUB", "1")), budget, folder_id)
    try:
        execute(event, github, state, translator, critic, allowed=frozenset(x.strip() for x in os.environ["YDBDOC_ALLOWED_ACTORS"].split(",") if x.strip()), budget=budget, dry_run=dry)
    except Blocked as error:
        github.report(int(event["pull_request"]["number"]), str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
