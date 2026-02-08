# MaiBot 插件集成文档

## 概述

Amaidesu 作为 AI VTuber 核心系统，负责智能决策和参数生成。通过 MaiBot Action 插件，Amaidesu 可以向 MaiBot 发送动作建议，实现自动化的虚拟主播行为控制。

本文档详细介绍如何在 MaiBot 中集成 Amaidesu 的 Action 建议，实现完整的 AI 到虚拟形象的流程。

### 系统架构

```
[弹幕/游戏输入]
        ↓
[Amaidesu] → [ActionSuggestionMessage] → [MaiBot] → [VTS/Warudo]
```

- **Amaidesu**: 负责接收输入、生成意图和动作建议
- **MaiBot**: 接收 Action 建议，执行对应的动作
- **VTS/Warudo**: 通过 MaiBot 控制虚拟形象

## 消息格式

### ActionSuggestionMessage

Amaidesu 通过 `ActionSuggestionMessage` 格式向 MaiBot 发送动作建议。

#### JSON 格式

```json
{
  "message_type": "action_suggestion",
  "intent_id": "uuid-string-1234",
  "original_text": "你叫什么名字？",
  "response_text": "我是 Amaidesu，一个 AI VTuber！",
  "emotion": "happy",
  "suggested_actions": [
    {
      "action_name": "wave",
      "priority": 80,
      "parameters": {
        "hotkey": "wave_animation",
        "duration": 1.0
      },
      "reason": "检测到友好的问候，应该挥手回应",
      "confidence": 0.9
    }
  ],
  "source": "amaidesu",
  "timestamp": 1634567890.123
}
```

#### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `message_type` | string | 是 | 固定值 "action_suggestion" |
| `intent_id` | string | 是 | 唯一标识符 |
| `original_text` | string | 是 | 原始输入文本 |
| `response_text` | string | 是 | AI 回复文本 |
| `emotion` | string | 是 | 情感类型（见 EmotionType 枚举） |
| `suggested_actions` | array | 是 | 建议的动作列表 |
| `source` | string | 否 | 来源标识，默认 "amaidesu" |
| `timestamp` | number | 否 | 时间戳，默认当前时间 |

### ActionSuggestion 结构

每个建议动作包含以下字段：

```json
{
  "action_name": "wave",
  "priority": 80,
  "parameters": {
    "hotkey": "wave_animation",
    "duration": 1.0
  },
  "reason": "检测到友好的问候，应该挥手回应",
  "confidence": 0.9
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `action_name` | string | 是 | 动作名称（对应 MaiBot 插件中的动作名） |
| `priority` | number | 是 | 优先级（0-100，越高越优先） |
| `parameters` | object | 是 | 动作参数（取决于具体动作类型） |
| `reason` | string | 否 | 建议原因（用于调试） |
| `confidence` | number | 否 | 置信度（0.0-1.0） |

## 支持的动作类型

Amaidesu 支持多种动作类型，每种类型都有对应的参数格式：

### 1. 表情动作 (expression)

```json
{
  "action_name": "expression_smile",
  "parameters": {
    "expressions": {
      "mouth_opening": 0.5,
      "cheek_puffing": 0.2
    }
  }
}
```

### 2. 热键动作 (hotkey)

```json
{
  "action_name": "hotkey_wave",
  "parameters": {
    "hotkey": "wave_animation"
  }
}
```

### 3. 动作序列 (motion)

```json
{
  "action_name": "motion_greet",
  "parameters": {
    "sequence": ["look", "wave", "smile"],
    "delay": 0.5
  }
}
```

### 4. TTS 控制动作 (tts)

```json
{
  "action_name": "tts_speak",
  "parameters": {
    "text": "你好！",
    "voice": "female",
    "speed": 1.0
  }
}
```

### 5. 字幕动作 (subtitle)

```json
{
  "action_name": "subtitle_show",
  "parameters": {
    "text": "你好！",
    "duration": 3.0,
    "position": "bottom"
  }
}
```

## 插件示例

### 完整的 MaiBot Action 插件代码

```python
"""
MaiBot Action 插件 - 接收 Amaidesu 的动作建议
"""
import json
import asyncio
import logging
from typing import Dict, Any, Optional
from maim_message import MessageBase
from maibot.plugin import BasePlugin, PluginConfig

