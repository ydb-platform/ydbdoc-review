#!/usr/bin/env bash
# Launch local Eliza doc_translate for a source PR; log + DONE sentinel.
# Usage: run_eliza_translate.sh <source_pr_number> [ydb_repo_path]
set -euo pipefail

PR="${1:?usage: run_eliza_translate.sh <source_pr> [ydb_repo_path]}"
YDB_PATH="${2:-/Users/iuriisintiaev/projects/ydb-clone/ydb}"
# scripts/ → skill/ → skills/ → .cursor/ → repo root
REVIEW_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
LOG="/tmp/ydbdoc-translate-${PR}.log"
DONE="/tmp/ydbdoc-translate-${PR}.done"
SENTINEL="YDBDOC_ELIZA_TRANSLATE_DONE"

if [[ ! -d "${YDB_PATH}/.git" ]]; then
  echo "error: ydb repo not found at ${YDB_PATH}" >&2
  exit 1
fi
if [[ ! -x "${REVIEW_ROOT}/.venv/bin/python" ]]; then
  echo "error: missing ${REVIEW_ROOT}/.venv (pip install -e '.[dev]')" >&2
  exit 1
fi

rm -f "${DONE}"
: >"${LOG}"

{
  echo "=== Eliza translate source PR #${PR} start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "ydb=${YDB_PATH}"
  echo "ydbdoc-review=${REVIEW_ROOT}"
} | tee -a "${LOG}"

# Prepare checkout (source PR head + main for merge-base)
git -C "${YDB_PATH}" fetch origin main
git -C "${YDB_PATH}" fetch origin "pull/${PR}/head:pr-${PR}"
git -C "${YDB_PATH}" checkout "pr-${PR}"
git -C "${YDB_PATH}" fetch origin main

set +e
zsh -lic "
set -e
export YDBDOC_MODEL_PROVIDER=eliza
export ELIZA_API_ROOT=\"\${ELIZA_API_ROOT:-https://api.eliza.yandex.net}\"
export YDBDOC_ELIZA_CA_BUNDLE=\"\${YDBDOC_ELIZA_CA_BUNDLE:-/etc/ssl/certs/YandexInternalCA.pem}\"
export GITHUB_TOKEN=\"\${GITHUB_TOKEN:-\$YDB_GH_TOKEN}\"
export YDBDOC_ELIZA_TRANSLATE_FALLBACKS=\"\${YDBDOC_ELIZA_TRANSLATE_FALLBACKS:-gpt-oss-120b}\"
export YDBDOC_ELIZA_CHECK_FALLBACKS=\"\${YDBDOC_ELIZA_CHECK_FALLBACKS:-}\"
cd \"${REVIEW_ROOT}\"
source .venv/bin/activate
echo \"PROVIDER=\$YDBDOC_MODEL_PROVIDER CA=\$YDBDOC_ELIZA_CA_BUNDLE\"
python -m ydbdoc_review job \\
  --mode translate \\
  --repo ydb-platform/ydb \\
  --pr ${PR} \\
  --repo-path \"${YDB_PATH}\" \\
  --merge-base-with origin/main
" >>"${LOG}" 2>&1
EXIT=$?
set -e

{
  echo "=== Eliza translate source PR #${PR} end $(date -u +%Y-%m-%dT%H:%M:%SZ) exit=${EXIT} ==="
  echo "${SENTINEL} pr=${PR} exit=${EXIT} log=${LOG}"
} | tee -a "${LOG}" | tee "${DONE}"

exit "${EXIT}"
