# 音频流传输模块

负责高效传输音频数据流（TTS 到 Avatar/RemoteStream）。

## 概述

`src/modules/streaming/` 模块提供音频流传输功能：
- 高效的音频数据传输
- 背压策略支持
- 订阅者管理

## 主要组件

| 文件 | 功能 |
|------|------|
| `audio_stream_channel.py` | AudioStreamChannel - 音频流通道核心 |
| `audio_chunk.py` | AudioChunk, AudioMetadata - 音频数据 |
| `backpressure.py` | 背压策略 |
| `audio_utils.py` | 音频工具函数 |

## 核心 API

### AudioStreamChannel

```python
from src.modules.streaming import AudioStreamChannel, AudioChunk, AudioMetadata

# 创建音频流通道
audio_channel = AudioStreamChannel("tts_output")

# 发布音频数据
metadata = AudioMetadata(
    text="你好",
    sample_rate=24000,
    channels=1,
    format="pcm"
)
await audio_channel.notify_start(metadata)

chunk = AudioChunk(
    data=audio_bytes,
    sample_rate=24000,
    channels=1,
    timestamp_ms=0
)
await audio_channel.publish(chunk)

await audio_channel.notify_end(metadata)
```

### 订阅音频

```python
from src.modules.streaming import SubscriberConfig, BackpressureStrategy

# 订阅音频流
await audio_channel.subscribe(
    name="my_avatar",
    on_audio_start=self._on_audio_start,
    on_audio_chunk=self._on_audio_chunk,
    on_audio_end=self._on_audio_end,
    config=SubscriberConfig(
        queue_size=100,
        backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
    ),
)

async def _on_audio_start(self, metadata: AudioMetadata):
    print(f"音频开始: {metadata.text}")

async def _on_audio_chunk(self, chunk: AudioChunk):
    # 处理音频数据
    await self.player.play(chunk.data)

async def _on_audio_end(self, metadata: AudioMetadata):
    print(f"音频结束")
```

## 数据类型

### AudioChunk

```python
from src.modules.streaming import AudioChunk

chunk = AudioChunk(
    data=b"...",              # 音频数据 bytes
    sample_rate=24000,        # 采样率
    channels=1,              # 声道数
    timestamp_ms=0,          # 时间戳(毫秒)
    is_final=False,          # 是否最后一块
)
```

### AudioMetadata

```python
from src.modules.streaming import AudioMetadata

metadata = AudioMetadata(
    text="要转换的文本",       # 原始文本
    sample_rate=24000,        # 采样率
    channels=1,              # 声道数
    format="pcm",            # 音频格式
    duration_ms=1000,        # 预计时长(毫秒)
)
```

## 背压策略

当消费者处理速度跟不上生产速度时，使用背压策略：

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| `BLOCK` | 队列满时阻塞等待 | 需要完整音频的场景 |
| `DROP_NEWEST` | 丢弃最新数据（默认） | 实时语音对话 |
| `DROP_OLDEST` | 替换最旧数据 | 最新数据更重要的场景 |
| `FAIL_FAST` | 队列满时抛出异常 | 错误处理优先的场景 |

### 配置背压策略

```python
from src.modules.streaming import SubscriberConfig, BackpressureStrategy

config = SubscriberConfig(
    queue_size=100,
    backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
)
```

## 订阅者配置

```python
from src.modules.streaming import SubscriberConfig

config = SubscriberConfig(
    queue_size=50,              # 队列大小
    backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
    enable_overflow_callback=True,  # 启用溢出回调
)
```

## 发布结果

```python
from src.modules.streaming import PublishResult

result = await audio_channel.publish(chunk)

if result.success:
    print(f"发布成功: {result.bytes_written} bytes")
else:
    print(f"发布失败: {result.error}")
```

## 使用示例

### TTS Provider 发送音频

```python
class MyTTSProvider(TTSOutputProvider):
    def _setup_internal(self):
        self.audio_channel = self._dependencies.get("audio_stream_channel")

    async def _synthesize(self, text: str) -> bytes:
        # 生成音频
        audio_data = await self._generate_audio(text)

        # 通知开始
        await self.audio_channel.notify_start(AudioMetadata(
            text=text,
            sample_rate=24000,
            channels=1,
        ))

        # 分块发送
        chunk_size = 4096
        for i in range(0, len(audio_data), chunk_size):
            chunk = AudioChunk(
                data=audio_data[i:i+chunk_size],
                sample_rate=24000,
                channels=1,
                timestamp_ms=i,
            )
            await self.audio_channel.publish(chunk)

        # 通知结束
        await self.audio_channel.notify_end(AudioMetadata(text=text))
```

### Avatar Provider 接收音频

```python
class MyAvatarProvider(AvatarOutputProvider):
    async def setup(self, event_bus, audio_stream_channel):
        await audio_stream_channel.subscribe(
            name="my_avatar",
            on_audio_start=self._on_audio_start,
            on_audio_chunk=self._on_audio_chunk,
            on_audio_end=self._on_audio_end,
        )

    async def _on_audio_start(self, metadata: AudioMetadata):
        self.buffer = []
        self.sample_rate = metadata.sample_rate

    async def _on_audio_chunk(self, chunk: AudioChunk):
        # 重采样到目标采样率
        resampled = resample_audio(
            chunk.data,
            chunk.sample_rate,
            self.target_sample_rate
        )
        self.buffer.extend(resampled)

    async def _on_audio_end(self, metadata: AudioMetadata):
        # 播放音频
        await self.player.play(bytes(self.buffer))
        self.buffer = []
```

---

*最后更新：2026-02-14*