logger = logging.getLogger("MaiBotActionPlugin")

class ActionSuggestionHandler:
    """Action 建议处理器"""

    def __init__(self):
        self.pending_actions = []
        self.action_cache = {}

    def handle_action_suggestion(self, message: MessageBase):
        """处理 Action 建议消息"""
        try:
            # 解析消息
            message_data = json.loads(message.message_segment.data)

            if message_data.get("message_type") != "action_suggestion":
                return

            logger.info(f"收到 Action 建议: {message_data['original_text']}")
            logger.info(f"情感: {message_data['emotion']}")

            # 处理建议动作
            for action in message_data.get("suggested_actions", []):
                self.execute_action(action, message_data)

        except Exception as e:
            logger.error(f"处理 Action 建议失败: {e}", exc_info=True)

    def execute_action(self, action: Dict[str, Any], context: Dict[str, Any]):
        """执行单个动作"""
        action_name = action["action_name"]
        priority = action["priority"]
        parameters = action["parameters"]
        confidence = action.get("confidence", 0.5)

        logger.info(f"执行动作: {action_name} (优先级: {priority}, 置信度: {confidence:.2f})")

        # 根据动作类型执行
        if action_name.startswith("expression_"):
            self.execute_expression_action(action_name, parameters)
        elif action_name.startswith("hotkey_"):
            self.execute_hotkey_action(action_name, parameters)
        elif action_name.startswith("motion_"):
            self.execute_motion_action(action_name, parameters)
        elif action_name.startswith("tts_"):
            self.execute_tts_action(action_name, parameters)
        elif action_name.startswith("subtitle_"):
            self.execute_subtitle_action(action_name, parameters)
        else:
            logger.warning(f"未知的动作类型: {action_name}")

    def execute_expression_action(self, action_name: str, params: Dict[str, Any]):
        """执行表情动作"""
        # 调用 MaiBot 的 API 执行 VTS 表情
        vts_params = params.get("expressions", {})

        # 示例：通过 WebSocket 发送到 VTS
        logger.info(f"设置 VTS 表情: {vts_params}")
        # 这里应该调用实际的 VTS API

    def execute_hotkey_action(self, action_name: str, params: Dict[str, Any]):
        """执行热键动作"""
        hotkey = params.get("hotkey")

        if hotkey:
            # 示例：触发热键
            logger.info(f"触发热键: {hotkey}")
            # 这里应该调用实际的热键触发逻辑

    def execute_motion_action(self, action_name: str, params: Dict[str, Any]):
        """执行动作序列"""
        sequence = params.get("sequence", [])
        delay = params.get("delay", 0.5)

        logger.info(f"执行动作序列: {sequence}")

        # 依次执行动作
        for motion in sequence:
            self.execute_single_motion(motion, params)
            if delay > 0:
                time.sleep(delay)

    def execute_tts_action(self, action_name: str, params: Dict[str, Any]):
        """执行 TTS 动作"""
        text = params.get("text")
        voice = params.get("voice", "default")
        speed = params.get("speed", 1.0)

        if text:
            logger.info(f"播放 TTS: {text} (声音: {voice}, 速度: {speed})")
            # 这里应该调用实际的 TTS API

    def execute_subtitle_action(self, action_name: str, params: Dict[str, Any]):
        """执行字幕动作"""
        text = params.get("text")
        duration = params.get("duration", 3.0)
        position = params.get("position", "bottom")

        if text:
            logger.info(f"显示字幕: {text} (位置: {position}, 持续: {duration}s)")
            # 这里应该调用实际的字幕显示逻辑

    def execute_single_motion(self, motion: str, params: Dict[str, Any]):
        """执行单个动作"""
        logger.info(f"执行动作: {motion}")
        # 这里应该调用实际的动作执行逻辑

