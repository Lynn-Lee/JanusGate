#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
evidence_file="${JANUSGATE_LOAD_TEST_EVIDENCE_FILE:-$repo_root/docs/performance/phase5-load-test-evidence-template.json}"

JANUSGATE_LOAD_TEST_EVIDENCE_FILE="$evidence_file" python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL_FIELDS = {
    "evidence_version",
    "target_environment",
    "run_metadata",
    "runner_configuration",
    "endpoint_mix",
    "aggregate_results",
    "capacity_model",
    "artifact_manifest",
    "redaction_policy",
}
REQUIRED_RESULT_FIELDS = {"requests", "rps", "p95_ms", "error_rate_percent"}
REQUIRED_CAPACITY_FIELDS = {
    "max_core_rps",
    "slo_p95_ms",
    "slo_error_rate_percent",
    "min_capacity_headroom_percent",
    "observed_capacity_headroom_percent",
    "result",
}
SENSITIVE_FIELD_FRAGMENTS = ("password", "secret", "token", "private_key", "connection_string")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def ensure_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{field} must be an object")
    return value


def scan_sensitive_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = key.lower()
            if any(fragment in lowered for fragment in SENSITIVE_FIELD_FRAGMENTS):
                fail(f"disallowed sensitive field at {path}.{key}")
            scan_sensitive_fields(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            scan_sensitive_fields(nested, f"{path}[{index}]")


evidence_path = Path(os.environ["JANUSGATE_LOAD_TEST_EVIDENCE_FILE"])
if not evidence_path.exists():
    fail(f"load-test evidence file not found: {evidence_path}")

try:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    fail(f"load-test evidence file is not valid JSON: {exc}")

if not isinstance(evidence, dict):
    fail("load-test evidence root must be an object")

missing = sorted(REQUIRED_TOP_LEVEL_FIELDS - evidence.keys())
if missing:
    fail("load-test evidence is missing fields: " + ", ".join(missing))

scan_sensitive_fields(evidence)

endpoint_mix = evidence["endpoint_mix"]
if not isinstance(endpoint_mix, list) or not endpoint_mix:
    fail("endpoint_mix must be a non-empty array")
for index, endpoint in enumerate(endpoint_mix):
    endpoint_map = ensure_mapping(endpoint, f"endpoint_mix[{index}]")
    for field in ("method", "path", "weight"):
        if field not in endpoint_map:
            fail(f"endpoint_mix[{index}] is missing {field}")

aggregate_results = ensure_mapping(evidence["aggregate_results"], "aggregate_results")
missing_results = sorted(REQUIRED_RESULT_FIELDS - aggregate_results.keys())
if missing_results:
    fail("aggregate_results is missing fields: " + ", ".join(missing_results))

capacity_model = ensure_mapping(evidence["capacity_model"], "capacity_model")
missing_capacity = sorted(REQUIRED_CAPACITY_FIELDS - capacity_model.keys())
if missing_capacity:
    fail("capacity_model is missing fields: " + ", ".join(missing_capacity))
if capacity_model["result"] not in {"pass", "fail"}:
    fail("capacity_model.result must be pass or fail")

artifact_manifest = evidence["artifact_manifest"]
if not isinstance(artifact_manifest, list) or not artifact_manifest:
    fail("artifact_manifest must be a non-empty array")
for index, artifact in enumerate(artifact_manifest):
    artifact_map = ensure_mapping(artifact, f"artifact_manifest[{index}]")
    if not artifact_map.get("path"):
        fail(f"artifact_manifest[{index}] must include path")

print("Phase 5 #t55 load-test evidence smoke")
print(f"evidence_file={evidence_path}")
print(f"endpoint_count={len(endpoint_mix)}")
print(f"artifact_count={len(artifact_manifest)}")
print(f"capacity_result={capacity_model['result']}")
PY
