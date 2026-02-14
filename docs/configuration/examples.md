# 配置示例文档

本文档提供各种 Provider 的详细配置示例，用于参考和定制。

## 配置路径规范（新架构）

### Provider 配置路径

| Provider 类型 | 主配置路径（推荐） | 本地配置文件 | 参数覆盖路径（已废弃） |
|--------------|-----------|-------------|-------------|
| **Input** | `[providers.input.{name}]` | `src/domains/input/providers/{name}/config.toml` | `[providers.input.overrides.{name}]` |
| **Output** | `[providers.output.{name}]` | `src/domains/output/providers/{name}/config.toml` | `[providers.output.overrides.{name}]` |
| **Decision** | `[providers.decision.{name}]` | `src/domains/decision/providers/{name}/config.toml` | `[providers.decision.providers.{name}]` |

### 配置优先级（从高到低）

1. `src/domains/{domain}/providers/{name}/config.toml` - Provider 本地配置文件（优先级最高）
2. `[providers.{domain}.{name}]` - 主配置文件中的 Provider 配置
3. Schema 默认值 - 从 Pydantic Schema 定义的默认值

### 配置路径说明

**新配置路径（推荐）**：
- `[providers.input.bili_danmaku_official]` - B站官方弹幕输入配置
- `[providers.output.tts]` - TTS 输出配置
- `[providers.decision.maicore]` - MaiCore 决策配置

**向后兼容旧路径（已废弃，但仍支持）**：
- `[providers.input.inputs.{name}]` - 旧的 Input Provider 详细配置
- `[providers.input.overrides.{name}]` - 旧的 Input Provider 参数覆盖
- `[providers.output.outputs.{name}]` - 旧的 Output Provider 详细配置
- `[providers.output.overrides.{name}]` - 旧的 Output Provider 参数覆盖
- `[providers.decision.providers.{name}]` - 旧的 Decision Provider 配置

## Input Provider 配置示例

### STTInputProvider: 语音转文字

```toml
[providers.input.stt]
type = "stt"

# 音频配置
[providers.input.stt.audio]
sample_rate = 16000
channels = 1
dtype = "int16"
stt_input_device_name = "麦克风名称"  # 可选，留空使用默认设备
use_remote_stream = false  # 使用远程音频流

# VAD 配置
[providers.input.stt.vad]
enable = true
vad_threshold = 0.5
silence_seconds = 1.0

# 讯飞 ASR 配置
[providers.input.stt.iflytek_asr]
host = "wss://istream-iflytek.xf-yun.com"
path = "/v2/iat"
appid = "your_appid"
api_secret = "your_api_secret"
api_key = "your_api_key"
language = "zh_cn"
domain = "iat"

# 消息配置
[providers.input.stt.message_config]
user_id = "stt_user"
user_nickname = "语音"
```

### BiliDanmakuOfficialInputProvider: B站官方弹幕

```toml
[providers.input.bili_danmaku_official]
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

### ConsoleInputProvider: 控制台输入

```toml
[providers.input.console_input]
type = "console_input"
# 没有其他必需配置
```

### MockDanmakuInputProvider: 模拟弹幕

```toml
[providers.input.mock_danmaku]
type = "mock_danmaku"
# 模拟消息列表（可选）
messages = [
    "你好",
    "测试消息",
    "这是模拟弹幕"
]
# 消息间隔（秒）
interval = 5.0
```

## Decision Provider 配置示例

### MaiCoreDecisionProvider: MaiCore 决策

```toml
[providers.decision.maicore]
type = "maicore"
host = "127.0.0.1"
port = 8000
connect_timeout = 10.0
reconnect_interval = 5.0

# Action 建议配置（可选）
action_suggestions_enabled = false
action_confidence_threshold = 0.6
action_cooldown_seconds = 5.0
max_suggested_actions = 3
```

### LLMDecisionProvider: LLM 决策

```toml
[providers.decision.llm]
type = "llm"
# LLM 类型（使用全局配置的 llm, llm_fast, vlm）
llm_type = "llm"

# 降级模式（LLM调用失败时的行为）
fallback_mode = "simple"  # simple（返回原始文本） | echo（回声） | error（抛出异常）

