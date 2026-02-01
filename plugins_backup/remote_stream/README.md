# Remote Stream Plugin

## 简介

Remote Stream 插件通过 WebSocket 协议实现与边缘设备的音视频双向传输。这个插件旨在解决在计算资源有限的边缘设备上运行完整 Amaidesu 项目的问题，允许将音频和视频处理任务移至更强大的服务器上。

## 功能特点

1. **图像按需获取**：仅在需要图像时发送请求，避免不必要的网络传输
2. **音频实时流式传输**：支持麦克风音频上传到服务器和 TTS 音频下发到设备
3. **作为服务注册**：提供 API 供其他插件调用音视频功能
4. **灵活部署模式**：支持服务端模式和客户端模式

## 系统架构

整个系统包括两个部分：

1. **服务器端**：运行完整的 Amaidesu 项目，包括此 Remote Stream 插件
2. **边缘设备端**：运行一个轻量级的客户端应用，负责音视频采集和播放

通信流程：
- 边缘设备采集音频 → WebSocket → 服务器 → ASR/处理
- 服务器 TTS 生成 → WebSocket → 边缘设备播放
- 服务器需要图像时 → 发送请求 → 边缘设备拍摄 → WebSocket → 服务器

## 配置说明

详细配置见 `config-template.toml`。主要配置项包括：

- `server_mode`：`true` 作为服务端，`false` 作为客户端
- `host`/`port`：WebSocket 监听/连接地址和端口
- 音频和图像相关参数

## 依赖项

本插件依赖以下库：
- `websockets`：WebSocket 通信
- `numpy`：音频数据处理
- `Pillow`：图像处理

使用前请确保安装：
```bash
pip install websockets numpy Pillow
```

## 服务接口

本插件注册为 `remote_stream` 服务，提供以下主要方法：

1. **音频回调注册**：
```python
register_audio_callback(event: str, callback: Callable)
```

2. **图像获取**：
```python
async request_image() -> bool
```

3. **TTS 音频发送**：
```python
async send_tts_audio(audio_data: bytes, format_info: Dict[str, Any] = None) -> bool
```

## 边缘设备客户端

边缘设备需要运行一个对应的客户端程序。客户端程序需要实现：

1. 与服务器建立 WebSocket 连接
2. 采集并发送麦克风音频
3. 播放接收到的 TTS 音频
4. 响应图像请求，拍摄并发送图像

具体实现可以根据边缘设备的平台和资源情况选择合适的语言和框架。

## 安全注意事项

1. 通信未加密，不适合在公共网络中使用
2. 在实际部署时，建议添加身份验证机制
3. 如果在公网使用，请配置防火墙并使用 TLS/SSL 加密连接

## 部署建议

1. 确保服务器和边缘设备之间有稳定的网络连接
2. 低延迟场景（如实时对话）建议使用局域网
3. 对于高清图像或高质量音频，请确保有足够的带宽
