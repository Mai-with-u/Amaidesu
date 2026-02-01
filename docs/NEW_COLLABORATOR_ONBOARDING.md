# Amaidesu 架构学习指南 - 新协作者入门

> **目标受众**: 新加入的团队成员
> **前置知识**: Python 基础
> **学习路径**: 旧架构 → 新架构 → 迁移理解
> **阅读时间**: 约 30 分钟

---

## 📋 学习路径总览

```
第一步：了解旧架构（10分钟）
  ├─ AmaidesuCore 的原始职责
  ├─ BasePlugin 插件系统
  └─ 服务注册机制

第二步：了解新架构（15分钟）
  ├─ 6 层核心数据流
  ├─ Provider 接口系统
  ├─ Plugin 协议系统
  └─ EventBus 事件系统

第三步：理解迁移变化（5分钟）
  ├─ 从 BasePlugin 到 Plugin
  ├─ 从服务注册到 EventBus
  └─ AmaidesuCore 职责变化

第四步：实际开发指南
  ├─ 如何开发新插件
  ├─ 如何调试和测试
  └─ 常见问题解答
```

---

## 📚 第一步：旧架构（重构前）

### 1.1 旧架构核心理念

**问题**：
- 所有东西都叫"插件"（共 24 个）
- 核心功能（WebSocket/HTTP/路由）也在 AmaidesuCore 里
- 插件之间通过"服务注册"相互调用，形成复杂依赖链

**数据流（简化版）**：
```
外部输入（弹幕、游戏、语音）
    ↓
插件接收数据
    ↓
通过 self.core.send_to_maicore() 发送到 MaiCore
    ↓
MaiCore 处理
    ↓
返回消息给插件（WebSocket Handler）
    ↓
插件调用其他插件服务（通过 self.core.get_service()）
    ↓
输出到各个目标（TTS、字幕、VTS）
```

### 1.2 AmaidesuCore 的旧职责

**旧代码量**：约 641 行

**核心功能**：
```python
class AmaidesuCore:
    def __init__(self, maicore_host, maicore_port, ...):
        # WebSocket 连接管理
        self.ws_client = None
        self.maicore_host = maicore_host
        self.maicore_port = maicore_port
        
        # HTTP 服务器管理
        self.http_server = None
        
        # 插件管理器
        self.plugin_manager = PluginManager()
        
        # 管道管理器
        self.pipeline_manager = PipelineManager()
        
        # 上下文管理器
        self.context_manager = ContextManager()
        
        # 服务注册表
        self._services = {}  # 插件可以注册自己为服务
    
    async def send_to_maicore(self, message):
        """通过 WebSocket 发送消息到 MaiCore"""
        await self.ws_client.send(message)
    
    def register_service(self, name, service):
        """注册服务，供其他插件调用"""
        self._services[name] = service
    
    def get_service(self, name):
        """获取服务"""
        return self._services.get(name)
    
    async def register_websocket_handler(self, message_type, handler):
        """注册 WebSocket 消息处理器"""
        # 注册处理器，接收 MaiCore 返回的消息
        ...
```

### 1.3 BasePlugin 插件系统

**旧插件接口**：
```python
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from typing import Dict, Any

class MyPlugin(BasePlugin):
    """
    旧插件系统：继承 BasePlugin
    - 通过 self.core 访问核心功能
    - 通过 self.core.register_service() 注册服务
    - 通过 self.core.get_service() 调用其他插件服务
    """
    
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # 插件配置
        self.room_id = plugin_config.get("room_id")
        self.api_key = plugin_config.get("api_key")
        
        # 可以直接访问 core
        self.core = core
    
    async def setup(self):
        """初始化插件"""
        # 注册 WebSocket 处理器
        await self.core.register_websocket_handler("text", self.handle_message)
        
        # 注册自己为服务，供其他插件调用
        self.core.register_service("my_service", self)
        
        # 调用其他插件的服务
        tts_service = self.core.get_service("tts")
        tts_service.speak("你好")
    
    async def handle_message(self, message: MessageBase):
        """处理从 MaiCore 返回的消息"""
        text = message.message_segment.data
        print(f"收到消息: {text}")
    
    async def cleanup(self):
        """清理插件资源"""
        # 取消 WebSocket 处理器
        # 取消服务注册
        pass
```

