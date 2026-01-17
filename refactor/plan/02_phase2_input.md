# Phase 2: 输入层实现 (Layer 1-2)

## 🎯 目标

实现输入数据流的前两层：
1. **Layer 1: 输入感知层** - 统一所有输入源接口，支持多Provider并发
2. **Layer 2: 输入标准化层** - 统一转换为Text格式

## 📁 目录结构

```
src/
├── perception/                    # Layer 1: 输入感知
│   ├── __init__.py
│   ├── base_input.py
│   ├── text/
│   │   ├── __init__.py
│   │   ├── console_input.py        # 控制台输入
│   │   └── danmaku/
│   │       ├── __init__.py
│   │       ├── base_danmaku.py
│   │       ├── bilibili_danmaku.py
│   │       └── mock_danmaku.py
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── microphone.py
│   │   └── stt/
│   │       ├── __init__.py
│   │       ├── edge_stt.py
│   │       └── funasr_stt.py
│   └── input_factory.py           # 输入Provider工厂
│
├── normalization/                 # Layer 2: 输入标准化
│   ├── __init__.py
│   ├── base_normalizer.py
│   ├── text_normalizer.py
│   ├── audio_to_text.py
│   └── normalizer_factory.py
```

## 📝 实施内容

### 2.1 Layer 1: 输入感知层

#### 目标
统一所有输入源接口，支持多个InputProvider并发运行

#### 实施步骤

**步骤1：创建输入源基类**

`src/perception/base_input.py`:
```python
import time
from typing import Protocol, AsyncIterator
from src.core.provider import RawData

class InputSource(Protocol):
    """输入源协议 - Layer 1

    多个InputSource可以并发运行，采集不同来源的数据
    """
    async def start(self) -> AsyncIterator[RawData]:
        """启动输入流，返回原始数据"""
        ...

    async def stop(self):
        """停止输入源"""
        ...

    async def cleanup(self):
        """清理资源"""
        ...
```

**步骤2：迁移现有输入源**

##### 控制台输入
`src/perception/text/console_input.py`:
```python
import sys
import asyncio
from typing import AsyncIterator
from src.core.provider import RawData
from src.utils.logger import get_logger

class ConsoleInputProvider:
    """控制台输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("ConsoleInputProvider")
        self._stop_event = asyncio.Event()

    async def start(self) -> AsyncIterator[RawData]:
        """启动控制台输入循环"""
        self.logger.info("Console input started")
        loop = asyncio.get_event_loop()

        while not self._stop_event.is_set():
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                text = line.strip()

                if not text:
                    continue
                if text.lower() == "exit()":
                    break

                # 生成RawData
                yield RawData(
                    content=text,
                    source="console",
                    timestamp=time.time()
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Console input error: {e}", exc_info=True)
                await asyncio.sleep(1)

        self.logger.info("Console input stopped")

    async def stop(self):
        """停止输入"""
        self._stop_event.set()

    async def cleanup(self):
        """清理资源"""
        await self.stop()
```

##### 弹幕输入
`src/perception/text/danmaku/base_danmaku.py`:
```python
from typing import Protocol

class DanmakuMessage:
    """弹幕消息数据结构"""
    def __init__(self, username: str, content: str, timestamp: float, **metadata):
        self.username = username
        self.content = content
        self.timestamp = timestamp
        self.metadata = metadata

class DanmakuInputProvider(Protocol):
    """弹幕输入Provider协议"""
    async def start(self):
        """连接弹幕服务器"""
        ...

    async def stop(self):
        """断开连接"""
        ...
```

`src/perception/text/danmaku/bilibili_danmaku.py`:
```python
import time
from typing import AsyncIterator
from src.core.provider import RawData
from src.perception.text.danmaku.base_danmaku import DanmakuMessage
from src.utils.logger import get_logger

class BilibiliDanmakuProvider:
    """B站弹幕输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.room_id = config.get("room_id")
        self.logger = get_logger("BilibiliDanmakuProvider")

    async def start(self) -> AsyncIterator[RawData]:
        """连接B站弹幕服务器"""
        self.logger.info(f"Connecting to Bilibili room {self.room_id}")

        # 模拟弹幕（实际应连接B站API）
        for i in range(5):
            msg = DanmakuMessage(
                username=f"用户{i}",
                content=f"这是第{i}条弹幕",
                timestamp=time.time()
            )

            yield RawData(
                content=msg.content,
                source="bilibili_danmaku",
                timestamp=msg.timestamp,
                username=msg.username
            )

            await asyncio.sleep(1)

    async def stop(self):
        """断开连接"""
        self.logger.info("Disconnected from Bilibili")

    async def cleanup(self):
        """清理资源"""
        await self.stop()
```

##### 麦克风输入
`src/perception/audio/microphone.py`:
```python
import time
from typing import AsyncIterator
from src.core.provider import RawData
from src.utils.logger import get_logger

class MicrophoneInputProvider:
    """麦克风输入Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.device_index = config.get("device_index", 0)
        self.logger = get_logger("MicrophoneInputProvider")

    async def start(self) -> AsyncIterator[RawData]:
        """启动麦克风监听"""
        self.logger.info(f"Microphone started on device {self.device_index}")

        # 模拟音频数据
        for i in range(5):
            yield RawData(
                content={"audio_data": f"audio_bytes_{i}"},
                source="microphone",
                timestamp=time.time()
            )

            await asyncio.sleep(1)

    async def stop(self):
        """停止监听"""
        self.logger.info("Microphone stopped")

    async def cleanup(self):
        """清理资源"""
        await self.stop()
```

