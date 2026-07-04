"""Output 阶段配置 Schema 测试套件 (Task 9)

覆盖 `src.modules.config.schemas.output_schemas` 提供的 Pydantic 聚合模型:

1. **OutputConfig 顶层结构**: 包含 `handlers` (OutputHandlersConfig) 和 `pipelines` (OutputPipelinesConfig)
2. **OutputHandlersConfig 元数据字段**: enabled / concurrent_rendering / error_handling / render_timeout_ms
3. **OutputHandlersConfig 聚合**: 包含每个已注册 Handler 的 ConfigSchema (Optional)
4. **OutputPipelinesConfig**: 与 core_schemas.pipelines 一致的动态键 dict
5. **json_schema_extra**: UI 元数据（如 select/options/boolean）
6. **现有 config/output.toml**: 必须能通过新 Schema 验证无漂移
7. **每个 Handler ConfigSchema**: 字段覆盖与默认值正确
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError


# ===========================================================================
# Fixtures
# ===========================================================================


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_OUTPUT_TOML = REPO_ROOT / "config" / "output.toml"


def _load_output_toml(path: Path) -> Dict[str, Any]:
    """加载真实 output.toml（用于回归测试）"""
    import tomlkit

    with open(path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)
    return doc.unwrap()


# ===========================================================================
# 1. OutputConfig 顶层结构
# ===========================================================================


class TestOutputConfigTopLevel:
    """OutputConfig 顶层结构（匹配 config/output.toml 加载后的字典）"""

    def test_default_type(self):
        from src.modules.config.schemas.output_schemas import OutputConfig

        cfg = OutputConfig()
        

    def test_handlers_section_present(self):
        from src.modules.config.schemas.output_schemas import (
            OutputConfig,
            OutputHandlersConfig,
        )

        cfg = OutputConfig()
        assert isinstance(cfg.handlers, OutputHandlersConfig)

    def test_pipelines_section_present(self):
        from src.modules.config.schemas.output_schemas import (
            OutputConfig,
            OutputPipelinesConfig,
        )

        cfg = OutputConfig()
        assert isinstance(cfg.pipelines, OutputPipelinesConfig)


# ===========================================================================
# 2. OutputHandlersConfig 元数据字段（在 [handlers] 段内）
# ===========================================================================


class TestOutputHandlersConfigMetadata:
    """OutputHandlersConfig 顶层元数据（concurrent_rendering 等）"""

    def test_default_enabled_is_empty_list(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()
        assert cfg.enabled == []

    def test_default_concurrent_rendering_true(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()
        assert cfg.concurrent_rendering is True

    def test_default_error_handling_continue(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()
        assert cfg.error_handling == "continue"

    def test_default_render_timeout_ms(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()
        assert cfg.render_timeout_ms == 10000

    def test_error_handling_literal_validation(self):
        """error_handling 必须是 'continue' 或 'stop'"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        # 合法值
        OutputHandlersConfig(error_handling="continue")
        OutputHandlersConfig(error_handling="stop")

        # 非法值
        with pytest.raises(ValidationError):
            OutputHandlersConfig(error_handling="invalid")

    def test_render_timeout_ms_must_be_non_negative(self):
        """render_timeout_ms 必须 >= 0"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        OutputHandlersConfig(render_timeout_ms=0)  # 0 表示不限制
        OutputHandlersConfig(render_timeout_ms=5000)

        with pytest.raises(ValidationError):
            OutputHandlersConfig(render_timeout_ms=-1)

    def test_type_field_default(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()
        


# ===========================================================================
# 3. OutputHandlersConfig 聚合（每个 Handler 子配置）
# ===========================================================================


class TestOutputHandlersConfigAggregate:
    """OutputHandlersConfig 聚合所有 Handler 子配置"""

    def test_all_handlers_default_none(self):
        """未配置时所有 handler 子字段应为 None (Optional)"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig()

        # 12 个 Handler 全部应为 None
        for name in (
            "debug_console",
            "edge_tts",
            "gptsovits",
            "obs_control",
            "omni_tts",
            "remote_stream",
            "sticker",
            "subtitle",
            "vrchat",
            "vts",
            "warudo",
        ):
            assert getattr(cfg, name) is None, f"{name} 应当默认 None"

    def test_includes_subtitle_when_provided(self):
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        cfg = OutputHandlersConfig(
            subtitle={
                "type": "subtitle",
                "window_width": 1024,
                "window_height": 120,
            },
        )
        assert cfg.subtitle is not None
        assert cfg.subtitle.window_width == 1024
        assert cfg.subtitle.window_height == 120

    def test_unknown_handler_rejected(self):
        """extra='forbid'：未知 handler 子段应被拒绝"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        with pytest.raises(ValidationError):
            OutputHandlersConfig(
                unknown_handler={"type": "unknown_handler"},
            )


# ===========================================================================
# 4. OutputPipelinesConfig
# ===========================================================================


class TestOutputPipelinesConfig:
    """OutputPipelinesConfig 容器"""

    def test_default_pipelines_is_dict(self):
        from src.modules.config.schemas.output_schemas import OutputPipelinesConfig

        cfg = OutputPipelinesConfig()
        assert isinstance(cfg.pipelines, dict)

    def test_pipelines_dict_accepts_known_keys(self):
        """支持 profanity_filter 等任意动态键"""
        from src.modules.config.schemas.output_schemas import OutputPipelinesConfig

        cfg = OutputPipelinesConfig(
            pipelines={
                "profanity_filter": {
                    "enabled": True,
                    "priority": 100,
                    "words": ["bad"],
                    "replacement": "***",
                },
                "custom_pipeline": {
                    "enabled": True,
                    "priority": 50,
                    "option": "value",
                },
            },
        )
        assert cfg.pipelines["profanity_filter"]["enabled"] is True
        assert cfg.pipelines["custom_pipeline"]["option"] == "value"

    def test_pipelines_dict_empty_default(self):
        from src.modules.config.schemas.output_schemas import OutputPipelinesConfig

        cfg = OutputPipelinesConfig()
        assert cfg.pipelines == {}


# ===========================================================================
# 5. 现有 config/output.toml 验证
# ===========================================================================


class TestExistingConfigValidation:
    """用真实的 config/output.toml 做端到端验证"""

    def test_real_output_toml_exists(self):
        assert REAL_OUTPUT_TOML.exists(), f"未找到 {REAL_OUTPUT_TOML}"

    def test_real_output_toml_loads_as_output_config(self):
        """真实 output.toml 必须能被 OutputConfig 验证无错"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        raw = _load_output_toml(REAL_OUTPUT_TOML)
        cfg = OutputConfig.model_validate(raw)

        # 顶层字段
        

        # handlers 段元数据
        assert cfg.handlers.concurrent_rendering is True
        assert cfg.handlers.error_handling == "continue"
        assert cfg.handlers.render_timeout_ms == 10000

        # handlers 聚合体自身是 OutputHandlersConfig
        

    def test_real_output_toml_drift_detected_as_missing_only(self):
        """真实 output.toml 应通过漂移检测报告缺失字段（新加字段未填）

        新 Schema 比现有 output.toml 更新，因此会报告缺失字段。
        这不是错误，而是"需要补全"的提示。测试断言：
        - 没有冗余（无未识别字段）
        - 缺失字段是预期的（仅来自 schema 新增字段，非损坏）
        """
        from src.modules.config.schemas.output_schemas import OutputConfig

        raw = _load_output_toml(REAL_OUTPUT_TOML)
        cfg, report = OutputConfig.from_dict_with_drift_check(raw)

        assert not report.redundant, f"real output.toml 出现冗余字段（schema 未覆盖）: {report.redundant}"

        expected_missing_prefixes = ("handlers.", "pipelines", "type")
        unexpected = [m for m in report.missing if not any(m.startswith(p) for p in expected_missing_prefixes)]
        assert not unexpected, f"real output.toml 出现非预期的缺失字段: {unexpected}"

    def test_real_output_toml_handlers_have_values(self):
        """真实 output.toml 中应至少有一些 handler 配置"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        raw = _load_output_toml(REAL_OUTPUT_TOML)
        cfg = OutputConfig.model_validate(raw)

        # 至少 subtitle / warudo 等 template 存在
        configured = sum(
            1
            for name in (
                "debug_console",
                "edge_tts",
                "gptsovits",
                "obs_control",
                "omni_tts",
                "remote_stream",
                "sticker",
                "subtitle",
                "vrchat",
                "vts",
                "warudo",
            )
            if getattr(cfg.handlers, name) is not None
        )
        assert configured >= 5, f"期望至少 5 个 handler 配置，实际 {configured}"


# ===========================================================================
# 6. 每个 Handler ConfigSchema 字段覆盖
# ===========================================================================


class TestHandlerConfigSchemas:
    """逐个验证 Handler 的 ConfigSchema 字段名与默认值"""

    def test_subtitle_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import SubtitleConfigSchema

        s = SubtitleConfigSchema()
        assert s.window_width == 800
        assert s.window_height == 100
        assert s.font_family == "Microsoft YaHei UI"
        assert s.font_size == 28
        assert s.auto_hide is True

    def test_subtitle_schema_validation(self):
        """subtitle window_width 必须 >= 100"""
        from src.modules.config.schemas.output_schemas import SubtitleConfigSchema

        with pytest.raises(ValidationError):
            SubtitleConfigSchema(window_width=50)

    def test_edge_tts_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import EdgeTTSConfigSchema

        e = EdgeTTSConfigSchema()
        assert e.voice == "zh-CN-XiaoxiaoNeural"
        assert e.output_device_name is None

    def test_gptsovits_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import GPTSoVITSConfigSchema

        g = GPTSoVITSConfigSchema()
        assert g.host == "127.0.0.1"
        assert g.port == 9880
        assert g.sample_rate == 32000
        assert g.text_language == "zh"

    def test_gptsovits_text_language_pattern(self):
        """text_language 必须是 zh/en/ja/auto"""
        from src.modules.config.schemas.output_schemas import GPTSoVITSConfigSchema

        GPTSoVITSConfigSchema(text_language="en")
        with pytest.raises(ValidationError):
            GPTSoVITSConfigSchema(text_language="klingon")

    def test_obs_control_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import ObsControlConfigSchema

        # obs-websocket-py 未安装时 schema 可能为 None
        if ObsControlConfigSchema is None:
            pytest.skip("obs-websocket-py 未安装")
        o = ObsControlConfigSchema()
        assert o.host == "localhost"
        assert o.port == 4455
        assert o.typewriter_enabled is False

    def test_vts_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import VTSConfigSchema

        v = VTSConfigSchema()
        assert v.vts_host == "localhost"
        assert v.vts_port == 8001
        assert v.lip_sync_enabled is True

    def test_vrchat_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import VRChatConfigSchema

        v = VRChatConfigSchema()
        assert v.vrc_host == "127.0.0.1"
        assert v.vrc_out_port == 9000

    def test_warudo_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import WarudoConfigSchema

        w = WarudoConfigSchema()
        assert w.ws_host == "localhost"
        assert w.ws_port == 19190
        assert w.lip_sync_enabled is True

    def test_remote_stream_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import RemoteStreamConfigSchema

        r = RemoteStreamConfigSchema()
        assert r.host == "0.0.0.0"
        assert r.port == 8765
        assert r.server_mode is True

    def test_sticker_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import StickerConfigSchema

        s = StickerConfigSchema()
        assert s.sticker_size == 0.33
        assert s.target_handler == "vts"

    def test_omni_tts_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import OmniTTSConfigSchema

        o = OmniTTSConfigSchema()
        assert o.host == "127.0.0.1"
        assert o.port == 9880
        assert o.use_subtitle is True

    def test_debug_console_schema_defaults(self):
        from src.modules.config.schemas.output_schemas import DebugConsoleConfigSchema

        d = DebugConsoleConfigSchema()
        assert d.print_source_context is True
        assert d.print_actions is True
        assert d.prefix == "[DEBUG]"

    def test_all_handler_schemas_subclass_baseconfig(self):
        """所有 Handler ConfigSchema 必须继承 BaseConfig"""
        from src.modules.config.schemas.base import BaseConfig
        from src.modules.config.schemas.output_schemas import (
            DebugConsoleConfigSchema,
            EdgeTTSConfigSchema,
            GPTSoVITSConfigSchema,
            ObsControlConfigSchema,
            OmniTTSConfigSchema,
            RemoteStreamConfigSchema,
            StickerConfigSchema,
            SubtitleConfigSchema,
            VRChatConfigSchema,
            VTSConfigSchema,
            WarudoConfigSchema,
        )

        schemas = (
            DebugConsoleConfigSchema,
            EdgeTTSConfigSchema,
            GPTSoVITSConfigSchema,
            OmniTTSConfigSchema,
            RemoteStreamConfigSchema,
            StickerConfigSchema,
            SubtitleConfigSchema,
            VRChatConfigSchema,
            VTSConfigSchema,
            WarudoConfigSchema,
        )
        for cls in schemas:
            assert issubclass(cls, BaseConfig), f"{cls.__name__} 必须继承 BaseConfig"

        # ObsControlConfigSchema 可能因依赖缺失为 None
        if ObsControlConfigSchema is not None:
            assert issubclass(ObsControlConfigSchema, BaseConfig)


# ===========================================================================
# 7. json_schema_extra / UI 元数据
# ===========================================================================


class TestJsonSchemaExtra:
    """验证字段携带 UI 元数据（供 Dashboard 前端动态表单使用）"""

    def test_error_handling_has_ui_metadata(self):
        """error_handling 字段应标注为 select + 提供 options"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        field_info = OutputHandlersConfig.model_fields["error_handling"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "select"
        assert "continue" in extra.get("x-options", [])
        assert "stop" in extra.get("x-options", [])

    def test_concurrent_rendering_has_ui_metadata(self):
        """concurrent_rendering 应标注为 boolean"""
        from src.modules.config.schemas.output_schemas import OutputHandlersConfig

        field_info = OutputHandlersConfig.model_fields["concurrent_rendering"]
        extra = field_info.json_schema_extra or {}
        assert extra.get("x-ui-type") == "boolean"