### 1.4 服务注册机制

**工作原理**：
```
插件 A 注册服务:
    self.core.register_service("tts", TTSPlugin实例)
    
插件 B 调用服务:
    tts_service = self.core.get_service("tts")
    tts_service.speak("Hello")
```

**问题**：
- ❌ 依赖链复杂（插件 A → 服务 B → 插件 C）
- ❌ 运行时依赖不清晰
- ❌ 难以测试（需要 mock AmaidesuCore）

### 1.5 旧架构的优点和缺点

**优点**：
- ✅ 简单直接：插件直接访问 AmaidesuCore
- ✅ 服务调用方便：get_service() 即可

**缺点**：
- ❌ AmaidesuCore 职责过重（641 行）
- ❌ WebSocket/HTTP 紧耦合在核心中
- ❌ 依赖地狱（18 个插件使用服务注册）
- ❌ 难以扩展（社区开发者需要理解复杂的依赖链）

---

## 🆕 第二步：新架构（重构后）

### 2.1 新架构核心理念

**核心改进**：
1. **消灭插件化**：核心功能全部模块化，不作为"插件"
2. **统一接口**：同一功能收敛到 Provider 接口
3. **消除依赖**：推广 EventBus 通信，替代服务注册
4. **按数据流组织**：6 层清晰的架构

**新数据流**：
```
外部输入（弹幕、游戏、语音）
    ↓
【Layer 1: 输入感知】多个 InputProvider 并发采集
    ↓ (发布 perception.raw_data.generated 事件)
【Layer 2: 输入标准化】转换为 NormalizedText
    ↓ (发布 normalization.text.ready 事件)
【Layer 3: 中间表示】构建 CanonicalMessage
    ↓ (发送到决策层)
【Layer 4: 决策层】DecisionProvider 可替换、可扩展
    ├─ MaiCoreDecisionProvider (默认)
    ├─ LocalLLMDecisionProvider (可选)
    └─ RuleEngineDecisionProvider (可选)
    ↓ (返回 MessageBase)
【Layer 5: 表现理解】解析 MessageBase → Intent
    ↓ (发布 understanding.intent_generated 事件)
【Layer 6: 表现生成】生成 RenderParameters
    ↓ (发布 expression.parameters_generated 事件)
【Layer 7: 渲染呈现】多个 OutputProvider 并发渲染
    ↓ (字幕、TTS、VTS 等)
【插件系统：Plugin】社区开发的插件能力
```

### 2.2 AmaidesuCore 的简化

**新代码量**：341 行（减少 24 行，-6.6%）

**删除的职责**（约 500 行）：
- ❌ WebSocket 连接管理 → 迁移到 MaiCoreDecisionProvider
- ❌ HTTP 服务器管理 → 独立管理
- ❌ maim_message.Router 相关 → 迁移到 MaiCoreDecisionProvider
- ❌ send_to_maicore() 方法
- ❌ _handle_maicore_message() 方法

**保留的职责**：
- ✅ EventBus 管理
- ✅ Pipeline 管理
- ✅ Context 管理
- ✅ Avatar 管理器
- ✅ LLM 客户端管理器

**新增的职责**：
- ✅ DecisionManager 集成（决策层管理）
- ✅ OutputProviderManager 集成（输出层管理）
- ✅ ExpressionGenerator 集成（表现生成）

### 2.3 Provider 接口系统

**核心理念**：标准化原子能力，分为三类

**接口定义**：

| 类型 | 位置 | 职责 | 示例 |
|------|------|------|------|
| **InputProvider** | Layer 1 | 接收外部数据，生成 RawData | ConsoleInputProvider, BiliDanmakuInputProvider |
| **OutputProvider** | Layer 7 | 接收渲染参数，执行实际输出 | TTSOutputProvider, SubtitleOutputProvider |
| **DecisionProvider** | Layer 4: 决策层 | 决策能力接口 | MaiCoreDecisionProvider, LocalLLMDecisionProvider |

