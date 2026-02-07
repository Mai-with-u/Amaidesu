# 消息日志管道 (MessageLoggerPipeline)

## 功能简介

消息日志管道用于将所有消息记录到JSON文件中，便于后续分析和查看。主要特点：

1. 将消息保存为JSONL格式（每行一个JSON对象）
2. 根据群组ID自动分类保存到不同文件
3. 记录消息的完整信息，包括用户、时间、内容等
4. 支持定期轮转日志文件
5. 提供工具方法用于读取和分析日志文件

## 为什么使用JSONL格式

JSONL（JSON Lines）格式有以下优势：

1. **增量写入** - 可以简单地追加新记录，无需修改现有内容
2. **流式处理** - 可以逐行读取解析，支持处理超大文件
3. **易于解析** - 每行是完整独立的JSON对象，处理简单
4. **人类可读** - 保持了JSON的可读性，便于调试和检查
5. **无特殊依赖** - 使用标准JSON库即可处理，不需要特殊库

## 配置说明

在`config.toml`中启用此管道并设置优先级：

```toml
[pipelines]
# 设置较低的优先级，让其他处理管道先处理消息
message_logger = 900
```

### 管道专用配置 (src/domains/input/pipelines/message_logger/config.toml)

```toml
[message_logger]
# 是否启用此管道
enabled = true

# 日志文件存储目录
logs_dir = "logs/messages"

# 日志文件名前缀
file_prefix = "messages_"

# 日志文件扩展名
file_extension = ".jsonl"

# 当没有群组ID时使用的默认ID
default_group_id = "default"

# 是否包含原始消息内容
include_raw_message = true

# 日志文件轮转间隔（秒），默认86400（一天）
rotation_interval = 86400

# 缓冲区刷新间隔（秒），默认10秒
flush_interval = 10
```

## 日志文件格式

每条消息被直接序列化为JSON对象，包含MessageBase的完整数据结构。典型的记录可能包含以下字段：

```json
{
  "message_info": {
    "platform": "platform_name",
    "message_id": "unique_message_id",
    "time": 1687548635.123,
    "user_info": {
      "platform": "platform_name",
      "user_id": "12345",
      "user_nickname": "用户昵称",
      "user_cardname": "用户名片"
    },
    "group_info": {
      "platform": "platform_name",
      "group_id": "67890",
      "group_name": "群组名称"
    },
    "format_info": {
      "content_format": ["text"],
      "accept_format": ["text"]
    },
    "additional_config": {}
  },
  "message_segment": {
    "type": "text",
    "data": "消息内容"
  },
  "raw_message": "原始消息"
}
```

## 日志文件命名

日志文件按照群组ID和日期命名，格式为：`{file_prefix}{group_id}{file_extension}`

例如：
- `messages_default.jsonl` - 默认群组或没有群组ID的消息
- `messages_12345.jsonl` - 群组ID为12345的消息

如果启用了日志轮转，文件名会包含日期：
- `messages_default_20230623.jsonl`
- `messages_12345_20230623.jsonl`

## 工具方法

消息日志管道还提供了以下工具方法，用于读取和分析日志文件：

### 读取整个日志文件

```python
from src.pipelines.message_logger import MessageLoggerPipeline

# 读取所有消息
messages = MessageLoggerPipeline.read_jsonl_file("logs/messages/messages_12345.jsonl")
print(f"共读取 {len(messages)} 条消息")
```

### 流式读取大型日志文件

```python
# 流式处理大文件
for msg in MessageLoggerPipeline.stream_jsonl_file("logs/messages/messages_12345.jsonl"):
    print(f"时间: {msg['message_info']['time']}, 用户: {msg['message_info']['user_info']['user_nickname']}")
```

### 提取特定用户的消息

```python
# 提取用户消息
user_messages = MessageLoggerPipeline.extract_messages_by_user("logs/messages/messages_12345.jsonl", "user123")
print(f"用户发送了 {len(user_messages)} 条消息")
```

## 注意事项

1. 确保`logs_dir`指定的目录存在或有权限创建
2. 可使用`rotation_interval`参数启用日志文件轮转，避免单个文件过大
3. `flush_interval`参数控制日志缓冲区刷新频率，较小的值可确保消息及时写入磁盘但可能影响性能
4. 文件会在程序退出时自动关闭，但如果非正常退出可能导致最后几条记录丢失 