# The API image: FastAPI under uvicorn, run as an unprivileged user. Built by
# deploy/docker-compose.yml (see docs/07-deployment.md); also runs migrations and the operator CLI.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

# Dependencies and the application package (no tests, scripts or local state: .dockerignore).
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY alembic ./alembic
COPY alembic.ini ./

# Menu photos live on a volume mounted here.
RUN mkdir -p /var/lib/restaurant/media && chown app:app /var/lib/restaurant/media
USER app
EXPOSE 8000

# --ws-max-size: refuse oversized WebSocket frames before they are buffered (docs/06 §7).
# --no-proxy-headers: client addresses come from X-Forwarded-For only via TRUSTED_PROXIES.
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2", \
     "--ws-max-size", "65536", "--no-proxy-headers", "--no-server-header", \
     "--timeout-graceful-shutdown", "20"]
