"""
插件接口（Plugin Protocol）

定义了refactor/design/plugin_system.md中要求的Plugin接口。

这个接口是新的插件系统架构，用于替代BasePlugin系统：
- BasePlugin（旧系统）：继承AmaidesuCore，通过self.core访问核心功能
- Plugin（新系统）：聚合Provider，通过event_bus和config进行依赖注入

Plugin的职责：
1. 聚合多个Provider（InputProvider、OutputProvider、DecisionProvider等）
2. 通过EventBus进行通信
3. 管理Provider的生命周期
"""

from typing import Protocol, List, Dict, Any, TYPE_CHECKING


# --- 核心服务接口（用于避免循环依赖 - A-09） ---
class CoreServices(Protocol):
    """
    核心服务接口（Protocol）

    用于解耦 Plugin 与 AmaidesuCore 的直接依赖关系（A-09 重构）。
    Plugin 只依赖这个接口，不依赖具体的 AmaidesuCore 实现。

    这个接口定义了 Plugin 需要的核心服务，通过依赖注入方式传递。
    """

    @property
    def event_bus(self) -> Any:  # 实际上是 EventBus，但避免循环导入
        """获取事件总线实例"""
        ...


# --- Provider 接口 ---
if TYPE_CHECKING:
    from .event_bus import EventBus

    # Provider类型的统一接口
    class Provider(Protocol):
        """Provider基类协议"""

        async def setup(self, event_bus: EventBus): ...

        async def cleanup(self): ...


class Plugin(Protocol):
    """
    插件协议 - 聚合多个Provider

    这是plugin_system.md中定义的新Plugin接口，用于替代BasePlugin系统。

    与BasePlugin的区别：
    - BasePlugin继承自 AmaidesuCore，通过 self.core 访问核心功能
    - Plugin不继承任何基类，通过 event_bus 和 config 进行依赖注入

    生命周期：
    1. 实例化(__init__) - 接收配置
    2. 设置(setup()) - 初始化Provider并返回Provider列表
    3. 清理(cleanup()) - 清理所有Provider资源

    示例：
    ```python
    class BilibiliDanmakuPlugin:
        def __init__(self, config: dict):
            self.config = config
            self._providers = []

        async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
            # 创建InputProvider
            danmaku_provider = BilibiliDanmakuInputProvider(config)
            await danmaku_provider.setup(event_bus)
            self._providers.append(danmaku_provider)

            # 创建OutputProvider（如果有）
            # command_provider = MinecraftCommandProvider(config)
            # await command_provider.setup(event_bus)
            # self._providers.append(command_provider)

            return self._providers

        async def cleanup(self):
            for provider in self._providers:
                await provider.cleanup()
            self._providers.clear()

        def get_info(self) -> dict:
            return {
                "name": "BilibiliDanmaku",
                "version": "1.0.0",
                "author": "Amaidesu Team",
                "description": "B站弹幕输入插件",
                "category": "input",
            }
    ```

    Provider类型：
    - InputProvider: 输入Provider，从外部数据源采集数据
    - OutputProvider: 输出Provider，渲染到目标设备
    - DecisionProvider: 决策Provider，处理CanonicalMessage
    """

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例（或 CoreServices 接口）
                       A-09 重构：可以直接传递 EventBus 实例，或传递 CoreServices 接口
            config: 插件配置（从config.toml中读取）

        Returns:
            Provider列表: 插件管理的所有Provider实例
                         （InputProvider、OutputProvider、DecisionProvider等）

        Raises:
            ConnectionError: 如果无法连接到外部服务
            ValueError: 如果配置无效
        """
        ...

    async def cleanup(self):
        """
        清理资源

        清理插件管理的所有Provider资源。
        """
        ...

    def get_info(self) -> Dict[str, Any]:
        """
        获取插件信息

        Returns:
            dict: 插件信息，包含以下字段：
                - name: 插件名称
                - version: 版本号
                - author: 作者
                - description: 描述
                - category: 分类（input/output/processing/game/hardware/software）
                - api_version: API版本
        """
        return {
            "name": "PluginName",
            "version": "1.0.0",
            "author": "Author",
            "description": "Plugin description",
            "category": "input",  # input/output/processing/game/hardware/software
            "api_version": "1.0",
        }


# 导出
__all__ = ["Plugin", "CoreServices"]
