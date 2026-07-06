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

cat > "$output_abs/manifest.json" <<'JSON'
{
  "name": "JanusGate docs-site",
  "format": "markdown-static-package",
  "entry": "index.md",
  "openapi": "openapi.json",
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
