"""组件配置 Schema 注册表：目录填充幂等、完整性断言、采集器/Agent 拆表、与 factory/bootstrap 的同源契约"""

import pytest

from src.modules.agents.companions import COMPANION_COLLECTORS
from src.modules.collectors.factory import SUPPORTED_COLLECTORS
from src.modules.component_catalog import (
    COLLECTOR_BINDINGS,
    TOOL_PROVIDER_BINDINGS,
    TOOL_PROVIDER_IDENTITIES,
    ensure_component_registry,
    fill_component_schemas,
)
from src.modules.config import registry
from src.modules.tools.bootstrap import _DOMAIN_MEMBERS


def test_fill_registers_every_catalog_binding_idempotently() -> None:
    first = fill_component_schemas()
    for binding in COLLECTOR_BINDINGS:
        schema_cls = first[binding.name]
        assert issubclass(schema_cls, registry.BaseConfig), f"目录条目 {binding.name} 未注册或缺 ConfigSchema"
    assert set(fill_component_schemas()) == set(first)
    assert not [n for n in ensure_component_registry() if n not in first]


def test_empty_registry_is_asserted_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    # 组件目录的填充入口没有执行（组合根 import 链断裂 / 调用次序错误）→ 启动即硬错
    for table in ("COLLECTOR_SCHEMAS", "AGENT_SCHEMAS", "TOOL_PROVIDER_SCHEMAS"):
        monkeypatch.setattr(registry, table, {})
        with pytest.raises(RuntimeError, match="注册表不完整"):
            registry.assert_components_registered()
    ensure_component_registry()  # 正常填充后断言通过
    registry.assert_components_registered()


def test_collector_and_agent_tables_are_split_disjoint_and_match_factory() -> None:
    fill_component_schemas()
    collectors, agents = set(registry.COLLECTOR_SCHEMAS), set(registry.AGENT_SCHEMAS)
    assert set(SUPPORTED_COLLECTORS) == collectors  # factory 名单与注册表同源（都派生自组件目录）
    assert (
        not collectors & agents and set(registry.COMPONENT_SCHEMAS) == collectors | agents
    )  # collectors.toml 的段全集只认采集器名单
    companions = {name for names in COMPANION_COLLECTORS.values() for name in names}
    assert not companions & collectors  # 随 Agent 开关的附属采集器不进段全集，否则物化会把它们写回去


def test_tool_provider_registry_and_bootstrap_derive_from_catalog() -> None:
    fill_component_schemas()
    # 装配成员表与 Schema 注册表的身份都来自组件目录同一份条目，三表恒同源
    assert tuple((domain, key) for (domain, key), _description, _loader in _DOMAIN_MEMBERS) == TOOL_PROVIDER_IDENTITIES
    assert set(registry.TOOL_PROVIDER_SCHEMAS) == set(TOOL_PROVIDER_IDENTITIES)
    for binding in TOOL_PROVIDER_BINDINGS:
        # 每个目录条目可加载：Provider 携带 ConfigSchema，注册函数可调用
        module = binding.load_provider()
        assert issubclass(module.provider_cls.ConfigSchema, registry.BaseConfig)
        assert callable(module.register_fn)

    registry.TOOL_PROVIDER_SCHEMAS.clear()
    with pytest.raises(RuntimeError, match="注册表不完整"):
        registry.assert_components_registered()
    fill_component_schemas()  # 恢复全局注册表，避免污染后续用例