# 自定义模型配置（可选，不设置则使用全局配置）
# model = "gpt-4"
# api_key = "your-api-key"
# base_url = "https://api.openai.com/v1"
# temperature = 0.7
# max_tokens = 1000
```

**注意**：提示词已移至 `PromptManager` 统一管理，位置：`src/prompts/templates/decision/llm_intent_parser.md.j2`

### MaicraftDecisionProvider: MaiCraft 弹幕游戏决策

```toml
[providers.decision.maicraft]
enabled = true
type = "maicraft"
factory_type = "log"  # log | mcp
command_prefix = "!"

# 命令映射
[providers.decision.maicraft.command_mappings]
chat = "chat"
attack = "attack"
```

### RuleEngineDecisionProvider: 规则引擎决策

```toml
[providers.decision.rule_engine]
type = "rule_engine"
# 规则文件路径
rules_file = "data/rules/decision_rules.toml"
# 默认回复
default_response = "我不知道怎么回答这个问题"
```

## Output Provider 配置示例

### TTSOutputProvider: Edge TTS 语音合成

```toml
[providers.output.tts]
type = "tts"
# TTS 语音
voice = "zh-CN-YunxiNeural"
# 音量 (0.0 - 1.0)
volume = 1.0
# 语速 (0.5 - 2.0)
rate = 1.0
# 音调 (-10.0 - 10.0)
pitch = 0.0
# 输出设备名称（可选）
output_device_name = "扬声器名称"
```

### GPTSoVITSOutputProvider: GPT-SoVITS 语音合成

```toml
[providers.output.gptsovits]
type = "gptsovits"
host = "127.0.0.1"
port = 9880
ref_audio_path = "path/to/reference.wav"
prompt_text = "参考文本"
text_language = "zh"
prompt_language = "zh"
top_k = 20
top_p = 0.6
temperature = 0.3
speed_factor = 1.0
streaming_mode = true
sample_rate = 32000
output_device_name = "扬声器名称"
```

### AvatarOutputProvider: 虚拟形象输出

```toml
[providers.output.avatar]
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

### VTSOutputProvider: VTS 控制

```toml
[providers.output.vts]
type = "vts"
vts_host = "127.0.0.1"
vts_port = 8000
# 热键配置
hotkey_file = "data/vts_hotkeys.json"
```

### SubtitleOutputProvider: 字幕输出

```toml
[providers.output.subtitle]
type = "subtitle"
# 字体配置
font_name = "Microsoft YaHei"
font_size = 32
font_color = "#FFFFFF"
# 窗口配置
window_width = 1000
window_height = 100
window_x = 100
window_y = 100
# 背景配置
background_color = "#000000"
background_alpha = 0.5
```

### ObsControlOutputProvider: OBS 控制

```toml
[providers.output.obs_control]
type = "obs_control"
host = "localhost"
port = 4455
password = "your_password"  # 可选
text_source_name = "text"
typewriter_enabled = false
typewriter_speed = 0.1
typewriter_delay = 0.5
test_on_connect = true
```

### StickerOutputProvider: 贴纸输出

```toml
[providers.output.sticker]
type = "sticker"
sticker_size = 0.33
sticker_rotation = 90
sticker_position_x = 0.0
sticker_position_y = 0.0
image_width = 256
image_height = 256
cool_down_seconds = 5.0
display_duration_seconds = 3.0
```

### RemoteStreamOutputProvider: 远程流媒体输出

```toml
[providers.output.remote_stream]
type = "remote_stream"
server_mode = true
host = "0.0.0.0"
port = 8765
audio_sample_rate = 16000
audio_channels = 1
audio_format = "int16"
audio_chunk_size = 1024
image_width = 640
image_height = 480
image_format = "jpeg"
image_quality = 80
reconnect_delay = 5
max_reconnect_attempts = -1  # -1 表示无限
```

## 参数覆盖配置示例

### 在主配置文件中直接配置 Provider

新配置结构（推荐）：

