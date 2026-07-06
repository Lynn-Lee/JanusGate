import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5_core_api_load_test_runner_is_wired_to_capacity_model() -> None:
    script_path = REPO_ROOT / "scripts/phase5-core-api-load-test.sh"
    capacity_smoke = (REPO_ROOT / "scripts/phase5-capacity-model-smoke.sh").read_text()
    capacity_doc = (REPO_ROOT / "docs/performance/phase5-capacity-model.md").read_text()
    roadmap = (REPO_ROOT / "docs/architecture/10-master-evaluation-and-roadmap.md").read_text()
    readme = (REPO_ROOT / "README.md").read_text()

    assert script_path.exists()
    script = script_path.read_text()
    assert "JANUSGATE_LOAD_TEST_BASE_URL is required" in script
    assert "JANUSGATE_LOAD_TEST_ACCESS_TOKEN is required" in script
    assert "JANUSGATE_LOAD_TEST_DURATION_SECONDS" in script
    assert "JANUSGATE_LOAD_TEST_CONCURRENCY" in script
    assert "GET /api/v1/auth/me" in script
    assert "GET /api/v1/assets/" in script
    assert "GET /api/v1/sessions/" in script
    assert "GET /api/v1/automation/jobs/runs" in script
    assert "Authorization: Bearer ${JANUSGATE_LOAD_TEST_ACCESS_TOKEN}" in script
    assert "JANUSGATE_LOAD_TEST_P95_MS" in script
    assert "JANUSGATE_LOAD_TEST_RPS" in script
    assert "scripts/phase5-capacity-model-smoke.sh" in script
    assert "password" not in script.lower()
    assert "secret" not in script.lower()

    assert "JANUSGATE_LOAD_TEST_RPS" in capacity_smoke
    assert "scripts/phase5-core-api-load-test.sh" in capacity_doc
    assert "Endpoint Mix" in capacity_doc
    assert "Authorization token must come from the environment" in capacity_doc
    assert "真实压测脚本" in roadmap
    assert "scripts/phase5-core-api-load-test.sh" in readme


def test_phase5_load_test_evidence_archive_contract_is_wired() -> None:
    evidence_template = REPO_ROOT / "docs/performance/phase5-load-test-evidence-template.json"
    evidence_smoke = REPO_ROOT / "scripts/phase5-load-test-evidence-smoke.sh"
    capacity_doc = (REPO_ROOT / "docs/performance/phase5-capacity-model.md").read_text()
    roadmap = (REPO_ROOT / "docs/architecture/10-master-evaluation-and-roadmap.md").read_text()
    readme = (REPO_ROOT / "README.md").read_text()

    assert evidence_template.exists()
    assert evidence_smoke.exists()

    template = evidence_template.read_text()
    for required_field in (
        "evidence_version",
        "target_environment",
        "run_metadata",
        "runner_configuration",
        "endpoint_mix",
        "aggregate_results",
        "capacity_model",
        "artifact_manifest",
        "redaction_policy",
    ):
        assert required_field in template

    script = evidence_smoke.read_text()
    assert "JANUSGATE_LOAD_TEST_EVIDENCE_FILE" in script
    assert "phase5-load-test-evidence-template.json" in script
    assert "disallowed sensitive field" in script
    assert "password" not in template.lower()
    assert "secret" not in template.lower()
    assert "token" not in template.lower()

    subprocess.run([str(evidence_smoke)], cwd=REPO_ROOT, check=True)

    assert "phase5-load-test-evidence-template.json" in capacity_doc
    assert "scripts/phase5-load-test-evidence-smoke.sh" in roadmap
    assert "phase5-load-test-evidence-template.json" in readme
