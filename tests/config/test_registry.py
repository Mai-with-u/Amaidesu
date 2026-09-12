"""组件配置 Schema 注册表测试（确定性装配）"""

import pytest

from src.modules.config import registry


class TestRegistryFill:
    def test_fill_populates_all_expected(self):
        schemas = registry.fill_component_schemas()
        for name in registry.EXPECTED_COMPONENTS:
            assert name in schemas, f"期望组件 {name} 未注册"
            assert issubclass(schemas[name], registry.BaseConfig)

    def test_fill_is_idempotent(self):
        first = registry.fill_component_schemas()
        second = registry.fill_component_schemas()
        assert set(first.keys()) == set(second.keys())

    def test_ensure_returns_complete_registry(self):
        schemas = registry.ensure_component_registry()
        missing = [n for n in registry.EXPECTED_COMPONENTS if n not in schemas]
        assert not missing


class TestRegistryAssert:
    def test_empty_registry_raises_with_missing_list(self, monkeypatch):
        """负例：注册表被清空 → 断言抛错且信息含缺失组件清单。"""
        monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", {})

        with pytest.raises(RuntimeError) as exc_info:
            registry.assert_components_registered()

        message = str(exc_info.value)
        for name in ("bili_danmaku", "console_input", "minecraft"):
            assert name in message, f"缺失清单应含 {name}: {message}"

    def test_partial_registry_raises_with_missing(self, monkeypatch):
        schemas = dict(registry.fill_component_schemas())
        del schemas["stt"]
        monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", schemas)

        with pytest.raises(RuntimeError) as exc_info:
            registry.assert_components_registered()

        assert "stt" in str(exc_info.value)

    def test_ensure_raises_when_fill_broken(self, monkeypatch):
        """组合根 import 链断裂（填充产物为空）→ 装配入口直接失败。"""
        monkeypatch.setattr(registry, "fill_component_schemas", lambda: {})
        monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", {})

        with pytest.raises(RuntimeError):
            registry.ensure_component_registry()
