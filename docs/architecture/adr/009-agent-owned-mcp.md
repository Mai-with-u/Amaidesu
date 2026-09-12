# ADR-009：Agent 私有 MCP 与工具归属限定（位置即归属，装配即声明）

- 状态：已采纳（2026-09-08）；**2026-09-11 修订：归属判定升级为"归属 + 名单"双轴（见下方修订节与 ADR-012），`owner_agent` 机制由逐工具可见名单取代**
- 日期：2026-09-08（原决策）/ 2026-09-11（修订定案）
- 实现提交：
  - `993aff8032551d54c6457c742b104a704529ffc5`（feat(tools): 工具归属限定与运营面参数）
  - `94dab8eb14ad27b86e8e82025e2a16cf5bf9d6af`（feat(config): minecraft 私有 mcp 段与跨文件迁移）
  - `0e23998d11eb1044af5a0e60869f554cde573665`（feat(minecraft): 私有 MCP 装配走自身配置段）
  - `2ff7dd3d83b90449b5cf4d9ba0b05154bf4548c0`（feat(dashboard): 工具列表透出归属字段）
  - `1e2b90584a8edfedb5945904bb930a78e748ae92`（docs(development): 补工具可见性三维模型与MCP二分）
  - 修订（名单双轴）实现提交：待收口回填

## 背景（Context）

maicraft MCP server 是 MinecraftAgent 的实现器官（游戏内操作能力），但配置位于全局
`tools.toml [tools.mcp.config.servers]`，装配走通用通道 `bind_mcp_tools` 全局注册。
由此产生三个问题：

1. **配置位置不表达归属**：一个显然属于 minecraft 域的 server，配置上与"未来的
   web-search 等真正通用 server"平起平坐，位置不携带任何归属信息。
2. **通用工具面被器官污染**：maicraft_* 工具进入所有消费方（主播 Planner 等）的
   一般工具面——其他 Agent 既不需要也不该看见它们，只留下幻觉编名调用的风险面。
3. **通用通道承载特定器官**：`[tools.mcp]` 的语义本应是"全局可见的外部工具源"，
   特定 Agent 的实现器官混入其中，通用与私有失去边界。

同期工具管控体系已落两层可见性过滤：手动可见性停用（`disabled_tools`）与熔断抑制
（`tripped`，见工具熔断探活实现）。可见性维度还缺最后一块：**归属限定**。

## 决策（Decision）

### 1. 工具可见性三维模型与 LLM 工具面公式

工具管控按回答的问题分三维：存在性（配置启用/停用，重启生效）、可见性（LLM 能否
看见，运行时即时）、可用性（通道通不通，运行时即时）。归属限定属于可见性维度，
与手动停用、熔断抑制同族：注册进 registry，但默认不进一般工具面。LLM 工具面公式：

```
LLM 工具面 = 全部注册工具
           − 手动可见性停用        （disabled_tools）
           − 熔断中                （自动抑制）
           − 归属限定              （owner_agent 非空；结构性默认排除）
```

### 2. MCP 二分：通用 vs Agent 私有

| | 通用 MCP | Agent 私有 MCP |
|--|---------|---------------|
| 配置位置 | tools.toml `[tools.mcp.config.servers]` | agents.toml `[agents.<name>.mcp]` |
| 归属 | 无（全局可见） | `owner_agent`（仅所属 Agent 域内可见） |
| 装配 | `bind_mcp_tools`（全局路径） | Agent 自己的 `_on_start`（读自身配置段构造） |

原则十二字：**位置即归属，装配即声明，调度与基建全局统一**。私有 Provider 照常
注册进 ToolRegistry——熔断、探活、tool.result 观测、停机清理（`close_mcp_providers`
遍历 registry providers）全部原样覆盖，无特殊路径。

### 3. 归属限定语义与三条可见路径

`register_provider(provider, owner_agent="minecraft")` 非空时记录"注册名 → 归属
Agent"映射。三条可见路径覆盖全部合法消费方：

1. **一般查询** `list_tools()`：结构性排除归属限定工具——Planner 等既有消费方
   零改动即被保护
2. **显式域查询** `list_tools(provider="maicraft")`：域内查询不受归属过滤——
   MinecraftAgent 的动态工具面发现零改动
3. **运营查询** `list_tools(include_scoped=True)`：Dashboard 工具页显式看见一切，
   条目携带 `owner_agent` 字段

### 4. invoke 不校验归属

受众治理只管发现面。LLM 幻觉编名直调保留工具是**已知边界**（与手动停用同级别的
残存风险）——封死它需要调用方身份治理，而 `source` 不是凭证，留待未来化。

### 5. 配置与迁移

