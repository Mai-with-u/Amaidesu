// @vitest-environment happy-dom
// 场次复盘页（SessionReview.vue）的 keep-alive 刷新与工具栏刷新行为测试
//
// 使用真实 Vue Router 与 <KeepAlive> 包裹复盘页和占位页：切走再切回真正触发
// onDeactivated / onActivated，验证场次列表静默刷新、自动跟进更新的已结束场次、
// 尊重用户手动选择与深链参数，以及工具栏「刷新」按钮防重复点击语义。

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, KeepAlive, nextTick } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';
import {
  ElAlert,
  ElButton,
  ElEmpty,
  ElOption,
  ElRadioButton,
  ElRadioGroup,
  ElSelect,
  ElTag,
} from 'element-plus';
import SessionReview from './SessionReview.vue';
import type { LiveSessionItem, SessionKind, SessionReviewResponse } from '@/types';

const api = vi.hoisted(() => ({
  liveSessionsApi: {
    list: vi.fn(),
    rundownExecution: vi.fn(),
  },
  reviewApi: {
    get: vi.fn(),
    listSpeech: vi.fn(),
    deleteSpeech: vi.fn(),
    listCues: vi.fn(),
    exportUrl: vi.fn((ids: number[]) => `/api/v1/review/export?ids=${ids.join(',')}`),
  },
  ledgerApi: {
    listArchive: vi.fn(),
    deleteArchiveLine: vi.fn(),
  },
}));

vi.mock('@/api', () => api);

// ECharts 图表组合式在 happy-dom 中不依赖真实 canvas 渲染，用空实现隔离
vi.mock('@/composables/useECharts', () => ({
  useChartPalette: () => ({
    value: {
      textColor: '#333',
      borderColor: '#ddd',
      borderColorLight: '#eee',
      monoFont: 'monospace',
    },
  }),
  useECharts: vi.fn(),
}));

const OtherPage = {
  name: 'OtherPage',
  render: () => h('div', { class: 'other-page' }, '其他页面'),
};

const KeepAliveHost = defineComponent({
  name: 'KeepAliveHost',
  setup() {
    return () =>
      h(RouterView, null, {
        default: ({ Component }: { Component?: unknown }) =>
          h(KeepAlive, null, Component ? [h(Component as never)] : []),
      });
  },
});

function makeSession(
  id: number,
  options: { title?: string; ended?: boolean; startedAtMs?: number; kind?: SessionKind } = {},
): LiveSessionItem {
  const startedAt = options.startedAtMs ?? 1_700_000_000_000 + id * 3_600_000;
  return {
    live_session_id: id,
    source: 'manual',
    kind: options.kind ?? 'live',
    title: options.title ?? `第 ${id} 场`,
    notes: null,
    room_id: 'room-1',
    platform: 'bilibili',
    started_at_ms: startedAt,
    ended_at_ms: options.ended === false ? null : startedAt + 1_800_000,
    message_count: 12,
    is_active: options.ended === false,
    settled_at_ms: options.ended === false ? null : startedAt + 1_900_000,
    settle_attempts: 0,
    reviewed_at_ms: null,
    overview: null,
  };
}

function makeReview(ids: number[]): SessionReviewResponse {
  return {
    sessions: ids.map(id => ({
      live_session_id: id,
      title: `第 ${id} 场`,
      source: 'manual',
      started_at_ms: 1_700_000_000_000,
      ended_at_ms: 1_700_001_800_000,
      duration_ms: 1_800_000,
      heat: 10,
      audience_total: 5,
    })),
    window_start_ms: 1_700_000_000_000,
    window_end_ms: 1_700_001_800_000,
    degraded: false,
    report_file: null,
    chips: {
      danmaku_count: 10,
      enter_count: 2,
      gift_count: 0,
      super_chat_count: 0,
      gift_total_cny: null,
      super_chat_total_cny: null,
    },
    speech: {
      count: 12,
      rate_per_min: 1.2,
      longest_silence_ms: 15_000,
      longest_silence_start_ms: null,
    },
    decisions: {
      total: 5,
      distribution: { danmaku: 5 },
      silent_count: 0,
      failed_count: 0,
      latency: { p50_ms: 1000, p90_ms: 2000, max_ms: 3000, max_at_ms: null, sample_count: 5 },
    },
    llm: {
      overall: {
        calls: 5,
        slow_calls: 0,
        failed_calls: 0,
        prompt_tokens: 1000,
        completion_tokens: 200,
        cache_hit_rate: 0.5,
        cost: 0.01,
      },
      by_profile: [],
    },
    game: {
      total: 0,
      by_type: {},
      items: [],
      truncated: false,
    },
    viewers: null,
    waterline: [],
  } as unknown as SessionReviewResponse;
}

