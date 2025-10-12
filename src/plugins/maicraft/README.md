# Maicraft 插件

基于**抽象工厂模式**的弹幕互动游戏插件。

## 设计架构

### 核心设计模式：抽象工厂模式（Abstract Factory Pattern）

插件采用抽象工厂模式，允许通过配置切换整套动作实现，而不需要修改代码。

```
┌─────────────────────────────────────────────────────────────┐
│                     MaicraftPlugin                          │
│  - 解析命令                                                  │
│  - 根据配置选择工厂                                          │
│  - 通过工厂创建动作                                          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ├──────────────┐
                            ▼              ▼
                  ┌──────────────┐  ┌──────────────┐
                  │ LogFactory   │  │ McpFactory   │
                  │ (日志实现)   │  │ (MCP实现)    │
                  └──────────────┘  └──────────────┘
                         │                  │
         ┌───────────────┼────────┐         ├───────────────┐
         ▼               ▼        ▼         ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │LogChat   │   │LogAttack │   │McpChat   │   │McpAttack │
  │Action    │   │Action    │   │Action    │   │Action    │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘
       │              │               │              │
       └──────────────┴───────────────┴──────────────┘
                      │
                      ▼
            ┌──────────────────┐
            │  动作接口层      │
            │  - IChatAction   │
            │  - IAttackAction │
            └──────────────────┘
```

## 目录结构

```
maicraft/
├── actions/                      # 动作接口和实现
│   ├── action_interfaces.py     # 动作接口定义（IAction、IChatAction、IAttackAction）
│   └── impl/                     # 动作具体实现
│       ├── log/                  # Log 系列实现
│       │   ├── chat_action.py   # Log 聊天动作
│       │   ├── attack_action.py # Log 攻击动作
│       │   └── __init__.py
│       ├── mcp/                  # MCP 系列实现
│       │   ├── chat_action.py   # MCP 聊天动作
│       │   ├── attack_action.py # MCP 攻击动作
│       │   └── __init__.py
│       └── __init__.py
├── factories/                    # 工厂模块
│   ├── abstract_factory.py      # 抽象工厂接口
│   ├── log_factory.py           # Log 工厂实现
│   ├── mcp_factory.py           # MCP 工厂实现
│   └── __init__.py
├── action_types.py              # 动作类型枚举
├── action_registry.py           # 命令到动作类型的映射
├── command_parser.py            # 命令解析器
├── command.py                   # 命令数据结构
├── plugin.py                    # 插件主逻辑
└── config.toml                  # 配置文件
```

## 核心概念

### 1. 动作接口（Action Interfaces）

定义抽象的动作类型，规定参数和行为接口：
- `IChatAction`: 聊天动作接口（参数：message）
- `IAttackAction`: 攻击动作接口（参数：mob_name）

### 2. 抽象工厂（Abstract Factory）

`AbstractActionFactory` 定义创建所有动作类型的接口：
```python
class AbstractActionFactory(ABC):
    @abstractmethod
    def create_chat_action(self) -> IChatAction: ...
    
    @abstractmethod
    def create_attack_action(self) -> IAttackAction: ...
```

### 3. 具体工厂（Concrete Factories）

#### LogActionFactory
创建一整套"仅打印日志"的动作实现，用于测试和调试。

#### McpActionFactory
创建一整套"通过 MCP Server"的动作实现，用于真实的游戏控制。

### 4. 动作类型枚举（ActionType）

定义所有可用的抽象动作类型：
```python
class ActionType(Enum):
    CHAT = "chat"      # 聊天动作
    ATTACK = "attack"  # 攻击动作
```

### 5. 命令映射

命令名称映射到抽象的动作类型，而不是具体实现：
```toml
[command_mappings]
chat = "chat"      # chat 命令 -> CHAT 动作类型
say = "chat"       # say 命令 -> CHAT 动作类型
attack = "attack"  # attack 命令 -> ATTACK 动作类型
```

## 配置说明

### 切换动作实现系列

通过配置 `factory_type` 来切换整套动作实现：

```toml
# 使用 Log 实现（仅打印日志）
factory_type = "log"

# 或使用 MCP 实现（真实游戏控制）
factory_type = "mcp"
```

切换工厂后，所有动作（聊天、攻击等）都会自动使用对应的实现。

### 命令映射配置

