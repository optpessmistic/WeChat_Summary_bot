from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .ai import AnalysisCancelled, AnalysisError, load_analysis_material, run_analysis
from .config import Settings
from .db import session_scope
from .importer import import_archive
from .models import AnalysisJob, ProviderConfig, Report, now_ts
from .reports import render_markdown
from .security import SecretStore
from .upstream import UpstreamClient
from .workflows import WorkflowDefinition, default_workflow

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


async def _run_blocking(function: Any) -> Any:
    state: dict[str, Any] = {}
    cancelled = False

    def target() -> None:
        try:
            state["result"] = function()
        except BaseException as exc:
            state["error"] = exc

    thread = threading.Thread(target=target, name="wechat-summary-worker", daemon=True)
    thread.start()
    while thread.is_alive():
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
    thread.join()
    if cancelled:
        raise asyncio.CancelledError
    if "error" in state:
        raise state["error"]
    return state.get("result")


def job_public(job: AnalysisJob) -> dict[str, Any]:
    try:
        result = json.loads(job.result_json or "{}")
    except json.JSONDecodeError:
        result = {}
    return {
        "id": job.id,
        "job_type": job.job_type,
        "conversation_id": job.conversation_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error": job.error,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "result": result,
    }


class JobManager:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        secret_store: SecretStore,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.analysis_semaphore = asyncio.Semaphore(1)

    def recover_interrupted_jobs(self) -> None:
        with session_scope(self.session_factory) as session:
            jobs = list(
                session.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(["queued", "running"])))
            )
            for job in jobs:
                job.status = "failed"
                job.stage = "任务已中断"
                job.error = "应用在任务完成前退出，请重新运行。"
                job.completed_at = now_ts()

    def create_job(
        self,
        *,
        job_type: str,
        input_data: dict[str, Any],
        conversation_id: str | None = None,
    ) -> str:
        with session_scope(self.session_factory) as session:
            job = AnalysisJob(
                job_type=job_type,
                conversation_id=conversation_id,
                status="queued",
                stage="等待处理",
                progress=0,
                input_json=json.dumps(input_data, ensure_ascii=False),
            )
            session.add(job)
            session.flush()
            return job.id

    def start_upload_import(
        self,
        *,
        path: Path,
        filename: str,
        source_type: str = "upload",
    ) -> str:
        job_id = self.create_job(
            job_type="import",
            input_data={"filename": filename, "source_type": source_type},
        )
        self._track(job_id, self._run_import_file(job_id, path, filename, source_type))
        return job_id

    def start_upstream_import(
        self,
        *,
        upstream_url: str,
        account: str | None,
        username: str,
        start_time: int | None,
        end_time: int | None,
    ) -> str:
        input_data = {
            "upstream_url": upstream_url,
            "account": account,
            "username": username,
            "start_time": start_time,
            "end_time": end_time,
        }
        job_id = self.create_job(job_type="import", input_data=input_data)
        self._track(job_id, self._run_upstream_import(job_id, input_data))
        return job_id

    def start_analysis(self, input_data: dict[str, Any]) -> str:
        job_id = self.create_job(
            job_type="analysis",
            conversation_id=str(input_data["conversation_id"]),
            input_data=input_data,
        )
        self._track(job_id, self._run_analysis(job_id, input_data))
        return job_id

    def cancel(self, job_id: str) -> bool:
        cancel_running_analysis = False
        with session_scope(self.session_factory) as session:
            job = session.get(AnalysisJob, job_id)
            if job is None:
                return False
            if job.status in TERMINAL_STATUSES:
                return True
            cancel_running_analysis = job.job_type == "analysis" and job.status == "running"
            job.cancel_requested = True
            job.stage = "正在取消"
        if cancel_running_analysis:
            task = self.tasks.get(job_id)
            if task is not None:
                task.cancel()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            return bool(job is None or job.cancel_requested)

    def _track(self, job_id: str, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine, name=f"wechat-summary-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(job_id, None))

    def _update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(AnalysisJob, job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
                if status == "running" and job.started_at is None:
                    job.started_at = now_ts()
                if status in TERMINAL_STATUSES:
                    job.completed_at = now_ts()
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = max(0, min(100, int(progress)))
            if error is not None:
                job.error = str(error)[:20_000]
            if result is not None:
                job.result_json = json.dumps(result, ensure_ascii=False)
            if conversation_id is not None:
                job.conversation_id = conversation_id

    async def _run_import_file(
        self,
        job_id: str,
        path: Path,
        filename: str,
        source_type: str,
    ) -> None:
        self._update(job_id, status="running", stage="正在校验并导入文件", progress=10)
        try:
            if self.is_cancelled(job_id):
                raise asyncio.CancelledError

            def perform_import() -> dict[str, Any]:
                with session_scope(self.session_factory) as session:
                    return import_archive(
                        session,
                        path=path,
                        source_type=source_type,
                        filename=filename,
                        settings=self.settings,
                    )

            result = await _run_blocking(perform_import)
            first_conversation = next(
                (str(item.get("id")) for item in result.get("conversations") or [] if item.get("id")),
                None,
            )
            self._update(
                job_id,
                status="succeeded",
                stage="导入完成",
                progress=100,
                result=result,
                conversation_id=first_conversation,
            )
        except asyncio.CancelledError:
            self._update(job_id, status="cancelled", stage="已取消", progress=100)
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="导入失败",
                progress=100,
                error=str(exc),
            )
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _run_upstream_import(self, job_id: str, input_data: dict[str, Any]) -> None:
        self._update(job_id, status="running", stage="正在请求上游导出", progress=5)
        destination: Path | None = None
        try:
            client = UpstreamClient(str(input_data["upstream_url"]), self.settings)
            created = await client.create_export(
                account=input_data.get("account"),
                username=str(input_data["username"]),
                start_time=input_data.get("start_time"),
                end_time=input_data.get("end_time"),
            )
            upstream_job = created.get("job") if isinstance(created, dict) else None
            if not isinstance(upstream_job, dict):
                upstream_job = created if isinstance(created, dict) else {}
            export_id = str(upstream_job.get("exportId") or upstream_job.get("export_id") or "")
            if not export_id:
                raise RuntimeError("上游没有返回 exportId，可能版本不兼容。")
            with tempfile.NamedTemporaryFile(
                prefix="upstream_", suffix=".zip", dir=self.settings.temp_dir, delete=False
            ) as stream:
                destination = Path(stream.name)

            async def on_progress(job: dict[str, Any]) -> None:
                progress_raw = job.get("progress")
                progress_value = 0
                if isinstance(progress_raw, dict):
                    total = int(
                        progress_raw.get("conversationsTotal") or progress_raw.get("conversations_total") or 0
                    )
                    done = int(
                        progress_raw.get("conversationsDone") or progress_raw.get("conversations_done") or 0
                    )
                    progress_value = int(done / total * 55) if total else 10
                elif isinstance(progress_raw, (int, float)):
                    progress_value = int(float(progress_raw) * 0.55)
                self._update(
                    job_id,
                    stage="WeChatDataAnalysis 正在导出",
                    progress=max(8, min(62, 8 + progress_value)),
                )

            await client.wait_and_download(
                export_id=export_id,
                destination=destination,
                on_progress=on_progress,
                is_cancelled=lambda: self.is_cancelled(job_id),
            )
            self._update(job_id, stage="导出完成，正在写入本地资料库", progress=70)
            await self._run_import_file(
                job_id,
                destination,
                f"wechat_export_{export_id}.zip",
                "upstream",
            )
            destination = None
        except asyncio.CancelledError:
            self._update(job_id, status="cancelled", stage="已取消", progress=100)
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="上游导入失败",
                progress=100,
                error=str(exc),
            )
        finally:
            if destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _run_analysis(self, job_id: str, input_data: dict[str, Any]) -> None:
        async with self.analysis_semaphore:
            self._update(job_id, status="running", stage="正在准备聊天上下文", progress=3)
            try:
                with self.session_factory() as session:
                    material = load_analysis_material(
                        session,
                        conversation_id=str(input_data["conversation_id"]),
                        mode=str(input_data["mode"]),
                        member_username=input_data.get("member_username"),
                        start_time=input_data.get("start_time"),
                        end_time=input_data.get("end_time"),
                    )
                    provider = session.get(ProviderConfig, str(input_data["provider_id"]))
                    if provider is None:
                        raise AnalysisError("AI 服务配置不存在。")
                    session.expunge(provider)
                api_key = self.secret_store.get(provider.id)
                workflow_raw = input_data.get("workflow")
                workflow = (
                    WorkflowDefinition.model_validate(workflow_raw)
                    if isinstance(workflow_raw, dict)
                    else default_workflow()
                )

                async def on_progress(stage: str, progress: int) -> None:
                    self._update(job_id, stage=stage, progress=progress)

                output = await run_analysis(
                    material=material,
                    provider=provider,
                    api_key=api_key,
                    mode=str(input_data["mode"]),
                    focus=str(input_data.get("focus") or ""),
                    pseudonymize=bool(input_data.get("pseudonymize", True)),
                    workflow=workflow,
                    is_cancelled=lambda: self.is_cancelled(job_id),
                    on_progress=on_progress,
                )
                output.report.setdefault("metadata", {}).update(
                    {
                        "workflow_id": str(input_data.get("workflow_id") or ""),
                        "workflow_name": str(input_data.get("workflow_name") or "均衡总结"),
                    }
                )
                output.metrics.update(
                    {
                        "workflow_id": str(input_data.get("workflow_id") or ""),
                        "workflow_name": str(input_data.get("workflow_name") or "均衡总结"),
                    }
                )
                markdown = render_markdown(output.report)
                with session_scope(self.session_factory) as session:
                    report = Report(
                        job_id=job_id,
                        conversation_id=str(input_data["conversation_id"]),
                        report_json=json.dumps(output.report, ensure_ascii=False),
                        markdown=markdown,
                        prompt_tokens=output.usage.prompt_tokens,
                        completion_tokens=output.usage.completion_tokens,
                        metrics_json=json.dumps(output.metrics, ensure_ascii=False),
                    )
                    session.add(report)
                    session.flush()
                    report_id = report.id
                self._update(
                    job_id,
                    status="succeeded",
                    stage="报告已生成",
                    progress=100,
                    result={
                        "report_id": report_id,
                        "estimated_input_tokens": output.estimated_input_tokens,
                        "prompt_tokens": output.usage.prompt_tokens,
                        "completion_tokens": output.usage.completion_tokens,
                        "reasoning_tokens": output.usage.reasoning_tokens,
                        "cached_prompt_tokens": output.usage.cached_prompt_tokens,
                        "total_tokens": (output.usage.prompt_tokens + output.usage.completion_tokens),
                        "metrics": output.metrics,
                    },
                )
            except (AnalysisCancelled, asyncio.CancelledError):
                self._update(job_id, status="cancelled", stage="已取消", progress=100)
            except Exception as exc:
                self._update(
                    job_id,
                    status="failed",
                    stage="分析失败",
                    progress=100,
                    error=str(exc),
                )
