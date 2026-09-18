# AWS ECR Public mirrors Docker Hub library images; more reliable from GitHub runners.
# Override at build time: --build-arg BASE_IMAGE=python:3.12-slim (Hub direct).
ARG BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
FROM public.ecr.aws/docker/library/node:24-bookworm-slim AS docs-builder
RUN npm install --global @diplodoc/cli@5.61.0

FROM ${BASE_IMAGE}
COPY --from=docs-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=docs-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@diplodoc/cli/build/index.js /usr/local/bin/yfm

ARG YDBDOC_GIT_SHA=dev
LABEL org.opencontainers.image.source="https://github.com/ydb-platform/ydbdoc-review" \
      org.opencontainers.image.revision="${YDBDOC_GIT_SHA}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir /app

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
