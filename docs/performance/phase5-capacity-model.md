# Phase 5 #t55 Capacity Model And SLO Baseline

This document defines the first reproducible capacity-model checkpoint for Phase 5 #t55. It is not a live load-test result; it is the acceptance contract that future k6/Locust or environment-specific load runs must feed.

## Scope

- Target surface: authenticated JanusGate core API traffic behind one backend deployment.
- Current smoke script: `scripts/phase5-capacity-model-smoke.sh`.
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

## Next Slice

Replace the default inputs with a real repeatable load-test artifact from a target environment. That follow-up should record endpoint mix, dataset size, pod count, database shape, Redis mode, and the raw tool output path before treating the numbers as production evidence.
