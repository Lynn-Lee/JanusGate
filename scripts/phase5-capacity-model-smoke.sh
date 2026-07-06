#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import os
import sys


def read_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError:
        print(f"{name} must be numeric, got {raw!r}", file=sys.stderr)
        sys.exit(1)
    if value < 0:
        print(f"{name} must be non-negative, got {raw!r}", file=sys.stderr)
        sys.exit(1)
    return value


observed_rps = read_float("JANUSGATE_LOAD_TEST_RPS", "120")
observed_p95_ms = read_float("JANUSGATE_LOAD_TEST_P95_MS", "280")
observed_error_rate = read_float("JANUSGATE_LOAD_TEST_ERROR_RATE_PERCENT", "0.2")
max_core_rps = read_float("JANUSGATE_CAPACITY_MODEL_MAX_CORE_RPS", "200")
slo_p95_ms = read_float("JANUSGATE_SLO_P95_MS", "500")
slo_error_rate = read_float("JANUSGATE_SLO_ERROR_RATE_PERCENT", "1")
min_headroom = read_float("JANUSGATE_MIN_CAPACITY_HEADROOM_PERCENT", "30")

if observed_rps <= 0:
    print("JANUSGATE_LOAD_TEST_RPS must be greater than zero", file=sys.stderr)
    sys.exit(1)

capacity_headroom_percent = ((max_core_rps - observed_rps) / observed_rps) * 100

failures: list[str] = []
if observed_p95_ms > slo_p95_ms:
    failures.append(
        f"p95 {observed_p95_ms:.1f}ms exceeds SLO {slo_p95_ms:.1f}ms"
    )
if observed_error_rate > slo_error_rate:
    failures.append(
        f"error rate {observed_error_rate:.2f}% exceeds SLO {slo_error_rate:.2f}%"
    )
if capacity_headroom_percent < min_headroom:
    failures.append(
        "capacity_headroom_percent "
        f"{capacity_headroom_percent:.1f}% is below required {min_headroom:.1f}%"
    )

print("Phase 5 #t55 capacity model smoke")
print(f"observed_rps={observed_rps:.1f}")
print(f"observed_p95_ms={observed_p95_ms:.1f}")
print(f"observed_error_rate_percent={observed_error_rate:.2f}")
print(f"modeled_max_core_rps={max_core_rps:.1f}")
print(f"capacity_headroom_percent={capacity_headroom_percent:.1f}")

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    sys.exit(1)
PY
