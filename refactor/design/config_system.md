# 配置系统设计

## 核心理念

**Schema即模板**：Pydantic BaseModel定义配置结构和默认值，首次启动时自动生成config.toml，运行时加载并验证。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    配置生命周期                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  【开发时】                                                  │
│  Pydantic Schema (代码，纳入版本管理)                        │
│  └── 定义字段类型 + 默认值 + 验证规则                        │
│                                                             │
│  【首次启动】                                                │
│  Schema.model_dump() → tomli_w → config.toml               │
│  └── 自动为每个Provider生成配置文件                          │
│                                                             │
│  【运行时】                                                  │
│  config.toml → tomllib.load() → Schema(**data) → 验证      │
│  └── 加载用户配置，Schema验证后使用                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 关键设计原则

### 1. Schema是唯一真相源

- **无** config-defaults.toml（废弃）
- Schema代码 = 模板 + 默认值 + 验证规则
- 首次启动时从Schema自动生成config.toml

### 2. 配置覆盖关系（二级）

```
Schema默认值 (最低优先级)
    ↓ 被覆盖
主配置 [providers.{layer}.{name}] (最高优先级)
```

### 3. 关注点分离

| 配置位置 | 职责 | 示例 |
|---------|------|------|
| **主配置** `enabled_outputs` | 控制哪些Provider启用 | `["subtitle", "vts"]` |
| **主配置** `[providers.*.{name}]` | 控制Provider怎么运行 | `font_size = 24` |

**约束**：Provider配置中**禁止**包含`enabled`字段。

### 4. 版本管理规则

| 文件 | 版本管理 | 说明 |
|-----|---------|------|
| Schema代码 (`schemas/*.py`) | ✅ 纳入 | 配置结构的唯一定义 |
| `config-template.toml` | ✅ 纳入 | 主配置模板 |
| `config.toml` (所有位置) | ❌ gitignore | 用户配置，首次启动生成 |

---

## 文件结构

```
Amaidesu/
├── config-template.toml              # 主配置模板 (版本管理)
├── config.toml                       # 主配置 (gitignore)
│
├── src/core/config/
│   ├── config_service.py             # 配置加载服务
│   ├── generator.py                  # 从Schema生成config.toml
│   └── schemas/                      # Pydantic Schema (版本管理)
│       ├── base.py
│       ├── input_providers.py
│       ├── decision_providers.py
│       └── output_providers.py
│
└── src/layers/*/providers/{name}/
    └── config.toml                   # Provider配置 (gitignore)
```

---

## Pydantic Schema 设计

### 基类

```python
class ProviderConfig(BaseModel):
    """Provider配置基类"""
    model_config = ConfigDict(extra="forbid")
    
    @classmethod
    def generate_toml(cls, path: Path) -> None:
        """从Schema生成config.toml"""
        data = cls().model_dump()
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
```

### 示例Schema

```python
class SubtitleConfig(ProviderConfig):
    font_size: int = Field(default=24, ge=8, le=72)
    font_family: str = "Arial"
    display_time: float = Field(default=5.0, gt=0)
```

---

## 配置加载流程

### 启动时

```python
def ensure_config(provider_name: str, schema: type) -> Path:
    """确保config.toml存在"""
    path = get_provider_config_path(provider_name)
    if not path.exists():
        schema.generate_toml(path)
    return path
```

### 加载与合并

```python
def load_config(provider_name: str, schema: type) -> ProviderConfig:
    """二级合并：Schema默认值 < 主配置"""
    result = schema().model_dump()                    # 1. Schema默认值
    result = deep_merge(result, get_main_override())  # 2. 主配置覆盖
    return schema(**result)                           # 验证
```

---

## 主配置格式

```toml
# config.toml (根目录)

[llm]
api_key = "your-key"

[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.output]
enabled_outputs = ["subtitle", "vts"]

# Provider 配置（在主配置文件中）
[providers.output.subtitle]
font_size = 32
```

---

## 首次启动流程

```
1. config.toml 不存在 → 从 config-template.toml 复制
2. 读取 enabled_inputs / enabled_outputs
3. 对每个启用的Provider：
   └── config.toml 不存在 → 从 Schema 生成
4. 加载配置并验证
5. 启动应用
```

---

## .gitignore

```gitignore
config.toml
src/layers/*/providers/*/config.toml
```

---

## 相关文档

- [CONFIG_FINAL_DESIGN.md](../../docs/CONFIG_FINAL_DESIGN.md) - 历史方案参考
