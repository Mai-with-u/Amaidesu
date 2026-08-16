"""Dashboard 大纲 API 调试可观察性测试（T12）

覆盖 ``/api/v1/outline/{state, transitions}`` 端点 + ``/outline/segments`` 附加 expanded 字段：

1. **state 透传**：响应字段 == :meth:`OutlineState.get_snapshot()` 全部字段（含
   ``needs_expansion`` / ``manually_overridden`` 等），不做白名单裁剪
2. **transitions 端点**：成功返回 ``{loaded, transitions}``；未实现时 501
3. **segments 附 expanded**：有 expanded_cache 时附带 ``expanded`` 字段 =
   ``model_dump()`` 后的 dict；无缓存时为 ``None``

参考：
- src/modules/dashboard/api/outline.py
- src/stages/decision/manager.py（_dispatch_outline_call 鸭子类型分发）
- src/stages/decision/deciders/amaidesu/amaidesu_decider.py
  （outline_state / outline_transitions / outline_segments 实现）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _FakeExpanded:
    """模拟 ExpandedSegment-shape 对象（带 model_dump 方法，Pydantic 协议）"""

    def __init__(self, segment_id: str, opening_line: str, topic_guidance: str, talking_points: list):
        self.segment_id = segment_id
        self.opening_line = opening_line
        self.topic_guidance = topic_guidance
        self.talking_points = talking_points

    def model_dump(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "opening_line": self.opening_line,
            "topic_guidance": self.topic_guidance,
            "talking_points": list(self.talking_points),
        }


# ===========================================================================
# 共享 fixture：最小化内存结构 + 模拟 dashboard client
# ===========================================================================


_CORE_TOML = """\
type = "core"

[meta]
type = "meta"
version = "0.4.0"

[general]
type = "general"
platform_id = "amaidesu"

[persona]
type = "persona"
bot_name = "麦麦"
personality = "活泼开朗"

[maicore]
type = "maicore"
host = "127.0.0.1"
port = 8000
token = ""

[context]
type = "context"
storage_type = "memory"
max_messages_per_session = 50

[dashboard]
type = "dashboard"
enabled = true
host = "127.0.0.1"
port = 60214

[logging]
type = "logging"
enabled = true
format = "jsonl"
level = "INFO"
"""

_MODEL_TOML = """\
type = "model"

[[llm_providers]]
name = "default"
client_type = "openai"
base_url = "https://api.openai.com/v1"
api_key = ""
timeout = 60
max_retries = 3
retry_delay = 1.0

[llm]
provider = "default"
model = "gpt-4"
temperature = 0.2

[llm_fast]
provider = "default"
model = "gpt-3.5-turbo"

[vlm]
provider = "default"
model = "gpt-4-vision-preview"

[llm_local]
provider = "default"
model = "llama3"
base_url = "http://localhost:11434/v1"
api_key = "sk-dummy"
"""

_INPUT_TOML = """\
type = "input"

[collectors]
enabled = ["console_input"]

[collectors.console_input]
type = "console_input"
user_id = "console_user"
user_nickname = "控制台"
"""

_DECISION_TOML = """\
type = "decision"

[deciders]
enabled = ["maidesu"]

[deciders.amaidesu]
type = "amaidesu"
batch_window_ms = 3000
"""

_OUTPUT_TOML = """\
type = "output"

[handlers]
enabled = ["subtitle"]

[handlers.subtitle]
type = "subtitle"
font_size = 28
window_width = 800
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    (cfg / "model.toml").write_text(_MODEL_TOML, encoding="utf-8")
    (cfg / "input.toml").write_text(_INPUT_TOML, encoding="utf-8")
    (cfg / "decision.toml").write_text(_DECISION_TOML, encoding="utf-8")
    (cfg / "output.toml").write_text(_OUTPUT_TOML, encoding="utf-8")
    return cfg


@pytest.fixture
def config_service(config_dir: Path):
    from src.modules.config.service import ConfigService

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return svc


