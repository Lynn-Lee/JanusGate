#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
screenshot_dir="$repo_root/docs/site/assets/screenshots"
docs_package_dir="${JANUSGATE_DOCS_SITE_DIR:-$repo_root/dist/docs-site}"

required_screenshots=(
  "admin-settings-license-summary.svg"
  "admin-audits-soc2-export.svg"
  "admin-sessions-recording-timeline.svg"
)

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    printf 'Missing required file: %s\n' "$path" >&2
    exit 1
  fi
}

for screenshot in "${required_screenshots[@]}"; do
  require_file "$screenshot_dir/$screenshot"
  grep -q '<svg' "$screenshot_dir/$screenshot"
done

require_file "$repo_root/docs/site/admin-screenshots.md"
grep -q 'JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1' "$repo_root/docs/site/admin-screenshots.md"
grep -q 'scripts/phase5-docs-browser-screenshots-smoke.sh' "$repo_root/docs/site/admin-screenshots.md"

if [[ -f "$docs_package_dir/manifest.json" ]]; then
  grep -q '"screenshotCapture"' "$docs_package_dir/manifest.json"
  for screenshot in "${required_screenshots[@]}"; do
    grep -q "assets/screenshots/$screenshot" "$docs_package_dir/manifest.json"
  done
fi

if [[ "${JANUSGATE_CAPTURE_DOC_SCREENSHOTS:-0}" != "1" ]]; then
  printf 'Validated docs screenshot evidence contract. Set JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1 to capture live browser screenshots.\n'
  exit 0
fi

if [[ -z "${JANUSGATE_FRONTEND_BASE_URL:-}" ]]; then
  printf 'JANUSGATE_FRONTEND_BASE_URL is required when JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1.\n' >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  printf 'node is required for browser screenshot capture.\n' >&2
  exit 1
fi

if ! (cd "$repo_root/frontend" && node -e "require.resolve('playwright')" >/dev/null 2>&1); then
  printf 'Playwright is required for capture mode. Install it in frontend/ before setting JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1.\n' >&2
  exit 1
fi

capture_script="$(mktemp)"
trap 'rm -f "$capture_script"' EXIT
cat > "$capture_script" <<'JS'
const path = require('node:path');
const { chromium } = require('playwright');

const baseUrl = process.env.JANUSGATE_FRONTEND_BASE_URL.replace(/\/+$/, '');
const outputDir = process.env.JANUSGATE_DOC_SCREENSHOT_OUTPUT_DIR;
const accessToken = process.env.JANUSGATE_DOC_SCREENSHOT_ACCESS_TOKEN || 'docs-screenshot-token';

const shots = [
  { route: '/settings', file: 'admin-settings-license-summary.svg' },
  { route: '/audits', file: 'admin-audits-soc2-export.svg' },
  { route: '/sessions', file: 'admin-sessions-recording-timeline.svg' }
];

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.addInitScript((token) => {
    window.localStorage.setItem('janusgate-access-token', token);
  }, accessToken);

  for (const shot of shots) {
    const page = await context.newPage();
    await page.goto(`${baseUrl}${shot.route}`, { waitUntil: 'networkidle' });
    await page.screenshot({
      path: path.join(outputDir, shot.file),
      fullPage: true
    });
    await page.close();
  }

  await browser.close();
})();
JS

JANUSGATE_DOC_SCREENSHOT_OUTPUT_DIR="$screenshot_dir" \
  node "$capture_script"

printf 'Captured browser screenshots into %s\n' "$screenshot_dir"
