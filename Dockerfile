# AWS ECR Public mirrors Docker Hub library images; more reliable from GitHub runners.
FROM public.ecr.aws/docker/library/python:3.12-slim

ARG YDBDOC_GIT_SHA=dev
ENV YDBDOC_GIT_SHA=${YDBDOC_GIT_SHA}
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
COPY ng/pyproject.toml /app/ng/
COPY ng/src /app/ng/src
COPY ng/tests/test_state_contract.py ng/tests/test_real_ydb_state.py /app/ng/tests/
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir /app \
    && pip install --no-cache-dir /app/ng pytest==8.3.5

COPY entrypoint.sh /app/entrypoint.sh
COPY scripts/run_ng_real_ydb_preflight.py /app/scripts/run_ng_real_ydb_preflight.py
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
