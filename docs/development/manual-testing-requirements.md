# 人工环境测试需求分析

本文档分析现有自动化测试无法覆盖的、需要人工搭建真实环境进行测试的组件和功能。

## 一、需要真实环境测试的Provider分类

### 1.1 虚拟形象控制类（Layer 7 渲染）

#### VTSProvider (`src/domains/rendering/providers/vts/`)
**外部依赖：**
- VTube Studio 软件（需运行并开启WebSocket服务器）
- 音频设备（用于口型同步测试）

**需要人工测试的功能：**
- ✗ WebSocket连接（localhost:8001）
- ✗ 热键触发（VTS热键API）
- ✗ 表情参数控制（MouthSmile, MouthOpen, EyeOpen等）
- ✗ 口型同步（lip sync）
- ✗ 道具加载/卸载/更新
- ✗ LLM智能热键匹配（需要LLM API）

**测试配置要求：**
```toml
[providers.output.outputs.vts]
type = "vts"
enabled = true
vts_host = "localhost"
vts_port = 8001
llm_matching_enabled = false  # 如需测试需要额外配置LLM
```

**测试步骤：**
1. 启动VTube Studio软件
2. 在VTS设置中开启WebSocket插件（端口8001）
3. 运行应用并发送测试消息
4. 观察：
   - VTS模型表情变化
   - 口型是否同步
   - 热键是否正确触发

---

#### WarudoOutputProvider (`src/domains/rendering/providers/warudo/`)
**外部依赖：**
- Warudo 软件（需运行WebSocket服务器）

**需要人工测试的功能：**
- ✗ WebSocket连接（localhost:19190）
- ✗ 表情事件发送（`vts.send_emotion`）
- ✗ 状态事件发送（`vts.send_state`）
- ✗ TTS音频同步（`tts.audio_ready`）

**测试配置要求：**
```toml
[providers.output.outputs.warudo]
type = "warudo"
ws_host = "localhost"
ws_port = 19190
```

**测试步骤：**
1. 启动Warudo软件
2. 确认WebSocket服务器在19190端口运行
3. 触发表情事件
4. 观察Warudo虚拟形象是否响应

---

#### AvatarOutputProvider (`src/domains/rendering/providers/avatar/`)
**外部依赖：**
- 需要查看具体实现（可能是另一种虚拟形象控制）

---

### 1.2 TTS语音合成类（Layer 7 渲染）

#### TTSProvider (`src/domains/rendering/providers/tts/`)
**外部依赖：**
- 音频输出设备（扬声器/耳机）
- Edge TTS API（在线服务）或 Omni TTS服务

**需要人工测试的功能：**
- ✗ 音频设备枚举和选择
- ✗ Edge TTS调用和网络请求
- ✗ 音频流式播放（sounddevice库）
- ✗ 音频中断和队列管理
- ✗ 异常处理（网络失败、播放失败）

**测试配置要求：**
```toml
[providers.output.outputs.tts]
type = "tts"
enabled = true
engine = "edge"  # 或 "omni"
voice = "zh-CN-XiaoxiaoNeural"
output_device_name = ""  # 留空使用默认设备
```

**测试步骤：**
1. 连接音频输出设备
2. 发送带TTS文本的消息
3. 观察：
   - 语音是否正常播放
   - 语音质量是否符合预期
   - 音量是否正常
   - 多条消息的播放顺序是否正确

---

#### GPTSoVITSProvider (`src/domains/rendering/providers/gptsovits/`)
**外部依赖：**
- GPT-SoVITS服务器（本地或远程）
- 音频输出设备

**需要人工测试的功能：**
- ✗ GPT-SoVITS API调用（http://127.0.0.1:9880）
- ✗ 参考音频上传和配置
- ✗ 流式TTS音频接收
- ✗ 音频播放（sounddevice库）
- ✗ text_cleanup服务集成
- ✗ vts_lip_sync服务集成

