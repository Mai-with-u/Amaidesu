"""Core 和 Model Schema 默认值测试"""

from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.config.file_meta import CONFIG_BASELINE_VERSION, FileMetaConfig


class TestCoreConfig:
    def test_defaults(self):
        c = CoreConfig()
        assert c.general.platform_id == "amaidesu"
        assert c.persona.bot_name == "麦麦"
        assert c.persona.emotion_intensity == 7
        # v2.0.6：PersonaConfig 新增 behavior_style 字段，Planner 决策侧注入。
        # 默认值由 core_schemas 权威定义；测试以存在性 + 非空即可锁定契约，
        # 文本变化由其他用例覆盖。
        assert isinstance(c.persona.behavior_style, str)
        assert c.persona.behavior_style, "behavior_style 默认值不应为空"
        assert c.context.enabled is True
        assert c.dashboard.port == 60214
        # 事件日志仅内存（运行周期观察窗）：默认不落库，重启即清，
        # 保证每轮运行的调试视野互不污染。
        assert c.events.persist is False
        assert FileMetaConfig().version == CONFIG_BASELINE_VERSION

    def test_persona_behavior_style_default_matches_config_version(self):
        """behavior_style 字段必须存在且默认文本与权威定义一致（防漂移）。"""
        c = CoreConfig()
        # 显式断言 behavior_style 默认值已落盘（防止上游"升了版本但没加字段"的回退）。
        assert c.persona.behavior_style.startswith("积极与观众互动"), (
            "behavior_style 默认文本与 core_schemas 权威定义漂移，请回归"
        )

    def test_simulator_present(self):
        c = CoreConfig()
        assert c.simulator.enabled is False

    def test_logging_present(self):
        c = CoreConfig()
        assert c.logging.level == "INFO"

    def test_interceptors_is_dict(self):
        c = CoreConfig()
        assert isinstance(c.interceptors, dict)


class TestModelConfig:
    """三层结构（providers / models / profiles）的新断言

    旧的 ``llm`` / ``llm_fast`` / ``vlm`` / ``llm_local`` 单字段断言已废弃——
    profile 现在是 ``llm_profiles.<name>`` 字典条目（planner / replyer / summary /
    minecraft / vision / simulator 6 成员）。完整基线测试由 T20 收口，本类
    仅保留面向三层结构的核心不变量。
    """

    def test_three_layer_defaults(self):
        """三层结构默认值：1 个默认 provider + 空 models/空 profiles 字典"""
        m = ModelConfig()
        assert len(m.llm_providers) >= 1
        assert m.llm_models == []
        assert m.llm_profiles == {}

    def test_no_hardcoded_real_keys(self):
        """provider.api_key 默认空字符串（无硬编码真实密钥）"""
        m = ModelConfig()
        provider = m.llm_providers[0]
        assert provider.api_key == "", "provider.api_key should be empty string by default"

    def test_required_profile_names_exposed(self):
        """必填 profile 成员清单常量暴露，供加载期校验调用"""
        from src.modules.config.model_schemas import REQUIRED_PROFILE_NAMES

        assert REQUIRED_PROFILE_NAMES == ("planner", "replyer", "summary", "minecraft", "vision", "simulator")
