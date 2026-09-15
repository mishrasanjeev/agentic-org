# Development OIDC stub for docker-compose.dev.yml. Build context: tools/.
# Never deploy this image: it refuses to start unless AGENTICORG_ENV is
# development, local or test.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

RUN pip install --no-cache-dir "PyJWT==2.14.0" "cryptography==50.0.1"

WORKDIR /app
COPY __init__.py tools/__init__.py
COPY oidc_stub/ tools/oidc_stub/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nobody
EXPOSE 9400
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=20 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9400/healthz', timeout=3).read()"]
CMD ["python", "-m", "tools.oidc_stub"]
