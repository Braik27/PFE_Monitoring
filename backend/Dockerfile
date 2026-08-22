# ══════════════════════════════════════════════════════════════
# Dockerfile — Azure App Service (Linux)
# Build : docker build -t flux-monitor .
# Run   : docker run -p 8000:8000 --env-file .env flux-monitor
#
# In CI (pipeline): frontend/dist is pre-built and copied into
#   the build context before `docker build`. The Node build is
#   skipped automatically when the dist folder already exists.
# Locally: Node.js is installed and frontend is built inside Docker.
# ══════════════════════════════════════════════════════════════

FROM python:3.12-slim

# Driver ODBC pour Azure SQL (pyodbc)
RUN apt-get update && \
        apt-get install -y --no-install-recommends curl gnupg2 unixodbc-dev && \
        curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft-prod.gpg && \
        curl -sSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
        apt-get update && \
        ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Backend deps ──
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Code source (inclut frontend/dist si prébuild CI) ──
COPY . .

# ── Frontend build (skippé si dist déjà présent) ──
RUN if [ ! -d "frontend/dist" ] || [ -z "$(ls -A frontend/dist 2>/dev/null)" ]; then \
        echo "📦 frontend/dist absent — installation de Node.js et build..."; \
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
        apt-get install -y nodejs && \
        apt-get clean && \
        cd frontend && npm ci && npm run build; \
    else \
        echo "✅ frontend/dist trouvé — build ignoré"; \
    fi

# Dossier instance (SQLite) — écrasé par Azure Volume en prod
RUN mkdir -p /home/instance instance

EXPOSE 8000

# Use PORT env var (Azure sets this dynamically), fallback to 8000
CMD gunicorn --bind "0.0.0.0:${PORT:-8000}" \
    --workers 2 \
    --worker-tmp-dir /dev/shm \
    --preload \
    --timeout 300 \
    --access-logfile - \
    app:app