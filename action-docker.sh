#!/usr/bin/env bash
set -euo pipefail
ACTION_PATH="${GITHUB_ACTION_PATH:?}"
WORKSPACE="${GITHUB_WORKSPACE:?}"
IMAGE="ydbdoc-review-local:$$"
docker build -t "$IMAGE" -f "$ACTION_PATH/Dockerfile" "$ACTION_PATH"
trap 'docker rmi -f "$IMAGE" >/dev/null 2>&1 || true' EXIT
docker_env=()
for var in GITHUB_TOKEN GITHUB_PUSH_TOKEN GITHUB_ACTOR YDB_SA_KEY \
  YDBDOC_ALLOWED_ACTORS YDBDOC_DAILY_BUDGET_RUB \
  YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE YDBDOC_MAX_SOURCE_CHARACTERS \
  YDBDOC_YDB_ENDPOINT YDBDOC_YDB_DATABASE YDBDOC_ELIZA_CA_BUNDLE INPUT_REPO INPUT_PR INPUT_MODE INPUT_CONFIG; do
  docker_env+=(-e "$var")
done
# Explicit endpoint token_env names come from the technical config, never secret values.
while IFS= read -r var; do
  [[ -n "$var" ]] || continue
  [[ "$var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || exit 2
  docker_env+=(-e "$var")
done < <(python3 - "${INPUT_CONFIG}" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    data = json.load(stream)
print('\n'.join(sorted({ep['token_env'] for role in data['models'].values()
                         for ep in role.values() if ep})))
PY
)
NAME="ydbdoc-review-$$"
stop_container() {
  docker stop --time 20 "$NAME" >/dev/null 2>&1 || true
  wait "${docker_pid}" || true
  exit 143
}
trap stop_container TERM INT
# Config is mounted at its existing absolute path; checkout is read-only.
docker run --rm --init --name "$NAME" -v "$WORKSPACE:$WORKSPACE:ro" \
  -w "$WORKSPACE" "${docker_env[@]}" "$IMAGE" &
docker_pid=$!
wait "$docker_pid"
