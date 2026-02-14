# 核心类型定义模块

存放跨域共享的类型定义，避免循环依赖。

## 概述

`src/modules/types/` 模块存放被多个 Domain 共享的类型：
- 意图类型（EmotionType, ActionType, Intent）
- 渲染参数（RenderParameters）
- Provider 基类
- 标准化消息

## 主要组件

| 文件 | 功能 |
|------|------|
| `intent.py` | 意图类型（EmotionType, ActionType, Intent） |
| `render_parameters.py` | 渲染参数（RenderParameters） |
| `base/` | Provider 基类 |

### base 子模块

| 文件 | 类 | 功能 |
|------|-----|------|
| `input_provider.py` | InputProvider | 输入 Provider 基类 |
| `decision_provider.py` | DecisionProvider | 决策 Provider 基类 |
| `output_provider.py` | OutputProvider | 输出 Provider 基类 |
| `normalized_message.py` | NormalizedMessage | 标准化消息 |
| `pipeline_stats.py` | PipelineStats | 管道统计 |

## 意图类型

### EmotionType

```python
from src.modules.types import EmotionType

# 可用情感类型
EmotionType.NEUTRAL    # 中性
EmotionType.HAPPY     # 开心
EmotionType.SAD       # 难过
EmotionType.ANGRY     # 生气
EmotionType.SURPRISED # 惊讶
EmotionType.LOVE      # 喜欢
EmotionType.SHY       # 害羞
EmotionType.EXCITED   # 兴奋
EmotionType.CONFUSED  # 困惑
EmotionType.SCARED    # 害怕
```

### ActionType

```python
from src.modules.types import ActionType

# 可用动作类型
ActionType.EXPRESSION   # 表情
ActionType.HOTKEY      # 热键触发
ActionType.EMOJI       # emoji 表情
ActionType.BLINK       # 眨眼
ActionType.NOD         # 点头
ActionType.SHAKE       # 摇头
ActionType.WAVE        # 挥手
ActionType.CLAP        # 鼓掌
ActionType.STICKER     # 贴图
ActionType.MOTION      # 动作
ActionType.CUSTOM      # 自定义
ActionType.GAME_ACTION # 游戏动作
ActionType.NONE        # 无动作
```

### Intent

```python
from src.modules.types import Intent, IntentAction

# 创建意图
intent = Intent(
    original_text="你好",              # 原始文本
    response_text="你好呀",           # 响应文本
    emotion=EmotionType.HAPPY,       # 情感类型
    actions=[
        IntentAction(
            type=ActionType.HOTKEY,
            params={"hotkey_id": "smile_01"}
        ),
        IntentAction(
            type=ActionType.EXPRESSION,
            params={"expression": "happy"}
        )
    ],
    source=SourceContext(
        source="bili_danmaku",
        importance=0.8
    )
)
```

### IntentAction

```python
from src.modules.types import IntentAction, ActionType

action = IntentAction(
    type=ActionType.HOTKEY,
    params={
        "hotkey_id": "smile_01",
        "duration_ms": 1000
    }
)
```

### SourceContext

```python
from src.modules.types import SourceContext

context = SourceContext(
    source="console_input",    # 来源
    importance=1.0,          # 重要性 (0-1)
    session_id="default",    # 会话 ID
    metadata={}             # 额外元数据
)
```

## 渲染参数

### RenderParameters

```python
from src.modules.types import RenderParameters

params = RenderParameters(
    text="你好",                    # 显示文本
    emotion=EmotionType.HAPPY,    # 情感
    actions=[...],                 # 动作列表
    metadata={}                   # 额外数据
)
```

### ExpressionParameters

```python
from src.modules.types import ExpressionParameters

expr_params = ExpressionParameters(
    text="你好",
    emotion=EmotionType.HAPPY,
    intensity=0.8,                 # 表情强度
    duration_ms=2000,              # 持续时间
)
```

## Provider 基类

### InputProvider

```python
from src.modules.types import InputProvider

class MyInputProvider(InputProvider):
    name = "my_input"

    async def start(self):
        """启动数据采集"""
        async for data in self._collect_data():
            yield data

    async def stop(self):
        """停止数据采集"""
        pass
```

### DecisionProvider

```python
from src.modules.types import DecisionProvider

class MyDecisionProvider(DecisionProvider):
    name = "my_decision"

    async def setup(self, event_bus):
        """初始化"""
        await event_bus.subscribe(
            CoreEvents.DATA_MESSAGE,
            self._on_message
        )

    async def _on_message(self, event, message):
        # 处理消息
        intent = await self._process_message(message)
        if intent:
            await self._publish_intent(intent)
```

### OutputProvider

```python
from src.modules.types import OutputProvider

class MyOutputProvider(OutputProvider):
    name = "my_output"

    async def setup(self, event_bus):
        """初始化"""
        await event_bus.subscribe(
            CoreEvents.DECISION_INTENT,
            self._on_intent
        )

    async def _on_intent(self, event, intent, source):
        # 渲染输出
        await self._render(intent)
```

## 标准化消息

### NormalizedMessage

```python
from src.modules.types import NormalizedMessage

message = NormalizedMessage(
    content="用户消息",           # 消息内容
    message_type="text",         # 消息类型
    source="console",           # 来源
    session_id="default",        # 会话 ID
    metadata={}                 # 额外元数据
)
```

## 管道统计

### PipelineStats

```python
from src.modules.types import PipelineStats

stats = PipelineStats(
    messages_processed=100,
    messages_filtered=10,
    average_processing_time_ms=5.0,
    last_processed_at=datetime.now()
)
```

---

*最后更新：2026-02-14*
