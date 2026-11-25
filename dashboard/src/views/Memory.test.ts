// @vitest-environment happy-dom
// 记忆页跨面板联动的行为测试
//
// 被测的是记忆页（Memory.vue）对七个面板的协调，面板内部不在范围：每个面板换成
// 一个占位组件，它与真实面板有两处一致——挂载时自己拉一次数据；对外暴露的方法名
// 相同。页面每让一个面板重拉，就落在该面板的记录函数上，测试据此断言哪个面板
// 被拉了几次。页签容器、路由与页面本身都用真实的：页签的惰性挂载时机（面板
// 首次进入时才挂载）只有真实容器才能复现。

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';
import { ElButton, ElTabPane, ElTabs } from 'element-plus';
import Memory from './Memory.vue';
import { MEMORY_TAB_KEYS, type MemoryTabKey } from '@/components/memory/pageTabs';

const panelStubs = vi.hoisted(() => ({
  /** 每个占位面板的重拉记录，键是页签键；面板挂载时自己拉的那一次也记在这里 */
  reloads: {
    commitment: vi.fn(),
    candidates: vi.fn(),
    settle: vi.fn(),
    archive: vi.fn(),
    players: vi.fn(),
    'player-candidates': vi.fn(),
    announcement: vi.fn(),
  },
  selectSession: vi.fn(),
  /** 页签键 → 占位面板的组件名，与真实面板同名 */
  names: {
    commitment: 'CommitmentPanel',
    candidates: 'CandidatePanel',
    settle: 'SettlePanel',
    archive: 'ArchivePanel',
    players: 'PlayerPanel',
    'player-candidates': 'PlayerCandidatePanel',
    announcement: 'AnnouncementPanel',
  },
}));

const api = vi.hoisted(() => ({
  ledgerApi: {
    listCommitments: vi.fn(),
    listCandidates: vi.fn(),
    getSettleState: vi.fn(),
  },
  playersApi: {
    list: vi.fn(),
    listCandidates: vi.fn(),
  },
}));

vi.mock('@/api', () => api);

vi.mock('@/components/memory/CommitmentPanel.vue', async () => ({
  default: panelStub(await import('vue'), 'commitment', ['changed']),
}));

vi.mock('@/components/memory/CandidatePanel.vue', async () => ({
  default: panelStub(await import('vue'), 'candidates', ['changed']),
}));

vi.mock('@/components/memory/SettlePanel.vue', async () => ({
  default: panelStub(await import('vue'), 'settle', ['view-archive', 'changed']),
}));

vi.mock('@/components/memory/ArchivePanel.vue', async () => ({
  default: panelStub(await import('vue'), 'archive', []),
}));

vi.mock('@/components/memory/PlayerPanel.vue', async () => ({
  default: panelStub(await import('vue'), 'players', []),
}));

vi.mock('@/components/memory/PlayerCandidatePanel.vue', async () => ({
  default: panelStub(await import('vue'), 'player-candidates', ['changed']),
}));

vi.mock('@/components/memory/AnnouncementPanel.vue', async () => ({
  default: panelStub(await import('vue'), 'announcement', []),
}));

/**
 * 记忆页面板的占位组件，由上面的事件声明决定它能发出哪些事件。暴露的方法名
 * 跟着真实面板走：公告面板只有 load，其余面板只有 reload，档案面板另有
 * selectSession——页面调错方法名时会直接报错，不会被占位悄悄接住。
 */
function panelStub(vue: typeof import('vue'), tab: MemoryTabKey, events: string[]) {
  const load = panelStubs.reloads[tab];
  const exposed =
    tab === 'announcement'
      ? { load }
      : { reload: load, ...(tab === 'archive' ? { selectSession: panelStubs.selectSession } : {}) };
  return vue.defineComponent({
    name: panelStubs.names[tab],
    emits: events,
    setup(_props, { expose }) {
      expose(exposed);
      vue.onMounted(() => {
        void load();
      });
      return () => vue.h('section', { 'data-panel': tab }, tab);
    },
  });
}

