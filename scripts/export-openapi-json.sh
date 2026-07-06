#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_path="${1:-docs/site/openapi.json}"

case "$output_path" in
  /*) output_abs="$output_path" ;;
  *) output_abs="$repo_root/$output_path" ;;
esac

mkdir -p "$(dirname "$output_abs")"

if [ -x "$repo_root/backend/.venv/bin/python" ]; then
  python_bin="$repo_root/backend/.venv/bin/python"
else
  python_bin="${PYTHON:-python3}"
fi

(
  cd "$repo_root/backend"
  SECRET_KEY="${SECRET_KEY:-docs-export-secret-key-docs-export-secret-key-32}" \
    "$python_bin" - "$output_abs" <<'PY'
import json
import sys
from pathlib import Path

from app.main import app

output = Path(sys.argv[1])
output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
PY
)

printf 'Exported OpenAPI schema to %s\n' "$output_abs"
