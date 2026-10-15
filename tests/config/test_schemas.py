"""Schema 默认值与约束：主播人设/上下文、三层模型配置、TTS 与字幕 typed 子段、tools 根结构与提供者开关"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.streamer.config import StreamerContextConfig, StreamerPersonaConfig
from src.modules.config.infra_schemas import InfraRootConfig
from src.modules.config.model_schemas import LLMProfilesConfig, ModelConfig
from src.modules.config.tools_schemas import (
    McpProviderConfig,
    StudioProviderConfig,
    ToolProviderConfig,
    ToolsConfig,
    ToolsRootConfig,
    ToolsTasksConfig,
    VisionProviderConfig,
    WebProviderConfig,
)


def _infra(**sections: dict):
    return InfraRootConfig.from_dict_with_drift_check({"meta": {"version": "0.0.0"}, **sections})
class TestStreamerPersonaConfig:
    """Persona 段权威（agents/streamer/config.py StreamerPersonaConfig）

    原 CoreConfig.persona 段已迁出——本测试直接断言新位置默认值与字段存在性。
    """

    def test_defaults(self):
        p = StreamerPersonaConfig()
        # StreamerPersonaConfig 权威默认值
        assert p.bot_name == "麦麦"
        assert p.personality == "活泼开朗，有些调皮，喜欢和观众互动"
        # 表达侧统一禁 emoji：默认值不含"适当使用emoji"（与模板指令矛盾，见 v2.0.45 钩子）
        assert p.style_constraints == "口语化，使用网络流行语，避免机械式回复"
        assert p.behavior_style.startswith("积极与观众互动"), "behavior_style 默认文本与权威定义漂移"
        assert p.audience_salutation == "大家"

    def test_behavior_style_non_empty(self):
        """behavior_style 字段必须存在且非空（Planner 决策侧注入契约）。"""
        p = StreamerPersonaConfig()
        assert isinstance(p.behavior_style, str)
        assert p.behavior_style, "behavior_style 默认值不应为空"


def test_streamer_persona_and_context_defaults() -> None:
    persona = StreamerPersonaConfig()
    assert persona.bot_name == "麦麦" and persona.audience_salutation == "大家"
    assert persona.personality == "活泼开朗，有些调皮，喜欢和观众互动"
    # v2.0.45 迁移钩子后旧默认（含"适当使用emoji"）已改写，见 replyer_style_drop_emoji
    assert persona.style_constraints == "口语化，使用网络流行语，避免机械式回复"
    assert persona.behavior_style.startswith("积极与观众互动")  # Planner 决策侧注入契约，必须非空

    context = StreamerContextConfig()
    assert context.enabled is True and not hasattr(
        context, "memory_recall_long_term"
    )  # 画像按本批发言人全量注入，无条数上限


def test_model_config_three_layer_defaults() -> None:
    model = ModelConfig()
    assert len(model.llm_providers) >= 1 and [m.name for m in model.llm_models] == ["default"]
    assert model.llm_providers[0].api_key == ""  # 无硬编码真实密钥
    assert set(model.llm_profiles.model_dump()) == set(LLMProfilesConfig.model_fields)
    assert all(
        profile["model_list"] == ["default"] for profile in model.llm_profiles.model_dump().values()
    )  # 全新安装即可运行
    assert (model.llm_profiles.planner.temperature, model.llm_profiles.simulator.temperature) == (
        0.7,
        0.9,
    )  # 决策稳、表达活
    assert tuple(LLMProfilesConfig.model_fields) == (
        "planner",
        "replyer",
        "summary",
        "minecraft",
        "minecraft_builder",
        "vision",
        "simulator",
        "tts",
    )


def test_tts_engine_sections_are_typed_with_dotted_drift_paths() -> None:
    instance, _ = _infra(tts={"enabled": True, "provider": "gptsovits"})
    tts = instance.tts
    assert (tts.gptsovits.host, tts.gptsovits.port) == ("127.0.0.1", 9880)
    assert (tts.omni_tts.llm_profile, tts.omni_tts.voice) == ("tts", "")
    assert (tts.voicebox.profile_id, tts.edge_tts.voice) == ("", "zh-CN-XiaoxiaoNeural")

    _, report = _infra(tts={"provider": "omni_tts", "omni_tts": {"voice": "Chelsie", "junk_key": 1}})
    assert (
        "tts.omni_tts.llm_profile" in report.missing and "tts.omni_tts.voice" not in report.missing
    )  # 缺键带点路径，写回据此补默认
    assert "tts.omni_tts.junk_key" in report.redundant  # 未知键按 infra 域既有语义剥离并计入 redundant

    custom, _ = _infra(tts={"provider": "gptsovits", "gptsovits": {"sample_rate": 48000, "sample_steps": 8}})
    dumped = custom.tts.gptsovits.model_dump()
    assert (dumped["sample_rate"], dumped["sample_steps"], dumped["temperature"]) == (
        48000,
        8,
        1.0,
    )  # 用户值保留，缺失键补默认


def test_subtitle_tk_gui_section_is_typed() -> None:
    instance, _ = _infra(subtitle={"enabled": True, "backends": ["tk_gui"]})
    gui = instance.subtitle.tk_gui
    assert (gui.window_width, gui.font_size, gui.obs_friendly_mode) == (800, 28, True)

    _, report = _infra(subtitle={"tk_gui": {"font_size": 36}})
    assert "subtitle.tk_gui.window_width" in report.missing and "subtitle.tk_gui.font_size" not in report.missing


# tools


def test_tools_tasks_defaults_and_bounds() -> None:
    assert isinstance(ToolsRootConfig().tools, ToolsConfig)
    tasks = ToolsConfig().tasks
    assert isinstance(tasks, ToolsTasksConfig) and (tasks.poll_interval_ms, tasks.wait_timeout_ms) == (2000, 1_800_000)
    assert ToolsConfig.model_fields["tasks"].json_schema_extra.get("x-ui-type") == "object"
    for bad in ({"poll_interval_ms": 50}, {"wait_timeout_ms": 500}):
        with pytest.raises(ValidationError):
            ToolsTasksConfig(**bad)


@pytest.mark.parametrize(
    ("legacy", "keyword"),
    [
        ({"perception": {"enabled": True}}, "perception"),  # 已迁出至 collectors.toml
        ({"output": {"enabled": True}}, "output"),  # 已迁出至 infra.toml
        ({"enabled": ["perception"]}, "enabled"),  # 工具域无包级 enabled 列表，域开关 = 提供者开关单元
        ({"avatar": {"vts": {"enabled": True, "config": {}}}}, "avatar"),  # 已迁出为独立文件 avatar.toml
    ],
)
def test_migrated_out_sections_are_rejected_by_extra_forbid(legacy: dict, keyword: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ToolsConfig.model_validate(legacy)
    assert keyword in str(exc_info.value).lower()


def test_tool_domains_defaults_and_provider_switch_semantics() -> None:
    tools = ToolsConfig()  # 禁 None 契约：动态分类缺省空 dict，单实例分类缺省具体实例
    assert tools.studio == {} and tools.web == {} and isinstance(tools.mcp, McpProviderConfig)
    assert tools.vision.enabled is False  # vision 缺省关态
    assert not hasattr(tools, "memory")  # memory 段已退役：主播记忆工具随主播 Agent 注册，无独立开关

    provider = ToolProviderConfig()
    assert provider.enabled is True and provider.config == {}
    assert ToolProviderConfig(enabled=False, config={"key": "value"}).config == {"key": "value"}

    dynamic = ToolsConfig(
        studio={"obs": {"enabled": True, "config": {}}}, web={"search": {"enabled": True, "config": {}}}
    )
    assert dynamic.studio["obs"].enabled is True and isinstance(dynamic.web["search"], WebProviderConfig)
    assert StudioProviderConfig(enabled=True, extra_field="x").extra_field == "x"  # 域内允许 extra

    assert McpProviderConfig(enabled=True, config={"servers": {}}).config == {"servers": {}}


def test_vision_provider_reuses_the_tool_config_schema_defaults() -> None:
    config = VisionProviderConfig(enabled=True).config
    assert (config.monitor_index, config.default_region) == (1, None)
    assert "vlm_timeout_ms" not in config.model_dump()
    assert config.default_max_width == 1280  # 图进对话后默认降采样（传输成本），以新语义在册
