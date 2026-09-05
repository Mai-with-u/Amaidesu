"""Core 和 Model Schema 默认值测试"""

from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig
from src.modules.config.multi_file_loader import CONFIG_VERSION


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
        assert c.meta.version == CONFIG_VERSION

    def test_persona_behavior_style_default_matches_config_version(self):
        """v2.0.6: CONFIG_VERSION 与 behavior_style 字段必须同时存在（防漂移）。"""
        from src.modules.config.upgrade_hooks import _parse_version

        assert _parse_version(CONFIG_VERSION) >= _parse_version("2.0.6"), (
            "CONFIG_VERSION 必须不低于本次升级（2.0.6）"
        )
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
    def test_defaults(self):
        """新结构：llm_providers[] + llm (LLMRoleConfig 引用 provider)。

        - `client` / `api_key` / `base_url` / `max_retries` / `retry_delay` 现位于 provider 层
        - role 仅保留 `provider` / `model` / `temperature` / `max_tokens` + 可选覆盖
        """
        m = ModelConfig()
        # Provider 默认值
        assert len(m.llm_providers) >= 1
        provider = m.llm_providers[0]
        assert provider.name == "default"
        assert provider.client_type == "openai"
        assert provider.api_key == ""
        assert provider.base_url == "https://api.openai.com/v1"
        assert provider.max_retries == 3
        assert provider.retry_delay == 1.0
        # Role 默认值
        assert m.llm.provider == "default"
        assert m.llm.model == "gpt-4o-mini"
        # role.api_key 默认 None (空时使用 provider.api_key)
        assert m.llm.api_key is None
        # role.temperature / max_tokens 默认 None (空时使用 provider 默认)
        assert m.llm.temperature is None
        assert m.llm.max_tokens is None

    def test_fast_llm_defaults(self):
        """llm_fast 与 llm 共享同一个 LLMRoleConfig (默认 model 一致)。"""
        m = ModelConfig()
        # 新结构：所有 role 默认相同 model,toml 可独立覆盖
        assert m.llm_fast.model == "gpt-4o-mini"
        assert m.llm_fast.provider == "default"

    def test_vlm_defaults(self):
        """vlm role 同样是 LLMRoleConfig 实例,默认 model 与 llm 一致。"""
        m = ModelConfig()
        assert m.vlm.model == "gpt-4o-mini"
        assert m.vlm.provider == "default"
        # temperature 默认 None (由 provider 兜底)
        assert m.vlm.temperature is None

    def test_local_llm_defaults(self):
        """llm_local 默认 model 与其他 role 一致;base_url/api_key 覆盖默认 None (运行时由 provider 兜底)。"""
        m = ModelConfig()
        assert m.llm_local.model == "gpt-4o-mini"
        # role 级 base_url/api_key 默认 None,使用 provider 的对应字段
        assert m.llm_local.base_url is None
        assert m.llm_local.api_key is None

    def test_no_hardcoded_real_keys(self):
        """provider.api_key 必须默认空字符串,role.api_key 默认 None。"""
        m = ModelConfig()
        provider = m.llm_providers[0]
        assert provider.api_key == "", "provider.api_key should be empty string by default"
        # role 级 api_key 默认 None(由 provider 兜底),不应硬编码
        for field_name in ("llm", "llm_fast", "vlm"):
            role = getattr(m, field_name)
            assert role.api_key is None, f"{field_name}.api_key should default to None"
