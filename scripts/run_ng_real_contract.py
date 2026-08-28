#!/usr/bin/env python3
"""Load the YDB SDK before pytest plugin discovery, then run the exact contract."""

from __future__ import annotations

import argparse
import sys

YDB_IMPORT_FAILURE = 86
PYTEST_IMPORT_FAILURE = 87
REAL_CONTRACT = "/app/ng/tests/test_real_ydb_state.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--junitxml", required=True)
    args = parser.parse_args(argv)
    try:
        import ydb  # noqa: F401
    except Exception:
        print("YDB_INIT_IMPORT", file=sys.stderr)
        return YDB_IMPORT_FAILURE
    try:
        import pytest
    except Exception:
        print("PYTEST_INIT_IMPORT", file=sys.stderr)
        return PYTEST_IMPORT_FAILURE
    return int(pytest.main([REAL_CONTRACT, f"--junitxml={args.junitxml}", "-vv", "-s", "-x"]))


if __name__ == "__main__":
    raise SystemExit(main())
