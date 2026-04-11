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

ENTRYPOINT ["python", "-m", "worker.platform.cli"]
CMD ["--config", "/outputs/config.json"]
