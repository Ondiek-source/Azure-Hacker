FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_OUTPUT_DIR=/outputs

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

COPY worker/ ./worker/

RUN mkdir -p "$JOB_OUTPUT_DIR"

RUN groupadd --gid 1000 worker \
    && useradd --uid 1000 --gid worker --no-create-home worker \
    && chown -R worker:worker /app /outputs
USER worker

ENTRYPOINT ["python", "-m", "worker.platform.cli"]
CMD ["--config", "/config.json"]
