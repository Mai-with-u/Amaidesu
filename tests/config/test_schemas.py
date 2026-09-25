"""Schema 默认值测试（六文件新结构）

persona / context / events / dashboard / simulator / logging / interceptors
各段的真实权威分别位于：

- persona       → agents/streamer/config.py StreamerPersonaConfig
- context       → agents.toml [agents.streamer.context]（StreamerContextConfig）
- events        → infra.toml [events]（EventHistoryConfig，保留在 core_schemas 引用）
- dashboard     → infra.toml [dashboard]（DashboardConfig，保留在 core_schemas 引用）
- simulator     → infra.toml [simulator]（SimulatorConfigSchema）
- logging       → infra.toml [logging]（LoggingConfig）
- interceptors  → infra.toml [interceptors]（动态段，typed 化待 T15 后收口）

本文件保留 TestModelConfig 与新 TestStreamerPersonaConfig 等断言。
"""

from src.modules.config.model_schemas import LLMProfilesConfig, ModelConfig
from src.agents.streamer.config import StreamerPersonaConfig
from src.agents.streamer.config import StreamerContextConfig


class TestStreamerPersonaConfig:
    """Persona 段权威（agents/streamer/config.py StreamerPersonaConfig）

    原 CoreConfig.persona 段已迁出——本测试直接断言新位置默认值与字段存在性。
    """

    def test_defaults(self):
        p = StreamerPersonaConfig()
        # StreamerPersonaConfig 权威默认值
        assert p.bot_name == "麦麦"
        assert p.personality == "活泼开朗，有些调皮，喜欢和观众互动"
        assert p.style_constraints == "口语化，使用网络流行语，避免机械式回复，适当使用emoji"
        assert p.behavior_style.startswith("积极与观众互动"), "behavior_style 默认文本与权威定义漂移"
        assert p.audience_salutation == "大家"

    def test_behavior_style_non_empty(self):
        """behavior_style 字段必须存在且非空（Planner 决策侧注入契约）。"""
        p = StreamerPersonaConfig()
        assert isinstance(p.behavior_style, str)
        assert p.behavior_style, "behavior_style 默认值不应为空"


class TestStreamerContextConfig:
    """Context 段权威（agents/streamer/config.py StreamerContextConfig）

    原 CoreConfig.context 段已迁出至 agents.toml [agents.streamer.context]。
    """

    def test_defaults(self):
        c = StreamerContextConfig()
        assert c.enabled is True
        # 记忆召回条数字段已废弃（画像注入上限归 [memory] 段）
        assert not hasattr(c, "memory_recall_long_term")


class TestModelConfig:
    """三层结构（providers / models / profiles）的新断言

    LLM 配置已迁移三层结构（model.toml [llm_profiles]），单字段形态不再存在——
    profile 现在是 ``llm_profiles.<name>`` 字典条目（planner / replyer / summary /
    minecraft / vision / simulator 6 成员）。完整基线测试由 T20 收口，本类
    仅保留面向三层结构的核心不变量。
    """

    def test_three_layer_defaults(self):
        """三层结构默认值：1 默认 provider + 1 默认模型 + 6 用途 profile 种子

        全新安装即可运行：每个 profile 缺省引用模型注册表的 default 条目；
        各 profile 带用途化温度/超时档位（禁 None 政策，字段全部具体值）。
        """
        m = ModelConfig()
        assert len(m.llm_providers) >= 1
        assert len(m.llm_models) == 1
        assert m.llm_models[0].name == "default"
        assert set(m.llm_profiles.model_dump().keys()) == set(LLMProfilesConfig.model_fields)
        for profile in m.llm_profiles.model_dump().values():
            assert profile["model_list"] == ["default"]
        # 用途档位抽样：planner 决策稳（0.7），simulator 表达活（0.9）
        assert m.llm_profiles.planner.temperature == 0.7
        assert m.llm_profiles.simulator.temperature == 0.9

    def test_no_hardcoded_real_keys(self):
        """provider.api_key 默认空字符串（无硬编码真实密钥）"""
        m = ModelConfig()
        provider = m.llm_providers[0]
        assert provider.api_key == "", "provider.api_key should be empty string by default"

    def test_required_profile_names_exposed(self) -> None:
        """独立建筑设计用途由配置 Schema 声明，其他用途保持原顺序。"""
        from src.modules.config.model_schemas import LLMProfilesConfig

        assert tuple(LLMProfilesConfig.model_fields) == (
            "planner",
            "replyer",
            "summary",
            "minecraft",
            "minecraft_builder",
            "vision",
            "simulator",
        )
