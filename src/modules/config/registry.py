"""组件配置 Schema 注册表（确定性装配）

中央配置树从本注册表动态装配各组件的配置段；每个组件的
ConfigSchema 在自己包内定义，是唯一权威。

注册方式为组合根对各组件包的**显式 import**——不做装饰器
注册副作用，import 链断裂或组件缺 Schema 会在启动期被断言
拦下（缺失清单随异常给出），杜绝静默漏注册。

两张表对应两类动态键段（加载期按表分发校验与默认值补全，见
``multi_file_loader``）：

- ``COMPONENT_SCHEMAS``：单层名键——采集器（``[collectors.<name>]``）
  与 Agent（``[agents.<name>]``）；
- ``TOOL_PROVIDER_SCHEMAS``：二层身份键——工具提供者
  （``[tools.<domain>.<key>].config``），键形与装配侧
  ``tools.bootstrap._DOMAIN_MEMBERS`` 的成员身份同构；
  两表一致性由契约测试守护（装配成员必须有 Schema，否则该
  provider 的 config 段退化为无校验、无默认值补全的自由 dict）。

静态命名段不走注册表——直接在聚合 Schema 里 typed 引用包内
ConfigSchema（先例：``VisionProviderConfig.config``）。
"""

from src.modules.config.schemas.base import BaseConfig

# 组件名 → 该组件包内权威 ConfigSchema
COMPONENT_SCHEMAS: dict[str, type[BaseConfig]] = {}

# 工具提供者身份 (分类段, 提供者键) → 包内权威 ConfigSchema
TOOL_PROVIDER_SCHEMAS: dict[tuple[str, str], type[BaseConfig]] = {}

# 必须在册的组件清单（新增组件时在此登记——漏登记会在启动断言暴露）
EXPECTED_COMPONENTS: tuple[str, ...] = (
    # 采集器（配置宿主 collectors.toml）
    "bili_danmaku",
    "bili_danmaku_official",
    "console_input",
    "maicraft_attention",
    "stt",
    # Agent（配置宿主 agents.toml）
    "minecraft",
)

# 必须在册的工具提供者清单（新增 provider 时在此与 bootstrap 成员表两处登记）
EXPECTED_TOOL_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("avatar", "vts"),
    ("avatar", "vrchat"),
    ("avatar", "warudo"),
    ("studio", "obs"),
    ("web", "search"),
)

# tools.toml 动态分类域清单（从期望清单派生：每个分类域至少有一个在册成员）。
# 这些域在加载期走注册表校验与默认值补全、渲染期走注释化 provider 表、
# WebUI 写入口按注册表下钻校验；静态命名段（vision/memory/mcp 等）不走此路径。
TOOL_PROVIDER_DOMAINS: tuple[str, ...] = tuple(dict.fromkeys(d for d, _ in EXPECTED_TOOL_PROVIDERS))


def _fill_collectors() -> dict[str, type[BaseConfig]]:
    """从各采集器包显式收集 ConfigSchema（包内类内嵌定义）。

    多数采集器代码在 ``src/modules/collectors/`` 下；``maicraft_attention``
    是**游戏相关**的外部世界适配器，按"游戏内容逻辑内聚 ``src/agents/<名>/``"
    放在 Minecraft Agent 包内，只有装配走采集器框架。
    """
    from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
    from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import BiliDanmakuCollector
    from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
        BiliDanmakuOfficialCollector,
    )
    from src.modules.collectors.console.console_input_collector import ConsoleInputCollector
    from src.modules.collectors.stt.stt_collector import STTCollector

    return {
        "bili_danmaku": BiliDanmakuCollector.ConfigSchema,
        "bili_danmaku_official": BiliDanmakuOfficialCollector.ConfigSchema,
        "console_input": ConsoleInputCollector.ConfigSchema,
        "maicraft_attention": MaicraftAttentionCollector.ConfigSchema,
        "stt": STTCollector.ConfigSchema,
    }


def _fill_agents() -> dict[str, type[BaseConfig]]:
    """从各 Agent 包显式收集 ConfigSchema（包内模块级定义）"""
    from src.agents.minecraft.config import MinecraftConfig

    return {"minecraft": MinecraftConfig}


def _fill_tool_providers() -> dict[tuple[str, str], type[BaseConfig]]:
    """从各工具提供者包显式收集 ConfigSchema（provider 类内嵌定义）。

    成员身份 (分类段, 提供者键) 与装配侧 ``tools.bootstrap._DOMAIN_MEMBERS``
    同构；两表一致性由契约测试守护。
    """
    from src.modules.avatar.vrchat.vrchat_provider import VRChatProvider
    from src.modules.avatar.vts.vts_provider import VTSProvider
    from src.modules.avatar.warudo.warudo_provider import WarudoProvider
    from src.modules.studio.obs.obs_provider import OBSProvider
    from src.modules.web.search_provider import WebSearchProvider

    return {
        ("avatar", "vts"): VTSProvider.ConfigSchema,
        ("avatar", "vrchat"): VRChatProvider.ConfigSchema,
        ("avatar", "warudo"): WarudoProvider.ConfigSchema,
        ("studio", "obs"): OBSProvider.ConfigSchema,
        ("web", "search"): WebSearchProvider.ConfigSchema,
    }


def fill_component_schemas() -> dict[str, type[BaseConfig]]:
    """全量重建注册表（幂等）。

    逐包显式 import 各组件的权威 ConfigSchema；组件包本身
    不产生注册副作用，装配确定性由本函数的静态 import 链保证。
    """
    schemas: dict[str, type[BaseConfig]] = {}
    schemas.update(_fill_collectors())
    schemas.update(_fill_agents())
    COMPONENT_SCHEMAS.clear()
    COMPONENT_SCHEMAS.update(schemas)
    TOOL_PROVIDER_SCHEMAS.clear()
    TOOL_PROVIDER_SCHEMAS.update(_fill_tool_providers())
    return COMPONENT_SCHEMAS


def assert_components_registered() -> None:
    """启动断言：期望清单内的组件与工具提供者必须全部在册。

    Raises:
        RuntimeError: 存在缺失时抛出，信息含缺失清单——
            通常意味着显式 import 链断裂或组件包未定义 ConfigSchema
    """
    missing = [name for name in EXPECTED_COMPONENTS if name not in COMPONENT_SCHEMAS]
    provider_missing = [f"{d}.{k}" for d, k in EXPECTED_TOOL_PROVIDERS if (d, k) not in TOOL_PROVIDER_SCHEMAS]
    if missing or provider_missing:
        raise RuntimeError(
            f"组件配置 Schema 注册缺失: {sorted(missing) or '无'}，"
            f"工具提供者 Schema 注册缺失: {provider_missing or '无'}"
            f"（组合根显式 import 链断裂，或 provider 包内未定义 ConfigSchema）"
        )


def ensure_component_registry() -> dict[str, type[BaseConfig]]:
    """装配入口：填充注册表并断言完整性（每次加载前调用）"""
    registry = fill_component_schemas()
    assert_components_registered()
    return registry


__all__ = [
    "COMPONENT_SCHEMAS",
    "TOOL_PROVIDER_SCHEMAS",
    "EXPECTED_COMPONENTS",
    "EXPECTED_TOOL_PROVIDERS",
    "TOOL_PROVIDER_DOMAINS",
    "assert_components_registered",
    "ensure_component_registry",
    "fill_component_schemas",
]
