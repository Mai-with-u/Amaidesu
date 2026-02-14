# TTS 服务模块

负责 TTS 客户端管理和音频设备管理。

## 概述

`src/modules/tts/` 模块提供 TTS 相关功能：
- TTS 管理器
- GPT-SoVITS 客户端
- 音频设备管理

## 主要组件

| 文件 | 功能 |
|------|------|
| `manager.py` | TTSManager - TTS 管理器 |
| `gptsovits_client.py` | GPTSoVITSClient - GPT-SoVITS 客户端 |
| `audio_device_manager.py` | AudioDeviceManager - 音频设备管理 |

## 核心 API

### TTSManager

```python
from src.modules.tts import TTSManager

# 创建 TTS 管理器
tts_manager = TTSManager(config)

# 获取可用的 TTS 引擎
engines = tts_manager.list_engines()

# 合成语音
audio_data = await tts_manager.synthesize(
    text="你好",
    engine="edge_tts",
    voice="zh-CN-XiaoxiaoNeural"
)
```

### GPTSoVITSClient

```python
from src.modules.tts import GPTSoVITSClient

# 创建客户端
client = GPTSoVITSClient(
    api_url="http://localhost:5000",
    ref_audio_path="./ref.wav",
    prompt_text="参考文本"
)

# 合成语音
audio_data = await client.synthesize("要转换的文本")
```

### AudioDeviceManager

```python
from src.modules.tts import AudioDeviceManager

# 创建设备管理器
device_manager = AudioDeviceManager()

# 列出输入设备
input_devices = device_manager.list_input_devices()

# 列出输出设备
output_devices = device_manager.list_output_devices()

# 获取默认设备
default_output = device_manager.get_default_output_device()
```

## TTS 引擎

项目支持多种 TTS 引擎：

| 引擎 | 配置键 | 说明 |
|------|--------|------|
| Edge TTS | `edge_tts` | Microsoft Edge TTS，免费无需 API Key |
| GPT-SoVITS | `gptsovits` | 本地 TTS，效果好 |
| Azure TTS | `azure_tts` | Azure 语音服务 |

### Edge TTS 配置

```toml
[providers.output.providers.tts]
provider = "edge_tts"

[providers.output.providers.edge_tts]
voice = "zh-CN-XiaoxiaoNeural"
rate = "+0%"
pitch = "+0Hz"
volume = "+0%"
```

### GPT-SoVITS 配置

```toml
[providers.output.providers.tts]
provider = "gptsovits"

[providers.output.providers.gptsovits]
api_url = "http://localhost:5000"
ref_audio = "./ref.wav"
ref_text = "参考文本"
```

## 音频设备

### 列出设备

```python
from src.modules.tts import AudioDeviceManager

manager = AudioDeviceManager()

# 获取所有设备信息
devices = manager.get_all_devices()
for device in devices:
    print(f"{device.name} ({device.host_api})")

# 获取默认输出设备
default = manager.get_default_output_device()
print(f"默认输出: {default.name}")
```

### 播放音频

```python
import sounddevice as sd

# 播放音频数据
sd.play(audio_data, samplerate=24000)

# 等待播放完成
sd.wait()
```

## 使用示例

### 在 TTS Provider 中使用

```python
class MyTTSProvider(TTSOutputProvider):
    def __init__(self, config, dependencies):
        self.tts_manager = dependencies.get("tts_manager")

    async def _synthesize(self, text: str) -> bytes:
        # 使用 TTS 管理器合成
        audio = await self.tts_manager.synthesize(
            text=text,
            engine="edge_tts",
            voice="zh-CN-XiaoxiaoNeural"
        )
        return audio
```

### 管理音频设备

```python
from src.modules.tts import AudioDeviceManager

manager = AudioDeviceManager()

# 检查设备可用性
if not manager.has_input_device():
    print("警告：没有可用的输入设备")

if not manager.has_output_device():
    print("警告：没有可用的输出设备")

# 选择特定设备
output_devices = manager.list_output_devices()
if output_devices:
    device = output_devices[0]
    print(f"使用设备: {device.name}")
```

---

*最后更新：2026-02-14*
