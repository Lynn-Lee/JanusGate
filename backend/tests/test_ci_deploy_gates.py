from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ci_runs_phase3_deploy_smoke_gates() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()

    assert "docker compose config" in workflow
    assert "helm template" in workflow
