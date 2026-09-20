# ADR-026：皮套工具面契约——语义参数、发现协议与结构类型规范

- 状态：已采纳（2026-09-20）
- 日期：2026-09-20
- 实现提交：`2a918d0e0213b68cfd00004bf00f730b71a18f65`（feat: 皮套工具面收敛28至11并立契约协议）

## 背景（Context）

皮套三后端共注册 28 个 LLM 工具，构成失控：VTS 有 `smile` / `close_eyes` / `set_parameter_value` 等设备微旋钮（参数名不可发现）、`load_item`（14 个参数）；Warudo 有 `set_eyebrow` / `set_eye` / `set_pupil` / `set_mouth` 四件面部通道工具——而 `eyebrow_happy_strong` 这类键本就是情绪映射的底层通道，属适配器内部；运维件（`reconnect` / `get_stats` / `get_parameter_value`）与已定案机制（自动重连循环、熔断健康、输出投影禁令）重复。LLM 面对的是一排设备旋钮而不是表达意图。

同时，三后端的同语义工具名与参数形状各说各话（VTS `trigger_hotkey` / VRChat `trigger_gesture` / Warudo 蓝图三件），情绪入口在主播域还有一份私印的参数表。

## 决策（Decision）

**工具面收敛为 11 个语义化工具（28 → 11），同语义跨后端同名 + 同参数形状，契约由结构类型 Protocol + 契约测试承载。**

- **终态清单**：VTS `vts_set_expression` / `vts_list_preset_actions` / `vts_trigger_preset_action` / `vts_set_idle_enabled`；Warudo `warudo_set_expression` / `warudo_list_preset_actions` / `warudo_trigger_preset_action` / `warudo_set_sight`；VRChat `vrchat_set_expression` / `vrchat_list_preset_actions` / `vrchat_trigger_preset_action`。平台前缀保留（`streamer_reply` 同款既有约定）。
- **语义参数**：`set_expression(emotion: 17 枚举值, intensity: 0~1)`——LLM 说意图不说设备键；`set_sight(target: camera/danmu/phone)` 语义化且不给强度（程度由适配器定）。
- **发现协议 B（两工具）**：`list_preset_actions` 返回目录、`trigger_preset_action(action)` 执行；未知动作名把可用目录随失败结果返回（失败即发现，自愈，零成本）。弃"描述内联目录"（schema 承载运行期数据、目录常驻每次请求）与"查做合一"（读/写语义重载）。
- **剔除判据**：参数不可发现 → 剔（VTS `load_item` / Warudo 裸蓝图节点）；与已定案机制重复 → 剔（运维件）；能力已折叠 → 剔（`load_sticker` 折进预设目录——VTS 内绑成热键自然出现在目录；`throw_fish` 并入目录条目，冷却经返回值告知）。**被剔工具的 Python 方法保留**，仅撤 ToolSpec 注册（内部机件仍调用）。
- **Warudo 面部四件收归内部**：`eyebrow/eye/pupil/mouth` 状态件是情绪映射与氛围/口型机件的内部通道，方法保留、不再对 LLM 暴露；`set_sight` 例外保留（独立意图 + 语义参数）。
- **契约形态 = Protocol（结构类型，非继承）+ 契约测试**：`AvatarProvider` 声明 `set_expression` / `list_preset_actions` / `trigger_preset_action` 成员形状；测试用 `isinstance` 校验三 provider。定位是**规范载体 + 测试锚点**，非运行时接缝（无多态调用方——LLM 直接看各后端前缀工具）。不抽 provider 基类：三后端执行模型差异大（VTS WebSocket 推参数 / Warudo 状态件 + 蓝图 / VRChat OSC 单向写），强抽 = 抽象泄漏（ADR-007 决策 10 的推理可迁移）。
- **工具设计规范（契约兼作）**：LLM 面参数必须语义化、无设备键、无裸量纲；一意图一工具；同语义跨后端同名同形状；适配器内部通道不进名单。

## 替代方案（Alternatives）

- **域级工具名 `avatar_*` 统一入口**：见 ADR-023（Facade 多余一跳，否决）。
- **抽 AvatarProvider 基类统一三后端**：执行模型差异上强抽基类 = 抽象泄漏；且无多态调用方，基类只为继承而继承。
- **描述内联动作目录（方案 A）**：注册期快照永不刷新（VTS 热键连接后才可枚举），且目录常驻每次 LLM 请求。
- **保留设备微旋钮给"精细控制"**：LLM 无从发现参数名与量纲，幻觉率高；精细控制归适配器内部与未来人工配置。

## 后果（Consequences）

- LLM 工具面从 28 减到 11，schema 全语义化；情绪词表 17 值全覆盖（VTS 映射用足面部参数，Warudo 走 blendshape 状态件，VRChat 情绪面诚实返回未应用）。
- 动作目录改走工具结果，注册期快照缺陷（`list_tools` 依赖运行期状态、启动不刷新）对目录可达性不再必要修；缺陷本身登记备查——若将来再出现同类 provider，修 `start_providers` 后刷新。
- 新增皮套后端的验收集：实现 `AvatarProvider` 契约成员 + 通过契约测试 + 工具面逐字对齐终态清单。
