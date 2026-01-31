# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用开发命令

### 运行应用程序
```bash
# 正常运行
python main.py

# 调试模式（显示详细日志）
python main.py --debug

# 过滤日志，只显示指定模块（除了WARNING及以上级别的日志）
python main.py --filter StickerPlugin TTSPlugin

# 调试模式并过滤特定模块
python main.py --debug --filter StickerPlugin
```

### 测试
```bash
# 运行所有测试
python -m pytest tests/

# 运行特定测试文件
python -m pytest tests/test_event_system.py

# 详细输出模式
python -m pytest tests/ -v
```

### 代码质量
```bash
# 代码检查
ruff check .

# 代码格式化
ruff format .

# 自动修复可修复的问题
ruff check --fix .
```

### 开发环境设置
```bash
# 安装依赖
pip install -r requirements.txt

# 当没有部署MaiCore时，使用模拟服务器测试
python mock_maicore.py
```

## 核心架构

项目采用**6层架构** + **Provider系统** + **插件系统**的组合设计，通过核心模块管理插件与MaiCore（主聊天机器人核心）之间的通信。

### 6层核心数据流

```
外部输入（弹幕、游戏、语音）
  ↓
【Layer 1: 输入感知】多个InputProvider并发采集
  ↓ 采集RawData
【Layer 2: 输入标准化】统一转换为Text
  ↓ 转换
【Layer 3: 中间表示】构建CanonicalMessage
  ↓ 生成Intent
【决策层：DecisionProvider】⭐ 可替换、可扩展
  ├─ MaiCoreDecisionProvider (默认，WebSocket连接MaiCore)
  ├─ LocalLLMDecisionProvider (可选，本地LLM)
  └─ RuleEngineDecisionProvider (可选，规则引擎)
  ↓ 返回MessageBase
【Layer 4: 表现理解】解析MessageBase → Intent
  ↓ EventBus事件流
【Layer 5: 表现生成】生成RenderParameters
  ↓ 生成ExpressionParameters
【Layer 6: 渲染呈现】多个OutputProvider并发渲染
  ↓ 并发渲染到多个设备
【插件系统：Plugin】社区开发的插件能力
```

### 关键组件

#### 核心模块
- **AmaidesuCore** ([`src/core/amaidesu_core.py`](src/core/amaidesu_core.py)): 中央枢纽，管理插件、EventBus、DecisionManager、OutputProviderManager
- **EventBus** ([`src/core/event_bus.py`](src/core/event_bus.py)): 增强的事件总线（优先级、错误隔离、统计功能）
- **PluginManager** ([`src/core/plugin_manager.py`](src/core/plugin_manager.py)): 插件管理器（支持新旧两套架构）
- **PipelineManager** ([`src/core/pipeline_manager.py`](src/core/pipeline_manager.py)): 管道管理器（消息预处理）

#### Provider接口系统（核心抽象）
- **InputProvider** ([`src/core/providers/input_provider.py`](src/core/providers/input_provider.py)): 输入Provider接口，从外部数据源采集RawData
- **DecisionProvider** ([`src/core/providers/decision_provider.py`](src/core/providers/decision_provider.py)): 决策Provider接口，处理CanonicalMessage
- **OutputProvider** ([`src/core/providers/output_provider.py`](src/core/providers/output_provider.py)): 输出Provider接口，渲染到目标设备

#### 管理器
- **DecisionManager** ([`src/core/decision_manager.py`](src/core/decision_manager.py)): 管理决策Provider，支持运行时切换
- **OutputProviderManager** ([`src/core/output_provider_manager.py`](src/core/output_provider_manager.py)): 管理输出Provider，支持并发渲染
- **LLMClientManager** ([`src/core/llm_client_manager.py`](src/core/llm_client_manager.py)): 管理LLM客户端（llm/llm_fast/vlm）
- **ContextManager** ([`src/core/context_manager.py`](src/core/context_manager.py)): 聚合插件上下文信息

### Provider接口说明

Provider是**新架构的核心抽象**，封装具体功能，提供更好的解耦和可测试性。

| Provider类型 | 职责 | 示例实现 |
|-------------|------|---------|
| **InputProvider** | 从外部数据源采集RawData | BiliDanmakuProvider、MinecraftProvider、ConsoleProvider |
| **DecisionProvider** | 处理CanonicalMessage并决策 | MaiCoreDecisionProvider、LocalLLMDecisionProvider、RuleEngineProvider |
| **OutputProvider** | 渲染到目标设备 | TTSProvider、SubtitleProvider、VTSProvider、StickerProvider |

Provider实现位于：
- 接口定义：[`src/core/providers/`](src/core/providers/)
- 具体实现：[`src/providers/`](src/providers/)

### 插件系统

项目支持**两种互补的插件架构**（向后兼容）：

#### 新架构（推荐）：Plugin协议

- **不继承任何基类**，通过event_bus和config依赖注入
- **返回Provider列表**（InputProvider、OutputProvider、DecisionProvider等）
- 参考实现：[`src/plugins/gptsovits_tts/plugin.py`](src/plugins/gptsovits_tts/plugin.py)（已迁移）
- 接口定义：[`src/core/plugin.py`](src/core/plugin.py)

#### 旧架构（已废弃）：BasePlugin

- **继承BasePlugin类**，通过self.core访问核心功能
- 通过服务注册机制通信
- 仅用于向后兼容，现有插件无需立即迁移

**迁移状态**：
- ✅ 已迁移：gptsovits_tts
- ⏳ 待迁移：console_input、tts等大部分插件

### 通信模式

