# 添加工具（ToolProvider）

> 归类判据、端到端消息流与通用规范见[组件开发指南](README.md)。

## 添加工具

### 工具管控三维模型（术语纪律）

工具的任何"开/关/通"话题，先分清说的是三个正交维度中的哪一个——跨维度混用词会造成沟通歧义：

| 维度 | 回答的问题 | 生效期 |
|------|-----------|--------|
| **存在性** | 系统里有没有它 | 装配期（配置启用/停用，重启生效） |
| **可见性** | LLM 能不能看见它 | 运行时即时（名单停用 / 熔断抑制 / 归属限定） |
| **可用性** | 通道通不通 | 运行时即时（已连接/已断开；探活与重连） |

LLM 工具面公式（可见性维度的完整定义）：

```
LLM 工具面 = 全部注册工具 − 名单停用 − 熔断中 − 归属限定
```

措辞禁令（跨维度语义漂移防护）：谈连接或工具状态时禁用"关闭/开启/重置"；`audience` / `visible_to` / `for_agent` / `reconnectable` 为废弃命名，不得复用。

### 适用场景

需要为 Agent 提供**单一可调用的能力**时，新增一个工具。工具是**契约层**——只声明能力（`ToolSpec` + 工具实现函数），不持有会话、不发起事件（除 `async` 工具的结果事件外）。

工具的典型形态：

- **公用感知**（如 `vision_look_at_screen`）——任何 Agent 都可能需要，放 `src/modules/vision/`
- **Agent 专属推进**（如 `text_adv_advance` / `text_adv_choose`）——只服务于某个游戏 Agent，放该 Agent 自家包内 `src/agents/<name>/tools.py`

### 数据契约与协议速览

| 类型 | 位置 | 关键字段 |
|------|------|----------|
| `ToolSpec` | `src/modules/tools/models.py` | `name`（声明名，**不带前缀**）, `description`, `parameters_schema`, `kind` (`"sync"`/`"async"`), `provider`（提供者短名）, `result_event`, `output_schema`；对外全名 = `full_name` 派生（`<provider>_<name>`，唯一实现、不存下来） |
| `ToolInvocation` | `src/modules/tools/models.py` | `tool_name`, `arguments`, `call_id`, `invoked_at_ms`, `source` |
| `ToolExecutionResult` | `src/modules/tools/models.py` | `tool_name`, `success`, `content`, `blocks` (`ResultBlock` 列表), `error_message`, `structured_content`, `duration_ms`, `timestamp_ms` |
| `ResultBlock` | `src/modules/tools/models.py` | `kind` (`"text"`/`"image"`), `text`, `data` (base64), `mime_type` |
| `ToolProvider`（Protocol） | `src/modules/tools/provider.py` | `name` 属性（与全部 spec 的 `provider` 同值同源，注册期校验）、`list_tools()`、`async invoke(invocation) -> ToolExecutionResult`（**永不抛异常**）；可选钩子 `query_task` / `subscribe_task_notifications`（任务适配器，默认不支持） |
| `BaseToolProvider`（ABC） | `src/modules/tools/provider.py` | 所有经 `register_provider` 装配的 Provider 的继承基类；带 `category` ClassVar 与 `health_check` 探活钩子默认实现 |
| `ToolRegistry` | `src/modules/tools/registry.py` | `register(spec, impl)` / `register_provider(provider, *, visible_to=...)`（可见名单）/ `invoke(invocation)` / `invoke_many(invocations)` / `to_llm_definitions()` / `has(name)` / `list_tools(for_agent=...)`（按名单计算工具列表）/ `visible_to_of(name)` / `probe_tool(name)`（熔断器探活入口） |
| `as_tool_impl` / `make_provider_from_specs` | `src/modules/tools/provider.py` | 简单工具正典路径：普通 async 函数 + spec 组装标准 Provider（样板 `src/agents/streamer/memory/recall.py`） |

### Provider 探活契约（与熔断器配套）

