"""PromptManager 单元测试"""

import tempfile
from pathlib import Path

import pytest
from loguru import logger

from src.modules.prompts import (
    PromptManager,
    PromptTemplate,
    TemplateMetadata,
    get_prompt_manager,
    reset_prompt_manager,
)


class TestPromptManager:
    """PromptManager 测试类"""

    def test_create_manager(self):
        """测试创建 PromptManager 实例"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PromptManager(templates_dir=tmpdir)
            assert manager.templates_dir == Path(tmpdir)
            assert manager.list_templates() == []

    def test_load_templates(self):
        """测试加载模板文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试模板文件
            templates_dir = Path(tmpdir)
            (templates_dir / "decision").mkdir(parents=True)

            # 创建带 frontmatter 的模板
            template_path = templates_dir / "decision" / "intent.md"
            template_path.write_text(
                """---
description: Intent 决策模板
version: 1.0
variables:
  - user_name
  - message
---
你是一个助手，用户名是 $user_name，消息内容是：$message
""",
                encoding="utf-8",
            )

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 验证模板已加载
            assert "decision/intent" in manager.list_templates()

    def test_render_template(self):
        """测试渲染模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建模板文件
            template_path = templates_dir / "test.md"
            template_path.write_text("Hello, $name! You are $age years old.", encoding="utf-8")

            # 加载并渲染
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            result = manager.render("test", name="Alice", age=18)
            assert result == "Hello, Alice! You are 18 years old."

    def test_render_template_without_variables(self):
        """测试渲染不带变量的模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建简单的静态模板
            template_path = templates_dir / "static.md"
            template_path.write_text("This is a static template.", encoding="utf-8")

            # 加载并渲染
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            result = manager.render("static")
            assert result == "This is a static template."

    def test_render_missing_variable_raises_error(self):
        """测试严格模式下缺失变量抛出 KeyError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建带变量的模板
            template_path = templates_dir / "test.md"
            template_path.write_text("Hello, $name!", encoding="utf-8")

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 严格模式下缺失变量应抛出异常，报错含模板名与缺失变量名
            with pytest.raises(KeyError, match=r"test.*name"):
                manager.render("test")  # 缺少 name 变量

    def test_render_missing_variable_error_lists_context(self):
        """严格渲染缺变量时报错含模板名与缺失变量清单"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            template_path = templates_dir / "greet.md"
            template_path.write_text("Hi $name, today is $day.", encoding="utf-8")

            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            with pytest.raises(KeyError) as exc_info:
                manager.render("greet", name="Alice")
            message = str(exc_info.value)
            assert "greet" in message
            assert "day" in message

    def test_load_all_warns_on_declaration_mismatch(self):
        """frontmatter variables 声明与正文占位符不一致时 load_all 告警不阻断"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            template_path = templates_dir / "drift.md"
            template_path.write_text(
                """---
variables:
  - declared_only
