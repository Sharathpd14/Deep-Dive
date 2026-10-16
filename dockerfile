FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so this layer is cached until the lockfile changes
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN mkdir -p /app/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'))"

# One worker on purpose: runs are background threads and the run limits live in this process.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]