@pytest.fixture
def dashboard_server(config_service):
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    cfg = DashboardConfig(host="127.0.0.1", port=60214)
    server = DashboardServer(
        event_bus=None,  # type: ignore[arg-type]
        input_manager=None,
        decision_manager=None,
        output_manager=None,
        context_service=None,  # type: ignore[arg-type]
        config_service=config_service,
        dashboard_config=cfg,
    )
    set_dashboard_server(server)
    yield server
    set_dashboard_server(None)  # type: ignore[arg-type]


@pytest.fixture
def client(dashboard_server, config_service):
    from fastapi.testclient import TestClient

    from src.modules.dashboard.api.router import create_app

    app = create_app()
    return TestClient(app)


def _make_snapshot(**overrides) -> dict:
    """构造一个完整的 OutlineState.get_snapshot() 风格 dict（含全部字段）"""
    base = {
        "status": "running",
        "current_segment": {
            "id": "intro",
            "title": "开场",
            "duration_ms": 60_000,
            "elapsed_ms": 15_000,
            "remaining_ms": 45_000,
            "expanded": True,
            "needs_expansion": False,
        },
        "next_segment": {"id": "main", "title": "主体"},
        "completed_count": 1,
        "total_count": 3,
        "is_paused": False,
        "elapsed_live_ms": 120_000,
        "total_planned_ms": 600_000,
        "progress_percent": 20.0,
        "outline_id": "outline-demo",
        "outline_title": "演示大纲",
        "manually_overridden": False,
    }
    base.update(overrides)
    return base


# ===========================================================================
# /api/v1/outline/state 透传测试
# ===========================================================================


