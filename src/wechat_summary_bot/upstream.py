from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings


class UpstreamError(RuntimeError):
    pass


def validate_loopback_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("上游地址必须是有效的 http(s) URL。")
    host = parsed.hostname.lower().rstrip(".")
    allowed = host == "localhost"
    if not allowed:
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = False
    if not allowed:
        raise ValueError("为保护聊天数据，上游地址只允许 localhost 或回环 IP。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("上游地址不能包含凭据、查询参数或片段。")
    return raw


class UpstreamClient:
    def __init__(
        self,
        base_url: str,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = validate_loopback_url(base_url)
        self.settings = settings
        self.transport = transport

    async def accounts(self) -> dict[str, Any]:
        return await self._json("GET", "/api/chat/accounts")

    async def targets(self, account: str | None = None) -> dict[str, Any]:
        params = {
            "source": "auto",
            "include_hidden": "true",
            "include_official": "false",
        }
        if account:
            params["account"] = account
        return await self._json("GET", "/api/chat/exports/targets", params=params)

    async def create_export(
        self,
        *,
        account: str | None,
        username: str,
        start_time: int | None,
        end_time: int | None,
    ) -> dict[str, Any]:
        body = {
            "account": account or None,
            "source": "auto",
            "scope": "selected",
            "usernames": [username],
            "format": "json",
            "start_time": start_time,
            "end_time": end_time,
            "include_hidden": True,
            "include_official": False,
            "include_media": False,
            "media_kinds": [],
            "message_types": [],
            "download_remote_media": False,
            "privacy_mode": False,
        }
        return await self._json("POST", "/api/chat/exports", json_body=body)

    async def wait_and_download(
        self,
        *,
        export_id: str,
        destination: Path,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]],
        is_cancelled: Callable[[], bool],
    ) -> None:
        deadline = time.monotonic() + self.settings.upstream_timeout_seconds
        while time.monotonic() < deadline:
            if is_cancelled():
                await self.cancel(export_id)
                raise asyncio.CancelledError
            payload = await self._json("GET", f"/api/chat/exports/{export_id}")
            job = payload.get("job") if isinstance(payload, dict) else None
            if not isinstance(job, dict):
                raise UpstreamError("上游返回了无法识别的导出任务状态。")
            await on_progress(job)
            status = str(job.get("status") or "").lower()
            if status in {"done", "completed", "succeeded"}:
                await self._download(f"/api/chat/exports/{export_id}/download", destination)
                return
            if status in {"error", "failed", "cancelled"}:
                detail = str(job.get("error") or "导出失败")
                raise UpstreamError(f"上游导出任务{status}：{detail}")
            await asyncio.sleep(1)
        await self.cancel(export_id)
        raise UpstreamError("等待上游导出超时。")

    async def cancel(self, export_id: str) -> None:
        try:
            await self._json("DELETE", f"/api/chat/exports/{export_id}")
        except Exception:
            pass

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, params=params, json=json_body)
                response.raise_for_status()
                payload = response.json()
        except httpx.ConnectError as exc:
            raise UpstreamError("无法连接 WeChatDataAnalysis，请先启动它。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise UpstreamError(f"上游接口返回 {exc.response.status_code}：{detail}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"上游接口请求失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise UpstreamError("上游接口返回了非对象 JSON。")
        return payload

    async def _download(self, path: str, destination: Path) -> None:
        total = 0
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=None,
                trust_env=False,
                transport=self.transport,
            ) as client:
                async with client.stream("GET", path) as response:
                    response.raise_for_status()
                    with destination.open("wb") as stream:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > self.settings.max_upload_bytes:
                                raise UpstreamError("上游导出文件超过安全上限。")
                            stream.write(chunk)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"下载上游导出文件失败：{exc}") from exc
