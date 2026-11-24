ARG BASE_IMAGE=python:3.12.6-slim-bookworm

FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 1001 appuser
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r requirements.txt

COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser web/ ./web/

RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

CMD ["sh", "-c", "test -d data/chroma || python scripts/build_index.py; exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
