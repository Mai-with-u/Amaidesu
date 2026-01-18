# 插件内直接使用 OpenAI 客户端评估

## 问题描述

多个插件和 Provider 直接使用 OpenAI 客户端（`openai.AsyncOpenAI`/`openai.OpenAI`），而不是通过统一的 `LLMService`。这导致配置、重试、计费统计分散。

## 受影响的文件

| 文件 | 用途 | 使用方式 |
|------|------|---------|
| `src/providers/vts_provider.py` | VTS Provider 的 LLM 智能热键匹配 | `AsyncOpenAI` |
| `src/plugins/emotion_judge/emotion_judge_decision_provider.py` | 情感判断决策 Provider | `AsyncOpenAI` |
| `src/plugins/vtube_studio/providers/vts_output_provider.py` | VTS Output Provider 的 LLM 智能热键匹配 | `OpenAI` |

## 迁移可行性分析

### LLMService 当前接口

```python
async def chat(
    self,
    prompt: str,
    *,
    backend: str = "llm",
    system_message: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
```

### 兼容性评估

**好消息**：所有受影响文件的 LLM 使用方式都与 `LLMService.chat()` 兼容！

例如，`EmotionJudgeDecisionProvider` 的当前用法：

```python
response = await self.client.chat.completions.create(
    model=...,
    messages=[
        {"role": "system", "content": "...系统提示..."},
        {"role": "user", "content": text},
    ],
    max_tokens=...,
    temperature=...,
)
```

可以迁移为：

```python
result = await llm_service.chat(
    prompt=text,
    backend="llm",  # 或使用配置的后端名称
    system_message="...系统提示...",
    temperature=...,
    max_tokens=...,
)
```

### 需要处理的变化

1. **响应结构变化**：
   - OpenAI 客户端：`response.choices[0].message.content`
   - LLMService：`result.content`（`LLMResponse` 对象）

2. **错误处理**：
   - OpenAI 客户端：捕获异常
   - LLMService：检查 `result.success` 和 `result.error`

3. **LLMService 访问**：
   - 需要确定如何获取 `llm_service` 实例
   - 对于 Provider：可能需要在 `setup()` 中通过 `event_bus` 或注入
   - 对于 Plugin：可能需要从 `core` 获取

## 迁移方案

### 方案 A：通过 EventBus 获取 LLMService（推荐）

```python
async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
    self.event_bus = event_bus
    self.config = config

    # 订阅 core.ready 事件获取 llm_service
    self.event_bus.on("core.ready", self._on_core_ready, priority=10)

    # ...

async def _on_core_ready(self, event_name: str, event_data: dict, source: str) -> None:
    """处理 core.ready 事件，获取 LLMService 实例"""
    core = event_data.get("core")
    if core and hasattr(core, "llm_service"):
        self.llm_service = core.llm_service
        self.logger.info("已获取 LLMService 实例")
    else:
        self.logger.warning("未获取到 LLMService 实例")
```

### 方案 B：通过 AmaidesuCore 注入（如果可用）

对于 Plugin：
```python
async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
    self.event_bus = event_bus
    self.config = config

    # 如果有 core 参数，获取 llm_service
    if hasattr(config, "core"):
        self.llm_service = config.core.llm_service
```

### 方案 C：使用 fallback 机制（过渡期方案）

```python
async def _call_llm(self, prompt: str, system_message: str, **kwargs) -> str:
    """调用 LLM，优先使用 LLMService，fallback 到 OpenAI 客户端"""
    if self.llm_service:
        result = await self.llm_service.chat(
            prompt=prompt,
            system_message=system_message,
            **kwargs,
        )
        if result.success:
            return result.content
        else:
            self.logger.warning(f"LLMService 调用失败: {result.error}")

    # Fallback 到 OpenAI 客户端
    if self.client:
        response = await self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )
        return response.choices[0].message.content
```

## 迁移收益

1. **统一配置管理**：LLM API 配置集中在 `[llm]`/`[llm_fast]` 配置节
2. **内置重试机制**：LLMService 自动重试失败的请求
3. **Token 统计**：统一记录所有 LLM 调用的 Token 使用量
4. **后端切换**：可以轻松切换不同的 LLM 后端（OpenAI、Ollama 等）
5. **降低依赖**：插件不再需要直接依赖 `openai` 包

## 迁移风险

1. **向后兼容性**：需要确保迁移后功能不变
2. **配置变更**：可能需要更新配置文件
3. **测试覆盖**：需要充分测试 LLM 调用功能
4. **LLMService 可用性**：需要确认 LLMService 总是可用

## 建议的迁移顺序

### 阶段 1：准备阶段
1. 确认 LLMService 在 AmaidesuCore 中正确初始化
2. 确定 Provider/Plugin 如何访问 LLMService（推荐方案 A）
3. 创建测试用例验证迁移后的功能

### 阶段 2：迁移阶段
1. 迁移 `EmotionJudgeDecisionProvider`（相对独立，适合先做）
2. 迁移 `vts_provider.py`
3. 迁移 `vts_output_provider.py`

### 阶段 3：验证阶段
1. 运行所有相关测试
2. 验证 LLM 功能正常工作
3. 验证 Token 统计正确
4. 更新文档

## 实施建议

由于这是一个低优先级问题，建议：
1. **短期**：暂时保留现状，在文档中标注"推荐逐步迁移到 LLMService"
2. **中期**：在下一个大版本中实施迁移
3. **长期**：将所有 LLM 调用统一到 LLMService

## 待确认问题

1. **LLMService 访问方式**：Provider/Plugin 应该如何获取 LLMService 实例？
2. **配置映射**：插件特定的 LLM 配置（如 `base_url`、`model`）如何映射到 LLMService 的后端配置？
3. **后端命名**：是否需要为每个插件创建专用的 LLM 后端（如 `llm_emotion`、`llm_vts`）？

## 结论

迁移到 LLMService 是可行的，且收益显著。建议采用方案 A（通过 EventBus 获取 LLMService）并使用 fallback 机制作为过渡期方案。
