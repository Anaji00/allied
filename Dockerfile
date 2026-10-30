# ==============================================================================
# Allied Welding Systems Universal Engine — Backend Dockerfile
# Python 3.11 / FastAPI / Uvicorn
# ==============================================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install production dependencies only
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Create the non-root user before copying, so ownership can be set in one pass.
# The uid/gid are fixed so that `chown -R 10001:10001 ./data ./output` on the
# host matches the container user for the bind-mounted volumes.
RUN groupadd -r -g 10001 alliedgroup \
    && useradd -r -u 10001 -g alliedgroup allieduser

# Copy application source and data
COPY --chown=allieduser:alliedgroup app/ ./app/
COPY --chown=allieduser:alliedgroup alembic.ini ./alembic.ini
COPY --chown=allieduser:alliedgroup migrations/ ./migrations/
# Writable mount points. These are bind-mounted at runtime; creating them here
# with the right ownership means the container user can write to them even
# before the host directories exist.
RUN mkdir -p /app/data /app/output/packing_slips \
    && chown -R allieduser:alliedgroup /app

# Expose the API port
EXPOSE 8000

USER allieduser

# Run with Uvicorn — bind to 0.0.0.0 so the container is reachable
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
