#!/usr/bin/env bash
# Build the action image from Dockerfile; optional GHCR fallback (disabled by default).
set -uo pipefail

ACTION_PATH="${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH is required}"
WORKSPACE="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
LOCAL_TAG="ydbdoc-review-local:$$"
REF="${GITHUB_ACTION_REF:-v0.1.0}"
REF="${REF#refs/tags/}"
FALLBACK_IMAGE="ghcr.io/ydb-platform/ydbdoc-review:${REF}"
# Prefer ECR Public mirror; on 429/outage retry Docker Hub library (§6.229).
BASE_IMAGES=(
  "public.ecr.aws/docker/library/python:3.12-slim"
  "python:3.12-slim"
)

ACTION_SHA="$(git -C "${ACTION_PATH}" rev-parse HEAD 2>/dev/null || true)"
BUILD_SHA="${YDBDOC_GIT_SHA:-${ACTION_SHA:-${REF}}}"

echo "ydbdoc-review: action ref=${REF} checkout=${ACTION_SHA:-unknown} build_sha=${BUILD_SHA}" >&2

IMAGE=""
BUILD_OK=0
for BASE_IMAGE in "${BASE_IMAGES[@]}"; do
  echo "ydbdoc-review: docker build with BASE_IMAGE=${BASE_IMAGE}" >&2
  if docker build -t "${LOCAL_TAG}" \
    -f "${ACTION_PATH}/Dockerfile" \
    --build-arg "YDBDOC_GIT_SHA=${BUILD_SHA}" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    "${ACTION_PATH}"; then
    IMAGE="${LOCAL_TAG}"
    BUILD_OK=1
    echo "ydbdoc-review: using locally built image ${LOCAL_TAG}" >&2
    break
  fi
  echo "::warning::ydbdoc-review: docker build failed for BASE_IMAGE=${BASE_IMAGE}" >&2
done

if [[ "${BUILD_OK}" -ne 1 ]]; then
  if [[ "${YDBDOC_GHCR_FALLBACK:-}" != "1" ]]; then
    echo "::error::ydbdoc-review: docker build failed for all base images and GHCR fallback is disabled (set YDBDOC_GHCR_FALLBACK=1 to allow stale fallback image ${FALLBACK_IMAGE})" >&2
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

# Pass-by-name (-e VAR) so multiline JSON secrets (YDB_SA_KEY) survive.
# Host file paths are not visible inside the container unless mounted below.
docker_env=()
for var in \
  GITHUB_TOKEN GITHUB_PUSH_TOKEN YDBDOC_PUSH_PAT YDBDOC_REPO_PATH \
  GITHUB_ACTOR \
  YANDEX_CLOUD_FOLDER_DOC_REVIEW YANDEX_CLOUD_API_KEY_DOC_REVIEW \
  YDBDOC_YC_FOLDER_ID YDBDOC_YC_API_KEY \
  YDBDOC_REVIEW_ENABLED YDBDOC_MODEL_CHECK YDBDOC_MODEL_TRANSLATE \
  YDBDOC_ALLOWED_ACTORS YDBDOC_DAILY_BUDGET_RUB YDBDOC_SKIP_OPS_GATES \
  YDBDOC_TRANSCRIPT_BACKEND YDBDOC_RUNS_LEDGER \
  YDBDOC_YDB_ENDPOINT YDBDOC_YDB_DATABASE \
  YDB_SA_KEY YDBDOC_YDB_SA_KEY_JSON \
  YDBDOC_S3_BUCKET YDBDOC_S3_ACCESS_KEY_ID YDBDOC_S3_SECRET_ACCESS_KEY \
  YDBDOC_S3_ENDPOINT YDBDOC_S3_REGION \
  INPUT_REPO INPUT_PR INPUT_MERGE_BASE_WITH INPUT_DRY_RUN INPUT_NO_COMMIT INPUT_MODE; do
  if [[ -n "${!var:-}" ]]; then
    docker_env+=(-e "${var}")
  fi
done

docker_mounts=(-v "${WORKSPACE}:/github/workspace")
# Optional: host SA key file → fixed path inside the container (§6.143).
if [[ -n "${YDBDOC_YDB_SA_KEY_FILE:-}" && -f "${YDBDOC_YDB_SA_KEY_FILE}" ]]; then
  docker_mounts+=(-v "${YDBDOC_YDB_SA_KEY_FILE}:/run/secrets/ydb-sa.json:ro")
  docker_env+=(-e "YDBDOC_YDB_SA_KEY_FILE=/run/secrets/ydb-sa.json")
fi

set -e
exec docker run --rm \
  "${docker_mounts[@]}" \
  -w /github/workspace \
  -e "GITHUB_WORKSPACE=/github/workspace" \
  -e "GITHUB_ACTION_REF=${GITHUB_ACTION_REF:-}" \
  -e "YDBDOC_GIT_SHA=${BUILD_SHA}" \
  "${docker_env[@]}" \
  "${IMAGE}"
