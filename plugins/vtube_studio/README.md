# VTube Studio 插件

这个插件为 Amaidesu 提供了与 VTube Studio 的集成功能，包括智能热键触发、表情控制和实时口型同步等功能。

## 功能特性

### 1. 热键触发
- 基于 LLM 的智能热键匹配
- 根据聊天内容自动触发相应的表情热键
- 支持自定义表情关键词映射

### 2. 表情控制
- 程序化控制眼部动作（眨眼、闭眼）
- 嘴部表情控制（微笑等）
- VTS 参数值设置和获取

### 3. 实时口型同步 ✨
- 与 TTS 插件联动，实时分析音频进行口型同步
- 支持音量检测和元音识别
- 平滑的口型过渡效果
- 适配多种 TTS 引擎（Edge TTS、GPT-SoVITS等）

### 4. 道具管理
- 加载和卸载虚拟道具
- 支持自定义道具位置、大小、旋转等参数

## 安装依赖

```bash
# 基础依赖
pip install pyvts

# 口型同步依赖（可选）
pip install librosa scipy numpy

# LLM功能依赖（可选）
pip install openai
```

## 配置说明

### 基础配置

复制 `config-template.toml` 为 `config.toml` 并根据需要修改：

```toml
[vtube_studio]
enabled = true
plugin_name = "Amaidesu_VTS_Connector"
developer = "your-name"
authentication_token_path = "./src/plugins/vtube_studio/vts_token.txt"
vts_host = "localhost"
vts_port = 8001
```

### 口型同步配置

```toml
[vtube_studio.lip_sync]
enabled = true                       # 启用口型同步
volume_threshold = 0.01              # 音量阈值（越小越敏感）
smoothing_factor = 0.3               # 平滑因子（0-1，越大越平滑）
vowel_detection_sensitivity = 2.0    # 元音检测敏感度
sample_rate = 32000                  # 音频采样率
buffer_size = 1024                   # 缓冲区大小

# 🆕 播放时间同步配置
min_accumulation_duration = 0.1      # 最小累积时长（秒）
max_accumulation_duration = 0.2      # 最大累积时长（秒）
playback_sync_enabled = true         # 启用播放时间同步
```

### LLM 智能匹配配置

```toml
llm_matching_enabled = true
llm_api_key = "your-api-key"
llm_base_url = "https://api.siliconflow.cn/v1"
llm_model = "deepseek-chat"
llm_temperature = 0.1
```

## VTube Studio 设置

### 1. 启用 API
1. 打开 VTube Studio
2. 进入设置 → 通用 → 插件
3. 启用 "Allow API access"
4. 启用 "Allow external plugins"

### 2. 口型同步参数设置

为了实现口型同步，您的 VTube Studio 模型需要包含以下参数：

#### 必需参数：
- `VoiceVolume`: 音量参数 (0.0-1.0)
- `VoiceSilence`: 静音参数 (0.0=有声, 1.0=静音)

#### 元音参数（可选）：
- `VoiceA`: A 音参数 (0.0-1.0)
- `VoiceI`: I 音参数 (0.0-1.0) 
- `VoiceU`: U 音参数 (0.0-1.0)
- `VoiceE`: E 音参数 (0.0-1.0)
- `VoiceO`: O 音参数 (0.0-1.0)

### 3. 模型参数配置

在 VTube Studio 中：

1. 选择您的模型
2. 进入参数设置
3. 确保上述参数存在并正确映射到模型的口型控制
4. 调整参数范围和响应曲线以获得最佳效果

## 使用方法

### 启动连接

1. 确保 VTube Studio 已运行并启用了 API
2. 启动 Amaidesu，插件会自动尝试连接
3. 首次连接时，VTube Studio 会弹出认证请求，点击"允许"
4. 认证令牌会自动保存，后续无需重复认证

### 口型同步使用

口型同步功能会自动与 TTS 插件联动：

1. 确保 TTS 插件已启用并正常工作
2. 当 TTS 播放语音时，VTube Studio 插件会自动：
   - 检测音频音量并控制 `VoiceVolume` 参数
   - 分析元音特征并控制相应的元音参数
   - 在静音时设置 `VoiceSilence` 参数

#### 🆕 播放时间同步控制

插件现在支持基于**实际播放时间**控制口型，而不仅仅是音频数据长度：

- **播放时间同步模式**：口型更新基于真实的音频播放时间
- **智能累积策略**：同时考虑播放时间和音频数据时长
- **精确会话管理**：精确控制口型同步的开始和结束

详细信息请参考：[播放时间控制文档](PLAYBACK_TIMING.md)

### 手动控制表情

插件提供了程序化的表情控制接口：

```python
# 通过服务调用
vts_service = core.get_service("vts_control")

# 控制微笑
await vts_service.smile(1.0)  # 完全微笑
await vts_service.smile(0.0)  # 不微笑

# 控制眼部
await vts_service.close_eyes()  # 闭眼
await vts_service.open_eyes()   # 睁眼

# 触发热键
await vts_service.trigger_hotkey("hotkey_name")
```

## 故障排除

### 1. 连接问题

**问题**: 无法连接到 VTube Studio
- 确认 VTube Studio 正在运行
- 检查 API 是否已启用
- 确认端口号（默认 8001）没有被占用
- 检查防火墙设置

### 2. 认证问题

**问题**: 认证失败或超时
- 删除 `vts_token.txt` 文件重新认证
- 确保在 VTube Studio 弹出认证请求时及时点击"允许"
- 检查插件名称是否与配置一致

### 3. 口型同步问题

**问题**: 口型同步不工作
- 确认模型包含必需的口型参数
- 检查 TTS 插件是否正常工作
- 确认音频分析依赖已安装（librosa、scipy）
- 调整 `volume_threshold` 和 `vowel_detection_sensitivity` 参数

**问题**: 口型过于敏感或迟钝
- 调整 `volume_threshold`：值越小越敏感
- 调整 `smoothing_factor`：值越大口型变化越平滑
- 调整 `vowel_detection_sensitivity`：影响元音检测敏感度

### 4. 性能问题

**问题**: 口型同步造成性能下降
- 降低 `sample_rate` 值（如设为 16000）
- 增大 `buffer_size` 值
- 在配置中禁用不需要的元音检测

## API 参考

### 服务接口

插件注册了两个服务：

1. `vts_control`: 基础 VTS 控制功能
2. `vts_lip_sync`: 口型同步功能

### 主要方法

```python
# VTS 控制服务
await vts_control.trigger_hotkey(hotkey_id)
await vts_control.set_parameter_value(param_name, value, weight)
await vts_control.get_parameter_value(param_name)
await vts_control.smile(value)
await vts_control.close_eyes()
await vts_control.open_eyes()

# 口型同步服务  
await vts_lip_sync.start_lip_sync_session(text)
await vts_lip_sync.process_tts_audio(audio_data, sample_rate)
await vts_lip_sync.stop_lip_sync_session()
```

## 更新日志

### v1.2.0
- ✨ 新增实时口型同步功能
- 🔧 支持与多种 TTS 插件联动
- 📝 完善文档和配置说明

### v1.1.0
- 🤖 添加基于 LLM 的智能热键匹配
- 🎭 扩展表情控制 API
- 🐛 修复连接稳定性问题

### v1.0.0
- 🎉 初始版本发布
- ⭐ 基础 VTS 连接和热键触发功能 