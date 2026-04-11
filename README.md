
# Azure Hacker — Multi-Mode Job Runner

A containerized, async Python worker that scrapes data from web APIs and
paginated websites.  It handles retries, rate limiting, budget enforcement,
and writes results to CSV — all from a single JSON config file.

## Table of contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Three modes](#three-modes)
- [Config reference](#config-reference)
- [Output files](#output-files)
- [Safety features](#safety-features)
- [Architecture](#architecture)
- [Running locally](#running-locally-without-docker)
- [Running in Docker](#running-in-docker)
- [Programmatic usage](#programmatic-usage)
- [Next steps](#next-steps)

---

## Quick start

```bash
# 1. Build
docker build -t azure-hacker-worker .

# 2. Create a config file (see examples below)

# 3. Run
docker run --rm \
  -v ${PWD}/output:/output \
  -v ${PWD}/job_config.json:/config.json \
  azure-hacker-worker --config /config.json


How it works

text
text
+-------------+     +--------------+     +-------------+
|  job_config  |---->|  CLI entry   |---->|  JobRunner  |
|  .json       |     |  point       |     |  (loop)     |
+-------------+     +--------------+     +------+------+
                                                |
                    +---------------------------+
                    |                           |
              +-----v-----+              +------v------+
              | Extractor  |              | HttpService |
              | (pure logic|              | (transport) |
              |  decides   |              |  fetches,   |
              |  next URL) |              |  retries)   |
              +-----+------+              +------+------+
                    |                           |
                    |    +-------------------+   |
                    +--->| StorageService   |<---+
                         | (CSV + status)   |
                         +-------------------+

1.CLI loads the JSON config, validates it, passes it to JobRunner.
2.JobRunner runs a loop: asks the extractor for the next request,
sends it via HttpService, writes results via StorageService.
3.Extractors are pure logic — they compute URLs and params, nothing else.
4.HttpService handles the network: rate limiting, retries, SSRF checks,
redirect following, response parsing.
5.StorageService writes CSV and status files atomically (temp file +
rename, so a crash mid-write never corrupts output).


Three modes

Paged

Scrapes pages by number. The URL template must contain {page}.



```json
{
  "job_id": "books-scrape",
  "mode": "paged",
  "target_url": "https://books.toscrape.com/catalogue/page-{page}.html",
  "output_dir": "./output",
  "max_requests": 5000,
  "max_runtime_minutes": 60,
  "concurrency": 8,
  "preview_rows": 50,
  "max_bytes": 52428800,
  "max_response_size": 1048576,
  "requests_per_second_limit": 10,
  "max_retries_per_cycle": 3,
  "per_host_concurrency": 2,
  "page_start": 1,
  "page_end": 50,
  "max_cost_usd": 3.0
}
```

How it works:

Cycle 1 fetches page-1.html
Cycle 2 fetches page-2.html
Stops when page_end is reached or budget is exhausted.

Cursor

Handles APIs that return a "next page" token in the response body.

```json
{
  "job_id": "cursor-scrape",
  "mode": "cursor",
  "target_url": "https://api.example.com/data",
  "output_dir": "./output",
  "max_requests": 5000,
  "max_runtime_minutes": 120,
  "concurrency": 8,
  "preview_rows": 20,
  "max_bytes": 52428800,
  "max_response_size": 1048576,
  "requests_per_second_limit": 10,
  "max_retries_per_cycle": 3,
  "cursor_param": "cursor",
  "next_cursor_key": "next_cursor",
  "max_cost_usd": 3.0
}
```

How it works:

Cycle 1 fetches with no cursor.
Extracts next_cursor from the response.
Cycle 2 fetches with ?cursor=token.
Stops when the API returns null for the cursor key.

API loop

Iterates over a fixed list of query parameter sets.

```json
{
  "job_id": "search-scrape",
  "mode": "api_loop",
  "target_url": "https://api.example.com/search",
  "output_dir": "./output",
  "max_requests": 5000,
  "max_runtime_minutes": 120,
  "concurrency": 8,
  "preview_rows": 20,
  "max_bytes": 52428800,
  "max_response_size": 1048576,
  "requests_per_second_limit": 10,
  "max_retries_per_cycle": 3,
  "query_variations": [
    {"q": "python", "limit": 100},
    {"q": "javascript", "limit": 100},
    {"q": "java", "limit": 100}
  ],
  "max_iterations": 1000,
  "max_cost_usd": 3.0
}
```

How it works:

Cycle 1 sends ?q=python&limit=100
Cycle 2 sends ?q=javascript&limit=100
Cycle 3 sends ?q=java&limit=100
Stops when all variations are exhausted.

Config reference

Required fields

Field Type Description
job_id string Unique identifier for this job
mode string "paged", "cursor", or "api_loop"
target_url string URL to scrape (use {page} in paged mode)
output_dir string Directory for output files
max_requests int Hard cap on total HTTP requests (1-10 000)
max_runtime_minutes int Hard cap on job runtime
concurrency int Max simultaneous connections
preview_rows int Number of rows in the preview CSV
max_bytes int Hard cap on total response bytes (1-100 MB)
max_response_size int Hard cap on a single response (1-10 MB)
requests_per_second_limit float Rate limit (0.1-50)
max_retries_per_cycle int Retry attempts per failed request (0-10)

Optional fields

Field Type Default Description
per_host_concurrency int 1 Max connections to a single host
allowlist list null URL substrings that must be present
denylist list null URL substrings that trigger rejection
max_cost_usd float 3.0 Estimated cloud cost ceiling
dry_run bool false Validate config and exit without fetching
ssrf_check bool true Block requests to private IPs

Mode-specific fields

Paged mode:

Field Type Description
page_start int First page number
page_end int Last page number (max range: 1000)

Cursor mode:

Field Type Description
cursor_param string Query parameter name for the cursor token
next_cursor_key string JSON key containing the next cursor
initial_cursor string Optional starting cursor value

API loop mode:

Field Type Description
query_variations list List of dicts, one per request
max_iterations int Hard cap on iterations (1-1000)

Output files

All files are written to output_dir:

File Description
full.csv Complete dataset — append-only, one row per record
preview.csv First N rows (from preview_rows) — generated at the end
status.json Job state, progress, stats — updated every 10 requests
schema.json Column registry — grows as new fields appear

Status JSON example

json
json
{
  "job_id": "books-scrape",
  "mode": "paged",
  "state": "completed",
  "started_at": "2025-04-11T12:00:00Z",
  "updated_at": "2025-04-11T12:05:30Z",
  "progress": 1.0,
  "records_collected": 1000,
  "max_requests": 5000,
  "iteration": 50,
  "request_count": 50,
  "failure_count": 0,
  "retry_count": 0,
  "message": "Completed. 1000 records, 50 requests, 330.0s",
  "total_bytes": 2048000,
  "total_latency_ms": 15000.0,
  "requests_per_second": 0.1515,
  "records_per_second": 3.0303,
  "avg_latency_ms": 300.0,
  "is_preview_available": true,
  "is_download_ready": true,
  "estimated_cost_usd": 0.0002,
  "per_host_stats": {
    "books.toscrape.com": {
      "requests": 50,
      "failures": 0,
      "avg_latency_ms": 300.0,
      "total_bytes": 2048000
    }
  }
}

Safety features

Feature What it does
Budget enforcement Stops before every request if time, bytes, requests, or cost limits are hit
SSRF protection Resolves DNS and blocks private/loopback IPs
Rate limiting Token-bucket limiter — continuous refill, no bursts beyond the limit
Retry with backoff Exponential backoff (1s, 2s, 4s...) with jitter for 429/5xx/timeout
Atomic writes All files written via temp file + rename — crash-safe
Redirect validation Every redirect hop is SSRF-checked
Response size cap Rejects responses larger than max_response_size
Empty cycle detection Stops after 3 consecutive empty responses
Graceful shutdown SIGTERM/SIGINT triggers clean stop and final status write

Architecture

text
text
worker/
+-- worker.py                   Logging setup + public re-exports
+-- exceptions.py               Shared exception hierarchy
+-- adapters/
|   +-- http_service.py         HTTP transport (httpx, retries, SSRF)
|   +-- network_policy.py       URL validation, DNS checks, allow/deny
|   +-- storage_service.py      CSV writer, schema registry, status
+-- domain/
|   +-- extractors.py           Pure logic: compute next request
|   +-- model.py                JobState, CycleResult dataclasses
|   +-- utils.py                iso_now(), safe_unlink()
+-- engine/
|   +-- budget.py               Time/bytes/requests/cost gate
|   +-- host_pool.py            Per-host semaphores + LRU eviction
|   +-- metrics.py              Per-host latency/failure tracking
|   +-- rate_limiter.py         Token-bucket rate limiter
|   +-- runner.py               Orchestrator: wiring + main loop
+-- platform/
    +-- cli.py                  Argument parsing, entry point
    +-- config.py               Config validation + URL sanitization

Dependency rule: every arrow points inward.

text
text
platform -> engine -> adapters -> domain -> exceptions

No circular imports. Domain has zero external dependencies.

Running locally (without Docker)

bash
bash
pip install httpx[http2]

python -m worker.platform.cli --config job_config.json

Running in Docker

bash
bash
docker build -t azure-hacker-worker .

docker run --rm \
  -v ${PWD}/output:/output \
  -v ${PWD}/job_config.json:/config.json \
  azure-hacker-worker --config /config.json

Programmatic usage

python
python
import asyncio
from worker import JobRunner

config = {
    "job_id": "test-001",
    "mode": "paged",
    "target_url": "<https://books.toscrape.com/catalogue/page-{page}.html>",
    "output_dir": "./output",
    "max_requests": 100,
    "max_runtime_minutes": 5,
    "concurrency": 4,
    "preview_rows": 10,
    "max_bytes": 10_485_760,
    "max_response_size": 1_048_576,
    "requests_per_second_limit": 5,
    "max_retries_per_cycle": 2,
    "page_start": 1,
    "page_end": 10,
}

runner = JobRunner(config)
asyncio.run(runner.run())

Next steps

Azure Blob Storage integration for cloud output
Azure Container Instances deployment
Webhook notifications on job completion
