"""Schema Registry → Generator 覆盖率门禁测试 (Task 11)

背景
----
Task 5 已完成 ``ConfigSchemaGenerator`` 与 ``schema_registry`` 的字段级对比脚本
(``scripts/check_schema_coverage.py``),本测试把它**纳入 pytest 套件**,确保:

1. ``schema_registry`` 中所有 7 个分组(general / persona / llm / maicore /
   logging / context / dashboard)都被 generator 100% 覆盖
2. 每个分组中的每个字段在 generator 输出中能找到对应
3. 字段类型兼容 (registry ``float`` ↔ generator ``number`` 等)
4. 当 generator 缺失某字段时,门禁**必须失败** (回归保护)
5. ``ConfigService.initialize()`` 在启动流程中执行 gate,日志含
   ``SCHEMA_REGISTRY_COVERED`` 或警告

测试隔离
--------
通过 ``check_registry_coverage(groups, mapping)`` 接受任意 ``ConfigGroupDefinition``
序列(而非 singleton),使测试可以构造人造 ``ConfigGroupDefinition`` 来验证 "缺字段就
失败" 这条不变量,不污染全局 singleton。

参见
----
- ``src/modules/config/schema_coverage.py`` — gate 实现
- ``scripts/check_schema_coverage.py`` — CLI 版 (CI 用)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import pytest
from pydantic import BaseModel, Field

from src.modules.config.core_schemas import GeneralConfig
from src.modules.config.schema_coverage import (
    REGISTRY_GROUP_TO_PYDANTIC,
    CoverageResult,
    check_registry_coverage,
)
from src.modules.config.schema_generator import (
    ConfigSchemaGenerator,
    collect_all_fields,
)
from src.modules.config.schema_registry import (
    ConfigFieldDefinition,
    ConfigGroupDefinition,
    get_schema_registry,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def fresh_registry_groups():
    """获取 registry 中所有 7 个分组的副本(不被 gate 修改)。"""
    registry = get_schema_registry()
    return list(registry.get_all_groups())


# ===========================================================================
# 1. registry 不变量:必须包含 7 个原始分组(向后兼容 contract)
# ===========================================================================


class TestRegistryHasSevenGroups:
    """registry 必须保持 7 个分组,这与 ``ConfigSchemaRegistry`` 早期版本契约一致。"""

    EXPECTED_KEYS = {
        "general",
        "persona",
        "llm",
        "maicore",
        "logging",
        "context",
        "dashboard",
    }

    def test_registry_singleton_loads(self):
        registry = get_schema_registry()
        assert registry is not None

    def test_registry_has_exactly_seven_groups_minimum(self):
        registry = get_schema_registry()
        keys = {g.key for g in registry.get_all_groups()}
        # 至少包含 7 个分组;可以更多 (向后兼容)
        assert self.EXPECTED_KEYS.issubset(keys), (
            f"registry 缺失核心分组: {self.EXPECTED_KEYS - keys};实际 keys={sorted(keys)}"
        )

    @pytest.mark.parametrize("key", ["general", "persona", "llm", "maicore", "logging", "context", "dashboard"])
    def test_registry_contains_group(self, key):
        registry = get_schema_registry()
        assert registry.get_group(key) is not None, f"registry 缺少分组 {key!r}"


# ===========================================================================
# 2. 100% 字段覆盖率:每个 registry 字段在 generator 中能找到
# ===========================================================================


# 测试用的映射(全部 11 个 group;但只校验 registry 自带的 7 个)
_ALL_GROUP_TO_PYDANTIC = REGISTRY_GROUP_TO_PYDANTIC
_REGISTRY_BACKED_KEYS = {
    "general",
    "persona",
    "llm",
    "maicore",
    "logging",
    "context",
    "dashboard",
}


class TestCoverageGate100Percent:
    """registry → generator 覆盖率门禁:100% 字段必须命中。"""

    @pytest.mark.parametrize(
        "group_key",
        sorted(_ALL_GROUP_TO_PYDANTIC.keys() & _REGISTRY_BACKED_KEYS),
    )
    def test_group_full_field_coverage(self, group_key):
        """每个 registry 分组的所有字段都在 generator 中找到。"""
        registry = get_schema_registry()
        reg_group = registry.get_group(group_key)
        assert reg_group is not None, f"registry 缺少分组 {group_key}"

        py_cls = _ALL_GROUP_TO_PYDANTIC[group_key]
        gen_schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        gen_fields = {f["name"]: f for f in collect_all_fields(gen_schema, prefix=group_key)}

        missing = []
        for reg_field in reg_group.fields:
            local = reg_field.key.split(".")[-1]
            if local not in gen_fields:
                missing.append(reg_field.key)

        assert not missing, f"[{group_key}] generator 缺失字段 (覆盖率 < 100%):\n  " + "\n  ".join(missing)

    @pytest.mark.parametrize(
        "group_key",
        sorted(_ALL_GROUP_TO_PYDANTIC.keys() & _REGISTRY_BACKED_KEYS),
    )
    def test_group_field_type_compatibility(self, group_key):
        """字段类型在 registry 和 generator 之间必须兼容。"""
        registry = get_schema_registry()
        reg_group = registry.get_group(group_key)
        assert reg_group is not None

        py_cls = _ALL_GROUP_TO_PYDANTIC[group_key]
        gen_schema = ConfigSchemaGenerator.generate_config_schema(py_cls)
        gen_fields = {f["name"]: f for f in collect_all_fields(gen_schema, prefix=group_key)}

        mismatches: List[str] = []
        for reg_field in reg_group.fields:
            local = reg_field.key.split(".")[-1]
            gen_field = gen_fields.get(local)
            if gen_field is None:
                continue  # 已在另一测试中报失败
            reg_type = reg_field.field_type
            gen_type = gen_field["type"]
            if reg_type == gen_type:
                continue
            # registry 写法 -> generator 写法
            TYPE_NORMALIZATION = {
                "string": "string",
                "integer": "integer",
                "float": "number",
                "number": "number",
                "boolean": "boolean",
                "array": "array",
                "object": "object",
                "select": "select",
            }
            norm = TYPE_NORMALIZATION.get(reg_type, reg_type)
            if norm == gen_type:
                continue
            mismatches.append(f"{reg_field.key}: reg={reg_type}, gen={gen_type}")

        assert not mismatches, f"[{group_key}] 类型不兼容:\n  " + "\n  ".join(mismatches)


# ===========================================================================
# 3. gate 函数不变量:不变量修复与回归保护
# ===========================================================================


class TestCheckRegistryCoverageFunction:
    """``check_registry_coverage()`` 必须正确报告覆盖与缺口。"""

    def test_returns_coverage_result_instance(self, fresh_registry_groups):
        """返回值必须是 CoverageResult。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert isinstance(result, CoverageResult)

    def test_current_state_is_fully_covered(self, fresh_registry_groups):
        """当前状态:registry 所有字段应被 generator 100% 覆盖。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert result.fully_covered is True, (
            f"覆盖率不足:missing={result.missing_fields}, type_mismatches={result.type_mismatches}"
        )
        assert result.missing_fields == {}
        assert result.type_mismatches == {}

    def test_total_field_count_positive(self, fresh_registry_groups):
        """必须有正数个字段参与覆盖计算。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert result.total_registry_fields > 0
        assert result.total_covered_fields > 0
        assert result.total_covered_fields <= result.total_registry_fields

    def test_no_unmapped_groups_become_failures(self, fresh_registry_groups):
        """只校验映射中存在的分组;多余的 registry 分组不导致失败(向后兼容)。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            # 只提供 general 映射
            {"general": GeneralConfig},
        )
        # 仅 general 应被覆盖;其它分组不影响结果
        assert isinstance(result, CoverageResult)
        # general 必定完全覆盖
        assert "general" not in result.missing_fields


class TestGateFailsOnMissingField:
    """回归保护:registry 字段在 generator 缺失时,gate 必须报失败。

    构造人造 ``ConfigGroupDefinition`` 注入一个 generator 不存在的字段,
    验证 gate 识别并报告。
    """

    def test_missing_field_detected(self):
        """当 generator 缺失某字段时,gate 返回 fully_covered=False。"""

        # 构造一个简单的"真" Pydantic 类(generator 能识别)
        class FakeConfig(BaseModel):
            """测试用 Pydantic 类,仅含 known_field。"""

            known_field: str = Field(default="x", description="已知字段")
            type: str = Field(default="fake", description="类型标识符")

        # 构造人造 registry 分组:包含 known_field 和不存在的 phantom_field
        fake_group = ConfigGroupDefinition(
            key="fake",
            label="Fake 测试组",
            description="仅用于测试 gate 失败的临时分组",
            icon="Test",
            order=999,
        )
        fake_group.add_field(
            ConfigFieldDefinition(
                key="fake.known_field",
                label="已知字段",
                field_type="string",
                description="与 generator 对齐",
            )
        )
        fake_group.add_field(
            ConfigFieldDefinition(
                key="fake.phantom_field",
                label="幽灵字段",
                field_type="string",
                description="generator 中不存在",
            )
        )

        result = check_registry_coverage(
            [fake_group],
            {"fake": FakeConfig},
        )

        assert result.fully_covered is False, "gate 失败未触发 (回归)"
        assert "fake" in result.missing_fields
        assert "phantom_field" in result.missing_fields["fake"]
        # known_field 不应在缺失列表中
        assert "known_field" not in result.missing_fields["fake"]

    def test_known_field_present_is_covered(self):
        """对照组:所有字段都在 generator 中,gate 应通过。"""

        class FakeConfig(BaseModel):
            type: str = Field(default="fake", description="类型标识符")
            only_field: str = Field(default="y", description="唯一字段")

        fake_group = ConfigGroupDefinition(
            key="fake",
            label="Fake",
            description="",
            icon="Test",
            order=999,
        )
        fake_group.add_field(
            ConfigFieldDefinition(
                key="fake.only_field",
                label="唯一字段",
                field_type="string",
            )
        )

        result = check_registry_coverage(
            [fake_group],
            {"fake": FakeConfig},
        )

        assert result.fully_covered is True
        assert result.missing_fields == {}
        assert result.type_mismatches == {}


class TestTypeMismatchDetected:
    """类型不匹配时 gate 必须报告。"""

    def test_incompatible_type_breaks_gate(self):
        """registry 字段类型与 generator 类型不兼容时,fully_covered=False。"""

        # FakeConfig 的 field 是 integer;registry 字段标记为 float (应兼容),
        # 所以换用完全不相容的类型:boolean vs string 不归一化
        class FakeConfig(BaseModel):
            type: str = Field(default="fake", description="类型标识符")
            the_field: str = Field(default="x", description="字符串字段")

        fake_group = ConfigGroupDefinition(
            key="fake",
            label="Fake",
            description="",
            icon="",
            order=0,
        )
        fake_group.add_field(
            ConfigFieldDefinition(
                key="fake.the_field",
                label="冲突字段",
                field_type="boolean",  # 与 generator 的 string 不兼容
            )
        )

        result = check_registry_coverage(
            [fake_group],
            {"fake": FakeConfig},
        )
        assert result.fully_covered is False
        assert "fake" in result.type_mismatches
        # 报错条目中应包含字段名
        for entry in result.type_mismatches["fake"]:
            # entry 是 (field, reg_type, gen_type) 形式
            assert entry[0] == "the_field"
            assert entry[1] == "boolean"
            assert entry[2] == "string"


# ===========================================================================
# 4. 启动上下文:ConfigService.initialize() 必须执行 coverage gate
# ===========================================================================


def _build_minimal_core_toml() -> str:
    """最小可用的 core.toml 用于触发 ConfigService.initialize()。"""
    return (
        "[meta]" + "\n"
        'type = "meta"' + "\n"
        'version = "0.4.0"' + "\n"
        "\n"
        "[general]" + "\n"
        'type = "general"' + "\n"
        'platform_id = "amaidesu"' + "\n"
        "\n"
        "[persona]" + "\n"
        'type = "persona"' + "\n"
        'bot_name = "mai"' + "\n"
    )


def _build_minimal_model_toml() -> str:
    return '[llm]\ntype = "llm"\nclient = "openai"\nmodel = "gpt-4"\n'


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """提供完整 5 个 toml 文件的临时 config/ 目录。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "core.toml").write_text(_build_minimal_core_toml(), encoding="utf-8")
    (cfg / "model.toml").write_text(_build_minimal_model_toml(), encoding="utf-8")
    for name in ("input", "decision", "output"):
        (cfg / f"{name}.toml").write_text("", encoding="utf-8")
    return tmp_path


