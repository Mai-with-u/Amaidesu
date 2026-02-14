# STT Input Provider

语音转文字（Speech-to-Text）输入 Provider，使用讯飞流式 ASR 和 Silero VAD 实现实时语音识别。

## 功能特性

- **本地麦克风输入** - 使用 sounddevice 捕获音频
- **Silero VAD** - 本地语音活动检测，判断话语起止
- **讯飞流式 ASR** - 实时将音频发送到讯飞云 API 进行识别
- **远程音频流支持** - 可接收来自 RemoteStream 的音频数据

## 依赖安装

```bash
# 核心依赖
uv add torch sounddevice aiohttp

# 可选：用于音频处理
uv add numpy
```

## 配置说明

### 启用 Provider

在 `[providers.input]` 中添加 `"stt"` 到 `enabled_inputs` 列表：

```toml
[providers.input]
enabled_inputs = [
    "stt",  # 语音转文字
]
```

### 配置参数

```toml
# --- STT InputProvider ---
[providers.input.stt]

# 讯飞 ASR 配置（必填）
[providers.input.stt.iflytek_asr]
appid = "your_appid"         # 讯飞应用 ID
api_key = "your_api_key"     # 讯飞 API Key
api_secret = "your_api_secret"  # 讯飞 API Secret

# 可选配置
host = "iat-api.xfyun.cn"  # API 主机（语音听写流式版）
path = "/v2/iat"            # API 路径（语音听写流式版）
language = "zh_cn"           # 语言（默认）
domain = "iat"              # 领域（默认）
accent = "mandarin"          # 口音（默认）

# VAD 配置
[providers.input.stt.vad]
enable = true               # 是否启用 VAD（默认 true）
vad_threshold = 0.5         # VAD 阈值 0-1，越高越严格（默认 0.5）
silence_seconds = 1.0        # 静音持续时间阈值秒（默认 1.0）

# 音频配置
[providers.input.stt.audio]
sample_rate = 16000          # 采样率 8000 或 16000（默认 16000）
channels = 1                # 声道数（默认 1）
dtype = "int16"             # 数据类型（默认 int16）
# stt_input_device_name = ""  # 可选，指定音频设备名称，留空使用默认
# use_remote_stream = false   # 可选，是否使用远程音频流（默认 false）

# 消息配置
[providers.input.stt.message_config]
user_id = "stt_user"        # 用户 ID
user_nickname = "语音"       # 用户昵称
```

## 获取讯飞 API 凭据

1. 访问[讯飞开放平台](https://www.xfyun.cn/)
2. 注册/登录账号
3. 创建应用，进入控制台
4. 获取 API Key 和 API Secret
5. 在"我的应用"中找到 AppID

## 工作原理

```
麦克风 → sounddevice → 音频块队列 → Silero VAD → 语音检测
                                                      ↓
                                            讯飞 WebSocket API
                                                      ↓
                                           识别结果 → NormalizedMessage
```

1. **音频采集**：sounddevice 从麦克风采集音频数据
2. **VAD 检测**：Silero VAD 判断当前音频是否为语音
3. **流式识别**：检测到语音开始时建立 WebSocket 连接，持续发送音频
4. **结果处理**：收到最终识别结果后构造 `NormalizedMessage` 发送

## 故障排除

### VAD 模型加载失败

确保 torch 安装正确：
```bash
uv add torch
```

### 音频设备未找到

检查系统音频设备，或在配置中指定设备名称：
```toml
[providers.input.stt.audio]
stt_input_device_name = "你的麦克风名称"
```

### 讯飞连接失败

- 检查网络连接
- 确认 API 凭据正确
- 检查讯飞服务状态
