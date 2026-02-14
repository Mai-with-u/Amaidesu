# Provider 开发指南

Provider 是项目的核心组件，负责具体的数据处理功能。本指南介绍如何开发 InputProvider、DecisionProvider 和 OutputProvider。

## Provider 概述

### Provider 类型

| 类型 | 位置 | 职责 | 示例 |
|------|------|------|------|
| **InputProvider** | Input Domain | 从外部数据源采集数据 | ConsoleInputProvider, BiliDanmakuInputProvider, STTInputProvider, BiliDanmakuOfficialInputProvider |
| **DecisionProvider** | Decision Domain | 决策能力接口 | MaiCoreDecisionProvider, LocalLLMDecisionProvider, MaicraftDecisionProvider |
| **OutputProvider** | Output Domain | 渲染到目标设备 | TTSOutputProvider, GPTSoVITSOutputProvider, AvatarOutputProvider, ObsControlOutputProvider, StickerOutputProvider |

### Provider 系统特点

- **配置驱动**：通过配置文件启用和配置
- **自动注册**：通过 ProviderRegistry 自动注册
- **异步优先**：所有 I/O 操作使用 async/await
- **错误隔离**：一个 Provider 失败不影响其他

## InputProvider 开发

### 基本结构

InputProvider 从外部数据源采集数据，**直接构造 `NormalizedMessage`**（重构后）。

```python
from typing import AsyncIterator, Dict, Any
import asyncio
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.logging import get_logger

class MyInputProvider(InputProvider):
    """自定义输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        # 初始化逻辑
        self.api_url = config.get("api_url")
        self.poll_interval = config.get("poll_interval", 5)

    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """启动 Provider，直接返回 NormalizedMessage 流"""
        await self._setup_internal()
        self.is_running = True

        try:
            while self.is_running:
                try:
                    # 采集数据
                    data = await self._fetch_data()
                    if data:
                        # 直接构造 NormalizedMessage
                        yield NormalizedMessage(
                            text=data["text"],
                            source="my_provider",
                            data_type="text",
                            importance=0.5,
                            raw={
                                "user": data.get("user", "未知用户"),
                                "user_id": data.get("user_id", "unknown"),
                            },
                        )

                    # 等待下一次采集
                    await asyncio.sleep(self.poll_interval)
                except Exception as e:
                    self.logger.error(f"采集数据失败: {e}", exc_info=True)
        finally:
            self.is_running = False
            await self._cleanup_internal()

    async def _fetch_data(self):
        """实现具体的数据采集逻辑"""
        # ... 实现细节
        pass
```

### InputProvider 接口

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `start()` | 启动数据采集，返回 AsyncIterator[NormalizedMessage] | ✅ |
| `stop()` | 停止数据采集 | ❌（默认实现） |
| `cleanup()` | 清理资源 | ❌（可选） |
| `_setup_internal()` | 内部初始化逻辑 | ❌ |
| `_cleanup_internal()` | 内部清理逻辑 | ❌ |

### InputProvider 生命周期方法

| 方法 | 说明 | 必须实现 | 调用时机 |
|------|------|----------|----------|
| `start()` | 启动数据采集，返回 AsyncIterator[NormalizedMessage] | ✅ | Manager 启动时 |
| `_setup_internal()` | 内部初始化逻辑 | ❌ | `start()` 开始时 |
| `_cleanup_internal()` | 内部清理逻辑 | ❌ | `stop()`/`cleanup()` 调用时 |
| `stop()` | 停止数据采集 | ❌（默认实现） | Manager 停止时 |
| `cleanup()` | 清理资源 | ❌（默认实现） | Manager 清理时 |

### NormalizedMessage 结构

```python
from src.modules.types.base.normalized_message import NormalizedMessage

# 直接构造 NormalizedMessage
normalized_message = NormalizedMessage(
    text="用户消息",              # 文本描述（用于LLM处理）
    source="my_provider",          # 数据源标识
    data_type="text",              # 数据类型
    importance=0.5,                # 重要性（0-1）
    raw={                          # 原始数据（可选）
        "user": "用户昵称",
        "user_id": "user_id",
    },
)
```