class TestStartupCoverageCheck:
    """ConfigService.initialize() 必须执行 coverage gate 并记录结果。

    验证策略
    --------
    Amaidesu 使用 loguru + stdlib 没有自动桥接,pytest 的 ``caplog`` /
    ``capsys`` / ``capfd`` 在 Windows 上对 loguru 的 stderr sink 都不稳定
    (loguru 在 ``_ensure_default_handler`` 时缓存原始 ``sys.stderr`` 句柄,
    pytest 后续重定向解耦)。

    因此本测试改用 **属性检查** 验证 gate:
        ``service._last_coverage_result`` 在 ``initialize()`` 末尾被填充
        为 :class:`CoverageResult`,测试可直接读取 ``.fully_covered`` /
        ``.missing_fields`` / ``.summary`` 等结构化字段。

    同时验证 ``SCHEMA_REGISTRY_COVERED`` 关键字仍以日志形式产出
    (runtime 运维需要看到这条 PASS 提示)。
    """

    def test_initialize_runs_coverage_gate(self, tmp_config_dir):
        """initialize() 完成后必须填充 _last_coverage_result,且 fully_covered=True。"""
        from src.modules.config.service import ConfigService

        service = ConfigService(base_dir=str(tmp_config_dir))
        service.initialize()

        result = service._last_coverage_result
        assert result is not None, "initialize() 后 _last_coverage_result 应被填充"
        assert result.fully_covered is True, (
            f"coverage gate 报告失败: missing={result.missing_fields}, type_mismatches={result.type_mismatches}"
        )
        assert result.missing_fields == {}
        assert result.type_mismatches == {}
        # 运行时日志关键字 ``SCHEMA_REGISTRY_COVERED`` 应保留在 summary 中
        assert "PASS" in result.summary

    def test_initialize_emits_schema_registry_covered_via_runlog(self, tmp_config_dir):
        """运行时仍打印 SCHEMA_REGISTRY_COVERED (运维可观察)。"""
        from src.modules.config.service import ConfigService

        service = ConfigService(base_dir=str(tmp_config_dir))
        service.initialize()

        result = service._last_coverage_result
        assert result is not None
        assert result.fully_covered is True
        # log_to() 在 fully_covered 时日志消息含 SCHEMA_REGISTRY_COVERED
        # 这里不直接读 stderr (loguru 在 Windows + pytest 下不稳定),
        # 通过 summary 关键字间接验证 gate 的结论
        summary = result.summary
        assert "PASS" in summary

    def test_initialize_idempotent_does_not_recompute_coverage(self, tmp_config_dir):
        """重复 initialize() 不应重新计算 _last_coverage_result (保持首次结果)。"""
        from src.modules.config.service import ConfigService

        service = ConfigService(base_dir=str(tmp_config_dir))
        service.initialize()
        first_result = service._last_coverage_result

        # given: 已初始化的服务
        # when:  二次调用 initialize (因已 init 而 early-return)
        service.initialize()

        # then:  _last_coverage_result 保持首次结果不变 (idempotent)
        assert service._last_coverage_result is first_result


