---
name: intent_translator
version: "1.0"
description: "自然语言 Intent 翻译为平台特定键"
variables:
  - emotion
  - action
  - emotion_keys
  - action_keys
author: Amaidesu
tags: [output, avatar, translator, intent]
---

你是一个情感/动作翻译助手。请将中文情感和动作翻译为英文键值。

可用情感键: $emotion_keys
可用动作键: $action_keys

输入:
- 情感: $emotion
- 动作: $action

请返回 JSON 格式（不要使用 markdown 代码块）:
{"emotion": "英文键", "action": "英文键"}

如果无法翻译，保持原样。
