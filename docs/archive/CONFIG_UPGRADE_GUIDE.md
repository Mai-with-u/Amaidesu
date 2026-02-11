# 配置系统升级指南

## 概述

本指南帮助你从旧的配置系统迁移到新的Schema-as-Template配置系统。

**主要变化**：
- ❌ 移除：`config-defaults.toml`文件
- ✅ 新增：Pydantic Schema定义
- ✅ 改进：配置自动生成机制
- ✅ 改进：三级配置合并体系

## 升级前检查

### 确认当前配置系统版本

检查你的项目中是否存在以下文件：

```bash
# 旧系统（需要迁移）
find src/layers -name "config-defaults.toml"  # 如果有输出，说明使用旧系统

# 新系统（无需迁移）
ls src/core/config/schemas/  # 如果有schema文件，说明已部分迁移
```

### 备份现有配置

```bash
# 备份主配置文件
cp config.toml config.toml.backup

# 备份所有Provider配置
find src/layers -name "config*.toml" -exec cp {} {}.backup \;
```

## 迁移步骤

### 步骤1：理解新的配置结构

阅读[CONFIG_FINAL_DESIGN.md](CONFIG_FINAL_DESIGN.md)了解新架构。

**核心概念**：
- 配置的"事实来源"是Pydantic Schema类，不是配置文件
- 配置文件可以从Schema自动生成
- 三级合并：Schema默认值 → 主配置覆盖 → Provider本地配置

### 步骤2：创建Provider Schema

为你的Provider创建Pydantic Schema类。

**示例：迁移BiliDanmakuProvider**

旧配置（`config-defaults.toml`）：
```toml
[bili_danmaku]
room_id = 0
poll_interval = 3
message_config = {}
```

新Schema（`src/core/config/schemas/input_providers.py`）：
```python
from pydantic import BaseModel, Field
from typing import Dict

class BiliDanmakuProviderConfig(BaseProviderConfig):
    """Bilibili弹幕输入Provider配置"""

    type: Literal["bili_danmaku"] = "bili_danmaku"
    room_id: int = Field(default=0, description="直播间ID", gt=0)
    poll_interval: int = Field(default=3, description="轮询间隔（秒）", ge=1)
    message_config: Dict = Field(default_factory=dict, description="消息配置")
```

**字段映射规则**：
| TOML类型 | Pydantic类型 | Field定义 |
|---------|-------------|-----------|
| 字符串 | `str` | `Field(default="value")` |
| 整数 | `int` | `Field(default=0, ge=0)` |
| 浮点数 | `float` | `Field(default=1.0, ge=0.0)` |
| 布尔值 | `bool` | `Field(default=True)` |
| 数组 | `List[T]` | `Field(default_factory=list)` |
| 字典 | `Dict` | `Field(default_factory=dict)` |
| 必填项 | - | `Field(...)` |

### 步骤3：注册Schema

将新Schema注册到全局注册表：

```python
# src/core/config/schemas/__init__.py

from .input_providers import BiliDanmakuProviderConfig

PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    # ... 其他Provider
    "bili_danmaku": BiliDanmakuProviderConfig,
}
```

### 步骤4：更新Provider代码

修改Provider的`__init__`方法，使用新的配置加载方式：

**旧代码**：
```python
class BiliDanmakuProvider(InputProvider):
    def __init__(self, config: dict):
        self.room_id = config.get("room_id", 0)
        self.poll_interval = config.get("poll_interval", 3)
        # 没有验证
```

**新代码**：
```python
from src.core.config_service import ConfigService
from src.core.config.schemas import BiliDanmakuProviderConfig
from pydantic import ValidationError

class BiliDanmakuProvider(InputProvider):
    def __init__(self, config: dict, config_service: ConfigService):
        # 使用ConfigService加载并验证配置
        validated_config = config_service.get_provider_config_with_defaults(
            provider_name="bili_danmaku",
            provider_layer="input",
            schema_class=BiliDanmakuProviderConfig
        )

        self.room_id = validated_config["room_id"]
        self.poll_interval = validated_config["poll_interval"]
        self.message_config = validated_config["message_config"]
```

### 步骤5：删除旧的config-defaults.toml

```bash
# 删除所有config-defaults.toml文件
find src/layers -name "config-defaults.toml" -delete
```

### 步骤6：生成新的配置文件

```bash
# 运行程序，配置文件会自动生成
python main.py

# 或手动生成
python -c "
from src.core.config import ensure_provider_config
from src.core.config.schemas import BiliDanmakuProviderConfig

ensure_provider_config(
    provider_name='bili_danmaku',
    provider_layer='input',
    schema_class=BiliDanmakuProviderConfig
)
"
```