/** 会报告改动的四个面板所在的页签 */
const CHANGED_REPORTING_TABS = ['commitment', 'candidates', 'settle', 'player-candidates'] as const;

let mountedWrappers: VueWrapper[] = [];

/** 等页签切换引出的渲染、路由跳转与面板挂载全部完成 */
async function flush(): Promise<void> {
  await flushPromises();
  await nextTick();
}

/** 挂载记忆页；initialTab 模拟带 ?tab= 的深链进入 */
async function mountMemory(
  initialTab?: MemoryTabKey,
): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: Memory }],
  });
  await router.replace({ path: '/', query: initialTab ? { tab: initialTab } : {} });
  await router.isReady();
  const wrapper = mount(Memory, {
    global: { plugins: [router], components: { ElTabs, ElTabPane, ElButton } },
  });
  mountedWrappers.push(wrapper);
  await flush();
  return { wrapper, router };
}

/** 像运营者那样点页签头；Element Plus 给每个页签头的 id 是 tab-<页签键> */
async function clickTab(wrapper: VueWrapper, tab: MemoryTabKey): Promise<void> {
  await wrapper.find(`#tab-${tab}`).trigger('click');
  await flush();
}

/** 取某页签里的占位面板，让它像真实面板那样发出事件 */
function panelOf(wrapper: VueWrapper, tab: MemoryTabKey) {
  return wrapper.findComponent({ name: panelStubs.names[tab] });
}

/** 某面板到目前为止被拉的次数，首次挂载时自己拉的那一次也算在内 */
function loadCount(tab: MemoryTabKey): number {
  return panelStubs.reloads[tab].mock.calls.length;
}

/** 七个面板各自到目前为止被拉的次数 */
function loadCounts(): Record<MemoryTabKey, number> {
  return Object.fromEntries(MEMORY_TAB_KEYS.map(tab => [tab, loadCount(tab)])) as Record<
    MemoryTabKey,
    number
  >;
}

/** 徽标计数到目前为止被刷新的次数（每次刷新读一次整理状态） */
function badgeRefreshCount(): number {
  return api.ledgerApi.getSettleState.mock.calls.length;
}

beforeEach(() => {
  vi.resetAllMocks();
  for (const reload of Object.values(panelStubs.reloads)) {
    reload.mockImplementation(async (): Promise<void> => undefined);
  }
  api.ledgerApi.listCommitments.mockResolvedValue({ data: { open_count: 0 } });
  api.ledgerApi.listCandidates.mockResolvedValue({ data: { pending_count: 0 } });
  api.ledgerApi.getSettleState.mockResolvedValue({ data: { pending_count: 0 } });
  api.playersApi.list.mockResolvedValue({ data: { items: [] } });
  api.playersApi.listCandidates.mockResolvedValue({ data: { pending_count: 0 } });
});

afterEach(() => {
  for (const wrapper of mountedWrappers) wrapper.unmount();
  mountedWrappers = [];
});