let mountedWrappers: VueWrapper[] = [];

async function flush(): Promise<void> {
  await flushPromises();
  await nextTick();
  await flushPromises();
  await nextTick();
}

async function mountInKeepAlive(
  initialQuery: Record<string, string> = {},
): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/review', name: 'review', component: SessionReview },
      { path: '/other', name: 'other', component: OtherPage },
      { path: '/memory', name: 'memory', component: OtherPage },
      { path: '/live', name: 'live', component: OtherPage },
      { path: '/llm/history', name: 'llm-history', component: OtherPage },
    ],
  });
  await router.replace({ path: '/review', query: initialQuery });
  await router.isReady();
  const wrapper = mount(KeepAliveHost, {
    global: {
      plugins: [router],
      components: {
        ElAlert,
        ElButton,
        ElEmpty,
        ElOption,
        ElRadioButton,
        ElRadioGroup,
        ElSelect,
        ElTag,
      },
      directives: { loading: () => undefined },
      stubs: { ArchiveLineList: true },
    },
  });
  mountedWrappers.push(wrapper);
  await flush();
  return { wrapper, router };
}

beforeEach(() => {
  vi.resetAllMocks();
  api.liveSessionsApi.rundownExecution.mockRejectedValue(new Error('no rundown'));
  api.ledgerApi.listArchive.mockResolvedValue({ data: { items: [] } });
  api.reviewApi.listSpeech.mockResolvedValue({ data: { items: [] } });
  api.reviewApi.listCues.mockResolvedValue({ data: { items: [] } });
  api.reviewApi.get.mockImplementation(async (ids?: number[]) => ({
    data: makeReview(ids?.length ? ids : [1]),
  }));
});

afterEach(() => {
  for (const wrapper of mountedWrappers) wrapper.unmount();
  mountedWrappers = [];
});

describe('SessionReview keep-alive 刷新与默认选中跟进', () => {
  it('首次挂载拉一次列表并默认选中最新已结束场次，不因首次 onActivated 重复请求', async () => {
    api.liveSessionsApi.list.mockResolvedValue({
      data: {
        items: [
          makeSession(79, { title: '进行中场次', ended: false }),
          makeSession(78, { title: '最新已结束场次', ended: true }),
          makeSession(77, { title: '旧场次', ended: true }),
        ],
      },
    });

    await mountInKeepAlive();

    expect(api.liveSessionsApi.list).toHaveBeenCalledTimes(1);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(1);
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([78]);
  });

  it('切走再切回页面：停在自动默认旧场次且出现更新已结束场次时，自动切换并重拉复盘数据', async () => {
    api.liveSessionsApi.list
      .mockResolvedValueOnce({
        data: {
          items: [
            makeSession(78, { title: '场次 78', ended: false }),
            makeSession(77, { title: '场次 77', ended: true }),
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            makeSession(78, { title: '场次 78', ended: true }),
            makeSession(77, { title: '场次 77', ended: true }),
          ],
        },
      });

    const { router } = await mountInKeepAlive();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([77]);

    // 切去其他页面再切回：场次 78 已结束，应自动切到 78 并重拉复盘聚合
    await router.push('/other');
    await flush();
    await router.push('/review');
    await flush();

    expect(api.liveSessionsApi.list).toHaveBeenCalledTimes(2);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(2);
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([78]);
  });

  it('切走再切回页面：用户手动选定特定场次时只刷新列表，不覆盖用户选择', async () => {
    api.liveSessionsApi.list
      .mockResolvedValueOnce({
        data: {
          items: [
            makeSession(77, { title: '场次 77', ended: true }),
            makeSession(76, { title: '场次 76', ended: true }),
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            makeSession(78, { title: '场次 78', ended: true }),
            makeSession(77, { title: '场次 77', ended: true }),
            makeSession(76, { title: '场次 76', ended: true }),
          ],
        },
      });

    const { wrapper, router } = await mountInKeepAlive();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([77]);

    // 模拟用户在下拉框手动选择场次 76
    const select = wrapper.findComponent(ElSelect);
    await select.vm.$emit('update:modelValue', [76]);
    await select.vm.$emit('change', [76]);
    await flush();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);

    // 切走再切回：新场次 78 已出现，但用户手动选择 76 应原样保留，不强行跳到 78
    await router.push('/other');
    await flush();
    await router.push('/review');
    await flush();

    expect(api.liveSessionsApi.list).toHaveBeenCalledTimes(2);
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(2);
  });

  it('带 ?session= 深链进入后切走再切回不覆盖深链选中；带新 ?session= 切回则切到新深链', async () => {
    api.liveSessionsApi.list.mockResolvedValue({
      data: {
        items: [
          makeSession(78, { title: '场次 78', ended: true }),
          makeSession(77, { title: '场次 77', ended: true }),
          makeSession(76, { title: '场次 76', ended: true }),
        ],
      },
    });

    const { router } = await mountInKeepAlive({ session: '76' });
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);

    // 原样切走再切回同深链：保留 76
    await router.push('/other');
    await flush();
    await router.push({ path: '/review', query: { session: '76' } });
    await flush();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(1);

    // 切走后带着新深链 ?session=77 返回：切到 77 并重拉
    await router.push('/other');
    await flush();
    await router.push({ path: '/review', query: { session: '77' } });
    await flush();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([77]);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(2);
  });
  it('带 ?session= 深链进入后手动改选其他场次，切走再带同深链切回可重新选中深链场次', async () => {
    api.liveSessionsApi.list.mockResolvedValue({
      data: {
        items: [
          makeSession(78, { title: '场次 78', ended: true }),
          makeSession(77, { title: '场次 77', ended: true }),
          makeSession(76, { title: '场次 76', ended: true }),
        ],
      },
    });

    const { wrapper, router } = await mountInKeepAlive({ session: '76' });
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);

    // 手动在下拉框切到场次 77
    const select = wrapper.findComponent(ElSelect);
    await select.vm.$emit('update:modelValue', [77]);
    await select.vm.$emit('change', [77]);
    await flush();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([77]);

    // 切走后再从外部带回同一深链 ?session=76，应重新应用深链切回 76
    await router.push('/other');
    await flush();
    await router.push({ path: '/review', query: { session: '76' } });
    await flush();
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([76]);
  });

  it('默认选中跳过彩排场，取最新已结束的正式场', async () => {
    api.liveSessionsApi.list.mockResolvedValue({
      data: {
        items: [
          makeSession(79, { title: '彩排场', ended: true, kind: 'rehearsal' }),
          makeSession(78, { title: '正式场', ended: true }),
        ],
      },
    });

    await mountInKeepAlive();

    expect(api.reviewApi.get).toHaveBeenLastCalledWith([78]);
  });

  it('选中含彩排场时页头出警示条，只选正式场时不出', async () => {
    api.liveSessionsApi.list.mockResolvedValue({
      data: {
        items: [
          makeSession(78, { title: '正式场', ended: true }),
          makeSession(77, { title: '彩排场', ended: true, kind: 'rehearsal' }),
        ],
      },
    });

    const { wrapper } = await mountInKeepAlive();
    expect(wrapper.text()).not.toContain('选中含彩排场');

    // 手动把彩排场并进选择：警示条出现
    const select = wrapper.findComponent(ElSelect);
    await select.vm.$emit('update:modelValue', [78, 77]);
    await select.vm.$emit('change', [78, 77]);
    await flush();
    expect(wrapper.text()).toContain('选中含彩排场');
  });
});

