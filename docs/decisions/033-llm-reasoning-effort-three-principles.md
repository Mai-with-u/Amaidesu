# ADR-033：LLM 思考强度控制三原则（设了才发 / 自由字符串 / 方言逃生舱）

- 状态：已采纳（2026-09-25 定案并实现）
- 日期：2026-09-25
- 实现提交：`4080ea050f7bf1971a0a542b8d7db3a67ffe0c95`（三原则主体：思考强度发送侧 / 缓存分段计价 / 观测落库与正名）；`2cd4ba2410ead930d62fbda20afc212d117df523`（dashboard 前端配套）

## 背景（Context）

用户需要按用途控制模型思考深度（planner 深想、replyer/simulator 快出）。行业现状：各家 API 的思考强度参数名、取值集合、语义机制三重分裂（OpenAI `reasoning_effort` 七档模型相关、Anthropic `output_config.effort`、Gemini `thinking_level`/`thinkingBudget`、Qwen `enable_thinking`+整数预算、GLM/DeepSeek `thinking.type` 开关），且档位词汇随模型代际漂移（`minimal`/`xhigh`/`none` 增删频繁）；同名档位在不同厂商有五种物理行为（直传/译成预算/逐 token 兑现/收下忽略照常计费/直接 400）。本项目只接 OpenAI 兼容端点。ADR-032 曾裁决思考强度控制面"暂不归一，将来按需单开议题"——真实需求现已出现，本篇即该留白的兑现。

## 决策（Decision）

核心一句：**profile 级自由字符串 `reasoning_effort`（设了才随请求发出，不做封闭枚举校验）+ provider 级 `extra_body` 自由 dict 原样合并进请求体（承载一切协议外方言字段）+ 引擎与中立契约对方言内容零感知。**

- `reasoning_effort` 挂 `[llm_profiles.<name>]`（用途诉求归用途档，与 temperature 同构）；空串 = 不控制 = 请求不含该字段
- `extra_body` 挂 `[[llm_providers]]`（方言是端点属性，provider 即方言边界；聚合端点拆多 provider 指向同址）；只有 OpenAI 适配端消费，引擎不读不传不存
- 传递路径照抄 temperature 双路径先例：`generate()` 签名可选参数（显式覆盖）+ `GenerateRequest` 可选字段 + 引擎装配"显式优先、profile 兜底"

## 替代方案（Alternatives）

- **封闭枚举档位**：否决——档位集合模型相关且代际漂移，Schema 封死必腐烂，且拒合法值（如 DeepSeek 自创 `ultra`）
- **框架内建厂商方言翻译表**：否决——上游每季度变（DeepSeek 为 Responses 格式另立 `reasoning.effort` 即例），方言知识写进框架代码注定过时；MaiBot 的 extra_params + vendor 专属前端组件路线即此弊
- **档位字段放模型级**：否决——同用途主备模型无法按用途差异化，temperature 先例作废需新造"模型级生成参数"通路
- **只做 profile 默认值单路径**：小拒——与 temperature 双路径形状不一致的认知成本高于一个字段的声明成本（用户拍板一致性优先）

## 后果（Consequences）

- 未配置行为与历史完全一致（设了才发）；不支持的端点收到不支持值会报错并走既有故障切换，损害有界
- 语义漂移（如 DeepSeek 将 `medium` 映射为 `high`、Kimi 收下忽略）框架不可控——观测面以 `usage` 原文落库兜底（解析即弃环节必须留 raw），用户可据实际思考 token 数校准配置
- 请求历史白名单入账 `reasoning_effort`；`llm_requests` 增 `reasoning_tokens` / `usage_raw_json` 列与 `client_type`→`profile_name` 正名随同一批迁移（详见草稿 §2）
- 配置新增字段经漂移写回自动出现在已有配置文件，description 自动生成为字段上方注释——用户可见可懂，零额外机制

## 关联

- ADR-032"归一化分治"：本篇兑现其"控制面将来按需单开议题"的留白
- 讨论草稿（兼权威）：`.omo/drafts/llm-capability-config.md`（含各议题决策与亲核事实）
- 参考对照：MaiBot `ModelInfo.extra_params` 路线（`E:\01_Projects\Code\AI\MaiBot\MaiBot-v1.0.0`，本机）
