# 配置管理模块

负责配置文件解析、Schema 验证、配置版本管理。

## 概述

`src/modules/config/` 模块提供统一的配置管理功能，包括：
- TOML 配置文件解析
- Pydantic Schema 验证
- Provider 配置管理
- 配置版本迁移

## 主要组件

| 文件 | 功能 |
|------|------|
| `service.py` | ConfigService - 配置加载和管理 |
| `config_utils.py` | 配置工具函数 |
| `toml_utils.py` | TOML 文件解析工具 |
| `version_manager.py` | 配置文件版本管理 |
| `schemas/` | Pydantic 配置 Schema |
| `schemas/generator.py` | 配置生成器 - 从 Schema 自动生成 config.toml |

## 核心 API

### ConfigService

```python
from src.modules.config.service import ConfigService

# 获取配置服务实例
config_service = ConfigService()

# 加载配置
await config_service.load_config("config.toml")

# 获取配置值
value = config_service.get("providers.input.enabled_inputs")
```

### Provider Schema

```python
from src.modules.config import get_provider_schema, validate_provider_config

# 获取 Provider 的配置 Schema
schema = get_provider_schema("bili_danmaku")

# 验证 Provider 配置
is_valid = validate_provider_config("bili_danmaku", config_dict)
```

### 列出 Provider

```python
from src.modules.config import list_all_providers

# 列出所有可用的 Provider
providers = list_all_providers()
# 返回: {'input': [...], 'decision': [...], 'output': [...]}
```

## 配置结构

```toml
# 根配置
[app]
name = "Amaidesu"
debug = false

# 输入 Provider
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.input.providers.console]
source = "stdin"

[providers.input.providers.bili_danmaku]
room_id = "123456"

# 决策 Provider
[providers.decision]
active_provider = "maicore"

[providers.decision.providers.maicore]
api_url = "http://localhost:8080"

# 输出 Provider
[providers.output]
enabled_outputs = ["tts", "subtitle"]

[providers.output.providers.tts]
provider = "edge_tts"
```

## 配置 Schema

Provider 配置使用 Pydantic BaseModel 定义 Schema，存放在 `src/modules/config/schemas/` 目录。

### 自定义 Schema

```python
from pydantic import BaseModel, Field
from typing import Optional

class MyProviderConfig(BaseModel):
    """自定义 Provider 配置"""
    enabled: bool = Field(default=True, description="是否启用")
    api_key: str = Field(..., description="API 密钥")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间(秒)")
```

## 版本管理

ConfigService 支持配置文件版本管理：

```python
from src.modules.config.version_manager import ConfigVersionManager

version_manager = ConfigVersionManager()
current_version = version_manager.get_version()
needs_migration = version_manager.needs_migration()
```

---

*最后更新：2026-02-14*
