# 插件系统移除说明

**日期**：2025年2月1日
**状态**：✅ 已完成
**影响范围**：所有重构设计文档

---

## 📋 变更摘要

插件系统已从重构设计文档中**完全移除**，替换为纯Provider架构。

### 为什么移除？

详见：[重构设计总览 - 为什么移除插件系统？](refactor/design/overview.md#为什么移除插件系统)

**核心原因**：
1. Plugin在创建Provider，违背了"不创建Provider"的设计原则
2. 与"消灭插件化"的重构目标直接矛盾
3. 增加了一层不必要的抽象，反而使架构更复杂

### 新架构

**Provider由Manager统一管理，配置驱动启用。**

- Provider位置：`src/layers/{layer}/providers/`
- 管理方式：Manager直接管理
- 启用方式：配置文件中的 `[input]enabled` 或 `[output]enabled`

---

## 📁 已更新的文档

### 主要文档

| 文档 | 变更内容 |
|------|---------|
| **[refactor/design/overview.md](refactor/design/overview.md)** | ✅ 完全重写<br/>- 说明为什么移除插件系统<br/>- 更新架构图，移除插件系统<br/>- 添加Provider管理架构说明<br/>- 添加社区扩展指南 |
| **[refactor/design/layer_refactoring.md](refactor/design/layer_refactoring.md)** | ✅ 架构图更新<br/>- 移除"插件系统"部分<br/>- 添加"配置驱动启用"标注<br/>- 更新相关文档链接 |
| **[refactor/design/plugin_system.md](refactor/design/plugin_system.md)** | ✅ 标记为已废弃<br/>- 添加"⚠️ 已废弃"警告<br/>- 详细说明废弃原因<br/>- 提供迁移指南 |
| **[refactor/design/multi_provider.md](refactor/design/multi_provider.md)** | ✅ 更新文档链接<br/>- 标记插件系统文档为已废弃 |
| **[refactor/design/core_refactoring.md](refactor/design/core_refactoring.md)** | ✅ 更新文档链接<br/>- 标记插件系统文档为已废弃 |

### 其他文档

| 文档 | 变更内容 |
|------|---------|
| **[refactor/design/avatar_refactoring.md](refactor/design/avatar_refactoring.md)** | ✅ 标记插件系统文档为已废弃 |
| **[refactor/design/data_cache.md](refactor/design/data_cache.md)** | ✅ 标记插件系统文档为已废弃 |
| **[refactor/design/event_data_contract.md](refactor/design/event_data_contract.md)** | ✅ 标记插件系统文档为已废弃 |
| **[refactor/design/http_server.md](refactor/design/http_server.md)** | ✅ 标记插件系统文档为已废弃 |
| **[refactor/design/pipeline_refactoring.md](refactor/design/pipeline_refactoring.md)** | ✅ 标记插件系统文档为已废弃 |

---

## 🔄 配置迁移指南

### 旧配置（已废弃）

```toml
# 插件配置（已废弃）
[plugins]
enabled = [
    "console_input",
    "bili_danmaku",
    "tts",
]

[plugins.console_input]
enabled = true
source = "stdin"

[plugins.bili_danmaku]
enabled = true
room_id = "123456"

[plugins.tts]
enabled = true
engine = "gptsovits"
```

### 新配置（推荐）

```toml
# 输入Provider配置
[input]
enabled = ["console", "bili_danmaku"]

[input.providers.console]
source = "stdin"

[input.providers.bili_danmaku]
room_id = "123456"

# 输出Provider配置
[output]
enabled = ["tts", "subtitle", "vts"]

[output.providers.tts]
engine = "gptsovits"
api_url = "http://localhost:5000"
```

### 配置映射规则

| 旧配置路径 | 新配置路径 |
|-----------|-----------|
| `[plugins]enabled = [...]` | `[input]enabled = [...]` 或 `[output]enabled = [...]` |
| `[plugins.xxx]enabled` | 移除，使用 `[input]enabled` 或 `[output]enabled` 列表 |
| `[plugins.xxx]` | `[input.providers.xxx]` 或 `[output.providers.xxx]` |

---

## 📦 代码迁移指南

### Plugin → Provider

**旧代码（Plugin方式）**：

```python
# src/plugins/my_plugin/plugin.py
class MyPlugin:
    async def setup(self, event_bus, config) -> List[Any]:
        # ❌ 创建Provider
        provider = MyProvider(config)
        await provider.setup(event_bus)
        return [provider]
```

**新代码（Provider方式）**：

```python
# src/layers/input/providers/my_provider.py
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from typing import AsyncIterator

class MyInputProvider(InputProvider):
    """自定义输入Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("MyInputProvider")

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while self.is_running:
            data = await self._fetch_data()
            if data:
                yield RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )
```

### 目录迁移

| 旧目录 | 新目录 |
|--------|--------|
| `src/plugins/my_plugin/providers/` | `src/layers/input/providers/` 或 `src/layers/output/providers/` |
| `src/plugins/my_plugin/plugin.py` | 移除，Provider直接放在providers目录 |

---

## ✅ 检查清单

### 文档更新

- [x] 更新 refactor/design/overview.md
- [x] 更新 refactor/design/layer_refactoring.md
- [x] 标记 refactor/design/plugin_system.md 为已废弃
- [x] 更新所有设计文档中的插件系统引用

### 代码迁移

- [ ] 移除 `src/plugins/` 目录
- [ ] 将Provider移到对应的 `src/layers/{layer}/providers/` 目录
- [ ] 更新配置文件格式
- [ ] 更新 AmaidesuCore 中的PluginManager相关代码
- [ ] 测试所有功能正常运行

### 验证

- [ ] 配置文件不再识别 `[plugins.xxx]` 格式
- [ ] 所有Provider由对应的Manager管理
- [ ] EventBus通信正常
- [ ] 功能测试通过

---

## 🔗 相关链接

- **设计总览**：[refactor/design/overview.md](refactor/design/overview.md)
- **插件系统废弃说明**：[refactor/design/plugin_system.md](refactor/design/plugin_system.md)
- **5层架构设计**：[refactor/design/layer_refactoring.md](refactor/design/layer_refactoring.md)
- **Provider管理架构**：[refactor/design/multi_provider.md](refactor/design/multi_provider.md)

---

## 📝 遗留问题

### 待办事项

1. **代码迁移**：将 `src/plugins/` 下的Provider移到对应层目录
2. **配置迁移**：更新配置文件格式
3. **AmaidesuCore重构**：移除PluginManager相关代码
4. **测试验证**：确保所有功能正常运行

### 注意事项

- 旧的Plugin配置会报错，需要迁移到新的Provider配置格式
- 社区开发者需要更新自定义Plugin
- 文档已完全更新，开发者可以参考新架构

---

**最后更新**：2025年2月1日
**维护者**：Amaidesu Team
