# P2中等插件迁移指南

## 迁移概述

本文档提供了将6个中等复杂度插件从BasePlugin迁移到新Plugin架构的详细指南。

## 已完成的工作

### ✅ bili_danmaku (已完成)

创建了以下文件:
- `src/plugins/bili_danmaku/providers/bili_danmaku_provider.py` - InputProvider
- `src/plugins/bili_danmaku/providers/__init__.py` - Provider导出
- `src/plugins/bili_danmaku/plugin.py` (新) - 新Plugin类
- `src/plugins/bili_danmaku/plugin_old_baseplugin.py` - 旧版本备份
- `tests/test_bili_danmaku_plugin.py` - 测试文件

## 待迁移的插件

### 1. bili_danmaku_official (B站官方弹幕插件)

#### 迁移步骤:

**步骤1: 创建InputProvider**

```bash
mkdir -p src/plugins/bili_danmaku_official/providers
```

创建 `src/plugins/bili_danmaku_official/providers/bili_official_provider.py`:

```python
"""
Bilibili 官方弹幕 InputProvider

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据。
"""

import asyncio
from typing import AsyncIterator, Dict, Any

from .client.websocket_client import BiliWebSocketClient
from .service.message_cache import MessageCacheService
from .service.message_handler import BiliMessageHandler

from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger


class BiliDanmakuOfficialInputProvider(InputProvider):
    """
    Bilibili 官方弹幕 InputProvider

    使用官方WebSocket API获取实时弹幕。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.id_code = config.get("id_code")
        self.app_id = config.get("app_id")
        self.access_key = config.get("access_key")
        self.access_key_secret = config.get("access_key_secret")
        self.api_host = config.get("api_host", "https://live-open.biliapi.com")

        # 验证配置
        required_configs = ["id_code", "app_id", "access_key", "access_key_secret"]
        if missing_configs := [key for key in required_configs if not config.get(key)]:
            raise ValueError(f"缺少必需的配置项: {missing_configs}")

        # 状态变量
        self.websocket_client = None
        self.message_handler = None
        self.message_cache_service = None
        self._stop_event = asyncio.Event()

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集弹幕数据"""
        # 初始化消息缓存服务
        cache_size = self.config.get("message_cache_size", 1000)
        self.message_cache_service = MessageCacheService(max_cache_size=cache_size)

        # 初始化WebSocket客户端
        self.websocket_client = BiliWebSocketClient(
            id_code=self.id_code,
            app_id=self.app_id,
            access_key=self.access_key,
            access_key_secret=self.access_key_secret,
            api_host=self.api_host,
        )

        # 初始化消息处理器
        self.message_handler = BiliMessageHandler(
            config=self.config,
            context_tags=self.config.get("context_tags"),
            template_items=self.config.get("template_items"),
            message_cache_service=self.message_cache_service,
        )

        # 运行WebSocket连接
        try:
            await self.websocket_client.run(self._handle_message_from_bili)
        except Exception as e:
            self.logger.error(f"WebSocket运行出错: {e}", exc_info=True)

    async def _handle_message_from_bili(self, message_data: Dict[str, Any]):
        """处理从Bilibili接收到的消息"""
        try:
            message = await self.message_handler.create_message_base(message_data)
            if message:
                # 缓存消息
                self.message_cache_service.cache_message(message)

                # 创建RawData
                raw_data = RawData(
                    content={
                        "message": message,
                        "message_config": self.message_handler.get_message_config(),
                    },
                    source="bili_danmaku_official",
                    data_type="text",
                    timestamp=message.message_info.time,
                )
                yield raw_data

        except Exception as e:
            self.logger.error(f"处理消息时出错: {message_data} - {e}", exc_info=True)

    async def _cleanup(self):
        """清理资源"""
        if self.websocket_client:
            try:
                await self.websocket_client.close()
            except Exception as e:
                self.logger.error(f"关闭WebSocket客户端时出错: {e}")

        if self.message_cache_service:
            self.message_cache_service.clear_cache()

        self.logger.info("BiliDanmakuOfficialInputProvider 已清理")
```

**步骤2: 重写Plugin类**

创建 `src/plugins/bili_danmaku_official/plugin_new.py`:

```python
# src/plugins/bili_danmaku_official/plugin.py

import asyncio
from typing import Dict, Any, List, Optional

from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider
from src.utils.logger import get_logger


class BiliDanmakuOfficialPlugin:
    """
    Bilibili 官方弹幕插件，使用官方开放平台API获取实时弹幕。

    迁移到新的Plugin接口。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._providers: List[InputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("BiliDanmakuOfficialPlugin 在配置中已禁用。")
            return

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        # 创建Provider
        try:
            provider = BiliDanmakuOfficialInputProvider(self.config)
            self._providers.append(provider)
            self.logger.info("BiliDanmakuOfficialInputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建Provider失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 BiliDanmakuOfficialPlugin...")

        for provider in self._providers:
            try:
                await provider.stop()
            except Exception as e:
                self.logger.error(f"清理Provider时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("BiliDanmakuOfficialPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "BiliDanmakuOfficial",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Bilibili 官方弹幕插件，使用WebSocket API获取实时弹幕",
            "category": "input",
            "api_version": "1.0",
        }


plugin_entrypoint = BiliDanmakuOfficialPlugin
```