## DecisionProvider 开发

### 基本结构

DecisionProvider 处理 `NormalizedMessage` 生成 `Intent`。

```python
from typing import Dict, Any
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types import EmotionType, ActionType, IntentAction
from src.modules.types import Intent
from src.modules.logging import get_logger

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
            original_text=message.text,
            response_text="响应内容",
            emotion=EmotionType.HAPPY,
            actions=[IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"})],
            metadata={},
        )
```

### DecisionProvider 接口

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `decide()` | 决策方法，NormalizedMessage → Intent | ✅ |
| `setup()` | 设置 Provider（注册事件订阅） | ❌（默认实现） |
| `cleanup()` | 清理资源 | ❌（可选） |
| `_setup_internal()` | 内部初始化逻辑 | ❌（可选） |
| `_cleanup_internal()` | 内部清理逻辑 | ❌（可选） |

### Intent 结构

```python
from src.modules.types import EmotionType, ActionType, IntentAction
from src.modules.types import Intent

intent = Intent(
    original_text="用户消息",           # 原始输入文本
    response_text="回复内容",           # 响应内容
    emotion=EmotionType.HAPPY,         # 情感类型（枚举）
    actions=[                          # 动作列表
        IntentAction(type=ActionType.EXPRESSION, params={"name": "smile"}),
        IntentAction(type=ActionType.HOTKEY, params={"key": "wave"}),
    ],
    metadata={                         # 额外元数据
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
from src.modules.types.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters
from src.modules.logging import get_logger

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
from src.modules.provider_registry import ProviderRegistry
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
from src.modules/types/base/input_provider import InputProvider
import asyncio
from src.modules/types/base.normalized_message import NormalizedMessage
from src.modules.logging import get_logger

class BiliDanmakuInputProvider(InputProvider):
    """Bilibili 弹幕输入 Provider"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)
        self.room_id = config.get("room_id", 0)
        self.poll_interval = config.get("poll_interval", 3)

    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """采集弹幕，直接返回 NormalizedMessage"""
        await self._setup_internal()
        self.is_running = True

        try:
            while self.is_running:
                try:
                    # 调用 Bilibili API
                    danmaku_list = await self._fetch_danmaku()
                    for danmaku in danmaku_list:
                        # 直接构造 NormalizedMessage
                        yield NormalizedMessage(
                            text=danmaku.text,
                            source="bili_danmaku",
                            data_type="text",
                            importance=0.5,
                            raw={
                                "user": danmaku.nickname,
                                "user_id": danmaku.user_id,
                                "room_id": self.room_id,
                            },
                        )
                    await asyncio.sleep(self.poll_interval)
                except Exception as e:
                    self.logger.error(f"采集弹幕失败: {e}", exc_info=True)
        finally:
            self.is_running = False
            await self._cleanup_internal()

    async def _fetch_danmaku(self):
        """获取弹幕列表"""
        # 实现 API 调用逻辑
        pass
```

### TTS 输出 Provider

