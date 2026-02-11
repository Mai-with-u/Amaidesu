# 人工环境测试需求分析

本文档分析现有自动化测试无法覆盖的、需要人工搭建真实环境进行测试的组件和功能。

## 一、需要真实环境测试的 Provider 分类

### 1.1 虚拟形象控制类（Output Domain - Avatar）

#### VTSOutputProvider (`src/domains/output/providers/avatar/vts/`)

**外部依赖：**
- VTube Studio 软件（需运行并开启 WebSocket 服务器）
- 音频设备（用于口型同步测试）

**需要人工测试的功能：**
- WebSocket 连接（localhost:8001）
- 热键触发（VTS 热键 API）
- 表情参数控制（MouthSmile, MouthOpen, EyeOpen 等）
- 口型同步（lip sync）
- 道具加载/卸载/更新
- LLM 智能热键匹配（需要 LLM API）

**测试配置要求：**
```toml
[providers.output.vts]
enabled = true
host = "localhost"
port = 8001
llm_matching_enabled = false  # 如需测试需要额外配置 LLM
```

**测试步骤：**
1. 启动 VTube Studio 软件
2. 在 VTS 设置中开启 WebSocket 插件（端口 8001）
3. 运行应用并发送测试消息
4. 观察：
   - VTS 模型表情变化
   - 口型是否同步
   - 热键是否正确触发

---

#### WarudoOutputProvider (`src/domains/output/providers/avatar/warudo/`)

**外部依赖：**
- Warudo 软件（需运行 WebSocket 服务器）

**需要人工测试的功能：**
- WebSocket 连接（localhost:19190）
- 表情事件发送（`vts.send_emotion`）
- 状态事件发送（`vts.send_state`）
- TTS 音频同步（`tts.audio_ready`）

**测试配置要求：**
```toml
[providers.output.warudo]
enabled = true
ws_host = "localhost"
ws_port = 19190
```

**测试步骤：**
1. 启动 Warudo 软件
2. 确认 WebSocket 服务器在 19190 端口运行
3. 触发表情事件
4. 观察 Warudo 虚拟形象是否响应

---

#### VRChatOutputProvider (`src/domains/output/providers/avatar/vrchat/`)

**外部依赖：**
- VRChat 应用程序
- OSC 网络接口

**需要人工测试的功能：**
- OSC 消息发送
- 表情参数控制
- 嘴型同步

---

### 1.2 TTS 语音合成类（Output Domain - Audio）

#### TTSOutputProvider (`src/domains/output/providers/audio/`)

**外部依赖：**
- 音频输出设备（扬声器/耳机）
- Edge TTS API（在线服务）或 Omni TTS 服务

**需要人工测试的功能：**
- 音频设备枚举和选择
- Edge TTS 调用和网络请求
- 音频流式播放（sounddevice 库）
- 音频中断和队列管理
- 异常处理（网络失败、播放失败）

**测试配置要求：**
```toml
[providers.output.tts]
enabled = true
engine = "edge"  # 或 "omni"
voice = "zh-CN-XiaoxiaoNeural"
output_device_name = ""  # 留空使用默认设备
```

**测试步骤：**
1. 连接音频输出设备
2. 发送带 TTS 文本的消息
3. 观察：
   - 语音是否正常播放
   - 语音质量是否符合预期
   - 音量是否正常
   - 多条消息的播放顺序是否正确

---

#### GPTSoVITSOutputProvider (`src/domains/output/providers/audio/`)

**外部依赖：**
- GPT-SoVITS 服务器（本地或远程）
- 音频输出设备

**需要人工测试的功能：**
- GPT-SoVITS API 调用（http://127.0.0.1:9880）
- 参考音频上传和配置
- 流式 TTS 音频接收
- 音频播放（sounddevice 库）
- text_cleanup 服务集成
- vts_lip_sync 服务集成

**测试配置要求：**
```toml
[providers.output.gptsovits]
enabled = true
host = "127.0.0.1"
port = 9880
ref_audio_path = "/path/to/reference.wav"
prompt_text = "参考文本"
text_language = "zh"
streaming_mode = true
output_device_name = ""
```

**测试步骤：**
1. 启动 GPT-SoVITS 服务器
2. 准备参考音频文件
3. 发送测试文本
4. 观察生成语音质量和速度

---

### 1.3 字幕显示类（Output Domain - Subtitle）

#### SubtitleOutputProvider (`src/domains/output/providers/subtitle/`)

**外部依赖：**
- 图形界面环境（支持 GUI）
- customtkinter 库

**需要人工测试的功能：**
- CustomTkinter 窗口显示
- 文字渲染和描边效果
- 窗口位置和大小调整
- 文字颜色、字体、大小配置
- 自动滚动和换行
- GUI 线程安全性

