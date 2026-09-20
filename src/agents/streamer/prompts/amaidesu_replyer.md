---
name: amaidesu_replyer
version: "3.0"
description: "Amaidesu 表达引擎本轮输入模板（每轮变化段）：Planner 决策 + 本批弹幕 + 当前流程单环节，渲染为本轮 user 消息，跟在对话历史之后。人设/风格/规则等全程稳定段在 amaidesu_replyer_system。"
variables:
  - plan
  - danmaku_batch
  - rundown
author: Amaidesu
tags: [decision, live, vtuber, replyer, tool-calling]
---

# 本轮输入

## Planner 决策

上游 Planner 已经完成了"是否回复 / 回复谁 / 聊什么 / 用什么情绪基调"的决策。你**只需执行**，不需要重新判断是否值得回复——Planner 已保证本批需要回复。

$plan

其中：
- `target`：本次应当回应的对象（弹幕用户 / 游戏角色 / 话题）。
- `topic_summary`：本次回应应当围绕的话题摘要。
- `reply_guidance`：Planner 给出的回复方向、情绪基调或风格提示（如"回应夸奖、带点得意"、"安慰失落的观众"）。

请严格围绕 Planner 指定的 `target` 与 `topic_summary` 组织回复，并把 `reply_guidance` 作为语气/情绪的参考。

## 本批弹幕

$danmaku_batch

> **当本批弹幕为"（本批无弹幕）"时**：表示这是**主动发言**（冷场救场 / 定时话题 / 运营指令触发），观众此刻没有说话。此时不要"等观众接话"，而是**以你自己输出为主**——按 system 中的"主动发言规则"执行。

## 当前流程单环节

$rundown
