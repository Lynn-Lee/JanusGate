#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
screenshot_dir="$repo_root/docs/site/assets/screenshots"
fixture_file="$repo_root/docs/site/fixtures/admin-screenshot-data.json"
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
require_file "$fixture_file"
grep -q 'JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1' "$repo_root/docs/site/admin-screenshots.md"
grep -q 'scripts/phase5-docs-browser-screenshots-smoke.sh' "$repo_root/docs/site/admin-screenshots.md"
grep -q 'docs/site/fixtures/admin-screenshot-data.json' "$repo_root/docs/site/admin-screenshots.md"
grep -q 'admin-settings-license-summary' "$fixture_file"
grep -q 'admin-audits-soc2-export' "$fixture_file"
grep -q 'admin-sessions-recording-timeline' "$fixture_file"
grep -q 'password=\[REDACTED\]' "$fixture_file"

if [[ -f "$docs_package_dir/manifest.json" ]]; then
  grep -q '"screenshotCapture"' "$docs_package_dir/manifest.json"
  grep -q 'fixtures/admin-screenshot-data.json' "$docs_package_dir/manifest.json"
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
const fs = require('node:fs');
const { chromium } = require('playwright');

const baseUrl = process.env.JANUSGATE_FRONTEND_BASE_URL.replace(/\/+$/, '');
const outputDir = process.env.JANUSGATE_DOC_SCREENSHOT_OUTPUT_DIR;
const fixturePath = process.env.JANUSGATE_DOC_SCREENSHOT_FIXTURE_PATH;
const accessToken = process.env.JANUSGATE_DOC_SCREENSHOT_ACCESS_TOKEN || 'docs-screenshot-token';
const fixtureData = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

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
  await context.addInitScript((fixture) => {
    window.localStorage.setItem('janusgate-doc-screenshot-fixture', JSON.stringify(fixture));
  }, fixtureData);

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
JANUSGATE_DOC_SCREENSHOT_FIXTURE_PATH="$fixture_file" \
  node "$capture_script"

printf 'Captured browser screenshots into %s\n' "$screenshot_dir"
