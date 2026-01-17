# AmaidesuCore 重构分析

## 📊 当前 AmaidesuCore 的职责

### 外部通信管理（约30%代码）

| 方法 | 职责 | 应该去哪里？ |
|------|------|--------------|
| `_setup_router()` | 配置maim_message Router | ❌ 删除 |
| `_run_websocket()` | 运行WebSocket连接 | ❌ 删除 |
| `_monitor_ws_connection()` | 监控连接状态 | ❌ 删除 |
| `_setup_http_server()` / `_start_http_server_internal()` | HTTP服务器 | ❌ 删除 |
| `send_to_maicore()` | 发送消息到MaiCore | ❌ 删除 |
| `_handle_maicore_message()` | 接收MaiCore消息 | ❌ 删除 |
| `register_websocket_handler()` | 注册WebSocket处理器 | ❌ 删除 |
| `register_http_handler()` | 注册HTTP处理器 | ❌ 删除 |

### 内部协调（约40%代码）

| 方法/属性 | 职责 | 应该保留？ |
|------------|------|------------|
| `event_bus` | EventBus管理器 | ✅ 保留 |
| `avatar` | 虚拟形象管理器 | ✅ 保留 |
| `get_context_manager()` | 上下文管理器 | ✅ 保留 |
| `llm_client_manager` / `get_llm_client()` | LLM客户端管理器 | ✅ 保留 |
| `register_service()` / `get_service()` | 服务注册系统 | ✅ 简化后保留 |
| `__init__` | 初始化所有组件 | ✅ 简化后保留 |

### 生命周期管理（约20%代码）

| 方法 | 职责 | 应该去哪里？ |
|------|------|--------------|
| `connect()` | 启动WebSocket和HTTP服务器 | ⚠️ 简化为启动内部组件 |
| `disconnect()` | 断开连接和停止服务器 | ⚠️ 简化为停止内部组件 |

---

## 🎯 两种重构方案对比

### 方案1：AmaidesuCore完全解耦（推荐）

**设计理念**：AmaidesuCore只负责内部协调，外部通信交给DecisionProvider

#### AmaidesuCore的职责变化

**删除职责（约500行代码）**：
```python
# ❌ 删除以下所有代码
- maim_message.Router相关（_setup_router、_router）
- WebSocket连接管理（_run_websocket、_monitor_ws_connection、_ws_task）
- HTTP服务器管理（_setup_http_server、_start_http_server_internal）
- send_to_maicore()方法
- _handle_maicore_message()方法
- WebSocket/HTTP处理器注册系统（register_websocket_handler、register_http_handler）
- aiohttp导入和相关依赖
```

**保留职责（约300行代码）**：
```python
# ✅ 保留以下所有代码
- EventBus管理（event_bus属性）
- PipelineManager管理（pipeline_manager属性）
- ContextManager管理（get_context_manager()方法）
- Avatar管理器（avatar属性）
- LLM客户端管理器（llm_client_manager属性）
- 服务注册系统（register_service、get_service，但只用于内部组件）
- 简化的生命周期管理
```

**新增职责**：
```python
# ✅ 新增DecisionManager集成
- decision_manager属性
- get_decision_manager()方法
```

#### 新的AmaidesuCore代码结构

