from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from wechat_summary_bot.upstream import UpstreamClient, validate_loopback_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:10392",
        "http://localhost:10392",
        "http://[::1]:10392",
    ],
)
def test_allows_loopback_upstream(url):
    assert validate_loopback_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.2:10392",
        "ftp://127.0.0.1/file",
        "http://user:pass@localhost:10392",
    ],
)
def test_rejects_non_loopback_or_unsafe_upstream(url):
    with pytest.raises(ValueError):
        validate_loopback_url(url)


@pytest.mark.asyncio
async def test_upstream_export_flow(settings, tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/chat/accounts":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "items": [{"account": "wxid_me", "name": "wxid_me"}],
                },
            )
        if request.url.path == "/api/chat/exports/targets":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "account": "wxid_me",
                    "targets": [
                        {
                            "username": "team@chatroom",
                            "displayName": "项目群",
                            "isGroup": True,
                        }
                    ],
                },
            )
        if request.url.path == "/api/chat/exports" and request.method == "POST":
            return httpx.Response(
                200,
                json={"status": "success", "job": {"exportId": "export-1"}},
            )
        if request.url.path == "/api/chat/exports/export-1":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "job": {
                        "exportId": "export-1",
                        "status": "done",
                        "progress": {
                            "conversationsTotal": 1,
                            "conversationsDone": 1,
                        },
                    },
                },
            )
        if request.url.path == "/api/chat/exports/export-1/download":
            return httpx.Response(200, content=b"PK-test-archive")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = UpstreamClient(
        "http://127.0.0.1:10392",
        settings,
        transport=httpx.MockTransport(handler),
    )
    assert (await client.accounts())["items"][0]["account"] == "wxid_me"
    assert (await client.targets("wxid_me"))["targets"][0]["isGroup"]
    created = await client.create_export(
        account="wxid_me",
        username="team@chatroom",
        start_time=100,
        end_time=200,
    )
    assert created["job"]["exportId"] == "export-1"
    destination = tmp_path / "export.zip"
    progress: list[dict] = []
    await client.wait_and_download(
        export_id="export-1",
        destination=destination,
        on_progress=lambda job: _capture_progress(progress, job),
        is_cancelled=lambda: False,
    )
    assert destination.read_bytes() == b"PK-test-archive"
    assert progress[-1]["status"] == "done"
    export_request = next(
        request
        for request in requests
        if request.method == "POST" and request.url.path == "/api/chat/exports"
    )
    body = json.loads(export_request.content)
    assert body["format"] == "json"
    assert body["include_media"] is False
    assert body["usernames"] == ["team@chatroom"]
    assert body["start_time"] == 100
    assert body["end_time"] == 200


@pytest.mark.asyncio
async def test_upstream_cancel_propagates(settings, tmp_path):
    cancelled: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            cancelled.append(True)
            return httpx.Response(200, json={"status": "success"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = UpstreamClient(
        "http://127.0.0.1:10392",
        settings,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(asyncio.CancelledError):
        await client.wait_and_download(
            export_id="export-2",
            destination=tmp_path / "unused.zip",
            on_progress=lambda job: _capture_progress([], job),
            is_cancelled=lambda: True,
        )
    assert cancelled


async def _capture_progress(target: list[dict], job: dict) -> None:
    target.append(job)
