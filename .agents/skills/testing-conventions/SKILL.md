---
name: testing-conventions
description: 写或改任何 tests/ 下的测试、为生产代码补测试、评审测试改动时使用——用户说"补个测试""这个 bug 加个回归测试""测试偶发红怎么回事""把测试挪个位置""新增能力怎么测"这类请求时。核心纪律：测试文件放在和源码对应的目录；等事件处理完用 wait_until_drained、让时间前进用假时钟，不写真实的 sleep；同一个假对象全库只做一份；想 patch 私有成员就说明生产代码缺注入入口，先修生产代码再写测试。
---

# 测试规约速查

平常写或改测试，只看这一个文件就够，另外两份材料不需要主动去读。它们的分工，只在两种情况下才用得上：

- 发现本文件和规则原文说法不一致时——规则原文在 `tests/AGENTS.md`，以它为准，并回来改本文件。
- 想搞清楚某条规则为什么这么定、或系统学习这个项目时——`docs/guides/testing.md` 是写给人看的详解，带完整代码示例。

另外，大部分规则有守护测试自动检查（`tests/architecture/` 目录下）：写错了测试会直接失败，失败信息里告诉你怎么改。所以不需要背这些规则——犯了错，红灯会教。

## 写测试前过一遍这七条

1. **文件放哪**：测 `src/agents/**` 的放 `tests/agents/**`，测 `src/modules/**` 的放 `tests/modules/**`，架构约束放 `tests/architecture/`。同一个东西的测试不要在两处都放。同类的用例优先并进现有文件，主题确实不同才新建文件。Agent 框架自身的测试可以用真实的 Agent 对象当测试材料（例外名单记录在守护测试里）。
2. **等事件处理完**：发事件后要断言结果，写 `await bus.wait_until_drained()`（EventBus 自带），它会等所有订阅者处理完。不要写 `asyncio.sleep(0.05)` 去猜时长。如果 bus 被藏在被测对象里拿不到，就改代码把它递出来，不要退回去写 sleep。
3. **让时间前进**：测试重试间隔、超时、攒批窗口时用假时钟（fixture 在 `tests/conftest.py`），不要真的等。特别注意：断言写精确值（比如 `remaining_ms == 60_000`）时必须用固定时钟——用真实时钟的话，代码执行本身花掉的几毫秒会让断言偶发失败，这个坑已经踩过三次。
4. **假对象**：先查下面的"基建地图"——每个生产接口全库只做一份假对象。没有就去该域的 `fakes.py` / `helpers.py` 里建一份，不要在测试文件里现写。位置分工：pytest fixture 放 `conftest.py`，假对象和工厂放 `fakes.py` / `helpers.py`。
5. **测试数据**：用现成的工厂函数构造（比如弹幕消息）。没有工厂就先写一个，再用它。
6. **想 patch 私有成员**（名字以 `_` 开头的）：停下来。这说明生产代码少开了一个注入入口，是生产代码的问题——先给生产代码加构造参数（现成例子：`src/modules/mcp/client.py` 的双工厂），再写测试。守护测试会让新出现的私有 patch 直接失败。
7. **两个细节**：项目开了 asyncio 自动模式，异步测试直接写 `async def`，不要加 `@pytest.mark.asyncio`；同一件事测多组输入用 `@pytest.mark.parametrize` 写一张参数表，不要复制粘贴多份测试。

## 测试失败与发现问题时

- **测试红了就是有缺陷**：要么修生产代码，要么修测试，没有第三条路。删测试、加 skip、放宽断言让红变绿，都不算修好。`skip` 只允许在缺某个环境前提时用，并且写清楚缺什么。
- **发现别处测试有问题，登记上报，不要顺手到处修**：比如偶发失败、用真实时钟导致的偶发失败、断言绑死了提示词原文、到处摸私有成员、重复造假对象。上报时带上证据和修法建议，由用户决定要不要单开修复任务；顺手到处修会把无关改动混进来。你自己正在改的那个文件里发现的小问题，顺手修掉不算越界。
- **偶发失败先单独重跑一次再下结论**：单独跑能过、全量跑才失败 → 大概率是时间或资源竞争，当缺陷去查根因，别指望多跑几次碰运气；单独跑也失败 → 真缺陷，或者断言写得太脆。

## 基建地图（先来找现成的，别重造）

| 需要什么 | 去哪拿 |
|---|---|
| 假 LLM（可编排应答，调错方法会失败） | `tests/modules/llm/fakes.py` |
| 假 MCP 传输 / 会话（进程内注入） | `tests/modules/mcp/fakes.py` |
| Minecraft 场景工厂（一次调用装好一整局） | `tests/agents/minecraft_v1_fakes.py` |
| 捕获事件的假总线、MCP 客户端替身 | `tests/agents/minecraft_collector_fakes.py` |
| 事件载荷工厂（弹幕等） | `tests/modules/storage/helpers.py` |
| streamer 装配工厂与缺省配置 | `tests/agents/streamer/helpers.py` |
| 假时钟、token 计数等全局 fixture | `tests/conftest.py` |
| 仪表盘 API 夹具 | `tests/dashboard/conftest.py` |
| 配置迁移测试助手 | `tests/config/helpers.py` |

## 抄结构不抄内容的样板

- 状态机与时间行为测试：`tests/agents/streamer/test_rundown_state.py`（假时钟 + 场景装配 + 参数化）
- 替身注入写法：`tests/modules/mcp/test_client.py`（构造注入，零私有 patch）
- 表驱动迁移测试：`tests/config/test_migrations.py`（旧数据播种 / 迁移后断言两段式 + 幂等检查）
