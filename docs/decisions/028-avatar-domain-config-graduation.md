# ADR-028：avatar 域配置毕业——七文件布局与皮套配置结构

- 状态：已采纳
- 日期：2026-09-21
- 实现提交：`657d1d36e72328354e7702c554f6859f6f6f9cd4`（avatar 域配置毕业为第七配置文件）→ `1fe827b462a56bee3f62db3dbbe3300f38260c16`（idle 六轴绑定改纯配置语义并修订默认值）→ `259ef8deaa3fb0d4d5cb60248f6dfec3133939e9`（换模跟随：健康心跳内模型名轮询触发解析链重跑）→ `0c625ee9d120ee771a946d43c1330507261fdf81`（默认情绪表眉参数换用联合插座 Brows）；名字收编与文档对齐见本文件所在收口提交

## 背景（Context）

avatar 是治理最重的域之一——独立模块目录 `src/modules/avatar/`、结构型协议 `AvatarProvider`、ADR 足迹 023/024/025/026——但其配置无单一之家：皮套平台配置栖身 `[tools.avatar.<name>]`（"avatar" 对配置管线无机制语义，仅是 tools.toml 里的分类标签，归属方向倒置：加任何 avatar 内容都须依附 tools 域）、口型调参远在 infra.toml `[avatar.lipsync]`。

同时，对 17 个 VTS 模型（5 官方样例 + 12 个真实/工坊模型）的接线表（`.vtube.json` ParameterSettings）聚合实证，暴露三处配置缺陷：

1. **idle 回退表是错误机制**：VTS 无通用躯干输入插座，`_IDLE_PARAM_FALLBACKS` 的 body 候选名在真实模型 0 命中，产生无效警告刷屏（用户实测）；身体动效在全部受查模型上都由 FaceAngleX/Y/Z 的多路输出承载（头部注入天然带动身体）。
2. **默认情绪表眉参数生态死亡**：`_emotion_map` 中 BrowLeftY/BrowRightY（12/17 个情绪在用）的绑定率为 0/17 与 1/17，写成功但不可见；事实标准是联合插座 `Brows`（11/17）。参数"存在"与"有效果"是两层——绑定率不可运行期检测（输入→模型映射无 API）。
3. **换模后解析链不重跑**：解析（热键清单/idle 量纲缩放/表情基线）只在连接时执行，VTS 内中途换模后全部陈旧。

## 决策（Decision）

**avatar 从 tools 域毕业，新设第七配置文件 `avatar.toml` 作为域唯一配置之家；配置结构以 17 模型生态实证设计。**

- **毕业判据**（防滑坡，三条同时满足）：域内多 provider 实例；自有协议/模块目录/ADR 足迹；配置已发生跨文件溢出。今日仅 avatar 满足；studio/vision/memory/mcp 留在 tools.toml。
- **文件结构**：`[avatar.platform]` 组段放 enabled 名单（对齐 agents/collectors 域根名单约定）；`[avatar.platform.<名>]` 成员段直接铺参数键（去 `.config` 层——该层是 tools 动态键机制的产物）；`[avatar.lipsync]` 自 infra 收编（placement 判据：组件代码住哪，配置段跟哪）。代码目录镜像：`src/modules/avatar/platform/{vts,warudo,vrchat}/`。
- **机制键零改动**：注册表键 `("avatar", "vts")`、bootstrap 成员表、dashboard 工具页分组均不变（"platform" 段是纯配置结构，不进键）。
- **idle 绑定语义**：删除 `_IDLE_PARAM_FALLBACKS` 猜测机制；轴绑定纯配置——空 = 停用（零输出零警告），非内置名缺失 = 一次性警告 + 停写；默认 head = FaceAngleX/Y/Z（内置且 16/17 绑定）、body = 空（内置无躯干插座，任何非空默认都是猜）。
- **换模跟随**：订阅 `ModelLoadedEvent`，模型每次加载后重跑解析链（热键重拉 → 量纲缩放重建 → 表情基线重写 → 清 failed_params）。模型选择权在 VTS/人，Amaidesu 只适配不驱动。
- **默认情绪表修订**：BrowLeftY/BrowRightY 换为 `Brows`——9 个成对同值情绪无损合并（surprised 1.0、excited 0.85、happy 0.6、speechless 0.3、angry 0.2、sad/serious 0.15、tired/crying 0.1）；scared/confused/smug 三处不对称取较大值近似（0.9/0.95/0.7）；FaceAngry 保留（语义独特、零成本）。
- **参数选择原则**：默认值 = 内置参数 ∧ 高绑定率（内置保证写入成功，绑定率保证大概率可见，两条件缺一不可）；配置值 = 任意插座名，按模型适配；效果失配由用户观察后改配置（绑定率不可检测是硬边界）。

