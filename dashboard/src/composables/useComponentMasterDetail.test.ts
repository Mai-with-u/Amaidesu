// useComponentMasterDetail.ts 组件主从视图组合式的测试（Agents / Collectors
// 两页同构逻辑）：默认选中语义（RUNNING 优先）、选中保持与重拾、单组件控制
// 与批量控制的分支文案、事件流缓冲（映射/封顶/暂停/清空）、状态标签。

import { effectScope, nextTick, ref, type EffectScope, type Ref } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ElMessage } from 'element-plus';
import {
  STREAM_CAP,
  useComponentMasterDetail,
  type ComponentEvent,
} from '@/composables/useComponentMasterDetail';
import type { ComponentSummary, WebSocketMessage } from '@/types';

const controlComponent = vi.fn();
const batchControl = vi.fn();

vi.mock('@/stores', () => ({
  useComponentsStore: () => ({
    controlComponent,
    batchControl,
  }),
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

function summary(name: string, partial: Partial<ComponentSummary> = {}): ComponentSummary {
  return {
    name,
    group: 'agents',
    type: name,
    is_started: false,
    is_enabled: false,
    ...partial,
  };
}

interface Fixture {
  list: Ref<ComponentSummary[]>;
  events: Ref<ComponentEvent[]>;
  comp: ReturnType<typeof useComponentMasterDetail>;
  scope: EffectScope;
}

function mapEvent(event: ComponentEvent, ctx: { selectedName: string | null }) {
  if (!ctx.selectedName) return null;
  return {
    id: event.id,
    eventType: event.type,
    summary: String(event.data.text ?? ''),
    timestampMs: event.timestamp_ms,
  };
}

function setup(list: ComponentSummary[], events: ComponentEvent[] = []): Fixture {
  const listRef = ref(list);
  const eventsRef = ref(events);
  const scope = effectScope();
  let comp!: ReturnType<typeof useComponentMasterDetail>;
  scope.run(() => {
    comp = useComponentMasterDetail({
      list: listRef,
      domain: 'agents',
      noun: '采集器',
      events: eventsRef,
      mapEvent,
    });
  });
  return { list: listRef, events: eventsRef, comp, scope };
}

function event(id: string, text = `消息${id}`): ComponentEvent {
  const msg: WebSocketMessage = { type: 'agent.replied', timestamp_ms: 1, data: { text } };
  return { ...msg, id };
}

describe('useComponentMasterDetail 选中语义', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('默认选中首个 RUNNING，其次 enabled，否则首个；空清单为 null', () => {
    expect(setup([]).comp.selectedName.value).toBeNull();
    expect(setup([summary('a'), summary('b', { is_enabled: true })]).comp.selectedName.value).toBe(
      'b',
    );
    expect(
      setup([summary('a'), summary('b', { is_enabled: true }), summary('c', { is_started: true })])
        .comp.selectedName.value,
    ).toBe('c');
  });

  it('选中项仍在清单时保持；消失后按默认规则重拾', async () => {
    const f = setup([summary('a', { is_enabled: true }), summary('b')]);
    f.comp.select('b');
    await nextTick();
    expect(f.comp.selectedName.value).toBe('b');

    f.list.value = [summary('a', { is_enabled: true }), summary('b')];
    await nextTick();
    expect(f.comp.selectedName.value).toBe('b');

    f.list.value = [summary('a', { is_enabled: true })];
    await nextTick();
    expect(f.comp.selectedName.value).toBe('a');

    f.list.value = [];
    await nextTick();
    expect(f.comp.selectedName.value).toBeNull();
  });

  it('statusLabel 三态', () => {
    const f = setup([]);
    expect(f.comp.statusLabel(summary('a', { is_started: true }))).toBe('运行中');
    expect(f.comp.statusLabel(summary('a', { is_enabled: true }))).toBe('已停止');
    expect(f.comp.statusLabel(summary('a'))).toBe('未启用');
  });
});

describe('useComponentMasterDetail 控制', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('handleControl 成功：调 store、提示成功、清 loading、回调 afterControl', async () => {
    controlComponent.mockResolvedValue({ message: '已启动' });
    const afterControl = vi.fn();
    const scope = effectScope();
    let comp!: ReturnType<typeof useComponentMasterDetail>;
    scope.run(() => {
      comp = useComponentMasterDetail({
        list: ref([summary('a', { is_enabled: true })]),
        domain: 'agents',
        noun: 'Agent',
        events: ref([]),
        mapEvent,
        afterControl,
      });
    });
    comp.select('a');

    await comp.handleControl('start');

    expect(controlComponent).toHaveBeenCalledWith('agents', 'a', 'start');
    expect(ElMessage.success).toHaveBeenCalledWith('已启动');
    expect(comp.controlPending['a-start']).toBe(false);
    expect(afterControl).toHaveBeenCalledTimes(1);
    scope.stop();
  });

  it('handleControl 失败：透出后端 detail', async () => {
    controlComponent.mockRejectedValue({ response: { data: { detail: '组件不存在' } } });
    const f = setup([summary('a')]);
    f.comp.select('a');

    await f.comp.handleControl('stop');

    expect(ElMessage.error).toHaveBeenCalledWith('组件不存在');
  });

  it('runBatch：零零提示 info、全成提示 success、部分失败提示 warning 附失败样例', async () => {
    const f = setup([]);
    batchControl.mockResolvedValue({ succeeded: 0, failed: 0, messages: [] });
    await f.comp.runBatch('start');
    expect(ElMessage.info).toHaveBeenCalledWith('所有采集器已在运行中');

    batchControl.mockResolvedValue({ succeeded: 2, failed: 0, messages: ['m1', 'm2'] });
    await f.comp.runBatch('stop');
    expect(ElMessage.success).toHaveBeenCalledWith('已停止 2 个采集器');

    batchControl.mockResolvedValue({
      succeeded: 1,
      failed: 1,
      messages: ['启动失败：端口占用', 'ok', '未启动：依赖缺失'],
    });
    await f.comp.runBatch('start');
    const warning = vi.mocked(ElMessage.warning).mock.calls[0][0] as string;
    expect(warning).toContain('1 成功, 1 失败');
    expect(warning).toContain('启动失败：端口占用');
    expect(f.comp.batchLoading.value).toBeNull();
  });
});

describe('useComponentMasterDetail 事件流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('事件按 mapEvent 映射入流，超出封顶保留最近 STREAM_CAP 条', async () => {
    const events = Array.from({ length: STREAM_CAP + 20 }, (_, i) => event(`e${i}`));
    const f = setup([summary('a', { is_started: true })], events);
    await nextTick();

    expect(f.comp.streamItems.value).toHaveLength(STREAM_CAP);
    expect(f.comp.streamItems.value[0].id).toBe('e20');
    expect(f.comp.streamItems.value.at(-1)!.id).toBe(`e${STREAM_CAP + 19}`);
  });

  it('暂停时停渲染，恢复后按当前事件重算；清空即空', async () => {
    const f = setup([summary('a', { is_started: true })], [event('e1')]);
    await nextTick();
    expect(f.comp.streamItems.value).toHaveLength(1);

    f.comp.togglePause();
    f.events.value = [...f.events.value, event('e2')];
    await nextTick();
    expect(f.comp.streamItems.value).toHaveLength(1);

    f.comp.togglePause();
    await nextTick();
    expect(f.comp.streamItems.value).toHaveLength(2);

    f.comp.clearStream();
    expect(f.comp.streamItems.value).toEqual([]);
  });
});
