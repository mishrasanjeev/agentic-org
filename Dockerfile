# SEC-010: production base images pinned by digest to keep rebuilds
# reproducible. Refresh via scripts/refresh_image_digests.sh after a
# Renovate/Dependabot bump confirms upstream is safe.
# python:3.14-slim @ 2026-05-01
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder
WORKDIR /app
# Build deps for patched Pillow (required by fastembed) and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libjpeg-dev zlib1g-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY core/ core/
COPY api/ api/
COPY auth/ auth/
COPY connectors/ connectors/
COPY workflows/ workflows/
COPY scaling/ scaling/
COPY observability/ observability/
COPY audit/ audit/
COPY schemas/ schemas/
COPY migrations/ migrations/
RUN pip install --upgrade pip && pip install --no-cache-dir ".[v4]"

# Presidio (installed via the [v4] extra) uses spaCy for NER-based PII
# detection. Without a language model the AnalyzerEngine constructor raises
# OSError and every agent run 500s with "util.py:531 (OSError)". Bake the
# smallest English model into the image so PII redaction actually runs.
RUN python -m spacy download en_core_web_sm

FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m agenticorg
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
USER agenticorg
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/liveness', timeout=4).read()"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "20"]
