# syntax=docker/dockerfile:1
#
# The whole app as one image: the React build, the Python pipeline, and the
# FastAPI server that serves both. `compose.yaml` puts Caddy in front of it
# for HTTPS; this image on its own speaks plain http on :8000.
#
# Two stages, so Node and node_modules never reach the final image -- only the
# ~600 KB of built files do.

# ---- 1. the UI ---------------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci
COPY web/ui/ ./
RUN npm run build

# ---- 2. the server -----------------------------------------------------------
FROM python:3.14-slim

# Pinned to the uv this repo is developed with; uv_build in pyproject.toml is
# pinned to the same minor.
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/uv

# DejaVu Sans Mono is the Linux font capture/fonts.py looks for. Without it
# every terminal screenshot falls back to Pillow's bitmap font.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies before code. Docker reuses a layer until something it COPYs
# changes, so editing a .py file rebuilds from `COPY src` down and keeps the
# ~400 MB of wheels above it cached.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY web/requirements.txt web/requirements.txt
RUN uv pip install -r web/requirements.txt

COPY src ./src
COPY web/server ./web/server
# --inexact matters: a plain `uv sync` makes the venv match uv.lock EXACTLY,
# which would uninstall fastapi and uvicorn -- they live in
# web/requirements.txt, not the lockfile.
RUN uv sync --frozen --no-dev --inexact
COPY --from=ui /ui/dist ./web/ui/dist

# Not root. The model's code runs as this user, so a bad program can damage
# runs/ but not the image. runs/ is created here, owned by `app`, so the
# named volume compose mounts over it starts with the right owner.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app/runs \
 && chown app:app /app/runs
USER app

EXPOSE 8000

# One worker, and that is a requirement rather than a default: running jobs
# live in memory (jobs.JobRegistry), so a second worker would not know about
# a run the first one started.
#
# --forwarded-allow-ips "*" trusts X-Forwarded-Proto from any sender. Safe only
# because compose publishes no port for this container: Caddy is the only
# thing that can reach it. It is what makes the sign-in cookie Secure.
CMD ["python", "-m", "uvicorn", "web.server.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
