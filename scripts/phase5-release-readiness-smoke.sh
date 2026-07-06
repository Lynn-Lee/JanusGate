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

require_equal() {
  local left="$1"
  local right="$2"
  local description="$3"

  if [[ "$left" != "$right" ]]; then
    printf '%s mismatch: %s != %s\n' "$description" "$left" "$right" >&2
    exit 1
  fi
}

backend_pyproject="$repo_root/backend/pyproject.toml"
backend_app="$repo_root/backend/app/main.py"
helm_chart="$repo_root/deploy/helm/janusgate/Chart.yaml"
helm_values="$repo_root/deploy/helm/janusgate/values.yaml"
ci_file="$repo_root/.github/workflows/ci.yml"
deploy_readme="$repo_root/deploy/README.md"

versions="$(
  python3 - "$backend_pyproject" "$backend_app" "$helm_chart" "$helm_values" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

backend_pyproject, backend_app, helm_chart, helm_values = map(Path, sys.argv[1:])
backend_version = tomllib.loads(backend_pyproject.read_text())["project"]["version"]
app_text = backend_app.read_text()
fastapi_version = re.search(r'version="([^"]+)"', app_text).group(1)
health_version = re.search(r'"version": "([^"]+)"', app_text).group(1)
chart_text = helm_chart.read_text()
values_text = helm_values.read_text()
chart_version = re.search(r"^version: \"?([^\n\"]+)\"?", chart_text, re.MULTILINE).group(1)
chart_app_version = re.search(r"^appVersion: \"?([^\n\"]+)\"?", chart_text, re.MULTILINE).group(1)
image_tag = re.search(r"^  tag: \"?([^\n\"]+)\"?", values_text, re.MULTILINE).group(1)
print("\n".join([backend_version, fastapi_version, health_version, chart_version, chart_app_version, image_tag]))
PY
)"
backend_version="$(printf '%s\n' "$versions" | sed -n '1p')"
fastapi_version="$(printf '%s\n' "$versions" | sed -n '2p')"
health_version="$(printf '%s\n' "$versions" | sed -n '3p')"
chart_version="$(printf '%s\n' "$versions" | sed -n '4p')"
chart_app_version="$(printf '%s\n' "$versions" | sed -n '5p')"
image_tag="$(printf '%s\n' "$versions" | sed -n '6p')"

for version in "$backend_version" "$fastapi_version" "$health_version" "$chart_version" "$chart_app_version" "$image_tag"; do
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'version must use semantic version MAJOR.MINOR.PATCH, got %s\n' "$version" >&2
    exit 1
  fi
done

require_equal "$backend_version" "$fastapi_version" "backend pyproject and FastAPI app version"
require_equal "$backend_version" "$health_version" "backend pyproject and health endpoint version"
require_equal "$backend_version" "$chart_app_version" "backend pyproject and Helm appVersion"
require_equal "$backend_version" "$image_tag" "backend pyproject and Helm image tag"
require_equal "$chart_version" "$chart_app_version" "Helm chart version and appVersion"

require_file_text "$ci_file" 'tags: ["v*"]' "release tag trigger"
require_file_text "$ci_file" "type=semver,pattern={{version}}" "release tag semantic Docker metadata"
require_file_text "$ci_file" "startsWith(github.ref, 'refs/tags/v')" "release tag guarded image publish"
require_file_text "$ci_file" "scripts/phase5-release-readiness-smoke.sh" "release readiness CI gate"

require_file_text "$deploy_readme" "版本发布与回滚 runbook" "release and rollback runbook"
require_file_text "$deploy_readme" "迁移前备份" "migration backup requirement"
require_file_text "$deploy_readme" "alembic" "alembic migration checkpoint"
require_file_text "$deploy_readme" "helm rollback" "Helm rollback command"

printf 'Phase 5 #t57 release readiness smoke passed\n'