```python
from typing import Dict, Any
from src.modules.types.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters
from src.modules.logging import get_logger

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
async def start(self) -> AsyncIterator[NormalizedMessage]:
    await self._setup_internal()
    self.is_running = True

    try:
        while self.is_running:
            try:
                data = await self._fetch_data()
                # 构造并 yield NormalizedMessage
                yield NormalizedMessage(...)
            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
                # 错误后等待一段时间再重试
                await asyncio.sleep(5)
    finally:
        self.is_running = False
        await self._cleanup_internal()
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

## Provider 生命周期方法说明

### 内部方法命名约定

所有 Provider 类型的内部扩展方法使用统一的 `_xxx_internal()` 命名约定：

| Provider 类型 | 内部初始化 | 内部清理 |
|--------------|-----------|----------|
| InputProvider | `_setup_internal()` | `_cleanup_internal()` |
| DecisionProvider | `_setup_internal()` | `_cleanup_internal()` |
| OutputProvider | `_setup_internal()` | `_cleanup_internal()` |

子类可以重写这些内部方法来实现自定义的初始化和清理逻辑。

### 公共 API 差异说明

不同类型的 Provider 使用不同的公共生命周期方法，这反映了它们不同的语义：

| Provider 类型 | 启动方法 | 停止方法 | 内部方法 | 原因 |
|--------------|---------|---------|----------|------|
| InputProvider | `start()` | `stop()` + `cleanup()` | `_setup_internal()` / `_cleanup_internal()` | 返回异步数据流 (AsyncIterator) |
| DecisionProvider | `setup()` | `cleanup()` | `_setup_internal()` / `_cleanup_internal()` | 注册到 EventBus 作为事件订阅者 |
| OutputProvider | `setup()` | `cleanup()` | `_setup_internal()` / `_cleanup_internal()` | 注册到 EventBus 作为事件订阅者 |

**为什么不能统一公共 API？**

InputProvider 的 `start()` 方法必须返回 `AsyncIterator[NormalizedMessage]`：
- 这是 Python 异步生成器的语法要求
- `setup()` 方法无法返回 AsyncIterator
- 使用 `start()` 更符合"启动流式数据源"的语义

Decision/OutputProvider 的 `setup()` 方法用于注册事件订阅：
- 它们是事件消费者，而非数据生产者
- 使用 `setup()` 更符合"配置事件处理器"的语义

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
    async for message in provider.stream():
        data_count += 1
        assert message.source == "my_provider"
        assert isinstance(message, NormalizedMessage)
        if data_count >= 3:
            await provider.stop()
```

## 新增 Provider 详细说明

### STTInputProvider - 语音转文字

语音转文字输入 Provider，使用讯飞流式 ASR 和 Silero VAD 实现实时语音识别。

**功能特性：**
- 本地麦克风输入
- 远程音频流支持 (RemoteStream)
- Silero VAD 语音活动检测
- 讯飞流式 ASR
- 自定义 torch 缓存目录（避免 Windows 中文用户名问题）

**配置示例：**
```toml
[providers.input.inputs.stt]
type = "stt"

# 音频配置
audio.sample_rate = 16000
audio.channels = 1
audio.dtype = "int16"
audio.stt_input_device_name = "麦克风名称"  # 可选
audio.use_remote_stream = false  # 使用远程音频流

# VAD 配置
vad.enable = true
vad.vad_threshold = 0.5
vad.silence_seconds = 1.0

# 讯飞 ASR 配置
iflytek_asr.host = "wss://istream-iflytek.xf-yun.com"
iflytek_asr.path = "/v2/iat"
iflytek_asr.appid = "your_appid"
iflytek_asr.api_secret = "your_api_secret"
iflytek_asr.api_key = "your_api_key"
iflytek_asr.language = "zh_cn"
iflytek_asr.domain = "iat"

# 消息配置
message_config.user_id = "stt_user"
message_config.user_nickname = "语音"
```

**依赖安装：**
```bash
uv add torch sounddevice aiohttp
```

### MaicraftDecisionProvider - 弹幕互动游戏决策

基于抽象工厂模式的弹幕互动游戏决策 Provider，支持通过配置切换不同的动作实现系列。

**功能特性：**
- 弹幕命令解析
- 工厂模式创建动作（Log、MCP）
- 支持多种游戏操作（聊天、攻击等）
- 可扩展的动作注册系统

**配置示例：**
```toml
[providers.decision.maicraft]
enabled = true
factory_type = "log"  # 或 "mcp"
command_prefix = "!"

[providers.decision.maicraft.command_mappings]
chat = "chat"
attack = "attack"
```

### AvatarOutputProvider - 虚拟形象输出

虚拟形象输出 Provider，使用 PlatformAdapter 执行渲染，支持多个平台。

**功能特性：**
- 支持多平台适配器（VTS、VRChat、Live2D）
- 抽象参数自动翻译为平台特定参数
- 统一的接口管理不同平台

**配置示例：**
```toml
[providers.output.outputs.avatar]
type = "avatar"
adapter_type = "vts"  # vts | vrchat | live2d

# VTS 特定配置
vts_host = "127.0.0.1"
vts_port = 8000

# VRChat 特定配置
vrc_host = "127.0.0.1"
vrc_in_port = 9001
vrc_out_port = 9000
enable_server = false
```

