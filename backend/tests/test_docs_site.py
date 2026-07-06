from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5_docs_site_foundation_is_wired_for_operator_handoff() -> None:
    docs_readme = (REPO_ROOT / "docs/README.md").read_text()
    docs_index = (REPO_ROOT / "docs/site/index.md").read_text()
    install_guide = (REPO_ROOT / "docs/site/install.md").read_text()
    admin_guide = (REPO_ROOT / "docs/site/admin.md").read_text()
    screenshot_guide = (REPO_ROOT / "docs/site/admin-screenshots.md").read_text()
    screenshot_fixture_path = REPO_ROOT / "docs/site/fixtures/admin-screenshot-data.json"
    screenshot_archive_path = REPO_ROOT / "docs/site/fixtures/admin-screenshot-archive.json"
    runbook_evidence_path = REPO_ROOT / "docs/site/fixtures/operation-runbook-evidence.json"
    license_evidence_path = REPO_ROOT / "docs/site/fixtures/license-operations-evidence.json"
    api_docs = (REPO_ROOT / "docs/site/api.md").read_text()
    runbooks = (REPO_ROOT / "docs/site/runbooks.md").read_text()
    api_contract = (REPO_ROOT / "docs/api-contract.md").read_text()
    export_script = (REPO_ROOT / "scripts/export-openapi-json.sh").read_text()
    build_script_path = REPO_ROOT / "scripts/build-docs-site.sh"
    screenshot_script_path = REPO_ROOT / "scripts/phase5-docs-browser-screenshots-smoke.sh"
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    roadmap = (REPO_ROOT / "docs/architecture/10-master-evaluation-and-roadmap.md").read_text()

    assert "docs/site/index.md" in docs_readme
    assert "Phase 5 #t59" in docs_index
    assert "install.md" in docs_index
    assert "admin.md" in docs_index
    assert "api.md" in docs_index
    assert "runbooks.md" in docs_index
    assert "admin-screenshots.md" in docs_index
    assert "operation-runbook-evidence.json" in docs_index
    assert "license-operations-evidence.json" in docs_index
    assert "SECRET_KEY" in install_guide
    assert "docker compose up --build -d" in install_guide
    assert "helm upgrade --install" in install_guide
    assert "License / Edition" in admin_guide
    assert "/api/v1/admin/license-summary" in admin_guide
    assert "External license service evidence" in admin_guide
    assert "截图证据" in admin_guide
    assert "Settings - License / Edition" in screenshot_guide
    assert "Audits - SOC2 report export" in screenshot_guide
    assert "Sessions - recording command timeline" in screenshot_guide
    assert "Tenancy - organization inventory" in screenshot_guide
    assert "Accounts - credential rotation custody" in screenshot_guide
    assert "SSH CA - trust bundle and certificates" in screenshot_guide
    assert "assets/screenshots/admin-settings-license-summary.svg" in screenshot_guide
    assert "assets/screenshots/admin-audits-soc2-export.svg" in screenshot_guide
    assert "assets/screenshots/admin-sessions-recording-timeline.svg" in screenshot_guide
    assert "assets/screenshots/admin-tenancy-organization-inventory.svg" in screenshot_guide
    assert "assets/screenshots/admin-accounts-credential-rotation.svg" in screenshot_guide
    assert "assets/screenshots/admin-ssh-ca-trust-bundle.svg" in screenshot_guide
    assert "frontend/src/pages/mvp-pages.test.tsx" in screenshot_guide
    assert "docs/site/fixtures/admin-screenshot-data.json" in screenshot_guide
    assert "docs/site/fixtures/admin-screenshot-archive.json" in screenshot_guide
    assert "scripts/phase5-docs-browser-screenshots-smoke.sh" in screenshot_guide
    assert "JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1" in screenshot_guide
    screenshot_assets = [
        REPO_ROOT / "docs/site/assets/screenshots/admin-settings-license-summary.svg",
        REPO_ROOT / "docs/site/assets/screenshots/admin-audits-soc2-export.svg",
        REPO_ROOT / "docs/site/assets/screenshots/admin-sessions-recording-timeline.svg",
        REPO_ROOT / "docs/site/assets/screenshots/admin-tenancy-organization-inventory.svg",
        REPO_ROOT / "docs/site/assets/screenshots/admin-accounts-credential-rotation.svg",
        REPO_ROOT / "docs/site/assets/screenshots/admin-ssh-ca-trust-bundle.svg",
    ]
    for screenshot_asset in screenshot_assets:
        assert screenshot_asset.exists()
        assert "<svg" in screenshot_asset.read_text()
    assert "/api/v1/auth/login" in api_docs
    assert "/api/v1/sessions/" in api_docs
    assert "/api/v1/admin/license-summary" in api_docs
    assert "scripts/export-openapi-json.sh" in api_docs
    assert "Release checklist" in runbooks
    assert "helm rollback" in runbooks
    assert "connection token" in runbooks
    assert "Do not print" in runbooks
    assert "Operation evidence manifest" in runbooks
    assert "License operations evidence manifest" in runbooks
    assert "docs/site/fixtures/operation-runbook-evidence.json" in runbooks
    assert "docs/site/fixtures/license-operations-evidence.json" in runbooks
    assert "app.main import app" in export_script
    assert "openapi.json" in export_script
    assert build_script_path.exists()
    build_script = build_script_path.read_text()
    assert "docs-site" in build_script
    assert "openapi.json" in build_script
    assert "index.md" in build_script
    assert "runbooks.md" in build_script
    assert "admin-screenshots.md" in build_script
    assert "assets/screenshots" in build_script
    assert "fixtures/admin-screenshot-data.json" in build_script
    assert "fixtures/admin-screenshot-archive.json" in build_script
    assert "fixtures/operation-runbook-evidence.json" in build_script
    assert "fixtures/license-operations-evidence.json" in build_script
    assert "assets/screenshots/live-screenshots/admin-settings-license-summary.png" in build_script
    assert screenshot_script_path.exists()
    screenshot_script = screenshot_script_path.read_text()
    assert screenshot_fixture_path.exists()
    assert screenshot_archive_path.exists()
    assert runbook_evidence_path.exists()
    assert license_evidence_path.exists()
    screenshot_fixture = screenshot_fixture_path.read_text()
    screenshot_archive = screenshot_archive_path.read_text()
    assert "admin-settings-license-summary" in screenshot_fixture
    assert "configured_edition" in screenshot_fixture
    assert "admin-audits-soc2-export" in screenshot_fixture
    assert "worm_content_hash" in screenshot_fixture
    assert "admin-sessions-recording-timeline" in screenshot_fixture
    assert '"label": "Recording ID"' in screenshot_fixture
    assert '"录制回放时间线"' in screenshot_fixture
    assert "password=[REDACTED]" in screenshot_fixture
    assert "admin-tenancy-organization-inventory" in screenshot_fixture
    assert "Tenant A Ops" in screenshot_fixture
    assert "admin-accounts-credential-rotation" in screenshot_fixture
    assert "sec_tenant_a_deploy" in screenshot_fixture
    assert "plaintext-password" in screenshot_fixture
    assert "admin-ssh-ca-trust-bundle" in screenshot_fixture
    assert "Tenant A SSH CA" in screenshot_fixture
    assert "/api/v1/ssh-certificates/5/revoke" in screenshot_fixture
    assert "private_key_secret_id" in screenshot_fixture
    assert "JANUSGATE_CAPTURE_DOC_SCREENSHOTS" in screenshot_script
    assert "docs/site/fixtures/admin-screenshot-data.json" in screenshot_script
    assert "docs/site/fixtures/admin-screenshot-archive.json" in screenshot_script
    assert "playwright" in screenshot_script
    assert "capture_actions" in screenshot_fixture
    assert "must_show" in screenshot_script
    assert "must_not_show" in screenshot_script
    assert "getByRole" in screenshot_script
    assert "getByLabel" in screenshot_script
    assert "live-screenshots" in screenshot_script
    assert "NODE_PATH" in screenshot_script
    assert "replace(/\\.svg$/u, '.png')" in screenshot_script
    assert "admin-settings-license-summary.svg" in screenshot_script
    assert "admin-settings-license-summary.png" in screenshot_script
    assert "admin-audits-soc2-export.svg" in screenshot_script
    assert "admin-sessions-recording-timeline.svg" in screenshot_script
    assert "admin-tenancy-organization-inventory.svg" in screenshot_script
    assert "admin-accounts-credential-rotation.svg" in screenshot_script
    assert "admin-ssh-ca-trust-bundle.svg" in screenshot_script
    assert "janusgate.docs.admin-screenshot-archive.v1" in screenshot_archive
    assert "JANUSGATE_FRONTEND_BASE_URL" in screenshot_archive
    assert "live_browser_capture" in screenshot_archive
    assert "live_output_directory" in screenshot_archive
    assert "assets/screenshots/live-screenshots/admin-settings-license-summary.png" in screenshot_archive
    assert "admin-settings-license-summary" in screenshot_archive
    assert "admin-ssh-ca-trust-bundle" in screenshot_archive
    runbook_evidence = runbook_evidence_path.read_text()
    assert "janusgate.docs.operation-runbook-evidence.v1" in runbook_evidence
    assert "release_checklist" in runbook_evidence
    assert "multi_replica_smoke_checklist" in runbook_evidence
    assert "rollback_checklist" in runbook_evidence
    assert "secret_handling_checklist" in runbook_evidence
    assert "scripts/phase5-release-readiness-smoke.sh" in runbook_evidence
    assert "scripts/phase5-ha-config-smoke.sh" in runbook_evidence
    assert "scripts/phase5-k8s-multi-replica-smoke.sh" in runbook_evidence
    assert "helm rollback" in runbook_evidence
    assert "DATABASE_URL" in runbook_evidence
    license_evidence = license_evidence_path.read_text()
    assert "janusgate.docs.license-operations-evidence.v1" in license_evidence
    assert "external_license_service_sla" in license_evidence
    assert "JANUSGATE_LICENSE_VALIDATION_URL" in license_evidence
    assert "JANUSGATE_LICENSE_VALIDATION_TIMEOUT_SECONDS" in license_evidence
    assert "key_custody_checklist" in license_evidence
    assert "JANUSGATE_LICENSE_VALIDATION_TOKEN" in license_evidence
    assert "license key" in license_evidence
    assert "signing secret" in license_evidence
    assert "scripts/export-openapi-json.sh" in workflow
    assert "scripts/build-docs-site.sh" in workflow
    assert "scripts/phase5-docs-browser-screenshots-smoke.sh" in workflow
    assert "API 前缀" in api_contract
    assert "#t59" in roadmap
    assert "OpenAPI 自动生成 foundation" in roadmap
    assert "静态站点发布 smoke" in roadmap
    assert "真实浏览器截图流水线 foundation" in roadmap
    assert "live PNG 已在真实 Vite 前端环境刷新" in roadmap
    assert "真实浏览器 PNG 已完成刷新" in docs_readme
    assert "操作 runbook evidence manifest" in roadmap
    assert "license operations evidence manifest" in roadmap