有外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider 继承 `BaseToolProvider` 并**重写** `health_check`（连接可用才返回 True）；无状态工具沿用基类默认（返回 True，语义为"无可检查之物，让流量决定"——熔断后冷却期满即恢复，再失败再熔断）。`ToolRegistry.probe_tool(name)` 统一按名定位 provider 并调用 `health_check`，熔断器配套的 `ToolHealthMonitor` 走这一条路径，无须再做反射式存在性探测。维护外部连接的 Provider 实际覆写 `health_check` 由架构测试 `tests/architecture/test_provider_health_contract.py` 强制约束。

### 简单工具正典路径（唯一路径）

无状态、轻量的简单工具**不必手写 Provider 类**——正典路径三件套：

1. `ToolSpec`（声明名裸名，全名自动派生）
2. 普通 async 函数（经 `as_tool_impl` 包装：返回值/异常/计时归一为
   `ToolExecutionResult`）
3. `make_provider_from_specs` 组装 Provider，装配处一行
   `registry.register_provider(provider, visible_to=...)` 注册

样板：`src/agents/streamer/memory/recall.py`（主播回忆工具——无连接、无状态，按键读账本、档案与台词）。

**升级为手写 Provider 类的判据**（出现任一条才升级）：需要连接重连
（覆写 `connect`/`health_check`）/ 共享状态 / 动态工具表 / 任务适配器
（回执型工具覆写 `query_task` / `subscribe_task_notifications`）。
手写类同样继承 `BaseToolProvider`，`name` 属性必须与其全部 spec 的
`provider` 同值（注册期 fail-fast 校验）。

### 最小骨架代码（正典路径）

参考样板：`src/agents/streamer/memory/recall.py`（主播回忆工具——无连接、无状态，按键读账本、档案与台词）。

```python
# my_tool.py —— 简单工具正典路径样板
# 放 src/modules/tools/<domain>/my_tool.py（公用）或 src/agents/<name>/tools.py（Agent 专属）
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

PROVIDER_NAME = "my"  # 提供者短名（三合一身份：全名前缀 / 注册名 / 定位键）

MY_TOOL_SPEC = ToolSpec(
    name="my_tool",  # 声明名裸名（不带前缀）；对外全名 = my_my_tool 派生
    description="示例工具：把传入的文本翻译为大写。",
    parameters_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要转换的文本"}},
        "required": ["text"],
    },
    kind="sync",
    provider=PROVIDER_NAME,
)


async def _run(invocation) -> str:
    """执行体：普通 async 函数。

    返回 str/None → 成功 content；抛异常 → 失败结果（含异常信息）；计时自动包。
    """
    args = invocation.arguments or {}
    return str(args.get("text", "")).upper()


def build_my_tool() -> ToolProvider:
    """组装 Provider（as_tool_impl 归一化 + make_provider_from_specs 包装）。"""
    return make_provider_from_specs(
        PROVIDER_NAME,
        [(MY_TOOL_SPEC, as_tool_impl(MY_TOOL_SPEC.full_name, _run))],
        category="my_domain",  # 可选分类（Dashboard 分组用）
    )
```

**注册调用**（装配处 / `Agent._register_tools` / `main.py`）：

