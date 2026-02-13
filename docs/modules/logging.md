# 日志系统模块

提供统一的日志配置和获取功能。

## 概述

`src/modules/logging/` 模块提供统一的日志系统：
- 基于 Python logging 的封装
- 配置驱动的日志级别
- 结构化日志支持

## 主要组件

| 文件 | 功能 |
|------|------|
| `logger.py` | 日志工具函数 |

## 核心 API

### get_logger

```python
from src.modules.logging import get_logger

# 获取日志器
logger = get_logger("MyClassName")

# 使用日志
logger.info("信息日志")
logger.debug("调试日志")
logger.warning("警告日志")
logger.error("错误日志", exc_info=True)
```

### 配置日志

```python
from src.modules.logging import configure_from_config

# 从配置初始化日志系统
configure_from_config(logging_config)
```

## 日志级别

| 级别 | 说明 |
|------|------|
| DEBUG | 调试信息 |
| INFO | 一般信息 |
| WARNING | 警告 |
| ERROR | 错误 |
| CRITICAL | 严重错误 |

## 使用示例

### 在 Provider 中使用

```python
from src.modules.logging import get_logger

class MyProvider(InputProvider):
    def __init__(self, config, dependencies):
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info("Provider 初始化完成")

    async def _process(self):
        self.logger.debug("处理数据...")
        try:
            result = await self._do_process()
            self.logger.info("处理成功")
        except Exception as e:
            self.logger.error(f"处理失败: {e}", exc_info=True)
```

### 过滤日志

使用 `--filter` 参数过滤日志输出：

```bash
# 只显示特定模块的日志
uv run python main.py --filter MyProvider

# 显示多个模块
uv run python main.py --filter MyProvider,AnotherProvider
```

### 结构化日志

```python
import logging

# 使用 extra 参数添加结构化数据
logger.info(
    "用户消息处理完成",
    extra={
        "session_id": "default",
        "message_length": 100,
        "processing_time_ms": 50
    }
)
```

## 日志配置

在配置文件中设置日志：

```toml
[logging]
level = "INFO"
format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

[logging.file]
enabled = true
path = "./logs/amaidesu.log"
max_bytes = 10485760  # 10MB
backup_count = 5
```

## 最佳实践

1. **使用类名作为 logger 名称**：
   ```python
   logger = get_logger(self.__class__.__name__)
   ```

2. **记录异常时使用 exc_info=True**：
   ```python
   logger.error("错误发生", exc_info=True)
   ```

3. **使用结构化日志记录关键信息**：
   ```python
   logger.info("操作完成", extra={"key": "value"})
   ```

---

*最后更新：2026-02-14*
