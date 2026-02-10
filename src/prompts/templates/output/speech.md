<!--
TODO: 此模板当前未被使用

预期用途: TTS 语音合成的情感和参数指导
集成位置: TTSOutputProvider 或 GPTSoVITSOutputProvider
集成方式: 在 TTS 合成前调用 LLM 分析文本情感，生成语音参数

预期变量:
  - text: 待朗读文本
  - emotion: 情感类型 (neutral/happy/sad/angry/surprised/love)
  - speed: 语速 (0.5-2.0)
  - pitch: 音调 (0.5-2.0)

输出格式: 参数字典或配置对象

预计集成时间: 待定
负责人: TBD
-->

---
description: 语音输出模板
version: 1.0
variables:
  - text
  - emotion
author: Amaidesu
tags: [output, speech]
---
请以 $emotion 的语气朗读以下内容：

$text
