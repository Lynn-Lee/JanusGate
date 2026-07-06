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
mkdir -p "$output_abs/assets"
cp -R "$repo_root/docs/site/assets/screenshots" "$output_abs/assets/screenshots"
mkdir -p "$output_abs/fixtures"
cp "$repo_root/docs/site/fixtures/admin-screenshot-data.json" "$output_abs/fixtures/admin-screenshot-data.json"

cat > "$output_abs/manifest.json" <<'JSON'
{
  "name": "JanusGate docs-site",
  "format": "markdown-static-package",
  "entry": "index.md",
  "openapi": "openapi.json",
  "assets": [
    "assets/screenshots/admin-settings-license-summary.svg",
    "assets/screenshots/admin-audits-soc2-export.svg",
    "assets/screenshots/admin-sessions-recording-timeline.svg"
  ],
  "fixtures": [
    "fixtures/admin-screenshot-data.json"
  ],
  "screenshotCapture": {
    "smoke": "scripts/phase5-docs-browser-screenshots-smoke.sh",
    "fixture": "docs/site/fixtures/admin-screenshot-data.json",
    "captureEnv": "JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1",
    "frontendBaseUrlEnv": "JANUSGATE_FRONTEND_BASE_URL"
  },
  "pages": [
    "index.md",
    "install.md",
    "admin.md",
    "admin-screenshots.md",
    "api.md",
    "runbooks.md"
  ]
}
JSON

printf 'Built docs-site package at %s\n' "$output_abs"
