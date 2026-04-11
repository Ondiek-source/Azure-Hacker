FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_OUTPUT_DIR=/outputs

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/ ./worker/
COPY templates/ ./templates/
COPY app.py ./

RUN mkdir -p "$JOB_OUTPUT_DIR"

EXPOSE 8000

ENTRYPOINT ["python", "app.py"]
