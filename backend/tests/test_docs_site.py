from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5_docs_site_foundation_is_wired_for_operator_handoff() -> None:
    docs_readme = (REPO_ROOT / "docs/README.md").read_text()
    docs_index = (REPO_ROOT / "docs/site/index.md").read_text()
    install_guide = (REPO_ROOT / "docs/site/install.md").read_text()
    admin_guide = (REPO_ROOT / "docs/site/admin.md").read_text()
    api_docs = (REPO_ROOT / "docs/site/api.md").read_text()
    api_contract = (REPO_ROOT / "docs/api-contract.md").read_text()
    export_script = (REPO_ROOT / "scripts/export-openapi-json.sh").read_text()
    build_script_path = REPO_ROOT / "scripts/build-docs-site.sh"
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    roadmap = (REPO_ROOT / "docs/architecture/10-master-evaluation-and-roadmap.md").read_text()

    assert "docs/site/index.md" in docs_readme
    assert "Phase 5 #t59" in docs_index
    assert "install.md" in docs_index
    assert "admin.md" in docs_index
    assert "api.md" in docs_index
    assert "SECRET_KEY" in install_guide
    assert "docker compose up --build -d" in install_guide
    assert "helm upgrade --install" in install_guide
    assert "License / Edition" in admin_guide
    assert "/api/v1/admin/license-summary" in admin_guide
    assert "/api/v1/auth/login" in api_docs
    assert "/api/v1/sessions/" in api_docs
    assert "/api/v1/admin/license-summary" in api_docs
    assert "scripts/export-openapi-json.sh" in api_docs
    assert "app.main import app" in export_script
    assert "openapi.json" in export_script
    assert build_script_path.exists()
    build_script = build_script_path.read_text()
    assert "docs-site" in build_script
    assert "openapi.json" in build_script
    assert "index.md" in build_script
    assert "scripts/export-openapi-json.sh" in workflow
    assert "scripts/build-docs-site.sh" in workflow
    assert "API 前缀" in api_contract
    assert "#t59" in roadmap
    assert "OpenAPI 自动生成 foundation" in roadmap
    assert "静态站点发布 smoke" in roadmap
