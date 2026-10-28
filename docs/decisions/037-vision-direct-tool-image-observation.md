# ADR-037：视觉直连——工具图像进对话观察（内部契约带图 / 翻译层拆分 / 能力协商按需 VLM）

- 状态：已采纳（2026-10-11 定案）
- 日期：2026-10-11
- 实现提交：`f4a7b7624c06039058c464914bf0c0f7c52e3ef1`（能力出口与调用凭据）+ `68110c2fd7b587cd38be05e66ae3e0b046afa309`（观察组装带图消息）+ `005fd433c850744e09728fd23da01aaff3ae9bc9`（翻译层协议拆分）+ `db0c4175a55a7d9e88b60dc3e765ed8563c0b495`（VLM 按需执行）+ `c3de381d690dab543288c5e3c198a42dfc0784b4`（content null 协议宽容，冒烟顺带）

## 背景（Context）

场次21 直播实锤：`vision_look_at_screen` 的工具结果把 base64 截图经 `structured_content` 整包渲染进 planner/minecraft 对话，单笔最高 85 万 tokens（占该请求 98.9%）。泄漏修复合入后（`e0405d70`，观察视图滤除 base64），图与对话彻底断路——而 planner 实际配置的模型（MiniMax-M3.1-Flash-Preview）本身具备视觉能力（vision 档位生产实证），"模型亲眼看图"的正规路径不存在。两层缺口：agent 观察组装只会拼文本；OpenAI 兼容协议的 tool 角色消息只收文本（"image URLs are only allowed for role: 'user'"），图像必须挂 user 消息。另有两个既有事实：`ToolExecutionResult.blocks`（文本块+图像块）零消费；`LLMModelConfig.visual` 能力位全仓库零消费（预留死字段）。

## 决策（Decision）

核心一句：**内部契约工具消息允许携带 `ImagePart`（与 MCP `CallToolResult.ImageContent` / Anthropic `tool_result` image block 同构）；OpenAI 翻译层负责把工具消息里的图拆为 user 消息（落位在该回合整段 tool 回执结束之后）；agent 层按 `is_profile_visual(profile)` 判定图附不附与 VLM 调不调。**

- 承载位置分层：agent 与 engine 只见"一条带图的工具消息"（图与工具结果同消息，breakdown 计量自动归属工具名）；拆分方言归 OpenAI client（工具文本照发 + 图提为 user 消息，落位整段回执之后）——援引 ADR-033"方言是端点属性"先例
- 能力协商：engine 加 `is_profile_visual(profile) -> bool` 出口（取该 profile 当前主模型的 `visual` 位，激活既有死字段）；组装层 visual=True → 文本视图照旧 + 附 `ImagePart`，False → 纯文本视图
- VLM 按需：`look_at_screen` 的 VLM 步骤对 visual 调用方跳过（模型亲眼看图，省一次 vision 调用与 1–9s 延迟）；非 visual 调用方照旧。调用方身份经 `ToolInvocation.caller_profile` 新字段显式传递（既有 `source` 保持"调用场景"语义）；**空串 = 管线式调用 = 按非视觉处理**——text_adv 的结构化读屏管线（VLM 固定格式输出中心像素坐标）零改动且语义正确
- 图生命周期 = 单个工具回合（与文本工具结果同生共死），跨回合画面记忆不做
- 图像承载与生命周期两项已由 [ADR-040](040-context-layering-and-checkpoints.md) 取代：工具消息不再带图，图只挂在紧随回执的那一次请求尾部；能力协商与按需 VLM 仍按本 ADR

## 替代方案（Alternatives）

- **agent 组装层直接产出两条消息**（工具文本 + user 带图）：线上形态相同，但把"OpenAI 系要拆"的方言知识散进每个 agent 循环，未来接 Anthropic 形态端点（tool_result 原生带图）时需返工
- **engine 层判定**（agent 全量带图、engine 按实际模型剥）：非视觉调用方的历史囤图，上下文计量需处理"图在内部消息但未发出"的账
- **profile 级配置开关**：违反"模型固有属性归模型级"先例（ADR-033 已把上下文窗口定为模型级字段，本篇 `visual` 能力位同理），配置一致性责任压给用户
- **VLM 总是执行**（visual 调用方拿答案+图双份）：visual 调用方白付费用与延迟，转述文字对能亲眼看图的模型是冗余/误读干扰
- **base64 文本进对话**：85 万 tokens 事故本体，弃

## 后果（Consequences）

- 全部档位现状配 MiniMax-M3.1（visual=true）→ 实现落地当日即激活：planner/minecraft 直看原图、VLM 默认不执行、vision 档位降级为配置回退路径
- 看图链路从"截屏→VLM(1–9s)→续轮"缩为"截屏→续轮"；单图成本从 85 万 tokens（伪影）降为千级（正规视觉编码）
- 故障切换边缘：主模型 visual、备选非 visual 时带图请求失败——接受单轮损失 + 错误日志（主备能力不一致属配置问题）
- 落位修正（2026-10-12 无场次冒烟实锤）：提图 user 消息原设计紧随自己的回执插入，多工具并发轮里会打断 assistant 回合的回执连续性，被 DeepSeek 400 拒收（insufficient tool messages following tool_calls message）——翻译层统一推迟到整段回执结束之后落位，按来源回执分组保序，配对语义不变
- DeepSeek flash 的图像请求形态（data URL vs 公网 URL）我方未实测，列入实现切片冒烟验收；context_meter 对 ImagePart 采用千级标称估算（API usage 仍为真值）
- 关联：缺陷修复 `e0405d70`（base64 滤除）
