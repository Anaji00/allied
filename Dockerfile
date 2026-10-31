# ==============================================================================
# Allied Operations backend: FastAPI on Uvicorn, Python 3.11
#
#   docker compose up --build          (with the frontend; see docker-compose.yml)
#
# One process, one worker: the app runs its own background workers (mailbox,
# monday inbox, coordination and autopilot) inside the web process, so a second
# Uvicorn worker would run every automation twice.
# ==============================================================================

# --- Build: install dependencies into a virtualenv ---------------------------
FROM python:3.11-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /tmp/requirements.txt

# --- Runtime ------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# tesseract-ocr: pytesseract reads scanned POs and drawings through it.
# tini: passes stop signals on, so the workers shut down cleanly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tini \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid, so host folders mounted at /app/data and /app/output can be
# made writable with: chown -R 10001:10001 data output
RUN groupadd -r -g 10001 allied && useradd -r -u 10001 -g allied -d /app allied

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    # The master catalog (items, customers, vendors, aliases) is rewritten by
    # CSV imports, so it lives in the data volume, not in the image. The
    # entrypoint copies the shipped defaults in only where a file is missing.
    DATA_DIR=/app/data/catalog \
    MASTER_ITEMS_PATH=/app/data/catalog/master_items.csv \
    MASTER_CUSTOMERS_PATH=/app/data/catalog/master_customers.csv \
    MASTER_VENDORS_PATH=/app/data/catalog/master_vendors.csv \
    ALIAS_DICT_PATH=/app/data/catalog/alias_dictionary.json

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=allied:allied app/ ./app/
COPY --chown=allied:allied migrations/ ./migrations/
COPY --chown=allied:allied alembic.ini ./alembic.ini
COPY --chmod=755 docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN mkdir -p /app/data /app/output && chown -R allied:allied /app/data /app/output

USER allied
EXPOSE 8000
VOLUME ["/app/data", "/app/output"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4)"]

ENTRYPOINT ["tini", "--", "backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
