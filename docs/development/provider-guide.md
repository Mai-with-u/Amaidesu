# Provider 开发指南

Provider 是项目的核心组件，负责具体的数据处理功能。本指南介绍如何开发 InputProvider、DecisionProvider 和 OutputProvider。

## Provider 概述

### Provider 类型

| 类型 | 位置 | 职责 | 示例 |
|------|------|------|------|
| **InputProvider** | Input Domain | 从外部数据源采集数据 | ConsoleInputProvider, BiliDanmakuInputProvider |
| **DecisionProvider** | Decision Domain | 夳策能力接口 | MaiCoreDecisionProvider, LocalLLMDecisionProvider |
| **OutputProvider** | Output Domain | 渲染到目标设备 | TTSOutputProvider, SubtitleOutputProvider, VTSOutputProvider |

### Provider 系统特点

- **配置驱动**：通过配置文件启用和配置
- **自动注册**：通过 ProviderRegistry 自动注册
- **异步优先**：所有 I/O 操作使用 async/await
- **错误隔离**：一个 Provider 失败不影响其他

## InputProvider 开发

### 基本结构

InputProvider 从外部数据源采集数据，生成 `RawData` 流。

```python
from typing import AsyncIterator, Dict, Any
from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData
from src.utils.logger import get_logger

class MyInputProvider(InputProvider):
    """自定义输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        # 初始化逻辑
        self.api_url = config.get("api_url")
        self.poll_interval = config.get("poll_interval", 5)

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while self.is_running:
            try:
                # 采集数据逻辑
                data = await self._fetch_data()
                if data:
                    yield RawData(
                        content={"data": data},
                        source="my_provider",
                        data_type="text",
                    )
                await self._sleep_if_running(self.poll_interval)
            except Exception as e:
                self.logger.error(f"采集数据失败: {e}", exc_info=True)

    async def _fetch_data(self):
        """实现具体的数据采集逻辑"""
        # ... 实现细节
        pass
```

### InputProvider 接口

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `_collect_data()` | 采集数据，返回 AsyncIterator[RawData] | ✅ |
| `start()` | 启动数据采集 | ❌（默认实现） |
| `stop()` | 停止数据采集 | ❌（默认实现） |
| `cleanup()` | 清理资源 | ❌（可选） |

### RawData 结构

```python
from src.core.base.raw_data import RawData

raw_data = RawData(
    content={"text": "用户消息", "user": "nickname"},  # 原始数据
    source="bili_danmaku",                           # 数据源标识
    data_type="text",                                # 数据类型
    timestamp=time.time(),                           # 时间戳
    metadata={"room_id": 123456}                     # 元数据
)
```

## DecisionProvider 开发

### 基本结构

DecisionProvider 处理 `NormalizedMessage` 生成 `Intent`。

```python
from typing import Dict, Any
from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent
from src.utils.logger import get_logger

class MyDecisionProvider(DecisionProvider):
    """自定义决策 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        # 初始化逻辑

    async def decide(self, message: NormalizedMessage) -> Intent:
        """决策逻辑"""
        # 实现决策逻辑
        return Intent(
            type="response",
            content="响应内容",
            emotion="happy",
            parameters={},
        )
```

### DecisionProvider 接口

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `decide()` | 决策方法，NormalizedMessage → Intent | ✅ |
| `initialize()` | 初始化 Provider | ❌（可选） |
| `cleanup()` | 清理资源 | ❌（可选） |

### Intent 结构

```python
from src.domains.decision.intent import Intent

intent = Intent(
    type="response",                 # Intent 类型
    content="回复内容",               # 响应内容
    emotion="happy",                 # 情感
    emotion_value=0.8,               # 情感强度
    parameters={                     # 额外参数
        "tts_enabled": True,
        "subtitle_enabled": True,
    }
)
```

## OutputProvider 开发

### 基本结构

OutputProvider 渲染到目标设备。

```python
from typing import Dict, Any
from src.core.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters
from src.utils.logger import get_logger

class MyOutputProvider(OutputProvider):
    """自定义输出 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

    async def render(self, parameters: RenderParameters):
        """渲染逻辑"""
        try:
            # 实现渲染逻辑
            self.logger.info(f"渲染: {parameters.text}")
            # ... 实际渲染操作
        except Exception as e:
            self.logger.error(f"渲染失败: {e}", exc_info=True)
```

### OutputProvider 接口

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `render()` | 渲染参数到目标设备 | ✅ |
| `initialize()` | 初始化 Provider | ❌（可选） |
| `cleanup()` | 清理资源 | ❌（可选） |

### RenderParameters 结构

```python
from src.domains.output.parameters.render_parameters import RenderParameters

params = RenderParameters(
    text="显示文本",
    tts_text="TTS文本",
    emotion_type="happy",
    emotion_value=0.8,
    vts_hotkey="smile",
    metadata={}
)
```

## Provider 注册

### 注册到 ProviderRegistry

在 Provider 的 `__init__.py` 中注册：

```python
# src/domains/input/providers/my_provider/__init__.py
from src.core.provider_registry import ProviderRegistry
from .my_input_provider import MyInputProvider

ProviderRegistry.register_input(
    "my_provider",           # Provider 名称
    MyInputProvider,         # Provider 类
    source="builtin:my_provider"
)
```