```python
from typing import Callable, Dict, Any, Optional, TYPE_CHECKING

from src.utils.logger import get_logger
from src.core.pipeline_manager import PipelineManager
from src.core.context_manager import ContextManager
from src.core.event_bus import EventBus
from src.core.decision_manager import DecisionManager

if TYPE_CHECKING:
    from src.core.avatar.avatar_manager import AvatarControlManager
    from src.core.llm_client_manager import LLMClientManager


class AmaidesuCore:
    """
    Amaidesu 核心模块 - 负责内部协调和组件管理。

    重构后职责：
    - EventBus管理和事件分发
    - Pipeline管理
    - Context管理
    - Avatar管理
    - LLM客户端管理
    - 内部服务注册

    不再负责：
    - 与MaiCore的WebSocket连接（交给DecisionProvider）
    - HTTP服务器（交给DecisionProvider）
    - 消息发送/接收（通过EventBus）
    """

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例"""
        return self._event_bus

    @property
    def avatar(self) -> Optional["AvatarControlManager"]:
        """获取虚拟形象控制管理器实例"""
        return self._avatar

    @property
    def decision_manager(self) -> Optional[DecisionManager]:
        """获取决策管理器实例"""
        return self._decision_manager

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        avatar: Optional["AvatarControlManager"] = None,
        llm_client_manager: Optional["LLMClientManager"] = None,
    ):
        """
        初始化 Amaidesu Core。

        注意：不再接收maicore_host、maicore_port等外部通信参数
        """
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始（重构后架构）")

        # 内部组件管理
        self._event_bus = event_bus
        self._pipeline_manager = pipeline_manager
        self._context_manager = context_manager or ContextManager({})
        self._avatar = avatar
        self._llm_client_manager = llm_client_manager

        # 决策管理器（新增）
        self._decision_manager = None

        # 服务注册（仅用于内部组件）
        self._services: Dict[str, Any] = {}

        # WebSocket处理器注册（仅用于内部组件）
        self._message_handlers: Dict[str, list[Callable]] = {}

        self.logger.info("AmaidesuCore 初始化完成（内部协调模式）")

    def register_service(self, name: str, service_instance: Any):
        """
        注册一个服务实例（仅用于内部组件）。

        注意：外部通信相关的服务已经迁移到DecisionProvider
        """
        if name in self._services:
            self.logger.warning(f"服务名称 '{name}' 已被注册，将被覆盖！")
        self._services[name] = service_instance
        self.logger.info(f"服务已注册: '{name}' (类型: {type(service_instance).__name__})")

    def get_service(self, name: str) -> Optional[Any]:
        """
        根据名称获取已注册的服务实例。

        注意：外部通信相关的服务已经迁移到DecisionProvider
        """
        service = self._services.get(name)
        if service:
            self.logger.debug(f"获取服务 '{name}' 成功。")
        else:
            self.logger.warning(f"尝试获取未注册的服务: '{name}'")
        return service

    def register_websocket_handler(self, message_type_or_key: str, handler: Callable):
        """
        注册一个WebSocket消息处理器（仅用于内部组件）。

        注意：这里只注册到内部EventBus，不再直接与MaiCore通信
        """
        # 通过EventBus订阅事件
        event_name = f"decision.response_generated"
        if isinstance(handler, asyncio.coroutine):
            self._event_bus.on(event_name, handler)
        else:
            # 包装为异步函数
            async def wrapper(event):
                return handler(event)
            self._event_bus.on(event_name, wrapper)

        self.logger.info(f"成功注册消息处理器: Key='{message_type_or_key}', Handler='{handler.__name__}'")

    def set_decision_manager(self, decision_manager: DecisionManager):
        """
        设置决策管理器

        Args:
            decision_manager: 决策管理器实例
        """
        self._decision_manager = decision_manager
        self.logger.info("决策管理器已设置")

    async def start(self):
        """启动所有内部组件"""
        self.logger.info("启动AmaidesuCore内部组件...")

        # 启动管道管理器（如果存在）
        if self._pipeline_manager:
            try:
                await self._pipeline_manager.start()
                self.logger.info("管道管理器已启动")
            except Exception as e:
                self.logger.error(f"启动管道管理器失败: {e}", exc_info=True)

    async def stop(self):
        """停止所有内部组件"""
        self.logger.info("停止AmaidesuCore内部组件...")

        # 停止管道管理器（如果存在）
        if self._pipeline_manager:
            try:
                await self._pipeline_manager.stop()
                self.logger.info("管道管理器已停止")
            except Exception as e:
                self.logger.error(f"停止管道管理器失败: {e}", exc_info=True)
```

#### DecisionProvider的职责

每个DecisionProvider自己管理外部通信：

```python
class MaiCoreDecisionProvider:
    """MaiCore决策Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.router = None
        self.logger = get_logger("MaiCoreDecisionProvider")

    async def setup(self, event_bus: EventBus, config: dict):
        """初始化WebSocket连接（自己管理！）"""
        from maim_message import Router, RouteConfig, TargetConfig

        ws_url = f"ws://{config.get('host', 'localhost')}:{config.get('port', 8000)}/ws"

        route_config = RouteConfig(
            route_config={
                "amaidesu": TargetConfig(
                    url=ws_url,
                    token=None
                )
            }
        )

        self.router = Router(route_config)
        self.router.register_class_handler(self._handle_maicore_message)

        # 订阅EventBus
        event_bus.on("canonical.message_ready", self._on_canonical_message)

        self.logger.info(f"MaiCore WebSocket连接已配置: {ws_url}")

        # 启动WebSocket连接
        self._ws_task = asyncio.create_task(self._run_websocket())

    async def _run_websocket(self):
        """运行WebSocket连接（自己管理！）"""
        try:
            await self.router.run()
        except asyncio.CancelledError:
            self.logger.info("WebSocket任务被取消")
        except Exception as e:
            self.logger.error(f"WebSocket异常: {e}", exc_info=True)

    async def _on_canonical_message(self, event: dict):
        """处理CanonicalMessage事件"""
        canonical_message = event.get("data")

        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore（自己管理！）
        await self.router.send_message(message)

    async def _handle_maicore_message(self, message_data: dict):
        """处理MaiCore返回的消息"""
        from maim_message import MessageBase
        message = MessageBase.from_dict(message_data)

        # 发布到EventBus
        await self.event_bus.emit("decision.response_generated", {
            "data": message
        })

    async def decide(self, canonical_message):
        """决策接口"""
        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore
        await self.router.send_message(message)

        # 等待响应（简化实现，实际应该用asyncio.Queue）
        # 响应会通过_handle_maicore_message回调

        return message

    def _build_messagebase(self, canonical_message):
        """构建MessageBase"""
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        # ... 构建逻辑

    async def cleanup(self):
        """清理资源"""
        if self._ws_task:
            self._ws_task.cancel()
        self.logger.info("MaiCore WebSocket连接已清理")
```