class MaiBotActionPlugin(BasePlugin):
    """MaiBot Action 插件"""

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.action_handler = ActionSuggestionHandler()

    async def on_load(self):
        """插件加载时调用"""
        logger.info("MaiBot Action 插件已加载")

        # 订阅来自 Amaidesu 的消息
        self.maim_bot.message_handler.register(
            "amaidesu",
            self.handle_amaidesu_message
        )

    async def on_unload(self):
        """插件卸载时调用"""
        logger.info("MaiBot Action 插件已卸载")

        # 取消订阅
        self.maim_bot.message_handler.unregister(
            "amaidesu",
            self.handle_amaidesu_message
        )

    def handle_amaidesu_message(self, message: MessageBase):
        """处理来自 Amaidesu 的消息"""
        try:
            # 只处理 action_suggestion 类型的消息
            if message.message_segment.type == "action_suggestion":
                self.action_handler.handle_action_suggestion(message)
        except Exception as e:
            logger.error(f"处理 Amaidesu 消息失败: {e}", exc_info=True)

    async def on_action(self, action_name: str, params: Dict[str, Any]) -> Any:
        """执行自定义动作"""
        logger.info(f"执行自定义动作: {action_name}")

        # 这里可以添加自定义动作的处理逻辑
        if action_name == "custom_motion":
            return await self.execute_custom_motion(params)

        return None

    async def execute_custom_motion(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行自定义动作"""
        motion_type = params.get("type", "simple")
        duration = params.get("duration", 1.0)

        logger.info(f"执行自定义动作: {motion_type} (持续: {duration}s)")

        # 模拟执行
        await asyncio.sleep(duration)

        return {
            "status": "completed",
            "duration": duration
        }

# 插件配置
plugin_config = {
    "name": "maibot_action",
    "version": "1.0.0",
    "description": "MaiBot Action 插件，用于接收和执行来自 Amaidesu 的动作建议",
    "author": "Amaidesu Team",
    "config": {
        "enable_auto_execute": True,
        "max_priority": 100,
        "min_confidence": 0.5,
        "action_timeout": 5.0
    }
}

# 创建插件实例
action_plugin = MaiBotActionPlugin(plugin_config)
```

## 集成步骤

### 1. 在 MaiBot 中安装插件

1. 将插件代码保存为 `maibot_action_plugin.py`
2. 将其放在 MaiBot 的插件目录中
3. 在 MaiBot 的配置文件中启用插件：

```json
{
  "plugins": {
    "maibot_action": {
      "enabled": true,
      "config": {
        "enable_auto_execute": true,
        "max_priority": 100,
        "min_confidence": 0.5,
        "action_timeout": 5.0
      }
    }
  }
}
```

### 2. 配置消息路由

确保 MaiBot 能够接收来自 Amaidesu 的消息：

```python
# 在 MaiBot 主程序中配置消息路由
from maim_bot import MaiBot

# 创建 MaiBot 实例
maibot = MaiBot()

# 注册消息处理器
maibot.message_handler.register(
    "amaidesu",
    handle_amaidesu_message
)

# 启动消息监听
maibot.start_message_listener()
```

### 3. 配置 Amaidesu 的输出

在 Amaidesu 的配置文件中启用 MaiBot 输出提供者：

```toml
# config.toml
[providers.output]
enabled_outputs = ["maibot"]

[providers.output.outputs.maibot]
type = "maibot"
host = "localhost"
port = 8080
webhook_url = "http://localhost:8080/amaidesu"
```

### 4. 运行和测试

1. 启动 MaiBot
2. 启动 Amaidesu
3. 测试弹幕输入，验证动作执行

## 配置选项

### MaiBot 插件配置

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_auto_execute` | boolean | true | 是否自动执行建议的动作 |
| `max_priority` | number | 100 | 最大优先级阈值 |
| `min_confidence` | number | 0.5 | 最小置信度阈值 |
| `action_timeout` | number | 5.0 | 动作执行超时时间（秒） |

### Amaidesu 输出配置

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `host` | string | "localhost" | MaiBot 服务器地址 |
| `port` | number | 8080 | MaiBot 服务器端口 |
| `webhook_url` | string | - | Webhook 回调地址 |

## 最佳实践

### 1. 动作优先级处理

```python
# 在插件中实现优先级过滤
def filter_actions(self, actions: list) -> list:
    """根据配置过滤动作"""
    min_priority = self.config.get("max_priority", 100)
    min_confidence = self.config.get("min_confidence", 0.5)

    filtered = []
    for action in actions:
        if (action["priority"] >= min_priority and
            action.get("confidence", 1.0) >= min_confidence):
            filtered.append(action)

    return sorted(filtered, key=lambda x: x["priority"], reverse=True)
```

### 2. 动作冲突处理

```python
# 处理动作冲突
def handle_action_conflicts(self, actions: list) -> list:
    """处理动作冲突，返回唯一可执行的动作"""
    conflicting_types = ["expression", "hotkey", "motion"]

    result = []
    seen_types = set()

    for action in actions:
        action_type = action["action_name"].split("_")[0]

        if action_type not in seen_types:
            result.append(action)
            seen_types.add(action_type)
        else:
            logger.warning(f"跳过冲突动作: {action['action_name']}")

    return result
```

### 3. 日志记录

```python
# 使用结构化日志
def log_action_execution(self, action: dict, success: bool):
    """记录动作执行日志"""
    log_data = {
        "action_name": action["action_name"],
        "priority": action["priority"],
        "timestamp": time.time(),
        "success": success,
        "error": None if success else "执行失败"
    }

    if success:
        logger.info(f"动作执行成功: {action['action_name']}", extra=log_data)
    else:
        logger.error(f"动作执行失败: {action['action_name']}", extra=log_data)
```

## 故障排除

### 常见问题

1. **消息未收到**
   - 检查网络连接
   - 验证消息路由配置
   - 查看 MaiBot 日志

2. **动作未执行**
   - 检查插件是否启用
   - 验证优先级和置信度设置
   - 查看插件日志

3. **动作执行错误**
   - 检查参数格式
   - 验证 VTS/Warudo 连接
   - 查看错误详情

### 调试模式

```python
# 启用调试日志
logging.basicConfig(level=logging.DEBUG)

# 在插件中启用详细日志
class MaiBotActionPlugin(BasePlugin):
    def __init__(self, config):
        super().__init__(config)
        # 设置日志级别
        self.logger.setLevel(logging.DEBUG)
```

## 示例场景

### 场景1：观众问候响应

```json
{
  "message_type": "action_suggestion",
  "original_text": "你好呀",
  "response_text": "你好！很高兴见到你！",
  "emotion": "happy",
  "suggested_actions": [
    {
      "action_name": "expression_smile",
      "priority": 90,
      "parameters": {
        "expressions": {
          "mouth_opening": 0.5,
          "eye_opening": 0.3
        }
      },
      "reason": "观众友好问候，应该微笑回应",
      "confidence": 0.95
    },
    {
      "action_name": "hotkey_wave",
      "priority": 85,
      "parameters": {
        "hotkey": "wave_animation"
      },
      "reason": "挥手增强互动感",
      "confidence": 0.8
    }
  ]
}
```

### 场景2：观众提问思考

```json
{
  "message_type": "action_suggestion",
  "original_text": "你有什么特长吗？",
  "response_text": "我是一个 AI VTuber，擅长回答问题！",
  "emotion": "neutral",
  "suggested_actions": [
    {
      "action_name": "expression_neutral",
      "priority": 60,
      "parameters": {
        "expressions": {
          "mouth_opening": 0.1
        }
      },
      "reason": "思考问题时保持中性表情",
      "confidence": 0.7
    },
    {
      "action_name": "subtitle_show",
      "priority": 70,
      "parameters": {
        "text": "正在思考...",
        "duration": 2.0
      },
      "reason": "显示思考状态的字幕",
      "confidence": 0.8
    }
  ]
}
```

## 总结

通过 MaiBot Action 插件，Amaidesu 能够实现：
- 自动化的虚拟主播行为控制
- 丰富的动作表达（表情、热键、动作序列等）
- 智能的优先级和置信度处理
- 灵活的配置和扩展性

这种架构实现了从 AI 决策到虚拟形象表达的完整流程，为 AI VTuber 提供了强大的表现能力。