## 替代方案（Alternatives）

- **维持六文件、avatar 留 tools.toml（ADR-014 机制域判据）**：判据是手段不是目的；归属倒置痛点无法解决；ADR-027 已用更便宜机制解决了可发现性，但解决不了归属。拒绝（本 ADR 修订 ADR-014 的文件数边界，机制判据全部沿用）。
- **插座自举（配置名缺失时自动 ParameterCreationRequest 创建）**：当前功能（idle + 口型）全部跑在内置参数上，自造参数零贡献——YAGNI 砍除。官方语义（"recreate these parameters at any time and they will continue to work"）支持其可行性，作为升级候选存档。
- **expression_params 情绪覆盖层（Dict[情绪→Dict[参数→幅度]] 配置覆盖）**：与"不给用户自定义参数空间"的裁定冲突，被默认表 Brows 修订替代；设计已存档，作为升级候选。
- **vts_load_model 换模工具 + model 配置字段**：主播不需要临时换模型；换模是 VTS/人工操作，Amaidesu 只需跟随。否决（ModelLoadedEvent 跟随是既定路线）。
- **平台段改注册表键（("avatar.platform", "vts")）**：键第一位是域（dashboard 分组/bootstrap 装配语义），"platform" 是纯配置结构不进键——零改动方案成立。

## 后果（Consequences）

- 配置六文件 → 七文件：AGENTS.md 与 docs 同步表述；tools.toml 瘦身（保留工具机制参数 + 未毕业分类）；infra.toml `[avatar.lipsync]` 迁出（infra 为热段，口型调参从即时生效变重启生效——低频操作，可接受）。
- 同批键两个月内二次搬家（v2.0.36 tools→infra，本次 infra→avatar）：每次均有正当理由（修机制错位 / 修域无家），本次为终点站。
- 迁移 = 跨文件钩子（target_file 双写双版本同升）×2 + idle 默认值迁移钩子（仅旧默认值改写，不碰用户显式配置），均配迁移测试；提交前实际验证迁移写回落盘。
- 文档"平台/后端"混用收编为"平台"（ADR-023 标题用语为准），实现期全库 grep 残留。
- 升级候选登记（触发条件 = 第一个真实需求案例）：ARKit 嘴型自建参数口型升级（JawOpen/Funnel/Pucker 经 ParameterCreationRequest，冰糖类 VBridger rig 直接受益）；expression_params 覆盖层；vts_load_model 工具。
- 换模后 idle 绑定若指向新模型不存在的插座 → 一次性警告 + 该轴诚实停写。
- 换模跟随之实现形态：pyvts 的 request 为"单次 send+recv"、无常驻接收循环，订阅 ModelLoadedEvent 需自建统一接收泵且与请求路径抢包；实现采用健康心跳（5s）内的模型名轮询（AvailableModelsRequest 单请求含清单与加载态），变化即重跑解析链——跟随语义等效，换模为 VTS/人侧低频操作，秒级跟随足够。

## 参考（References）

- VTS 官方 API 文档：https://github.com/DenchiSoft/VTubeStudio （仓库 README 即 API 开发文档；Wiki 在同仓库 wiki 页签）
- [ADR-014：配置体系六文件重构](014-config-six-file-refactor.md)（本 ADR 修订其文件数边界，机制判据沿用）
- [ADR-023：皮套平台无关边界——平台差异全关进适配器](023-avatar-platform-agnostic-boundary.md)
- [ADR-027：动态配置段统一注册表机制](027-dynamic-config-section-registry.md)
- 讨论全档与生态证据：`.omo/drafts/avatar-config-boundary.md`（本文兼作讨论权威档）