---

### 方案2：AmaidesuCore保留连接（不推荐）

**设计理念**：AmaidesuCore继续管理WebSocket，DecisionProvider只是包装

**问题**：
- ❌ 职责不清晰
- ❌ AmaidesuCore和MaiCore耦合
- ❌ 决策层替换不彻底

---

## 📊 两种方案对比

| 对比项           | 方案1（完全解耦） | 方案2（保留连接） |
| ---------------- | ---------------- | ------------------ |
| **职责清晰度**   | ✅ 非常清晰     | ❌ 不清晰          |
| **解耦程度**     | ✅ 完全解耦     | ❌ 部分耦合        |
| **决策层替换**   | ✅ 彻底替换     | ⚠️ 不彻底         |
| **改动工作量**   | ⚠️ 较大（重构AmaidesuCore） | ✅ 较小（包装现有代码） |
| **长期维护性**   | ✅ 优秀         | ❌ 较差           |
| **扩展性**       | ✅ 优秀         | ⚠️ 一般           |

---

## 🎯 推荐方案详解

### 为什么推荐方案1？

1. **职责清晰**：AmaidesuCore只负责内部协调，DecisionProvider负责外部通信
2. **彻底解耦**：AmaidesuCore不再依赖MaiCore
3. **真正的可替换**：任何DecisionProvider都可以替换MaiCore
4. **长期维护**：架构清晰，易于扩展

### 通信方式分析

#### 当前架构（固定使用maim_message）

```
AmaidesuCore ←→ MaiCore
    ↓
WebSocket (maim_message.Router)
    ↓
MessageBase对象
```

#### 新架构（支持多种通信方式）

```
AmaidesuCore ←→ DecisionManager ←→ DecisionProvider
                                    ↓
                          ┌───────┴───────┐
                          │                │
                MaiCoreDecisionProvider  LocalLLMDecisionProvider
                          │                │
                          ↓                ↓
                    WebSocket          HTTP API
                    (maim_message)     (OpenAI API)
```

### 通信方式总结

**回答你的第3个问题**：通信方式还是maim_message吗？

**回答**：**不完全是！**

- **MaiCoreDecisionProvider**：继续使用maim_message（WebSocket + MessageBase）
- **LocalLLMDecisionProvider**：使用HTTP API（如OpenAI API）
- **RuleEngineDecisionProvider**：本地处理，无需网络通信

**新架构的优势**：
1. ✅ 支持多种通信方式
2. ✅ DecisionProvider可以自由选择通信协议
3. ✅ AmaidesuCore不关心通信细节
4. ✅ 易于扩展新的决策方式

---

## 📝 重构工作量评估

### AmaidesuCore改动

**删除**：约500行代码
- WebSocket连接管理
- HTTP服务器管理
- maim_message.Router相关
- 外部通信相关方法

**修改**：约100行代码
- 移除maicore_host、maicore_port等参数
- 简化connect()/disconnect()
- 移除register_websocket_handler()等

**保留**：约300行代码
- EventBus、Pipeline、Context等内部组件管理

**新增**：约50行代码
- decision_manager属性
- set_decision_manager()方法

### 新增代码

**DecisionManager**：约200行
**MaiCoreDecisionProvider**：约300行
**LocalLLMDecisionProvider**：约200行（示例）

### 总体评估

| 项目            | 代码行数 | 说明                 |
| --------------- | -------- | -------------------- |
| AmaidesuCore删除 | -500     | 外部通信相关代码      |
| AmaidesuCore修改 | 100      | 简化接口             |
| AmaidesuCore保留 | 300      | 内部组件管理          |
| AmaidesuCore新增 | 50       | DecisionManager集成    |
| 新增DecisionManager | 200     | 决策管理器            |
| 新增DecisionProviders | 500     | MaiCore + 本地LLM示例 |
| **净变化**       | **650**  | 从642行增加到~1292行  |

---

## ✅ 总结

### 回答你的3个问题

1. **Core是不是需要大改？**
   - ✅ 是的，需要删除约500行外部通信代码
   - ✅ 简化为内部协调，职责更清晰

2. **还是说AmaidesuCore只需要改成和Provider通信？**
   - ✅ 基本正确，但不仅是"改成和Provider通信"
   - ✅ 是从"管理外部连接"变为"管理内部协调"
   - ✅ 外部通信完全交给DecisionProvider

3. **通信方式还是maim_message吗？**
   - ❌ 不完全是！
   - ✅ MaiCoreDecisionProvider继续使用maim_message
   - ✅ LocalLLMDecisionProvider使用HTTP API
   - ✅ 支持任意DecisionProvider自由选择通信协议

### 推荐方案

**推荐方案1（完全解耦）**，理由：
1. ✅ 职责清晰，架构合理
2. ✅ 彻底解耦，易于维护
3. ✅ 真正支持决策层替换
4. ✅ 支持多种通信方式
5. ✅ 长期维护性好

**缺点**：初始改动工作量较大，但长期收益明显。
