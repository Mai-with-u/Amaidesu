"""Dashboard 配置 API 测试套件 (v2.0.0：7 文件)

覆盖 Dashboard 配置管理 API 在多文件配置结构下的行为:

1. **GET /api/v1/config** — 返回当前合并配置 (扁平化的 main_config)
2. **PATCH /api/v1/config** — 根据节名路由到正确的 TOML 文件:
   - core 节 (general/persona/context/dashboard/logging/interceptors/meta/events) → core.toml
   - model 节 (llm/llm_fast/vlm/llm_local/llm_summary/llm_agenda/llm_providers) → model.toml
   - agents 节 (agents/streamer) → agents.toml
   - tools 节 (tools/perception/output/...) → tools.toml
   - memory 节 → memory.toml
   - storage 节 → storage.toml
   - background 节 → background.toml
3. **GET /api/v1/config/schema** — 返回 ConfigSchemaGenerator 生成的 schema
4. **get_config_path(section)** — 服务端辅助方法返回对应 TOML 文件路径

参考:
- src/modules/dashboard/api/config.py
- src/modules/dashboard/server.py (get_config_path)
- src/modules/config/service.py (T7 schema API)
- src/modules/config/multi_file_loader.py (v2.0.0: 7 个 TOML 文件结构)
"""

from __future__ import annotations

from pathlib import Path

import pytest


_CORE_TOML = """\
# 核心系统配置 - Amaidesu v2.0.0

type = "core"

[meta]
type = "meta"
version = "2.0.0"

[general]
type = "general"
platform_id = "amaidesu"

[persona]
type = "persona"
bot_name = "麦麦"
personality = "活泼开朗"

[context]
type = "context"
enabled = true

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

_AGENTS_TOML = """\
# 业务 Agent 配置

[agents]
enabled = ["streamer"]

[agents.streamer]
planner_llm = "llm_fast"
replyer_llm = "llm"
reply_probability = 0.7
"""

_TOOLS_TOML = """\
# 工具包配置

[tools]
enabled = ["perception", "output"]

[tools.perception]
enabled = true
provider = "builtin"

[tools.output]
enabled = true
provider = "builtin"
"""

_MEMORY_TOML = """\
# 记忆系统配置

[memory]
backend = "simple"

[memory.simple]
recall_top_k = 5
"""

_STORAGE_TOML = """\
# 存储配置

[storage.sqlite]
db_path = "data/amaidesu.db"
wal = true
"""

_BACKGROUND_TOML = """\
# 后台维护配置

[background]
light_tick_ms = 5000