项目支持**两种互补的通信模式**：

1. **服务注册机制（请求-响应模式）**
   - 插件通过 `core.register_service(name, instance)` 注册服务
   - 其他插件通过 `core.get_service(name)` 获取服务
   - 适用场景：稳定的、长期存在的功能（如TTS、VTS控制）
   - 示例：`vts_service = core.get_service("vts_control")`

2. **事件系统（发布-订阅模式）**（推荐）
   - 插件通过 `await event_bus.emit(event_name, data, source)` 发布事件
   - 其他插件通过 `self.listen_event(event_name, handler)` 监听事件
   - 适用场景：瞬时通知、广播场景
   - 示例：`await event_bus.emit("command_router.received", command_data, "CommandRouter")`

**EventBus增强功能**：
- 优先级控制（priority参数）
- 错误隔离（单个handler异常不影响其他）
- 统计功能（emit计数、错误率、执行时间）

### 插件配置

#### 插件启用（新格式推荐）

推荐使用新的 `enabled` 列表格式来管理插件开关：

```toml
[plugins]
# 启用的插件列表
enabled = [
    "console_input",
    "llm_text_processor",
    "keyword_action",

    # 注释掉的插件将被禁用
    # "stt",
    # "bili_danmaku",
]
```

**新格式优势**：
- 一目了然：所有启用的插件集中在一个列表中
- 操作简便：只需添加、删除或注释插件名
- 分组清晰：通过注释可以将相关插件分组
- 避免混淆：不会因为同时存在enabled和disabled列表而困惑

**向后兼容**：
系统仍然支持旧的 `enable_xxx = true/false` 格式，但会提示迁移到新格式。如果同时存在两种格式，新格式优先。

#### 管道配置

管道按优先级顺序在消息发送到MaiCore前进行预处理：

```toml
[pipelines]
  # 管道名称对应src/pipelines/下的目录名（snake_case）
  [pipelines.command_router]
  priority = 100  # 必须定义priority来启用管道

  [pipelines.throttle]
  priority = 200  # 数值越小，优先级越高
  global_rate_limit = 50  # 可选：覆盖管道独立配置
```

### 插件开发

#### 新插件（推荐）

```python
# src/plugins/my_plugin/plugin.py
from typing import Dict, Any, List
from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider
from src.utils.logger import get_logger

class MyPlugin:
    """我的插件（使用新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._providers: List[InputProvider] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """设置插件，返回Provider列表"""
        from .providers.my_provider import MyProvider
        provider = MyProvider(config)
        self._providers.append(provider)
        return self._providers

    async def cleanup(self):
        """清理资源"""
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "My plugin description",
            "category": "input",  # input/output/processing/game/hardware/software
            "api_version": "1.0",
        }

plugin_entrypoint = MyPlugin
```

#### Provider开发示例

```python
# src/plugins/my_plugin/providers/my_provider.py
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from typing import AsyncIterator
from src.utils.logger import get_logger

class MyProvider(InputProvider):
    """自定义输入Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while self.is_running:
            # 采集数据逻辑
            data = await self._fetch_data()
            if data:
                yield RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )
```

### 配置文件层次

```
config.toml（根配置）
├── [plugins] - 全局插件配置
│   └── enabled = [...] - 新格式启用列表
├── [plugins.xxx] - 插件级覆盖
├── [pipelines] - 管道配置
│   └── [pipelines.xxx] - 各管道priority+配置
├── [llm]/[llm_fast]/[vlm] - LLM配置
└── [avatar] - 虚拟形象控制配置
```

- 根配置：`config-template.toml` → `config.toml`（首次运行时自动生成）
- 插件配置：`src/plugins/{插件}/config-template.toml` → `config.toml`
- 管道配置：`src/pipelines/{管道}/config-template.toml` → `config.toml`
- 全局覆盖：根配置中的 `[plugins.插件名]` 或 `[pipelines.管道名]` 部分

### 开发注意事项

- 项目使用中文作为注释和用户界面语言
- 事件系统和服务注册可以共存以保持向后兼容
- 日志过滤器使用类名或目录名作为模块标识符
- 新插件应使用Plugin协议（参考gptsovits_tts实现）
- Provider提供更好的解耦和可测试性
- 系统在没有EventBus时会优雅地回退到服务注册模式
- 应用程序在生成配置文件后会退出，需要手动配置API密钥和其他敏感值后重新启动

### 架构设计文档

详细的架构设计文档位于 [`refactor/design/`](refactor/design/)：
- [架构总览](refactor/design/overview.md) - 重构目标和6层架构概述
- [插件系统设计](refactor/design/plugin_system.md) - 插件系统和Provider接口详细说明
- [决策层设计](refactor/design/decision_layer.md) - 可替换的决策Provider系统
- [多Provider并发设计](refactor/design/multi_provider.md) - 输入/输出层并发处理

### 关键变化摘要（相比旧架构）

| 变化 | 旧架构 | 新架构 |
|-----|-------|--------|
| **插件模式** | 继承BasePlugin | 实现Plugin协议（依赖注入） |
| **核心抽象** | 服务注册 | Provider接口（Input/Decision/Output） |
| **通信方式** | 服务注册为主 | EventBus事件系统（优先级、错误隔离） |
| **数据流** | 隐式、混乱 | 明确的6层架构 |
| **决策层** | 硬编码MaiCore | 可替换的DecisionProvider |
| **输出渲染** | 单一输出 | 多Provider并发渲染 |
| **配置格式** | enable_xxx布尔值 | enabled列表（推荐） |
| **DataCache** | 存在 | 已移除 |
