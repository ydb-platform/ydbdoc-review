#!/usr/bin/env python3
"""Require the concise knowledge bank when committing requirement changes."""

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

    if "REQUIREMENTS_RU.md" not in staged or ".cursor/knowledge-bank.md" in staged:
        output("allow")
        return 0

    output(
        "deny",
        "Коммит заблокирован: при изменении REQUIREMENTS_RU.md обнови "
        "и добавь в staged .cursor/knowledge-bank.md.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
