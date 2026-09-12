"""组件配置 Schema 注册表（确定性装配）

中央配置树从本注册表动态装配各组件的配置段；每个组件的
ConfigSchema 在自己包内定义，是唯一权威。

注册方式为组合根对各组件包的**显式 import**——不做装饰器
注册副作用，import 链断裂或组件缺 Schema 会在启动期被断言
拦下（缺失清单随异常给出），杜绝静默漏注册。
"""

from src.modules.config.schemas.base import BaseConfig

# 组件名 → 该组件包内权威 ConfigSchema
COMPONENT_SCHEMAS: dict[str, type[BaseConfig]] = {}

# 必须在册的组件清单（新增组件时在此登记——漏登记会在启动断言暴露）
EXPECTED_COMPONENTS: tuple[str, ...] = (
    # 采集器（配置宿主 collectors.toml）
    "bili_danmaku",
    "bili_danmaku_official",
    "console_input",
    "screen",
    "stt",
    # Agent（配置宿主 agents.toml）
    "minecraft",
)


def _fill_collectors() -> dict[str, type[BaseConfig]]:
    """从各采集器包显式收集 ConfigSchema（包内类内嵌定义）"""
    from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import BiliDanmakuCollector
    from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
        BiliDanmakuOfficialCollector,
    )
    from src.modules.collectors.console.console_input_collector import ConsoleInputCollector
    from src.modules.collectors.screen.screen_change_collector import ScreenChangeCollector
    from src.modules.collectors.stt.stt_collector import STTCollector

    return {
        "bili_danmaku": BiliDanmakuCollector.ConfigSchema,
        "bili_danmaku_official": BiliDanmakuOfficialCollector.ConfigSchema,
        "console_input": ConsoleInputCollector.ConfigSchema,
        "screen": ScreenChangeCollector.ConfigSchema,
        "stt": STTCollector.ConfigSchema,
    }


def _fill_agents() -> dict[str, type[BaseConfig]]:
    """从各 Agent 包显式收集 ConfigSchema（包内模块级定义）"""
    from src.agents.minecraft.config import MinecraftConfig

    return {"minecraft": MinecraftConfig}


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
    return COMPONENT_SCHEMAS


def assert_components_registered() -> None:
    """启动断言：期望清单内的组件必须全部在册。

    Raises:
        RuntimeError: 存在缺失时抛出，信息含缺失组件清单——
            通常意味着显式 import 链断裂或组件包未定义 ConfigSchema。
    """
    missing = [name for name in EXPECTED_COMPONENTS if name not in COMPONENT_SCHEMAS]
    if missing:
        raise RuntimeError(
            f"组件配置 Schema 注册缺失: {sorted(missing)}（组合根显式 import 链断裂，或组件包内未定义 ConfigSchema）"
        )


def ensure_component_registry() -> dict[str, type[BaseConfig]]:
    """装配入口：填充注册表并断言完整性（每次加载前调用）"""
    registry = fill_component_schemas()
    assert_components_registered()
    return registry


__all__ = [
    "COMPONENT_SCHEMAS",
    "EXPECTED_COMPONENTS",
    "assert_components_registered",
    "ensure_component_registry",
    "fill_component_schemas",
]
