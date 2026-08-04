"""Dashboard 主动发言 API 测试套件 (Task 7)

覆盖 ``POST /api/v1/proactive/speak`` 端点：

1. **正常路径**：注入 mock ``decision_manager`` + ``AsyncMock``，POST 含 ``topic_hint``
   → 200，返回 ``status="queued"`` / ``deciders`` / ``topic_hint`` 三字段
2. **manager 缺失**：``decision_manager=None`` → 404，返回 ``{"detail": "决策器未加载"}``
3. **超长 topic_hint**：201+ 字符 → 422（Pydantic ``max_length=200`` 校验）

参考：
- src/modules/dashboard/api/proactive.py
- src/modules/dashboard/server.py（decision_manager 字段）
- src/stages/decision/manager.py:trigger_proactive（Task 5 实现）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


# ===========================================================================
# Fixtures
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
enabled = ["maibot"]

[deciders.llm]
type = "llm"
client = "llm"

[deciders.maibot]
type = "maibot"
host = "localhost"
port = 8000
"""

_OUTPUT_TOML = """\
type = "output"

[handlers]
enabled = ["subtitle"]

[handlers.subtitle]
type = "subtitle"
font_size = 28
window_width = 800

[handlers.vts]
type = "vts"
vts_host = "localhost"
vts_port = 8001
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """生成包含 5 个 TOML 的临时 config/ 目录"""
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
    """已 initialize() 的 ConfigService"""
    from src.modules.config.service import ConfigService

    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return svc


@pytest.fixture
def dashboard_server(config_service):
    """构造最小化 DashboardServer（decision_manager=None，由各测试按需替换）"""
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
    """FastAPI TestClient（仅路由 + 依赖，不启动 uvicorn）"""
    from fastapi.testclient import TestClient

    from src.modules.dashboard.api.router import create_app

    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_decision_manager():
    """构造一个最小化 mock decision_manager（带 AsyncMock trigger_proactive）"""
    manager = AsyncMock()
    manager.trigger_proactive = AsyncMock(return_value=["amaidesu_decider"])
    return manager


# ===========================================================================
# Tests
# ===========================================================================


class TestProactiveSpeakEndpoint:
    """POST /api/v1/proactive/speak 行为覆盖"""

    def test_speak_with_topic_hint_returns_200(self, client, dashboard_server, mock_decision_manager):
        """正常路径：POST 含 topic_hint → 200 + status=queued + deciders 列表"""
        dashboard_server.decision_manager = mock_decision_manager  # type: ignore[assignment]

        resp = client.post("/api/v1/proactive/speak", json={"topic_hint": "测试"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["topic_hint"] == "测试"
        assert isinstance(body["deciders"], list)
        assert body["deciders"] == ["amaidesu_decider"]

    def test_speak_calls_trigger_proactive_with_topic_hint(
        self, client, dashboard_server, mock_decision_manager
    ):
        """POST 时必须把 topic_hint 透传给 trigger_proactive"""
        dashboard_server.decision_manager = mock_decision_manager  # type: ignore[assignment]

        client.post("/api/v1/proactive/speak", json={"topic_hint": "今天天气"})

        mock_decision_manager.trigger_proactive.assert_awaited_once_with("今天天气")

    def test_speak_without_topic_hint_passes_none(
        self, client, dashboard_server, mock_decision_manager
    ):
        """缺省 body → trigger_proactive(None)"""
        dashboard_server.decision_manager = mock_decision_manager  # type: ignore[assignment]

        resp = client.post("/api/v1/proactive/speak", json={})

        assert resp.status_code == 200
        mock_decision_manager.trigger_proactive.assert_awaited_once_with(None)

    def test_speak_returns_deciders_from_manager(self, client, dashboard_server):
        """响应中的 deciders 必须等于 manager 的返回值"""
        manager = AsyncMock()
        manager.trigger_proactive = AsyncMock(return_value=["amaidesu", "maibot"])
        dashboard_server.decision_manager = manager  # type: ignore[assignment]

        resp = client.post("/api/v1/proactive/speak", json={"topic_hint": "abc"})

        assert resp.status_code == 200
        assert resp.json()["deciders"] == ["amaidesu", "maibot"]


class TestProactiveSpeakMissingManager:
    """decision_manager 为 None 时必须返回 404 + 中文 detail"""

    def test_speak_without_manager_returns_404(self, client, dashboard_server):
        """dashboard_server.decision_manager=None → 404"""
        # dashboard_server 默认 decision_manager=None（fixture 默认值）
        assert dashboard_server.decision_manager is None

        resp = client.post("/api/v1/proactive/speak", json={"topic_hint": "测试"})

        assert resp.status_code == 404
        assert resp.json() == {"detail": "决策器未加载"}

    def test_speak_without_manager_does_not_call(self, client, dashboard_server):
        """manager 缺失时不能触发任何调用（连 AsyncMock 都没有）"""
        resp = client.post("/api/v1/proactive/speak", json={})

        assert resp.status_code == 404


class TestProactiveSpeakValidation:
    """Pydantic 入参校验"""

    def test_speak_with_too_long_topic_hint_returns_422(self, client, dashboard_server, mock_decision_manager):
        """topic_hint 长度 > 200 → 422（max_length=200 校验）"""
        dashboard_server.decision_manager = mock_decision_manager  # type: ignore[assignment]

        long_hint = "x" * 201
        resp = client.post("/api/v1/proactive/speak", json={"topic_hint": long_hint})

        assert resp.status_code == 422
        # 不能调用 manager
        mock_decision_manager.trigger_proactive.assert_not_called()

    def test_speak_with_exact_200_chars_passes(self, client, dashboard_server, mock_decision_manager):
        """topic_hint 恰好 200 字符 → 通过校验（边界值）"""
        dashboard_server.decision_manager = mock_decision_manager  # type: ignore[assignment]

        boundary_hint = "y" * 200
        resp = client.post("/api/v1/proactive/speak", json={"topic_hint": boundary_hint})

        assert resp.status_code == 200
        assert resp.json()["topic_hint"] == boundary_hint
        mock_decision_manager.trigger_proactive.assert_awaited_once_with(boundary_hint)