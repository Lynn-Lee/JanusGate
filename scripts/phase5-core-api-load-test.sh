#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${JANUSGATE_LOAD_TEST_BASE_URL:-}" ]]; then
  printf 'JANUSGATE_LOAD_TEST_BASE_URL is required.\n' >&2
  exit 1
fi
if [[ -z "${JANUSGATE_LOAD_TEST_ACCESS_TOKEN:-}" ]]; then
  printf 'JANUSGATE_LOAD_TEST_ACCESS_TOKEN is required.\n' >&2
  exit 1
fi

duration_seconds="${JANUSGATE_LOAD_TEST_DURATION_SECONDS:-60}"
concurrency="${JANUSGATE_LOAD_TEST_CONCURRENCY:-4}"
request_timeout_seconds="${JANUSGATE_LOAD_TEST_REQUEST_TIMEOUT_SECONDS:-5}"
summary_file="$(mktemp)"
trap 'rm -f "$summary_file"' EXIT

auth_header="Authorization: Bearer ${JANUSGATE_LOAD_TEST_ACCESS_TOKEN}"

JANUSGATE_LOAD_TEST_SUMMARY_FILE="$summary_file" \
JANUSGATE_LOAD_TEST_AUTH_HEADER="$auth_header" \
JANUSGATE_LOAD_TEST_DURATION_SECONDS="$duration_seconds" \
JANUSGATE_LOAD_TEST_CONCURRENCY="$concurrency" \
JANUSGATE_LOAD_TEST_REQUEST_TIMEOUT_SECONDS="$request_timeout_seconds" \
python3 - <<'PY'
from __future__ import annotations

import concurrent.futures
import math
import os
import time
import urllib.error
import urllib.request


ENDPOINT_MIX = (
    ("GET /api/v1/auth/me", "/api/v1/auth/me", 3),
    ("GET /api/v1/assets/", "/api/v1/assets/", 4),
    ("GET /api/v1/sessions/", "/api/v1/sessions/", 2),
    ("GET /api/v1/automation/jobs/runs", "/api/v1/automation/jobs/runs", 1),
)


def read_positive_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be greater than zero, got {raw!r}")
    return value


base_url = os.environ["JANUSGATE_LOAD_TEST_BASE_URL"].rstrip("/")
auth_header = os.environ["JANUSGATE_LOAD_TEST_AUTH_HEADER"]
duration_seconds = read_positive_int("JANUSGATE_LOAD_TEST_DURATION_SECONDS", "60")
concurrency = read_positive_int("JANUSGATE_LOAD_TEST_CONCURRENCY", "4")
request_timeout = read_positive_int("JANUSGATE_LOAD_TEST_REQUEST_TIMEOUT_SECONDS", "5")
summary_file = os.environ["JANUSGATE_LOAD_TEST_SUMMARY_FILE"]
weighted_paths = [
    (label, path)
    for label, path, weight in ENDPOINT_MIX
    for _ in range(weight)
]


def request_once(path: str) -> tuple[float, bool]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        headers={
            "Authorization": auth_header.removeprefix("Authorization: "),
            "Accept": "application/json",
        },
        method="GET",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            failed = response.status >= 400
            response.read(512)
    except (urllib.error.URLError, TimeoutError):
        failed = True
    return (time.perf_counter() - started) * 1000, failed


def worker(worker_index: int, ends_at: float) -> list[tuple[float, bool]]:
    results: list[tuple[float, bool]] = []
    cursor = worker_index
    while time.monotonic() < ends_at:
        _label, path = weighted_paths[cursor % len(weighted_paths)]
        cursor += 1
        results.append(request_once(path))
    return results


started_at = time.monotonic()
ends_at = started_at + duration_seconds
with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
    futures = [executor.submit(worker, index, ends_at) for index in range(concurrency)]
    results = [item for future in futures for item in future.result()]
elapsed = max(time.monotonic() - started_at, 0.001)

if not results:
    raise SystemExit("load test produced no requests")

latencies = sorted(latency for latency, _failed in results)
p95_index = min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1))
p95_ms = latencies[p95_index]
failures = sum(1 for _latency, failed in results if failed)
error_rate = (failures / len(results)) * 100
rps = len(results) / elapsed

with open(summary_file, "w", encoding="utf-8") as handle:
    handle.write(f"JANUSGATE_LOAD_TEST_RPS={rps:.2f}\n")
    handle.write(f"JANUSGATE_LOAD_TEST_P95_MS={p95_ms:.2f}\n")
    handle.write(f"JANUSGATE_LOAD_TEST_ERROR_RATE_PERCENT={error_rate:.2f}\n")

print("Phase 5 #t55 core API load-test summary")
print(f"duration_seconds={duration_seconds}")
print(f"concurrency={concurrency}")
print("endpoint_mix=" + ",".join(label for label, _path, _weight in ENDPOINT_MIX))
print(f"requests={len(results)}")
print(f"rps={rps:.2f}")
print(f"p95_ms={p95_ms:.2f}")
print(f"error_rate_percent={error_rate:.2f}")
PY

set -a
# shellcheck disable=SC1090
source "$summary_file"
set +a

"$repo_root/scripts/phase5-capacity-model-smoke.sh"
