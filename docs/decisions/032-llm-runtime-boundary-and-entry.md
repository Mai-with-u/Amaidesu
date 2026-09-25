# ADR-032：LLM 运行时边界与入口收口

- 状态：已采纳（2026-09-13 定案并实现）
- 日期：2026-09-13
- 实现提交：`996d1bd766436e8bdb61dbce334437a806c0d54d`（提取模块骨架：编排/装配/中立模型分离）

## 背景（Context）

LLM 运行时是全系统唯一"每个决策都经过"的热路径，但它的形状是历史层积：

1. **糅合**：978 行 `manager.py` 一文件承载编排（选模型/超时/重试/failover）、装配（provider 池/模型索引）、profile 解析、费用记账、查询面——边界不可见。
2. **入口泛滥**：`chat_messages` / `call_tools` / `chat` / `chat_vision` / `stream_chat` / `simple_chat` / `simple_vision` / `chat_fast` + 10 个查询方法——生产调用方实测只有 4 个方法被用，`stream_chat` 等零调用。
3. **韧性语义三处含糊**：超时包哪段（单次尝试 vs 整轮）、按什么重试（400 参数错重试无意义）、流式半途失败能不能切模型——无一定论。
4. **profile 绑定四处硬编码** + `legacy_map` 旧名兜底 + `getattr(config, "profile", "llm")` 静默兜底——乱，且已有 3 处配置死字段。
5. **缓存命中率无法跨厂商统计**：各 client 返回的 usage 字段名不同。

## 决策（Decision）

核心一句：**模块按"编排 / 通道 / 中立模型"切开；入口收口为 `generate` + `generate_vision` 两方法；profile 绑定在代码显式声明、用途集合封闭；参数归一化只做读取面。**

### 模块边界

`engine.py`（编排：selection/超时/重试/failover/中断）+ `clients/`（`Client` 接口 + OpenAIClient：错误产生与翻译、provider 级 socket 超时）+ `payload.py`（中立数据模型）+ `observation.py`（费用/命中记账）+ `bootstrap.py`（装配）。命名保留 `Client`（弃 Transport）；注册表收敛为极简 `client_type → Client` 调度。

### 运行时韧性（Engine 职责）

- `hard_timeout_ms` 包住**整个单模型尝试（含其内部重试）**，校验 `hard_timeout_ms >= provider.timeout`；
- 按错误类型重试：可重试错误才重试，400 类直接切下一模型；
- 流式：首 token 前可 failover，首 token 后只能中止；流式同样受 hard_timeout 约束。

### 入口收口

对外仅两方法：`generate(input, *, profile, system, tools, temperature, max_tokens, on_delta, interrupt)` 与 `generate_vision(prompt, images, *, profile, system, interrupt)`。`input` 收 `str | list[消息]`，Engine 归一化到 payload；流式经 `on_delta` 回调；删 `stream_chat` / `simple_*` / `chat_fast` / 查询面 10 方法（生产零调用方，将来需要再加）。6 个消费方调用点一次性迁移。

### profile 绑定与用途集合

- **绑定在代码显式声明**（各组件常量，如 Planner 用 `planner`、Replyer 用 `replyer`），**显式、无默认兜底**——删 `legacy_map` 与静默 `"llm"` 兜底；校验改为"代码声明的 profile 均存在"（fail-fast），删硬编码名单。
- **用途集合封闭**：用途来自代码（某段代码按名字调用它才存在），自加的无引用 profile 是死条目——允许自加 profile 与"恨配置死字段"自相矛盾，故参考 MaiBot `ModelTaskConfig` 采用封闭集合。
- **三样东西再分**（两次修正后定形）：内容（节目/人设）→ 配置；用途定义（模型与参数）→ 配置 `[llm_profiles.<name>]`；组件挂哪个用途 → 代码（组件内在属性，如名字）。
- 例外裁决：组件硬编码自己的 profile **不违反**"加内容=配置"——该原则管"内容"（节目/人设），不管"组件"；治的是乱（常量散落+静默兜底），不是硬编码本身。

### 归一化分治

参数归一化分**读取面**（缓存命中计数，跨厂商字段名可归一，本次做）与**控制面**（思考强度，跨厂商语义不同难归一，本次不做）——先做读取面，词汇表仅缓存命中一项。

## 替代方案（Alternatives）

### per-consumer 配置绑定（配置字段声明组件用哪个 profile）

**否决（用户主张后经分析自认）**。区分两种操作：改"用途用哪个模型"（编辑 `[llm_profiles.<name>]`，已配置化）与改"组件挂哪个用途"（重绑定，罕见）——常见需求全落前者；加配置的代价真实（6 字段+校验+文档，且刚查出 3 处配置死字段）。组件挂哪个 profile 是组件内在属性，不是部署配置。

### Transport 命名

**否决**。保留 `Client`——换名是无信息量的扰动。

### 用途集合开放（用户可自加 profile 挂新用途）

**否决**。第一性原理：用途来自代码的调用点，集合天然封闭；开放 = 允许死条目，与配置死字段的清理立场冲突。

## 后果（Consequences）

- 6 个消费方调用点一次性迁移（重构本就要动调用点，净成本低）；`stream_chat` 的流式诉求由 `on_delta` 承载。
- 思考强度（reasoning effort）控制面暂不归一——跨厂商语义差异大，将来按需单开议题。
- "绑定在代码"让换用途要走代码改动——这是有意取舍：重绑定罕见，而为它开配置面会制造第 4 处死字段温床。
- 缓存命中归一只覆盖读取面；新厂商接入时在 client 层补字段映射，Engine 与调用方零改动。

## 关联

- 模块权威：`src/modules/llm/`（engine / clients / payload / observation / bootstrap）
- 精华来源：`.omo/drafts/llm-system-refactor.md`（含用户三次推翻 AI 建议的完整裁决轨迹）
- 缓存优化后续：请求历史逐条命中率展示（dashboard，独立批次）