**步骤3: 移动和重命名文件**

```bash
cd src/plugins/bili_danmaku_official
mv plugin.py plugin_old_baseplugin.py
mv plugin_new.py plugin.py
```

**步骤4: 创建providers/__init__.py**

```python
from .bili_official_provider import BiliDanmakuOfficialInputProvider

__all__ = ["BiliDanmakuOfficialInputProvider"]
```

**步骤5: 创建测试文件**

创建 `tests/test_bili_danmaku_official_plugin.py` (参照test_bili_danmaku_plugin.py)

### 2. 其他插件迁移模板

剩余插件的迁移步骤类似,主要区别在于:

#### bili_danmaku_official_maicraft
- 继承自 bili_danmaku_official
- 添加 ForwardWebSocketClient
- 在InputProvider中处理转发逻辑

#### vtube_studio (OutputProvider)
- 创建 `VTStudioOutputProvider` 继承 `OutputProvider`
- 实现 `_render_internal()` 方法
- 实现热键触发、表情控制等方法

#### tts (OutputProvider)
- 创建 `TTSOutputProvider` 继承 `OutputProvider`
- 实现 `_render_internal()` 方法处理TTS播放
- 支持 Edge TTS 和 Omni TTS

#### gptsovits_tts (OutputProvider)
- 创建 `GPTSoVITSOutputProvider` 继承 `OutputProvider`
- 实现 `_render_internal()` 方法
- 支持流式TTS播放

## 核心迁移模式

### InputProvider模式 (输入型插件)

```python
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData

class MyInputProvider(InputProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        # 初始化配置和状态

    async def _collect_data(self) -> AsyncIterator[RawData]:
        while not self._stop_event.is_set():
            # 采集数据
            data = await self.collect_data()
            yield RawData(
                content=data,
                source="my_provider",
                data_type="text",
                timestamp=time.time(),
            )
            await asyncio.sleep(interval)

    async def _cleanup(self):
        # 清理资源
        pass
```

### OutputProvider模式 (输出型插件)

```python
from src.core.providers.output_provider import OutputProvider
from src.core.providers.base import RenderParameters

class MyOutputProvider(OutputProvider):
    async def _setup_internal(self):
        # 初始化设备
        pass

    async def _render_internal(self, parameters: RenderParameters):
        # 渲染参数到设备
        await self.render(parameters)

    async def _cleanup_internal(self):
        # 清理资源
        pass
```

### Plugin模式 (统一)

```python
from src.core.plugin import Plugin

class MyPlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # 初始化配置

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        self.event_bus = event_bus
        # 创建Provider
        providers = [MyProvider(self.config)]
        return providers

    async def cleanup(self):
        # 清理所有Provider
        pass

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "Description",
            "category": "input",  # 或 output, processing, game, hardware, software
            "api_version": "1.0",
        }

plugin_entrypoint = MyPlugin
```

## 验证步骤

对于每个迁移后的插件:

1. **检查LSP错误**:
   ```bash
   # 迁移完成后检查
   # 查看是否有类型错误
   ```

2. **运行测试**:
   ```bash
   pytest tests/test_xxx_plugin.py -v
   ```

3. **Git操作**:
   ```bash
   # 查看变更
   git status

   # 添加新文件
   git add src/plugins/xxx/plugin.py
   git add src/plugins/xxx/providers/
   git add tests/test_xxx_plugin.py

   # 如果需要保留历史
   git mv plugin.py plugin_old_baseplugin.py

   # 提交
   git commit -m "迁移 xxx 插件到新Plugin架构"
   ```

## 注意事项

1. **Git历史保留**:
   - 旧plugin.py应重命名为plugin_old_baseplugin.py
   - 新plugin.py保留在git历史中
   - 使用git mv移动已有文件

2. **配置文件**:
   - 保留原有的config.toml
   - 新Plugin类使用相同的配置结构
   - 不需要修改配置文件

3. **服务访问**:
   - 移除`self.core.register_service()`
   - 移除`self.core.get_service()`
   - 通过`event_bus.emit()`发送事件
   - 如果需要服务,通过依赖注入

4. **事件发布**:
   - 使用`event_bus.emit(event_name, data, source)`替代`self.core.send_to_maicore()`

5. **测试文件**:
   - 每个插件必须有对应的测试文件
   - 使用`tests/test_plugin_utils.py`中的工具
   - 测试Plugin和Provider的生命周期

## 总结

本文档提供了完整的迁移指南。按照上述步骤,可以系统地将所有6个中等复杂度插件迁移到新的Plugin架构。

关键要点:
1. 创建Provider (InputProvider或OutputProvider)
2. 重写Plugin类 (实现Plugin协议)
3. 创建测试文件
4. 保留git历史
5. 运行验证测试
