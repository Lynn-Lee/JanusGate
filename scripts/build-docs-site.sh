#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:-dist/docs-site}"

case "$output_dir" in
  /*) output_abs="$output_dir" ;;
  *) output_abs="$repo_root/$output_dir" ;;
esac

mkdir -p "$output_abs"

"$repo_root/scripts/export-openapi-json.sh" "$output_abs/openapi.json"

cp "$repo_root/docs/site/index.md" "$output_abs/index.md"
cp "$repo_root/docs/site/install.md" "$output_abs/install.md"
cp "$repo_root/docs/site/admin.md" "$output_abs/admin.md"
cp "$repo_root/docs/site/admin-screenshots.md" "$output_abs/admin-screenshots.md"
cp "$repo_root/docs/site/api.md" "$output_abs/api.md"
cp "$repo_root/docs/site/runbooks.md" "$output_abs/runbooks.md"
cp "$repo_root/docs/site/connectors-ssh.md" "$output_abs/connectors-ssh.md"
cp "$repo_root/docs/site/connectors-k8s.md" "$output_abs/connectors-k8s.md"
cp "$repo_root/docs/site/acl-command-filter.md" "$output_abs/acl-command-filter.md"
cp "$repo_root/docs/site/acl-data-masking.md" "$output_abs/acl-data-masking.md"
cp "$repo_root/docs/site/asset-tree-authorization.md" "$output_abs/asset-tree-authorization.md"
cp "$repo_root/docs/site/account-automation.md" "$output_abs/account-automation.md"
mkdir -p "$output_abs/assets"
cp -R "$repo_root/docs/site/assets/screenshots" "$output_abs/assets/screenshots"
mkdir -p "$output_abs/fixtures"
cp "$repo_root/docs/site/fixtures/admin-screenshot-data.json" "$output_abs/fixtures/admin-screenshot-data.json"
cp "$repo_root/docs/site/fixtures/admin-screenshot-archive.json" "$output_abs/fixtures/admin-screenshot-archive.json"
cp "$repo_root/docs/site/fixtures/operation-runbook-evidence.json" "$output_abs/fixtures/operation-runbook-evidence.json"
cp "$repo_root/docs/site/fixtures/license-operations-evidence.json" "$output_abs/fixtures/license-operations-evidence.json"
cp "$repo_root/docs/site/fixtures/runtime-alert-evidence.json" "$output_abs/fixtures/runtime-alert-evidence.json"

cat > "$output_abs/manifest.json" <<'JSON'
{
  "name": "JanusGate docs-site",
  "format": "markdown-static-package",
  "entry": "index.md",
  "openapi": "openapi.json",
  "assets": [
    "assets/screenshots/admin-settings-license-summary.svg",
    "assets/screenshots/admin-audits-soc2-export.svg",
    "assets/screenshots/admin-sessions-recording-timeline.svg",
    "assets/screenshots/admin-tenancy-organization-inventory.svg",
    "assets/screenshots/admin-accounts-credential-rotation.svg",
    "assets/screenshots/admin-ssh-ca-trust-bundle.svg",
    "assets/screenshots/live-screenshots/admin-settings-license-summary.png",
    "assets/screenshots/live-screenshots/admin-audits-soc2-export.png",
    "assets/screenshots/live-screenshots/admin-sessions-recording-timeline.png",
    "assets/screenshots/live-screenshots/admin-tenancy-organization-inventory.png",
    "assets/screenshots/live-screenshots/admin-accounts-credential-rotation.png",
    "assets/screenshots/live-screenshots/admin-ssh-ca-trust-bundle.png"
  ],
  "fixtures": [
    "fixtures/admin-screenshot-data.json",
    "fixtures/admin-screenshot-archive.json",
    "fixtures/operation-runbook-evidence.json",
    "fixtures/license-operations-evidence.json",
    "fixtures/runtime-alert-evidence.json"
  ],
  "operationRunbookEvidence": {
    "source": "docs/site/runbooks.md",
    "manifest": "docs/site/fixtures/operation-runbook-evidence.json",
    "packagePath": "fixtures/operation-runbook-evidence.json",
    "schemaVersion": "janusgate.docs.operation-runbook-evidence.v1"
  },
  "licenseOperationsEvidence": {
    "source": "docs/site/runbooks.md",
    "manifest": "docs/site/fixtures/license-operations-evidence.json",
    "packagePath": "fixtures/license-operations-evidence.json",
    "schemaVersion": "janusgate.docs.license-operations-evidence.v1"
  },
  "runtimeAlertEvidence": {
    "source": "docs/site/runbooks.md",
    "manifest": "docs/site/fixtures/runtime-alert-evidence.json",
    "packagePath": "fixtures/runtime-alert-evidence.json",
    "schemaVersion": "janusgate.docs.runtime-alert-evidence.v1"
  },
  "screenshotCapture": {
    "smoke": "scripts/phase5-docs-browser-screenshots-smoke.sh",
    "fixture": "docs/site/fixtures/admin-screenshot-data.json",
    "archive": "docs/site/fixtures/admin-screenshot-archive.json",
    "liveOutputDirectory": "docs/site/assets/screenshots/live-screenshots",
    "liveArtifactFormat": "png",
    "captureEnv": "JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1",
    "frontendBaseUrlEnv": "JANUSGATE_FRONTEND_BASE_URL"
  },
  "pages": [
    "index.md",
    "install.md",
    "admin.md",
    "admin-screenshots.md",
    "api.md",
    "runbooks.md",
    "connectors-ssh.md",
    "connectors-k8s.md",
    "acl-command-filter.md",
    "acl-data-masking.md",
    "asset-tree-authorization.md",
    "account-automation.md"
  ]
}
JSON

printf 'Built docs-site package at %s\n' "$output_abs"
