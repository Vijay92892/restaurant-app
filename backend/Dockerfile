# The API image: FastAPI under uvicorn, run as an unprivileged user. Built by
# deploy/docker-compose.yml (see docs/07-deployment.md); also runs migrations and the operator CLI.
FROM python:3.12-slim

# PIP_DEFAULT_TIMEOUT / PIP_RETRIES: downloads from PyPI can be slow from some cloud regions.
# As environment variables they also reach pip's build subprocess, which ignores the
# command-line --default-timeout (a build once failed there on its 15-second default).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

# Dependencies first, in their own layer: downloaded again only when pyproject.toml changes,
# not for every code change. The build tool (setuptools) comes with them, so installing the
# application itself needs no network. (No tests, scripts or local state: .dockerignore.)
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['build-system']['requires'] + p['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt
COPY app ./app
RUN pip install --no-deps --no-build-isolation .
COPY alembic ./alembic
COPY alembic.ini ./

# Menu photos live on a volume mounted here.
RUN mkdir -p /var/lib/restaurant/media && chown app:app /var/lib/restaurant/media
USER app
EXPOSE 8000

# --ws-max-size: refuse oversized WebSocket frames before they are buffered (docs/06 §7).
# --no-proxy-headers: client addresses come from X-Forwarded-For only via TRUSTED_PROXIES.
# Worker processes: WEB_CONCURRENCY (set by deploy/make_env.py: 2, or 1 on a small server).
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--ws-max-size", "65536", "--no-proxy-headers", "--no-server-header", \
     "--timeout-graceful-shutdown", "20"]
