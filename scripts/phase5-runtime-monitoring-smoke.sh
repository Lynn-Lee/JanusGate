#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_file_text() {
  local file="$1"
  local needle="$2"
  local description="$3"

  if ! grep -Fq "$needle" "$file"; then
    printf 'missing %s in %s\n' "$description" "$file" >&2
    exit 1
  fi
}

compose_file="$repo_root/docker-compose.yml"
helm_values="$repo_root/deploy/helm/janusgate/values.yaml"
helm_deployment="$repo_root/deploy/helm/janusgate/templates/deployment.yaml"
metrics_test="$repo_root/backend/tests/test_observability_metrics.py"
alert_rules="$repo_root/deploy/monitoring/phase5-runtime-alerts.yaml"

require_file_text "$compose_file" "read_only: true" "Compose read-only backend filesystem"
require_file_text "$compose_file" "no-new-privileges:true" "Compose no-new-privileges runtime guard"
require_file_text "$compose_file" "tmpfs:" "Compose writable tmpfs for read-only runtime"

require_file_text "$helm_values" "runAsNonRoot: true" "Helm non-root pod security context"
require_file_text "$helm_values" "allowPrivilegeEscalation: false" "Helm privilege escalation guard"
require_file_text "$helm_values" "readOnlyRootFilesystem: true" "Helm read-only root filesystem"
require_file_text "$helm_values" "drop:
      - ALL" "Helm dropped Linux capabilities"
require_file_text "$helm_deployment" "mountPath: /tmp" "Helm writable /tmp volume mount"

require_file_text "$metrics_test" 'client.get("/metrics")' "GET /metrics runtime metrics endpoint regression"
require_file_text "$metrics_test" "janusgate_http_requests_total" "Prometheus request counter regression"
require_file_text "$metrics_test" "janusgate_http_request_duration_seconds" "Prometheus latency histogram regression"

require_file_text "$alert_rules" "JanusGateRuntimeHigh5xxRate" "runtime high 5xx alert rule"
require_file_text "$alert_rules" "JanusGateRuntimeHighP95Latency" "runtime p95 latency alert rule"
require_file_text "$alert_rules" "JanusGateRuntimeMetricsEndpointMissing" "runtime metrics missing alert rule"
require_file_text "$alert_rules" "janusgate_http_requests_total" "runtime alert request metric"
require_file_text "$alert_rules" "janusgate_http_request_duration_seconds_bucket" "runtime alert latency metric"

printf 'Phase 5 runtime monitoring smoke passed\n'