```toml
[command_mappings]
# 多个命令可以映射到同一个动作类型
chat = "chat"
say = "chat"
whisper = "chat"

attack = "attack"
hit = "attack"
```

## 添加新动作

### 1. 定义动作接口

在 `action_interfaces.py` 中定义新的动作接口：

```python
class IMineAction(IAction):
    """挖矿动作接口（参数：block_type）"""
    pass
```

### 2. 添加动作类型枚举

在 `action_types.py` 中添加枚举值：

```python
class ActionType(Enum):
    CHAT = "chat"
    ATTACK = "attack"
    MINE = "mine"  # 新增
```

### 3. 扩展抽象工厂

在 `abstract_factory.py` 中添加创建方法：

```python
class AbstractActionFactory(ABC):
    @abstractmethod
    def create_mine_action(self) -> IMineAction: ...
```

### 4. 实现具体动作

在 `impl/log/mine_action.py` 和 `impl/mcp/mine_action.py` 中分别实现：

```python
# impl/log/mine_action.py
class LogMineAction(IMineAction):
    async def execute(self, params: Dict[str, Any]) -> bool:
        block_type = params.get("block_type")
        self.logger.info(f"[MAICRAFT-MINE] 挖掘方块: {block_type}")
        return True
```

### 5. 更新 impl 模块的 __init__.py

在 `impl/log/__init__.py` 和 `impl/mcp/__init__.py` 中导出新动作：

```python
# impl/log/__init__.py
from .mine_action import LogMineAction
```

### 6. 更新工厂实现

在 `log_factory.py` 和 `mcp_factory.py` 中实现创建方法：

```python
from ..actions.impl.log import LogMineAction

class LogActionFactory(AbstractActionFactory):
    def create_mine_action(self) -> IMineAction:
        return LogMineAction()
```

### 7. 更新插件逻辑

在 `plugin.py` 的 `_create_action()` 和 `_prepare_action_params()` 中添加对应逻辑。

### 8. 更新配置

在 `config.toml` 中添加命令映射：

```toml
[command_mappings]
mine = "mine"
dig = "mine"
```

## 添加新工厂

如果要添加新的实现系列（如 WebSocket 实现），需要：

### 1. 创建新的动作实现

在 `impl/websocket/` 目录下创建各个动作文件：

```python
# impl/websocket/chat_action.py
class WebSocketChatAction(IChatAction):
    async def execute(self, params):
        # WebSocket 实现
        pass
```

### 2. 创建新工厂

```python
# websocket_factory.py
class WebSocketActionFactory(AbstractActionFactory):
    def create_chat_action(self) -> IChatAction:
        return WebSocketChatAction()
    
    def create_attack_action(self) -> IAttackAction:
        return WebSocketAttackAction()
```

### 3. 在插件中注册

在 `plugin.py` 的 `_initialize_factory()` 中添加：

```python
elif factory_type == "websocket":
    self.action_factory = WebSocketActionFactory()
```

### 4. 更新配置

```toml
factory_type = "websocket"
```

## 优势

1. **易于扩展**：添加新动作只需实现接口，所有工厂自动支持
2. **易于切换**：通过配置即可切换整套实现，无需修改代码
3. **解耦合**：命令、动作类型、具体实现三者完全解耦
4. **易于测试**：可以轻松切换到 Log 实现进行测试
5. **统一接口**：所有实现遵循相同的接口规范

## 工作流程

1. 用户发送弹幕命令：`chat 你好`
2. `CommandParser` 解析命令：`Command(name="chat", args=["你好"])`
3. `ActionRegistry` 查找映射：`"chat" -> ActionType.CHAT`
4. `Plugin` 通过当前工厂创建动作：`factory.create_chat_action()`
5. 准备参数：`{"message": "你好"}`
6. 执行动作：`action.execute(params)`
7. 动作实现执行具体逻辑（打印日志 或 调用 MCP Server）

## 当前状态

- ✅ 抽象工厂架构已完成
- ✅ Log 系列实现已完成（聊天、攻击）
- ✅ MCP 系列框架已完成（需补充 MCP Server 调用逻辑）
- ⚠️ MCP Server 连接逻辑待实现（标记为 TODO）

## 待办事项

- [ ] 实现 MCP Server 连接和通信逻辑
- [ ] 添加更多动作类型（移动、挖掘、建造等）
- [ ] 添加动作执行结果反馈机制
- [ ] 添加动作队列和调度系统
