# 测试目录结构

测试目录与 `src/` 的 v2 布局对应：`modules/`（共享模块）+ `agents/`（业务 Agent）。

## 双测试根分工（新测试放哪边）

仓库有两个业务测试根，按**被测对象的协作范围**分工：

- `tests/modules/`（组件单测根）：被测对象是**单一 src 组件**，测试路径镜像 `src/` 包路径（如 `src/agents/streamer/message_buffer.py` → `tests/modules/agents/streamer/test_message_buffer.py`）。依赖用 mock 隔离，不触真实存储 / 事件总线装配 / 跨模块链路。
- `tests/agents/`（跨模块接线根）：被测对象是 **Agent 与其它模块的协作链路**——存储读写（SQLite 落盘）、事件发射与订阅接线、TTS/VTS 管线、Dashboard 调试门面、端到端装配。判定口诀：断言需要"两个以上真实模块一起转"才成立的，放这里。

**新测试归位判据**（按序判断，命中即停）：

1. 只测一个类/函数的纯逻辑（mock 掉协作者）→ `tests/modules/`（镜像 src 路径）。
2. 断言跨模块协作结果（真实 SQLite、EventBus 装配、事件发射链、多组件管线）→ `tests/agents/<agent>/`。
3. 分层依赖 / 事件流约束 → `tests/architecture/`。
4. 仍拿不准 → 看现有同类测试在哪边，跟随放置；两根都放会形成重复覆盖，禁止。

现有 `tests/agents/streamer/` 中的个别文件（如 `test_utterance_queue.py`、`test_message_budget.py`）是历史遗留的组件单测形态，因其断言面与接线链路耦合（决策循环调用上下文），保留原位不迁移。

## 目录结构

```
tests/
├── architecture/                    # 架构约束测试（分层依赖方向 / 事件流约束）
├── agents/                          # 跨模块接线测试（对应 src/agents/，见上文分工节）
├── config/                          # 配置系统测试（Schema / 升级 hook / 迁移 / 漂移写回）
├── dashboard/                       # Dashboard API 与服务测试
├── integration/                     # 集成测试
├── modules/                         # 模块层组件单测（镜像 src/modules/ 与 src/agents/）
│   ├── agents/                      # Agent 框架 + StreamerAgent 组件
│   │   └── streamer/                # planner / replyer / rundown / 决策循环
│   ├── base/                        # 基类测试占位（当前仅 __init__.py，无测试文件）
│   ├── collectors/                  # bilibili / console / stt
│   ├── config/                      # 配置加载 / 回归修复
│   ├── dashboard/                   # Dashboard 服务
│   ├── events/                      # EventBus / 拦截器 / Payload 注册表
│   ├── llm/                         # LLMManager 与客户端
│   ├── memory/                      # MemoryProvider / SimpleMemory
│   ├── storage/                     # SQLite 存储层
│   ├── tools/                       # 工具契约（ToolSpec / Registry / as_tool_impl / tasks）
│   │   └── output/                  # 渲染工具（remote_stream）
│   ├── tts/                         # TTS 客户端
│   ├── types/                       # 共享类型（bili 消息等）
│   └── …（audio / avatar / logging / mcp / prompts / session / simulator /
│         studio / subtitle / vision 等，均镜像 src/modules/ 同名包）
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

- [测试指南](../docs/guides/testing.md) - 测试规范和最佳实践
