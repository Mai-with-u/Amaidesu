# Amaidesu Edge TTS 插件

Edge TTS 插件是 Amaidesu VTuber 项目的核心语音合成组件，使用 Microsoft Edge TTS 引擎将文本消息转换为语音并播放给用户。插件支持与其他插件如文本清理服务和字幕服务的集成。

## 功能特点

- 接收并处理 WebSocket 文本消息
- **Microsoft Edge TTS**: 免费、高质量的语音合成引擎
- 支持多种中文语音角色和输出音频设备选择
- 支持通过 UDP 广播 TTS 内容（用于外部监听）
- 集成文本清理服务（可选）
- 发送播放信息到字幕服务（可选）
- 智能错误处理和资源管理

## 依赖

### 必需依赖

- `edge-tts`: Microsoft Edge TTS 引擎
- `sounddevice`: 音频播放
- `soundfile`: 音频文件处理
- `numpy`: 用于音频数据处理

### 可选服务依赖

- `text_cleanup`: 用于优化 TTS 的文本（由 LLM Text Processor 插件提供）
- `subtitle_service`: 用于显示正在播放的文本（由 Subtitle 插件提供）

### 安装依赖

```bash
# 基础依赖
pip install edge-tts sounddevice soundfile numpy
```

## 消息处理流程

Edge TTS 插件处理流程如下：

1. **消息接收**：监听来自 MaiCore 的所有 WebSocket 消息，过滤出文本类型消息
2. **文本清理**（可选）：通过 `text_cleanup` 服务优化文本内容
3. **UDP 广播**（可选）：将最终文本通过 UDP 广播到外部监听者
4. **语音合成**：使用 Edge TTS 将文本合成为语音并保存为临时文件
5. **播放前处理**：计算音频时长并通知字幕服务
6. **音频播放**：通过 sounddevice 播放音频
7. **资源清理**：播放完成后删除临时文件

## 配置

### 基本配置

```toml
[tts]
# Edge TTS 语音模型
voice = "zh-CN-XiaoxiaoNeural"
# 输出音频设备名称（留空使用默认设备）
output_device_name = ""

[tts.udp_broadcast]
# UDP 广播设置
enable = false
host = "127.0.0.1"
port = 9998
```

### 可用的中文语音

Edge TTS 支持多种中文语音角色：

- `zh-CN-XiaoxiaoNeural` - 晓晓（女声，温柔）
- `zh-CN-YunxiNeural` - 云希（男声）
- `zh-CN-YunjianNeural` - 云健（男声）
- `zh-CN-XiaoyiNeural` - 晓伊（女声）
- `zh-CN-YunyangNeural` - 云扬（男声）
- `zh-CN-XiaochenNeural` - 晓辰（女声）
- `zh-CN-XiaohanNeural` - 晓涵（女声）
- `zh-CN-XiaomengNeural` - 晓梦（女声）
- `zh-CN-XiaomoNeural` - 晓墨（女声）
- `zh-CN-XiaoqiuNeural` - 晓秋（女声）
- `zh-CN-XiaoruiNeural` - 晓睿（女声）
- `zh-CN-XiaoshuangNeural` - 晓双（女声，儿童）
- `zh-CN-XiaoxuanNeural` - 晓萱（女声）
- `zh-CN-XiaoyanNeural` - 晓颜（女声）
- `zh-CN-XiaoyouNeural` - 晓悠（女声，儿童）
- `zh-CN-XiaozhenNeural` - 晓甄（女声）

## 核心代码解析

### 1. 消息处理函数

```python
async def handle_maicore_message(self, message: MessageBase):
    """处理从 MaiCore 收到的消息，如果是文本类型，则进行 TTS 处理"""
    if message.message_segment and message.message_segment.type == "text":
        original_text = message.message_segment.data
        if not isinstance(original_text, str) or not original_text.strip():
            return

        # 文本清理（可选）
        cleanup_service = self.core.get_service("text_cleanup")
        if cleanup_service:
            cleaned = await cleanup_service.clean_text(original_text)
            if cleaned:
                final_text = cleaned

        # UDP 广播（可选）
        if self.udp_enabled:
            self._broadcast_text(final_text)

        # 执行 Edge TTS
        await self._speak(final_text)
```

### 2. Edge TTS 执行函数

```python
async def _speak(self, text: str):
    """执行 Edge TTS 合成和播放，并通知 Subtitle Service"""
    async with self.tts_lock:
        try:
            # Edge TTS 合成
            communicate = edge_tts.Communicate(text, self.voice)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            
            # 读取音频并计算时长
            audio_data, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")
            duration_seconds = len(audio_data) / samplerate
            
            # 通知字幕服务
            subtitle_service = self.core.get_service("subtitle_service")
            if subtitle_service:
                asyncio.create_task(subtitle_service.record_speech(text, duration_seconds))
            
            # 播放音频
            sd.play(audio_data, samplerate=samplerate, device=self.output_device_index)
            await asyncio.sleep(duration_seconds + 0.3)  # 等待播放完成
            
        finally:
            # 清理临时文件
            if tmp_filename and os.path.exists(tmp_filename):
                os.remove(tmp_filename)
```

### 3. UDP 广播机制

```python
def _broadcast_text(self, text: str):
    """通过 UDP 发送文本"""
    if self.udp_socket and self.udp_dest:
        try:
            message_bytes = text.encode("utf-8")
            self.udp_socket.sendto(message_bytes, self.udp_dest)
        except Exception as e:
            self.logger.warning(f"UDP广播失败: {e}")
```

## 与其他服务的集成

### 文本清理服务

Edge TTS 插件可以与文本清理服务集成，自动优化输入文本以获得更好的语音效果：

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

## 错误处理

插件包含完善的错误处理机制：

- **播放失败**: 备用播放方案（使用系统默认播放器）
- **依赖缺失**: 启动时检查并提示用户安装
- **设备问题**: 自动回退到默认音频设备
- **网络问题**: Edge TTS 在线合成失败时的错误处理

## 性能优化

- **异步处理**: 所有IO操作都是异步的，不阻塞主线程
- **资源管理**: 自动清理临时文件，防止磁盘空间浪费
- **锁机制**: 防止并发播放导致的音频冲突
- **智能等待**: 根据音频实际时长进行精确等待

## 优势

1. **免费使用**: Edge TTS 完全免费，无需 API 密钥
2. **高质量**: Microsoft 提供的高质量语音合成
3. **低延迟**: 本地处理，响应速度快
4. **多语音**: 支持丰富的中文语音角色
5. **稳定可靠**: 基于成熟的 Edge TTS 技术
6. **易于配置**: 简单的配置选项，开箱即用

## 注意事项

1. 需要网络连接（Edge TTS 是在线服务）
2. 播放质量依赖于网络状况
3. 某些特殊字符可能影响合成效果
4. 建议配置合适的音频输出设备以获得最佳体验 