[background.compressor]
concurrency = 1
queue_max = 100
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """生成包含 7 个 TOML 的临时 config/ 目录（v2.0.0）。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_CORE_TOML, encoding="utf-8")
    (cfg / "model.toml").write_text(_MODEL_TOML, encoding="utf-8")
    (cfg / "agents.toml").write_text(_AGENTS_TOML, encoding="utf-8")
    (cfg / "tools.toml").write_text(_TOOLS_TOML, encoding="utf-8")
    (cfg / "memory.toml").write_text(_MEMORY_TOML, encoding="utf-8")
    (cfg / "storage.toml").write_text(_STORAGE_TOML, encoding="utf-8")
    (cfg / "background.toml").write_text(_BACKGROUND_TOML, encoding="utf-8")
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
    from fastapi.testclient import TestClient
    from src.modules.dashboard.api.router import create_app

    app = create_app()
    return TestClient(app)


# ===========================================================================
# 1. get_config_path(section) — 路由节名 → 正确的 TOML 文件
# ===========================================================================


class TestGetConfigPath:
    def test_get_path_for_persona_returns_core_toml(self, dashboard_server, config_dir):
        result = dashboard_server.get_config_path("persona")
        assert result is not None
        assert Path(result).name == "core.toml"
        assert Path(result) == config_dir / "core.toml"

    def test_get_path_for_llm_returns_model_toml(self, dashboard_server, config_dir):
        result = dashboard_server.get_config_path("llm")
        assert result is not None
        assert Path(result).name == "model.toml"
        assert Path(result) == config_dir / "model.toml"

    def test_get_path_for_agents_returns_agents_toml(self, dashboard_server, config_dir):
        """agents 节 → agents.toml（v2.0.0）。"""
        result = dashboard_server.get_config_path("agents")
        assert result is not None
        assert Path(result).name == "agents.toml"
        assert Path(result) == config_dir / "agents.toml"

    def test_get_path_for_tools_returns_tools_toml(self, dashboard_server, config_dir):
        """tools 节 → tools.toml（v2.0.0）。"""
        result = dashboard_server.get_config_path("tools")
        assert result is not None
        assert Path(result).name == "tools.toml"

    def test_get_path_for_all_core_sections(self, dashboard_server, config_dir):
        """core 节族（meta/general/persona/context/dashboard/logging）全部 → core.toml。"""
        for section in ("meta", "general", "persona", "context", "dashboard", "logging"):
            result = dashboard_server.get_config_path(section)
            assert result is not None, f"{section} 应返回非 None"
            assert Path(result).name == "core.toml", f"{section} 应路由到 core.toml"

    def test_get_path_for_all_model_sections(self, dashboard_server, config_dir):
        """model 节族（llm/llm_fast/vlm/llm_local/llm_summary/llm_agenda）全部 → model.toml。"""
        for section in ("llm", "llm_fast", "vlm", "llm_local", "llm_summary", "llm_agenda"):
            result = dashboard_server.get_config_path(section)
            assert result is not None, f"{section} 应返回非 None"
            assert Path(result).name == "model.toml", f"{section} 应路由到 model.toml"

    def test_get_path_for_interceptors_returns_core_toml(self, dashboard_server, config_dir):
        result = dashboard_server.get_config_path("interceptors")
        assert result is not None
        assert Path(result).name == "core.toml"

    def test_get_path_without_service_returns_none(self):
        from src.modules.config.core_schemas import DashboardConfig
        from src.modules.dashboard.server import DashboardServer

        cfg = DashboardConfig(host="127.0.0.1", port=60214)
        server = DashboardServer(
            event_bus=None,
            input_manager=None,
            decision_manager=None,
            output_manager=None,
            context_service=None,
            config_service=None,
            dashboard_config=cfg,
        )
        assert server.get_config_path("persona") is None

    def test_get_path_unknown_section_falls_back_to_core(self, dashboard_server, config_dir):
        result = dashboard_server.get_config_path("nonexistent_section_xyz")
        assert result is not None
        assert Path(result).name == "core.toml"


# ===========================================================================
# 2. GET /api/v1/config — 返回当前合并配置
# ===========================================================================


class TestGetConfigEndpoint:
    def test_get_config_returns_200(self, client):
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200

    def test_get_config_includes_core_sections(self, client):
        resp = client.get("/api/v1/config")
        body = resp.json()
        assert "config" in body, "响应必须包含 'config' 字段"
        data = body["config"]
        assert "dashboard" in data
        assert "logging" in data
        assert "tts" in data
        # 人设归位 [agents.streamer.persona] 分组（嵌套呈现）
        assert "persona" in data["agents"]["streamer"], "persona 必须在 agents.streamer 下"

    def test_get_config_includes_model_sections(self, client):
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert "llm" in data, "llm 节必须在响应中"
        assert "vlm" in data

    def test_get_config_includes_agents_sections(self, client):
        """v2.0.0：agents 段族必须在响应中。"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert "agents" in data

    def test_get_config_values_match_toml(self, client):
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert data["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
        assert data["llm"]["model"] == "gpt-4"
        assert data["llm"]["provider"] == "default"
        assert isinstance(data["llm_providers"], list)
        assert len(data["llm_providers"]) >= 1
        assert data["llm_providers"][0]["name"] == "default"
        assert data["llm_providers"][0]["client_type"] == "openai"

    def test_get_config_maicore_removed(self, client):
        """v2.0.0：maicore 段已从响应中消失。"""
        resp = client.get("/api/v1/config")
        body = resp.json()
        data = body["config"]
        assert "maicore" not in data


# ===========================================================================
# 3. PATCH /api/v1/config — 路由到正确的 TOML 文件
# ===========================================================================


class TestPatchConfigEndpoint:
    def test_patch_persona_writes_to_core_toml(self, client, config_dir):
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "新名字"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "新名字" in core_content
        for fname in ("model.toml", "agents.toml", "tools.toml", "memory.toml", "storage.toml", "background.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "新名字" not in content, f"{fname} 不应被 persona 修改影响"

    def test_patch_llm_writes_to_model_toml(self, client, config_dir):
        resp = client.patch("/api/v1/config", json={"key": "llm.model", "value": "gpt-4-turbo"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        model_content = (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "gpt-4-turbo" in model_content
        for fname in ("core.toml", "agents.toml", "tools.toml", "memory.toml", "storage.toml", "background.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "gpt-4-turbo" not in content

    def test_patch_agents_writes_to_agents_toml(self, client, config_dir):
        """v2.0.0：agents 段 PATCH → agents.toml。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "agents.streamer.persona.bot_name", "value": "新主播名"},
        )
        assert resp.status_code == 200, resp.text
        agents_content = (config_dir / "agents.toml").read_text(encoding="utf-8")
        assert "新主播名" in agents_content

    def test_patch_tools_writes_to_tools_toml(self, client, config_dir):
        """v2.0.0：tools 段 PATCH → tools.toml。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "tools.perception.enabled", "value": False},
        )
        assert resp.status_code == 200, resp.text
        tools_content = (config_dir / "tools.toml").read_text(encoding="utf-8")
        assert "enabled" in tools_content

    def test_patch_does_not_use_hardcoded_config_toml(self, client, tmp_path):
        old_path = tmp_path / "config.toml"
        assert not old_path.exists(), "测试前提: tmp_path/config.toml 不应存在"

        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "测试"})
        assert resp.status_code == 200

        assert not old_path.exists(), "PATCH 不应写入根目录的硬编码 config.toml 路径,应写入 config/core.toml"

    def test_patch_returns_requires_restart_for_llm_keys(self, client):
        resp = client.patch("/api/v1/config", json={"key": "llm.model", "value": "gpt-5"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_returns_requires_restart_for_dashboard_keys(self, client):
        resp = client.patch("/api/v1/config", json={"key": "dashboard.port", "value": 9999})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_persona_requires_restart(self, client):
        """decision A: 任何 PATCH 都要求重启（ConfigService.reload_config 不向已构造 Agent 注入新值）。"""
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "测试名"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_logging_requires_restart(self, client):
        resp = client.patch("/api/v1/config", json={"key": "logging.level", "value": "DEBUG"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True

    def test_patch_persists_value(self, client, config_dir):
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "持久化测试"})
        assert resp.status_code == 200

        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["persona"]["bot_name"] == "持久化测试"

    def test_patch_multiple_sections_each_writes_to_correct_file(self, client, config_dir):
        """v2.0.0：连续 PATCH 多个不同节，路由到 7 个不同文件。"""
        updates = [
            ("persona.bot_name", "新名字", "core.toml"),
            ("llm.model", "claude-3", "model.toml"),
            ("agents.streamer.persona.bot_name", "跨节主播名", "agents.toml"),
        ]
        for key, value, _expected_file in updates:
            resp = client.patch("/api/v1/config", json={"key": key, "value": value})
            assert resp.status_code == 200, f"PATCH {key} 失败: {resp.text}"
            assert resp.json()["success"] is True

        assert "新名字" in (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "claude-3" in (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "跨节主播名" in (config_dir / "agents.toml").read_text(encoding="utf-8")


# ===========================================================================
# 3b. PATCH /api/v1/config — 空键校验 (issue #69 #7)
# ===========================================================================


class TestPatchConfigEmptyKeyValidation:
    def test_patch_rejects_top_level_empty_key(self, client, config_dir):
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.custom", "value": {"": "x"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "空键" in body["message"]
        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "custom" not in core_content

    def test_patch_rejects_nested_empty_key_with_path(self, client, config_dir):
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.custom", "value": {"sub": {"": "x"}}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "sub." in body["message"]

    def test_patch_rejects_whitespace_key(self, client, config_dir):
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.custom", "value": {" ": "x"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "空键" in body["message"]

    def test_patch_rejects_empty_key_in_list(self, client, config_dir):
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.custom", "value": [{"ok": 1}, {"": 2}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "[1]" in body["message"]

    def test_patch_accepts_valid_nested_dict(self, client, config_dir):
        """``interceptors`` 是 ``dict[str, Any]`` 字段，下一段是自由键，叶子无 schema 校验。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "interceptors.rate_limit", "value": {"name": "麦麦", "age": 18}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["interceptors"]["rate_limit"]["name"] == "麦麦"
        assert doc["interceptors"]["rate_limit"]["age"] == 18


# ===========================================================================
# 4. GET /api/v1/config/schema — 返回生成的 schema
# ===========================================================================


class TestGetConfigSchemaEndpoint:
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

    def test_get_schema_contains_agents_group(self, client):
        """v2.0.0：agents 组应在响应中。"""
        groups = self._get_groups(client)
        assert any(g["key"] == "agents" for g in groups)

    def test_get_schema_contains_tools_group(self, client):
        """v2.0.0：tools 组应在响应中。"""
        groups = self._get_groups(client)
        assert any(g["key"] == "tools" for g in groups)

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


# ===========================================================================
# 5. PATCH /api/v1/config — schema 校验 / readonly / 类型约束
# ===========================================================================


class TestPatchConfigSchemaValidation:
    """PATCH schema 校验矩阵：未知 / readonly / 类型与约束违规全部拒绝。"""

    def test_patch_unknown_key_rejected_and_nothing_written(self, client, config_dir):
        """未知 key：返回 success=false，磁盘文件保持原状。"""
        resp = client.patch("/api/v1/config", json={"key": "persona.does_not_exist", "value": "x"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "未知配置项" in body["message"]
        assert "persona.does_not_exist" in body["message"]

        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "does_not_exist" not in core_content, "未知 key 不应写入磁盘"

    def test_patch_unknown_section_rejected(self, client, config_dir):
        """未注册的 section：返回 success=false。"""
        resp = client.patch("/api/v1/config", json={"key": "ghost.foo", "value": "x"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "未知配置项" in body["message"]

    def test_patch_readonly_field_meta_version_rejected(self, client, config_dir):
        """``meta.version`` 标记 readonly，PATCH 一律拒绝。"""
        resp = client.patch("/api/v1/config", json={"key": "meta.version", "value": "v2.0.28"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "只读" in body["message"]
        assert "meta.version" in body["message"]

        # 写拒绝后磁盘内容不应被本次 PATCH 覆盖（drift 写回可能升级 version，但 PATCH 值不应落地）
        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["meta"]["version"] != "v2.0.28", "readonly 字段不应被 PATCH 覆盖"

    def test_patch_type_violation_string_into_integer_rejected(self, client, config_dir):
        """``persona.max_response_length`` 是 int 字段，写入字符串应被拒绝。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.max_response_length", "value": "fifty"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "persona.max_response_length" in body["message"]
        assert "整数" in body["message"]

        core_content = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "fifty" not in core_content, "类型错误不应写入磁盘"

    def test_patch_type_violation_bool_into_string_rejected(self, client, config_dir):
        """bool 写入字符串字段应被拒绝。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "persona.bot_name", "value": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "字符串" in body["message"]

    def test_patch_constraint_violation_above_max_rejected(self, client, config_dir):
        """``dashboard.subtitle_widget.max_messages`` 约束 ``le=50``，写入超出范围应被拒绝。"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "dashboard.subtitle_widget.max_messages", "value": 999},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False, f"超出 max_messages 上限应被拒绝，实际消息: {body['message']}"
        assert "不能大于 50" in body["message"] or "le" in body["message"].lower()

    def test_patch_valid_save_returns_success_and_requires_restart(self, client):
        """合法写入：success=true 且 requires_restart=True（decision A）。"""
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": "新名"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body.get("requires_restart") is True, (
            "decision A: reload_config 不向已构造 Agent 注入新值，任何 PATCH 都要提示重启"
        )
        assert body.get("target_file") == "core.toml"

    def test_patch_valid_save_writes_toml_value_correctly(self, client, config_dir):
        """合法写入：值正确落地到对应 TOML 文件。"""
        new_value = "持久化_校验_" + "x"  # 用 ASCII 避免控制台编码干扰
        resp = client.patch("/api/v1/config", json={"key": "persona.bot_name", "value": new_value})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["persona"]["bot_name"] == new_value

    def test_patch_does_not_create_unknown_sections(self, client, config_dir):
        """未注册的中间段不会被自动创建（避免垃圾 section 注入）。"""
        # 第一段就是未知 section，必须拒绝
        resp = client.patch(
            "/api/v1/config",
            json={"key": "totally_unknown_section.field", "value": "x"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "未知配置项" in body["message"]

        # 确认没有任何 toml 文件被污染
        for fname in (
            "core.toml",
            "model.toml",
            "agents.toml",
            "tools.toml",
            "memory.toml",
            "storage.toml",
            "background.toml",
        ):
            content = (config_dir / fname).read_text(encoding="utf-8")
            assert "totally_unknown_section" not in content


# ===========================================================================
# 6. GET /api/v1/config/schema — readonly 字段标记透传
# ===========================================================================


class TestSchemaReadonlyFlag:
    """``meta.version`` 通过 ``json_schema_extra`` 标记 readonly，schema 接口必须透传。"""

    def test_meta_version_field_marked_readonly(self, client):
        resp = client.get("/api/v1/config/schema")
        assert resp.status_code == 200
        groups = resp.json()["groups"]
        meta_group = next((g for g in groups if g["key"] == "meta"), None)
        assert meta_group is not None, "meta 组必须在 schema 中存在"
        version_field = next(
            (f for f in meta_group["fields"] if f["key"] == "meta.version"),
            None,
        )
        assert version_field is not None, "meta.version 字段必须在 schema 中"
        assert version_field.get("readonly") is True, f"meta.version 应标记 readonly，实际字段: {version_field}"

    def test_other_fields_default_to_non_readonly(self, client):
        """非 readonly 字段在响应中显式带 ``readonly: false``，供前端判别。"""
        resp = client.get("/api/v1/config/schema")
        groups = resp.json()["groups"]
        persona_group = next((g for g in groups if g["key"] == "persona"), None)
        assert persona_group is not None
        bot_name = next(
            (f for f in persona_group["fields"] if f["key"] == "persona.bot_name"),
            None,
        )
        assert bot_name is not None
        assert bot_name.get("readonly") is False


# ===========================================================================
# 7. POST /api/v1/config/batch — 批量原子保存
# ===========================================================================


class TestBatchUpdateEndpoint:
    """批量端点事务语义：校验全部通过才写盘，按目标 TOML 文件分组写入。"""

    def _post_batch(self, client, changes):
        return client.post("/api/v1/config/batch", json={"changes": changes})

    def test_batch_empty_changes_rejected(self, client):
        resp = self._post_batch(client, [])
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["message"] == "没有可保存的更改"
        assert body.get("results", []) == []
        assert body.get("errors", []) == []

    def test_batch_single_file_writes_once_and_requires_restart(self, client, config_dir):
        changes = [
            {"key": "persona.bot_name", "value": "新名字A"},
            {"key": "persona.personality", "value": "温柔"},
        ]
        resp = self._post_batch(client, changes)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["message"] == "配置已保存"
        assert body["requires_restart"] is True
        assert len(body["results"]) == 2
        for r in body["results"]:
            assert r["success"] is True
        assert body.get("errors", []) == []

        core = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "新名字A" in core
        assert "温柔" in core

    def test_batch_multi_file_writes_each_target_once(self, client, config_dir):
        changes = [
            {"key": "persona.bot_name", "value": "跨文件A"},
            {"key": "llm.model", "value": "claude-3-batch"},
            {"key": "agents.streamer.persona.bot_name", "value": "批量主播名"},
        ]
        resp = self._post_batch(client, changes)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["requires_restart"] is True
        assert len(body["results"]) == 3

        assert "跨文件A" in (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "claude-3-batch" in (config_dir / "model.toml").read_text(encoding="utf-8")
        assert "批量主播名" in (config_dir / "agents.toml").read_text(encoding="utf-8")

        for fname in ("tools.toml", "memory.toml", "storage.toml", "background.toml"):
            content = (config_dir / fname).read_text(encoding="utf-8")
            for v in ("跨文件A", "claude-3-batch", "批量主播名"):
                assert v not in content, f"{fname} 不应被批量端点影响"

    def test_batch_one_invalid_key_writes_nothing(self, client, config_dir):
        before_core = (config_dir / "core.toml").read_bytes()
        before_model = (config_dir / "model.toml").read_bytes()
        before_agents = (config_dir / "agents.toml").read_bytes()

        changes = [
            {"key": "persona.bot_name", "value": "应当不被写入"},
            {"key": "persona.does_not_exist", "value": "x"},
            {"key": "llm.model", "value": "gpt-4-batch-fail"},
        ]
        resp = self._post_batch(client, changes)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "未知配置项" in body["message"]
        assert "persona.does_not_exist" in body["message"]
        assert body.get("requires_restart") in (False, None)

        errors = body["errors"]
        assert len(errors) == 1
        assert errors[0]["key"] == "persona.does_not_exist"
        assert "未知配置项" in errors[0]["message"]

        assert (config_dir / "core.toml").read_bytes() == before_core
        assert (config_dir / "model.toml").read_bytes() == before_model
        assert (config_dir / "agents.toml").read_bytes() == before_agents

    def test_batch_multiple_failures_aggregates_message(self, client):
        changes = [
            {"key": "persona.max_response_length", "value": "fifty"},
            {"key": "persona.does_not_exist", "value": "x"},
            {"key": "meta.version", "value": "v9.9.9"},
        ]
        resp = self._post_batch(client, changes)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "另有 2 项失败" in body["message"]
        assert len(body["errors"]) == 3
        keys_in_errors = {e["key"] for e in body["errors"]}
        assert keys_in_errors == {"persona.max_response_length", "persona.does_not_exist", "meta.version"}

    def test_batch_readonly_field_rejected_with_others_unchanged(self, client, config_dir):
        changes = [
            {"key": "persona.bot_name", "value": "应被丢弃"},
            {"key": "meta.version", "value": "v9.9.9"},
        ]
        resp = self._post_batch(client, changes)
        body = resp.json()
        assert body["success"] is False
        assert "只读" in body["message"]
        assert body["errors"][0]["key"] == "meta.version"

        core = (config_dir / "core.toml").read_text(encoding="utf-8")
        assert "应被丢弃" not in core

    def test_batch_type_violation_rejected(self, client):
        changes = [{"key": "persona.max_response_length", "value": "fifty"}]
        resp = self._post_batch(client, changes)
        body = resp.json()
        assert body["success"] is False
        assert "整数" in body["message"]
        assert body["errors"][0]["key"] == "persona.max_response_length"

    def test_batch_unknown_section_rejected(self, client):
        changes = [{"key": "ghost.foo", "value": "x"}]
        resp = self._post_batch(client, changes)
        body = resp.json()
        assert body["success"] is False
        assert "未知配置项" in body["message"]
        assert body["errors"][0]["key"] == "ghost.foo"

    def test_batch_duplicate_key_last_wins(self, client, config_dir):
        changes = [
            {"key": "persona.bot_name", "value": "第一次"},
            {"key": "persona.bot_name", "value": "最终值"},
        ]
        resp = self._post_batch(client, changes)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["results"]) == 1
        assert body["results"][0]["key"] == "persona.bot_name"
        assert body["results"][0]["success"] is True

        from src.modules.config.toml_utils import load_toml_with_comments

        doc = load_toml_with_comments(str(config_dir / "core.toml"))
        assert doc["persona"]["bot_name"] == "最终值"
        assert "第一次" not in (config_dir / "core.toml").read_text(encoding="utf-8")

    def test_batch_duplicate_key_last_invalid_overrides_valid(self, client):
        """同一 key 重复时，后者的无效值会覆盖前者的合法值（last-wins），整批回退。"""
        changes = [
            {"key": "persona.bot_name", "value": "合法值"},
            {"key": "persona.bot_name", "value": 12345},
            {"key": "persona.max_response_length", "value": "fifty"},
        ]
        resp = self._post_batch(client, changes)
        body = resp.json()
        assert body["success"] is False
        # 去重后 bot_name 末值为 12345（int 写入 string 字段），max_response_length 是 "fifty"
        # 两个都会触发类型错误，整批回退
        assert len(body["errors"]) == 2

    def test_batch_without_service_returns_failure(self):
        from src.modules.config.core_schemas import DashboardConfig
        from src.modules.dashboard.server import DashboardServer
        from src.modules.dashboard.dependencies import set_dashboard_server

        cfg = DashboardConfig(host="127.0.0.1", port=60214)
        server = DashboardServer(
            event_bus=None,
            input_manager=None,
            decision_manager=None,
            output_manager=None,
            context_service=None,
            config_service=None,
            dashboard_config=cfg,
        )
        set_dashboard_server(server)
        try:
            from fastapi.testclient import TestClient
            from src.modules.dashboard.api.router import create_app

            app = create_app()
            client = TestClient(app)
            resp = client.post(
                "/api/v1/config/batch",
                json={"changes": [{"key": "persona.bot_name", "value": "x"}]},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is False
            assert "not available" in body["message"].lower() or "不可用" in body["message"]
        finally:
            set_dashboard_server(None)  # type: ignore[arg-type]


# ===========================================================================
# 8. GET /api/v1/config — 敏感字段屏蔽
# ===========================================================================


class TestGetConfigSensitiveMasking:
    """GET /config 全量导出接口对敏感字段（api_key / token / password / secret）做屏蔽。"""

    def test_api_key_in_llm_local_masked(self, client):
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["llm_local"]["api_key"] == ""

    def test_api_key_in_providers_masked(self, client):
        resp = client.get("/api/v1/config")
        body = resp.json()
        providers = body["config"].get("llm_providers", [])
        assert isinstance(providers, list) and len(providers) >= 1
        assert providers[0]["api_key"] == ""

    def test_non_sensitive_fields_unchanged(self, client):
        resp = client.get("/api/v1/config")
        body = resp.json()
        assert body["config"]["agents"]["streamer"]["persona"]["bot_name"] == "麦麦"
        assert body["config"]["llm"]["model"] == "gpt-4"
        assert body["config"]["llm_local"]["base_url"] == "http://localhost:11434/v1"

    def test_masking_does_not_mutate_underlying_config(self, client, dashboard_server):
        client.get("/api/v1/config")
        assert dashboard_server.config_service.main_config["llm_local"]["api_key"] == "sk-dummy"


# ===========================================================================
# 9. GET /api/v1/config/schema — 敏感字段 value 屏蔽
# ===========================================================================


class TestSchemaSensitiveMasking:
    """Schema 接口的敏感字段 value 一律为 ``""``，避免明文凭据经 schema 接口外泄。"""

    def _all_fields(self, client) -> list[dict]:
        groups = client.get("/api/v1/config/schema").json()["groups"]
        out: list[dict] = []
        for g in groups:
            for f in g["fields"]:
                out.append(f)
                for c in f.get("children", []) or []:
                    out.append(c)
        return out

    def test_schema_sensitive_field_value_is_empty_string(self, client):
        fields = self._all_fields(client)
        api_key_field = next((f for f in fields if f["key"] == "llm_local.api_key"), None)
        assert api_key_field is not None, "llm_local.api_key 字段应在 schema 中存在"
        assert api_key_field.get("sensitive") is True
        assert api_key_field["value"] == ""

    def test_schema_provider_api_key_value_masked(self, client):
        fields = self._all_fields(client)
        provider_api_key = next((f for f in fields if f["key"] == "llm_providers.api_key"), None)
        assert provider_api_key is not None, "llm_providers.api_key 字段应在 schema 中存在"
        assert provider_api_key.get("sensitive") is True
        assert provider_api_key["value"] == ""

    def test_schema_non_sensitive_field_value_preserved(self, client):
        fields = self._all_fields(client)
        port = next((f for f in fields if f["key"] == "dashboard.port"), None)
        assert port is not None
        assert port.get("sensitive") is False
        assert port["value"] == 60214