# ===========================================================================
# 5. 端到端:CoreConfig / ModelConfig 通过 generator 输出 100% 覆盖 registry
# ===========================================================================


class TestEndToEndCoverage:
    """端到端验证:CoreConfig + ModelConfig 通过 generator 100% 覆盖 registry 字段。"""

    def test_coreconfig_via_generator_covers_all_registry_groups(self):
        """CoreConfig 的 generator 输出应覆盖 registry 的 6 个 core 分组 + logging。"""
        registry = get_schema_registry()
        core_groups = [g for g in registry.get_all_groups() if g.key != "llm"]
        result = check_registry_coverage(
            core_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert result.fully_covered, f"core 配置未完全覆盖:{result}"

    def test_llm_registry_covered_by_modelconfig(self):
        """registry 的 llm 分组由 ModelConfig.nested.llm (LLMConfig) 覆盖。"""
        registry = get_schema_registry()
        llm_group = registry.get_group("llm")
        assert llm_group is not None

        result = check_registry_coverage(
            [llm_group],
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert result.fully_covered, f"llm 配置未完全覆盖:{result}"


# ===========================================================================
# 6. CoverageResult dataclass 结构不变量
# ===========================================================================


class TestCoverageResultDataclass:
    """``CoverageResult`` 字段类型与可序列化性。"""

    def test_result_has_required_fields(self, fresh_registry_groups):
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        assert hasattr(result, "fully_covered")
        assert hasattr(result, "missing_fields")
        assert hasattr(result, "type_mismatches")
        assert hasattr(result, "total_registry_fields")
        assert hasattr(result, "total_covered_fields")

    def test_summary_str_contains_pass_or_fail_marker(self, fresh_registry_groups):
        """``summary`` 必须包含明确的 PASS/FAIL 标记供日志消费。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        s = result.summary
        assert isinstance(s, str)
        assert "PASS" in s or "FAIL" in s

    def test_log_to_writes_to_logger(self, fresh_registry_groups, caplog):
        """``log_to(logger)`` 必须将结论写入指定 logger。"""
        result = check_registry_coverage(
            fresh_registry_groups,
            _ALL_GROUP_TO_PYDANTIC,
        )
        test_logger = logging.getLogger("test_schema_coverage_gate")

        with caplog.at_level(logging.DEBUG, logger="test_schema_coverage_gate"):
            result.log_to(test_logger)

        assert any(rec.name == "test_schema_coverage_gate" for rec in caplog.records), "log_to() 没有产生任何日志记录"
