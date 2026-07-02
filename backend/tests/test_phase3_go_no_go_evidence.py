from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DOC = REPO_ROOT / "docs" / "qa" / "phase3-go-no-go.md"


def test_phase3_go_no_go_evidence_package_covers_release_gate() -> None:
    evidence = EVIDENCE_DOC.read_text()
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "# Phase 3 QA Go/No-Go Evidence Package" in evidence
    assert "Decision: GO" in evidence
    assert "docs/architecture/10-master-evaluation-and-roadmap.md" in evidence
    assert "pytest -q --cov=app --cov-report=term-missing --cov-fail-under=80" in evidence
    assert "pytest --cov=app --cov-report=term-missing --cov-fail-under=80" in workflow

    for page in [
        "Login",
        "Assets",
        "Sessions",
        "Workflow/JIT",
        "Audit Logs",
        "Settings",
    ]:
        assert page in evidence

    for gate in [
        "Product acceptance",
        "Security acceptance",
        "Backend quality",
        "Frontend quality",
        "Deployability",
        "Documentation",
    ]:
        assert gate in evidence

    for evidence_item in [
        "backend/tests/test_phase3_api_smoke.py",
        "scripts/phase3-compose-health-smoke.sh",
        "helm template janusgate deploy/helm/janusgate",
        "npm run build",
    ]:
        assert evidence_item in evidence
