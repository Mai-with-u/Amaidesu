# 配置系统最终设计 - Schema作为模板

## 概述

Amaidesu采用**Schema-as-Template（Schema作为模板）**配置系统，通过Pydantic Schema类定义Provider的配置结构，Schema既是配置验证器，也是配置文件生成器。

**核心理念**：配置的"事实来源"（Source of Truth）是代码中的Pydantic Schema类，而非独立的配置文件。

## 架构演进

### 旧架构问题（已废弃）

之前的项目尝试使用`config-defaults.toml`作为Provider默认配置：

```
❌ 旧架构：
Provider目录/
├── provider.py
├── config-defaults.toml  # 手动维护，容易与代码不同步
└── config.toml           # 用户自定义配置
```

**问题**：
1. **重复维护**：Schema类和config-defaults.toml需要同时维护
2. **同步困难**：修改Schema后，配置文件可能未更新
3. **验证分离**：配置验证需要另外定义Schema
4. **类型不安全**：TOML是文本格式，没有类型检查

### 新架构：Schema-as-Template

```
✅ 新架构：
Provider目录/
├── provider.py
├── schema.py              # Pydantic Schema（唯一事实来源）
└── config.toml            # 用户自定义配置（可选，从Schema自动生成）
```

**优势**：
1. **单一事实来源**：Schema类定义结构、默认值、验证规则
2. **自动生成**：配置文件可从Schema自动生成
3. **类型安全**：Pydantic提供编译时和运行时类型检查
4. **文档一体**：Schema的docstring和Field description即为配置文档

## 三级配置合并体系

配置加载时的三级合并顺序（后者覆盖前者）：

```
1. Schema默认值（最低优先级）
   ↓ 被2覆盖
2. 主配置覆盖 (config.toml中的[providers.*.overrides])
   ↓ 被3覆盖
3. Provider本地配置 (src/layers/*/providers/{name}/config.toml)
   （最高优先级）
```

### 配置来源详解

| 优先级 | 配置来源 | 位置 | 用途 | 示例 |
|--------|---------|------|------|------|
| 1（最低） | Schema默认值 | `src/core/config/schemas/*.py` | 定义默认配置结构 | `room_id: int = Field(default=0)` |
| 2（中） | 主配置覆盖 | `config.toml` → `[providers.*.overrides]` | 全局配置覆盖 | `bili_danmaku.room_id = "123456"` |
| 3（最高） | Provider本地配置 | `src/layers/*/providers/{name}/config.toml` | Provider特定配置 | 本地开发配置 |

## 核心组件

### 1. Schema定义（`src/core/config/schemas/`）

每个Provider都有对应的Pydantic Schema类：

```python
# src/core/config/schemas/input_providers.py
from pydantic import BaseModel, Field

class BiliDanmakuProviderConfig(BaseProviderConfig):
    """Bilibili弹幕输入Provider配置"""

    type: Literal["bili_danmaku"] = "bili_danmaku"
    room_id: int = Field(..., description="直播间ID", gt=0)
    poll_interval: int = Field(default=3, description="轮询间隔（秒）", ge=1)
    message_config: dict = Field(default_factory=dict, description="消息配置")
```

**Schema特性**：
- `Field(...)` 表示必填项
- `Field(default=...)` 定义默认值
- `Field(description=...)` 提供配置说明（用于生成注释）
- `Field(ge=1, gt=0)` 等提供验证规则

### 2. Schema注册表（`PROVIDER_SCHEMA_REGISTRY`）

全局注册表将Provider名称映射到Schema类：

```python
# src/core/config/schemas/__init__.py
PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    "console_input": ConsoleInputProviderConfig,
    "bili_danmaku": BiliDanmakuProviderConfig,
    "tts": TTSProviderConfig,
    # ... 更多Provider
}
```

### 3. 配置生成器（`src/core/config/generator.py`）

从Schema自动生成TOML配置文件：

```python
from src.core.config import generate_toml

# 生成TOML内容
toml_content = generate_toml(
    schema_class=BiliDanmakuProviderConfig,
    section_name="bili_danmaku",
    include_comments=True
)

# 输出：
# [bili_danmaku]
# # 直播间ID
# room_id = 0
# # 轮询间隔（秒）
# poll_interval = 3
```

### 4. 配置服务（`ConfigService`）

统一的配置加载和合并服务：

```python
from src.core.config_service import ConfigService

config_service = ConfigService(base_dir="/path/to/project")
config_service.initialize()

# 获取Provider配置（三级合并）
config = config_service.get_provider_config_with_defaults(
    provider_name="bili_danmaku",
    provider_layer="input",
    schema_class=BiliDanmakuProviderConfig  # 可选，自动查找
)
```

## 配置文件自动生成

### 自动生成机制

当Provider本地配置（`config.toml`）不存在时，系统会：

