# tests/ 目录规则

本文件约束在 tests/ 目录下工作的编码代理；仓库级硬约束见根目录 AGENTS.md，机器本地约定见 AGENTS.local.md。写或改测试先加载 `testing-conventions` skill——它是本文件的操作化重述（检查单与基建地图），两者不一致以本文件为准。

## 测试树归属（新测试放哪边）

测试树按**被测源码包镜像**归属：

- `tests/agents/` 镜像 `src/agents/`：测业务 Agent（streamer / minecraft / text_adv / game_2048 等）的测试全部放这里，不区分单组件逻辑与跨模块接线。
- `tests/modules/` 镜像 `src/modules/`：只放测 `src/modules/` 组件的测试；其中 `tests/modules/agents/` 只放 Agent 框架（base / manager / supervisor / delegation / factory / companions）的测试。
- 归位判据：看被测逻辑的主包——主包在 `src/agents/` 下落 `tests/agents/` 镜像路径，主包在 `src/modules/` 下落 `tests/modules/` 镜像路径；分层依赖 / 事件流约束落 `tests/architecture/`。
- 同一被测对象在两棵树都放禁止（重复覆盖）。
- 例外：Agent 框架与服务层测试可用具体 Agent 作真实夹具（构造路径一致性、停止链路、组件运行时重建等），归属按主被测包不变；例外名单由 `tests/architecture/` 的归属守护执法。

## 写测试的纪律

- 等事件派发落地用 EventBus 的 `wait_until_drained()`（src/modules/events），不用真实 sleep 猜时长。
- 时间的流逝用注入的时钟（正面先例：tests/modules/collectors/test_live_frame_collector.py 的 now_ms 注入）；攒批窗口、重试退避、超时判定不写真实时间等待。
- 需要 patch 私有成员（前导下划线）才能搭测试，视为生产模块缺接缝的缺陷：上报并修接缝，不绕过。
- 假对象（fake）单点收敛：一个生产接口的替身全库只写一份，放对应域的 `fakes.py` / `helpers.py`，跨域 import，不复制。
- 载荷与场景用既有工厂构造（如 tests/modules/storage/helpers.py 的 make_room_message）；没有工厂的先建工厂再用。