**步骤3：创建输入Provider工厂**

`src/perception/input_factory.py`:
```python
from typing import Dict, Any
from src.core.provider import RawData
from src.perception.text.console_input import ConsoleInputProvider
from src.perception.text.danmaku.bilibili_danmaku import BilibiliDanmakuProvider
from src.perception.audio.microphone import MicrophoneInputProvider
from src.utils.logger import get_logger

class InputProviderFactory:
    """输入Provider工厂 - 动态创建InputProvider"""

    def __init__(self):
        self.logger = get_logger("InputProviderFactory")
        self._providers: Dict[str, Any] = {
            "console": ConsoleInputProvider,
            "bilibili_danmaku": BilibiliDanmakuProvider,
            "microphone": MicrophoneInputProvider,
        }

    def create(self, provider_type: str, config: dict):
        """创建InputProvider实例"""
        provider_class = self._providers.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown input provider type: {provider_type}")

        self.logger.info(f"Creating input provider: {provider_type}")
        return provider_class(config)

    def register(self, provider_type: str, provider_class: type):
        """注册新的InputProvider"""
        self._providers[provider_type] = provider_class
        self.logger.info(f"Registered input provider: {provider_type}")
```

### 2.2 Layer 2: 输入标准化层

#### 目标
将所有RawData统一转换为Text格式

#### 实施步骤

**步骤1：创建标准化器基类**

`src/normalization/base_normalizer.py`:
```python
from typing import Protocol
from src.core.provider import RawData

class Normalizer(Protocol):
    """标准化器协议 - Layer 2

    将RawData转换为Text
    """
    async def normalize(self, raw_data: RawData) -> str:
        """
        标准化原始数据

        Args:
            raw_data: 原始数据

        Returns:
            str: 标准化后的文本
        """
        ...
```

**步骤2：实现文本标准化器**

`src/normalization/text_normalizer.py`:
```python
from src.core.provider import RawData
from src.normalization.base_normalizer import Normalizer
from src.utils.logger import get_logger

class TextNormalizer:
    """文本标准化器"""

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("TextNormalizer")

    async def normalize(self, raw_data: RawData) -> str:
        """标准化文本数据"""
        content = raw_data.content

        # 如果content已经是str，直接返回
        if isinstance(content, str):
            return content

        # 如果是dict，提取text字段
        if isinstance(content, dict):
            return content.get("text", str(content))

        # 其他类型转为字符串
        return str(content)
```

**步骤3：实现音频转文本（STT）**

`src/normalization/audio_to_text.py`:
```python
from src.core.provider import RawData
from src.normalization.base_normalizer import Normalizer
from src.utils.logger import get_logger

class AudioToTextNormalizer:
    """音频转文本标准化器（STT）"""

    def __init__(self, config: dict):
        self.config = config
        self.stt_provider = config.get("stt_provider", "edge")
        self.logger = get_logger("AudioToTextNormalizer")

    async def normalize(self, raw_data: RawData) -> str:
        """将音频数据转换为文本"""
        content = raw_data.content

        # 如果不是音频数据，返回空字符串
        if not isinstance(content, dict) or "audio_data" not in content:
            return ""

        # 调用STT服务
        audio_data = content["audio_data"]
        text = await self._transcribe(audio_data)

        return text

    async def _transcribe(self, audio_data: str) -> str:
        """调用STT服务进行语音识别"""
        # 模拟STT（实际应调用真实的STT API）
        self.logger.debug(f"Transcribing audio: {audio_data}")
        return "这是语音识别的文本"
```

**步骤4：创建标准化器工厂**

`src/normalization/normalizer_factory.py`:
```python
from typing import Dict, Any
from src.core.provider import RawData
from src.normalization.text_normalizer import TextNormalizer
from src.normalization.audio_to_text import AudioToTextNormalizer
from src.utils.logger import get_logger

class NormalizerFactory:
    """标准化器工厂 - 动态创建Normalizer"""

    def __init__(self):
        self.logger = get_logger("NormalizerFactory")
        self._normalizers: Dict[str, Any] = {
            "text": TextNormalizer,
            "audio": AudioToTextNormalizer,
        }

    def create(self, normalizer_type: str, config: dict):
        """创建Normalizer实例"""
        normalizer_class = self._normalizers.get(normalizer_type)
        if not normalizer_class:
            raise ValueError(f"Unknown normalizer type: {normalizer_type}")

        self.logger.info(f"Creating normalizer: {normalizer_type}")
        return normalizer_class(config)
```

## ✅ 验证标准

1. ✅ 所有输入源都实现InputSource协议
2. ✅ 支持多个InputProvider并发运行
3. ✅ 所有Normalizer都实现Normalizer协议
4. ✅ RawData → Text转换正常工作
5. ✅ 工厂模式可以动态创建Provider和Normalizer
6. ✅ 所有代码通过类型检查

## 📝 提交

```bash
# 迁移输入源
git mv src/plugins/console_input src/perception/text/console_input.py
git mv src/plugins/bili_danmaku src/perception/text/danmaku/bilibili_danmaku.py

# 添加新文件
git add src/perception/ src/normalization/

git commit -m "feat(phase2): implement Layer 1-2 input perception and normalization"
```