describe('面板报告改动后，各页签在下次进入时重拉', () => {
  it('整理状态面板报告改动后，再进入已打开过的档案页签会重拉档案面板', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    expect(loadCount('archive')).toBe(1);

    panelOf(wrapper, 'settle').vm.$emit('changed');
    await clickTab(wrapper, 'archive');

    expect(loadCount('archive')).toBe(2);
  });

  it('整理状态面板报告改动后，先后进入承诺与档案页签：两个面板都重拉，别的面板不动', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    const before = loadCounts();

    panelOf(wrapper, 'settle').vm.$emit('changed');
    await clickTab(wrapper, 'commitment');
    await clickTab(wrapper, 'archive');

    expect(loadCounts()).toEqual({
      ...before,
      commitment: before.commitment + 1,
      archive: before.archive + 1,
    });
  });

  it('自述面板报告改动后，再进入已打开过的承诺页签会重拉承诺面板', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'player-candidates');
    expect(loadCount('commitment')).toBe(1);

    panelOf(wrapper, 'player-candidates').vm.$emit('changed');
    await clickTab(wrapper, 'commitment');

    expect(loadCount('commitment')).toBe(2);
  });

  it('改动发生时运营者正停在目标页签上：当场不重拉，切走再回来才重拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'player-candidates');
    await clickTab(wrapper, 'commitment');

    // 自述页签里那次写操作的请求此刻才返回。重拉只在进入页签时发生，而承诺
    // 页签正是当前页签，所以面板当场还停在旧内容上
    panelOf(wrapper, 'player-candidates').vm.$emit('changed');
    await flush();

    expect(loadCount('commitment')).toBe(1);

    await clickTab(wrapper, 'player-candidates');
    await clickTab(wrapper, 'commitment');

    expect(loadCount('commitment')).toBe(2);
  });

  it.each(CHANGED_REPORTING_TABS)(
    '「%s」页签里的面板报告改动：刷新徽标计数，并让之后进入的页签重拉',
    async source => {
      const { wrapper } = await mountMemory();
      await clickTab(wrapper, 'archive');
      await clickTab(wrapper, source);
      const badgesBefore = badgeRefreshCount();

      panelOf(wrapper, source).vm.$emit('changed');

      expect(badgeRefreshCount()).toBe(badgesBefore + 1);
      expect(loadCount('archive')).toBe(1);

      await clickTab(wrapper, 'archive');

      expect(loadCount('archive')).toBe(2);
    },
  );

  it.each(MEMORY_TAB_KEYS)(
    '有改动时进入「%s」页签：只重拉该页签的面板，别的已打开面板不动',
    async tab => {
      const source = CHANGED_REPORTING_TABS.find(candidate => candidate !== tab)!;
      const { wrapper } = await mountMemory();
      for (const opened of MEMORY_TAB_KEYS) await clickTab(wrapper, opened);
      await clickTab(wrapper, source);
      const before = loadCounts();

      panelOf(wrapper, source).vm.$emit('changed');
      await clickTab(wrapper, tab);

      const expected = Object.fromEntries(
        MEMORY_TAB_KEYS.map(key => [key, before[key] + (key === tab ? 1 : 0)]),
      );
      expect(loadCounts()).toEqual(expected);
    },
  );

  it('一个页签重拉过之后，没有新的改动时再进出它不再重拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');
    await clickTab(wrapper, 'archive');
    expect(loadCount('archive')).toBe(2);

    await clickTab(wrapper, 'settle');
    await clickTab(wrapper, 'archive');

    expect(loadCount('archive')).toBe(2);
  });

  it('没有面板报告改动时，进出页签不重拉任何面板', async () => {
    const { wrapper } = await mountMemory();
    for (const opened of MEMORY_TAB_KEYS) await clickTab(wrapper, opened);
    await clickTab(wrapper, 'commitment');
    await clickTab(wrapper, 'archive');

    expect(Object.values(loadCounts())).toEqual(MEMORY_TAB_KEYS.map(() => 1));
  });

  it('页签还没打开过时进入它：面板首次挂载自己拉一次并移出待重拉集合，之后不补拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');

    await clickTab(wrapper, 'archive');
    expect(loadCount('archive')).toBe(1);

    // 该页签已经移出集合：之后没有新的改动时再进出它，不会因为面板此刻已挂载而补一次重拉
    await clickTab(wrapper, 'settle');
    await clickTab(wrapper, 'archive');

    expect(loadCount('archive')).toBe(1);
  });

  it('由 ?tab= 参数变化带动的切页签同样重拉', async () => {
    const { wrapper, router } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');

    await router.replace({ query: { tab: 'archive' } });
    await flush();

    expect(loadCount('archive')).toBe(2);
  });
});

