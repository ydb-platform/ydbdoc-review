#!/usr/bin/env bash
# Build the action image from Dockerfile; optional GHCR fallback (disabled by default).
set -uo pipefail

ACTION_PATH="${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH is required}"
WORKSPACE="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
LOCAL_TAG="ydbdoc-review-local:$$"
REF="${GITHUB_ACTION_REF:-v0.1.0}"
REF="${REF#refs/tags/}"
FALLBACK_IMAGE="ghcr.io/ydb-platform/ydbdoc-review:${REF}"

ACTION_SHA="$(git -C "${ACTION_PATH}" rev-parse HEAD 2>/dev/null || true)"
BUILD_SHA="${YDBDOC_GIT_SHA:-${ACTION_SHA:-${REF}}}"

echo "ydbdoc-review: action ref=${REF} checkout=${ACTION_SHA:-unknown} build_sha=${BUILD_SHA}" >&2

IMAGE=""
if docker build -t "${LOCAL_TAG}" \
  -f "${ACTION_PATH}/Dockerfile" \
  --build-arg "YDBDOC_GIT_SHA=${BUILD_SHA}" \
  "${ACTION_PATH}"; then
  IMAGE="${LOCAL_TAG}"
  echo "ydbdoc-review: using locally built image ${LOCAL_TAG}" >&2
else
  if [[ "${YDBDOC_GHCR_FALLBACK:-}" != "1" ]]; then
    echo "::error::ydbdoc-review: docker build failed and GHCR fallback is disabled (set YDBDOC_GHCR_FALLBACK=1 to allow stale fallback image ${FALLBACK_IMAGE})" >&2
    exit 1
  fi
  echo "::warning::ydbdoc-review: local docker build failed; pulling GHCR fallback ${FALLBACK_IMAGE} (may be stale — publish via docker-publish workflow)" >&2
  if ! docker pull "${FALLBACK_IMAGE}"; then
    echo "::error::ydbdoc-review: docker pull ${FALLBACK_IMAGE} failed." >&2
    exit 1
  fi
  IMAGE="${FALLBACK_IMAGE}"
fi

cleanup() {
  if [[ "${IMAGE}" == "${LOCAL_TAG}" ]]; then
    docker rmi -f "${LOCAL_TAG}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

docker_env=()
for var in \
  GITHUB_TOKEN GITHUB_PUSH_TOKEN YDBDOC_PUSH_PAT YDBDOC_REPO_PATH \
  YANDEX_CLOUD_FOLDER_DOC_REVIEW YANDEX_CLOUD_API_KEY_DOC_REVIEW \
  YDBDOC_YC_FOLDER_ID YDBDOC_YC_API_KEY \
  YDBDOC_REVIEW_ENABLED YDBDOC_MODEL_CHECK YDBDOC_MODEL_TRANSLATE \
  INPUT_REPO INPUT_PR INPUT_MERGE_BASE_WITH INPUT_DRY_RUN INPUT_NO_COMMIT INPUT_MODE; do
  if [[ -n "${!var:-}" ]]; then
    docker_env+=(-e "${var}=${!var}")
  fi
done

set -e
exec docker run --rm \
  -v "${WORKSPACE}:/github/workspace" \
  -w /github/workspace \
  -e "GITHUB_WORKSPACE=/github/workspace" \
  -e "GITHUB_ACTION_REF=${GITHUB_ACTION_REF:-}" \
  -e "YDBDOC_GIT_SHA=${BUILD_SHA}" \
  "${docker_env[@]}" \
  "${IMAGE}"