**InputProvider 示例**：
```python
from typing import AsyncIterator
from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger

class MyInputProvider(InputProvider):
    """输入 Provider - 示例"""
    
    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("MyInputProvider")
        self._running = False
    
    async def start(self) -> AsyncIterator[RawData]:
        """启动输入流"""
        self._running = True
        while self._running:
            # 采集数据
            data = await self._collect_data()
            yield RawData(
                content=data,
                source="my_provider",
                data_type="text",
                metadata={"timestamp": time.time()}
            )
    
    async def stop(self):
        """停止输入源"""
        self._running = False
    
    async def cleanup(self):
        """清理资源"""
        self.logger.info("MyInputProvider 清理完成")
```

### 2.4 Plugin 协议系统

**核心理念**：聚合多个 Provider，社区开发入口

**与 BasePlugin 的对比**：

| 特性 | BasePlugin（旧） | Plugin（新） |
|------|----------------|-------------|
| **继承** | 继承 AmaidesuCore | 不继承，Protocol 接口 |
| **依赖注入** | 通过 self.core 访问 | 通过 event_bus 和 config 依赖注入 |
| **返回值** | 无 | setup() 返回 Provider 列表 |
| **通信方式** | 服务注册 | EventBus 订阅/发布 |

**Plugin 示例**：
```python
from typing import List
from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.core.providers.input_provider import InputProvider
from src.core.providers.output_provider import OutputProvider
from src.utils.logger import get_logger

class MyPlugin:
    """
    插件协议 - 聚合多个 Provider
    
    与旧 BasePlugin 的区别：
    - 不继承 AmaidesuCore
    - 通过 event_bus 和 config 依赖注入
    - setup() 返回 Provider 列表
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("MyPlugin")
    
    async def setup(self, event_bus: EventBus, config: dict) -> List[Any]:
        """初始化插件，返回 Provider 列表"""
        self.event_bus = event_bus
        
        providers = []
        
        # 创建输入 Provider（如果有）
        if config.get("input_enabled", True):
            input_provider = MyInputProvider(config)
            await input_provider.start()
            providers.append(input_provider)
            self.logger.info("输入 Provider 已创建")
        
        # 创建输出 Provider（如果有）
        if config.get("output_enabled", True):
            output_provider = MyOutputProvider(config)
            await output_provider.setup(event_bus)
            providers.append(output_provider)
            self.logger.info("输出 Provider 已创建")
        
        # 订阅 EventBus 事件（如果需要）
        event_bus.on("some.event", self.on_event)
        
        return providers
    
    async def cleanup(self):
        """清理资源"""
        # 清理所有 Provider
        for provider in self.providers:
            await provider.cleanup()
        self.logger.info("MyPlugin 清理完成")
    
    def get_info(self) -> dict:
        """获取插件信息"""
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "author": "Author",
            "description": "My plugin description",
            "category": "input/output/processing",
            "api_version": "1.0"
        }
```

### 2.5 EventBus 事件系统

**核心理念**：发布-订阅模式，实现松耦合

**工作原理**：
```python
# 订阅事件
event_bus.on("normalization.text.ready", handler)

# 发布事件
await event_bus.emit("normalization.text.ready", {"normalized": normalized_text})

# 取消订阅
event_bus.off("normalization.text.ready", handler)
```

**常用事件**：

| 事件名称 | 发布者 | 订阅者 | 数据格式 |
|---------|--------|--------|---------|
| `perception.raw_data.generated` | InputProvider | InputLayer | `{"data": RawData, "source": str}` |
| `normalization.text.ready` | InputLayer | CanonicalMessage | `{"normalized": NormalizedText, ...}` |
| `understanding.intent_generated` | UnderstandingLayer | ExpressionGenerator | `{"intent": Intent, ...}` |
| `expression.parameters_generated` | ExpressionGenerator | OutputProviderManager | `{"parameters": RenderParameters, ...}` |

**与旧服务注册的对比**：

| 特性 | 服务注册（旧） | EventBus（新） |
|------|---------------|-------------|
| **通信方式** | 直接调用 | 发布-订阅 |
| **依赖关系** | 运行时绑定 | 解耦 |
| **可测试性** | 需要 mock AmaidesuCore | 易于 mock |
| **错误隔离** | 一个失败可能影响其他 | 错误隔离 |

---

## 🔄 第三步：理解迁移变化

