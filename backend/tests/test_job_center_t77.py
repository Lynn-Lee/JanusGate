"""#t77 job center API and execution linkage tests."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun, Job, JobExecution, JobPlaybook
from app.services.ansible_playbook import AnsiblePlaybookWorkerHandler


class RecordingRedisStream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int | None]] = []
        self._seq = 0

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        del approximate
        self._seq += 1
        message_id = f"170000000000{self._seq}-0"
        self.calls.append((name, fields, maxlen))
        return message_id


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


async def seed_assets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    playbook_root: Path,
) -> None:
    playbook_root.mkdir(parents=True, exist_ok=True)
    (playbook_root / "noop.yml").write_text(
        "---\n- hosts: all\n  tasks: []\n",
        encoding="utf-8",
    )
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                port=22,
                is_active=True,
            )
        )
        session.add(
            Asset(
                id=2,
                tenant_id="tenant-b",
                name="other",
                address="203.0.113.11",
                platform_id=1,
                is_active=True,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_job_center_playbook_job_run_enqueues_json_only_payload(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playbook_root = tmp_path / "playbooks"
    monkeypatch.setenv("ANSIBLE_PLAYBOOK_ROOT", str(playbook_root))
    from app.core.config import settings

    settings.ANSIBLE_PLAYBOOK_ROOT = str(playbook_root)

    await seed_assets(session_factory, playbook_root=playbook_root)
    stream = RecordingRedisStream()
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    install_db(session_factory)
    app.dependency_overrides[get_redis] = lambda: stream

    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/v1/job-center/playbooks/",
                json={"name": "baseline", "playbook_name": "../etc/passwd.yml"},
            )
            assert denied.status_code == 400

            created_playbook = client.post(
                "/api/v1/job-center/playbooks/",
                json={
                    "name": "noop",
                    "playbook_name": "noop.yml",
                    "description": "safe noop",
                },
            )
            assert created_playbook.status_code == 201, created_playbook.text
            playbook_id = created_playbook.json()["id"]

            created_job = client.post(
                "/api/v1/job-center/jobs/",
                json={
                    "name": "nightly-noop",
                    "playbook_id": playbook_id,
                    "target_asset_ids": [1],
                    "check_mode": True,
                },
            )
            assert created_job.status_code == 201, created_job.text
            job_id = created_job.json()["id"]

            # Cross-tenant asset rejected.
            bad_targets = client.post(
                f"/api/v1/job-center/jobs/{job_id}/run",
                json={"target_asset_ids": [2]},
            )
            assert bad_targets.status_code == 404

            run = client.post(
                f"/api/v1/job-center/jobs/{job_id}/run",
                json={"check_mode": False},
            )
            assert run.status_code == 202, run.text
            body = run.json()
            assert body["status"] == "queued"
            assert body["message_id"]
            assert body["execution_id"] > 0
            execution_id = body["execution_id"]

            secret_rejected = client.post(
                f"/api/v1/job-center/jobs/{job_id}/run",
                json={"check_mode": True, "password": "secret"},
            )
            assert secret_rejected.status_code == 422

            listed = client.get("/api/v1/job-center/executions/")
            assert listed.status_code == 200
            assert listed.json()["total"] == 1
            assert listed.json()["items"][0]["message_id"] == body["message_id"]
            assert listed.json()["items"][0]["playbook_name"] == "noop.yml"
    finally:
        app.dependency_overrides.clear()

    assert execution_id > 0
    assert len(stream.calls) == 1
    _name, fields, _maxlen = stream.calls[0]
    payload = json.loads(fields["payload_json"])
    assert fields["payload_format"] == "json"
    assert fields["job_type"] == "ansible.playbook"
    assert payload["playbook_name"] == "noop.yml"
    assert payload["target_asset_ids"] == [1]
    assert payload["check_mode"] is False
    assert payload["job_execution_id"] == execution_id
    assert "password" not in payload
    assert "secret" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_playbook_handler_updates_job_execution_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class RecordingRunner:
        async def run(self, playbook: object) -> None:
            del playbook

    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod",
                address="203.0.113.10",
                platform_id=1,
                is_active=True,
            )
        )
        session.add(
            JobPlaybook(
                id=1,
                tenant_id="tenant-a",
                name="noop",
                playbook_name="noop.yml",
                description="",
                is_active=True,
            )
        )
        session.add(
            Job(
                id=1,
                tenant_id="tenant-a",
                name="job-1",
                playbook_id=1,
                target_asset_ids_json="[1]",
                check_mode=False,
                description="",
                is_active=True,
            )
        )
        session.add(
            JobExecution(
                id=9,
                tenant_id="tenant-a",
                job_id=1,
                message_id="1700000000099-0",
                status="queued",
                requested_by="user-1",
                playbook_name="noop.yml",
                check_mode=False,
                target_count=1,
            )
        )
        await session.commit()

    handler = AnsiblePlaybookWorkerHandler(
        session_factory=session_factory,
        runner=RecordingRunner(),
    )
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={
            "playbook_name": "noop.yml",
            "target_asset_ids": [1],
            "check_mode": False,
            "job_execution_id": 9,
        },
        message_id="1700000000099-0",
    )

    async with session_factory() as session:
        execution = await session.get(JobExecution, 9)
        run = await session.get(AutomationJobRun, "1700000000099-0")
        assert execution is not None
        assert execution.status == "completed"
        assert execution.error_code is None
        assert run is not None
        assert run.status == "completed"


@pytest.mark.asyncio
async def test_job_center_list_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            JobPlaybook(
                tenant_id="tenant-a",
                name="a",
                playbook_name="noop.yml",
                description="",
                is_active=True,
            )
        )
        session.add(
            JobPlaybook(
                tenant_id="tenant-b",
                name="b",
                playbook_name="noop.yml",
                description="",
                is_active=True,
            )
        )
        await session.commit()

    install_user(tenant_id="tenant-a", permissions=["automation:read"])
    install_db(session_factory)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/job-center/playbooks/")
            assert response.status_code == 200
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["name"] == "a"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_playbook_blocked_when_jobs_exist(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playbook_root = tmp_path / "playbooks"
    monkeypatch.setenv("ANSIBLE_PLAYBOOK_ROOT", str(playbook_root))
    from app.core.config import settings

    settings.ANSIBLE_PLAYBOOK_ROOT = str(playbook_root)
    await seed_assets(session_factory, playbook_root=playbook_root)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    install_db(session_factory)
    try:
        with TestClient(app) as client:
            playbook = client.post(
                "/api/v1/job-center/playbooks/",
                json={"name": "noop", "playbook_name": "noop.yml"},
            ).json()
            client.post(
                "/api/v1/job-center/jobs/",
                json={
                    "name": "job",
                    "playbook_id": playbook["id"],
                    "target_asset_ids": [1],
                },
            )
            blocked = client.delete(f"/api/v1/job-center/playbooks/{playbook['id']}")
            assert blocked.status_code == 409
            assert blocked.json()["detail"] == "PLAYBOOK_IN_USE"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_job_center_helpers_reject_path_escape(tmp_path: Path) -> None:
    from app.services.job_center import resolve_playbook_file, validate_playbook_relative_name

    (tmp_path / "ok.yml").write_text("---\n", encoding="utf-8")
    assert validate_playbook_relative_name("ok.yml") == "ok.yml"
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        validate_playbook_relative_name("../ok.yml")
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        resolve_playbook_file("missing.yml", playbook_root=tmp_path)
