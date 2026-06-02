FROM python:3.12-slim

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
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir /app

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