### 3.1 从 BasePlugin 到 Plugin

| 变化点 | 旧方式 | 新方式 |
|--------|--------|--------|
| **继承** | 继承 AmaidesuCore | Protocol 接口（不继承） |
| **依赖注入** | 通过 self.core 访问 | 通过 event_bus 和 config 依赖注入 |
| **服务调用** | `self.core.get_service("tts")` | `event_bus.emit("tts.request", ...)` |
| **服务注册** | `self.core.register_service("tts", self)` | 直接提供 Provider |
| **返回值** | 无 | `setup()` 返回 Provider 列表 |
| **通信方式** | 服务注册 | EventBus 发布-订阅 |

**迁移示例**：

**旧代码（BasePlugin）**：
```python
class MyPlugin(BasePlugin):
    async def setup(self):
        # 注册服务
        self.core.register_service("my_service", self)
        
        # 调用其他插件服务
        tts = self.core.get_service("tts")
        tts.speak("Hello")
```

**新代码（Plugin）**：
```python
class MyPlugin:
    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        # 创建并返回 Provider
        provider = MyOutputProvider(config)
        await provider.setup(event_bus)
        return [provider]
    
    async def on_event(self, event_name, event_data):
        # 通过 EventBus 处理事件
        if event_name == "tts.request":
            await self._handle_tts_request(event_data)
```

### 3.2 AmaidesuCore 职责变化

**删除的代码**（约 500 行）：
```python
# 旧 AmaidesuCore 包含这些代码：
- WebSocket 连接管理（~100 行）
- HTTP 服务器管理（~200 行）
- maim_message.Router 相关（~100 行）
- send_to_maicore() 方法（~50 行）
- _handle_maicore_message() 方法（~50 行）
```

**新 AmaidesuCore（341 行）**：
```python
class AmaidesuCore:
    # 保留的职责
    def __init__(self, platform, pipeline_manager, context_manager,
                 event_bus, avatar, llm_service,
                 decision_manager, output_provider_manager, expression_generator):
        # 核心协调器
        self.pipeline_manager = pipeline_manager
        self.context_manager = context_manager
        self.event_bus = event_bus
        self._avatar = avatar
        self._llm_service = llm_service
        
        # Phase 3 新增
        self._decision_manager = decision_manager  # 决策层管理
        
        # Phase 4 新增
        self._output_provider_manager = output_provider_manager  # 输出层管理
        self.expression_generator = expression_generator  # 表现生成
    
    # 属性访问
    @property
    def event_bus(self) -> Optional[EventBus]:
        return self._event_bus
    
    @property
    def avatar(self) -> Optional["AvatarControlManager"]:
        return self._avatar
```

### 3.3 6 层架构对比

| 方面 | 旧架构 | 新架构 |
|------|--------|--------|
| **数据流** | 插件 → AmaidesuCore → MaiCore → 插件 | 6 层清晰的数据流 |
| **职责分离** | 混合（核心功能也作为插件） | 6 层职责清晰 |
| **依赖管理** | 服务注册（强依赖） | EventBus（松耦合） |
| **可替换性** | 决策层固定 | 决策层可替换 |
| **并发性** | 单线程 | 输入/输出层支持并发 |

---

## 📚 第四步：实际开发指南

### 4.1 如何开发新插件

**开发新插件步骤**：

1. **创建插件目录结构**：
   ```bash
   src/plugins/my_plugin/
   ├── __init__.py
   ├── plugin.py
   ├── providers/
   │   ├── __init__.py
   │   └── my_provider.py
   └── config-template.toml
   ```

2. **实现 Plugin 类**（`src/plugins/my_plugin/plugin.py`）：
   ```python
   from typing import List
   from src.core.plugin import Plugin
   from src.core.event_bus import EventBus
   from src.core.providers.input_provider import InputProvider
   from src.utils.logger import get_logger
   
   plugin_entrypoint = MyPlugin  # 必须有这行
   
   class MyPlugin:
       def __init__(self, config: dict):
           self.config = config
           self.logger = get_logger("MyPlugin")
       
       async def setup(self, event_bus: EventBus, config: dict) -> List[Any]:
           # 初始化 Provider
           providers = []
           # ... 创建和配置 Provider
           return providers
       
       async def cleanup(self):
           # 清理资源
           pass
       
       def get_info(self) -> dict:
           return {"name": "MyPlugin", "version": "1.0.0", ...}
   ```

