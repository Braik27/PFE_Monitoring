# ══════════════════════════════════════════════════════════════
# Dockerfile — Flux Monitor backend API
#
# Build context MUST be backend/ (not repo root):
#   docker build -t flux-monitor .
#   (from inside backend/, or `docker build -f backend/Dockerfile backend/`)
#
# Frontend: the SPA is expected pre-built at ./frontend/dist INSIDE
# this context. Azure Pipelines injects it there before building.
# Locally there is no Node stage anymore — run the Vite dev server
# (npm run dev) or serve frontend/dist with the compose `frontend`
# (nginx) service instead.
#
# Single-stage on purpose: torch/faiss/pandas are runtime
# requirements, a builder stage would not shrink the final image.
# ══════════════════════════════════════════════════════════════

# Pinned base (minor + OS), not :latest
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Driver ODBC pour Azure SQL (pyodbc) — kept for STORAGE_BACKEND=azure parity
RUN apt-get update && \
        apt-get install -y --no-install-recommends curl gnupg2 unixodbc-dev && \
        curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft-prod.gpg && \
        curl -sSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
        apt-get update && \
        ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Backend deps (cached unless requirements change) ──
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code (+ frontend/dist when provided by CI) ──
COPY . .

# Writable state dirs (mounted as volumes in compose)
RUN mkdir -p /home/instance instance reports && \
    useradd --uid 10001 --create-home --shell /usr/sbin/nologin fluxuser && \
    chown -R fluxuser:fluxuser /app/instance /app/reports /home/instance

USER fluxuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD sh -c 'curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1'

# Use PORT env var (Azure sets this dynamically), fallback to 8000
CMD gunicorn --bind "0.0.0.0:${PORT:-8000}" \
    --workers 2 \
    --worker-tmp-dir /dev/shm \
    --preload \
    --timeout 300 \
    --access-logfile - \
    app:app
