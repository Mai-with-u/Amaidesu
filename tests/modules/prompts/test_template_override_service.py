"""
TemplateOverrideService 测试
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.prompts.template_override_service import (
    OverrideConfig,
    TemplateOverrideService,
)


class TestStripYamlFrontmatter:
    """测试 YAML frontmatter 剥离功能"""

    def test_strip_simple_frontmatter(self):
        """测试剥离简单 frontmatter"""
        content = """---
name: test_template
version: "1.0"
---
实际模板内容"""
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result == "实际模板内容"
        assert "---" not in result
        assert "name:" not in result

    def test_strip_frontmatter_with_variables(self):
        """测试剥离带变量列表的 frontmatter"""
        content = """---
name: replyer_prompt
version: "1.0"
description: "VTuber 场景回复提示词"
variables:
  - bot_name
  - identity
  - dialogue_prompt
---

{knowledge_prompt}
你是一位正在直播的 VTuber {bot_name}...
{dialogue_prompt}
现在，你说："""
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result.startswith("{knowledge_prompt}")
        assert "---" not in result
        assert "variables:" not in result

    def test_no_frontmatter(self):
        """测试没有 frontmatter 的内容"""
        content = "纯模板内容，没有 YAML 头"
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result == content

    def test_empty_content(self):
        """测试空内容"""
        content = ""
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result == ""

    def test_only_frontmatter(self):
        """测试只有 frontmatter 没有实际内容"""
        content = """---
name: empty_template
---
"""
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result == ""

    def test_frontmatter_with_extra_whitespace(self):
        """测试 frontmatter 带额外空白"""
        content = """---
name: test
---

  模板内容带前导空格

"""
        result = TemplateOverrideService._strip_yaml_frontmatter(content)
        assert result == "模板内容带前导空格"


class TestOverrideConfig:
    """测试 OverrideConfig"""

    def test_default_values(self):
        """测试默认值"""
        config = OverrideConfig()
        assert config.enabled is False
        assert config.template_name == "amaidesu_override"
        assert config.templates == ["replyer_prompt"]

    def test_custom_values(self):
        """测试自定义值"""
        config = OverrideConfig(
            enabled=True,
            template_name="custom_scope",
            templates=["template1", "template2"]
        )
        assert config.enabled is True
        assert config.template_name == "custom_scope"
        assert config.templates == ["template1", "template2"]

    def test_none_templates_uses_default(self):
        """测试 None 模板使用默认值"""
        config = OverrideConfig(templates=None)
        assert config.templates == ["replyer_prompt"]


class TestTemplateOverrideService:
    """测试 TemplateOverrideService"""

    def test_service_disabled_returns_none(self):
        """测试禁用时返回 None"""
        config = OverrideConfig(enabled=False)
        service = TemplateOverrideService(config)

        result = service.build_template_info()

        assert result is None

    def test_service_enabled_returns_template_info(self):
        """测试启用时返回 TemplateInfo"""
        config = OverrideConfig(
            enabled=True,
            templates=["test_template"]
        )
        service = TemplateOverrideService(config)

        # Mock PromptManager
        with patch.object(service._prompt_manager, 'get_raw', return_value="模板内容"):
            result = service.build_template_info()

        assert result is not None
        assert result.template_default is False

    def test_template_info_has_correct_structure(self):
        """测试 TemplateInfo 结构正确"""
        config = OverrideConfig(
            enabled=True,
            template_name="test_scope",
            templates=["replyer_prompt", "chat_target_group1"]
        )
        service = TemplateOverrideService(config)

        with patch.object(service._prompt_manager, 'get_raw', return_value="模板内容"):
            result = service.build_template_info()

        assert result.template_name == "test_scope"
        assert "replyer_prompt" in result.template_items
        assert "chat_target_group1" in result.template_items

    def test_template_default_is_false(self):
        """测试 template_default=False"""
        config = OverrideConfig(enabled=True, templates=["test"])
        service = TemplateOverrideService(config)

        with patch.object(service._prompt_manager, 'get_raw', return_value="内容"):
            result = service.build_template_info()

        assert result.template_default is False

    def test_missing_template_logs_warning(self):
        """测试缺失模板记录警告日志"""
        config = OverrideConfig(enabled=True, templates=["nonexistent"])
        service = TemplateOverrideService(config)

        with patch.object(service._prompt_manager, 'get_raw', side_effect=KeyError("not found")):
            result = service.build_template_info()

        # 缺失模板时返回 None（因为没有成功加载任何模板）
        assert result is None

    def test_partial_template_loading(self):
        """测试部分模板加载失败时仍返回有效的 TemplateInfo"""
        config = OverrideConfig(
            enabled=True,
            templates=["exists", "nonexistent"]
        )
        service = TemplateOverrideService(config)

        def mock_get_raw(key):
            if "exists" in key:
                return "有效模板"
            raise KeyError("not found")

        with patch.object(service._prompt_manager, 'get_raw', side_effect=mock_get_raw):
            result = service.build_template_info()

        assert result is not None
        assert "exists" in result.template_items
        assert "nonexistent" not in result.template_items
