# Phase 5 #t55 Capacity Model And SLO Baseline

This document defines the first reproducible capacity-model checkpoint for Phase 5 #t55. It is not a live load-test result; it is the acceptance contract that future k6/Locust or environment-specific load runs must feed.

## Scope

- Target surface: authenticated JanusGate core API traffic behind one backend deployment.
- Current smoke script: `scripts/phase5-capacity-model-smoke.sh`.
- Repeatable load runner: `scripts/phase5-core-api-load-test.sh`.
- Evidence archive contract: `docs/performance/phase5-load-test-evidence-template.json`, validated by `scripts/phase5-load-test-evidence-smoke.sh`.
- Current CI purpose: validate that load-test input, SLO thresholds, and capacity headroom math stay explicit and reproducible.

## SLO

| Metric | Baseline |
| --- | --- |
| Core API p95 latency | `JANUSGATE_SLO_P95_MS=500` |
| Core API error rate | `JANUSGATE_SLO_ERROR_RATE_PERCENT=1` |
| Minimum capacity headroom | `JANUSGATE_MIN_CAPACITY_HEADROOM_PERCENT=30` |

## Capacity Model Inputs

The smoke script accepts deterministic inputs from a real load-test summary:

| Input | Meaning | Default |
| --- | --- | --- |
| `JANUSGATE_LOAD_TEST_RPS` | Observed sustained request rate | `120` |
| `JANUSGATE_LOAD_TEST_P95_MS` | Observed p95 latency in milliseconds | `280` |
| `JANUSGATE_LOAD_TEST_ERROR_RATE_PERCENT` | Observed failed request percentage | `0.2` |
| `JANUSGATE_CAPACITY_MODEL_MAX_CORE_RPS` | Current modeled max core API RPS | `200` |

Derived metric:

```text
capacity_headroom_percent = ((JANUSGATE_CAPACITY_MODEL_MAX_CORE_RPS - JANUSGATE_LOAD_TEST_RPS) / JANUSGATE_LOAD_TEST_RPS) * 100
```

The smoke fails if observed p95 latency, error rate, or capacity headroom miss the SLO thresholds.

## Usage

```bash
scripts/phase5-capacity-model-smoke.sh
JANUSGATE_LOAD_TEST_RPS=170 JANUSGATE_LOAD_TEST_P95_MS=420 scripts/phase5-capacity-model-smoke.sh
```

## Endpoint Mix

`scripts/phase5-core-api-load-test.sh` runs a small authenticated GET mix against a real dev, staging, or production-like deployment, then passes the observed RPS, p95 latency, and error rate into `scripts/phase5-capacity-model-smoke.sh`.

| Endpoint | Weight | Purpose |
| --- | ---: | --- |
| `GET /api/v1/auth/me` | 3 | Authenticated user profile read path |
| `GET /api/v1/assets/` | 4 | Core inventory list path |
| `GET /api/v1/sessions/` | 2 | Active/session history list path |
| `GET /api/v1/automation/jobs/runs` | 1 | Automation run metadata list path |

Authorization token must come from the environment:

```bash
JANUSGATE_LOAD_TEST_BASE_URL=http://127.0.0.1:8000 \
JANUSGATE_LOAD_TEST_ACCESS_TOKEN=<bearer-token> \
JANUSGATE_LOAD_TEST_DURATION_SECONDS=60 \
JANUSGATE_LOAD_TEST_CONCURRENCY=4 \
scripts/phase5-core-api-load-test.sh
```

The runner does not print the token or request bodies. It only records aggregate request count, RPS, p95 latency, and error-rate values. The target environment, dataset size, pod count, database shape, Redis mode, raw terminal output, and any trace IDs still need to be archived with the release or performance evidence package before using the numbers as production evidence.

## Evidence Archive Contract

`docs/performance/phase5-load-test-evidence-template.json` defines the minimum archive shape for a real run: target environment metadata, run metadata, runner configuration, endpoint mix, aggregate results, capacity-model thresholds/result, artifact manifest, and redaction policy. The template intentionally keeps authentication material and request/response bodies out of the archive.

Validate the template or a real evidence file before attaching it to a release or performance package:

```bash
scripts/phase5-load-test-evidence-smoke.sh
JANUSGATE_LOAD_TEST_EVIDENCE_FILE=artifacts/phase5-load-test/evidence.json scripts/phase5-load-test-evidence-smoke.sh
```

The smoke rejects missing required fields and disallowed sensitive field names such as password, secret, token, private key, or connection string.

## Next Slice

Run the repeatable endpoint mix against a target environment, fill the evidence archive from the real output, and store the validated evidence with the release or performance package. A later slice can replace this standard-library runner with k6 or Locust when the team needs richer scenarios, ramping profiles, or distributed load generation.
