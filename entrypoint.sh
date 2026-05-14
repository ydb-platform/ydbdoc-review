#!/bin/sh
set -eu

export YDBDOC_REPO_PATH="${YDBDOC_REPO_PATH:-${GITHUB_WORKSPACE:-}}"

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
