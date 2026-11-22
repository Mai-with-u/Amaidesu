# 项目文档

本目录包含 Amaidesu 项目的完整技术文档。

## 入门

- [快速开始](getting-started.md)
- [直播运行手册](guides/runbook.md)

## 开发指南

- [组件开发](guides/component/README.md)
- [测试](guides/testing.md)
- [提示词](guides/prompt.md)
- [Skill 编写与接入](guides/skill.md)
- [依赖注入](guides/dependency-injection.md)
- [模拟直播间](guides/simulator.md)
- [LAPLACE Event Bridge 弹幕接入](guides/laplace-bridge.md)
- [文字冒险 Agent](guides/text-adv-agent.md)
- [Minecraft Agent](guides/minecraft-agent.md)
- [发布](guides/release.md)
- [GPT-SoVITS 语音服务部署](guides/gptsovits.md)
- [主播记忆重设计发版前门槛](guides/memory-release-gate.md)
- [虚拟形象接入（Warudo / VRChat / 表情差分立绘）](guides/avatar.md)
- [与 MaiBot 同步（直播近况 / 整场记录）](guides/maibot-sync.md)

## 架构与设计

- [架构总览](architecture/v2-architecture.md)（先读这篇）
- [数据流边界规则](conventions/data-flow.md)
- [事件系统](architecture/event-system.md)
- [事件命名](conventions/event-naming.md)
- [工具层](architecture/tools.md)（注册表 / 可见名单 / 熔断探活 / 异步任务 / MCP 集成）
- [LLM 接入层](architecture/llm.md)（三层配置 / 用途档 / 故障切换 / 观测出库）
- [流程单机制](architecture/rundown.md)
- [存储](architecture/storage.md)
- [语音合成（TTS）](architecture/tts.md)
- [字幕](architecture/subtitle.md)（播放跟随 / 多后端广播）
- [屏幕感知（Vision）](architecture/vision.md)
- [游戏 Agent（Minecraft）](architecture/minecraft-agent.md)
- [主播 Agent 总述](architecture/streamer-agent.md)
- [动态主从](architecture/dynamic-lead.md)
- [虚拟形象（Avatar）](architecture/avatar-platform.md)
- [主播上下文构成](architecture/streamer-context.md)
- [主播记忆](architecture/memory/README.md)（承诺表 + 场次档案；下播整理 / 快照 / 记忆摘要 / 线索 / 回忆 / 审核删除 / 游戏侧各一篇）
- [配置系统](architecture/config.md)（七文件 / 加载管线 / 版本钩子 / 热生效）

## 架构决策记录

- [ADR 清单](decisions/README.md)

## 快速链接

- [项目主页](../README.md)
- [AGENTS.md](../AGENTS.md)
- [GLOSSARY.md](../GLOSSARY.md)（领域词汇表）