### GPTSoVITSOutputProvider - GPT-SoVITS 语音合成

使用 GPT-SoVITS 引擎进行高质量文本转语音。

**功能特性：**
- 流式 TTS 和音频播放
- 参考音频管理
- 音频设备管理
- 丰富的 TTS 参数配置

**配置示例：**
```toml
[providers.output.outputs.gptsovits]
type = "gptsovits"
host = "127.0.0.1"
port = 9880

# 参考音频
ref_audio_path = "path/to/reference.wav"
prompt_text = "参考文本"

# TTS 参数
text_language = "zh"
prompt_language = "zh"
top_k = 20
top_p = 0.6
temperature = 0.3
speed_factor = 1.0
streaming_mode = true

# 音频输出
sample_rate = 32000
output_device_name = "扬声器名称"
```

### ObsControlOutputProvider - OBS 控制

通过 WebSocket 连接到 OBS，支持文本显示和场景控制。

**功能特性：**
- 文本显示到 OBS 文本源
- 逐字打印效果
- 场景切换
- 源可见性控制

**配置示例：**
```toml
[providers.output.outputs.obs_control]
type = "obs_control"
host = "localhost"
port = 4455
password = "your_password"  # 可选
text_source_name = "text"

# 逐字效果配置
typewriter_enabled = false
typewriter_speed = 0.1
typewriter_delay = 0.5

# 连接测试
test_on_connect = true
```

### StickerOutputProvider - 贴纸输出

处理表情图片并发送到 VTS 显示。

**功能特性：**
- 图片大小调整
- 冷却时间控制
- 自动卸载
- 与 VTS Provider 集成

**配置示例：**
```toml
[providers.output.outputs.sticker]
type = "sticker"

# 贴纸配置
sticker_size = 0.33
sticker_rotation = 90
sticker_position_x = 0.0
sticker_position_y = 0.0

# 图片处理
image_width = 256
image_height = 256

# 时间控制
cool_down_seconds = 5.0
display_duration_seconds = 3.0
```

### RemoteStreamOutputProvider - 远程流媒体输出

通过 WebSocket 实现与边缘设备的音视频双向传输。

**功能特性：**
- WebSocket 服务器/客户端模式
- 音频数据传输
- 图像数据传输
- 自动重连

**配置示例：**
```toml
[providers.output.outputs.remote_stream]
type = "remote_stream"
server_mode = true
host = "0.0.0.0"
port = 8765

# 音频配置
audio_sample_rate = 16000
audio_channels = 1
audio_format = "int16"
audio_chunk_size = 1024

# 图像配置
image_width = 640
image_height = 480
image_format = "jpeg"
image_quality = 80

# 重连配置
reconnect_delay = 5
max_reconnect_attempts = -1  # -1 表示无限
```

### BiliDanmakuOfficialInputProvider - B站官方弹幕

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据。

**功能特性：**
- 官方 WebSocket API
- 消息缓存服务
- 上下文标签过滤
- 模板信息支持

**配置示例：**
```toml
[providers.input.inputs.bili_danmaku_official]
type = "bili_danmaku_official"
id_code = "直播间ID代码"
app_id = "应用ID"
access_key = "访问密钥"
access_key_secret = "访问密钥Secret"
api_host = "https://live-open.biliapi.com"
message_cache_size = 1000

# 上下文过滤（可选）
context_tags = ["游戏", "互动"]

# 模板信息（可选）
enable_template_info = false
template_items = {}
```

### ReadPingmuInputProvider - 读屏木输入

通过 ReadPingmu 服务采集屏幕内容，支持 OCR 文字识别和图像理解。

**功能特性：**
- 通过 ReadPingmu 服务采集屏幕内容
- 支持 OCR 文字识别
- 支持图像理解

**配置示例：**
```toml
[providers.input.inputs.read_pingmu]
type = "read_pingmu"
api_base_url = "http://127.0.0.1:8080"
```

### BiliDanmakuOfficialMaicraftInputProvider - B站官方弹幕（Maicraft版本）

专为 Maicraft 弹幕互动游戏优化的 B站官方弹幕输入 Provider。