### 注册类型

```python
# InputProvider
ProviderRegistry.register_input("name", InputProviderClass)

# DecisionProvider
ProviderRegistry.register_decision("name", DecisionProviderClass)

# OutputProvider
ProviderRegistry.register_output("name", OutputProviderClass)
```

## 配置启用

### InputProvider 配置

```toml
[providers.input]
enabled_inputs = ["console_input", "my_provider"]

[providers.input.inputs.my_provider]
type = "my_provider"
# Provider 特定配置
api_url = "https://api.example.com"
poll_interval = 5
```

### DecisionProvider 配置

```toml
[providers.decision]
active_provider = "my_provider"
available_providers = ["maicore", "my_provider"]

[providers.decision.providers.my_provider]
type = "my_provider"
# Provider 特定配置
model = "gpt-4"
api_key = "sk-..."
```

### OutputProvider 配置

```toml
[providers.output]
enabled_outputs = ["subtitle", "my_provider"]

[providers.output.outputs.my_provider]
type = "my_provider"
# Provider 特定配置
output_device = "default"
volume = 0.8
```

## 完整示例

### Bilibili 弹幕输入 Provider

```python
from typing import AsyncIterator, Dict, Any
from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData
from src.utils.logger import get_logger

class BiliDanmakuInputProvider(InputProvider):
    """Bilibili 弹幕输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self.room_id = config.get("room_id", 0)
        self.poll_interval = config.get("poll_interval", 3)

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集弹幕"""
        while self.is_running:
            try:
                # 调用 Bilibili API
                danmaku_list = await self._fetch_danmaku()
                for danmaku in danmaku_list:
                    yield RawData(
                        content={
                            "text": danmaku.text,
                            "user": danmaku.nickname,
                        },
                        source="bili_danmaku",
                        data_type="text",
                        metadata={"room_id": self.room_id}
                    )
                await self._sleep_if_running(self.poll_interval)
            except Exception as e:
                self.logger.error(f"采集弹幕失败: {e}", exc_info=True)

    async def _fetch_danmaku(self):
        """获取弹幕列表"""
        # 实现 API 调用逻辑
        pass
```

### TTS 输出 Provider

```python
from typing import Dict, Any
from src.core.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters
from src.utils.logger import get_logger

class TTSOutputProvider(OutputProvider):
    """TTS 输出 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self.voice = config.get("voice", "zh-CN-XiaoxiaoNeural")
        self.volume = config.get("volume", 1.0)

    async def render(self, parameters: RenderParameters):
        """渲染 TTS"""
        try:
            text = parameters.tts_text or parameters.text
            self.logger.info(f"TTS: {text[:50]}...")

            # 调用 TTS API
            audio_data = await self._synthesize_speech(text)

            # 播放音频
            await self._play_audio(audio_data)

        except Exception as e:
            self.logger.error(f"TTS 渲染失败: {e}", exc_info=True)

    async def _synthesize_speech(self, text: str):
        """合成语音"""
        # 实现 TTS API 调用
        pass

    async def _play_audio(self, audio_data):
        """播放音频"""
        # 实现音频播放
        pass
```

## 最佳实践

### 1. 错误处理

```python
async def _collect_data(self) -> AsyncIterator[RawData]:
    while self.is_running:
        try:
            data = await self._fetch_data()
            yield RawData(...)
        except Exception as e:
            self.logger.error(f"采集失败: {e}", exc_info=True)
            # 错误后等待一段时间再重试
            await self._sleep_if_running(5)
```

### 2. 日志记录

```python
class MyProvider(InputProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

    async def initialize(self):
        self.logger.info("Provider 初始化")
```

### 3. 资源清理

```python
class MyProvider(InputProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.session = None

    async def initialize(self):
        # 初始化资源
        self.session = aiohttp.ClientSession()

    async def cleanup(self):
        # 清理资源
        if self.session:
            await self.session.close()
```

### 4. 配置验证

```python
from pydantic import BaseModel, Field, validator

class MyProviderConfig(BaseModel):
    """Provider 配置"""
    api_url: str = Field(..., description="API URL")
    poll_interval: int = Field(default=5, ge=1, le=60)

    @validator("api_url")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("API URL 必须以 http:// 或 https:// 开头")
        return v
```

## 测试

### 单元测试

```python
import pytest
from src.domains.input.providers.my_provider import MyInputProvider

@pytest.mark.asyncio
async def test_my_input_provider():
    config = {"api_url": "https://api.example.com"}
    provider = MyInputProvider(config)

    # 测试初始化
    assert provider.api_url == "https://api.example.com"

    # 测试数据采集
    data_count = 0
    async for raw_data in provider._collect_data():
        data_count += 1
        assert raw_data.source == "my_provider"
        if data_count >= 3:
            await provider.stop()
```

## 相关文档

- [InputProvider API](../api/input_provider.md)
- [OutputProvider API](../api/output_provider.md)
- [DecisionProvider API](../api/decision_provider.md)
- [测试规范](testing-guide.md)

---

*最后更新：2026-02-09*
