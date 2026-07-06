#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ci_file="$repo_root/.github/workflows/ci.yml"

require_ci_text() {
  local needle="$1"
  local description="$2"

  if ! grep -Fq "$needle" "$ci_file"; then
    printf 'missing %s in %s\n' "$description" "$ci_file" >&2
    exit 1
  fi
}

require_ci_text "id-token: write" "OIDC permission for keyless image signing"
require_ci_text "aquasecurity/trivy-action" "Trivy high/critical vulnerability gate"
require_ci_text "anchore/sbom-action" "release image SBOM generation"
require_ci_text "sigstore/cosign-installer" "Cosign installer for release image signing"
require_ci_text "cosign sign --yes" "non-interactive release image signing command"
require_ci_text "steps.build.outputs.digest" "digest-pinned SBOM/signing target"

printf 'Phase 5 supply-chain security CI smoke passed\n'
