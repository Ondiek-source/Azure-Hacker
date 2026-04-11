from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid

from pathlib import Path
from typing import Any, Dict, List, cast

from flask import Flask, jsonify, render_template, request, send_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("portal")

app = Flask(__name__)
JOBS_DIR = Path("/jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS: Dict[str, Any] = {
    "max_requests": 5000,
    "max_runtime_minutes": 10,
    "preview_rows": 50,
    "max_bytes": 52428800,
    "max_response_size": 1048576,
    "requests_per_second_limit": 10,
    "max_retries_per_cycle": 3,
    "per_host_concurrency": 2,
    "max_cost_usd": 1.0,
}


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run_job():
    data = request.get_json()

    target_url: str = data.get("target_url", "").strip()
    mode: str = data.get("mode", "paged")
    page_start: int = int(data.get("page_start", 1))
    page_end: int = int(data.get("page_end", 10))
    concurrency: int = int(data.get("concurrency", 4))

    errors: List[str] = []
    if not target_url:
        errors.append("Target URL is required")
    if not target_url.startswith(("http://", "https://")):
        errors.append("URL must start with http:// or https://")
    if mode == "paged":
        if "{page}" not in target_url:
            errors.append("URL must contain {page} placeholder")
        if page_start > page_end:
            errors.append("Page start must be <= page end")
        if (page_end - page_start) > 500:
            errors.append("Max 500 pages per job")
    if concurrency < 1 or concurrency > 20:
        errors.append("Concurrency must be 1-20")

    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    job_id = f"job-{uuid.uuid4().hex[:8]}"
    output_dir = str(JOBS_DIR / job_id)

    config: Dict[str, Any] = {
        "job_id": job_id,
        "mode": mode,
        "target_url": target_url,
        "output_dir": output_dir,
        "concurrency": concurrency,
        **DEFAULTS,
    }

    if mode == "paged":
        config["page_start"] = page_start
        config["page_end"] = page_end
    elif mode == "cursor":
        config["cursor_param"] = data.get("cursor_param", "cursor").strip()
        config["next_cursor_key"] = data.get("next_cursor_key", "next_cursor").strip()
    elif mode == "api_loop":
        try:
            raw = json.loads(data.get("query_variations", "[]"))
            if not isinstance(raw, list):
                raise ValueError
            variations = cast(List[Dict[str, Any]], raw)
            if len(variations) == 0:
                raise ValueError
            config["query_variations"] = variations
            config["max_iterations"] = len(variations)
        except (json.JSONDecodeError, ValueError):
            return jsonify({"error": "query_variations must be a JSON array of objects"}), 400

    logger.info("Starting job %s | mode=%s | url=%s", job_id, mode, target_url)

    config_path = Path(output_dir) / "config.json"
    config_path.write_text(json.dumps(config, indent=2))

    try:
        result = subprocess.run(
            [sys.executable, "-m", "worker.platform.cli", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        logger.info("Job %s finished | exit=%d", job_id, result.returncode)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Job timed out (10 min limit)"}), 408

    output_path = Path(output_dir)
    full_csv = output_path / "full.csv"
    status_json = output_path / "status.json"

    if not full_csv.exists():
        return jsonify({
            "error": "Job completed but no data was extracted",
            "logs": result.stderr[-2000:] if result.stderr else "",
        }), 500

    status: Dict[str, Any] = {}
    if status_json.exists():
        status = json.loads(status_json.read_text())

    return jsonify({
        "job_id": job_id,
        "status": status.get("state", "completed"),
        "records": status.get("records_collected", 0),
        "requests": status.get("request_count", 0),
        "elapsed": f"{status.get('total_latency_ms', 0) / 1000:.1f}s",
        "download_url": f"/download/{job_id}/full.csv",
        "preview_url": f"/download/{job_id}/preview.csv",
    })


@app.route("/download/<job_id>/<filename>")
def download(job_id: str, filename: str):
    file_path = JOBS_DIR / job_id / filename
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(file_path), as_attachment=True, download_name=filename)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
