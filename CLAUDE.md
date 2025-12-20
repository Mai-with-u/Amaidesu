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

项目采用基于插件的架构，通过核心模块管理插件与MaiCore（主聊天机器人核心）之间的通信。

### 关键组件

1. **AmaidesuCore** (`src/core/amaidesu_core.py`): 中央枢纽，管理与MaiCore的WebSocket连接、HTTP回调和插件间通信
2. **PluginManager** (`src/core/plugin_manager.py`): 动态加载和管理 `src/plugins/` 下的插件
3. **PipelineManager** (`src/core/pipeline_manager.py`): 在消息发送到MaiCore前进行预处理（如限流、过滤）
4. **ContextManager** (`src/core/context_manager.py`): 聚合来自不同插件的上下文信息
5. **EventBus** (`src/core/event_bus.py`): 插件间的事件驱动通信系统（最新提交添加）

### 通信模式

项目支持**两种**互补的通信模式：

1. **服务注册机制（请求-响应模式）**
   - 插件通过 `core.register_service(name, instance)` 注册服务
   - 其他插件通过 `core.get_service(name)` 获取服务
   - 适用场景：稳定的、长期存在的功能（如TTS、VTS控制）
   - 示例：`vts_service = core.get_service("vts_control")`

2. **事件系统（发布-订阅模式）**
   - 插件通过 `await event_bus.emit(event_name, data, source)` 发布事件
   - 其他插件通过 `self.listen_event(event_name, handler)` 监听事件
   - 适用场景：瞬时通知、广播场景
   - 示例：`await event_bus.emit("command_router.received", command_data, "CommandRouter")`

### 插件结构

插件继承自 `BasePlugin` 并实现：
- `__init__()`: 插件初始化
- `setup()`: 注册处理器、服务或事件监听器
- `cleanup()`: 资源清理

插件配置自动从 `config.toml` 文件加载，支持在根 `config.toml` 中进行全局覆盖。

### 管道系统

管道按优先级顺序在消息发送到MaiCore前进行预处理：
- 位于 `src/pipelines/`
- 在根 `config.toml` 的 `[pipelines]` 部分启用/配置
- 每个管道有 `priority`（数字越小优先级越高）
- 示例：CommandRouter管道在命令到达MaiCore前处理它们

### 配置系统

- 根配置：`config-template.toml` → `config.toml`（首次运行时自动生成）
- 插件配置：`src/plugins/{插件}/config-template.toml` → `config.toml`
- 全局插件覆盖：根配置中的 `[plugins.插件名]` 部分
- 管道配置：`src/pipelines/{管道}/config-template.toml` → `config.toml`
- 管道覆盖：根配置中的 `[pipelines.管道名]` 部分

#### 插件配置（新格式）

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

应用程序在生成配置文件后会退出，需要手动配置API密钥和其他敏感值后重新启动。

## 开发注意事项

- 项目使用中文作为注释和用户界面语言
- 事件系统和服务注册可以共存以保持向后兼容
- 日志过滤器使用类名或目录名作为模块标识符
- 插件可以通过配置选择性启用/禁用
- 系统在没有EventBus时会优雅地回退到服务注册模式