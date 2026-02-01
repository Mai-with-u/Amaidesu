# Amaidesu Omni TTS 插件

Omni TTS 插件是专门使用阿里云 Qwen-Omni 大模型进行语音合成的插件，提供更自然、更高质量的语音合成体验。

## 功能特点

- **高质量语音合成**: 基于阿里云 Qwen-Omni 大模型
- **多语音音色支持**: 支持多种语音角色
- **音频后处理**: 支持音量调节、噪声添加等特效
- **彩蛋功能**: "麦炸了"特效（可配置触发概率）
- **调试功能**: 自动保存调试音频文件
- **UDP 广播**: 支持将 TTS 内容广播到外部监听器
- **智能错误处理**: 完善的异常处理和备用播放方案

## 依赖

### 必需依赖
- `aiohttp`: HTTP 客户端，用于调用 Qwen API
- `sounddevice`: 音频播放
- `soundfile`: 音频文件处理
- `numpy`: 音频数据处理

### 可选依赖
- `pydub`: 音频后处理（用于音频特效）

### 安装依赖

```bash
# 基础依赖
pip install aiohttp sounddevice soundfile numpy

# 音频后处理依赖（可选）
pip install pydub
```

## 配置

### API 密钥配置

在配置文件中设置：
```toml
[omni_tts]
api_key = "your_dashscope_api_key"
```

或者设置环境变量：
```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key"
```

### 完整配置示例

```toml
[omni_tts]
api_key = ""                          # API密钥
model_name = "qwen2.5-omni-7b"       # 模型名称
voice = "Chelsie"                    # 语音音色
format = "wav"                       # 音频格式
output_device_name = ""              # 输出设备名称
blow_up_probability = 0.01           # 彩蛋触发概率

[omni_tts.post_processing]
enabled = true                       # 启用音频后处理
volume_reduction = 3.0              # 音量降低3dB
noise_level = 0.1                   # 添加轻微噪声

[omni_tts.udp_broadcast]
enable = true                       # 启用UDP广播
host = "127.0.0.1"
port = 9999
```

## 特色功能

### 1. 音频后处理
- **音量调节**: 可以调整输出音频的音量
- **噪声添加**: 添加背景噪声模拟真实环境
- **特效处理**: 支持各种音频特效

### 2. 彩蛋功能
- 配置 `blow_up_probability` 可以设置"麦炸了"特效的触发概率
- 可以自定义彩蛋触发时的文本内容

### 3. 调试功能
- 自动保存生成的音频文件到 `debug_audio` 目录
- 包含原始音频和处理后音频的对比
- 详细的生成日志

## 与其他服务的集成

### 文本清理服务
插件会自动检测并使用 `text_cleanup` 服务来优化输入文本：

```python
cleanup_service = self.core.get_service("text_cleanup")
if cleanup_service:
    cleaned_text = await cleanup_service.clean_text(original_text)
```

### 字幕服务
播放音频时会自动通知字幕服务显示对应文本：

```python
subtitle_service = self.core.get_service("subtitle_service")
if subtitle_service:
    await subtitle_service.record_speech(text, duration_seconds)
```

## 工作流程

1. **消息接收**: 监听 WebSocket 文本消息
2. **文本清理**: 通过文本清理服务优化文本（可选）
3. **UDP广播**: 广播最终文本到外部监听器（可选）
4. **语音合成**: 调用 Qwen-Omni API 生成音频
5. **音频后处理**: 应用音量调节、噪声等特效（可选）
6. **时长计算**: 计算音频播放时长
7. **字幕通知**: 通知字幕服务显示文本（可选）
8. **音频播放**: 通过指定设备播放音频
9. **资源清理**: 清理临时文件

## 错误处理

插件包含完善的错误处理机制：

- **API调用失败**: 自动重试和错误日志
- **音频播放失败**: 备用播放方案（系统默认播放器）
- **依赖缺失**: 启动时检查并提示
- **配置错误**: 详细的配置验证和错误提示

## 性能优化

- **异步处理**: 所有 IO 操作都是异步的
- **锁机制**: 防止并发播放冲突
- **内存管理**: 及时清理临时文件和音频数据
- **智能等待**: 根据音频实际时长进行精确等待

## 注意事项

1. 需要有效的阿里云百炼 API 密钥
2. 网络连接必须稳定（需要访问阿里云API）
3. 音频后处理功能需要 pydub 库
4. 建议为调试音频设置足够的存储空间 