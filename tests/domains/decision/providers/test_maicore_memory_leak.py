"""
测试 MaiCoreDecisionProvider 核心功能

运行: uv run pytest tests/domains/decision/providers/test_maicore_memory_leak.py -v
"""

from src.domains.decision.providers.maicore.maicore_decision_provider import MaiCoreDecisionProvider


class TestMaiCoreDecisionProviderCore:
    """测试 MaiCoreDecisionProvider 核心功能"""

    def test_config_schema_default_values(self):
        """测试配置Schema默认值"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        # 验证默认配置
        assert provider.host == "localhost"
        assert provider.port == 8000
        assert provider.platform == "test"
        assert provider.http_callback_path == "/callback"
        assert provider.typed_config.connect_timeout == 10.0
        assert provider.typed_config.reconnect_interval == 5.0

    def test_config_schema_custom_values(self):
        """测试自定义配置值"""
        provider = MaiCoreDecisionProvider(
            {
                "host": "maicore.example.com",
                "port": 9000,
                "platform": "custom_platform",
                "http_host": "http.example.com",
                "http_port": 8080,
                "http_callback_path": "/custom_callback",
                "connect_timeout": 30.0,
                "reconnect_interval": 10.0,
            }
        )

        # 验证自定义配置
        assert provider.host == "maicore.example.com"
        assert provider.port == 9000
        assert provider.platform == "custom_platform"
        assert provider.http_host == "http.example.com"
        assert provider.http_port == 8080
        assert provider.http_callback_path == "/custom_callback"
        assert provider.typed_config.connect_timeout == 30.0
        assert provider.typed_config.reconnect_interval == 10.0

    def test_ws_url_generation(self):
        """测试WebSocket URL生成"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        assert provider.ws_url == "ws://localhost:8000/ws"

    def test_get_info_returns_required_fields(self):
        """测试get_info返回必需字段"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        info = provider.get_info()

        assert "name" in info
        assert "version" in info
        assert "host" in info
        assert "port" in info
        assert "platform" in info
        assert "is_connected" in info
        assert info["name"] == "MaiCoreDecisionProvider"

    def test_get_statistics_returns_required_fields(self):
        """测试get_statistics返回必需字段"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        stats = provider.get_statistics()

        assert "is_connected" in stats
        assert "router_running" in stats

    def test_provider_name(self):
        """测试Provider名称"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        assert provider.provider_name == "maicore"

    def test_action_suggestions_config_defaults(self):
        """测试Action建议配置默认值"""
        provider = MaiCoreDecisionProvider({"host": "localhost", "port": 8000, "platform": "test"})

        assert provider.typed_config.action_suggestions_enabled is False
        assert provider.typed_config.action_confidence_threshold == 0.6
        assert provider.typed_config.action_cooldown_seconds == 5.0
        assert provider.typed_config.max_suggested_actions == 3

    def test_action_suggestions_config_custom(self):
        """测试Action建议自定义配置"""
        provider = MaiCoreDecisionProvider(
            {
                "host": "localhost",
                "port": 8000,
                "platform": "test",
                "action_suggestions_enabled": True,
                "action_confidence_threshold": 0.8,
                "action_cooldown_seconds": 10.0,
                "max_suggested_actions": 5,
            }
        )

        assert provider.typed_config.action_suggestions_enabled is True
        assert provider.typed_config.action_confidence_threshold == 0.8
        assert provider.typed_config.action_cooldown_seconds == 10.0
        assert provider.typed_config.max_suggested_actions == 5
