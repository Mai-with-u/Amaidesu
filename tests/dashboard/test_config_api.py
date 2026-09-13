"""Dashboard 配置 API 测试套件（v2 六文件树）

覆盖 Dashboard 配置管理 API 在六文件配置结构下的行为:

1. **GET /api/v1/config** — 返回六 scope 合并视图，敏感字段"已设置"占位
2. **PATCH /api/v1/config** — scope 首段路由到对应 TOML 文件，经统一管线写盘；
   未知项 / 只读字段 / 类型违约 / 占位回写一律 422 + 中文消息
3. **POST /api/v1/config/batch** — 多文件事务语义：任一失败磁盘零写入
4. **GET /api/v1/config/schema** — 六个根 Schema 的自描述分组

API 键约定：scope 前缀 + 文件内点分路径（如 ``tools.tools.tasks.poll_interval_ms``），
与 GET 合并视图寻址一致。

参考:
- src/modules/dashboard/api/config.py
- src/modules/config/multi_file_loader.py（统一写回管线）
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """六文件基线布局（从 Schema 生成默认值，可被加载管线正常装载）。"""
    from src.modules.config.multi_file_loader import generate_default_configs

    cfg = tmp_path / "config"
    cfg.mkdir()
    generate_default_configs(cfg)
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
# 1. GET /api/v1/config
# ===========================================================================


class TestGetConfigEndpoint:
    def test_get_config_returns_200(self, client):
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "config" in body

    def test_get_config_returns_flattened_merge(self, client):
        """GET 返回扁平化合并视图：各文件根字段展平到顶层"""
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        config = resp.json()["config"]
        for key in ("agents", "tools", "enabled", "llm_providers", "sqlite", "dashboard"):
            assert key in config, f"合并视图缺少键: {key}"

    def test_get_config_masks_sensitive_fields(self, client):
        """敏感字段值为"已设置"占位，明文不下发"""
        resp = client.get("/api/v1/config")
        config = resp.json()["config"]
        api_key = config["llm_providers"][0]["api_key"]
        assert api_key == "已设置"

    def test_get_config_does_not_mask_budget_tokens(self, client):
        """max_tokens 等预算类参数不是凭据，正常显示数值（QA 发现的误伤修复）"""
        resp = client.get("/api/v1/config")
        config = resp.json()["config"]
        assert isinstance(config["llm_profiles"]["planner"]["max_tokens"], int)

    def test_get_config_values_match_toml(self, client, config_dir):
        """合并视图值与磁盘 TOML 一致（dashboard.port）"""
        resp = client.get("/api/v1/config")
        config = resp.json()["config"]
        assert config["dashboard"]["port"] == 60214


# ===========================================================================
# 2. PATCH /api/v1/config — 统一管线写回
# ===========================================================================


class TestPatchConfigEndpoint:
    def test_patch_infra_hot_scope_applies_without_restart(self, client, config_dir):
        """infra 是 hot 段：写盘 + 即时重载，requires_restart=False"""
        resp = client.patch("/api/v1/config", json={"key": "infra.dashboard.port", "value": 60299})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["requires_restart"] is False
        assert body["target_file"] == "infra.toml"

        import tomlkit

        with open(config_dir / "infra.toml", encoding="utf-8-sig") as f:
            doc = tomlkit.load(f).unwrap()
        assert doc["dashboard"]["port"] == 60299

    def test_patch_tools_writes_via_pipeline(self, client, config_dir):
        """非 hot 段：写盘成功 + 待重启语义"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "tools.tools.tasks.poll_interval_ms", "value": 3000},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["requires_restart"] is True
        assert body["target_file"] == "tools.toml"

        import tomlkit

        with open(config_dir / "tools.toml", encoding="utf-8-sig") as f:
            doc = tomlkit.load(f).unwrap()
        assert doc["tools"]["tasks"]["poll_interval_ms"] == 3000

    def test_patch_dict_profile_path(self, client, config_dir):
        """dict[str, 子模型] 动态键路径可下钻（llm_profiles.replyer.temperature）"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "model.llm_profiles.replyer.temperature", "value": 0.7},
        )
        assert resp.status_code == 200

        import tomlkit

        with open(config_dir / "model.toml", encoding="utf-8-sig") as f:
            doc = tomlkit.load(f).unwrap()
        assert doc["llm_profiles"]["replyer"]["temperature"] == 0.7

    def test_patch_dict_profile_business_rule_422(self, client):
        """业务级校验同样生效：model_list 引用未注册模型 → 422"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "model.llm_profiles.planner.model_list", "value": ["no-such-model"]},
        )
        assert resp.status_code == 422
        assert "no-such-model" in resp.json()["detail"]

    def test_patch_unknown_scope_422(self, client):
        resp = client.patch("/api/v1/config", json={"key": "legacy.persona.bot_name", "value": "x"})
        assert resp.status_code == 422
        assert "配置域" in resp.json()["detail"]

    def test_patch_unknown_key_422(self, client):
        """schema 树外的键 → 未知配置项（不静默落盘）"""
        resp = client.patch("/api/v1/config", json={"key": "tools.tools.no_such_field", "value": 1})
        assert resp.status_code == 422
        assert "未知配置项" in resp.json()["detail"]

    def test_patch_meta_version_readonly_422(self, client, config_dir):
        """[meta].version 只读：PATCH → 422，文件不动"""
        before = (config_dir / "agents.toml").read_bytes()
        resp = client.patch("/api/v1/config", json={"key": "agents.meta.version", "value": "9.9.9"})
        assert resp.status_code == 422
        assert "只读" in resp.json()["detail"]
        assert (config_dir / "agents.toml").read_bytes() == before

    def test_patch_type_violation_422_with_field_path(self, client, config_dir):
        """类型违约 → 422 + 消息含字段路径与文件名"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "tools.tools.tasks.poll_interval_ms", "value": "abc"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "tools.toml" in detail
        assert "poll_interval_ms" in detail

    def test_patch_constraint_violation_422(self, client):
        """约束违约（ge=100）→ 422"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "tools.tools.tasks.poll_interval_ms", "value": 1},
        )
        assert resp.status_code == 422

    def test_patch_sensitive_placeholder_rejected_422(self, client):
        """回写"已设置"占位 → 422（防前端回显覆盖真实值）"""
        resp = client.patch(
            "/api/v1/config",
            json={"key": "model.llm_providers", "value": [{"name": "default", "api_key": "已设置"}]},
        )
        assert resp.status_code == 422
        assert "占位" in resp.json()["detail"]

    def test_patch_sensitive_explicit_new_value_writes(self, client, config_dir):
        """敏感字段显式提交新值 → 正常写入"""
        providers = [{"name": "default", "client_type": "openai", "base_url": "https://x/v1", "api_key": "sk-new"}]
        resp = client.patch("/api/v1/config", json={"key": "model.llm_providers", "value": providers})
        assert resp.status_code == 200

        disk = (config_dir / "model.toml").read_text(encoding="utf-8-sig")
        assert "sk-new" in disk

    def test_patch_empty_key_in_value_422(self, client):
        resp = client.patch(
            "/api/v1/config",
            json={"key": "infra.interceptors", "value": {"": {"enabled": True}}},
        )
        assert resp.status_code == 422
        assert "空键" in resp.json()["detail"]


