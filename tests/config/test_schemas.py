"""Core 和 Model Schema 默认值测试"""

from src.modules.config.core_schemas import CoreConfig
from src.modules.config.model_schemas import ModelConfig


class TestCoreConfig:
    def test_defaults(self):
        c = CoreConfig()
        assert c.general.platform_id == "amaidesu"
        assert c.persona.bot_name == "麦麦"
        assert c.persona.emotion_intensity == 7
        assert c.maicore.port == 8000
        assert c.dashboard.port == 60214
        assert c.event_bus.enable_validation is False
        assert c.mcp.enabled is False
        assert c.meta.version == "0.4.0"

    def test_dashboard_overlay_defaults(self):
        c = CoreConfig()
        assert c.dashboard.overlay.show_danmaku is True
        assert c.dashboard.overlay.max_messages == 30

    def test_logging_present(self):
        c = CoreConfig()
        assert c.logging.level == "INFO"

    def test_pipelines_is_dict(self):
        c = CoreConfig()
        assert isinstance(c.pipelines, dict)


class TestModelConfig:
    def test_defaults(self):
        m = ModelConfig()
        assert m.llm.client == "openai"
        assert m.llm.model == "gpt-4"
        assert m.llm.temperature == 0.2
        assert m.llm.api_key == ""
        assert m.llm.max_tokens == 1024

    def test_fast_llm_defaults(self):
        m = ModelConfig()
        assert m.llm_fast.model == "gpt-3.5-turbo"

    def test_vlm_defaults(self):
        m = ModelConfig()
        assert m.vlm.model == "gpt-4-vision-preview"
        assert m.vlm.temperature == 0.3

    def test_local_llm_defaults(self):
        m = ModelConfig()
        assert m.llm_local.model == "llama3"
        assert m.llm_local.base_url == "http://localhost:11434/v1"
        assert m.llm_local.api_key == "sk-dummy"

    def test_no_hardcoded_real_keys(self):
        m = ModelConfig()
        for field_name in ("llm", "llm_fast", "vlm"):
            config = getattr(m, field_name)
            assert config.api_key == "", f"{field_name}.api_key should be empty string"
