# 测试目录结构

测试目录与 `src/` 的 v2 布局对应：`modules/`（共享模块）+ `agents/`（业务 Agent）。

## 目录结构

```
tests/
├── architecture/                    # 架构约束测试（分层依赖方向 / 事件流约束）
├── modules/                         # 模块层测试（对应 src/modules/）
│   ├── agents/                      # Agent 框架 + StreamerAgent 组件
│   │   └── streamer/                # planner / replyer / agenda / 决策循环
│   ├── base/                        # NormalizedMessage 等基类
│   ├── collectors/                  # bilibili / console / mock / screen / stt
│   ├── config/                      # 配置系统（Schema / 升级 hook / 漂移写回）
│   ├── context/                     # ContextAssembler 快照组装
│   ├── dashboard/                   # Dashboard API 与服务
│   ├── events/                      # EventBus / 拦截器 / Payload 注册表
│   ├── llm/                         # LLMManager 与客户端
│   ├── memory/                      # MemoryProvider / SimpleMemory
│   ├── storage/                     # SQLite 存储层
│   ├── tools/                       # 工具契约（ToolSpec / Registry / ResultBlock）
│   │   └── output/                  # 渲染工具（vts / warudo / tts / obs / subtitle…）
│   ├── tts/                         # TTS 客户端
│   └── types/                       # 共享类型（bili 消息等）
├── integration/                     # 集成测试
└── conftest.py                      # pytest 配置和共享 fixtures
```

## 运行测试

```bash
uv run pytest tests/ -q              # 全量
uv run pytest tests/modules/ -q      # 模块层
uv run pytest tests/architecture/ -q # 架构约束
```

## 命名规范

- `test_<component>.py` — 组件测试（如 `test_event_bus.py`）
- `test_<name>_collector.py` / `test_<name>_interceptor.py` — 采集器 / 拦截器
- 组件迁移时测试随迁；组件删除且迁移文档标记 DISCARD 时测试方可删除并注明依据

## 相关文档

- [测试指南](../docs/development/testing-guide.md) - 测试规范和最佳实践
