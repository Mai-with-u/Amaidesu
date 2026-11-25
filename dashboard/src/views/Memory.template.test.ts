// Memory.vue 页内 tab 信息架构的源码结构守护测试
//
// 这里只做读源码的结构断言；跨面板联动的实际行为（哪个面板在何时被重拉）由
// 挂载页面的 Memory.test.ts 验证。
//
// 守护的语义：七个 tab 的顺序是运营者的动线（承诺 → 观众候选 → 整理 → 档案 →
// 玩家 → 自述 → 公告），顺序与 pageTabs 键表一致；tab 全部惰性挂载，保证首屏
// 只拉当前面板；?tab= 深链必须经 resolveMemoryTab 白名单解析（未知值回退默认
// tab，外链拼错不落空白页）；跨面板联动必须存在——四个会报告改动的面板都接到
// 同一个处理入口，改动会把全部 tab 记进待重拉集合，进入 tab 时据此重拉并移出；
// 整理状态的"查看该场档案"切到档案 tab 并选中那一场。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { MEMORY_TAB_KEYS } from '../components/memory/pageTabs';

const sfcSource = readFileSync(fileURLToPath(new URL('./Memory.vue', import.meta.url)), 'utf-8');

const paneOpenTags = [...sfcSource.matchAll(/<el-tab-pane name="([^"]+)"[^>]*>/g)].map(
  match => match[0],
);
const paneNames = [...sfcSource.matchAll(/<el-tab-pane name="([^"]+)"/g)].map(match => match[1]);

describe('记忆页 tab 信息架构', () => {
  it('七个 tab 按动线顺序排列，与 pageTabs 键表一致', () => {
    expect(paneNames).toEqual([...MEMORY_TAB_KEYS]);
  });

  it('每个 tab 都惰性挂载，首屏只拉当前面板', () => {
    expect(paneOpenTags).toHaveLength(MEMORY_TAB_KEYS.length);
    for (const tag of paneOpenTags) expect(tag).toContain(':lazy="true"');
  });

  it('tab 深链经白名单解析，tab 键写回 route query', () => {
    expect(sfcSource).toContain('resolveMemoryTab(route.query.tab)');
    expect(sfcSource).toContain('() => route.query.tab,');
    expect(sfcSource).toContain('router.replace({ query: { ...route.query, tab: key } })');
  });

  it('跨面板联动存在：面板改动把 tab 记进待重拉集合、进入 tab 重拉并移出、查看该场档案切档案 tab 并选中那一场', () => {
    // 会报告改动的面板有四个：承诺、观众候选、整理状态、玩家自述
    expect(sfcSource.match(/@changed="onPanelChanged"/g)).toHaveLength(4);
    // 面板改动把全部七个 tab 记进集合；进入 tab 时在集合里才重拉并移出
    expect(sfcSource).toContain('for (const key of MEMORY_TAB_KEYS) tabsToReload.add(key)');
    expect(sfcSource).toContain('if (!tabsToReload.has(key)) return;');
    expect(sfcSource).toContain('tabsToReload.delete(key)');
    expect(sfcSource).toContain('PANEL_RELOAD_BY_TAB[key]()');
    expect(sfcSource).toContain("activeTab.value = 'archive'");
    expect(sfcSource).toContain('archivePanel.value?.selectSession(liveSessionId)');
  });
});
