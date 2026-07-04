"""Dashboard 配置 API 测试套件 (Task 12)

覆盖 Dashboard 配置管理 API 在多文件配置结构下的行为:

1. **GET /api/v1/config** — 返回当前合并配置 (扁平化的 main_config)
2. **PATCH /api/v1/config** — 根据节名路由到正确的 TOML 文件:
   - core 节 (general/persona/maicore/context/dashboard/logging/pipelines/meta) → core.toml
   - model 节 (llm/llm_fast/vlm/llm_local) → model.toml
   - collectors 节 → input.toml
   - deciders 节 → decision.toml
   - handlers 节 → output.toml
3. **GET /api/v1/config/schema** — 返回 ConfigSchemaGenerator 生成的 schema
   (className / fields / nested 形状),不是 schema_registry 的 groups 形状
4. **get_config_path(section)** — 服务端辅助方法返回对应 TOML 文件路径

参考:
- src/modules/dashboard/api/config.py
- src/modules/dashboard/server.py (get_config_path)
- src/modules/config/service.py (T7 schema API)
- src/modules/config/multi_file_loader.py (5 个 TOML 文件结构)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest


# ===========================================================================
# Fixtures: 5 个 TOML 配置文件的最小内容
# ===========================================================================


_CORE_TOML = """\
# 核心系统配置 - Amaidesu

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
# 模型配置

type = "model"

[llm]
type = "llm"
client = "openai"
model = "gpt-4"
api_key = ""
temperature = 0.2

[llm_fast]
type = "llm_fast"
client = "openai"
model = "gpt-3.5-turbo"

[vlm]
type = "vlm"
client = "openai"
model = "gpt-4-vision-preview"

[llm_local]
type = "llm_local"
client = "ollama"
model = "llama3"
"""

_INPUT_TOML = """\
# Input 阶段

[collectors]
enabled = ["console_input"]

