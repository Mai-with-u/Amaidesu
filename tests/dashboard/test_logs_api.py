"""日志历史查询 API / LogHistoryReader 测试。

覆盖：时间窗过滤与升序返回、before 游标翻页与 has_more_older、
take=oldest 反向取页、级别/模块/关键词筛选、落盘目录缺失降级、
坏行跳过、API 层参数契约与 persist_available 信号。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

import pytest
from fastapi.testclient import TestClient

from src.modules.dashboard.services.log_query import LogHistoryReader

_DAY_DIR = "2026-10-17"


def _ms(hour: int, minute: int = 0, second: int = 0) -> int:
    """测试固定时刻 → Unix 毫秒（与读取面同一 fromisoformat 口径，时区无关）。"""
    return int(datetime(2026, 10, 17, hour, minute, second).timestamp() * 1000)


def _iso(hour: int, minute: int = 0, second: int = 0) -> str:
    return f"2026-10-17T{hour:02d}:{minute:02d}:{second:02d}"


def _entry(hour: int, minute: int = 0, level: str = "INFO", module: str = "ModA", message: str = "hello") -> dict:
    return {"timestamp": _iso(hour, minute), "level": level, "module": module, "message": message}


def _write_day_file(root: Path, entries: List[dict], filename: str = f"{_DAY_DIR}.jsonl") -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(root / filename, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _timestamps(entries: List[dict]) -> List[str]:
    return [entry["timestamp"] for entry in entries]


# ------------------------------------------------------------------ #
# 服务层（LogHistoryReader）                                          #
# ------------------------------------------------------------------ #


def test_window_query_returns_entries_in_ascending_order(tmp_path: Path) -> None:
    root = tmp_path / "webui"
    _write_day_file(root, [_entry(10), _entry(11), _entry(12)])
    reader = LogHistoryReader(root)

    window = reader.query(start_ms=_ms(9), end_ms=_ms(13), limit=10)

    assert _timestamps(window.entries) == [_iso(10), _iso(11), _iso(12)]
    assert window.has_more_older is False
    assert window.has_more_newer is False


def test_before_cursor_pages_backwards_with_has_more(tmp_path: Path) -> None:
    root = tmp_path / "webui"
    _write_day_file(root, [_entry(10), _entry(11), _entry(12), _entry(13)])
    reader = LogHistoryReader(root)

    # 窗口 [9,14) 游标 13:00：取 13:00 之前最靠新的 2 条（11、12），更早还有 → has_more
    window = reader.query(start_ms=_ms(9), end_ms=_ms(14), before_timestamp_ms=_ms(13), limit=2)
    assert _timestamps(window.entries) == [_iso(11), _iso(12)]
    assert window.has_more_older is True

    # 继续翻：游标 11:00 → 只剩 10:00，已到窗口起点
    window = reader.query(start_ms=_ms(9), end_ms=_ms(14), before_timestamp_ms=_ms(11), limit=2)
    assert _timestamps(window.entries) == [_iso(10)]
    assert window.has_more_older is False


def test_take_oldest_returns_first_page_with_has_more_newer(tmp_path: Path) -> None:
    root = tmp_path / "webui"
    _write_day_file(root, [_entry(10), _entry(11), _entry(12)])
    reader = LogHistoryReader(root)

    window = reader.query(start_ms=_ms(9), end_ms=_ms(13), take="oldest", limit=2)

    assert _timestamps(window.entries) == [_iso(10), _iso(11)]
    assert window.has_more_newer is True
    assert window.has_more_older is False


def test_level_module_and_keyword_filters(tmp_path: Path) -> None:
    root = tmp_path / "webui"
    _write_day_file(
        root,
        [
            _entry(10, level="INFO", module="ModA", message="started fine"),
            _entry(11, level="WARNING", module="ModB", message="disk almost full"),
            _entry(12, level="ERROR", module="ModA", message="Disk failure"),
        ],
    )
    reader = LogHistoryReader(root)

    warn_error = reader.query(start_ms=_ms(9), end_ms=_ms(13), levels={"WARNING", "ERROR"}, limit=10)
    assert _timestamps(warn_error.entries) == [_iso(11), _iso(12)]

    mod_b = reader.query(start_ms=_ms(9), end_ms=_ms(13), modules={"ModB"}, limit=10)
    assert _timestamps(mod_b.entries) == [_iso(11)]

    # 关键词大小写不敏感，命中正文或模块
    disk = reader.query(start_ms=_ms(9), end_ms=_ms(13), keyword="disk", limit=10)
    assert _timestamps(disk.entries) == [_iso(11), _iso(12)]


def test_missing_persist_dir_degrades_to_empty(tmp_path: Path) -> None:
    reader = LogHistoryReader(tmp_path / "nonexistent")

    assert reader.persist_available is False
    window = reader.query(start_ms=_ms(9), end_ms=_ms(13), limit=10)
    assert window.entries == []
    assert window.has_more_older is False


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "webui"
    root.mkdir(parents=True)
    with open(root / f"{_DAY_DIR}.jsonl", "w", encoding="utf-8") as f:
        f.write("not-json\n")
        f.write(json.dumps({"level": "INFO"}) + "\n")  # 缺必需字段
        f.write(json.dumps(_entry(10)) + "\n")
    reader = LogHistoryReader(root)

    window = reader.query(start_ms=_ms(9), end_ms=_ms(13), limit=10)
    assert _timestamps(window.entries) == [_iso(10)]


# ------------------------------------------------------------------ #
# API 层（GET /api/v1/logs）                                          #
# ------------------------------------------------------------------ #


class _StubLogStreamer:
    """最小 LogStreamer 替身：仅暴露查询面读取的 persist_dir。"""

    def __init__(self, persist_dir: Optional[Path]) -> None:
        self._persist_dir = persist_dir

    @property
    def persist_dir(self) -> Optional[Path]:
        return self._persist_dir


@pytest.fixture
def api_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    server = DashboardServer(
        event_bus=None,
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )
    root = tmp_path / "webui"
    _write_day_file(root, [_entry(10), _entry(11, level="WARNING", module="ModB", message="boom")])
    server.log_streamer = _StubLogStreamer(root)  # type: ignore[assignment]
    set_dashboard_server(server)
    with TestClient(create_app()) as c:
        yield c
    set_dashboard_server(None)


def test_logs_api_returns_window_and_echoes_bounds(api_client: TestClient, tmp_path: Path) -> None:
    body = api_client.get(
        "/api/v1/logs",
        params={"start_ms": _ms(9), "end_ms": _ms(13), "levels": "warning", "limit": 200},
    ).json()

    assert body["persist_available"] is True
    assert body["window_start_ms"] == _ms(9)
    assert _timestamps([dict(e) for e in body["entries"]]) == [_iso(11)]
    assert body["total"] == 1


def test_logs_api_without_log_streamer_reports_unavailable() -> None:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    server = DashboardServer(
        event_bus=None,
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )
    set_dashboard_server(server)
    try:
        with TestClient(create_app()) as client:
            body = client.get("/api/v1/logs", params={"start_ms": _ms(9), "end_ms": _ms(13)}).json()
            assert body["persist_available"] is False
            assert body["entries"] == []
    finally:
        set_dashboard_server(None)


def test_logs_api_param_validation(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/logs", params={"limit": 501}).status_code == 422
    assert api_client.get("/api/v1/logs", params={"take": "middle"}).status_code == 422


def test_logs_api_paging_cursor(api_client: TestClient) -> None:
    body = api_client.get(
        "/api/v1/logs",
        params={"start_ms": _ms(9), "end_ms": _ms(13), "before_timestamp_ms": _ms(11), "limit": 200},
    ).json()
    assert _timestamps([dict(e) for e in body["entries"]]) == [_iso(10)]
    assert body["has_more_older"] is False
