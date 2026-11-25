# 可视化复盘页的做法（用户提出才做）

> 可选产出，默认不做：复盘汇报里不提，用户认为有必要时提出，AI 可以建议一次、不索要授权。

**前置条件**：问题条目已定稿。本页是最终渲染，一次成页，不再随讨论返工。写到 `logs/reviews/session-{场次id}-{YYYY-MM-DD}.html`（目录不存在先创建；场次 id 以 `live_sessions` 表为准）。页面只写 HTML 结构与内容——样式与脚本外置，以相对路径引用同目录的 `report.css` / `report.js`：

- 写页面前先确认 `logs/reviews/` 下有这两个文件：缺则从 [assets/](../assets/report-template.html)（report.css / report.js / report-template.html 三件套）原样拷贝过去；已存在则直接引用，不覆盖、不改动（模板本身更新不影响已落盘副本）；
- 结构按 [assets/report-template.html](../assets/report-template.html) 的章节槽位填**定稿后**的本场数据，文件头注释写明各槽位填法（形状条/发言刻度的百分比算法等）；
- 文件名前缀 `session-{id}-` 是 WebUI 查找键，不得改动；
- 页面内容与 markdown 定稿同源同序（同一分析，两种载体），矛盾行必须用 `.conflict` 样式并编号；定稿里有而模板没有槽位的章节（如言行一致），在模板相应位置补一节，沿用已有的样式类；
- 填完自查"占位词零残留"，并对本页独立过一遍 [objectivity-selfcheck.md](objectivity-selfcheck.md) 的 E3（两个载体各自独立核对，"照着 md 改"不算核对）；全场无某章节（如测试场无游戏表现表）就整节删除，不留空壳。
