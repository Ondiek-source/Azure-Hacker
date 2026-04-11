FROM python:3.11-slim AS base

# ── Environment ──
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_OUTPUT_DIR=/outputs

WORKDIR /app

# ── Dependencies (cached layer) ──
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

# ── Application code ──
COPY worker/ ./worker/

# ── Output directory ──
RUN mkdir -p "$JOB_OUTPUT_DIR"

# ── Non-root user ──
RUN groupadd --gid 1000 worker \
    && useradd --uid 1000 --gid worker --no-create-home worker \
    && chown -R worker:worker /app /outputs
USER worker

# ── Entry point ──
ENTRYPOINT ["python", "-m", "worker.platform.cli"]
