# 测试目录结构

测试目录与 `src/` 的 v2 布局对应：`modules/`（共享模块）+ `agents/`（业务 Agent）。

## 测试树归属规则（新测试放哪边）

测试树按**被测源码包镜像**归属：

- `tests/agents/` 镜像 `src/agents/`——测业务 Agent（streamer / minecraft / text_adv / game_2048 等）的测试全部放这里，无论单组件逻辑还是跨模块接线。
- `tests/modules/` 镜像 `src/modules/`——只放测 `src/modules/` 组件的测试；其中 `tests/modules/agents/` 只放 Agent 框架（base / manager / supervisor / delegation / factory / companions）的测试。

**新测试归位判据**（按序判断，命中即停）：

1. 看被测逻辑的主包：主包在 `src/agents/` 下 → `tests/agents/` 镜像路径；主包在 `src/modules/` 下 → `tests/modules/` 镜像路径。
2. 分层依赖 / 事件流约束 → `tests/architecture/`。
3. 仍拿不准 → 沿 import 链找主包再按第 1 条归位；同一被测对象在两棵树都放会形成重复覆盖，禁止。

## 目录结构

```
tests/
├── architecture/                    # 架构约束测试（分层依赖方向 / 事件流约束）
├── agents/                          # 业务 Agent 测试（镜像 src/agents/：streamer / minecraft / text_adv / game_2048）
├── config/                          # 配置系统测试（Schema / 升级 hook / 迁移 / 漂移写回）
├── dashboard/                       # Dashboard API 与服务测试
├── integration/                     # 集成测试
├── modules/                         # 模块层测试（镜像 src/modules/）
│   ├── agents/                      # Agent 框架（base / manager / supervisor / delegation / factory / companions）
│   ├── base/                        # 基类测试占位（当前仅 __init__.py，无测试文件）
│   ├── collectors/                  # bilibili / console / stt
│   ├── config/                      # 配置加载 / 回归修复
│   ├── dashboard/                   # Dashboard 服务
│   ├── events/                      # EventBus / 拦截器 / Payload 注册表
│   ├── llm/                         # LLMManager 与客户端
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
