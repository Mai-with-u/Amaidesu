# ADR-028：avatar 配置迁出 tools 域——第七配置文件与皮套配置结构

- 状态：已采纳
- 日期：2026-09-21
- 实现提交：`5d257006002b787cf0817ed4ea9c04e95b4ac91c`（avatar 配置迁出 tools 域，独立为第七配置文件）→ `25f96c1d652e248a1d20c530814883a3c8bc44f2`（idle 六轴绑定改纯配置语义并修订默认值）→ `340fa3789aa3576af410c0331e82042e0f823e97`（换模跟随：健康检查循环内轮询模型名，触发解析链重跑）→ `08288246c5bae1ff14b10c2fca62428ec526a8bd`（默认情绪表眉参数改用双眉联合参数 Brows）；用语统一与文档对齐见本文件所在收口提交

## 背景（Context）

avatar 是代码结构最完整的域之一——独立模块目录 `src/modules/avatar/`、跨平台协议 `AvatarProvider`、四篇架构决策记录（ADR-023/024/025/026）——但它的配置一直没有自己的文件：皮套平台的配置放在 `[tools.avatar.<name>]`（"avatar" 对配置系统来说只是 tools.toml 里的一个分组标签，角色是反的：avatar 是皮套平台本体，工具只是它的能力，配置却让 avatar 依附在 tools 底下，加任何 avatar 内容都得先过 tools 这一层的目录结构）；口型调参另在 infra.toml 的 `[avatar.lipsync]`。

同时，对 17 个 VTS 模型（5 个官方样例 + 12 个真实/创意工坊模型）的接线数据（`.vtube.json` 的 ParameterSettings，即"哪个输入参数被模型作者绑到了哪个部位"）汇总分析后，暴露三处配置缺陷：

1. **idle 回退表是错误机制**：VTS 没有通用的躯干输入参数，`_IDLE_PARAM_FALLBACKS` 里 body 轴的候选参数名在真实模型上一个都匹配不到，每帧产生无效警告刷屏（用户实测）；身体动作在全部受查模型上都由 FaceAngleX/Y/Z 的输出带动（模型作者把身体绑到同一组头部输入上，头部动身体自然跟着动）。
2. **默认情绪表的眉参数在真实模型上看不到效果**：`_emotion_map` 里在用的 BrowLeftY/BrowRightY（12/17 个情绪用到）分别只有 0/17 和 1/17 个模型绑定——参数写入成功（VTS 接受了）但模型上没有任何变化；17 个模型里 11 个绑定的是 Brows（一个参数同时控制双眉）。"参数存在"和"参数有效果"是两回事——输入参数与模型部位的绑定关系没有 API 可查，代码无法在运行期发现这个问题。
3. **VTS 里中途换模型后解析结果全部过期**：热键清单、idle 参数量纲缩放、表情基线只在连接时解析一次，之后在 VTS 界面里换模型，这些数据不会重新解析。

## 决策（Decision）

**avatar 的配置从 tools.toml 迁出，新设第七个配置文件 `avatar.toml`，avatar 相关配置统一放在这一个文件里；配置结构依据 17 个模型的实测数据设计。**

- **迁出判据**（防止其他分类跟着照做，三条同时满足）：域内有多个 provider 实例；有自己的协议定义、模块目录和多篇架构决策记录；配置已经分散在多个文件里。今天只有 avatar 同时满足三条；studio/vision/memory/mcp 留在 tools.toml。
- **文件结构**：`[avatar.platform]` 组段放 `enabled` 启用名单（对齐 agents/collectors 的域根名单约定）；`[avatar.platform.<名>]` 成员段直接铺参数键（不再有 `.config` 中间层——那一层是 tools.toml 动态键机制的产物）；`[avatar.lipsync]` 从 infra.toml 迁入（配置段跟组件代码走：口型分析代码在 avatar 域，配置也归 avatar.toml）。代码目录同步调整为 `src/modules/avatar/platform/{vts,warudo,vrchat}/`。
- **机制键零改动**：注册表键 `("avatar", "vts")`、bootstrap 成员表、dashboard 工具页分组都不变（"platform" 只是配置文件里的段落结构，不进注册表键）。
- **idle 绑定语义**：删除 `_IDLE_PARAM_FALLBACKS` 候选猜测机制；六个轴的绑定完全由配置决定——配置为空 = 该轴停用（不写入、不警告），配置的名字在 VTS 参数清单里找不到 = 警告一次然后停写；head 默认 FaceAngleX/Y/Z（VTS 内置参数、17 个受查模型里 16 个绑定）、body 默认空（VTS 没有通用躯干输入参数，任何非空默认都是猜测）。
- **换模跟随**：模型每次加载后重跑解析链（重拉热键 → 重建量纲缩放 → 重写表情基线 → 清失败参数停写集）。用哪个模型由 VTS 和主播决定，Amaidesu 只负责跟随适配，不主动换模。
- **默认情绪表修订**：BrowLeftY/BrowRightY 改为 `Brows`——左右眉同值的 9 个情绪直接换用、效果不变（surprised 1.0、excited 0.85、happy 0.6、speechless 0.3、angry 0.2、sad/serious 0.15、tired/crying 0.1）；scared/confused/smug 三个情绪原来左右眉幅度不同，改用 Brows 后取较大值近似（0.9/0.95/0.7）；FaceAngry 保留（语义独特、写入无成本）。
- **参数选择原则**：默认值 = VTS 内置参数 且 实测绑定率高（内置保证写入成功，绑定率高保证大概率看得到效果，两个条件缺一不可）；配置值 = 任意参数名，按各模型实际情况适配；写了参数但模型没反应时，由用户观察后自己改配置（输入参数与模型部位的绑定关系没有 API 可查，这是硬边界）。