1. 从`PROVIDER_SCHEMA_REGISTRY`查找对应的Schema类
2. 从Schema生成`config.toml`文件（作为模板）
3. 返回空字典，让主配置覆盖优先生效

**关键设计**：生成的配置文件仅作为模板，不直接加载。这样用户可以在主配置（`config.toml`）中覆盖参数，而无需修改Provider目录下的文件。

### 手动生成配置

开发者也可以手动生成配置文件：

```python
from src.core.config import ensure_provider_config

# 强制重新生成配置文件
config_path = ensure_provider_config(
    provider_name="tts",
    provider_layer="output",
    schema_class=TTSProviderConfig,
    force_regenerate=True
)
```

## 配置验证

### Schema验证

加载配置时自动进行Pydantic验证：

```python
from pydantic import ValidationError

try:
    config = config_service.get_provider_config_with_defaults(
        "bili_danmaku",
        "input",
        schema_class=BiliDanmakuProviderConfig
    )
except ValidationError as e:
    print(f"配置验证失败: {e}")
    # Error: room_id必须大于0 (got 0)
```

### 类型安全

Schema提供编译时和运行时类型检查：

```python
class MyProviderConfig(BaseProviderConfig):
    host: str  # 必须是字符串
    port: int  # 必须是整数
    enabled: bool  # 必须是布尔值

# 错误的配置会触发ValidationError
config = {"host": "localhost", "port": "not_an_int"}  # ❌ 错误
```

## 使用示例

### 示例1：为新Provider创建配置

**步骤1：定义Schema**

```python
# src/core/config/schemas/input_providers.py
class NewProviderConfig(BaseProviderConfig):
    """新Provider配置

    这个docstring会作为配置文件的文件头注释。
    """

    type: Literal["new_provider"] = "new_provider"
    api_key: str = Field(
        default="your-api-key",
        description="API密钥"
    )
    endpoint: str = Field(
        default="https://api.example.com",
        description="API端点"
    )
    timeout: int = Field(
        default=30,
        description="超时时间（秒）",
        ge=1
    )

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """验证API端点URL格式"""
        if not v.startswith(("http://", "https://")):
            raise ValueError("endpoint必须以http://或https://开头")
        return v
```

**步骤2：注册Schema**

```python
# src/core/config/schemas/__init__.py
from .input_providers import NewProviderConfig

PROVIDER_SCHEMA_REGISTRY["new_provider"] = NewProviderConfig
```

**步骤3：生成配置文件**

```python
from src.core.config import ensure_provider_config

config_path = ensure_provider_config(
    provider_name="new_provider",
    provider_layer="input",
    schema_class=NewProviderConfig
)
# 生成：src/layers/input/providers/new_provider/config.toml
```

**步骤4：在Provider中使用**

```python
# src/layers/input/providers/new_provider/new_provider.py
from src.core.config_service import ConfigService
from src.core.config.schemas import NewProviderConfig

class NewProvider(InputProvider):
    def __init__(self, config: dict, config_service: ConfigService):
        # 加载并验证配置
        validated_config = config_service.get_provider_config_with_defaults(
            "new_provider",
            "input",
            schema_class=NewProviderConfig
        )

        self.api_key = validated_config["api_key"]
        self.endpoint = validated_config["endpoint"]
        self.timeout = validated_config["timeout"]
```

### 示例2：在主配置中覆盖参数

```toml
# config.toml

[providers.input.overrides]
# 覆盖B站弹幕Provider的room_id
bili_danmaku.room_id = "123456"

# 覆盖TTS Provider的语音
tts.voice = "zh-CN-YunxiNeural"
tts.rate = "+10%"
```

### 示例3：Provider本地配置

```toml
# src/layers/rendering/providers/tts/config.toml

[tts]
# TTS语音名称
voice = "zh-CN-XiaoxiaoNeural"
# 语速调整
rate = "+0%"
# 音量调整
volume = "+0%"
```

## 配置迁移

### 从旧配置系统迁移

如果你使用的是旧的`config-defaults.toml`系统：

1. **创建Schema类**：定义Provider的Pydantic Schema
2. **注册Schema**：添加到`PROVIDER_SCHEMA_REGISTRY`
3. **删除旧文件**：删除`config-defaults.toml`
4. **重新生成**：使用`ensure_provider_config()`生成新配置

### 配置版本管理

建议将Schema纳入版本控制，而Provider本地配置（`config.toml`）应该被`.gitignore`忽略：

```gitignore
# .gitignore
# Provider本地配置（用户自定义）
src/layers/*/providers/*/config.toml

# 但保留Schema
!src/core/config/schemas/
```

## 最佳实践

### 1. Schema定义规范

```python
class ProviderConfig(BaseProviderConfig):
    """Provider配置

    使用清晰的docstring说明Provider的用途。
    """

    # 必填项使用Field(...)
    required_field: str = Field(..., description="必填字段说明")

    # 可选项使用Field(default=...)
    optional_field: int = Field(default=10, description="可选字段说明")

    # 带验证规则
    port: int = Field(default=8080, description="端口号", ge=1, le=65535)

    # 带默认工厂
    items: list = Field(default_factory=list, description="项目列表")
```