3. **实现 Provider 类**（`src/plugins/my_plugin/providers/my_provider.py`）：
   ```python
   from src.core.providers.input_provider import InputProvider
   from src.utils.logger import get_logger
   
   class MyProvider(InputProvider):
       def __init__(self, config: dict):
           self.config = config
           self.logger = get_logger("MyProvider")
       
       async def start(self):
           # 启动数据流
           ...
       
       async def stop(self):
           # 停止数据流
           ...
       
       async def cleanup(self):
           # 清理资源
           self.logger.info("MyProvider 清理完成")
   ```

4. **创建配置文件**（`config-template.toml`）：
   ```toml
   # MyPlugin 配置
   
   [my_plugin]
   enabled = true
   api_url = "https://api.example.com"
   
   [my_plugin.input]
   enabled = true
   data_source = "local"
   ```

5. **测试插件**：
   ```bash
   # 运行项目
   python main.py
   
   # 查看日志
   # 查找 "插件加载成功" 的日志
   ```

### 4.2 如何调试和测试

**调试技巧**：

1. **查看日志输出**：
   ```bash
   # 启用调试模式
   python main.py --debug
   
   # 过滤特定插件的日志
   python main.py --filter MyPlugin
   ```

2. **查看事件流**：
   - 在关键位置添加 `self.logger.debug()` 和 `self.logger.info()`
   - 观察 EventBus 事件的发布和订阅

3. **使用测试文件**：
   - 查看目录结构：`src/plugins/my_plugin/`
   - 查看 Provider 接口定义：`src/core/providers/`
   - 查看设计文档：`refactor/design/`

**测试流程**：
```
1. 编写插件代码
2. 运行 python main.py
3. 观察日志输出
4. 修复错误
5. 重复测试
```

### 4.3 常见问题解答

**Q1: 新插件和旧插件有什么区别？**

**A**: 
- 旧插件（BasePlugin）：继承 AmaidesuCore，通过 self.core 访问核心功能
- 新插件（Plugin）：不继承，通过 event_bus 和 config 依赖注入
- 新插件更灵活、更容易测试、社区开发者更容易上手

**Q2: 什么是 Provider？**

**A**: Provider 是标准化的原子能力接口：
- InputProvider：输入能力（如接收弹幕、控制台输入）
- OutputProvider：输出能力（如 TTS、字幕显示）
- DecisionProvider：决策能力（如与 MaiCore 通信、本地 LLM）

**Q3: 为什么用 EventBus 而不是服务注册？**

**A**: 
- 服务注册（旧）：强依赖，调用方直接持有被调用方的引用
- EventBus（新）：松耦合，通过事件通信，更容易测试和扩展
- EventBus 支持错误隔离（一个处理器失败不影响其他）

**Q4: 如何理解 6 层架构？**

**A**: 想象一条"生产线"：
- Layer 1：原材料采集（输入）
- Layer 2：原材料预处理（标准化）
- Layer 3：标准化零件组装（中间表示）
- 决策层：决策如何使用这些零件
- Layer 5：生成使用说明书（理解）
- Layer 6：生成生产计划（参数）
- Layer 7：执行生产计划（渲染）

**Q5: 决策层为什么可替换？**

**A**: 
- 默认实现：MaiCoreDecisionProvider（使用 MaiCore 的 WebSocket）
- 可选实现：LocalLLMDecisionProvider（使用本地 LLM）
- 可选实现：RuleEngineDecisionProvider（规则引擎）
- 通过配置可以切换不同的决策引擎

---

## 📖 推荐学习资源

### 5.1 必读文档（按优先级）

| 优先级 | 文档 | 内容 | 阅读时间 |
|--------|------|------|----------|
| **P0** | `refactor/design/overview.md` | 架构总览，了解整体思路 | 5 分钟 |
| **P0** | `refactor/design/plugin_system.md` | 插件系统设计，Plugin 和 Provider 接口 | 10 分钟 |
| **P1** | `refactor/design/layer_refactoring.md` | 6 层架构详细设计 | 10 分钟 |
| **P1** | `src/core/plugin.py` | Plugin 接口定义（代码注释） | 5 分钟 |