**与 BiliDanmakuOfficialInputProvider 的区别：**
- 针对游戏场景优化的事件处理
- 支持弹幕命令解析
- 与 MaicraftDecisionProvider 配合使用

**功能特性：**
- 官方 WebSocket API
- 消息缓存服务
- 上下文标签过滤
- 模板信息支持
- 弹幕命令解析

**配置示例：**
```toml
[providers.input.inputs.bili_danmaku_official_maicraft]
type = "bili_danmaku_official_maicraft"
id_code = "直播间ID代码"
app_id = "应用ID"
access_key = "访问密钥"
access_key_secret = "访问密钥Secret"
api_host = "https://live-open.biliapi.com"
message_cache_size = 1000

# 上下文过滤（可选）
context_tags = ["游戏", "互动"]

# 模板信息（可选）
enable_template_info = false
template_items = {}
```

### OmniTTSOutputProvider - Fish TTS 语音合成

使用 Fish Audio TTS API 进行高质量语音合成。

**功能特性：**
- 使用 Fish Audio TTS API
- 高质量语音合成
- 支持多音色
- 流式音频输出

**配置示例：**
```toml
[providers.output.outputs.omni_tts]
type = "omni_tts"
api_key = "your_api_key"
api_url = "https://api.fish.audio"
voice = "narration"  # 音色ID
speed = 1.0
```

**依赖安装：**
```bash
uv add aiohttp
```

### WarudoOutputProvider - Warudo 虚拟主播

通过 WebSocket 与 Warudo 虚拟主播软件通信，控制虚拟形象。

**功能特性：**
- 通过 WebSocket 与 Warudo 通信
- 支持参数控制（表情、手势等）
- 支持热键触发
- 支持场景切换

**配置示例：**
```toml
[providers.output.outputs.warudo]
type = "warudo"
host = "127.0.0.1"
port = 10800
```

## 共享服务

> **⚠️ 已废弃（A-02 重构）**
>
> 服务注册机制（ServiceManager）已完全移除。
> 所有共享服务（如 DGLabService）已迁移到 Provider 架构或移至 `plugins_backup/` 作为历史参考。
>
> 如果需要共享功能，请：
> 1. 创建对应的 Provider（InputProvider/DecisionProvider/OutputProvider）
> 2. 在 Provider 内部实现所需功能
> 3. 通过 EventBus 进行跨 Provider 通信
>
> 参见：[Provider 开发](#provider-开发)、[事件系统](../architecture/event-system.md)

## 新提示词

### input/screen_description.md - 屏幕内容描述

屏幕视觉理解助手提示词，分析屏幕截图并生成内容描述。

**变量：**
- `image_base64`: 屏幕截图（base64编码）
- `context`: 上一时刻屏幕的内容
- `images_count`: 图像数量（用于检测是否为多张拼接）

**使用示例：**
```python
from src.prompts import get_prompt_manager

prompt = get_prompt_manager().render(
    "input/screen_description",
    image_base64=image_base64,
    context="上一时刻的屏幕内容",
    images_count=1
)
```

### input/screen_context.md - 屏幕上下文分析

屏幕上下文分析助手提示词，理解当前屏幕状态和用户意图。

**变量：**
- `image`: 屏幕截图
- `context`: 当前上下文信息

**分析任务：**
1. 识别屏幕上的主要内容元素
2. 理解当前的应用程序或界面类型
3. 分析用户可能的操作意图
4. 识别关键的可交互元素

### output/avatar_expression.md - 虚拟形象表情生成

虚拟形象表情生成助手提示词，根据文本内容和表情列表选择最合适的表情。

**变量：**
- `text`: 用户文本
- `emotion_list`: 可选表情列表

**输出格式：**
```
表情名称: [选择的脸部表情名称]
说明: [为什么选择这个表情的理由]
```

## 相关文档

- [InputProvider API](../api/input_provider.md)
- [OutputProvider API](../api/output_provider.md)
- [DecisionProvider API](../api/decision_provider.md)
- [测试规范](testing-guide.md)
- [提示词管理](prompt-management.md)

---

*最后更新：2026-02-13*
