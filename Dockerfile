FROM python:3.14-slim AS builder
WORKDIR /app
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
RUN pip install --upgrade pip && pip install --no-cache-dir .

FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN useradd -m agenticorg
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
USER agenticorg
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -sf http://localhost:8000/api/v1/health/liveness || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