describe('SessionReview 工具栏「刷新」按钮与错误提示', () => {
  it('点击「刷新」重拉列表与当前复盘数据，并在请求未完成前防重复点击', async () => {
    api.liveSessionsApi.list.mockResolvedValueOnce({
      data: { items: [makeSession(78, { title: '场次 78', ended: true })] },
    });

    const { wrapper } = await mountInKeepAlive();
    expect(api.liveSessionsApi.list).toHaveBeenCalledTimes(1);
    expect(api.reviewApi.get).toHaveBeenCalledTimes(1);

    let finishList!: () => void;
    api.liveSessionsApi.list.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finishList = () =>
            resolve({ data: { items: [makeSession(78, { title: '场次 78', ended: true })] } });
        }),
    );

    const refreshBtn = wrapper.find('.review-refresh-btn');
    await refreshBtn.trigger('click');
    await nextTick();
    // 加载态下连点第二次被拦截
    await refreshBtn.trigger('click');
    expect(api.liveSessionsApi.list).toHaveBeenCalledTimes(2);

    finishList();
    await flush();

    expect(api.reviewApi.get).toHaveBeenCalledTimes(2);
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([78]);
  });

  it('列表接口失败时经 getApiErrorMessage 展示错误提示，再次刷新成功后清空错误', async () => {
    api.liveSessionsApi.list
      .mockRejectedValueOnce({
        response: { data: { detail: '后端场次接口暂时不可用' } },
      })
      .mockResolvedValueOnce({
        data: { items: [makeSession(78, { title: '场次 78', ended: true })] },
      });

    const { wrapper } = await mountInKeepAlive();

    expect(wrapper.text()).toContain('后端场次接口暂时不可用');

    await wrapper.find('.review-refresh-btn').trigger('click');
    await flush();

    expect(wrapper.text()).not.toContain('后端场次接口暂时不可用');
    expect(api.reviewApi.get).toHaveBeenLastCalledWith([78]);
  });
});
