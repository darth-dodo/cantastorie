# Cantastorie Production Dockerfile
#
# Two-stage build for the Cantastorie FastAPI application:
#   1. css      — node:22-slim compiles the Tailwind stylesheet
#   2. runtime  — python:3.12-slim installs the exact dependency set pinned in
#                 uv.lock (uv sync --frozen) and serves the app with uvicorn
#
# Build:   docker build -t cantastorie .
# Run:     docker run -p 8000:8000 cantastorie
#
# The app serves the landing page, the child player, the parent area and the
# workshop, and runs story generation in-process. It needs OPENROUTER_API_KEY,
# the R2_* credentials and the Clerk settings at runtime (see .env.example and
# docs/setup.md). The child player itself makes no keyed calls.

FROM node:22-slim AS css

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/static/css/input.css src/static/css/input.css
COPY src/templates/ src/templates/
COPY src/static/js/ src/static/js/
RUN npx @tailwindcss/cli -i ./src/static/css/input.css -o ./src/static/css/output.css --minify

FROM python:3.12-slim

# Copy uv from the official image for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

# PYTHONUNBUFFERED: logs reach Render's log stream immediately (B6).
# uv: use the image's Python, keep no download cache in the layer.
# The venv's bin comes first on PATH, so `python` and `uvicorn` are the locked ones.
ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, from the lockfile only, so this layer caches until
# uv.lock changes. --frozen fails the build if uv.lock is out of date with
# pyproject.toml instead of re-resolving.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself, with the compiled stylesheet from stage 1
COPY README.md .
COPY src/ src/
COPY --from=css /build/src/static/css/output.css src/static/css/output.css
RUN uv sync --frozen --no-dev --no-editable

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 8000

# Health check to monitor application availability
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# PORT environment variable support for Render compatibility (defaults to 8000)
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
