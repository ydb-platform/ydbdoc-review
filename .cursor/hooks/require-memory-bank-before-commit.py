#!/usr/bin/env python3
"""Block Codex git commits that omit the project's Memory Bank update."""

from __future__ import annotations

import json
import subprocess
import sys


def output(permission: str, message: str = "") -> None:
    payload = {"permission": permission}
    if message:
        payload["user_message"] = message
        payload["agent_message"] = message
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    event = json.load(sys.stdin)
    command = str(event.get("command", ""))
    if "git" not in command or "commit" not in command:
        output("allow")
        return 0

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output("deny", "Не удалось проверить staged-файлы перед commit.")
        return 0

    staged = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if not staged:
        output("allow")
        return 0

    has_index = "MEMORY_BANK.md" in staged
    has_detail = any(path.startswith("docs/memory-bank/") for path in staged)
    if has_index and has_detail:
        output("allow")
        return 0

    output(
        "deny",
        "Коммит заблокирован: сначала обнови и добавь в staged MEMORY_BANK.md "
        "и соответствующий файл docs/memory-bank/.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