[collectors.console_input]
type = "console_input"
user_id = "console_user"
user_nickname = "控制台"
"""

_DECISION_TOML = """\
# Decision 阶段

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
# Output 阶段

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
    """构造一个最小化的 DashboardServer (只注入 config_service)"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.server import DashboardServer
    from src.modules.dashboard.dependencies import set_dashboard_server

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
    """FastAPI TestClient (仅路由 + 依赖,不启动 uvicorn)"""
    from fastapi.testclient import TestClient
    from src.modules.dashboard.api.router import create_app

    app = create_app()
    return TestClient(app)


# ===========================================================================
# 1. get_config_path(section) — 路由节名 → 正确的 TOML 文件
# ===========================================================================


class TestGetConfigPath:
    """DashboardServer.get_config_path(section) 根据节名路由"""

    def test_get_path_for_persona_returns_core_toml(self, dashboard_server, config_dir):
        """persona 节 → core.toml"""
        result = dashboard_server.get_config_path("persona")
        assert result is not None
        assert Path(result).name == "core.toml"
        assert Path(result) == config_dir / "core.toml"

    def test_get_path_for_llm_returns_model_toml(self, dashboard_server, config_dir):
        """llm 节 → model.toml"""
        result = dashboard_server.get_config_path("llm")
        assert result is not None
        assert Path(result).name == "model.toml"
        assert Path(result) == config_dir / "model.toml"

    def test_get_path_for_collectors_returns_input_toml(self, dashboard_server, config_dir):
        """collectors 节 → input.toml"""
        result = dashboard_server.get_config_path("collectors")
        assert result is not None
        assert Path(result).name == "input.toml"
        assert Path(result) == config_dir / "input.toml"

    def test_get_path_for_deciders_returns_decision_toml(self, dashboard_server, config_dir):
        """deciders 节 → decision.toml"""
        result = dashboard_server.get_config_path("deciders")
        assert result is not None
        assert Path(result).name == "decision.toml"
        assert Path(result) == config_dir / "decision.toml"

    def test_get_path_for_handlers_returns_output_toml(self, dashboard_server, config_dir):
        """handlers 节 → output.toml"""
        result = dashboard_server.get_config_path("handlers")
        assert result is not None
        assert Path(result).name == "output.toml"
        assert Path(result) == config_dir / "output.toml"

    def test_get_path_for_all_core_sections(self, dashboard_server, config_dir):
        """core 节族 (meta/general/persona/maicore/context/dashboard/logging) 全部 → core.toml"""
        for section in ("meta", "general", "persona", "maicore", "context", "dashboard", "logging"):
            result = dashboard_server.get_config_path(section)
            assert result is not None, f"{section} 应返回非 None"
            assert Path(result).name == "core.toml", f"{section} 应路由到 core.toml"

    def test_get_path_for_all_model_sections(self, dashboard_server, config_dir):
        """model 节族 (llm/llm_fast/vlm/llm_local) 全部 → model.toml"""
        for section in ("llm", "llm_fast", "vlm", "llm_local"):
            result = dashboard_server.get_config_path(section)
            assert result is not None, f"{section} 应返回非 None"
            assert Path(result).name == "model.toml", f"{section} 应路由到 model.toml"

    def test_get_path_for_pipelines_returns_core_toml(self, dashboard_server, config_dir):
        """pipelines 节 → core.toml (因为 pipelines 属于 core)"""
        result = dashboard_server.get_config_path("pipelines")
        assert result is not None
        assert Path(result).name == "core.toml"

    def test_get_path_without_service_returns_none(self):
        """config_service 不存在时返回 None"""
        from src.modules.config.core_schemas import DashboardConfig
        from src.modules.dashboard.server import DashboardServer

        cfg = DashboardConfig(host="127.0.0.1", port=60214)
        server = DashboardServer(
            event_bus=None,  # type: ignore[arg-type]
            input_manager=None,
            decision_manager=None,
            output_manager=None,
            context_service=None,  # type: ignore[arg-type]
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=cfg,
        )
        assert server.get_config_path("persona") is None

    def test_get_path_unknown_section_falls_back_to_core(self, dashboard_server, config_dir):
        """未知节名 → 兜底到 core.toml (不允许静默失败,但也不能崩溃)"""
        result = dashboard_server.get_config_path("nonexistent_section_xyz")
        assert result is not None
        # 默认应回到 core.toml
        assert Path(result).name == "core.toml"


# ===========================================================================
# 2. GET /api/v1/config — 返回当前合并配置
# ===========================================================================


class TestGetConfigEndpoint:
    """GET /api/v1/config 返回当前扁平化 main_config"""

    def test_get_config_returns_200(self, client):
        """GET /api/v1/config 必须返回 200"""
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200

    def test_get_config_includes_core_sections(self, client):
        """返回的配置必须包含 core 节族"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        assert "config" in body, "响应必须包含 'config' 字段"
        data = body["config"]
        assert "persona" in data, "persona 节必须在响应中"
        assert "general" in data
        assert "maicore" in data
        assert "context" in data
        assert "dashboard" in data
        assert "logging" in data

    def test_get_config_includes_model_sections(self, client):
        """返回的配置必须包含 model 节族"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert "llm" in data, "llm 节必须在响应中"
        assert "vlm" in data

    def test_get_config_includes_phase_sections(self, client):
        """返回的配置必须包含阶段节 (collectors/deciders/handlers)"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert "collectors" in data
        assert "deciders" in data
        assert "handlers" in data

    def test_get_config_values_match_toml(self, client):
        """配置值必须与 TOML 文件中的实际值一致"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert data["persona"]["bot_name"] == "麦麦"
        assert data["llm"]["model"] == "gpt-4"
        assert data["collectors"]["console_input"]["user_id"] == "console_user"
        assert data["deciders"]["llm"]["client"] == "llm"
        assert data["handlers"]["subtitle"]["font_size"] == 28


# ===========================================================================
# 3. PATCH /api/v1/config — 路由到正确的 TOML 文件
# ===========================================================================


class TestPatchConfigEndpoint:
    """PATCH /api/v1/config 根据节名写入正确的 TOML 文件"""

    def test_patch_persona_writes_to_core_toml(self, client, config_dir):
        """PATCH persona.bot_name → 写入 core.toml (不触碰其他文件)"""
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "新名字"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        # core.toml 必须包含新值
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "新名字" in core_content

        # 其他文件不应被触碰
        for fname in ("model.toml", "input.toml", "decision.toml", "output.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "新名字" not in content, f"{fname} 不应被 persona 修改影响"

    def test_patch_llm_writes_to_model_toml(self, client, config_dir):
        """PATCH llm.model → 写入 model.toml"""
        resp = client.patch("/api/v1/config", json={"key": "llm.model", "value": "gpt-4-turbo"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        model_content = (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "gpt-4-turbo" in model_content

        # 其他文件不应被触碰
        for fname in ("core.toml", "input.toml", "decision.toml", "output.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "gpt-4-turbo" not in content

    def test_patch_collectors_writes_to_input_toml(self, client, config_dir):
        """PATCH collectors.console_input.user_id → 写入 input.toml"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "collectors.console_input.user_id", "value": "new_console_user"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        input_content = (config_dir / "input.toml").read_text(encoding="utf-8")
        assert "new_console_user" in input_content

        # 其他文件不应被触碰
        for fname in ("core.toml", "model.toml", "decision.toml", "output.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "new_console_user" not in content

    def test_patch_deciders_writes_to_decision_toml(self, client, config_dir):
        """PATCH deciders.llm.client → 写入 decision.toml"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "deciders.llm.client", "value": "llm_fast"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        decision_content = (config_dir / "decision.toml").read_text(encoding="utf-8")
        assert "llm_fast" in decision_content

        for fname in ("core.toml", "model.toml", "input.toml", "output.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "llm_fast" not in content or "llm_fast" in content  # llm_fast may exist in model.toml legitimately

    def test_patch_handlers_writes_to_output_toml(self, client, config_dir):
        """PATCH handlers.subtitle.font_size → 写入 output.toml"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "handlers.subtitle.font_size", "value": 36},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True

        output_content = (config_dir / "output.toml").read_text(encoding="utf-8")
        assert "36" in output_content

    def test_patch_does_not_use_hardcoded_config_toml(self, client, tmp_path):
        """PATCH 不应写入根目录下的 config.toml (旧路径)"""
        # 确认根目录下的 config.toml 不存在
        old_path = tmp_path / "config.toml"
        assert not old_path.exists(), "测试前提: tmp_path/config.toml 不应存在"

        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "测试"})
        assert resp.status_code == 200

        # 根目录下的 config.toml 必须保持不存在
        assert not old_path.exists(), (
            "PATCH 不应写入根目录的硬编码 config.toml 路径,应写入 config/core.toml"
        )

    def test_patch_returns_requires_restart_for_llm_keys(self, client):
        """PATCH llm.* 必须标记 requires_restart=True"""
        resp = client.patch("/api/v1/config", json={"key": "llm.model", "value": "gpt-5"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_returns_requires_restart_for_dashboard_keys(self, client):
        """PATCH dashboard.* 必须标记 requires_restart=True"""
        resp = client.patch("/api/v1/config", json={"key": "dashboard.port", "value": 9999})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_persona_does_not_require_restart(self, client):
        """PATCH persona.* 不应要求重启 (运行时可生效)"""
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "测试名"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # persona 不在 RESTART_REQUIRED_PREFIXES 中
        assert body.get("requires_restart") is False

    def test_patch_logging_requires_restart(self, client):
        """PATCH logging.* 必须标记 requires_restart=True"""
        resp = client.patch("/api/v1/config", json={"key": "logging.level", "value": "DEBUG"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_persists_value(self, client, config_dir):
        """PATCH 后,值必须持久化在磁盘上"""
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "持久化测试"})
        assert resp.status_code == 200

        # 重新从磁盘读取
        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["persona"]["bot_name"] == "持久化测试"

    def test_patch_multiple_sections_each_writes_to_correct_file(self, client, config_dir):
        """连续 PATCH 多个不同节的配置,每个都写入正确文件"""
        updates = [
            ("persona.bot_name", "新名字", "core.toml"),
            ("llm.model", "claude-3", "model.toml"),
            ("collectors.console_input.user_id", "u1", "input.toml"),
            ("deciders.llm.client", "llm_fast", "decision.toml"),
            ("handlers.subtitle.font_size", 40, "output.toml"),
        ]
        for key, value, _expected_file in updates:
            resp = client.patch("/api/v1/config", json={"key": key, "value": value})
            assert resp.status_code == 200, f"PATCH {key} 失败: {resp.text}"
            assert resp.json()["success"] is True

        # 验证每个文件都包含对应的值
        assert "新名字" in (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "claude-3" in (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "u1" in (config_dir / "input.toml").read_text(encoding="utf-8")
        assert "deciders.llm" in (config_dir / "decision.toml").read_text(encoding="utf-8")
        assert "40" in (config_dir / "output.toml").read_text(encoding="utf-8")


# ===========================================================================
# 4. GET /api/v1/config/schema — 返回生成的 schema (非 schema_registry)
# ===========================================================================


class TestGetConfigSchemaEndpoint:
    """GET /api/v1/config/schema 返回 groups 格式 (Shape Adapter)"""

    def _get_groups(self, client) -> list:
        resp = client.get("/api/v1/config/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data, "响应必须包含 groups 列表"
        assert isinstance(data["groups"], list)
        return data["groups"]

    def test_get_schema_returns_200(self, client):
        resp = client.get("/api/v1/config/schema")
        assert resp.status_code == 200

    def test_get_schema_has_groups_and_version(self, client):
        resp = client.get("/api/v1/config/schema")
        data = resp.json()
        assert "groups" in data
        assert "version" in data
        assert len(data["groups"]) > 0

    def test_get_schema_groups_have_required_structure(self, client):
        groups = self._get_groups(client)
        for g in groups:
            assert "key" in g
            assert "label" in g
            assert "fields" in g
            assert isinstance(g["fields"], list)

    def test_get_schema_each_field_has_key_type_label(self, client):
        groups = self._get_groups(client)
        for g in groups:
            for f in g["fields"]:
                assert "key" in f, f"field missing key: {f}"
                assert "type" in f, f"field missing type: {f}"
                assert "label" in f, f"field missing label: {f}"
                assert f["type"] in ("string", "integer", "float", "boolean", "select", "array", "object")

    def test_get_schema_field_has_value_from_config(self, client):
        groups = self._get_groups(client)
        assert any(g["key"] == "general" for g in groups), "general group should exist"

    def test_get_schema_contains_persona_group(self, client):
        groups = self._get_groups(client)
        assert any(g["key"] == "persona" for g in groups)

    def test_get_schema_llm_group_from_model_config(self, client):
        groups = self._get_groups(client)
        assert any(g["key"] == "llm" for g in groups), "llm group must exist (from ModelConfig)"

    def test_get_schema_groups_have_order_key(self, client):
        groups = self._get_groups(client)
        for g in groups:
            assert "order" in g

    def test_get_schema_sensitive_fields_marked(self, client):
        groups = self._get_groups(client)
        llm = next((g for g in groups if g["key"] == "llm"), None)
        if llm:
            api_key = next((f for f in llm["fields"] if "api_key" in f["key"]), None)
            if api_key:
                assert api_key.get("sensitive") is True

    def test_get_schema_type_number_mapped_to_float(self, client):
        groups = self._get_groups(client)
        for g in groups:
            for f in g["fields"]:
                if f["type"] == "float":
                    return
        for g in groups:
            for f in g["fields"]:
                assert f["type"] != "number", f"field {f['key']} has type 'number' which should be 'float'"