#!/bin/sh
set -eu

# In Docker Actions the repo is mounted at GITHUB_WORKSPACE (e.g. /github/workspace).
# Workflows often set YDBDOC_REPO_PATH=${{ github.workspace }}, which is a *runner* path
# (/home/runner/...) and does not exist inside the container — then merge-base fails.
REPO="${INPUT_REPO_PATH:-${YDBDOC_REPO_PATH:-}}"
if [ -z "${REPO}" ]; then
  REPO="${GITHUB_WORKSPACE:-}"
elif [ ! -e "${REPO}/.git" ] && [ -n "${GITHUB_WORKSPACE:-}" ] && [ -e "${GITHUB_WORKSPACE}/.git" ]; then
  REPO="${GITHUB_WORKSPACE}"
fi
export YDBDOC_REPO_PATH="${REPO}"

if [ -z "${REPO}" ]; then
  echo "::error::repo_path is required" >&2
  exit 2
fi

# Legacy: workflow may still pass YDBDOC_PUSH_PAT; app reads GITHUB_PUSH_TOKEN. ydb CI uses GITHUB_TOKEN only.
if [ -n "${YDBDOC_PUSH_PAT:-}" ] && [ -z "${GITHUB_PUSH_TOKEN:-}" ]; then
  export GITHUB_PUSH_TOKEN="${YDBDOC_PUSH_PAT}"
fi

# Bind-mounted repo: runner UID != container user → "dubious ownership". .git may be a *file* (gitdir), not dir.
if [ -n "${YDBDOC_REPO_PATH}" ] && [ -e "${YDBDOC_REPO_PATH}/.git" ]; then
  git config --global --add safe.directory "${YDBDOC_REPO_PATH}"
fi

MB="${INPUT_MERGE_BASE_WITH:-origin/main}"
MODE="${INPUT_MODE:-run}"
OPTS=""
case "${INPUT_DRY_RUN:-false}" in true|True|TRUE) OPTS="${OPTS} --dry-run" ;; esac
case "${INPUT_NO_COMMIT:-false}" in true|True|TRUE) OPTS="${OPTS} --no-commit" ;; esac

CLI="python -m ydbdoc_review"
if command -v ydbdoc-review >/dev/null 2>&1; then
  CLI="ydbdoc-review"
fi

case "${MODE}" in
  run|verify|continue)
    ;;
  *)
    echo "::error::unsupported mode: ${MODE}" >&2
    exit 2
    ;;
esac

set -- ${CLI} job \
  --mode "${MODE}" \
  --repo "${INPUT_REPO}" \
  --pr "${INPUT_PR}" \
  --merge-base-with "${MB}" \
  ${OPTS}
set -- "$@" --repo-path "${REPO}"

exec "$@"