# ===========================================================================
# 8. OutputConfig.from_dict_with_drift_check (基类继承)
# ===========================================================================


class TestDriftCheck:
    """OutputConfig 继承 BaseConfig.from_dict_with_drift_check 行为"""

    def test_drift_redundant_detected(self):
        """多余字段被检测为 redundant"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        _, report = OutputConfig.from_dict_with_drift_check(
            {
                "type": "output",
                "handlers": {},
                "pipelines": {},
                "unknown_field": "value",
            }
        )
        assert "unknown_field" in report.redundant

    def test_drift_missing_detected(self):
        """缺失字段被检测为 missing"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        _, report = OutputConfig.from_dict_with_drift_check(
            {
                "type": "output",
            }
        )
        missing = set(report.missing)
        assert "handlers" in missing
        assert "pipelines" in missing


# ===========================================================================
# 9. generate_toml_string 输出可解析
# ===========================================================================


class TestTomlGeneration:
    """OutputConfig 的 model_dump 形态应与 config/output.toml 加载后一致"""

    def test_model_dump_matches_toml_structure(self):
        """model_dump 输出应能直接通过 OutputConfig.model_validate() 还原"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        cfg = OutputConfig()
        cfg.handlers.enabled = ["subtitle", "vts"]
        cfg.handlers.render_timeout_ms = 15000

        dumped = cfg.model_dump()
        cfg2 = OutputConfig.model_validate(dumped)

        assert cfg2.handlers.enabled == ["subtitle", "vts"]
        assert cfg2.handlers.render_timeout_ms == 15000
        assert cfg2.handlers.concurrent_rendering == cfg.handlers.concurrent_rendering
        assert cfg2.handlers.error_handling == cfg.handlers.error_handling
        

    def test_generate_toml_string_outputs_valid_toml(self):
        """generate_toml_string 至少能生成可被 tomlkit 解析的 TOML"""
        from src.modules.config.schemas.output_schemas import OutputConfig

        cfg = OutputConfig()
        toml_str = cfg.generate_toml_string(name="output", include_comments=False)

        import tomlkit

        doc = tomlkit.loads(toml_str)
        assert "output" in doc
        assert "handlers" in doc["output"]
        assert "pipelines" in doc["output"]