```toml
# === 输入Provider配置 ===
[providers.input.bili_danmaku_official]
id_code = "新的直播间ID"
app_id = "新的应用ID"
access_key = "访问密钥"
access_key_secret = "访问密钥Secret"

# === 输出Provider配置 ===
[providers.output.subtitle]
font_size = 48
window_width = 1200

[providers.output.tts]
voice = "zh-CN-XiaoxiaoNeural"
rate = 1.2

[providers.output.vts]
vts_host = "192.168.1.100"
vts_port = 8000

# === 决策Provider配置 ===
[providers.decision.maicore]
host = "192.168.1.100"
port = 8000

[providers.decision.llm]
llm_type = "llm_fast"
```

**向后兼容旧配置结构（已废弃）**：

```toml
# 旧配置结构（已废弃，但仍支持）
[providers.input.overrides.bili_danmaku_official]
id_code = "新的直播间ID"
app_id = "新的应用ID"

[providers.output.overrides.subtitle]
font_size = 48

[providers.output.overrides.tts]
voice = "zh-CN-XiaoxiaoNeural"

[providers.output.overrides.vts]
vts_host = "192.168.1.100"

[providers.decision.overrides.maicore]
host = "192.168.1.100"
```

## 管道配置示例

### RateLimitPipeline: 限流管道

```toml
[pipelines.rate_limit]
priority = 100
enabled = true
global_rate_limit = 100  # 全局每分钟最大消息数
user_rate_limit = 10     # 每个用户每分钟最大消息数
window_size = 60         # 滑动窗口大小（秒）
```

### SimilarFilterPipeline: 相似消息过滤

```toml
[pipelines.similar_filter]
priority = 500
enabled = true
similarity_threshold = 0.85
time_window = 5.0
```

### ProfanityFilterPipeline: 敏感词过滤

```toml
[pipelines.profanity_filter]
enabled = true
priority = 100
words = ["测试脏话", "示例敏感词"]
replacement = "***"
# 可选：从文件加载
# wordlist_file = "data/profanity_words.txt"
filter_tts = true
filter_subtitle = true
case_sensitive = false
drop_on_match = false
```

### MessageLoggerPipeline: 消息日志

```toml
[pipelines.message_logger]
priority = 900
log_dir = "data/message_logs"  # 日志目录（可选）
```

## 全局配置示例

### LLM 配置

```toml
[llm]
client = "openai"
model = "gpt-4"
temperature = 0.2
api_key = "your-api-key"
base_url = "https://api.openai.com/v1"
max_tokens = 1024
max_retries = 3
retry_delay = 1.0
```

### MaiCore 连接配置

```toml
[maicore]
host = "127.0.0.1"
port = 8000
# token = "your_maicore_token_if_needed"
```

### 人设配置

```toml
[persona]
bot_name = "麦麦"
personality = "活泼开朗，有些调皮，喜欢和观众互动"
style_constraints = "口语化，使用网络流行语，避免机械式回复，适当使用emoji"
user_name = "大家"
max_response_length = 50
emotion_intensity = 7
```

### 日志配置

```toml
[logging]
enabled = true
format = "jsonl"  # jsonl | text
directory = "logs"
level = "INFO"  # TRACE | DEBUG | INFO | WARNING | ERROR | CRITICAL
rotation = "10 MB"
retention = "7 days"
compression = "zip"
split_by_session = false
```

## 配置最佳实践

1. **统一使用主配置文件**：所有 Provider 和 Pipeline 的配置统一在 `config.toml` 中管理

2. **配置路径**：
   - `[providers.input.{name}]` - Input Provider 配置
   - `[providers.output.{name}]` - Output Provider 配置
   - `[providers.decision.{name}]` - Decision Provider 配置
   - `[pipelines.{name}]` - Pipeline 配置

3. **敏感信息管理**：
   - API 密钥、密码等敏感信息可以使用环境变量
   - 或者在主配置文件中配置，不要提交到版本控制

4. **配置验证**：
   - 所有 Provider 都使用 Pydantic Schema 进行配置验证
   - 如果配置不符合 Schema 要求，启动时会报错

5. **配置更新**：
   - 修改配置后需要重启应用
   - Provider 支持运行时切换（Decision Provider）
