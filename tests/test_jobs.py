from __future__ import annotations

from wechat_summary_bot.jobs import JobManager
from wechat_summary_bot.models import AnalysisJob
from wechat_summary_bot.security import SecretStore


def test_interrupted_jobs_are_marked_failed(settings, database):
    _engine, factory = database
    manager = JobManager(
        settings=settings,
        session_factory=factory,
        secret_store=SecretStore(),
    )
    job_id = manager.create_job(
        job_type="analysis",
        input_data={"conversation_id": "missing"},
    )
    manager.recover_interrupted_jobs()
    with factory() as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "中断" in job.stage
        assert "退出" in job.error
