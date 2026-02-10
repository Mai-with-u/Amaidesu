<!--
TODO: 此模板当前未被使用

预期用途: 消息摘要生成，用于处理长文本或批量消息
集成位置: 可能用于 Input Domain 的预处理 Pipeline 或 Decision Provider
集成方式: 当输入消息过长或需要批量处理时，调用此模板生成摘要

预期变量:
  - messages: 需要摘要的消息列表或长文本
  - max_length: 摘要的最大长度限制（字数）

输出格式: 纯文本摘要

预期集成时间: 待定
负责人: TBD
-->

---
description: 输入摘要模板
version: 1.0
variables:
  - messages
  - max_length
author: Amaidesu
tags: [input, summarize]
---
请将以下消息摘要为不超过 $max_length 字的总结：

$messages
