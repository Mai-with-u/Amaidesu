"""组件目录——组件身份的单一源表（装配层）

"注册名 → 组件实现"全系统只有这里一张表：
- ``COLLECTOR_BINDINGS``：通用采集器（collectors.toml 里单独开关的那些）
- ``TOOL_PROVIDER_BINDINGS``：核心工具提供者（``[tools.<domain>.<key>]``
  分类段与 avatar 平台成员）

各消费方从本表派生，不再各自手抄名单：
- ``collectors.factory``：SUPPORTED_COLLECTORS 与实例化都取自本表
- ``tools.bootstrap``：核心工具成员表取自本表
- ``config.registry``：Schema 注册表由本模块的 ``fill_component_schemas``
  填充（config 包不反向 import 组件包，依赖方向由本模块单向承接）

新增一个通用采集器 / 工具提供者 = 组件包内写好实现 + 在本表加一条目。
每条目的加载器在调用时才 import 组件实现（组件依赖链重，本模块被
import 时不应付出这些成本）；漏登记的形态只剩"没加进本表"——组件
不进任何名单也就不存在于系统，而加载器 import 失败或实现缺
ConfigSchema 会在填充时以异常暴露。

Agent（``src/agents/``）的配置 Schema 填充也在本模块聚合（Agent 不进
factory/bootstrap 消费面，仅 Schema 注册表需要）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from src.modules.config import registry
from src.modules.config.schemas.base import BaseConfig

if TYPE_CHECKING:
    # 仅为类型注解引用：顶层 import 会拉起 collectors 包（其 __init__ 经
    # manager→factory 回到本模块，构成导入环），运行期不需要该类
    from src.modules.collectors.base import BaseCollector


@dataclass(frozen=True)
class CollectorBinding:
    """一个通用采集器的登记条目。"""

    name: str
    # 延迟加载器：调用时才 import 采集器实现类（依赖链重，按需付出）
    load_collector: Callable[[], type["BaseCollector"]]


class ToolProviderModule(NamedTuple):
    """一个工具提供者模块暴露的装配面：Provider 类（携带 ConfigSchema）与注册入口函数。"""

    provider_cls: type[Any]
    register_fn: Callable[..., Any]


@dataclass(frozen=True)
class ToolProviderBinding:
    """一个核心工具提供者的登记条目。"""

    domain: str
    key: str
    description: str
    # 延迟加载器：调用时才 import 提供者模块的 Provider 类与注册函数
    load_provider: Callable[[], ToolProviderModule]


# --- 采集器加载器（函数内 import：按需拉起各采集器实现及其依赖链） ---


def _load_bili_danmaku() -> type["BaseCollector"]:
    from src.modules.collectors.bilibili.legacy.bili_danmaku_collector import BiliDanmakuCollector

    return BiliDanmakuCollector


def _load_bili_danmaku_official() -> type["BaseCollector"]:
    from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
        BiliDanmakuOfficialCollector,
    )

    return BiliDanmakuOfficialCollector


def _load_bili_danmaku_laplace() -> type["BaseCollector"]:
    from src.modules.collectors.bilibili.laplace import BiliDanmakuLaplaceCollector

    return BiliDanmakuLaplaceCollector


def _load_console_input() -> type["BaseCollector"]:
    from src.modules.collectors.console.console_input_collector import ConsoleInputCollector

    return ConsoleInputCollector


def _load_live_frame() -> type["BaseCollector"]:
    from src.modules.collectors.frame.live_frame_collector import LiveFrameCollector

    return LiveFrameCollector


def _load_stt() -> type["BaseCollector"]:
    from src.modules.collectors.stt.stt_collector import STTCollector

    return STTCollector


# --- 工具提供者加载器（函数内 import：按需拉起 avatar 平台 / studio / web 依赖链） ---


def _load_vts() -> ToolProviderModule:
    from src.modules.avatar.platform.vts.vts_provider import VTSProvider, register_vts_tools

    return ToolProviderModule(VTSProvider, register_vts_tools)


def _load_vrchat() -> ToolProviderModule:
    from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider, register_vrchat_tools

    return ToolProviderModule(VRChatProvider, register_vrchat_tools)


def _load_warudo() -> ToolProviderModule:
    from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider, register_warudo_tools

    return ToolProviderModule(WarudoProvider, register_warudo_tools)


def _load_expression_sheet() -> ToolProviderModule:
    from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import (
        ExpressionSheetProvider,
        register_expression_sheet_tools,
    )

    return ToolProviderModule(ExpressionSheetProvider, register_expression_sheet_tools)


def _load_obs() -> ToolProviderModule:
    from src.modules.studio.obs.obs_provider import OBSProvider, register_obs_tools

    return ToolProviderModule(OBSProvider, register_obs_tools)


def _load_web_search() -> ToolProviderModule:
    from src.modules.web.search_provider import WebSearchProvider, register_web_search_tools

    return ToolProviderModule(WebSearchProvider, register_web_search_tools)


# --- 单一源表（顺序即装配顺序，消费方按序派生） ---

COLLECTOR_BINDINGS: tuple[CollectorBinding, ...] = (
    CollectorBinding("bili_danmaku", _load_bili_danmaku),
    CollectorBinding("bili_danmaku_official", _load_bili_danmaku_official),
    CollectorBinding("bili_danmaku_laplace", _load_bili_danmaku_laplace),
    CollectorBinding("console_input", _load_console_input),
    CollectorBinding("live_frame", _load_live_frame),
    CollectorBinding("stt", _load_stt),
)

TOOL_PROVIDER_BINDINGS: tuple[ToolProviderBinding, ...] = (
    ToolProviderBinding("avatar", "vts", "VTubeStudio 控制", _load_vts),
    ToolProviderBinding("avatar", "vrchat", "VRChat OSC 桥接", _load_vrchat),
    ToolProviderBinding("avatar", "warudo", "Warudo 控制", _load_warudo),
    ToolProviderBinding("avatar", "expression_sheet", "表情差分形象控制", _load_expression_sheet),
    ToolProviderBinding("studio", "obs", "OBS Studio 控制", _load_obs),
    ToolProviderBinding("web", "search", "联网搜索", _load_web_search),
)

# 表的静态身份派生量：只需表本体即可得出，无需加载组件实现
COLLECTOR_NAMES: tuple[str, ...] = tuple(b.name for b in COLLECTOR_BINDINGS)
TOOL_PROVIDER_IDENTITIES: tuple[tuple[str, str], ...] = tuple((b.domain, b.key) for b in TOOL_PROVIDER_BINDINGS)
# tools.toml 动态分类域清单（每个分类域至少有一个在册成员）
TOOL_PROVIDER_DOMAINS: tuple[str, ...] = tuple(dict.fromkeys(domain for domain, _ in TOOL_PROVIDER_IDENTITIES))


def _collect_agent_schemas() -> dict[str, type[BaseConfig]]:
    """从各 Agent 包显式收集 ConfigSchema（包内模块级定义）。"""
    # 函数内 import：Agent 包依赖链较重，仅在填充注册表时拉起
    from src.agents.game_2048.config import Game2048Config
    from src.agents.minecraft.config import MinecraftConfig

    return {"minecraft": MinecraftConfig, "game_2048": Game2048Config}


def fill_component_schemas() -> dict[str, type[BaseConfig]]:
    """按本目录填充 config 侧 Schema 注册表（幂等），返回组件合并视图。

    采集器与工具提供者逐条目加载实现并取其 ConfigSchema；Agent 的
    ConfigSchema 由 ``_collect_agent_schemas`` 显式收集。任一条目的
    import 失败或实现缺 ConfigSchema 在此直接抛出（启动期暴露）。
    """
    collector_schemas = {b.name: b.load_collector().ConfigSchema for b in COLLECTOR_BINDINGS}
    provider_schemas = {(b.domain, b.key): b.load_provider().provider_cls.ConfigSchema for b in TOOL_PROVIDER_BINDINGS}
    agent_schemas = _collect_agent_schemas()

    registry.COLLECTOR_SCHEMAS.clear()
    registry.COLLECTOR_SCHEMAS.update(collector_schemas)
    registry.AGENT_SCHEMAS.clear()
    registry.AGENT_SCHEMAS.update(agent_schemas)
    registry.COMPONENT_SCHEMAS.clear()
    registry.COMPONENT_SCHEMAS.update(collector_schemas)
    registry.COMPONENT_SCHEMAS.update(agent_schemas)
    registry.TOOL_PROVIDER_SCHEMAS.clear()
    registry.TOOL_PROVIDER_SCHEMAS.update(provider_schemas)
    return registry.COMPONENT_SCHEMAS


def ensure_component_registry() -> dict[str, type[BaseConfig]]:
    """装配入口：按目录填充注册表并断言完整性（每次配置加载前调用）。"""
    components = fill_component_schemas()
    registry.assert_components_registered()
    return components


__all__ = [
    "COLLECTOR_BINDINGS",
    "COLLECTOR_NAMES",
    "TOOL_PROVIDER_BINDINGS",
    "TOOL_PROVIDER_IDENTITIES",
    "TOOL_PROVIDER_DOMAINS",
    "ensure_component_registry",
    "fill_component_schemas",
]
