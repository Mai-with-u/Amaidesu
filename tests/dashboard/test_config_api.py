"""Dashboard 配置 API（七文件树）：合并视图与敏感字段占位、统一管线写回与 422 规则、批量事务、Schema 自描述

API 键 = scope 前缀 + 文件内点分路径（如 ``tools.tools.tasks.poll_interval_ms``），与 GET 合并视图寻址一致。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from src.modules.config.multi_file_loader import generate_default_configs
from src.modules.config.service import ConfigService


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    generate_default_configs(cfg)
    return cfg


@pytest.fixture
def client(mount, config_dir: Path):
    service = ConfigService(base_dir=str(config_dir.parent))
    service.initialize()
    return mount(config_service=service)


def _toml(config_dir: Path, name: str) -> dict:
    return tomllib.loads((config_dir / name).read_text(encoding="utf-8-sig"))


def _patch(client, key: str, value):
    return client.patch("/api/v1/config", json={"key": key, "value": value})


_PROVIDER = {"name": "default", "client_type": "openai", "base_url": "https://x/v1"}


def test_get_config_merged_view_masks_secrets_and_hides_retired_limits(client) -> None:
    config = client.get("/api/v1/config").json()["config"]
    for key in ("agents", "tools", "enabled", "llm_providers", "sqlite", "dashboard"):
        assert key in config, f"合并视图缺少键: {key}"
    assert config["dashboard"]["port"] == 60214
    assert config["llm_providers"][0]["api_key"] == "已设置"  # 明文不下发
    planner = config["llm_profiles"]["planner"]
    assert "max_tokens" not in planner and "hard_timeout_ms" not in planner  # 退役的额度与时限不再可编辑
    assert isinstance(planner["temperature"], float)


@pytest.mark.parametrize(
    ("key", "value", "file", "restart", "read"),
    [
        ("infra.dashboard.port", 60299, "infra.toml", False, lambda d: d["dashboard"]["port"]),  # hot 段即时重载
        (
            "tools.tools.tasks.poll_interval_ms",
            3000,
            "tools.toml",
            True,
            lambda d: d["tools"]["tasks"]["poll_interval_ms"],
        ),
        # dict[str, 子模型] 动态键路径可下钻
        (
            "model.llm_profiles.replyer.temperature",
            0.7,
            "model.toml",
            True,
            lambda d: d["llm_profiles"]["replyer"]["temperature"],
        ),
    ],
)
def test_patch_writes_through_pipeline(client, config_dir, key, value, file, restart, read) -> None:
    resp = _patch(client, key, value)
    body = resp.json()
    assert resp.status_code == 200 and body["success"] is True and body["target_file"] == file
    if "profiles" not in key:
        assert body["requires_restart"] is restart
    assert read(_toml(config_dir, file)) == value


@pytest.mark.parametrize(
    ("key", "value", "detail"),
    [
        ("model.llm_profiles.planner.model_list", ["no-such-model"], "no-such-model"),  # 业务级校验
        ("legacy.persona.bot_name", "x", "配置域"),
        ("tools.tools.no_such_field", 1, "未知配置项"),  # schema 树外的键不静默落盘
        ("tools.tools.tasks.poll_interval_ms", "abc", "poll_interval_ms"),  # 类型违约：含字段路径
        ("tools.tools.tasks.poll_interval_ms", 1, ""),  # 约束违约 ge=100
        ("infra.interceptors", {"": {"enabled": True}}, "空键"),
    ],
)
def test_patch_invalid_returns_422(client, key: str, value, detail: str) -> None:
    resp = _patch(client, key, value)
    assert resp.status_code == 422 and detail in resp.json()["detail"]
    if "poll_interval_ms" in key and value == "abc":
        assert "tools.toml" in resp.json()["detail"]


def test_patch_meta_version_is_readonly_and_leaves_file_untouched(client, config_dir) -> None:
    before = (config_dir / "agents.toml").read_bytes()
    resp = _patch(client, "agents.meta.version", "9.9.9")
    assert resp.status_code == 422 and "只读" in resp.json()["detail"]
    assert (config_dir / "agents.toml").read_bytes() == before


def test_patch_list_placeholders(client, config_dir) -> None:
    """整列表回显：已有元素的"已设置"占位按索引还原为磁盘真实值；残留占位（新元素无对位）422 且不落盘。"""
    assert _patch(client, "model.llm_providers", [{**_PROVIDER, "api_key": "sk-real"}]).status_code == 200

    echoed = _patch(client, "model.llm_providers", [{**_PROVIDER, "api_key": "已设置"}])
    disk = (config_dir / "model.toml").read_text(encoding="utf-8-sig")
    assert echoed.status_code == 200 and "sk-real" in disk and "已设置" not in disk

    before = (config_dir / "model.toml").read_bytes()
    residual = [{**_PROVIDER, "api_key": "sk-real"}, {**_PROVIDER, "name": "second", "api_key": "已设置"}]
    resp = _patch(client, "model.llm_providers", residual)
    assert resp.status_code == 422 and "占位" in resp.json()["detail"]
    assert (config_dir / "model.toml").read_bytes() == before

    assert _patch(client, "model.llm_providers", [{**_PROVIDER, "api_key": "sk-new"}]).status_code == 200
    assert "sk-new" in (config_dir / "model.toml").read_text(encoding="utf-8-sig")


def test_batch_writes_all_files_and_last_duplicate_wins(client, config_dir) -> None:
    resp = client.post(
        "/api/v1/config/batch",
        json={
            "changes": [
                {"key": "tools.tools.tasks.poll_interval_ms", "value": 3000},
                {"key": "infra.dashboard.port", "value": 60001},
                {"key": "infra.dashboard.port", "value": 60300},
            ]
        },
    )
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert _toml(config_dir, "tools.toml")["tools"]["tasks"]["poll_interval_ms"] == 3000
    assert _toml(config_dir, "infra.toml")["dashboard"]["port"] == 60300


def test_batch_invalid_change_writes_nothing(client, config_dir) -> None:
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
    assert {name: (config_dir / name).read_bytes() for name in before} == before


def test_batch_empty_is_rejected_and_write_leaves_backup(client, config_dir) -> None:
    empty = client.post("/api/v1/config/batch", json={"changes": []})
    assert empty.status_code == 200 and empty.json()["success"] is False

    client.post("/api/v1/config/batch", json={"changes": [{"key": "storage.sqlite.db_path", "value": "data/other.db"}]})
    assert len(list((config_dir / "old").rglob("storage.toml"))) == 1  # 备份在 config/old/<批次>/


def _leaf_fields(fields: list[dict]) -> list[dict]:
    """递归收集叶子字段（容器字段带 children）。"""
    return [leaf for f in fields for leaf in (_leaf_fields(f["children"]) if "children" in f else [f])]


def test_schema_is_self_described_per_root(client) -> None:
    body = client.get("/api/v1/config/schema").json()
    groups = {g["key"]: g for g in body["groups"]}
    assert body["version"] == "1.0.0"
    assert set(groups) == {"agents", "collectors", "tools", "avatar", "model", "storage", "infra"}
    for key, group in groups.items():
        assert group["file_name"] == f"{key}.toml" and group["label"] and group["file_label"]
        for field in _leaf_fields(group["fields"]):
            assert {"key", "type", "label"} <= set(field)
    # 字段 key 带 scope 前缀，与写接口键约定一致
    assert all(f["key"].startswith("tools.") for f in _leaf_fields(groups["tools"]["fields"]))
    # meta.version 的 readonly 标记透传（与写接口拒绝共用信号）
    version = next(f for f in _leaf_fields(groups["agents"]["fields"]) if f["key"] == "agents.meta.version")
    assert version["readonly"] is True


def test_patch_agents_domain_hot_applies_and_reports(client) -> None:
    """agents 域保存即回执"已热应用"：无管理器时组件未运行 → requires_restart=False + 跳过明细。"""
    resp = _patch(client, "agents.agents.streamer.persona.bot_name", "热应用名")
    body = resp.json()
    assert resp.status_code == 200 and body["success"] is True
    assert body["requires_restart"] is False
    assert "未运行组件将在下次启动生效" in body["message"] and "streamer" in body["message"]