**测试配置要求：**
```toml
[providers.output.subtitle]
enabled = true
window_width = 800
window_height = 100
text_color = "white"
outline_color = "black"
font_size = 24
```

**测试步骤：**
1. 配置启用 subtitle provider
2. 运行应用
3. 观察字幕窗口是否正常显示
4. 测试不同长度的文字是否正常换行
5. 测试特殊字符和表情符号

---

#### StickerOutputProvider (`src/domains/output/providers/sticker/`)

**外部依赖：**
- VTube Studio（接收贴纸图片）

**需要人工测试的功能：**
- 图片 base64 解码和处理
- 图片大小调整（PIL 库）
- 发送图片到 VTS
- 冷却时间机制
- 贴纸显示时长控制

**测试配置要求：**
```toml
[providers.output.sticker]
enabled = true
sticker_size = 0.33
sticker_rotation = 90
cool_down_seconds = 5
display_duration_seconds = 3
```

---

### 1.4 直播平台输入类（Input Domain）

#### BiliDanmakuInputProvider (`src/domains/input/providers/bili_danmaku/`)

**外部依赖：**
- Bilibili 直播间（需要有真实 room_id）
- 网络连接

**需要人工测试的功能：**
- Bilibili API 调用（弹幕历史接口）
- 实时轮询机制
- 弹幕去重（基于 timestamp）
- 网络异常处理

**测试配置要求：**
```toml
[providers.input.bili_danmaku]
enabled = true
room_id = 123456  # 真实直播间 ID
poll_interval = 3
message_config = {}
```

**测试步骤：**
1. 找一个正在直播的 B 站直播间 ID
2. 配置 room_id
3. 启动应用
4. 在直播间发送弹幕
5. 观察应用是否接收到弹幕

---

#### BiliDanmakuOfficialInputProvider (`src/domains/input/providers/bili_danmaku_official/`)

**外部依赖：**
- Bilibili 直播间（需真实 room_id）
- 网络连接

**需要人工测试的功能：**
- Bilibili 官方 WebSocket 连接
- Protobuf 消息解析
- 各类消息处理（弹幕、礼物、SC、入场等）
- 消息缓存机制
- 心跳保活

**测试配置要求：**
```toml
[providers.input.bili_danmaku_official]
enabled = true
room_id = 123456
```

---

#### ReadPingmuInputProvider (`src/domains/input/providers/read_pingmu/`)

**外部依赖：**
- 需要查看具体实现

---

#### MainosabaInputProvider (`src/domains/input/providers/mainosaba/`)

**外部依赖：**
- 需要查看具体实现

---

#### STTInputProvider (`src/domains/input/providers/stt/`)

**外部依赖：**
- 麦克风设备
- 语音识别服务/库

**需要人工测试的功能：**
- 麦克风音频采集
- 实时语音识别
- 识别结果发布
- 识别启停控制

---

#### RemoteStreamInputProvider (`src/domains/input/providers/remote_stream/`)

**外部依赖：**
- 网络连接
- 远程流服务

---

### 1.5 决策服务类（Decision Domain）

#### MaiCoreDecisionProvider (`src/domains/decision/providers/maicore/`)

**外部依赖：**
- MaiCore 服务器（WebSocket 服务器）
- maim_message 库（Router 协议）

**需要人工测试的功能：**
- WebSocket 连接（ws://localhost:8000/ws）
- 消息序列化和反序列化（maim_message）
- 异步响应处理（Future 模式）
- HTTP 回调（可选）
- 连接断开重连
- Intent 解析正确性

**测试配置要求：**
```toml
[providers.decision.maicore]
enabled = true
host = "localhost"
port = 8000
platform = "amaidesu"
http_host = "localhost"
http_port = 8080
http_callback_path = "/callback"
```

**测试步骤：**
1. 启动 MaiCore 服务器
2. 发送测试消息
3. 观察返回的 Intent 是否正确
4. 测试网络异常情况

---

#### MaicraftDecisionProvider (`src/domains/decision/providers/maicraft/`)

**外部依赖：**
- Maicraft 服务器
- 网络连接

---

#### LLMDecisionProvider (`src/domains/decision/providers/llm/`)

**外部依赖：**
- LLM API（OpenAI、Anthropic 等）
- 网络连接

**需要人工测试的功能：**
- LLM API 调用
- Prompt 渲染
- 响应解析
- 流式输出处理
- 上下文管理

---

### 1.6 OBS 控制类（Output Domain）

#### ObsControlOutputProvider (`src/domains/output/providers/obs_control/`)

**外部依赖：**
- OBS Studio 软件（需开启 WebSocket）
- obsws-python 库

**需要人工测试的功能：**
- OBS WebSocket 连接（localhost:4455）
- 文本源更新
- 密码认证
- 连接状态管理

