# ContextManager 移除归档文档

> **文档状态**: 历史归档
> **移除时间**: 2026-02-10
> **相关 Commit**: 1d9bc85 (重构), [当前移除]

---

## 目录

- [历史背景](#历史背景)
- [旧架构使用方式](#旧架构使用方式)
- [为什么不再需要](#为什么不再需要)
- [迁移方案](#迁移方案)
- [关键发现总结](#关键发现总结)

---

## 历史背景

### 起源

`ContextManager` 诞生于 **commit 1d9bc85** 的重构过程，当时将 `prompt_context` 插件重构为共享服务。原始设计意图是：

1. **动态上下文注册**: 允许插件在运行时注册可调用的异步函数
2. **延迟计算**: 上下文数据只在需要时才计算（lazy evaluation）
3. **全局访问**: 所有插件都能访问已注册的上下文提供者
4. **统一管理**: 集中管理所有 LLM 提示词的动态上下文

### 设计理念

在旧插件架构中，ContextManager 扮演了"上下文提供者注册表"的角色：

```python
# 旧架构的理想使用流程
read_pingmu.register_context("screen_content", lambda: get_screen_text())
bili_danmaku.register_context("recent_danmaku", lambda: get_recent_messages())

# LLM 调用时自动收集上下文
context = context_manager.get_all_contexts()
prompt = render_template(user_input, context)
```

### 实际问题

尽管设计意图良好，但实际使用中存在以下问题：

1. **运行时动态性**: 需要在插件启动时注册，增加了初始化复杂度
2. **隐式依赖**: 插件不知道谁在消费自己的上下文
3. **数据流不清晰**: 通过全局注册表间接传递数据，难以追踪
4. **过度抽象**: 实际上只有一个使用点，抽象层级过高

---

## 旧架构使用方式

### 1. 上下文提供者注册

以下是如何在旧插件架构中注册上下文的示例：

#### read_pingmu 插件（屏幕读取）

```python
# plugins/read_pingmu/plugin.py

from src.services.context_manager import get_context_manager

class ReadPingmuPlugin:
    def __init__(self):
        self.context_manager = get_context_manager()

    async def register_screen_context(self):
        """注册屏幕内容上下文提供者"""

        async def screen_content_provider():
            """返回当前屏幕内容的异步函数"""
            screen_text = await self.read_screen()
            return f"当前屏幕内容：\n{screen_text}"

        # 注册到全局上下文管理器
        self.context_manager.register_provider(
            name="screen_content",
            provider=screen_content_provider,
            description="提供当前游戏屏幕的文字内容"
        )

    async def on_enable(self):
        """插件启用时注册上下文"""
        await self.register_screen_context()
        logger.info("屏幕上下文已注册到 ContextManager")
```

#### bili_danmaku 插件（弹幕上下文）

```python
# plugins/bili_danmaku/plugin.py

from src.services.context_manager import get_context_manager

class BiliDanmakuPlugin:
    def __init__(self):
        self.context_manager = get_context_manager()
        self.recent_messages = []

    async def register_danmaku_context(self):
        """注册最近弹幕上下文提供者"""

        async def recent_danmaku_provider():
            """返回最近弹幕的异步函数"""
            recent = self.get_recent_messages(count=10)
            if not recent:
                return "最近没有弹幕"

            formatted = "\n".join([f"{m.user}: {m.text}" for m in recent])
            return f"最近弹幕：\n{formatted}"

        self.context_manager.register_provider(
            name="recent_danmaku",
            provider=recent_danmaku_provider,
            description="提供最近的弹幕消息"
        )

    async def on_message(self, message):
        """收到新消息时更新缓冲区"""
        self.recent_messages.append(message)
        if len(self.recent_messages) > 10:
            self.recent_messages.pop(0)
```

### 2. 上下文消费

上下文消费者通过 ContextManager 获取所有已注册的上下文：

```python
# plugins/maicore/plugin.py

from src.services.context_manager import get_context_manager

class MaiCorePlugin:
    def __init__(self):
        self.context_manager = get_context_manager()

    async def generate_llm_prompt(self, user_input: str) -> str:
        """生成 LLM 提示词（包含所有动态上下文）"""

        # 1. 收集所有已注册的上下文
        contexts = await self.context_manager.get_all_contexts()

        # 2. 格式化上下文为文本
        context_parts = []
        for name, content in contexts.items():
            context_parts.append(f"## {name}\n{content}")

        context_str = "\n\n".join(context_parts)

        # 3. 渲染最终提示词
        prompt = f"""
你是一个 AI 虚拟主播助手。

# 可用上下文
{context_str}

# 用户输入
{user_input}

请根据上下文和用户输入生成回复。
"""
        return prompt
```

### 3. ContextManager 内部实现

```python
# src/services/context_manager.py

class ContextManager:
    def __init__(self):
        self.providers: Dict[str, Callable] = {}

    def register_provider(self, name: str, provider: Callable, description: str = ""):
        """注册上下文提供者"""
        if name in self.providers:
            logger.warning(f"上下文提供者 '{name}' 已存在，将被覆盖")
        self.providers[name] = provider
        logger.debug(f"注册上下文提供者: {name} - {description}")

    async def get_all_contexts(self) -> Dict[str, Any]:
        """获取所有已注册上下文的实际值"""
        contexts = {}
        for name, provider in self.providers.items():
            try:
                # 调用异步函数获取实际数据
                if asyncio.iscoroutinefunction(provider):
                    value = await provider()
                else:
                    value = provider()

                contexts[name] = value
                logger.debug(f"获取上下文 '{name}': {len(str(value))} 字符")

            except Exception as e:
                logger.error(f"获取上下文 '{name}' 失败: {e}")
                contexts[name] = f"[错误: {str(e)}]"

        return contexts

    def clear_all(self):
        """清除所有已注册的提供者"""
        self.providers.clear()
```

### 4. 数据流图（旧架构）

```
┌─────────────────┐
│ read_pingmu     │
│ Plugin          │
└────────┬────────┘
         │ register_provider()
         ↓
┌─────────────────────────────────┐
│   ContextManager (全局单例)      │
│   providers = {                  │
│     "screen_content": lambda,    │
│     "recent_danmaku": lambda,    │
│   }                              │
└────────┬────────────────────────┘
         │ get_all_contexts()
         ↓
┌─────────────────┐
│ mai_core        │
│ Plugin          │
│ (LLM 调用)      │
└─────────────────┘
```

---

## 为什么不再需要

### 1. 新架构的数据流驱动模式

在新的 Provider 架构中，数据流是显式且单向的：

```
Input Domain → Decision Domain → Output Domain
```

数据通过 EventBus 传递，而不是通过全局注册表：

```python
# 新架构：显式数据流
async def on_message(self, raw_message):
    # 1. 标准化为 NormalizedMessage
    normalized = NormalizedMessage(
        text=raw_message.text,
        source="bili_danmaku",
        metadata={"user": raw_message.user}
    )

    # 2. 通过 EventBus 发送
    await event_bus.emit(
        CoreEvents.DATA_MESSAGE,
        normalized
    )
```

**优势**：
- 数据流清晰可见
- 没有隐式依赖
- 易于调试和追踪
- 符合领域驱动设计原则

### 2. PromptManager 的优势

新的 PromptManager 提供了编译期类型安全的提示词管理：

```python
# 新架构：PromptManager
from src.prompts import get_prompt_manager

# 1. 定义提示词模板（开发时）
# prompts/decision/intent_parser.j2
"""
你是一个决策助手。

# 上下文
- 屏幕内容: {{ screen_content }}
- 最近消息: {{ recent_messages }}

# 用户输入
{{ user_input }}
"""

# 2. 运行时渲染
prompt = get_prompt_manager().render(
    "decision/intent_parser",
    screen_content=await get_screen_text(),
    recent_messages=get_recent_messages(),
    user_input=user_message
)
```

**对比 ContextManager**：

| 特性 | ContextManager | PromptManager |
|------|----------------|---------------|
| 类型安全 | ❌ 运行时字典 | ✅ 编译期检查 |
| 依赖追踪 | ❌ 隐式注册 | ✅ 显式参数 |
| 调试难度 | ❌ 难以追踪来源 | ✅ 清晰的数据流 |
| 模板管理 | ❌ 硬编码在代码中 | ✅ 独立的 .j2 文件 |

### 3. 架构理念转变

#### 旧理念：服务定位器模式

```
"需要上下文？去 ContextManager 里拿"
```

- 插件主动注册自己的上下文
- 消费者从全局单例获取
- 运行时动态发现

#### 新理念：依赖注入 + 数据流

```
"需要什么数据？明确声明依赖"
```

- Provider 显式传递数据
- 通过 EventBus 流转
- 编译期明确依赖关系

### 4. 实际使用情况

运行时验证结果显示：

```python
# 运行时输出
INFO:context_manager:已注册的上下文提供者数量: 0
WARNING:context_manager:活跃的上下文提供者: []
```

**关键发现**：
- ✅ 代码库中没有活跃的上下文提供者
- ✅ 唯一的使用点已被 PromptManager 替代
- ✅ 没有插件依赖这个服务

**结论**：ContextManager 是**抽象过度**（Over-abstraction）的典型案例——为了一种从未实际发生的使用模式，创建了不必要的复杂性。

---

## 迁移方案

### 从 ContextManager 到 PromptManager

#### 旧代码（使用 ContextManager）

```python
# 1. 注册上下文
context_manager.register_provider(
    name="screen_content",
    provider=lambda: get_screen_text()
)

# 2. 消费上下文
contexts = await context_manager.get_all_contexts()
prompt = render_prompt(contexts)
```

#### 新代码（使用 PromptManager）

```python
# 1. 定义提示词模板
# prompts/decision/game_context.j2
"""
当前游戏状态：
屏幕内容：{{ screen_content }}
最近消息：{{ recent_messages }}

用户输入：{{ user_input }}
"""

# 2. 显式传递数据
prompt = get_prompt_manager().render(
    "decision/game_context",
    screen_content=await self.get_screen_text(),
    recent_messages=self.get_recent_messages(),
    user_input=message.text
)
```

### 数据流传递方式

#### 场景 1：屏幕内容需要传递给决策

**旧方式**（不推荐）：
```python
# read_pingmu 插件
context_manager.register_provider("screen", lambda: screen_text)

# mai_core 插件
screen = (await context_manager.get_all_contexts())["screen"]
```

**新方式**（推荐）：
```python
# 方案 A：直接在 DecisionProvider 中获取
class MaicraftDecisionProvider(DecisionProvider):
    async def process(self, message: NormalizedMessage) -> Optional[Intent]:
        screen_text = await self.screen_reader.read_screen()
        # 使用 screen_text 进行决策
        return intent

# 方案 B：通过 EventBus 传递
# InputProvider 发布屏幕内容事件
await event_bus.emit("game.screen.updated", screen_text)

# DecisionProvider 订阅屏幕事件
async def on_screen_updated(self, screen_text: str):
    self.current_screen = screen_text
```

#### 场景 2：需要组合多个数据源

**旧方式**：
```python
# 自动收集所有注册的上下文
contexts = await context_manager.get_all_contexts()
```

**新方式**：
```python
# 显式声明需要的数据
class MyDecisionProvider(DecisionProvider):
    def __init__(self, config):
        self.screen_reader = ScreenReader()
        self.danmaku_buffer = DanmakuBuffer()

    async def process(self, message: NormalizedMessage) -> Optional[Intent]:
        # 收集需要的上下文
        screen_text = await self.screen_reader.read_screen()
        recent_msgs = self.danmaku_buffer.get_recent(count=10)

        # 渲染提示词
        prompt = get_prompt_manager().render(
            "decision/composite_context",
            screen_content=screen_text,
            recent_messages=recent_msgs,
            user_input=message.text
        )
```

### 迁移检查清单

- [ ] **识别所有 ContextManager 使用点**
  ```bash
  grep -r "context_manager" src/
  ```

- [ ] **替换为 PromptManager**
  - 创建对应的 `.j2` 模板文件
  - 使用显式参数替代动态上下文

- [ ] **重构数据流**
  - 评估是否需要通过 EventBus 传递数据
  - 或者在 Provider 内部直接获取所需数据

- [ ] **更新测试**
  - 移除 ContextManager mock
  - 测试显式的依赖注入

- [ ] **删除导入**
  ```python
  # 删除
  - from src.services.context_manager import get_context_manager
  ```

---

## 关键发现总结

### 运行时验证结果

在移除 ContextManager 之前，进行了运行时验证：

```python
# src/amaidesu_core.py (验证代码)

if __name__ == "__main__":
    import asyncio

    async def check_context_manager():
        cm = get_context_manager()
        print(f"已注册的上下文提供者数量: {len(cm.providers)}")
        print(f"活跃的上下文提供者: {list(cm.providers.keys())}")

    asyncio.run(check_context_manager())
```

**输出**：
```
INFO:context_manager:已注册的上下文提供者数量: 0
WARNING:context_manager:活跃的上下文提供者: []
```

### 代码分析结果

通过 `Grep` 搜索整个代码库：

```bash
# 搜索 ContextManager 的使用
grep -r "context_manager" src/
```

**发现**：
1. **定义位置**：`src/services/context_manager.py`
2. **唯一使用点**：`src/domains/decision/providers/maicraft/provider.py`
   - 但该文件已被 PromptManager 替代
3. **零活跃注册**：没有代码调用 `register_provider()`

### 移除决策

基于以上发现，确定可以安全移除：

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 活跃提供者 | ✅ 0 个 | 没有运行时依赖 |
| 代码引用 | ✅ 1 个 | 已被 PromptManager 替代 |
| 测试覆盖 | ✅ 无测试 | 没有测试需要更新 |
| 配置依赖 | ✅ 无 | 没有配置项 |

### 架构收益

移除 ContextManager 后的架构优势：

1. **简化代码库**
   - 删除 ~150 行未使用的代码
   - 移除一个全局单例服务

2. **提升可维护性**
   - 明确的依赖关系
   - 更容易理解数据流

3. **增强类型安全**
   - PromptManager 提供编译期检查
   - IDE 自动补全支持

4. **符合架构原则**
   - 单一职责原则（PromptManager 只管理提示词）
   - 依赖倒置原则（显式声明依赖）

### 经验教训

这个案例提供了宝贵的架构设计经验：

1. **YAGNI 原则**（You Aren't Gonna Need It）
   - 不要为假设的使用场景创建抽象
   - 等实际需求出现再设计

2. **显式优于隐式**
   - 全局注册表增加理解成本
   - 显式参数传递更清晰

3. **验证抽象价值**
   - 定期审查抽象的使用情况
   - 移除过度设计的代码

4. **架构演进**
   - 从插件架构到 Provider 架构
   - 数据流从隐式到显式

---

## 附录：完整迁移示例

### 示例：游戏上下文决策

#### 旧架构（理论使用）

```python
# 1. read_pingmu 注册屏幕上下文
class ReadPingmuPlugin:
    async def on_enable(self):
        context_manager.register_provider(
            "screen_content",
            self.get_screen_text
        )

# 2. bili_danmaku 注册弹幕上下文
class BiliDanmakuPlugin:
    async def on_enable(self):
        context_manager.register_provider(
            "recent_danmaku",
            self.get_recent_danmaku
        )

# 3. mai_core 消费所有上下文
class MaiCorePlugin:
    async def make_decision(self, user_input):
        contexts = await context_manager.get_all_contexts()
        screen = contexts["screen_content"]
        danmaku = contexts["recent_danmaku"]

        prompt = f"""
        屏幕内容：{screen}
        最近弹幕：{danmaku}
        用户输入：{user_input}
        """
        return await llm_generate(prompt)
```

#### 新架构（实际实现）

```python
# 1. DecisionProvider 直接获取所需数据
class MaicraftDecisionProvider(DecisionProvider):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.screen_reader = ScreenReader()
        self.danmaku_buffer = DanmakuBuffer(max_size=10)

    async def process(self, message: NormalizedMessage) -> Optional[Intent]:
        # 显式收集上下文
        screen_text = await self.screen_reader.read_screen()
        recent_msgs = self.danmaku_buffer.get_recent()

        # 使用 PromptManager 渲染
        prompt = get_prompt_manager().render(
            "decision/maicraft_game_context",
            screen_content=screen_text,
            recent_messages=format_messages(recent_msgs),
            user_input=message.text,
            character_persona=self.config.get("persona", "")
        )

        # 生成决策
        response = await self.llm_service.generate(prompt)
        return self.parse_intent(response)
```

**对比优势**：
- ✅ 依赖关系一目了然
- ✅ 可以轻松 mock 测试
- ✅ 类型安全（IDE 支持）
- ✅ 性能更好（按需获取，不需要遍历所有提供者）

---

## 总结

ContextManager 是旧插件架构演进过程中的产物，体现了当时的架构理念：

- **动态注册**：插件在运行时注册能力
- **服务定位**：通过全局单例访问
- **隐式依赖**：消费者不知道数据来源

在新的 Provider 架构中，这些理念已被更好的实践替代：

- **显式依赖**：通过参数明确传递
- **数据流驱动**：EventBus 单向流转
- **类型安全**：PromptManager 编译期检查

移除 ContextManager 不是功能损失，而是架构简化的必然结果——**去掉不必要的抽象，让代码更清晰、更易维护**。

---

*文档创建时间：2026-02-10*
*架构版本：Provider Architecture v2.0*
