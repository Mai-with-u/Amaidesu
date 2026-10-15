"""组件配置 Schema 注册表（确定性装配）：填充幂等、缺失断言、采集器/Agent 拆表、与 factory/bootstrap 的契约"""

import pytest

from src.modules.agents.companions import COMPANION_COLLECTORS
from src.modules.collectors.factory import SUPPORTED_COLLECTORS
from src.modules.config import registry
from src.modules.tools.bootstrap import _DOMAIN_MEMBERS


def test_fill_registers_every_expected_component_idempotently() -> None:
    first = registry.fill_component_schemas()
    for name in registry.EXPECTED_COMPONENTS:
        assert name in first and issubclass(first[name], registry.BaseConfig), f"期望组件 {name} 未注册"
    assert set(registry.fill_component_schemas()) == set(first)
    assert not [n for n in registry.EXPECTED_COMPONENTS if n not in registry.ensure_component_registry()]


def test_missing_registrations_are_asserted_with_names(monkeypatch) -> None:
    monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", {})
    with pytest.raises(RuntimeError) as exc_info:
        registry.assert_components_registered()
    assert all(name in str(exc_info.value) for name in ("bili_danmaku", "console_input", "minecraft"))

    partial = dict(registry.fill_component_schemas())
    del partial["stt"]
    monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", partial)
    with pytest.raises(RuntimeError, match="stt"):
        registry.assert_components_registered()

    monkeypatch.setattr(registry, "fill_component_schemas", lambda: {})  # 组合根 import 链断裂：装配入口直接失败
    monkeypatch.setattr(registry, "COMPONENT_SCHEMAS", {})
    with pytest.raises(RuntimeError):
        registry.ensure_component_registry()


def test_collector_and_agent_tables_are_split_disjoint_and_match_factory() -> None:
    registry.fill_component_schemas()
    collectors, agents = set(registry.COLLECTOR_SCHEMAS), set(registry.AGENT_SCHEMAS)
    assert collectors == {"bili_danmaku", "bili_danmaku_official", "console_input", "live_frame", "stt"}
    assert agents == {"minecraft", "game_2048"}
    assert (
        not collectors & agents and set(registry.COMPONENT_SCHEMAS) == collectors | agents
    )  # collectors.toml 的段全集只认采集器名单
    assert set(SUPPORTED_COLLECTORS) == collectors  # factory 与注册表脱节意味着"认得但装配不了"（或反之）
    companions = {name for names in COMPANION_COLLECTORS.values() for name in names}
    assert not companions & collectors  # 随 Agent 开关的附属采集器不进段全集，否则物化会把它们写回去


def test_tool_provider_registry_is_complete_and_consistent_with_bootstrap(monkeypatch) -> None:
    registry.fill_component_schemas()
    for identity in registry.EXPECTED_TOOL_PROVIDERS:
        assert issubclass(registry.TOOL_PROVIDER_SCHEMAS[identity], registry.BaseConfig), (
            f"期望工具提供者 {identity} 未注册"
        )
    for domain in registry.TOOL_PROVIDER_DOMAINS:  # 每个动态分类域至少一个在册成员
        assert any(d == domain for d, _ in registry.EXPECTED_TOOL_PROVIDERS)
    for (domain, key), _description, _loader in _DOMAIN_MEMBERS:
        # 只登记装配、不登记 Schema 的 provider，其 config 段会退化为无校验无默认值补全的自由 dict
        assert (domain, key) in registry.TOOL_PROVIDER_SCHEMAS, (
            f"装配成员 {domain}.{key} 未在 TOOL_PROVIDER_SCHEMAS 注册"
        )

    monkeypatch.setattr(registry, "TOOL_PROVIDER_SCHEMAS", {})
    with pytest.raises(RuntimeError) as exc_info:
        registry.assert_components_registered()
    assert "avatar.vts" in str(exc_info.value) and "studio.obs" in str(exc_info.value)
