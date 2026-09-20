# ADR-025：字幕单一源——装配期择流 + Warudo 字幕面退役

- 状态：已采纳（2026-09-20）
- 日期：2026-09-20
- 实现提交：`483bd4b3f3edb0f7196fd70c7c22d5c97e10576c`（feat: 字幕单一源与Warudo字幕面退役）、`5070acdc157a3bdbf29e5d1d8b49d2d382619e42`（fix: lipsync迁移测试断言跟进tools钩子链尾2.0.37）

## 背景（Context）

字幕存在两套并行事实：`src/modules/subtitle/`（桌面窗 + Dashboard 挂件，经 `SubtitleService` 多后端广播）与 Warudo 自带的 `SubtitleManager`（端口 8766，docstring 自述"为 OBS 浏览器源提供实时字幕推送"）。逐项对比后确认两者做同一件事，且 Dashboard 的 `/subtitle` 页样式能力更强（每条消息可带字号/颜色/背景/边框 + `auto_hide_after_ms`）。双轨的害处在 ADR-007 遗留节已被自承："事件与字幕存在双轨，待统一"。

同时，字幕的**驱动时机**一直挂在编排层派发（发言决定时刻），与实际发声不对齐：TTS 队列积压或丢弃时，字幕显示的是"决定要说的话"而不是"正在说的话"。

## 决策（Decision）

**字幕收敛为"一源 → 三面"；源的选择在装配期一次定死，两种模式互斥。**

- **三面**：桌面小窗 / Dashboard 挂件 / OBS 用 Dashboard `/subtitle` 页。三者都是 `SubtitleService` 的 Backend，一源广播。
- **装配期择流**：以启动装配时有没有建出 TTS 引擎（`build_tts_infrastructure` 返回值，fail-soft 语义已含"禁用 / 缺配置 / 构造失败"）为判据——
  - **有引擎** → 字幕跟随播放事件：`tts.utterance.started` 显示该句文本、`finished` 清空（与实际播放对齐；队列丢最旧的台词自然不显示字幕）；
  - **无引擎** → 编排层派发直出（现状行为）。
  - 两种模式互斥，非两套源同时跑。
- **运行期逐句降级**：引擎建出但某句合成失败（`tts.utterance.failed`）→ 该句**照常显示文本**——无声音也要给文本，原文由 `speech_text` 必选字段承载（事件契约同批收紧）。
- **删除 Warudo 8766 字幕面**：`WarudoSubtitleManager`（含子包）、`WarudoProvider.push_subtitle`、`warudo_set_subtitle` 工具注册与 `[tools.avatar.warudo].config` 的 `subtitle_enabled` / `subtitle_port` / `subtitle_show_status` 三键（升 2.0.37 + 删键钩子 + 迁移测试）。少一个常驻服务器、少一个 LLM 工具。

## 替代方案（Alternatives）

- **把 Warudo 字幕迁为 SubtitleService 的一个 Backend**：最初提议，亲核其 docstring 后推翻——它是"给 OBS 的字幕页"，与 Dashboard `/subtitle` 完全重叠而非互补；迁移是给重叠能力留两个实现。
- **两种字幕源同时跑、按可用性切换**：重蹈双轨害处（同屏两份 / 互相覆盖），排除。
- **运行期动态择流**（引擎中途失效自动降直出）：引擎是装配期构造的基础设施，无"中途消失"的运行形态；为不存在的场景加切换逻辑属防御性发明。

## 后果（Consequences）

- 字幕与实际发声对齐（播放对齐模式）；队列丢最旧的台词不再出现"字幕说了但没说出口"。
- Warudo 用户的 OBS 字幕源需改指向 Dashboard `/subtitle` 页。三个小缺口登记为可选尾巴（连接时重推当前字幕 / 说话人名 / 连接状态徽章），非架构级。
- 配置基线推进到 2.0.37（tools.toml 钩子链：2.0.36 lip-sync 迁移 + 2.0.37 删键）。
- 编排层在无 TTS 场景仍直出字幕——`SpeechDispatcher` 的字幕扇出保留，但"何时显示"的唯一裁决权在装配期定死，运行期无双源竞争。