### 5.2 学习路径

**第一阶段（30 分钟）**：
1. 阅读 `refactor/design/overview.md` - 理解架构总览
2. 阅读 `refactor/design/plugin_system.md` - 了解 Plugin 和 Provider
3. 查看实际插件示例：
   - `src/plugins/console_input/plugin.py` - 简单输入插件
   - `src/plugins/subtitle/plugin.py` - 简单输出插件

**第二阶段（1 小时）**：
4. 阅读 `refactor/design/layer_refactoring.md` - 了解 6 层详细设计
5. 查看更多插件示例：
   - `src/plugins/bili_danmaku/plugin.py` - B站弹幕输入
   - `src/plugins/tts/plugin.py` - TTS 输出
6. 尝试运行项目：`python main.py`，观察日志输出

**第三阶段（按需）**：
7. 阅读 `src/core/providers/*.py` - Provider 接口定义
8. 阅读 `src/perception/` - 输入层实现
9. 阅读 `src/expression/` - 输出层实现

### 5.3 快速参考

**6 层数据流速查表**：

```
Layer 1 (输入感知) → Layer 2 (输入标准化) → Layer 3 (中间表示) → 
Layer 4 决策层 → Layer 5 (表现理解) → Layer 6 (表现生成) → Layer 7 (渲染呈现)
```

**关键接口速查表**：

```python
# Plugin 协议
Plugin.__init__(config)
Plugin.setup(event_bus, config) -> List[Provider]
Plugin.cleanup()
Plugin.get_info() -> dict

# InputProvider 接口
InputProvider.__init__(config)
InputProvider.start() -> AsyncIterator[RawData]
InputProvider.stop()
InputProvider.cleanup()

# OutputProvider 接口
OutputProvider.__init__(config)
OutputProvider.setup(event_bus)
OutputProvider.render(parameters)
OutputProvider.cleanup()
```

---

## 🎯 快速开始

### 5 分钟快速了解

**新架构一句话总结**：
> **6 层清晰数据流 + Provider 标准化接口 + Plugin 聚合 + EventBus 松耦合**

**核心数据流**：
```
外部输入 → Layer 1 → Layer 2 → Layer 3 → Layer 4 决策层 → Layer 5 → Layer 6 → Layer 7 → 最终输出
```

**三个核心概念**：
- **Provider**：原子能力接口（输入/输出/决策）
- **Plugin**：聚合多个 Provider，社区开发入口
- **EventBus**：发布-订阅模式，实现松耦合

---

## 🆘 需要帮助？

### 遇到问题时

1. **代码问题**：查看实际插件示例代码
2. **架构理解问题**：查看设计文档
3. **如何开发**：查看 `src/plugins/console_input/plugin.py` 简单示例
4. **测试和调试**：查看日志输出

### 联系方式

- **代码审查**：查看 `src/plugins/` 下的实际插件代码
- **设计文档**：`refactor/design/` 目录
- **架构分析**：`refactor/architecture_consistency_analysis.md`（已生成）

---

## 📝 总结

**旧架构**：
- AmaidesuCore 包含 WebSocket/HTTP/路由（641 行）
- 所有东西都叫"插件"（24 个）
- 通过服务注册相互调用（强依赖）

**新架构**：
- AmaidesuCore 简化到 341 行
- 6 层清晰数据流
- Provider 标准化接口（输入/输出/决策）
- Plugin 聚合多个 Provider
- EventBus 松耦合通信

**关键改进**：
- ✅ AmaidesuCore 减少 6.6%
- ✅ 消除依赖地狱（EventBus 代替服务注册）
- ✅ 决策层可替换
- ✅ 多 Provider 并发支持
- ✅ 社区开发者更容易上手

**学习建议**：
1. 先从简单插件开始（console_input, subtitle）
2. 理解 6 层数据流
3. 理解 Plugin 和 Provider 接口
4. 阅读设计文档
5. 实际运行和调试

---

**文档创建时间**: 2026-01-31
**版本**: 1.0
**状态**: 新协作者入门指南