```python
registry.register_provider(
    build_my_tool(),
    # 名单：默认仅主播（DEFAULT_VISIBLE_TO，fail-closed）；
    # 共享工具显式 ["*"]，其他 Agent 专属工具填 ["<Agent 注册名>"]
    visible_to={"my_my_tool": ["streamer"]},
)
```

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/modules/<domain>/…`（avatar/studio/vision 等公用域工具）或 `src/agents/<name>/tools.py`（Agent 专属） | Provider 类 `XxxToolProvider` + `build_xxx_spec()` |
| ② 注册（带名单） | Agent 专属：在该 Agent 的 `_register_tools` 中 `self._tool_registry.register_provider(provider, visible_to=...)`；公用：在装配根 `main.py` 注册（默认全员） | 名单是注册处代码事实：值 = Agent 注册名列表或 `["*"]`；未列工具默认仅主播（fail-closed） |
| ③ 配置（可选） | Agent 专属工具一般无独立配置段（行为由 Agent 配置决定）；公用工具按分类开关（`[tools.<domain>.<key>].enabled`） | |
| ④ 列出与转换 | `ToolRegistry.to_llm_definitions()` 自动从 `ToolSpec.parameters_schema` 派生 OpenAI 风格 function calling 定义供 LLM 看 | |
| ⑤ 调用 | `ToolRegistry.invoke(ToolInvocation(tool_name, arguments, call_id, source))`；**永不抛异常**——失败返回 `ToolExecutionResult(success=False, error_message=...)` | |

**谁调用注册？**

- **Agent 专属工具**：Agent 子类 `_register_tools()` 方法（参考 `StreamerAgent._register_tools`、`TextAdvGameAgent._register_tools`）。在 Agent `_on_start` 阶段调用。
- **公用域工具**：装配根按域装配——avatar 平台经 `bind_core_tools(..., avatar_section=...)` 按 avatar.toml 启用名单注册、studio 分类按 `[tools.<域>.<键>].enabled` 开关注册，vision（`vision_look_at_screen`）由组合根注入 mss 抓屏 + VLM 文本读取后端后注册。
- **工具注册聚合**：生产路径下不存在任何 manager 级聚合函数——Agent 子类在 `_register_tools()` 中自己 `registry.register_provider(provider, visible_to=...)`；avatar/studio 分类工具由 `main.py` 的 `bind_core_tools(..., avatar_section=...)` 按平台名单与域开关装配；启动结束后 `audit_tools(registry)` 只做只读审计（声明与注册按派生全名对账），不参与注入。

**工具面说明（可选）**：Provider 覆写 `guide_for(agent_name) -> str` 给出"这组工具合起来怎么用"的协议级说明（粒度粗于单个 `ToolSpec.description`）；调用方经 `ToolRegistry.guides(for_agent=...)` 按可见名单收集。主播的 Planner 用它拼装【当前游戏的玩法】——游戏 Agent 的工具面必须写（说明自己是委派型还是遥控型、该调哪些工具），其他公用工具一般不需要。

### 测试要点

- **Fake 后端 / Mock Provider**：构造一个返回固定值的 Provider 注入测试
- **断言 `ToolExecutionResult`**：`assert result.success` / `result.error_message` / `result.structured_content`
- **`ToolRegistry.clear()`**：每个测试开头清空，避免污染
- **永不抛异常**：构造故意抛异常的 impl，断言返回的是失败 result 而非异常
- **`to_llm_definitions()` 形状**：断言包含 `name` / `description` / `parameters` 字段（OpenAI function calling 兼容）
- **真实范例测试**：`tests/modules/vision/test_look_at_screen.py`、`tests/agents/test_text_adv.py`

### 真实范例指引

| 范例 | 文件 | 形态 |
|------|------|------|
| `memory_recall`（主播记忆，名单 `["streamer"]`，正典样板） | `src/agents/streamer/memory/recall.py` | 正典路径（`as_tool_impl` + `make_provider_from_specs`） |
| `vision_look_at_screen`（公用感知） | `src/modules/vision/look_at_screen.py` | 手写 Provider（DI `ScreenCapture` + `TextReader` 后端；多显示器 + 可选区域 + VLM 转文本；失败降级 success=True + error） |
| `text_adv_advance` / `text_adv_choose` / `text_adv_set_auto` / `text_adv_get_state`（Agent 专属，名单 `["streamer"]`） | `src/agents/text_adv/tools.py` | 手写 Provider（`provider="text_adv"`） |
| `streamer_reply`（主播发言出口，注册 + 名单 `["streamer"]`） | `src/agents/streamer/tools/reply_tool.py` | 手写 Provider（thinking 槽位） |
| `rundown_control`（动态工具，条件追加例外） | `src/agents/streamer/tools/rundown_tool.py` | 正典路径包装直连执行器 |
| `framework_delegate` / `framework_task_status`（委派原语） | `src/modules/agents/control.py` | 手写 Provider（`provider="framework"`） |

### 三个反直觉点（先读这里，防止推错）

1. **为何全部工具都注册？** 可见性由名单隔离——注册带来统一观测（`tool.result` 事件）、
   停用/熔断治理、工具页管理，全部复用注册表既有机制，不做第二路径。
   注册的判据 = **"是不是工具"**（LLM/调用方会去调的能力）。
2. **为何名单在生产侧（注册处代码声明）而不是消费侧（每 Agent 配清单）？**
   顶层 Agent 独立装配；Minecraft 的建筑设计 Agent 由游戏 Agent 按任务管理，
   不进入顶层委派名册。工具的生产者最清楚
   它给谁用（出生时一处声明），消费侧清单要求每个 Agent 持有全局工具知识且
   已被约束禁止。新 Agent 零维护自动正确。
3. **内部件判据（哪些不是工具）**：代码直接调用的部件不算工具、不进表——
   如主动发言判定（ProactiveTrigger）、命令解析原语（command/ 包）；它们
   被特意排除在 LLM 工具列表之外，没有 `ToolSpec`、不经注册表。
   观众 `/命令` 即走此通道：代码直连解析 + `mappings` 白名单 + 限频后，
   由 Agent 经 `framework_delegate` 工具委派游戏 Agent（映射值为给目标
   Agent 的自然语言指令，非 LLM 可调命令）。

#### 红线三分

Agent 内部件处置轴（AGENTS.md 同款表述）；组件顶层定类见[判据：组件该做成哪一类](README.md#判据组件该做成哪一类)。

| 类别 | 处置 | 例子 |
|------|------|------|
| LLM 可调的内部工具 | **注册 + 名单隔离**（名单填自己或指定受众） | `streamer_reply`、`minecraft_todo` |
| Planner / Replyer 类本体 | **留在 Agent 内部，不进表**（Agent 的决策/表达部件，代码直连） | Planner、Replyer |
| 代码直连的内部件 | **不是工具、不进表**（无 ToolSpec，被调才干活的纯代码部件） | ProactiveTrigger、命令解析原语 |

### 可见名单机制

名单是**注册处的代码事实**（生产侧逐工具声明）：

```python
registry.register_provider(
    provider,
    visible_to={
        "minecraft_todo": ["minecraft"],       # 仅自己
        "minecraft_glance": ["streamer"],      # 主播只读的精简叙事视图
        # 未列出的工具默认仅主播（DEFAULT_VISIBLE_TO，fail-closed）
    },
)
```

| 规则 | 说明 |
|------|------|
| 值 | Agent 注册名列表或 `["*"]`（单独出现 = 全员） |
| 默认 | `["streamer"]`（`DEFAULT_VISIBLE_TO`）——不写即仅主播，fail-closed；共享工具由注册处显式 `["*"]` |
| 校验（fail-fast） | 值非空、`"*"` 单独出现、键必须命中本注册项声明的工具全名（拼错即报错） |
| 计算接口 | `list_tools(for_agent="<Agent 名>")`——该 Agent 的工具列表；全体消费方统一从这里拿 |
| 运营全集 | `list_tools()`（不传 `for_agent`）——Dashboard 工具页看一切 |
| 调用边界 | `invoke()` 不查名单——LLM 幻觉编名直调是已知边界（封死需调用方身份治理） |

**LLM 工具列表公式**（`for_agent` 的过滤结果）：

```
<Agent> 工具列表 = 全部注册工具 − 停用(disabled_tools) − 熔断中(tripped) − 名单不含该 Agent 的工具
```

已知例外：动态工具（如 `rundown_control` 按流程单激活状态出现）名单静态，
靠工具列表内条件追加，记录为例外。

### 三事件分工（防混淆）

| 事件 | 语义 | 发射点 |
|------|------|--------|
| `planner.verdict` | 决定时刻（reply 工具被调用、表达生成之前） | ReplyToolProvider |
| `tool.result.<全名>` | 调用完成（成功/失败，含入参回显） | `ToolRegistry.invoke` 统一发射 |
| `streamer.speech` | 业务事实（一条发言已生成，与 TTS 启用正交） | StreamerAgent 发言管线 |

reply 走注册表后三事件都发——观测冗余是**有意接受**的（统一规则优先，
三事件各答一个不同的问题）。

### 异步任务基建与委派

回执型工具（调用拿任务号而非结果，如 `maicraft_execute`）与跨 Agent 委派
共用一套基建（`src/modules/tools/tasks.py`）：

- **受理约定**：结构化结果 `accepted=true + task_id`（回合不阻塞）
- **任务记录表**（`TaskLedger`，内存）：任务号 → 状态/快照/发起方/执行者；
  终态移除；`accepted↔running` 组内迁移静默（不唤醒），决策点与终态才发
  `task.changed`
- **跟踪循环**（`TaskTracker`，照 ToolHealthMonitor）：通知触发 + 周期兜底；
  订阅按提供者复用；无进展提醒（`wait_timeout_ms` 停滞告警，不杀任务）
- **适配器钩子**（默认不支持，回执型 provider 覆写）：`query_task(task_id)`
  查事实；`subscribe_task_notifications(callback)` 订提示
- **委派原语**：`framework_delegate(agent, instruction)` → 回执 + task_id
  （名册校验、禁自派、BaseAgent 接收入口默认拒收）；`framework_task_status
  (task_id)` 查进度。受理失败与任务失败分开
- **唤醒**：`task.changed`（仅真变化/告警）→ BaseAgent 默认按
  `payload.initiator == self.name` 过滤 → 子类覆写 `on_task_notification`
  注入消息 + 唤醒

接入回执型工具：绑定处声明适配器（查询工具全名 + 状态映射 + 通知 URI），
范例见 `MinecraftAgent._bind_agent_owned_mcp`；接入委派：Agent 覆写
`receive_delegation`（默认拒收），范例见 `MinecraftAgent.receive_delegation`。

### 已知边界（有意取舍，勿当缺陷上报）

- 编名直调不拦：`invoke` 不查名单/身份（可见性治理为主防线）
- 重启丢跟踪：任务记录表内存态，重启后进行中任务的跟踪消失（执行侧仍在跑）
- 无排队上限：委派/回执任务积压无限流
- 无发起方取消：执行侧可用其工具取消（如 maicraft task cancel）；跨 Agent
  取消按需再加
- 动态工具列表内条件追加：`rundown_control` 按激活状态出现（唯一已知例外）
- 停用边界作用于全部工具：停用关键内部件（如 `minecraft_todo`）会直接破坏
  宿主 Agent 运行——管理界面有警示标注（确认制，非硬禁）
- 任务基建节拍配置 `[tools.tasks]` 为兜底读取（新键 → 旧键 → 默认），正式
  配置段由配置线落

### MCP 二分表（通用 vs Agent 私有）

| 通道 | 配置位置 | 工具注册时 `provider` | 装配入口 | 适用 |
|------|---------|---------------------|----------|------|
| 通用 MCP | `tools.toml` 的 `[tools.mcp.config.servers.<别名>]` | `<server 名>`（如 `maicraft`） | 组合根 `bind_mcp_tools(registry, ...)` | 任何 Agent 都可调用，类 Claude Code 全局工具源 |
| Agent 私有 MCP | `agents.toml` 的 `[agents.<Agent 名>.mcp]` | `<server 名>` | Agent `_on_start` 自行装配，逐工具名单（fail-closed：默认仅自己） | 名单内的 Agent 可见；域内查询 `list_tools(provider=...)` 照常 |

原则：**位置即归属，装配即声明，调度与基建全局统一**——通用 MCP 与 Agent 私有 MCP 共用 `McpToolProvider` / `ToolRegistry` / 熔断器 / 探活等基建，唯一区别是归属标记与配置宿主文件。Agent 私有 MCP 的 `enabled=false` 时不装配（Agent 是命令驱动，MCP 不可用只降级）。

典型范例：MinecraftAgent 在 `[agents.minecraft].mcp` 声明其 maicraft server，启动时以逐工具名单注册（fail-closed：maicraft 工具仅 minecraft 可见；主播读游戏状态走 Agent 自带的 `minecraft_glance` 精简视图）——其它 Agent 的工具列表不被 maicraft 原始工具污染。

---

