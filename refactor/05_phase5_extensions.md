# Phase 5: 扩展系统实现

## 🎯 目标

实现扩展系统（Layer 8），支持：
1. 内置扩展（官方维护）
2. 用户扩展（社区开发）
3. 自动扫描用户扩展目录
4. Extension接口（聚合多个Provider）

## 📁 目录结构

```
src/
└── extensions/                      # 内置扩展
    ├── minecraft/
    │   ├── __init__.py
    │   │   └── MinecraftExtension
    │   └── providers/
    │       ├── event_provider.py
    │       └── command_provider.py
    ├── warudo/
    └── dg_lab/

extensions/                           # 用户扩展（根目录，.gitignore）
    ├── genshin/
    └── mygame/
```

## 📝 实施内容

### 5.1 迁移内置扩展

#### Minecraft扩展

使用`git mv`迁移Minecraft插件到扩展：

```bash
# 使用git mv迁移（必须！）
git mv src/plugins/minecraft src/extensions/minecraft
git commit -m "refactor: migrate minecraft to extension"
```

`src/extensions/minecraft/__init__.py`:
```python
from typing import List, Dict, Any
from src.core.extension import Extension
from src.core.event_bus import EventBus
from src.core.provider import InputProvider, OutputProvider
from src.utils.logger import get_logger

class MinecraftExtension:
    """Minecraft扩展 - 聚合Minecraft的所有能力"""

    def __init__(self):
        self.logger = get_logger("MinecraftExtension")
        self.providers: List = []

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List:
        """
        初始化Minecraft扩展

        Args:
            event_bus: 事件总线
            config: 扩展配置

        Returns:
            List[Provider]: 初始化好的Provider列表
        """
        self.logger.info("Setting up Minecraft extension")

        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)

        providers = []

        # 输入Provider：Minecraft事件
        if config.get("events_enabled", True):
            from .providers.event_provider import MinecraftEventProvider
            event_provider = MinecraftEventProvider({
                "host": self.host,
                "port": self.port
            })
            await event_provider.setup(event_bus, config)
            providers.append(event_provider)
            self.logger.info("Minecraft event provider initialized")

        # 输出Provider：Minecraft命令
        if config.get("commands_enabled", True):
            from .providers.command_provider import MinecraftCommandProvider
            command_provider = MinecraftCommandProvider({
                "host": self.host,
                "port": self.port
            })
            await command_provider.setup(event_bus, config)
            providers.append(command_provider)
            self.logger.info("Minecraft command provider initialized")

        self.providers = providers
        return providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("Cleaning up Minecraft extension")
        for provider in self.providers:
            await provider.cleanup()
        self.providers = []

    def get_info(self) -> Dict[str, Any]:
        """获取扩展信息"""
        return {
            "name": "Minecraft",
            "version": "1.0.0",
            "author": "Official",
            "description": "Minecraft游戏集成扩展",
            "category": "game",
            "api_version": "1.0"
        }
```

`src/extensions/minecraft/providers/event_provider.py`:
```python
from src.core.provider import InputProvider, RawData
from src.core.event_bus import EventBus
from src.utils.logger import get_logger

class MinecraftEventProvider:
    """Minecraft事件输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)
        self.logger = get_logger("MinecraftEventProvider")

    async def setup(self, event_bus: EventBus, config: dict):
        """设置Provider（订阅EventBus）"""
        self.event_bus = event_bus
        # 订阅需要的事件
        self.logger.info("Minecraft event provider setup complete")

    async def start(self):
        """启动Minecraft事件监听"""
        self.logger.info(f"Connecting to Minecraft {self.host}:{self.port}")
        # 实际实现应连接Minecraft服务器

    async def stop(self):
        """停止监听"""
        self.logger.info("Minecraft event provider stopped")

    async def cleanup(self):
        """清理资源"""
        await self.stop()
```

`src/extensions/minecraft/providers/command_provider.py`:
```python
from src.core.provider import OutputProvider
from src.core.event_bus import EventBus
from src.expression.expression_generator import RenderParameters
from src.utils.logger import get_logger

class MinecraftCommandProvider:
    """Minecraft命令输出Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)
        self.logger = get_logger("MinecraftCommandProvider")

    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        self.event_bus = event_bus
        self.event_bus.on("expression.parameters_generated", self._on_parameters)
        self.logger.info("Minecraft command provider subscribed to parameters_generated")

    async def _on_parameters(self, event: dict):
        """处理RenderParameters事件"""
        params = event.get("data", {}).get("parameters")
        if params and hasattr(params, 'minecraft_commands'):
            await self.render(params)

    async def render(self, parameters: RenderParameters):
        """渲染Minecraft命令"""
        commands = getattr(parameters, 'minecraft_commands', [])
        if commands:
            self.logger.info(f"Sending {len(commands)} commands to Minecraft")
            # 实际实现应发送命令到Minecraft服务器

    async def cleanup(self):
        """清理资源"""
        if hasattr(self, 'event_bus'):
            self.event_bus.off("expression.parameters_generated", self._on_parameters)
```

### 5.2 配置更新

```toml
# config.toml

# 内置扩展（官方）
[extensions.minecraft]
enabled = true
host = "localhost"
port = 25565
events_enabled = true
commands_enabled = true

[extensions.warudo]
enabled = true
host = "localhost"
port = 50051

# 用户扩展（社区）- 自动扫描，无需配置
# [extensions.genshin]
# enabled = false  # 可选：显式禁用
```

### 5.3 .gitignore配置

`.gitignore`:
```
# 用户扩展目录（不纳入版本控制）
extensions/

# 但保留.gitkeep文件
!extensions/.gitkeep
```

`extensions/.gitkeep`:
```
# 此文件用于保留extensions/目录在Git仓库中
# 实际的用户扩展不会被提交
```

## ✅ 验证标准

1. ✅ Minecraft扩展可以正常加载
2. ✅ 内置扩展自动加载
3. ✅ 用户扩展自动扫描（extensions/目录）
4. ✅ Extension接口正确实现
5. ✅ Provider正确聚合和注册
6. ✅ Git历史通过`git mv`保留

## 📝 提交

```bash
# 迁移所有内置扩展
git mv src/plugins/mainosaba src/extensions/mainosaba
git mv src/plugins/warudo src/extensions/warudo
git mv src/plugins/dg_lab_service src/extensions/dg_lab

# 添加.gitkeep
git add extensions/.gitkeep

git commit -m "feat(phase5): implement extension system and migrate built-in extensions"
```