---
Body: $undeclared_var
""",
                encoding="utf-8",
            )

            # 项目日志走 loguru，需挂 sink 捕获告警
            messages: list[str] = []
            handler_id = logger.add(lambda m: messages.append(m), level="WARNING")
            manager = PromptManager(templates_dir=tmpdir)
            try:
                manager.load_all()
            finally:
                logger.remove(handler_id)
            text = "\n".join(messages)

            assert "drift" in text
            assert "undeclared_var" in text
            assert "declared_only" in text
            assert "drift" in manager.list_templates()

    def test_load_all_raises_on_bad_frontmatter(self):
        """frontmatter 语法错误时 load_all 应 fail-fast 抛异常（不静默降级）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            template_path = templates_dir / "bad.md"
            template_path.write_text(
                "---\nbroken: [unclosed\n---\n正文内容",
                encoding="utf-8",
            )

            manager = PromptManager(templates_dir=tmpdir)
            with pytest.raises(Exception) as exc_info:
                manager.load_all()
            assert "bad.md" in str(exc_info.value)

    def test_list_templates(self):
        """测试列出所有模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            (templates_dir / "decision").mkdir(parents=True)
            (templates_dir / "output").mkdir(parents=True)

            # 创建多个模板
            (templates_dir / "decision" / "intent.md").write_text("Intent: $msg", encoding="utf-8")
            (templates_dir / "decision" / "action.md").write_text("Action: $act", encoding="utf-8")
            (templates_dir / "output" / "speech.md").write_text("Say: $text", encoding="utf-8")

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 列出模板
            templates = manager.list_templates()
            assert "decision/intent" in templates
            assert "decision/action" in templates
            assert "output/speech" in templates
            assert len(templates) == 3

    def test_nonexistent_template_raises_error(self):
        """测试访问不存在的模板抛出 KeyError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 不存在的模板应抛出 KeyError
            with pytest.raises(KeyError, match="模板 'nonexistent' 不存在"):
                manager.render("nonexistent")

    def test_nested_directory_structure(self):
        """测试嵌套目录结构的模板加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            (templates_dir / "decision" / "subdir").mkdir(parents=True)

            # 创建嵌套模板
            (templates_dir / "decision" / "subdir" / "deep.md").write_text("Deep template: $var", encoding="utf-8")

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 验证嵌套模板名称
            assert "decision/subdir/deep" in manager.list_templates()
            result = manager.render("decision/subdir/deep", var="test")
            assert result == "Deep template: test"


class TestPromptTemplate:
    """PromptTemplate 测试类"""

    def test_render_method(self):
        """测试 PromptTemplate 的 render 方法"""
        metadata = TemplateMetadata(name="test")
        template = PromptTemplate(
            name="test",
            content="Hello, $name!",
            raw="Hello, $name!",
            metadata=metadata,
            path=Path("/fake/path"),
        )

        result = template.render(name="Bob")
        assert result == "Hello, Bob!"


class TestGlobalSingleton:
    """全局单例测试"""

    def test_get_prompt_manager_singleton(self):
        """测试 get_prompt_manager 返回单例"""
        reset_prompt_manager()

        # 第一次调用创建实例
        manager1 = get_prompt_manager()
        assert isinstance(manager1, PromptManager)

        # 第二次调用返回同一实例
        manager2 = get_prompt_manager()
        assert manager1 is manager2

    def test_reset_prompt_manager(self):
        """测试 reset_prompt_manager 重置单例"""
        reset_prompt_manager()

        # 获取实例
        manager1 = get_prompt_manager()
        manager_id = id(manager1)

        # 重置后获取新实例
        reset_prompt_manager()
        manager2 = get_prompt_manager()

        # 验证是新实例
        assert id(manager2) != manager_id

    def test_singleton_loads_templates(self):
        """测试单例自动加载模板（中央目录可不存在，约定扫描照常发现内聚提示词）"""
        reset_prompt_manager()

        # 使用真实的模板发现机制
        manager = get_prompt_manager()
        # 中央模板目录已随内聚化清空，允许不存在
        templates = manager.list_templates()
        assert isinstance(templates, list)
        assert "amaidesu_replyer" in templates

    def test_template_render_escaped_dollar(self):
        """测试转义的美元符号"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # string.Template 使用 $$ 表示转义的 $
            template_path = templates_dir / "test.md"
            template_path.write_text("Price: $$100", encoding="utf-8")

            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            result = manager.render("test")
            assert result == "Price: $100"

    def test_template_without_frontmatter(self):
        """测试没有 frontmatter 的模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建不带 frontmatter 的模板
            template_path = templates_dir / "simple.md"
            template_path.write_text("Simple content: $var", encoding="utf-8")

            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 验证模板已加载
            assert "simple" in manager.list_templates()

            # 验证可以渲染
            result = manager.render("simple", var="test")
            assert result == "Simple content: test"


class TestDeclarativeKeys:
    """声明式键（frontmatter name 优先）与多根发现测试"""

    def test_frontmatter_name_overrides_path_key(self):
        """frontmatter 声明的 name 应作为注册键，而非文件路径"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            (templates_dir / "decision").mkdir(parents=True)
            template_path = templates_dir / "decision" / "intent.md"
            template_path.write_text(
                "---\nname: my_custom_key\ndescription: 测试\n---\n内容 $var",
                encoding="utf-8",
            )

            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            assert "my_custom_key" in manager.list_templates()
            assert "decision/intent" not in manager.list_templates()

    def test_duplicate_name_raises_valueerror(self):
        """两个模板声明同名键时 load_all 应 fail-fast"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            for filename in ("a.md", "b.md"):
                (templates_dir / filename).write_text("---\nname: same_key\n---\n内容", encoding="utf-8")

            manager = PromptManager(templates_dir=tmpdir)
            with pytest.raises(ValueError, match="模板键冲突"):
                manager.load_all()

    def test_register_scan_root_discovers_extra_dir(self):
        """register_scan_root 注册的目录应被 load_all 发现"""
        with tempfile.TemporaryDirectory() as tmpdir:
            central = Path(tmpdir) / "central"
            central.mkdir()
            component_prompts = Path(tmpdir) / "component" / "prompts"
            component_prompts.mkdir(parents=True)
            (component_prompts / "tool_action.md").write_text(
                "---\nname: tool_action\n---\n动作 $action",
                encoding="utf-8",
            )

            manager = PromptManager(templates_dir=str(central))
            manager.register_scan_root(component_prompts)
            manager.load_all()

            assert manager.render("tool_action", action="挥手") == "动作 挥手"

    def test_bare_manager_does_not_scan_src_by_default(self):
        """裸构造不启用 src 约定扫描，保证测试隔离（中央目录为空则无模板）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()
            assert manager.list_templates() == []

    def test_singleton_enables_auto_scan(self):
        """生产单例应开启 src 约定扫描"""
        reset_prompt_manager()
        try:
            manager = get_prompt_manager()
            assert manager.auto_scan_src is True
        finally:
            reset_prompt_manager()


class TestRealRepoTemplates:
    """真实仓库集成测试：约定扫描发现各组件内聚提示词"""

    def test_singleton_loads_exactly_expected_keys(self):
        """全仓加载后键集合应精确等于声明式键全集（防漂移回归网）"""
        reset_prompt_manager()
        try:
            manager = get_prompt_manager()
            assert set(manager.list_templates()) == {
                "amaidesu_planner_react",
                "amaidesu_replyer",
                "viewer_message",
                "sc_message",
                "passerby_message",
                "warmup_message",
                "persona_generation",
                "amaidesu_minecraft_agent",
                # T4 内联提示词归置新增
                "summary_system",
                "screen_vlm_system",
                "screen_vlm_prompt",
            }
        finally:
            reset_prompt_manager()
