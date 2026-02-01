# Mock Danmaku Plugin (模拟弹幕插件)

## 概述

`MockDanmakuPlugin` 是一个用于 Amaidesu 的插件，旨在模拟实时弹幕消息的接收。它从一个指定的 JSONL 文件中逐行读取预先录制或定义的消息，并按照设定的时间间隔将这些消息发送到 Amaidesu 核心，模拟真实的弹幕流。

这个插件主要用于调试目的，特别是在无法连接到真实的直播弹幕源（如 Bilibili 直播）或需要稳定、可重复的消息输入进行测试时非常有用。

## 功能

*   **读取 JSONL 文件**: 从插件目录下的 `data/` 子目录中读取指定的 `.jsonl` 文件。文件的每一行应包含一个符合 `maim_message.MessageBase` 结构（的字典表示）的 JSON 对象。
*   **模拟发送**: 按配置的时间间隔，依次将从文件中读取的消息解析为 `MessageBase` 对象，并发送给 `AmaidesuCore`。
*   **可配置速率**: 可以通过配置文件调整消息发送的速率（间隔时间）。
*   **循环播放**: 可选配置是否在读取到文件末尾后从头开始循环播放。
*   **自动/手动启动**: 可配置插件加载后是否立即开始发送模拟消息。

## 配置 (`config.toml`)

插件的配置位于其目录下的 `config.toml` 文件中，包含以下选项：

```toml
[mock_danmaku]
# 是否启用此插件
enabled = true

# JSONL 消息日志的文件名。
# 文件应放置在插件目录下的 "data/" 子目录中。
# 例如，如果这里设置为 "my_messages.jsonl"，
# 插件将查找 "src/plugins/mock_danmaku/data/my_messages.jsonl"
log_file_path = "msg_default.jsonl"

# 发送两条消息之间的间隔时间（秒）。
# 最小值建议为 0.1 以避免过于频繁的发送。
send_interval = 1.0

# 当读取到文件末尾时，是否从头开始重新播放。
loop_playback = true

# 是否在插件加载完成后立即开始发送消息。
# 如果设置为 false，则需要通过其他方式（例如命令）触发。
start_immediately = true
```

*   `enabled`: (布尔值) `true` 启用插件，`false` 禁用。
*   `log_file_path`: (字符串) **仅包含文件名**，指定位于 `src/plugins/mock_danmaku/data/` 目录下的 JSONL 文件名。
*   `send_interval`: (浮点数) 发送两条模拟消息之间的时间间隔（秒）。
*   `loop_playback`: (布尔值) `true` 表示文件播放完毕后从头开始，`false` 表示播放完毕后停止。
*   `start_immediately`: (布尔值) `true` 表示插件设置完成后自动开始发送消息，`false` 表示需要手动触发（例如通过未来可能添加的命令）。

## 数据文件 (`*.jsonl`)

*   数据文件必须放置在插件目录下的 `data/` 子目录中 (即 `src/plugins/mock_danmaku/data/`)。
*   文件名必须与 `config.toml` 中的 `log_file_path` 配置相匹配。
*   文件格式为 JSON Lines (JSONL)，意味着文件的每一行都是一个独立的、有效的 JSON 对象。
*   每一行的 JSON 对象都应该能够被 `maim_message.MessageBase.from_dict()` 方法成功解析。这意味着 JSON 对象的结构需要匹配 `MessageBase` 及其嵌套类的字段（如 `message_info`, `message_segment`, `user_info` 等）。

一个简单的示例行可能如下所示（具体结构取决于你的 `MessageBase` 定义）：
```json
{"message_info": {"platform": "mock", "message_id": "mock_123", "time": 1678886400, "user_info": {"platform": "mock", "user_id": "user_001", "user_nickname": "模拟用户1"}}, "message_segment": {"type": "text", "data": "这是一条模拟弹幕！"}, "raw_message": "这是一条模拟弹幕！"}
```

## 使用方法

1.  **放置数据文件**: 将你的 `.jsonl` 数据文件放入 `src/plugins/mock_danmaku/data/` 目录下。
2.  **配置插件**: 编辑 `src/plugins/mock_danmaku/config.toml` 文件，确保 `enabled = true`，并设置 `log_file_path` 为你的数据文件名，调整其他参数（如 `send_interval`）根据需要。
3.  **启用插件 (全局)**: 确保在 Amaidesu 的主配置文件中启用了该插件的加载（通常是通过类似 `enable_mock_danmaku = true` 的设置，具体取决于你的 `PluginManager` 实现）。
4.  **启动 Amaidesu**: 正常启动 Amaidesu。如果 `start_immediately = true`，插件应该会自动开始发送模拟弹幕。

## 注意事项

*   如果指定的日志文件不存在，插件在初始化时会记录警告，并在尝试加载消息时失败，导致无法发送任何模拟弹幕。
*   如果 JSONL 文件中的某一行无法被正确解析为 `MessageBase` 对象，该行将被跳过，并记录错误日志。 