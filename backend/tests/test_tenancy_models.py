from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.tenancy import Organization, Project, Team
from app.tenancy.scope import actor_scope_from_user, scoped_select


async def test_organization_team_project_models_are_tenant_scoped() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory.begin() as session:
            session.add_all(
                [
                    Organization(id="org-a", tenant_id="tenant-a", name="Tenant A"),
                    Organization(id="org-b", tenant_id="tenant-b", name="Tenant B"),
                    Team(id="team-a", tenant_id="tenant-a", organization_id="org-a", name="Ops"),
                    Project(
                        id="project-a",
                        tenant_id="tenant-a",
                        organization_id="org-a",
                        name="Production",
                    ),
                    Project(
                        id="project-b",
                        tenant_id="tenant-b",
                        organization_id="org-b",
                        name="Staging",
                    ),
                ]
            )

        actor_scope = actor_scope_from_user({"id": "user-1", "tenant_id": "tenant-a"})
        async with session_factory() as session:
            result = await session.execute(scoped_select(Project, actor_scope))
            projects = result.scalars().all()

        assert [project.id for project in projects] == ["project-a"]
    finally:
        await engine.dispose()
