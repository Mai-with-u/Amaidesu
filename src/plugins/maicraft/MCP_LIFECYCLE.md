# MCP 连接生命周期管理

## 问题

之前 MCP Client 可能在模块导入时就自动连接，导致即使插件未启用也会连接到 Minecraft 服务器。

## 解决方案

使用**延迟初始化**模式，确保只有在需要时才连接。

---

## 连接生命周期

### 阶段 1：插件加载

```python
# config.toml
factory_type = "mcp"  # 或 "log"
enabled = true        # 插件是否启用
```

当插件加载时：
```python
class MaicraftPlugin:
    def __init__(self, core, plugin_config):
        # ✅ 仅创建工厂实例，不连接
        if factory_type == "mcp":
            self.action_factory = McpActionFactory()  # 不连接
        elif factory_type == "log":
            self.action_factory = LogActionFactory()  # 不连接
```

**此时：MCP Client 尚未连接** ✅

---

### 阶段 2：插件启用 (setup)

```python
class MaicraftPlugin:
    async def setup(self):
        if not self.enabled:
            return  # ❌ 插件未启用，跳过
        
        # ✅ 只有启用时才初始化工厂
        success = await self.action_factory.initialize()
```

**此时才会连接！**

```python
class McpActionFactory:
    async def initialize(self):
        # ✅ 创建 MCP Client
        self.mcp_client = MCPClient()
        
        # ✅ 连接到 MCP Server
        connected = await self.mcp_client.connect()
        
        if connected:
            self.logger.info("✅ MCP Server 连接成功")
            return True
```

**如果 `enabled = false` 或 `factory_type = "log"`，则不会连接** ✅

---

### 阶段 3：使用动作

```python
class McpActionFactory:
    def create_chat_action(self):
        if not self.initialized:
            raise RuntimeError("工厂未初始化")
        
        action = McpChatAction()
        # ✅ 注入已连接的客户端
        action.mcp_client = self.mcp_client
        return action
```

动作使用已连接的客户端：
```python
class McpChatAction:
    async def execute(self, params):
        # ✅ 使用已连接的客户端
        await self.mcp_client.call_tool("chat", {...})
```

---

### 阶段 4：插件禁用 (cleanup)

```python
class MaicraftPlugin:
    async def cleanup(self):
        # ✅ 清理工厂，断开连接
        await self.action_factory.cleanup()
```

```python
class McpActionFactory:
    async def cleanup(self):
        if self.mcp_client:
            # ✅ 断开 MCP Server 连接
            await self.mcp_client.disconnect()
            self.mcp_client = None
```

**连接已断开** ✅

---

## 配置示例

### 场景 1：使用 Log 工厂（不连接 MCP）

```toml
[maicraft]
enabled = true
factory_type = "log"  # ✅ 使用 Log 工厂
```

**结果：**
- ✅ 插件启用
- ✅ 使用 LogActionFactory
- ✅ **不会连接** MCP Server
- ✅ 动作输出到日志

---

### 场景 2：使用 MCP 工厂（连接 MCP）

```toml
[maicraft]
enabled = true
factory_type = "mcp"  # ✅ 使用 MCP 工厂
```

**结果：**
- ✅ 插件启用
- ✅ 使用 McpActionFactory
- ✅ **连接** MCP Server
- ✅ 动作通过 MCP 执行

---

### 场景 3：插件禁用

```toml
[maicraft]
enabled = false
factory_type = "mcp"  # 无关紧要
```

**结果：**
- ❌ 插件不启用
- ❌ **不会连接** MCP Server
- ❌ 不会创建任何动作

---

## 工作流程图

```
启动应用
    ↓
加载插件配置
    ↓
创建 MaicraftPlugin
    ↓
创建工厂实例 (不连接)
    ├─ factory_type="log" → LogActionFactory
    └─ factory_type="mcp" → McpActionFactory (未连接)
    ↓
检查 enabled
    ├─ false → ❌ 跳过 setup()
    └─ true → ✅ 调用 setup()
        ↓
    调用 factory.initialize()
        ├─ LogActionFactory → ✅ 无需连接
        └─ McpActionFactory → ✅ 连接 MCP Server
            ↓
        创建动作时注入客户端
            ↓
        动作使用已连接的客户端
            ↓
        插件禁用时
            ↓
        调用 factory.cleanup()
            ↓
        断开 MCP Server 连接
```

---

## 关键代码位置

### 1. 工厂初始化（连接点）

**文件：** `factories/mcp_factory.py`

```python
async def initialize(self) -> bool:
    """✅ 只在这里连接"""
    self.mcp_client = MCPClient()
    connected = await self.mcp_client.connect()
    return connected
```

### 2. 工厂清理（断开点）

```python
async def cleanup(self):
    """✅ 只在这里断开"""
    if self.mcp_client:
        await self.mcp_client.disconnect()
        self.mcp_client = None
```

### 3. 插件生命周期

**文件：** `plugin.py`

```python
async def setup(self):
    """插件启用时调用"""
    if not self.enabled:
        return  # ❌ 未启用，不初始化
    
    # ✅ 只有启用时才初始化（连接）
    await self.action_factory.initialize()

async def cleanup(self):
    """插件禁用时调用"""
    # ✅ 断开连接
    await self.action_factory.cleanup()
```

---

## 优势

### ✅ 按需连接
- 只有在真正需要时才连接
- 节省资源

### ✅ 配置灵活
- 可以通过配置切换工厂
- 可以禁用插件而不影响其他部分

### ✅ 生命周期清晰
- 连接和断开的时机明确
- 易于调试和维护

### ✅ 资源管理
- 正确的连接清理
- 避免资源泄漏

---

## 测试场景

### 测试 1：插件禁用

```toml
enabled = false
```

**预期：** 不应该看到任何 MCP 连接日志

---

### 测试 2：使用 Log 工厂

```toml
enabled = true
factory_type = "log"
```

**预期：**
- ✅ 看到 "使用动作工厂: log"
- ❌ 不应该看到 MCP 连接日志
- ✅ 命令执行输出到日志

---

### 测试 3：使用 MCP 工厂

```toml
enabled = true
factory_type = "mcp"
```

**预期：**
- ✅ 看到 "开始初始化 MCP 工厂..."
- ✅ 看到 "正在连接到 MCP Server..."
- ✅ 看到 "✅ MCP Server 连接成功"
- ✅ 命令通过 MCP 执行

---

### 测试 4：插件重启

```python
# 禁用插件
await plugin.cleanup()
# 预期：看到 "正在断开 MCP Server 连接..."

# 重新启用
await plugin.setup()
# 预期：重新连接
```

---

## 故障排除

### 问题：即使使用 log 工厂仍然连接 MCP

**可能原因：**
- 配置文件中 `factory_type` 设置错误
- 代码中有多余的连接调用

**检查：**
```bash
# 查看日志
grep "MCP" logs/maicraft/plugin.log
```

**应该看到：**
- factory_type="log" → 无 MCP 日志
- factory_type="mcp" → 有连接日志

---

### 问题：MCP 连接失败

**检查顺序：**
1. MCP Server 是否运行？
2. 配置文件是否正确？
3. 网络是否正常？

**日志位置：**
```
logs/maicraft/plugin.log
logs/maicraft/plugin_error.log
```

---

## 总结

通过**延迟初始化**模式，我们确保：

1. ✅ MCP Client 只在需要时才连接
2. ✅ 配置 `enabled=false` 或 `factory_type="log"` 时不会连接
3. ✅ 连接生命周期与插件生命周期绑定
4. ✅ 正确的资源清理

这是一个健壮且高效的设计！🎉

