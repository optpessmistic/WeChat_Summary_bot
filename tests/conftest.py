from __future__ import annotations

from pathlib import Path

import pytest

from wechat_summary_bot.config import Settings
from wechat_summary_bot.db import create_database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_home=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_upload_bytes=20 * 1024 * 1024,
        max_uncompressed_bytes=50 * 1024 * 1024,
        max_zip_entries=100,
        chunk_token_budget=2_000,
    )


@pytest.fixture
def database(settings: Settings):
    from wechat_summary_bot.models import Base

    engine, factory = create_database(settings)
    Base.metadata.create_all(engine)
    try:
        yield engine, factory
    finally:
        engine.dispose()

