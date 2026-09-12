"""#t77 作业中心周期调度：到期作业 JSON-only 入队，不使用 Celery/pickle。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job_center import OpsJob
from app.services.automation_worker import AutomationJobQueue
from app.services.job_center import enqueue_ops_job, next_run_at, sanitize_cron_expr


@dataclass(frozen=True)
class JobCenterSchedulerResult:
    enqueued: int
    skipped: int


class JobCenterScheduler:
    """扫描 enabled 且 next_run_at 到期的作业，入队后推进下次执行时间。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: AutomationJobQueue,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue

    async def tick(self, *, now: datetime | None = None) -> JobCenterSchedulerResult:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        enqueued = 0
        skipped = 0
        async with self._session_factory() as session:
            result = await session.execute(
                select(OpsJob)
                .where(OpsJob.enabled.is_(True))
                .where(OpsJob.cron_expr.is_not(None))
                .where(OpsJob.next_run_at.is_not(None))
                .where(OpsJob.next_run_at <= moment)
                .order_by(OpsJob.next_run_at.asc(), OpsJob.id.asc())
            )
            jobs = list(result.scalars().all())
            for job in jobs:
                cron_expr = sanitize_cron_expr(job.cron_expr)
                if cron_expr is None:
                    skipped += 1
                    continue
                await enqueue_ops_job(queue=self._queue, job=job, requested_by="scheduler")
                job.next_run_at = next_run_at(
                    cron_expr=cron_expr, timezone=job.timezone, after=moment
                )
                enqueued += 1
            await session.commit()
        return JobCenterSchedulerResult(enqueued=enqueued, skipped=skipped)