- `MinecraftAgentConfig` 新增 `mcp: McpServerConfig`（默认 `url =
  http://127.0.0.1:8766/mcp`），运行时 `MinecraftConfig` 同步透传
- 跨文件迁移：`[tools.mcp.config.servers.maicraft]` 整段搬入
  `[agents.minecraft.mcp]`（用户原值保留、源段删除、天然幂等），
  `CONFIG_VERSION` 升 **2.0.27**
- 装配失败降级不阻断：MinecraftAgent 是命令驱动 Agent，MCP 不可用只影响游戏
  工具面，Agent 启动与命令循环照常

## 修订（2026-09-11）：归属 + 名单双轴

原决策"位置即归属"解决的是 **MCP 住在哪**（配置/装配/订阅四位一体在归属方名下）——这半轴维持不变。但可见性判定从"provider 粒度的 `owner_agent` 单一归属"升级为"**注册处逐工具可见名单**"（详见 [ADR-012](012-tool-visibility-list.md)）：

- 单一归属只有"自己 / 全员"两档，表达不了"读工具给主播+自己、执行工具仅自己"这类逐工具受众——同一条连接的不同工具需要不同名单。
- 名单仍是注册处代码事实（生产侧声明），与"位置即归属"同构：绑在哪、名单就写在哪个包。
- `owner_agent` 参数、`_scoped_owner` 机制与三条可见路径由 `visible_to` 名单与按受众计算接口（`for_agent`）取代；`include_scoped` 运营查询由名单全集承接。

另据 MCP 规范复核补充：server 自报的工具注解（annotations）**不可信**（规范原文要求客户端视为 untrusted），且 MaiCraft 的 task 工具混合读/写动作却只有一套注解、无法表达动作级区分——读/执行分类只能由我方代码在绑定处声明，注解不进机制。

## 替代方案（Alternatives）

### 消费方自行过滤（Planner 运行时按名单剔除）

**拒绝**。每个工具面消费方各自记得过滤是脆弱约定，新增消费方即踩坑——与思考流
ADR 中"delta 走 EventBus 迫使每个存储订阅方记得跳过"同源的失败形态。结构性排除
（默认面就看不见）从机制上根除。

### 工具级受众声明（audience / visible_to 字段）

**当时拒绝**。逐工具声明受众冗长且易漂移；归属天然是 provider/装配粒度的事实，不是
单工具属性。audience、visible_to、for_agent 列为废弃禁用词。

> 2026-09-11 修订：本条被 [ADR-012](012-tool-visibility-list.md) 部分推翻——逐工具
> 名单重回方案（maicraft 读/执行分受众的现实需求逼出这一档）。与当时拒绝对象的
> 区别：名单是**注册处代码事实**（一个注册项注册时一并声明），不是 spec 元数据
> 字段、不进配置；`for_agent` 作为注册表按受众计算接口复活（非配置词）。

### maicraft 留在 tools.toml 加 `private = true` 标记

**拒绝**。位置仍不表达归属，通用通道依然承载器官，只是打了个补丁；且通用装配路径
仍需特判该标记，"调度与基建全局统一"被破坏。

### invoke 时校验调用方身份（非归属 Agent 拒绝调用）

**本期不做，未来化**。调用方身份治理需要可信的 source 传递链（现有 `source` 参数
不是凭证），收益对当前威胁模型（自家 LLM 幻觉，非恶意攻击）不成立。发现面治理为
主防线。

## 后果（Consequences）

**收益**：

- 主播 Planner 与 MinecraftAgent 双向零改动：前者被结构性保护，后者域查询语义不变
- maicraft 从通用 servers 列表移除，通用 MCP 通道语义回归本位，为未来真正的通用
  server（web-search 等）腾出干净位置
- 后续游戏域 Agent 的专属 MCP 接入模式确立：加配置段 + `owner_agent`，框架零改动
- 三层可见性过滤（停用/熔断/归属）正交叠加，语义可组合

**代价**：

- registry 新增 `_scoped_owner` 状态与 `include_scoped` 查询参数，查询语义从
  "开关式过滤"变为"三方叠加过滤"，消费方需理解公式
- Dashboard 运营面必须显式 `include_scoped`，遗漏即"工具消失"的错觉
- 配置升版本 + 跨文件迁移（用户现有 tools.toml 的 maicraft 段自动搬家）

**遗留（如实记录，不掩盖）**：

- LLM 幻觉编名直调保留工具：invoke 无身份校验，封死需调用方身份治理（未来化）
- Dashboard 前端归属列展示留白（后端 `owner_agent` 字段已就位，前端未动）
- provider 级可见性粒度（同 provider 部分工具归属不同 Agent）未支持——当前无需求，
  归属即 provider 归属