**测试配置要求：**
```toml
[providers.output.outputs.gptsovits]
type = "gptsovits"
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
1. 启动GPT-SoVITS服务器
2. 准备参考音频文件
3. 发送测试文本
4. 观察生成语音质量和速度

---

#### OmniTTSProvider (`src/domains/rendering/providers/omni_tts/`)
**外部依赖：**
- Omni TTS服务
- 音频输出设备

---

### 1.3 字幕显示类（Layer 7 渲染）

#### SubtitleProvider (`src/domains/rendering/providers/subtitle/`)
**外部依赖：**
- 图形界面环境（支持GUI）
- customtkinter库

**需要人工测试的功能：**
- ✗ CustomTkinter窗口显示
- ✗ 文字渲染和描边效果
- ✗ 窗口位置和大小调整
- ✗ 文字颜色、字体、大小配置
- ✗ 自动滚动和换行
- ✗ GUI线程安全性

**测试配置要求：**
```toml
[providers.output.outputs.subtitle]
type = "subtitle"
enabled = true
window_width = 800
window_height = 100
text_color = "white"
outline_color = "black"
font_size = 24
```

**测试步骤：**
1. 配置启用subtitle provider
2. 运行应用
3. 观察字幕窗口是否正常显示
4. 测试不同长度的文字是否正常换行
5. 测试特殊字符和表情符号

---

#### StickerOutputProvider (`src/domains/rendering/providers/sticker/`)
**外部依赖：**
- VTube Studio（接收贴纸图片）

**需要人工测试的功能：**
- ✗ 图片base64解码和处理
- ✗ 图片大小调整（PIL库）
- ✗ 发送图片到VTS
- ✗ 冷却时间机制
- ✗ 贴纸显示时长控制

**测试配置要求：**
```toml
[providers.output.outputs.sticker]
type = "sticker"
enabled = true
sticker_size = 0.33
sticker_rotation = 90
cool_down_seconds = 5
display_duration_seconds = 3
```

---

### 1.4 直播平台输入类（Layer 1 输入）

#### BiliDanmakuProvider (`src/domains/input/providers/bili_danmaku/`)
**外部依赖：**
- Bilibili直播间（需要有真实room_id）
- 网络连接

**需要人工测试的功能：**
- ✗ Bilibili API调用（弹幕历史接口）
- ✗ 实时轮询机制
- ✗ 弹幕去重（基于timestamp）
- ✗ 网络异常处理

**测试配置要求：**
```toml
[providers.input.inputs.bili_danmaku]
type = "bili_danmaku"
enabled = true
room_id = 123456  # 真实直播间ID
poll_interval = 3
message_config = {}
```

**测试步骤：**
1. 找一个正在直播的B站直播间ID
2. 配置room_id
3. 启动应用
4. 在直播间发送弹幕
5. 观察应用是否接收到弹幕

---

#### BiliOfficialProvider (`src/domains/input/providers/bili_danmaku_official/`)
**外部依赖：**
- Bilibili直播间（需真实room_id）
- 网络连接

**需要人工测试的功能：**
- ✗ Bilibili官方WebSocket连接
- ✗ Protobuf消息解析
- ✗ 各类消息处理（弹幕、礼物、SC、入场等）
- ✗ 消息缓存机制
- ✗ 心跳保活

**测试配置要求：**
```toml
[providers.input.inputs.bili_official]
type = "bili_official"
enabled = true
room_id = 123456
```

---

#### ReadPingmuProvider (`src/domains/input/providers/read_pingmu/`)
**外部依赖：**
- 需要查看具体实现

#### MainosabaProvider (`src/domains/input/providers/mainosaba/`)
**外部依赖：**
- 需要查看具体实现

---

### 1.5 决策服务类（Layer 3 决策）

#### MaiCoreDecisionProvider (`src/domains/decision/providers/maicore/`)
**外部依赖：**
- MaiCore服务器（WebSocket服务器）
- maim_message库（Router协议）

**需要人工测试的功能：**
- ✗ WebSocket连接（ws://localhost:8000/ws）
- ✗ 消息序列化和反序列化（maim_message）
- ✗ 异步响应处理（Future模式）
- ✗ HTTP回调（可选）
- ✗ 连接断开重连
- ✗ Intent解析正确性

**测试配置要求：**
```toml
[providers.decision.providers.maicore]
type = "maicore"
enabled = true
host = "localhost"
port = 8000
platform = "amaidesu"
http_host = "localhost"
http_port = 8080
http_callback_path = "/callback"
```

**测试步骤：**
1. 启动MaiCore服务器
2. 发送测试消息
3. 观察返回的Intent是否正确
4. 测试网络异常情况

---

### 1.6 OBS控制类（Layer 7 渲染）

#### ObsControlProvider (`src/domains/rendering/providers/obs_control/`)
**外部依赖：**
- OBS Studio软件（需开启WebSocket）
- obsws-python库

**需要人工测试的功能：**
- ✗ OBS WebSocket连接（localhost:4455）
- ✗ 文本源更新
- ✗ 密码认证
- ✗ 连接状态管理

**测试配置要求：**
```toml
[providers.output.outputs.obs_control]
type = "obs_control"
enabled = true
[providers.output.outputs.obs_control.obs]
host = "localhost"
port = 4455
password = "your_password"
text_source_name = "text"
```

**测试步骤：**
1. 启动OBS Studio
2. 安装并配置WebSocket插件
3. 创建一个文本源
4. 运行应用并发送文本
5. 观察OBS中文本源是否更新

---

### 1.7 语音识别类（未完全集成）

#### STT Plugin (`plugins_backup/stt/plugin.py`)
**外部依赖：**
- 麦克风设备
- 语音识别服务/库

**需要人工测试的功能：**
- ✗ 麦克风音频采集
- ✗ 实时语音识别
- ✗ 识别结果发布（RawData）
- ✗ 识别启停控制

**注意：** STT目前在plugins_backup中，尚未完全集成到新架构。

---

## 二、集成测试场景（全链路人工测试）

### 2.1 完整VTuber流程测试

**环境要求：**
- 至少1个输入Provider（如ConsoleInput或BiliDanmaku）
- MaiCore决策Provider（或LocalLLMDecisionProvider）
- 至少2个输出Provider（如TTS + VTS）

**测试场景：**
1. 用户发送弹幕 → 应用接收 → 决策 → 生成语音 → VTS表情变化
2. 礼物消息 → 触发特殊动作 → 感谢语音
3. 超级聊天 → 显示字幕 → 特殊表情

**测试步骤：**
1. 配置所有Provider
2. 启动所有外部服务（MaiCore、VTS、GPT-SoVITS等）
3. 运行应用
4. 模拟各种用户输入
5. 观察整个数据流是否正常

---

### 2.2 性能和稳定性测试

**测试项目：**
- 长时间运行稳定性（24小时+）
- 高并发弹幕处理（100条/秒）
- 内存泄漏检测
- 网络断开重连
- 音频播放队列溢出

---

## 三、测试自动化程度评估

### 3.1 完全可自动化测试
- ✗ EventBus功能测试
- ✗ ProviderManager生命周期测试
- � 决策逻辑测试（使用Mock）
- ✗ 数据格式转换测试（RawData → NormalizedMessage → Intent → RenderParameters）

### 3.2 部分可自动化测试
- △ TTSProvider：可Mock音频设备，但无法测试实际音质
- △ VTSProvider：可Mock WebSocket连接，但无法测试实际效果
- △ SubtitleProvider：可测试业务逻辑，但GUI显示需要人工验证

### 3.3 完全需要人工测试
- ✗ 虚拟形象控制（VTS、Warudo）
- ✗ 音频播放质量和效果
- ✗ GUI显示效果
- ✗ 与外部服务的集成
- ✗ 性能和稳定性测试

---

## 四、人工测试优先级建议

### P0 - 核心功能（必须测试）
1. **VTSProvider** - 虚拟形象控制是核心功能
2. **TTSProvider** - 语音输出是核心功能
3. **MaiCoreDecisionProvider** - 决策是核心流程
4. **完整数据流测试** - 端到端验证

### P1 - 重要功能（推荐测试）
1. **GPTSoVITSProvider** - 如果使用GPT-SoVITS
2. **SubtitleProvider** - 字幕显示
3. **BiliDanmakuProvider** - 弹幕输入

### P2 - 辅助功能（可选测试）
1. **ObsControlProvider** - OBS集成
2. **WarudoOutputProvider** - Warudo虚拟形象
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

1. **增加Mock服务器**：
   - 实现MockMaiCore服务器（已有mock_maicore.py）
   - 实现MockVTS服务器
   - 实现MockGPTSoVITS服务器

2. **增加E2E自动化测试**：
   - 使用Selenium/Playwright自动化GUI测试
   - 使用音频分析库验证TTS输出

3. **增加性能监控**：
   - 添加性能指标收集
   - 自动化性能回归测试

4. **增加日志分析**：
   - 自动化日志分析工具
   - 异常检测和告警
