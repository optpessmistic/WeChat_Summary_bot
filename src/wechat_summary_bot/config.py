from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _default_home() -> Path:
    configured = os.environ.get("WECHAT_SUMMARY_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "WeChatSummaryBot"
    return Path.home() / ".local" / "share" / "WeChatSummaryBot"


@dataclass(frozen=True, slots=True)
class Settings:
    app_home: Path
    database_url: str
    upstream_url: str = "http://127.0.0.1:10392"
    host: str = "127.0.0.1"
    port: int = 10420
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_zip_entries: int = 10_000
    upstream_timeout_seconds: int = 30 * 60
    chunk_token_budget: int = 12_000

    @property
    def temp_dir(self) -> Path:
        return self.app_home / "tmp"

    @property
    def log_dir(self) -> Path:
        return self.app_home / "logs"

    def ensure_directories(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    home = _default_home()
    database_path = home / "wechat_summary.db"
    return Settings(
        app_home=home,
        database_url=os.environ.get("WECHAT_SUMMARY_DATABASE_URL", f"sqlite:///{database_path.as_posix()}"),
        upstream_url=os.environ.get("WECHAT_DATA_ANALYSIS_URL", "http://127.0.0.1:10392").rstrip("/"),
        host="127.0.0.1",
        port=int(os.environ.get("WECHAT_SUMMARY_PORT", "10420")),
        max_upload_bytes=int(os.environ.get("WECHAT_SUMMARY_MAX_UPLOAD_BYTES", str(2 * 1024**3))),
        max_uncompressed_bytes=int(
            os.environ.get("WECHAT_SUMMARY_MAX_UNCOMPRESSED_BYTES", str(4 * 1024**3))
        ),
        max_zip_entries=int(os.environ.get("WECHAT_SUMMARY_MAX_ZIP_ENTRIES", "10000")),
        upstream_timeout_seconds=int(os.environ.get("WECHAT_SUMMARY_UPSTREAM_TIMEOUT", str(30 * 60))),
        chunk_token_budget=int(os.environ.get("WECHAT_SUMMARY_CHUNK_TOKENS", "12000")),
    )
