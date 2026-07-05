# Cantastorie Production Dockerfile
#
# Single-stage Dockerfile for the Cantastorie FastAPI application.
# Uses Python 3.12-slim base with uv for fast dependency management.
#
# Build:   docker build -t cantastorie .
# Run:     docker run -p 8000:8000 cantastorie
#
# The app serves the player page and parent area only; it needs no API keys.
# OPENROUTER_API_KEY and ELEVENLABS_API_KEY belong to the pipeline environment,
# not this container (see .env.example).

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

WORKDIR /app

# Copy all necessary files for installation
COPY pyproject.toml .
COPY uv.lock .
COPY README.md .
COPY src/ src/
COPY --from=css /build/src/static/css/output.css src/static/css/output.css

# Install the package into the container's Python environment
RUN uv pip install --system .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 8000

# Health check to monitor application availability
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# PORT environment variable support for Render compatibility (defaults to 8000)
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
