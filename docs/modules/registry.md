# Provider 注册表模块

负责统一管理所有 Provider 的注册和创建。

## 概述

`src/registry.py` 模块提供 Provider 注册功能：
- 内置 Provider 自动注册
- 配置驱动的 Provider 创建
- Provider 信息查询

## 核心 API

### ProviderRegistry

```python
from src.modules.registry import ProviderRegistry

# 获取注册表实例
registry = ProviderRegistry()

# 列出所有 Provider
all_providers = registry.list_all()

# 按类型列出
input_providers = registry.list_by_type("input")
decision_providers = registry.list_by_type("decision")
output_providers = registry.list_by_type("output")

# 获取 Provider 信息
info = registry.get_info("bili_danmaku")
print(info.name)        # "bili_danmaku"
print(info.provider_type)  # "input"
print(info.module)      # "src.domains.input.providers.bili_danmaku"
```

### 注册 Provider

Provider 通过 `ProviderRegistry.register()` 方法自动注册：

```python
# 在 Provider 的 __init__.py 中
from src.modules.registry import ProviderRegistry

registry = ProviderRegistry()

@registry.register(name="my_provider", provider_type="input")
class MyProvider(InputProvider):
    name = "my_provider"
    # ...
```

### 创建 Provider 实例

```python
# 通过注册表创建
provider = registry.create(
    "bili_danmaku",
    config=provider_config,
    dependencies=dependencies
)
```

## Provider 信息

### ProviderInfo

```python
from src.modules.registry import ProviderInfo

info = ProviderInfo(
    name="bili_danmaku",
    provider_type="input",
    module="src.domains.input.providers.bili_danmaku",
    class_name="BiliDanmakuInputProvider",
    config_schema=Schema(...),
    description="B站弹幕输入"
)
```

## 使用示例

### 在 Manager 中使用

```python
from src.modules.registry import ProviderRegistry
from src.modules.config import get_provider_schema

class InputProviderManager:
    def __init__(self, config, event_bus):
        self.registry = ProviderRegistry()
        self.providers = {}
        self.config = config
        self.event_bus = event_bus

    async def setup(self):
        # 获取启用的 Provider
        enabled = self.config.get("providers.input.enabled_inputs", [])

        for name in enabled:
            # 获取配置 Schema
            schema = get_provider_schema(name)

            # 验证配置
            provider_config = self._validate_config(name, schema)

            # 创建 Provider
            provider = self.registry.create(
                name,
                config=provider_config,
                dependencies={"event_bus": self.event_bus}
            )

            self.providers[name] = provider
```

### 查询 Provider

```python
# 检查 Provider 是否存在
if registry.has("bili_danmaku"):
    print("Provider 已注册")

# 获取所有可用 Provider
all_names = registry.list_all_names()
print(f"可用 Provider: {all_names}")

# 按类型筛选
input_names = registry.list_by_type_names("input")
output_names = registry.list_by_type_names("output")
```

## 导入顺序

Provider 注册依赖于模块导入顺序。确保在应用启动时导入所有 Provider 模块：

```python
# 导入所有 Provider 模块以触发注册
from src.domains.input import providers as input_providers
from src.domains.decision import providers as decision_providers
from src.domains.output import providers as output_providers
```

## 配置驱动

通过配置文件启用/禁用 Provider：

```toml
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.output]
enabled_outputs = ["tts", "subtitle", "vts"]
```

---

*最后更新：2026-02-14*