# ===========================================================================
# 3. POST /api/v1/config/batch — 事务语义
# ===========================================================================


class TestBatchConfigEndpoint:
    def test_batch_multi_file_writes_all(self, client, config_dir):
        """跨文件批量保存：全部落盘"""
        resp = client.post(
            "/api/v1/config/batch",
            json={
                "changes": [
                    {"key": "tools.tools.tasks.poll_interval_ms", "value": 3000},
                    {"key": "infra.dashboard.port", "value": 60300},
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        import tomlkit

        with open(config_dir / "tools.toml", encoding="utf-8-sig") as f:
            tools_doc = tomlkit.load(f).unwrap()
        with open(config_dir / "infra.toml", encoding="utf-8-sig") as f:
            infra_doc = tomlkit.load(f).unwrap()
        assert tools_doc["tools"]["tasks"]["poll_interval_ms"] == 3000
        assert infra_doc["dashboard"]["port"] == 60300

    def test_batch_invalid_change_zero_writes(self, client, config_dir):
        """任一变更非法 → 422 + 磁盘零写入（事务回退）"""
        before = {name: (config_dir / name).read_bytes() for name in ("tools.toml", "infra.toml")}
        resp = client.post(
            "/api/v1/config/batch",
            json={
                "changes": [
                    {"key": "tools.tools.tasks.poll_interval_ms", "value": 3000},
                    {"key": "infra.dashboard.port", "value": "not-a-number"},
                ]
            },
        )
        assert resp.status_code == 422
        for name, content in before.items():
            assert (config_dir / name).read_bytes() == content, f"{name} 不应被写入"

    def test_batch_duplicate_key_last_wins(self, client, config_dir):
        """重复 key 后者覆盖前者"""
        resp = client.post(
            "/api/v1/config/batch",
            json={
                "changes": [
                    {"key": "infra.dashboard.port", "value": 60001},
                    {"key": "infra.dashboard.port", "value": 60002},
                ]
            },
        )
        assert resp.status_code == 200

        import tomlkit

        with open(config_dir / "infra.toml", encoding="utf-8-sig") as f:
            doc = tomlkit.load(f).unwrap()
        assert doc["dashboard"]["port"] == 60002

    def test_batch_empty_changes_rejected(self, client):
        resp = client.post("/api/v1/config/batch", json={"changes": []})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_batch_write_generates_backup(self, client, config_dir):
        """统一管线写回产生备份（config/old/<批次>/）"""
        client.post(
            "/api/v1/config/batch",
            json={"changes": [{"key": "storage.sqlite.db_path", "value": "data/other.db"}]},
        )
        old_dir = config_dir / "old"
        assert old_dir.is_dir()
        backups = list(old_dir.rglob("storage.toml"))
        assert len(backups) == 1


# ===========================================================================
# 4. GET /api/v1/config/schema — 自描述分组
# ===========================================================================


class TestGetConfigSchemaEndpoint:
    @staticmethod
    def _leaf_fields(group: dict) -> list[dict]:
        """递归收集组内叶子字段（容器字段带 children，叶子在深层）"""
        out: list[dict] = []

        def walk(fields: list[dict]) -> None:
            for f in fields:
                if "children" in f:
                    walk(f["children"])
                else:
                    out.append(f)

        walk(group["fields"])
        return out

    def test_get_schema_returns_six_groups(self, client):
        """每个根 Schema 一个分组（自描述协议，无手写映射表）"""
        resp = client.get("/api/v1/config/schema")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "1.0.0"
        groups = body["groups"]
        assert len(groups) == 6
        assert {g["key"] for g in groups} == {
            "agents",
            "collectors",
            "tools",
            "model",
            "storage",
            "infra",
        }

    def test_schema_groups_carry_self_described_file(self, client):
        resp = client.get("/api/v1/config/schema")
        for group in resp.json()["groups"]:
            assert group["file_name"] == f"{group['key']}.toml"
            assert group["label"]
            assert group["file_label"]

    def test_schema_field_keys_carry_scope_prefix(self, client):
        """字段 key 带 scope 前缀，与写接口键约定一致"""
        resp = client.get("/api/v1/config/schema")
        groups = {g["key"]: g for g in resp.json()["groups"]}
        tool_keys = [f["key"] for f in self._leaf_fields(groups["tools"])]
        assert tool_keys, "tools 组应有字段"
        for key in tool_keys:
            assert key.startswith("tools."), f"字段 key 缺 scope 前缀: {key}"

    def test_schema_fields_have_required_shape(self, client):
        resp = client.get("/api/v1/config/schema")
        groups = resp.json()["groups"]
        some_fields = [f for g in groups for f in self._leaf_fields(g)]
        assert some_fields
        for field in some_fields:
            assert "key" in field
            assert "type" in field
            assert "label" in field

    def test_schema_readonly_flag_passthrough(self, client):
        """meta.version 的 readonly 标记透传到 schema（与写接口拒绝共用信号）"""
        resp = client.get("/api/v1/config/schema")
        groups = {g["key"]: g for g in resp.json()["groups"]}
        agent_keys = {f["key"]: f for f in self._leaf_fields(groups["agents"])}
        version_field = agent_keys.get("agents.meta.version")
        assert version_field is not None
        assert version_field["readonly"] is True
