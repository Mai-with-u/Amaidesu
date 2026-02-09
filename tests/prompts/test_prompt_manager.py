"""PromptManager 单元测试"""

import tempfile
from pathlib import Path

import pytest

from src.prompts import PromptManager, PromptTemplate, TemplateMetadata, get_prompt_manager, reset_prompt_manager


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

            # 严格模式下缺失变量应抛出异常
            with pytest.raises(KeyError):
                manager.render("test")  # 缺少 name 变量

    def test_render_safe_missing_variable(self):
        """测试安全模式下缺失变量保留原样"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建带变量的模板
            template_path = templates_dir / "test.md"
            template_path.write_text("Hello, $name! Today is $day.", encoding="utf-8")

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 安全模式下缺失变量保留原样
            result = manager.render_safe("test", name="Alice")
            assert result == "Hello, Alice! Today is $day."

    def test_get_raw_template(self):
        """测试获取原始模板内容"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建模板
            template_path = templates_dir / "test.md"
            raw_content = """---
description: Test template
---
Hello, $name!
"""
            template_path.write_text(raw_content, encoding="utf-8")

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 获取原始内容
            raw = manager.get_raw("test")
            assert raw == raw_content

    def test_get_metadata(self):
        """测试获取模板元数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建带 frontmatter 的模板
            template_path = templates_dir / "test.md"
            template_path.write_text(
                """---
description: 测试模板
version: 2.0
variables:
  - name
  - age
author: TestAuthor
tags:
  - test
  - example
---
Hello, $name!
""",
                encoding="utf-8",
            )

            # 加载模板
            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            # 获取元数据
            metadata = manager.get_metadata("test")
            assert metadata.name == "test"
            assert metadata.description == "测试模板"
            assert metadata.version == "2.0"
            assert metadata.variables == ["name", "age"]
            assert metadata.author == "TestAuthor"
            assert metadata.tags == ["test", "example"]

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

    def test_render_safe_method(self):
        """测试 PromptTemplate 的 render_safe 方法"""
        metadata = TemplateMetadata(name="test")
        template = PromptTemplate(
            name="test",
            content="Hello, $name! Age: $age",
            raw="Hello, $name! Age: $age",
            metadata=metadata,
            path=Path("/fake/path"),
        )

        result = template.render_safe(name="Bob")
        assert result == "Hello, Bob! Age: $age"


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
        """测试单例自动加载模板"""
        reset_prompt_manager()

        # 使用真实的模板目录
        manager = get_prompt_manager()
        # 验证已初始化（templates_dir 存在）
        assert manager.templates_dir.exists()

        # 列出模板（可能为空，但不应该抛出异常）
        templates = manager.list_templates()
        assert isinstance(templates, list)

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

    def test_template_with_complex_yaml(self):
        """测试复杂 YAML frontmatter 解析"""
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = Path(tmpdir)
            # tempfile.TemporaryDirectory() 已经创建了目录，不需要再 mkdir

            # 创建带复杂 frontmatter 的模板
            template_path = templates_dir / "complex.md"
            template_path.write_text(
                """---
description: 复杂模板
version: 1.0.0
variables: [input, context, output]
author: Test Author
tags: [test, complex, multi-tag]
extra_key: extra_value
---
Content: $input -> $output
""",
                encoding="utf-8",
            )

            manager = PromptManager(templates_dir=tmpdir)
            manager.load_all()

            metadata = manager.get_metadata("complex")
            assert metadata.description == "复杂模板"
            assert metadata.variables == ["input", "context", "output"]
            assert metadata.tags == ["test", "complex", "multi-tag"]

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

            # 验证元数据（应该有默认值）
            metadata = manager.get_metadata("simple")
            assert metadata.name == "simple"
            assert metadata.description is None
            assert metadata.variables == []

            # 验证可以渲染
            result = manager.render("simple", var="test")
            assert result == "Simple content: test"
