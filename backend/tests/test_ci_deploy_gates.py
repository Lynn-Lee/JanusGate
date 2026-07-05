from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ci_runs_phase3_deploy_smoke_gates() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()

    assert "docker compose config" in workflow
    assert "scripts/phase3-compose-health-smoke.sh" in workflow
    assert "helm template" in workflow


def test_phase3_compose_health_smoke_script_runs_backend_healthcheck() -> None:
    script = (REPO_ROOT / "scripts/phase3-compose-health-smoke.sh").read_text()

    assert "docker compose" in script
    assert "up --build -d backend" in script
    assert "http://localhost:8000/health" in script
    assert "curl -fsS" in script
    assert "down -v --remove-orphans" in script


def test_compose_internal_dependencies_do_not_bind_fixed_host_ports() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())

    assert "ports" not in compose["services"]["postgres"]
    assert "ports" not in compose["services"]["redis"]


def test_helm_chart_supports_hpa_without_static_replicas_when_enabled() -> None:
    values = yaml.safe_load((REPO_ROOT / "deploy/helm/janusgate/values.yaml").read_text())
    hpa_template = (REPO_ROOT / "deploy/helm/janusgate/templates/hpa.yaml").read_text()
    deployment_template = (
        REPO_ROOT / "deploy/helm/janusgate/templates/deployment.yaml"
    ).read_text()

    assert values["autoscaling"] == {
        "enabled": False,
        "minReplicas": 2,
        "maxReplicas": 5,
        "targetCPUUtilizationPercentage": 70,
        "targetMemoryUtilizationPercentage": 80,
    }
    assert values["config"]["sessionConnectionTokenStore"] == "memory"
    assert "kind: HorizontalPodAutoscaler" in hpa_template
    assert "scaleTargetRef:" in hpa_template
    assert "autoscaling requires config.sessionConnectionTokenStore=redis" in hpa_template
    assert "{{- if not .Values.autoscaling.enabled }}" in deployment_template
