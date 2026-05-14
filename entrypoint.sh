#!/bin/sh
set -eu

# In Docker Actions the repo is mounted at GITHUB_WORKSPACE (e.g. /github/workspace).
# Workflows often set YDBDOC_REPO_PATH=${{ github.workspace }}, which is a *runner* path
# (/home/runner/...) and does not exist inside the container — then merge-base fails.
REPO="${YDBDOC_REPO_PATH:-}"
if [ -z "${REPO}" ]; then
  REPO="${GITHUB_WORKSPACE:-}"
elif [ ! -d "${REPO}/.git" ] && [ -n "${GITHUB_WORKSPACE:-}" ] && [ -d "${GITHUB_WORKSPACE}/.git" ]; then
  REPO="${GITHUB_WORKSPACE}"
fi
export YDBDOC_REPO_PATH="${REPO}"

# Repo is bind-mounted from the runner; file UID != container user → Git "dubious ownership" (2.35+).
if [ -n "${YDBDOC_REPO_PATH}" ] && [ -d "${YDBDOC_REPO_PATH}/.git" ]; then
  git config --global --add safe.directory "${YDBDOC_REPO_PATH}"
fi

MB="${INPUT_MERGE_BASE_WITH:-origin/main}"
OPTS=""
case "${INPUT_DRY_RUN:-false}" in true|True|TRUE) OPTS="${OPTS} --dry-run" ;; esac
case "${INPUT_NO_COMMIT:-false}" in true|True|TRUE) OPTS="${OPTS} --no-commit" ;; esac

set -- python -m ydbdoc_review run \
  --repo "${INPUT_REPO}" \
  --pr "${INPUT_PR}" \
  --merge-base-with "${MB}" \
  ${OPTS}

if [ -n "${YDBDOC_REPO_PATH}" ] && [ -d "${YDBDOC_REPO_PATH}/.git" ]; then
  set -- "$@" --repo-path "${YDBDOC_REPO_PATH}"
fi

exec "$@"
