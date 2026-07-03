"""TOML Writer 统一化测试 (Task 13)

目标：把 ``src/modules/config/schemas/generator.py`` 中的 ``tomli_w`` 依赖
替换为 ``tomlkit``，使整个仓库只保留一种 TOML writer。

覆盖范围
--------

1. ``tomli_w`` 不再被 ``schemas.generator`` 模块导入
2. 新的 ``merge_config_with_tomlkit`` 写出可被 ``tomlkit`` 重新解析的 TOML
3. ``tomlkit`` 写出的 TOML 保留注释（注释来自 Pydantic Field description）
4. ``generate_toml``（手动字符串拼接的旧入口）继续输出合法 TOML，
   内容语义不变
5. Pydantic schema 生成、深度合并、基本类型等场景 round-trip 一致

参考：
- ``src/modules/config/schemas/generator.py``  —— 被测对象
- ``src/modules/config/toml_utils.py``         —— tomlkit 已有的使用范例
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import tomlkit
from pydantic import BaseModel, Field


# ===========================================================================
# 测试夹具：定义一个最小可用的 Pydantic schema
# ===========================================================================


class _SampleSchema(BaseModel):
    """演示 schema —— 用于统一 TOML writer 测试。"""

    host: str = Field(default="localhost", description="服务器主机地址")
    port: int = Field(default=8080, description="服务器端口")
    enabled: bool = Field(default=True, description="是否启用")
    tags: list[str] = Field(default_factory=lambda: ["alpha", "beta"], description="标签列表")
    optional_name: Optional[str] = Field(default=None, description="可选名称")


# ===========================================================================
# 模块级：tomli_w 必须从 generator 模块移除
# ===========================================================================


class TestNoTomliWInGenerator:
    """``schemas.generator`` 模块不得再依赖 ``tomli_w``。"""

    def test_tomli_w_not_imported_as_module_attribute(self):
        """模块顶层不应有名为 ``tomli_w`` 的属性。"""
        import src.modules.config.schemas.generator as gen_mod

        assert not hasattr(gen_mod, "tomli_w"), "generator.py 不应再 import tomli_w，请改用 tomlkit"

    def test_tomlkit_imported_as_module_attribute(self):
        """模块顶层应已导入 tomlkit（以备使用）。"""
        import src.modules.config.schemas.generator as gen_mod

        assert hasattr(gen_mod, "tomlkit"), "generator.py 应 import tomlkit 作为 TOML writer"

    def test_tomli_w_function_renamed_or_removed(self):
        """``merge_config_with_tomli_w`` 必须改名/移除，避免误导调用方。"""
        import src.modules.config.schemas.generator as gen_mod

        assert not hasattr(gen_mod, "merge_config_with_tomli_w"), (
            "merge_config_with_tomli_w 必须改名为基于 tomlkit 的实现"
        )


# ===========================================================================
# merge_config_with_tomlkit：tomlkit 写入路径的核心契约
# ===========================================================================


class TestMergeConfigWithTomlkit:
    """``merge_config_with_tomlkit`` 必须按 tomlkit 写出合法 TOML。"""

    def test_creates_output_file(self, tmp_path: Path):
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "out.toml"
        merge_config_with_tomlkit(
            base_config={"provider": {"host": "localhost", "port": 8080}},
            override_config={},
            output_path=str(output),
        )

        assert output.exists(), "merge_config_with_tomlkit 应写出目标文件"
        assert output.stat().st_size > 0, "输出文件不应为空"

    def test_output_is_valid_toml_parseable_by_tomlkit(self, tmp_path: Path):
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "out.toml"
        merge_config_with_tomlkit(
            base_config={"provider": {"host": "localhost", "port": 8080}},
            override_config={},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert loaded["provider"]["host"] == "localhost"
        assert loaded["provider"]["port"] == 8080

    def test_override_merges_into_base(self, tmp_path: Path):
        """override 的值应覆盖 base 中同名 key。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "out.toml"
        merge_config_with_tomlkit(
            base_config={"provider": {"host": "localhost", "port": 8080}},
            override_config={"provider": {"port": 9090}},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert loaded["provider"]["host"] == "localhost"
        assert loaded["provider"]["port"] == 9090

    def test_nested_dict_merge(self, tmp_path: Path):
        """深度合并：嵌套字典递归合并，非字典值覆盖。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "out.toml"
        merge_config_with_tomlkit(
            base_config={"a": {"x": 1, "y": 2}, "b": 1},
            override_config={"a": {"y": 20, "z": 30}},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert loaded["a"]["x"] == 1
        assert loaded["a"]["y"] == 20
        assert loaded["a"]["z"] == 30
        assert loaded["b"] == 1

    def test_preserves_basic_types_round_trip(self, tmp_path: Path):
        """TOML 基础类型（str/int/float/bool/list）round-trip 不丢失。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "out.toml"
        merge_config_with_tomlkit(
            base_config={
                "cfg": {
                    "s": "hello",
                    "i": 42,
                    "f": 3.14,
                    "b": True,
                    "lst": ["a", "b", "c"],
                }
            },
            override_config={},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert loaded["cfg"]["s"] == "hello"
        assert loaded["cfg"]["i"] == 42
        assert loaded["cfg"]["f"] == 3.14
        assert loaded["cfg"]["b"] is True
        assert loaded["cfg"]["lst"] == ["a", "b", "c"]

    def test_writes_in_text_mode_not_binary(self, tmp_path: Path):
        """tomlkit 需要文本模式；输出文件应能被 ``open(..., 'r', encoding='utf-8')`` 读取。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        output = tmp_path / "utf.toml"
        merge_config_with_tomlkit(
            base_config={"x": "中文键值"},
            override_config={},
            output_path=str(output),
        )

        # 文本模式可读取；含中文
        content = output.read_text(encoding="utf-8")
        assert "中文键值" in content


# ===========================================================================
# generate_toml：旧的字符串拼接入口 —— 内容语义保持不变
# ===========================================================================


class TestGenerateTomlBackwardCompatibility:
    """``generate_toml`` 仍应输出合法 TOML，描述作为注释。"""

    def test_generates_section_header(self):
        from src.modules.config.schemas.generator import generate_toml

        text = generate_toml(_SampleSchema, "sample")
        assert "[sample]" in text

    def test_contains_field_values(self):
        from src.modules.config.schemas.generator import generate_toml

        text = generate_toml(_SampleSchema, "sample")
        assert 'host = "localhost"' in text
        assert "port = 8080" in text
        assert "enabled = true" in text

    def test_contains_description_comments(self):
        """Pydantic Field 的 description 应作为注释（以 # 开头）出现。"""
        from src.modules.config.schemas.generator import generate_toml

        text = generate_toml(_SampleSchema, "sample")
        assert "#" in text
        assert "服务器主机地址" in text
        assert "服务器端口" in text

    def test_output_parseable_by_tomlkit(self):
        """生成的字符串必须能被 tomlkit 解析回 dict。"""
        from src.modules.config.schemas.generator import generate_toml

        text = generate_toml(_SampleSchema, "sample")
        loaded = tomlkit.loads(text)
        assert loaded["sample"]["host"] == "localhost"
        assert loaded["sample"]["port"] == 8080
        assert loaded["sample"]["enabled"] is True
        assert loaded["sample"]["tags"] == ["alpha", "beta"]

    def test_round_trip_with_tomlkit(self):
        """generate_toml → tomlkit.loads → tomlkit.dumps → tomlkit.loads 保持等价。"""
        from src.modules.config.schemas.generator import generate_toml

        text = generate_toml(_SampleSchema, "sample")
        loaded1 = tomlkit.loads(text)
        dumped = tomlkit.dumps(loaded1)
        loaded2 = tomlkit.loads(dumped)

        assert dict(loaded1["sample"]) == dict(loaded2["sample"])


# ===========================================================================
# 语义等价性：tomlkit 路径与 tomli_w 旧路径输出结构一致
# ===========================================================================


class TestSemanticEquivalence:
    """不同的 writer 写出结构上等价的 TOML（key/value 完全相同）。"""

    def test_simple_dict_round_trip(self, tmp_path: Path):
        """最简字典通过 tomlkit 写 → 读 仍相等。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        original = {"section": {"key": "value", "n": 1}}
        output = tmp_path / "rt.toml"

        merge_config_with_tomlkit(
            base_config=original,
            override_config={},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert dict(loaded["section"]) == original["section"]

    def test_pydantic_default_dump_equivalent_to_merge_output(self, tmp_path: Path):
        """Pydantic 默认值 dict 与通过 tomlkit 写出再读回的 dict 应一致。"""
        from src.modules.config.schemas.generator import merge_config_with_tomlkit

        schema_dict = _SampleSchema().model_dump(exclude_unset=False)
        # 去除 None 值（tomlkit 不支持 None）
        clean = {k: v for k, v in schema_dict.items() if v is not None}

        output = tmp_path / "pyd.toml"
        merge_config_with_tomlkit(
            base_config={"sample": clean},
            override_config={},
            output_path=str(output),
        )

        loaded = tomlkit.loads(output.read_text(encoding="utf-8"))
        assert dict(loaded["sample"]) == clean


# ===========================================================================
# 集成：确保 generate_config_dict 仍然工作
# ===========================================================================


class TestGenerateConfigDict:
    """``generate_config_dict`` 不变；保留对外接口。"""

    def test_returns_section_wrapped_dict(self):
        from src.modules.config.schemas.generator import generate_config_dict

        result = generate_config_dict(_SampleSchema, "sample")
        assert "sample" in result
        assert result["sample"]["host"] == "localhost"
        assert result["sample"]["port"] == 8080