## 替代方案（Alternatives）

- **维持六文件、avatar 留在 tools.toml（ADR-014 的机制域判据）**：判据是手段不是目的；"avatar 依附于 tools"的结构问题没有解决；ADR-027 已用更便宜的机制解决了配置项不可发现的问题，但解决不了归属问题。拒绝（本 ADR 修订 ADR-014 的文件数边界，机制判据全部沿用）。
- **参数不存在时自动创建（配置名缺失时自动发 ParameterCreationRequest 创建参数）**：当前功能（idle + 口型）全部跑在 VTS 内置参数上，自建参数没有实际贡献——按 YAGNI 原则砍掉。VTS 官方文档确认这个方向可行（"recreate these parameters at any time and they will continue to work"），登记为将来可选的升级方向。
- **expression_params 情绪覆盖层（按情绪配置参数幅度表，覆盖代码默认表）**：与"不给用户自定义参数空间"的裁定冲突，被默认表改用 Brows 的方案替代；设计存档，作为将来可选的升级方向。
- **vts_load_model 换模工具 + model 配置字段**：主播不需要临时换模型；换模是 VTS 界面里的人工操作，Amaidesu 只需要跟随。否决（跟随重跑解析链是既定路线）。
- **平台段改注册表键（("avatar.platform", "vts")）**：键的第一位是域（dashboard 分组、bootstrap 装配语义），"platform" 只是配置文件里的段落结构、不进键——零改动方案成立。

## 后果（Consequences）

- 配置从六个文件变七个：AGENTS.md 与 docs 同步表述；tools.toml 变小（保留工具机制参数 + 未迁出的分类）；infra.toml 的 `[avatar.lipsync]` 迁出（infra 是热重载文件，口型调参从改完即时生效变成重启生效——低频操作，可接受）。
- 同一批参数两个月内迁了两次（2.0.36 从 tools.toml 迁到 infra.toml，这次从 infra.toml 迁到 avatar.toml）：每次都有具体原因（第一次是参数放错了文件，这次是修归属），这次落在 avatar.toml 后不再移动。
- 迁移 = 两个跨文件钩子（双写 + 双版本同升）+ idle 默认值改写钩子（只改写等于旧默认的落盘值，不碰用户自己设置的配置），都配迁移测试；提交前实际验证迁移写回落盘。
- 文档里"平台/后端"混用的地方统一为"平台"（以 ADR-023 标题用语为准），实现期全库 grep 确认清干净。
- 升级方向登记（触发条件 = 出现第一个真实需求案例）：ARKit 嘴型自建参数的口型升级（JawOpen/Funnel/Pucker 经 ParameterCreationRequest 自建，冰糖这类 VBridger 绑定的模型直接受益）；expression_params 情绪覆盖层；vts_load_model 换模工具。
- 换模后 idle 绑定若指向新模型上不存在的参数 → 警告一次 + 该轴停写。

## 参考（References）

- [ADR-014：配置体系六文件重构](014-config-six-file-refactor.md)（本 ADR 修订其文件数边界，机制判据沿用）
- [ADR-023：皮套平台无关边界——平台差异全关进适配器](023-avatar-platform-agnostic-boundary.md)
- [ADR-027：动态配置段统一注册表机制](027-dynamic-config-section-registry.md)
- 讨论全档与模型实测数据：`.omo/drafts/avatar-config-boundary.md`
