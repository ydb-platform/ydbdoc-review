from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from .fixture import Pr45949Fixture
from .pipeline import Blocked, Gates, TranslationPipeline


class CommandModel:
    def __init__(self, name: str, command: str):
        self.name = name
        self.command = command

    def invoke(self, role: str, request: Mapping[str, object]) -> Mapping[str, object]:
        payload = json.dumps({"role": role, **request}, ensure_ascii=False)
        completed = subprocess.run(
            [self.command], input=payload, text=True, capture_output=True, check=False, timeout=300
        )
        if completed.returncode:
            raise Blocked(f"Модель {self.name} сломалась. Попробуйте позже, снова запустив doc_translate.")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise Blocked(f"Модель {self.name} вернула некорректный ответ: {error}") from error
        if not isinstance(value, dict):
            raise Blocked(f"Модель {self.name} вернула ответ неизвестного формата.")
        return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("doc_translate", choices=["doc_translate"])
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--merged", action="store_true")
    parser.add_argument("--spent-rub", type=float, default=0)
    parser.add_argument("--budget-rub", type=float, required=True)
    parser.add_argument("--translator-command", required=True)
    parser.add_argument("--critic-command", required=True)
    args = parser.parse_args(argv)
    allowed = frozenset(x.strip() for x in os.environ.get("YDBDOC_ALLOWED_ACTORS", "").split(",") if x.strip())
    fixture = Pr45949Fixture(args.fixture)
    pipeline = TranslationPipeline(
        CommandModel("translator-command", args.translator_command),
        CommandModel("critic-command", args.critic_command),
    )
    try:
        result = pipeline.run(
            pr_number=args.pr,
            gates=Gates(args.actor, allowed, args.merged, args.spent_rub, args.budget_rub),
            manifest=fixture.manifest,
            read_current_main=fixture.read,
        )
    except Blocked as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({
        "verdict": result.verdict,
        "report": result.report,
        "translator": result.translator,
        "critics": result.critics,
        "overlay": [
            {"path": x.path, "op": x.op.value, "sha256": x.sha256, "source_path": x.source_path}
            for x in result.overlay
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