describe('整理状态面板的“查看该场档案”', () => {
  it('有改动时：先重拉档案面板，重拉完成后才选中那一场', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');
    let finishReload = (): void => {
      throw new Error('档案面板没有被重拉');
    };
    panelStubs.reloads.archive.mockImplementationOnce(
      () =>
        new Promise<void>(resolve => {
          finishReload = resolve;
        }),
    );

    panelOf(wrapper, 'settle').vm.$emit('view-archive', 42);
    await flush();

    expect(loadCount('archive')).toBe(2);
    expect(panelStubs.selectSession).not.toHaveBeenCalled();

    finishReload();
    await flush();

    expect(panelStubs.selectSession).toHaveBeenCalledTimes(1);
    expect(panelStubs.selectSession).toHaveBeenCalledWith(42);
  });

  it('没有改动时：不重拉，直接选中那一场', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');

    panelOf(wrapper, 'settle').vm.$emit('view-archive', 42);
    await flush();

    expect(loadCount('archive')).toBe(1);
    expect(panelStubs.selectSession).toHaveBeenCalledTimes(1);
    expect(panelStubs.selectSession).toHaveBeenCalledWith(42);
  });

  it('档案页签还没打开过：面板首次挂载时自己拉的就是新数据，页面不追加第二次', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');

    panelOf(wrapper, 'settle').vm.$emit('view-archive', 42);
    await flush();

    expect(loadCount('archive')).toBe(1);
    expect(panelStubs.selectSession).toHaveBeenCalledTimes(1);
    expect(panelStubs.selectSession).toHaveBeenCalledWith(42);
  });

  it('有改动时只取走档案页签：查看之后进入别的已打开页签仍会重拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    const before = loadCounts();
    panelOf(wrapper, 'settle').vm.$emit('changed');

    panelOf(wrapper, 'settle').vm.$emit('view-archive', 42);
    await flush();

    expect(loadCount('archive')).toBe(before.archive + 1);

    await clickTab(wrapper, 'commitment');

    expect(loadCount('commitment')).toBe(before.commitment + 1);
  });
});

describe('页面头的“刷新”', () => {
  it('重拉徽标计数与所有已打开的面板；没打开过的页签里面板还没挂载，不拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    const badgesBefore = badgeRefreshCount();

    await wrapper.find('.header-actions button').trigger('click');
    await flush();

    expect(badgeRefreshCount()).toBe(badgesBefore + 1);
    expect(loadCounts()).toEqual({
      commitment: 2,
      candidates: 0,
      settle: 2,
      archive: 2,
      players: 0,
      'player-candidates': 0,
      announcement: 0,
    });
  });

  it('七个页签都打开过后：七个面板全部重拉', async () => {
    const { wrapper } = await mountMemory();
    for (const opened of MEMORY_TAB_KEYS) await clickTab(wrapper, opened);

    await wrapper.find('.header-actions button').trigger('click');
    await flush();

    expect(Object.values(loadCounts())).toEqual(MEMORY_TAB_KEYS.map(() => 2));
  });

  it('重拉之后，没有新的改动时进入别的页签不再重拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'settle');
    panelOf(wrapper, 'settle').vm.$emit('changed');

    await wrapper.find('.header-actions button').trigger('click');
    await flush();
    await clickTab(wrapper, 'archive');

    expect(loadCount('archive')).toBe(1);
  });

  it('重拉还在进行时有面板报告改动：这次改动留得住，之后进入页签仍会重拉', async () => {
    const { wrapper } = await mountMemory();
    await clickTab(wrapper, 'archive');
    await clickTab(wrapper, 'settle');
    let finishReload = (): void => {
      throw new Error('承诺面板没有被重拉');
    };
    panelStubs.reloads.commitment.mockImplementationOnce(
      () =>
        new Promise<void>(resolve => {
          finishReload = resolve;
        }),
    );

    // 点页头“刷新”：承诺面板这一次重拉挂着不返回，整轮重拉停在它身上
    await wrapper.find('.header-actions button').trigger('click');
    await flush();
    const before = loadCounts();

    panelOf(wrapper, 'settle').vm.$emit('changed');
    finishReload();
    await flush();

    await clickTab(wrapper, 'archive');

    expect(loadCount('archive')).toBe(before.archive + 1);
  });
});
