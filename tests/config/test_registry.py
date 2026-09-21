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


class TestToolProviderRegistry:
    def test_fill_populates_all_expected_providers(self):
        registry.fill_component_schemas()
        for identity in registry.EXPECTED_TOOL_PROVIDERS:
            assert identity in registry.TOOL_PROVIDER_SCHEMAS, f"期望工具提供者 {identity} 未注册"
            assert issubclass(registry.TOOL_PROVIDER_SCHEMAS[identity], registry.BaseConfig)

    def test_provider_registry_assert_lists_missing(self, monkeypatch):
        """负例：工具提供者注册表被清空 → 断言信息含 provider 缺失清单。"""
        registry.fill_component_schemas()
        monkeypatch.setattr(registry, "TOOL_PROVIDER_SCHEMAS", {})

        with pytest.raises(RuntimeError) as exc_info:
            registry.assert_components_registered()

        message = str(exc_info.value)
        assert "avatar.vts" in message
        assert "studio.obs" in message

    def test_provider_domains_derived_from_expected(self):
        """动态分类域清单从期望清单派生：每个域至少一个在册成员、可被加载管线引用。"""
        registry.fill_component_schemas()
        assert registry.TOOL_PROVIDER_DOMAINS
        for domain in registry.TOOL_PROVIDER_DOMAINS:
            assert any(d == domain for d, _ in registry.EXPECTED_TOOL_PROVIDERS), (
                f"动态分类域 {domain} 无在册成员：TOOL_PROVIDER_DOMAINS 与期望清单脱节"
            )


class TestBootstrapRegistryContract:
    def test_every_assembly_member_has_schema(self):
        """契约：bootstrap 装配成员表的每个成员必须在注册表有 ConfigSchema。

        只登记装配、不登记 Schema 的 provider，其 config 段会退化为无校验、
        无默认值补全的自由 dict——正是本机制修复前的缺陷形态。
        """
        from src.modules.tools.bootstrap import _DOMAIN_MEMBERS

        registry.fill_component_schemas()
        for (domain, key), _description, _loader in _DOMAIN_MEMBERS:
            assert (domain, key) in registry.TOOL_PROVIDER_SCHEMAS, (
                f"装配成员 {domain}.{key} 未在 TOOL_PROVIDER_SCHEMAS 注册：其 config 段将不做校验与默认值补全"
            )