class TestOutlineStatePassthrough:
    """``GET /api/v1/outline/state`` 透传 snapshot 全部字段"""

    def test_state_passthrough_returns_all_snapshot_fields(self, client, dashboard_server):
        """响应包含 snapshot 全部字段（不被裁剪/重映射）"""
        manager = AsyncMock()
        manager.outline_state = AsyncMock(return_value=_make_snapshot())
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/state")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # 关键字段全部透传
        assert body["status"] == "running"
        assert body["current_segment"]["id"] == "intro"
        assert body["current_segment"]["title"] == "开场"
        assert body["current_segment"]["duration_ms"] == 60_000
        assert body["current_segment"]["elapsed_ms"] == 15_000
        assert body["current_segment"]["remaining_ms"] == 45_000
        assert body["current_segment"]["expanded"] is True
        assert body["current_segment"]["needs_expansion"] is False
        assert body["next_segment"] == {"id": "main", "title": "主体"}
        assert body["completed_count"] == 1
        assert body["total_count"] == 3
        assert body["is_paused"] is False
        assert body["elapsed_live_ms"] == 120_000
        assert body["total_planned_ms"] == 600_000
        assert body["progress_percent"] == 20.0
        assert body["outline_id"] == "outline-demo"
        assert body["outline_title"] == "演示大纲"
        assert body["manually_overridden"] is False

    def test_state_does_not_include_legacy_live_progress(self, client, dashboard_server):
        """T12 重构：响应当中不再包含 live_progress / expanded_ready 字段"""
        manager = AsyncMock()
        manager.outline_state = AsyncMock(return_value=_make_snapshot())
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/state")
        body = resp.json()
        assert "live_progress" not in body
        assert "expanded_ready" not in body

    def test_state_passes_through_needs_expansion(self, client, dashboard_server):
        """needs_expansion 调试字段被透传（不再被剥夺）"""
        manager = AsyncMock()
        manager.outline_state = AsyncMock(
            return_value=_make_snapshot(
                current_segment={
                    "id": "main",
                    "title": "主体",
                    "duration_ms": 60_000,
                    "elapsed_ms": 5_000,
                    "remaining_ms": 55_000,
                    "expanded": False,
                    "needs_expansion": True,
                },
            )
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/state")
        body = resp.json()
        assert body["current_segment"]["needs_expansion"] is True
        assert body["current_segment"]["expanded"] is False

    def test_state_501_when_not_implemented(self, client, dashboard_server):
        """Decider 无 outline_state 实现 → 501"""
        manager = AsyncMock()
        manager.outline_state = AsyncMock(
            return_value={
                "error": "not_implemented",
                "status_code": 501,
                "method": "outline_state",
                "message": "大纲未激活",
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/state")
        assert resp.status_code == 501
        assert "大纲" in resp.json()["detail"] or "未实现" in resp.json()["detail"]

    def test_state_404_when_manager_missing(self, client, dashboard_server):
        """dashboard_server.decision_manager=None → 404"""
        assert dashboard_server.decision_manager is None
        resp = client.get("/api/v1/outline/state")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "决策器未加载"}


# ===========================================================================
# /api/v1/outline/transitions 端点测试
# ===========================================================================


class TestOutlineTransitionsEndpoint:
    """``GET /api/v1/outline/transitions`` 推进历史端点"""

    def test_transitions_returns_loaded_and_list(self, client, dashboard_server):
        """正常路径：返回 {loaded: True, transitions: [...]}"""
        manager = AsyncMock()
        manager.outline_transitions = AsyncMock(
            return_value={
                "loaded": True,
                "transitions": [
                    {
                        "segment_id": "intro",
                        "title": "开场",
                        "reason": "start",
                        "at_ms": 1000,
                        "stayed_ms": None,
                    },
                    {
                        "segment_id": "main",
                        "title": "主体",
                        "reason": "outline:time",
                        "at_ms": 60000,
                        "stayed_ms": 59000,
                    },
                ],
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/transitions")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["loaded"] is True
        assert len(body["transitions"]) == 2
        assert body["transitions"][0]["reason"] == "start"
        assert body["transitions"][1]["reason"] == "outline:time"
        assert body["transitions"][1]["stayed_ms"] == 59000

    def test_transitions_empty_when_unloaded(self, client, dashboard_server):
        """大纲未激活时：loaded=False + 空列表"""
        manager = AsyncMock()
        manager.outline_transitions = AsyncMock(
            return_value={"loaded": False, "transitions": []}
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/transitions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["loaded"] is False
        assert body["transitions"] == []

    def test_transitions_501_when_not_implemented(self, client, dashboard_server):
        """Decider 没实现 outline_transitions → 501"""
        manager = AsyncMock()
        manager.outline_transitions = AsyncMock(
            return_value={
                "error": "not_implemented",
                "status_code": 501,
                "method": "outline_transitions",
                "message": "大纲接口尚未由任何已激活的 Decider 实现。",
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/transitions")
        assert resp.status_code == 501

    def test_transitions_404_when_manager_missing(self, client, dashboard_server):
        """dashboard_server.decision_manager=None → 404"""
        assert dashboard_server.decision_manager is None
        resp = client.get("/api/v1/outline/transitions")
        assert resp.status_code == 404

    def test_transitions_500_when_decider_returns_non_dict(self, client, dashboard_server):
        """Decider 返回非 dict → 500"""
        manager = AsyncMock()
        manager.outline_transitions = AsyncMock(return_value="not a dict")  # type: ignore[arg-type]
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/transitions")
        assert resp.status_code == 500
        assert "格式错误" in resp.json()["detail"]


# ===========================================================================
# /api/v1/outline/segments 附加 expanded 测试
# ===========================================================================


def _make_segment_dict(
    *,
    seg_id: str,
    title: str,
    duration_ms: int = 60_000,
    task_description: str = "",
    key_points: list | None = None,
    branches: list | None = None,
    min_duration_ms: int | None = None,
):
    """构造 OutlineSegment-style simple namespace（API 层做 duck-typed 序列化）"""
    return SimpleNamespace(
        id=seg_id,
        title=title,
        task_description=task_description,
        duration_ms=duration_ms,
        min_duration_ms=min_duration_ms,
        key_points=key_points or [],
        branches=branches or [],
    )


class TestOutlineSegmentsExpanded:
    """``GET /api/v1/outline/segments`` 附 expanded 字段"""

    def test_segments_attached_with_expanded_when_cached(self, client, dashboard_server):
        """有 expanded_cache 时，segments 每段附 expanded 字段（model_dump 后的 dict）"""
        # 构造 ExpandedSegment-like 对象（带 model_dump）
        expanded_intro = _FakeExpanded(
            segment_id="intro",
            opening_line="大家好，欢迎来到我的直播间！",
            topic_guidance="开场寒暄",
            talking_points=["问候", "引入话题"],
        )
        expanded_main = _FakeExpanded(
            segment_id="main",
            opening_line="",
            topic_guidance="主体内容",
            talking_points=["要点1"],
        )

        segs = [
            _make_segment_dict(seg_id="intro", title="开场"),
            _make_segment_dict(seg_id="main", title="主体"),
        ]
        manager = AsyncMock()
        manager.outline_segments = AsyncMock(
            return_value={
                "loaded": True,
                "outline_id": "outline-x",
                "title": "测试大纲",
                "fallback_segment_id": None,
                "path": "/tmp/outline.toml",
                "segments": segs,
                "expanded_cache": {
                    "intro": expanded_intro,
                    "main": expanded_main,
                },
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/segments")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["loaded"] is True
        assert len(body["segments"]) == 2

        # 第一个段：expanded 含 model_dump 内容
        assert body["segments"][0]["id"] == "intro"
        assert body["segments"][0]["expanded"] is not None
        assert body["segments"][0]["expanded"]["segment_id"] == "intro"
        assert body["segments"][0]["expanded"]["opening_line"] == "大家好，欢迎来到我的直播间！"
        assert body["segments"][0]["expanded"]["topic_guidance"] == "开场寒暄"
        assert body["segments"][0]["expanded"]["talking_points"] == ["问候", "引入话题"]

        # 第二个段：expanded 含 model_dump 内容
        assert body["segments"][1]["expanded"]["topic_guidance"] == "主体内容"
        assert body["segments"][1]["expanded"]["talking_points"] == ["要点1"]

    def test_segments_expanded_none_when_cache_miss(self, client, dashboard_server):
        """缓存未命中时，对应段 expanded=None"""
        segs = [
            _make_segment_dict(seg_id="a", title="A"),
            _make_segment_dict(seg_id="b", title="B"),
        ]
        manager = AsyncMock()
        manager.outline_segments = AsyncMock(
            return_value={
                "loaded": True,
                "outline_id": "outline-y",
                "title": "测试大纲",
                "fallback_segment_id": None,
                "path": "/tmp/outline.toml",
                "segments": segs,
                "expanded_cache": {},  # 空缓存
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/segments")
        body = resp.json()
        assert body["segments"][0]["expanded"] is None
        assert body["segments"][1]["expanded"] is None

    def test_segments_expanded_partial_cache(self, client, dashboard_server):
        """部分缓存命中：未命中段 expanded=None"""
        expanded_a = _FakeExpanded(
            segment_id="a",
            opening_line="A开场",
            topic_guidance="A 引导",
            talking_points=["A1"],
        )
        segs = [
            _make_segment_dict(seg_id="a", title="A"),
            _make_segment_dict(seg_id="b", title="B"),
        ]
        manager = AsyncMock()
        manager.outline_segments = AsyncMock(
            return_value={
                "loaded": True,
                "outline_id": "outline-z",
                "title": "测试大纲",
                "fallback_segment_id": None,
                "path": "/tmp/outline.toml",
                "segments": segs,
                "expanded_cache": {"a": expanded_a},
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/segments")
        body = resp.json()
        assert body["segments"][0]["expanded"] is not None
        assert body["segments"][0]["expanded"]["opening_line"] == "A开场"
        assert body["segments"][1]["expanded"] is None

    def test_segments_no_expanded_cache_field_at_all(self, client, dashboard_server):
        """Decider 未提供 expanded_cache 字段 → 所有段 expanded=None"""
        segs = [
            _make_segment_dict(seg_id="a", title="A"),
        ]
        manager = AsyncMock()
        manager.outline_segments = AsyncMock(
            return_value={
                "loaded": True,
                "outline_id": "outline-w",
                "title": "测试大纲",
                "fallback_segment_id": None,
                "path": "/tmp/outline.toml",
                "segments": segs,
                # 无 expanded_cache 字段
            }
        )
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.get("/api/v1/outline/segments")
        assert resp.status_code == 200
        assert resp.json()["segments"][0]["expanded"] is None