### 步骤7：验证配置

运行程序并检查日志，确认配置加载成功：

```
[INFO] ConfigService: 从Schema获取默认值: bili_danmaku
[INFO] ConfigService: 配置验证通过: bili_danmaku
```

## 常见迁移场景

### 场景1：简单的Provider配置

**旧配置**：
```toml
[my_provider]
host = "localhost"
port = 8080
enabled = true
```

**新Schema**：
```python
class MyProviderConfig(BaseProviderConfig):
    type: Literal["my_provider"] = "my_provider"
    host: str = Field(default="localhost", description="服务器地址")
    port: int = Field(default=8080, description="端口", ge=1, le=65535)
    enabled: bool = Field(default=True, description="是否启用")
```

### 场景2：带嵌套结构的配置

**旧配置**：
```toml
[my_provider]
name = "test"

[my_provider.server]
host = "localhost"
port = 8080

[my_provider.auth]
username = "admin"
password = "secret"
```

**新Schema**：
```python
class ServerConfig(BaseModel):
    """服务器配置"""
    host: str = Field(default="localhost", description="服务器地址")
    port: int = Field(default=8080, description="端口", ge=1, le=65535)

class AuthConfig(BaseModel):
    """认证配置"""
    username: str = Field(default="admin", description="用户名")
    password: str = Field(default="secret", description="密码")

class MyProviderConfig(BaseProviderConfig):
    type: Literal["my_provider"] = "my_provider"
    name: str = Field(default="test", description="Provider名称")
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
```

### 场景3：带验证规则的配置

**旧配置**：
```toml
[my_provider]
url = "https://api.example.com"
timeout = 30
retry_count = 3
```

**新Schema**：
```python
from pydantic import field_validator, HttpUrl

class MyProviderConfig(BaseProviderConfig):
    type: Literal["my_provider"] = "my_provider"
    url: HttpUrl = Field(default="https://api.example.com", description="API URL")
    timeout: int = Field(default=30, description="超时时间（秒）", ge=1, le=300)
    retry_count: int = Field(default=3, description="重试次数", ge=0, le=10)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: HttpUrl) -> HttpUrl:
        """验证URL必须是HTTPS"""
        if v.scheme != "https":
            raise ValueError("URL必须使用HTTPS协议")
        return v
```

### 场景4：带枚举的配置

**旧配置**：
```toml
[my_provider]
mode = "fast"
log_level = "INFO"
```

**新Schema**：
```python
from typing import Literal

class MyProviderConfig(BaseProviderConfig):
    type: Literal["my_provider"] = "my_provider"
    mode: Literal["fast", "normal", "slow"] = Field(
        default="normal",
        description="运行模式"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="日志级别"
    )
```

## 配置覆盖迁移

### 主配置覆盖

旧系统和新系统都支持主配置覆盖，但格式略有不同。

**旧格式（已废弃）**：
```toml
# config.toml
[providers.input.inputs.bili_danmaku]
room_id = "123456"
poll_interval = 5
```

**新格式**：
```toml
# config.toml
[providers.input.overrides]
bili_danmaku.room_id = "123456"
bili_danmaku.poll_interval = 5
```

### Provider本地配置

Provider本地配置（`src/domains/*/providers/{name}/config.toml`）格式保持不变：

```toml
# src/domains/input/providers/bili_danmaku/config.toml
[bili_danmaku]
room_id = 123456
poll_interval = 5
message_config = {}
```

## 验证迁移结果

### 1. 检查配置加载

运行程序并检查日志：

```bash
python main.py --debug
```

查找关键日志：
```
[INFO] ConfigService: 从Schema获取默认值: bili_danmaku
[INFO] ConfigService: 配置验证通过: bili_danmaku
[INFO] BiliDanmakuProvider: Provider初始化成功
```

### 2. 测试配置验证

尝试修改配置为非法值，验证Schema是否正确工作：

```python
# 测试脚本
from src.core.config_service import ConfigService
from src.core.config.schemas import BiliDanmakuProviderConfig
from pydantic import ValidationError

config_service = ConfigService(base_dir=".")
config_service.initialize()

# 测试合法配置
try:
    config = config_service.get_provider_config_with_defaults(
        "bili_danmaku",
        "input",
        BiliDanmakuProviderConfig
    )
    print("✅ 合法配置加载成功:", config)
except ValidationError as e:
    print("❌ 配置验证失败:", e)

# 测试非法配置（room_id = 0，但schema要求 > 0）
# 应该触发ValidationError
```

### 3. 运行现有测试

```bash
# 运行所有测试
uv run pytest tests/

# 运行Provider相关测试
uv run pytest tests/layers/
```

