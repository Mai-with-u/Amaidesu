# InputProvider API

## 概述

InputProvider 是输入 Provider 的抽象基类，用于从外部数据源采集数据。

## 核心方法

### `_collect_data(self) -> AsyncIterator[RawData]`
采集数据并生成 RawData 流。

**实现要求**:
- 必须是异步生成器（async generator）
- 定期检查 `_stop_event` 事件以优雅停止
- 每次 yield 一个 RawData 对象

**参数**: 无

**返回**: RawData 对象的异步迭代器

**RawData 结构**:
```python
@dataclass
class RawData:
    content: Dict[str, Any]  # 数据内容
    source: str                # 数据源标识
    data_type: str             # 数据类型（text/image/audio等）
    timestamp: float = time.time()  # 时间戳
    metadata: Dict[str, Any] = {}  # 元数据
```

**示例**:
```python
from typing import AsyncIterator
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData

class MyInputProvider(InputProvider):
    async def _collect_data(self) -> AsyncIterator[RawData]:
        while not self._stop_event.is_set():
            try:
                # 采集数据
                data = await self._fetch_data()
                if data:
                    raw_data = RawData(
                        content={"data": data},
                        source="my_provider",
                        data_type="text",
                    )
                    yield raw_data
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f"采集数据失败: {e}", exc_info=True)
```

### `_cleanup(self)`
清理资源。

**调用时机**: 
- 插件 cleanup 时
- 提供者停止时

**示例**:
```python
async def _cleanup(self):
    # 关闭连接
    if hasattr(self, 'connection'):
        await self.connection.close()
    
    # 清理资源
    self.logger.info("MyInputProvider 清理完成")
```

## 属性

### `config: Dict[str, Any]`
Provider 配置。

### `logger: Logger`
Logger 实例。

### `_stop_event: asyncio.Event`
停止事件，用于优雅停止数据采集。

## 完整示例

### 基础输入 Provider

```python
import asyncio
from typing import AsyncIterator
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger

class MyInputProvider(InputProvider):
    """简单的输入 Provider 示例"""

    async def _collect_data(self) -> AsyncIterator[RawData]:
        counter = 0
        while not self._stop_event.is_set():
            # 生成模拟数据
            data = f"Message {counter}"
            raw_data = RawData(
                content={"data": data},
                source="my_provider",
                data_type="text",
            )
            yield raw_data
            
            counter += 1
            await asyncio.sleep(1)

    async def _cleanup(self):
        self.logger.info("MyInputProvider 清理完成")
```

### 带网络请求的输入 Provider

```python
import aiohttp
import asyncio
from typing import AsyncIterator
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger

class APIInputProvider(InputProvider):
    """从API获取数据的输入 Provider"""

    def __init__(self, config):
        super().__init__(config)
        self.api_url = config.get("api_url")
        self.session = None

    async def _setup_internal(self):
        """初始化 HTTP 会话"""
        self.session = aiohttp.ClientSession()
        await super()._setup_internal()

    async def _collect_data(self) -> AsyncIterator[RawData]:
        while not self._self._stop_event.is_set():
            try:
                async with self.session.get(self.api_url) as response:
                    data = await response.json()
                    for item in data:
                        raw_data = RawData(
                            content={"data": item},
                            source="api_provider",
                            data_type="text",
                        )
                        yield raw_data
            except Exception as e:
                self.logger.error(f"API请求失败: {e}", exc_info=True)
                await asyncio.sleep(5)  # 错误后等待5秒再重试

    async def _cleanup(self):
        if self.session:
            await self.session.close()
        await super()._cleanup()
```

## 注意事项

1. **错误处理**: 所有网络请求和 I/O 操作都需要 try-except
2. **优雅停止**: 必须定期检查 `_stop_event`
3. **资源清理**: 在 `_cleanup()` 中正确关闭连接和会话
4. **日志记录**: 重要操作都需要记录日志

---

**相关文档**:
- [Plugin Protocol](./plugin_protocol.md)
- [OutputProvider API](./output_provider.md)
- [EventBus API](./event_bus.md)
