FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn[standard]>=0.34.0" "httpx>=0.28.0"

# Copy application source and assets
COPY src/ src/
COPY assets/ assets/

# Create non-root user
RUN groupadd --gid 1000 cdn \
    && useradd --uid 1000 --gid cdn --shell /bin/sh --create-home cdn \
    && chown -R cdn:cdn /app

USER cdn

# Runtime configuration
ENV CDN_ROLE=edge \
    CDN_HOST=0.0.0.0 \
    CDN_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["python", "-m", "entrypoint"]
