from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from wechat_summary_bot.main import _date_range


def test_date_range_uses_shanghai_inclusive_days():
    start, end = _date_range(date(2026, 7, 1), date(2026, 7, 2))
    timezone = ZoneInfo("Asia/Shanghai")
    assert start == int(datetime(2026, 7, 1, tzinfo=timezone).timestamp())
    assert end == int(datetime(2026, 7, 3, tzinfo=timezone).timestamp())


def test_date_range_rejects_reverse_range():
    with pytest.raises(HTTPException, match="结束日期"):
        _date_range(date(2026, 7, 2), date(2026, 7, 1))
