# 决策层设计

## 核心目标

实现可替换的决策系统，支持多种决策方式：

| Provider | 特点 | 适用场景 |
|----------|------|----------|
| **MaiCoreDecisionProvider** | WebSocket + LLM意图解析 | 默认，与 MaiCore 集成 |
| **LocalLLMDecisionProvider** | 直接调用 LLM | 独立部署 |
| **RuleEngineDecisionProvider** | 规则匹配 | 简单场景、低延迟 |

---

## 在 3层架构 中的位置

```
Input Domain
    ↓ NormalizedMessage (normalization.message_ready)
Decision Domain ⭐ 可替换
    ↓ Intent (decision.intent_generated)
Output Domain
```

---

## 核心接口

```python
class DecisionProvider(Protocol):
    """决策Provider接口"""
    
    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        处理标准化消息，返回意图
        
        Args:
            message: 标准化消息（包含文本和结构化数据）
            
        Returns:
            Intent: 意图对象（包含回复文本、情感、动作）
        """
        ...
    
    async def setup(self) -> None:
        """初始化Provider（连接外部服务等）"""
        ...
    
    async def cleanup(self) -> None:
        """清理资源"""
        ...
```

---

## Intent 数据结构

```python
@dataclass
class Intent:
    original_text: str            # 原始输入文本
    response_text: str            # AI 回复文本
    emotion: EmotionType          # 情感类型
    actions: List[IntentAction]   # 动作列表
    metadata: Dict[str, Any]      # 元数据

class EmotionType(Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    LOVE = "love"
```

---

## MaiCoreDecisionProvider（默认实现）

### 设计思路

MaiCore 是群聊机器人，返回自然语言文本，需要 LLM 解析为结构化 Intent。

```
NormalizedMessage
    ↓ 转换为 MessageBase
MaiCore (WebSocket)
    ↓ 返回文本回复
IntentParser (LLM)
    ↓ 解析为结构化数据
Intent
```

### IntentParser 提示词要点

```
你是一个意图解析器，将AI回复解析为结构化数据。

输出JSON格式：
{
  "emotion": "happy|sad|angry|neutral|surprised|love",
  "actions": ["wave", "nod", ...],
  "confidence": 0.0-1.0
}

根据回复内容推断情感和动作，不要猜测不确定的内容。
```

---

## LocalLLMDecisionProvider

### 设计思路

直接调用 LLM，一步生成回复和意图，无需 MaiCore。

```
NormalizedMessage
    ↓
LLMService.chat()
    ↓ 同时返回回复和意图
Intent
```

### 系统提示词要点

```
你是一个AI VTuber。根据用户消息生成回复。

输出JSON格式：
{
  "response": "你的回复文本",
  "emotion": "happy|sad|...",
  "actions": ["wave", ...]
}
```

---

## RuleEngineDecisionProvider

### 设计思路

基于关键词和规则匹配，适用于简单场景。

```python
rules = [
    Rule(keywords=["你好", "hi"], response="你好呀！", emotion="happy"),
    Rule(keywords=["谢谢"], response="不客气~", emotion="happy"),
    Rule(keywords=["再见"], response="拜拜！", emotion="neutral", actions=["wave"]),
]
```

优先级：精确匹配 > 关键词匹配 > 默认回复

---

## 配置示例

```toml
[providers.decision]
enabled = true
active_provider = "maicore"  # 当前激活的 Provider

[providers.decision.providers.maicore]
type = "maicore"
ws_host = "127.0.0.1"
ws_port = 8000

[providers.decision.providers.local_llm]
type = "local_llm"
# 使用全局 [llm] 配置

[providers.decision.providers.rule_engine]
type = "rule_engine"
rules_file = "rules.yaml"
```

---

## 运行时切换

```python
# DecisionManager 支持运行时切换 Provider
await decision_manager.switch_provider("local_llm")
```

切换时会自动执行旧 Provider 的 cleanup() 和新 Provider 的 setup()。