## 回滚计划

如果迁移出现问题，可以按以下步骤回滚：

1. **恢复备份配置**：
   ```bash
   cp config.toml.backup config.toml
   find src/layers -name "*.toml.backup" -exec sh -c 'cp "$1" "${1%.backup}"' _ {} \;
   ```

2. **恢复旧代码**：
   ```bash
   git checkout HEAD -- src/domains/*/providers/
   ```

3. **重新创建config-defaults.toml**（如果需要）：
   从Schema手动生成或从备份恢复

## 常见问题

### Q1：迁移后配置文件中的注释消失了

**A**：这是预期的。新系统会从Schema的`Field.description`生成注释。如果需要注释：

1. 在Schema中添加`description`参数
2. 重新生成配置文件：`ensure_provider_config(force_regenerate=True)`

### Q2：某些Provider没有Schema定义

**A**：你可以：

1. **创建Schema**（推荐）：定义Pydantic Schema并注册
2. **使用旧配置**：暂时保留`config-defaults.toml`，但优先级低于主配置覆盖
3. **跳过验证**：不传`schema_class`参数，但会失去类型安全

### Q3：配置验证失败，但我确定配置是正确的

**A**：检查：

1. **Schema定义是否正确**：字段类型、默认值、验证规则
2. **配置文件格式**：TOML语法是否正确
3. **类型转换**：TOML中的数字可能是整数或浮点数

使用`--debug`模式查看详细错误信息：
```bash
python main.py --debug
```

### Q4：如何在开发环境和生产环境使用不同配置？

**A**：使用主配置覆盖：

```toml
# config.toml（开发环境）
[providers.input.overrides]
bili_danmaku.room_id = "123456"  # 测试直播间
```

```toml
# config.prod.toml（生产环境）
[providers.input.overrides]
bili_danmaku.room_id = "789012"  # 生产直播间
```

启动时指定配置文件：
```bash
python main.py --config config.prod.toml
```

### Q5：如何迁移带有复杂默认值的配置？

**A**：使用`Field(default_factory=...)`：

```python
class MyProviderConfig(BaseProviderConfig):
    # 默认值为空列表
    items: List[str] = Field(default_factory=list, description="项目列表")

    # 默认值为复杂字典
    config: Dict = Field(
        default_factory=lambda: {
            "timeout": 30,
            "retry": 3
        },
        description="配置字典"
    )
```

## 迁移检查清单

完成以下检查以确保迁移成功：

- [ ] 所有`config-defaults.toml`文件已删除
- [ ] 所有Provider都有对应的Schema定义
- [ ] 所有Schema已注册到`PROVIDER_SCHEMA_REGISTRY`
- [ ] Provider代码已更新为使用`get_provider_config_with_defaults()`
- [ ] 主配置文件（`config.toml`）已更新为新格式
- [ ] 配置文件自动生成机制工作正常
- [ ] 配置验证功能工作正常
- [ ] 所有测试通过
- [ ] 日志中无配置相关错误或警告

## 后续优化

迁移完成后，你可以考虑以下优化：

1. **添加更多验证规则**：在Schema中添加`field_validator`
2. **改进配置文档**：完善Schema的docstring和Field description
3. **配置预加载**：在启动时预加载所有Provider配置，提前发现错误
4. **配置迁移工具**：编写工具自动检测和迁移旧配置
5. **配置版本管理**：在Schema中添加版本号，支持配置迁移

## 获取帮助

如果在迁移过程中遇到问题：

1. **查看日志**：使用`--debug`模式运行，查看详细错误信息
2. **检查文档**：阅读[CONFIG_FINAL_DESIGN.md](CONFIG_FINAL_DESIGN.md)
3. **查看示例**：参考`src/core/config/schemas/`中的现有Schema
4. **运行测试**：使用`pytest`验证配置加载

## 相关文档

- [CONFIG_FINAL_DESIGN.md](CONFIG_FINAL_DESIGN.md) - 新配置系统设计
- [CONFIG_GENERATOR.md](CONFIG_GENERATOR.md) - 配置生成器使用指南
- [CLAUDE.md](../CLAUDE.md) - 项目配置说明
- [refactor/design/overview.md](../refactor/design/overview.md) - 3域架构设计

## 总结

Schema-as-Template配置系统提供了更安全、更自动化的配置管理方式。通过遵循本指南，你可以平滑地迁移到新系统，并获得：

- ✅ 类型安全的配置验证
- ✅ 自动化的配置文件生成
- ✅ 单一事实来源（Schema）
- ✅ 更好的IDE支持和代码提示

祝迁移顺利！
