# Development model stub for docker-compose.dev.yml. Build context: the
# repository root, filtered by Dockerfile.dockerignore next to this file to the
# record/replay harness, the scripted model and the stub itself.
# Never deploy this image: it refuses to start unless AGENTICORG_ENV is
# development, local or test.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

RUN pip install --no-cache-dir "langchain-core==1.6.3" "pydantic==2.13.5" "pydantic-settings==2.15.0"

WORKDIR /app
COPY core/__init__.py core/config.py core/model_replay.py core/
COPY core/test_doubles/__init__.py core/test_doubles/scripted_model.py core/test_doubles/
COPY tools/__init__.py tools/
COPY tools/model_stub/ tools/model_stub/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]
CMD ["python", "-m", "tools.model_stub"]