### 2. 字段命名约定

- 使用**蛇形命名法**（snake_case）：`room_id`, `poll_interval`
- 布尔值使用**is/has前缀**或**形容词**：`enabled`, `is_active`, `has_permission`
- 列表使用**复数形式**：`allowed_users`, `enabled_inputs`

### 3. 验证规则

- 使用Pydantic内置验证器：`Field(ge=1, le=65535)`（大于等于1，小于等于65535）
- 使用自定义`field_validator`进行复杂验证：
  ```python
  @field_validator("url")
  @classmethod
  def validate_url(cls, v: str) -> str:
      if not v.startswith(("http://", "https://")):
          raise ValueError("url必须以http://或https://开头")
      return v
  ```

### 4. 配置分层

- **Schema默认值**：Provider的合理默认值
- **主配置覆盖**：部署环境特定配置（如开发/生产环境）
- **Provider本地配置**：开发者个性化配置（如本地测试配置）

## 常见问题

### Q1：为什么要用Schema而不是配置文件？

**A**：Schema提供：
- **类型安全**：编译时和运行时类型检查
- **自动验证**：配置加载时自动验证
- **单一来源**：代码即文档，减少重复维护
- **IDE支持**：自动补全、类型提示、重构支持

### Q2：Schema修改后，配置文件会自动更新吗？

**A**：不会自动更新已存在的配置文件。你需要：
1. 手动删除旧配置文件
2. 重新运行程序，系统会自动生成新配置
3. 或使用`ensure_provider_config(force_regenerate=True)`

### Q3：如何定义复杂的配置结构？

**A**：使用嵌套的Pydantic模型：

```python
class ServerConfig(BaseModel):
    host: str = Field(default="localhost", description="服务器地址")
    port: int = Field(default=8080, description="端口")

class ProviderConfig(BaseProviderConfig):
    server: ServerConfig = Field(default_factory=ServerConfig)
    enabled: bool = Field(default=True, description="是否启用")
```

生成的TOML：
```toml
[provider.server]
host = "localhost"
port = 8080
enabled = true
```

### Q4：如何支持配置热重载？

**A**：当前架构不支持配置热重载。修改配置后需要重启程序。如需热重载，可以：
1. 使用文件监控（如`watchdog`）检测配置变化
2. 重新加载ConfigService
3. 通知Provider更新配置

### Q5：Schema中的默认值会写入配置文件吗？

**A**：自动生成配置文件时，会写入Schema定义的默认值。但为了避免配置文件臃肿，建议：
- 只在Schema中定义默认值
- 配置文件只包含用户自定义的值
- 通过`get_provider_config_with_defaults()`合并

## 技术细节

### 依赖项

- **pydantic**：Schema定义和验证
- **tomli_w**：TOML文件写入（可选，使用tomllib替代）
- **tomllib**（Python 3.11+）：TOML文件读取

### 支持的数据类型

| Pydantic类型 | TOML类型 | 示例 |
|-------------|---------|------|
| `str` | 字符串 | `host = "localhost"` |
| `int` | 整数 | `port = 8080` |
| `float` | 浮点数 | `rate = 1.5` |
| `bool` | 布尔值（小写） | `enabled = true` |
| `list` | 数组 | `items = ["a", "b"]` |
| `dict` | 内联表格 | `[provider.config] key = "value"` |
| `Optional[T]` | 可选值 | `field: Optional[str] = None` |
| `Literal["a", "b"]` | 枚举 | `type = "input"` |

### 性能考虑

- **Schema实例化**：Pydantic验证有一定开销，但通常可忽略
- **配置缓存**：ConfigService会缓存配置，避免重复加载
- **延迟验证**：只在需要时验证配置，而非启动时验证所有Provider

## 相关文档

- [CONFIG_UPGRADE_GUIDE.md](CONFIG_UPGRADE_GUIDE.md) - 配置升级指南
- [CONFIG_GENERATOR.md](CONFIG_GENERATOR.md) - 配置生成器使用指南
- [CLAUDE.md](../CLAUDE.md) - 项目配置说明
- [refactor/design/layer_refactoring.md](../refactor/design/layer_refactoring.md) - 5层架构设计

## 总结

Schema-as-Template配置系统通过以下设计实现了配置的**类型安全**、**自动生成**和**单一事实来源**：

1. **Schema定义一切**：配置结构、默认值、验证规则都在Schema中定义
2. **自动生成配置**：配置文件可以从Schema自动生成
3. **三级合并体系**：Schema默认值 → 主配置覆盖 → Provider本地配置
4. **类型安全验证**：Pydantic提供编译时和运行时类型检查
5. **文档一体化**：Schema的docstring和Field description即为配置文档

这种设计使得配置管理更加简洁、安全和可维护。
