// Memory.vue 页内 tab 信息架构的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件），按既有惯例降级为源码
// 结构断言。
//
// 守护的语义：七个 tab 的顺序是运营者的动线（承诺 → 观众候选 → 整理 → 档案 →
// 玩家 → 自述 → 公告），顺序与 pageTabs 键表一致；tab 全部惰性挂载，保证首屏
// 只拉当前面板；?tab= 深链必须经 resolveMemoryTab 白名单解析（未知值回退默认
// tab，外链拼错不落空白页）；跨面板联动（候选决定 → 承诺/档案标脏，整理状态
// → 切档案 tab 切场）必须存在——删掉任一处，批准候选后承诺/档案的过期数据或
// "查看该场档案"入口就会无声失效。

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

  it('跨面板联动存在：候选决定标脏、切 tab 消化脏标记、查看该场档案切档案 tab', () => {
    expect(sfcSource).toContain('staleAfterCandidate.commitment = true');
    expect(sfcSource).toContain('staleAfterCandidate.archive = true');
    expect(sfcSource).toContain("activeTab.value = 'archive'");
    expect(sfcSource).toContain('archivePanel.value?.selectSession(liveSessionId)');
  });
});