**测试配置要求：**
```toml
[providers.output.obs_control]
enabled = true

[providers.output.obs_control.obs]
host = "localhost"
port = 4455
password = "your_password"
text_source_name = "text"
```

**测试步骤：**
1. 启动 OBS Studio
2. 安装并配置 WebSocket 插件
3. 创建一个文本源
4. 运行应用并发送文本
5. 观察 OBS 中文本源是否更新

---

### 1.7 远程流控制类（Output Domain）

#### RemoteStreamOutputProvider (`src/domains/output/providers/remote_stream/`)

**外部依赖：**
- 网络连接
- 远程客户端

**需要人工测试的功能：**
- WebSocket 连接管理
- 音频流传输
- 元数据同步
- 多客户端支持

---

### 1.8 Warudo 控制类（Output Domain）

#### WarudoOutputProvider (`src/domains/output/providers/warudo/`)

**外部依赖：**
- Warudo 软件
- WebSocket 连接

---

## 二、集成测试场景（全链路人工测试）

### 2.1 完整 VTuber 流程测试

**环境要求：**
- 至少 1 个 Input Provider（如 ConsoleInput 或 BiliDanmaku）
- Decision Provider（MaiCore、Maicraft 或 LLM）
- 至少 2 个 Output Provider（如 TTS + VTS）

**测试场景：**
1. 用户发送弹幕 → 应用接收 → 决策 → 生成语音 → VTS 表情变化
2. 礼物消息 → 触发特殊动作 → 感谢语音
3. 超级聊天 → 显示字幕 → 特殊表情

**测试步骤：**
1. 配置所有 Provider
2. 启动所有外部服务（MaiCore、VTS、GPT-SoVITS 等）
3. 运行应用
4. 模拟各种用户输入
5. 观察整个数据流是否正常

---

### 2.2 性能和稳定性测试

**测试项目：**
- 长时间运行稳定性（24 小时+）
- 高并发弹幕处理（100 条/秒）
- 内存泄漏检测
- 网络断开重连
- 音频播放队列溢出

---

## 三、测试自动化程度评估

### 3.1 完全可自动化测试
- EventBus 功能测试
- ProviderManager 生命周期测试
- 决策逻辑测试（使用 Mock）
- 数据格式转换测试（RawData → NormalizedMessage → Intent → RenderParameters）

### 3.2 部分可自动化测试
- TTSOutputProvider：可 Mock 音频设备，但无法测试实际音质
- VTSOutputProvider：可 Mock WebSocket 连接，但无法测试实际效果
- SubtitleOutputProvider：可测试业务逻辑，但 GUI 显示需要人工验证

### 3.3 完全需要人工测试
- 虚拟形象控制（VTS、Warudo、VRChat）
- 音频播放质量和效果
- GUI 显示效果
- 与外部服务的集成
- 性能和稳定性测试

---

## 四、人工测试优先级建议

### P0 - 核心功能（必须测试）
1. **VTSOutputProvider** - 虚拟形象控制是核心功能
2. **TTSOutputProvider** - 语音输出是核心功能
3. **MaiCoreDecisionProvider** - 决策是核心流程
4. **完整数据流测试** - 端到端验证

### P1 - 重要功能（推荐测试）
1. **GPTSoVITSOutputProvider** - 如果使用 GPT-SoVITS
2. **SubtitleOutputProvider** - 字幕显示
3. **BiliDanmakuInputProvider** - 弹幕输入

### P2 - 辅助功能（可选测试）
1. **ObsControlOutputProvider** - OBS 集成
2. **WarudoOutputProvider** - Warudo 虚拟形象
3. **StickerOutputProvider** - 贴纸功能

---

## 五、测试记录模板

```markdown
## 人工测试记录：XXXProvider

**测试日期：** YYYY-MM-DD
**测试人员：** [姓名]
**环境信息：**
- 操作系统：
- 外部服务版本：
- 配置文件：

### 测试用例1：基本功能测试
**步骤：**
1. ...
2. ...

**预期结果：**
- ...

**实际结果：**
- ...

**状态：** ✓ 通过 / ✗ 失败 / △ 部分通过

**备注：**
[遇到的问题、建议等]

### 测试用例2：异常处理测试
...
```

---

## 六、持续改进建议

1. **增加 Mock 服务器**：
   - 实现 MockMaiCore 服务器
   - 实现 MockVTS 服务器
   - 实现 MockGPTSoVITS 服务器

2. **增加 E2E 自动化测试**：
   - 使用 Selenium/Playwright 自动化 GUI 测试
   - 使用音频分析库验证 TTS 输出

3. **增加性能监控**：
   - 添加性能指标收集
   - 自动化性能回归测试

4. **增加日志分析**：
   - 自动化日志分析工具
   - 异常检测和告警
