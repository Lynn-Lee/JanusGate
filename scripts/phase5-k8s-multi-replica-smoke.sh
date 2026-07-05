#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

command -v helm >/dev/null 2>&1 || {
  printf 'helm is required for Phase 5 Kubernetes multi-replica smoke\n' >&2
  exit 1
}
command -v kubectl >/dev/null 2>&1 || {
  printf 'kubectl is required for Phase 5 Kubernetes multi-replica smoke\n' >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  printf 'curl is required for Phase 5 Kubernetes multi-replica smoke\n' >&2
  exit 1
}

: "${SECRET_KEY:?SECRET_KEY is required and must not be printed}"
: "${DATABASE_URL:?DATABASE_URL is required and must point to a real PostgreSQL writer}"
: "${DATABASE_READ_REPLICA_URL:?DATABASE_READ_REPLICA_URL is required for read-replica smoke}"
: "${REDIS_URL:?REDIS_URL is required for Redis-backed connection token store}"

namespace="${JANUSGATE_SMOKE_NAMESPACE:-janusgate-phase5-smoke}"
release="${JANUSGATE_SMOKE_RELEASE:-janusgate-smoke}"
image_repository="${JANUSGATE_IMAGE_REPOSITORY:-ghcr.io/lynn-lee/janusgate-backend}"
image_tag="${JANUSGATE_IMAGE_TAG:-0.1.0}"
local_port="${JANUSGATE_SMOKE_LOCAL_PORT:-18080}"
timeout="${JANUSGATE_SMOKE_TIMEOUT:-180s}"
created_namespace="0"
values_file="$(mktemp)"
port_forward_pid=""

cleanup() {
  if [ -n "$port_forward_pid" ] && kill -0 "$port_forward_pid" >/dev/null 2>&1; then
    kill "$port_forward_pid" >/dev/null 2>&1 || true
    wait "$port_forward_pid" >/dev/null 2>&1 || true
  fi
  helm uninstall "$release" -n "$namespace" >/dev/null 2>&1 || true
  if [ "$created_namespace" = "1" ]; then
    kubectl delete namespace "$namespace" --wait=false >/dev/null 2>&1 || true
  fi
  rm -f "$values_file"
}
trap cleanup EXIT

if ! kubectl auth can-i create namespace >/dev/null 2>&1; then
  printf 'kubectl cannot create namespaces in the current context\n' >&2
  exit 1
fi

if ! kubectl get namespace "$namespace" >/dev/null 2>&1; then
  kubectl create namespace "$namespace" >/dev/null
  created_namespace="1"
fi

chmod 600 "$values_file"
cat >"$values_file" <<EOF
image:
  repository: "$image_repository"
  tag: "$image_tag"
secret:
  secretKey: "$SECRET_KEY"
  databaseUrl: "$DATABASE_URL"
  databaseReadReplicaUrl: "$DATABASE_READ_REPLICA_URL"
config:
  sessionConnectionTokenStore: redis
  redisUrl: "$REDIS_URL"
  redisMode: "${REDIS_MODE:-single}"
  redisSentinelUrls: "${REDIS_SENTINEL_URLS:-}"
  redisSentinelMasterName: "${REDIS_SENTINEL_MASTER_NAME:-mymaster}"
  redisClusterUrls: "${REDIS_CLUSTER_URLS:-}"
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 2
EOF

helm upgrade --install "$release" deploy/helm/janusgate \
  --namespace "$namespace" \
  --values "$values_file" \
  --wait \
  --timeout "$timeout"

deployment="$release"
service="$release"

kubectl rollout status deployment/"$deployment" -n "$namespace" --timeout "$timeout"
kubectl wait --for=condition=ready pod \
  -n "$namespace" \
  -l "app.kubernetes.io/instance=$release" \
  --timeout "$timeout"

pod_names="$(kubectl get pods -n "$namespace" \
  -l "app.kubernetes.io/instance=$release" \
  -o jsonpath='{.items[*].metadata.name}')"
ready_pod_count="$(printf '%s\n' "$pod_names" | wc -w | tr -d ' ')"
if [ "$ready_pod_count" -lt 2 ]; then
  printf 'expected at least 2 JanusGate pods, got %s\n' "$ready_pod_count" >&2
  exit 1
fi

kubectl port-forward svc/"$service" "${local_port}:8000" -n "$namespace" >/tmp/janusgate-phase5-k8s-smoke-port-forward.log 2>&1 &
port_forward_pid="$!"
sleep 3

curl -fsS "http://127.0.0.1:${local_port}/health" >/dev/null

printf 'Phase 5 Kubernetes multi-replica smoke passed: rollout, ready pods, and /health verified.\n'
