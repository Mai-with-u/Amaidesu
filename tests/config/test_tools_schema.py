"""Tools 配置 Schema 测试（v2.0.31 工具域重构后）

测试 src/modules/config/tools_schemas.py：
1. ToolsRootConfig 根结构（tools 子段）
2. ToolsConfig 聚合：异步任务基建 + 各提供者分类（avatar/studio/vision/memory/mcp）
3. ToolProviderConfig 开关语义（enabled + config）
4. avatar/studio 动态子段（Dict[str, ProviderConfig]）
5. [tools.tasks] 段定义 + 默认值
6. [tools.memory].enabled 默认 true
7. 顶层禁用 perception/output（已迁出）
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.config.tools_schemas import (
    AvatarProviderConfig,
    McpProviderConfig,
    MemoryProviderConfig,
    StudioProviderConfig,
    ToolProviderConfig,
    ToolsConfig,
    ToolsRootConfig,
    ToolsTasksConfig,
    VisionProviderConfig,
)


class TestToolsRootConfig:
    def test_default_construction(self):
        cfg = ToolsRootConfig()
        assert isinstance(cfg.tools, ToolsConfig)

    def test_tools_section_present(self):
        cfg = ToolsRootConfig()
        assert cfg.tools is not None


class TestToolsTasksConfig:
    def test_default_values(self):
        cfg = ToolsTasksConfig()
        assert cfg.poll_interval_ms == 2000
        assert cfg.wait_timeout_ms == 1_800_000

    def test_poll_interval_ge_100(self):
        with pytest.raises(ValidationError):
            ToolsTasksConfig(poll_interval_ms=50)

    def test_wait_timeout_ge_1000(self):
        with pytest.raises(ValidationError):
            ToolsTasksConfig(wait_timeout_ms=500)

    def test_tools_tasks_under_tools(self):
        cfg = ToolsConfig()
        assert isinstance(cfg.tasks, ToolsTasksConfig)
        assert cfg.tasks.poll_interval_ms == 2000


class TestToolsConfigDisabledLegacyFields:
    def test_perception_field_rejected(self):
        """顶层 perception 段已迁出至 collectors.toml，应被 extra=forbid 拒绝"""
        with pytest.raises(ValidationError) as exc_info:
            ToolsConfig.model_validate({"perception": {"enabled": True}})
        assert "perception" in str(exc_info.value).lower()

    def test_output_field_rejected(self):
        """顶层 output 段已迁出至 infra.toml，应被 extra=forbid 拒绝"""
        with pytest.raises(ValidationError) as exc_info:
            ToolsConfig.model_validate({"output": {"enabled": True}})
        assert "output" in str(exc_info.value).lower()

    def test_enabled_field_rejected(self):
        """原包级 enabled 列表已废除（ToolPackType 已删除）"""
        with pytest.raises(ValidationError):
            ToolsConfig.model_validate({"enabled": ["perception"]})


class TestToolsConfigDomains:
    def test_domains_default_empty_or_none(self):
        """禁 None 契约：动态分类缺省空 dict；单实例分类缺省具体实例。

        vision 缺省关态（enabled=False，等价旧 None 语义）；
        memory 缺省 enabled=true（与消费端兜底对齐）。
        """
        cfg = ToolsConfig()
        assert cfg.avatar == {}
        assert cfg.studio == {}
        assert cfg.vision.enabled is False
        assert cfg.memory.enabled is True
        assert isinstance(cfg.mcp, McpProviderConfig)


class TestToolProviderConfig:
    def test_default_enabled_true(self):
        cfg = ToolProviderConfig()
        assert cfg.enabled is True
        assert cfg.config == {}

    def test_custom_enabled_and_config(self):
        cfg = ToolProviderConfig(enabled=False, config={"key": "value"})
        assert cfg.enabled is False
        assert cfg.config == {"key": "value"}


class TestAvatarStudioDomains:
    def test_avatar_accepts_dynamic_subdomains(self):
        cfg = ToolsConfig(avatar={"vts": {"enabled": True, "config": {}}})
        assert cfg.avatar["vts"].enabled is True

    def test_studio_accepts_dynamic_subdomains(self):
        cfg = ToolsConfig(studio={"obs": {"enabled": True, "config": {}}})
        assert cfg.studio["obs"].enabled is True

    def test_domain_extra_allowed(self):
        cfg = AvatarProviderConfig(enabled=True, extra_field="x")
        assert cfg.extra_field == "x"


class TestSpecializedDomains:
    def test_vision(self):
        cfg = VisionProviderConfig(enabled=True, config={"default_max_width": 1280})
        assert cfg.enabled is True

    def test_memory_default_enabled_true(self):
        """记忆分类默认 enabled=true（消除配置漂移）"""
        cfg = MemoryProviderConfig()
        assert cfg.enabled is True
        assert cfg.config == {}

    def test_mcp(self):
        cfg = McpProviderConfig(enabled=True, config={"servers": {}})
        assert cfg.config == {"servers": {}}

    def test_studio_specialized(self):
        cfg = StudioProviderConfig(enabled=True)
        assert cfg.enabled is True


class TestJsonSchemaExtra:
    def test_tasks_has_ui_metadata(self):
        field_info = ToolsConfig.model_fields["tasks"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "object"
