"""组件配置 Schema 注册表（纯容器）

本模块只持有"注册名 → ConfigSchema"的容器与完整性断言，不 import
任何组件包——组件实现的显式 import 与表填充在装配层单一源
``src.modules.component_catalog`` 完成（依赖方向：目录 → 本模块，
config 侧不反向依赖组件实现）。

组件表按配置宿主拆分为采集器与 Agent 两份（collectors.toml 的段全集、
物化补缺、WebUI 候选注入等"枚举全集"诉求只认 ``COLLECTOR_SCHEMAS``——
collectors.toml 是唯一靠这份名单干活的文件，混入 Agent 名会让物化把
Agent 段写进 collectors.toml；agents.toml 的段由 typed 字段直连承载，
``AGENT_SCHEMAS`` 成员仅承担启动断言与 schema 服务）。``COMPONENT_SCHEMAS``
是两者的合并视图，供既有消费方继续整体查询。

工具提供者表对应二层身份键段（``[tools.<domain>.<key>].config``），
加载期按表分发校验与默认值补全（见 ``multi_file_loader``），成员身份
即装配目录 ``TOOL_PROVIDER_BINDINGS`` 的 (分类段, 提供者键)。

静态命名段不走注册表——直接在聚合 Schema 里 typed 引用包内
ConfigSchema（先例：``VisionProviderConfig.config``）。
"""

from src.modules.config.schemas.base import BaseConfig

# 采集器注册名 → 包内权威 ConfigSchema（collectors.toml 的合法段全集）
COLLECTOR_SCHEMAS: dict[str, type[BaseConfig]] = {}

# Agent 注册名 → 包内权威 ConfigSchema（启动断言与 schema 服务用）
AGENT_SCHEMAS: dict[str, type[BaseConfig]] = {}

# 采集器 + Agent 的合并视图（既有消费方整体查询用；勿用于 collectors.toml 段全集判定）
COMPONENT_SCHEMAS: dict[str, type[BaseConfig]] = {}

# 工具提供者身份 (分类段, 提供者键) → 包内权威 ConfigSchema
TOOL_PROVIDER_SCHEMAS: dict[tuple[str, str], type[BaseConfig]] = {}


def assert_components_registered() -> None:
    """启动断言：各注册表必须已被组件目录填充且分表边界完好。

    空表意味着组合根没有走到装配目录的填充入口（import 链断裂或
    调用次序错误）；采集器与 Agent 名单交集非空意味着名单混入了
    错误的配置宿主（collectors.toml 的段全集判定会被污染）。

    Raises:
        RuntimeError: 上述任一不变式被破坏时抛出
    """
    empty = [
        label
        for label, table in (
            ("采集器", COLLECTOR_SCHEMAS),
            ("Agent", AGENT_SCHEMAS),
            ("工具提供者", TOOL_PROVIDER_SCHEMAS),
        )
        if not table
    ]
    overlap = sorted(set(COLLECTOR_SCHEMAS) & set(AGENT_SCHEMAS))
    if empty or overlap:
        raise RuntimeError(
            f"组件配置 Schema 注册表不完整: 空表={empty or '无'}，"
            f"采集器与 Agent 名单交集={overlap or '无'}"
            f"（组件目录 component_catalog 的填充入口未执行，或装配条目指向了错误宿主）"
        )


__all__ = [
    "COLLECTOR_SCHEMAS",
    "AGENT_SCHEMAS",
    "COMPONENT_SCHEMAS",
    "TOOL_PROVIDER_SCHEMAS",
    "assert_components_registered",
]
