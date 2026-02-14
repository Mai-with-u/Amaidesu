# 对话上下文管理模块

负责对话历史存储、多会话管理、上下文管理。

## 概述

`src/modules/context/` 模块提供对话上下文管理功能：
- 对话历史内存存储
- 多会话隔离（通过 session_id）
- 为 DecisionProvider 提供上下文

## 主要组件

| 文件 | 功能 |
|------|------|
| `service.py` | ContextService - 核心上下文服务 |
| `models.py` | 数据模型定义 |
| `config.py` | 配置模型 |
| `manager.py` | ContextManager - 管理器 |
| `storage/` | 存储后端实现 |

## 核心 API

### ContextService

```python
from src.modules.context import ContextService, MessageRole

# 创建上下文服务
context_service = ContextService(config)

# 添加消息
await context_service.add_message(
    session_id="default",
    role=MessageRole.USER,
    content="你好"
)

# 获取对话历史
messages = await context_service.get_history(
    session_id="default",
    limit=10
)

# 清空会话
await context_service.clear_session("default")
```

### 数据模型

```python
from src.modules.context import ConversationMessage, SessionInfo, MessageRole

# 消息角色
MessageRole.USER      # 用户
MessageRole.ASSISTANT # AI 助手
MessageRole.SYSTEM    # 系统

# 会话信息
session = SessionInfo(
    session_id="default",
    created_at=datetime.now(),
    message_count=10,
    metadata={}
)
```

### 配置

```python
from src.modules.context import ContextServiceConfig, StorageType

config = ContextServiceConfig(
    max_history_length=100,      # 最大历史长度
    max_sessions=10,             # 最大会话数
    storage_type=StorageType.MEMORY,  # 存储类型
    session_timeout=3600,        # 会话超时时间(秒)
)
```

## 存储后端

支持多种存储后端：

| 类型 | 说明 |
|------|------|
| `MEMORY` | 内存存储（默认） |
| `FILE` | 文件存储 |
| `DATABASE` | 数据库存储 |

### 内存存储

```python
config = ContextServiceConfig(storage_type=StorageType.MEMORY)
```

### 文件存储

```python
config = ContextServiceConfig(
    storage_type=StorageType.FILE,
    storage_path="./data/context/"
)
```

## 多会话管理

```python
# 创建多个会话
await context_service.add_message("session_1", MessageRole.USER, "消息1")
await context_service.add_message("session_2", MessageRole.USER, "消息2")

# 获取特定会话的历史
history_1 = await context_service.get_history("session_1")
history_2 = await context_service.get_history("session_2")

# 获取所有会话
sessions = await context_service.list_sessions()

# 删除会话
await context_service.delete_session("session_1")
```

## 使用示例

### 在 DecisionProvider 中使用

```python
class MyDecisionProvider(DecisionProvider):
    def __init__(self, config, dependencies):
        self.context_service = dependencies.get("context_service")

    async def _process_message(self, message: NormalizedMessage) -> Optional[Intent]:
        # 获取对话历史
        history = await self.context_service.get_history(
            message.session_id,
            limit=5
        )

        # 构建上下文
        context = "\n".join([
            f"{m.role.value}: {m.content}"
            for m in history
        ])

        # 添加当前消息
        await self.context_service.add_message(
            message.session_id,
            MessageRole.USER,
            message.content
        )

        return intent
```

---

*最后更新：2026-02-14*
