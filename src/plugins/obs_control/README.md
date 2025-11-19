```markdown
# Amaidesu OBS 控制插件

OBS 控制插件是专为直播场景设计的实时文本推送插件，可将弹幕、聊天消息等文本内容以逐字显示效果推送至 OBS Studio，实现专业级的直播文字上屏体验。

## 功能特点

- **实时文本推送**: 将 WebSocket 消息实时显示在 OBS 文本源中
- **逐字打字机效果**: 支持可配置的逐字显示动画，模拟真人打字效果
- **自动重连机制**: 网络中断后自动重连 OBS，保障直播稳定性
- **多源文本兼容**: 自动识别并提取来自不同插件的消息文本内容
- **插件服务接口**: 提供 `send_to_obs()` 方法供其他插件直接调用
- **测试消息逐字显示**: 连接成功时的测试消息也支持逐字动画效果
- **全中文日志**: 所有日志输出均为中文，便于调试与维护
- **智能容错机制**: 消息解析失败时自动降级为原始文本发送

## 依赖

### 必需依赖
- `obsws-python`: 与 OBS WebSocket 通信的核心库

### 安装依赖

```bash
pip install obsws-python
```

## 配置

### 连接配置

在配置文件中设置 OBS 连接参数：
```toml
[plugins.obs_control]
enabled = true

[plugins.obs_control.obs]
host = "localhost"
port = 4455
password = "your_obs_websocket_password_here"
text_source_name = "text"
```

### 完整配置示例

```toml
[plugins.obs_control]
enabled = true

[plugins.obs_control.obs]
host = "localhost"                    # OBS WebSocket 服务器地址
port = 4455                           # OBS WebSocket 端口
password = "your_secure_password"     # OBS WebSocket 密码（必须设置）
text_source_name = "text"             # OBS 场景中文本源的名称

[plugins.obs_control.behavior]
typewriter_effect = false             # 是否启用逐字显示效果
typewriter_speed = 0.1                # 逐字显示速度（秒/字符）
typewriter_delay = 0.5                # 逐字显示完成后延迟时间（秒）
disable_on_connect_fail = true        # 连接失败时自动禁用插件
send_test_message_on_connect = true   # 连接成功后发送测试消息

[plugins.obs_control.advanced]
connect_timeout = 10                  # 连接超时时间（秒）
reconnect_interval = 5                # 重连间隔（秒）
max_reconnect_attempts = 3            # 最大重连次数（0=无限重连）
```

## 特色功能

### 1. 逐字打字机效果
- 通过 `typewriter_effect = true` 启用逐字显示动画
- 可精细调节 `typewriter_speed` 控制打字节奏（如 0.05 为每秒 20 字）
- `typewriter_delay` 控制完整消息显示后的停留时间
- 测试消息和所有推送消息均统一支持逐字效果

### 2. 插件服务接口
其他插件可通过以下方式直接发送文本到 OBS：

```python
# 获取 OBS 服务
obs_service = self.core.get_service("obs_control")

# 直接发送文本（使用配置的默认效果）
await obs_service.send_to_obs("欢迎来到直播间！")

# 强制启用或禁用逐字效果
await obs_service.send_to_obs("立即显示", typewriter=False)
await obs_service.send_to_obs("逐字显示", typewriter=True)
```

### 3. 智能文本提取
自动从多种消息结构中提取文本内容：
- `message_segment.data`（seglist 类型）
- `raw_message`
- 消息对象本身（若为字符串）

### 4. 自动重连与容错
- 连接断开后自动尝试重连（可配置重试次数与间隔）
- 发送失败时记录详细错误日志
- 文本提取失败时自动降级为原始消息发送

## 与其他服务的集成

### 消息管道集成
本插件可无缝接入 Amaidesu 的消息管道系统，接收来自以下插件的消息：
- 弹幕解析插件
- 聊天室监听插件
- 自定义消息转发插件

### 文本清理服务
插件可与 `text_cleanup` 服务协同工作，优化输入文本：

```python
cleanup_service = self.core.get_service("text_cleanup")
if cleanup_service:
    cleaned_text = await cleanup_service.clean_text(original_text)
    await obs_service.send_to_obs(cleaned_text)
```

## 工作流程

1. **插件加载**: 初始化 OBS 连接参数与逐字显示配置
2. **连接 OBS**: 建立 WebSocket 连接，发送测试消息验证
3. **注册处理器**: 监听 `text` 类型 WebSocket 消息
4. **消息接收**: 接收来自其他插件或管道的消息
5. **文本提取**: 自动解析消息中的文本内容
6. **效果处理**: 根据配置决定是直接显示或逐字动画
7. **推送 OBS**: 调用 OBS API 更新文本源内容
8. **错误处理**: 捕获异常并记录日志，保持系统稳定
9. **资源清理**: 插件关闭时断开连接，释放资源

## 错误处理

插件包含完善的错误处理机制：

- **密码缺失**: 启动时强制提示并禁用插件
- **OBS 未运行**: 捕获连接异常，提示检查 OBS 状态
- **文本源不存在**: 提示检查 OBS 场景中的文本源名称
- **网络中断**: 自动重连，避免插件崩溃
- **消息格式异常**: 安全提取文本，避免程序异常退出

## 性能优化

- **异步非阻塞**: 所有网络与 I/O 操作均为异步
- **零阻塞处理**: 消息处理不阻塞主事件循环
- **内存安全**: 仅保留必要数据，及时释放临时变量
- **轻量级依赖**: 仅依赖一个核心库，无冗余依赖

## 注意事项

1. 需确保 OBS Studio 已启用 WebSocket 服务并设置密码
2. OBS 文本源名称必须与 `text_source_name` 完全一致（区分大小写）
3. 建议将 OBS 文本源设置为“滚动文本”或“动态缩放”以适应长消息
4. 逐字显示效果会略微延迟消息显示（取决于 `typewriter_speed`）
5. 请勿在 OBS 中将文本源设置为“自动调整大小”，以免影响布局
6. 若使用多个插件同时推送，建议启用消息限流管道避免刷屏
```