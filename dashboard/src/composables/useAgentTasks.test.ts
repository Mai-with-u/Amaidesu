// useAgentTasks.ts 任务账本共享组合式的测试
//
// 覆盖：executor 过滤、目标切换重过滤与就绪拉快照、task.changed 计数变化
// 触发静默刷新、cancelTask 的 API 调用与刷新、失败路径的静默/提示语义。

import { effectScope, ref, type EffectScope, type Ref } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { agentsApi, tasksApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useEventsStore } from '@/stores';
import { useAgentTasks, taskStatusText } from '@/composables/useAgentTasks';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  tasksApi: { listTasks: vi.fn() },
  agentsApi: { cancelAgentTask: vi.fn() },
}));

// events store 用响应式假体替代真实模块（真实链路会在 node 测试环境实例化
// WebSocket 客户端）；组合式只读 events 数组，假体同形即可
vi.mock('@/stores', async () => {
  const { reactive } = await import('vue');
  const eventsStore = reactive<{ events: unknown[] }>({ events: [] });
  return { useEventsStore: () => eventsStore };
});

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const listTasksMock = vi.mocked(tasksApi.listTasks);
const cancelAgentTaskMock = vi.mocked(agentsApi.cancelAgentTask);

/** 任务账本条目最小形状（字段与后端 TaskCard 对齐） */
function task(executor: string, taskId = 'task_1', status = 'running') {
  return {
    task_id: taskId,
    instruction: '做一件事',
    summary: '',
    status,
    initiator: 'operator',
    executor,
    created_at_ms: 1000,
    updated_at_ms: 2000,
  };
}

function snapshot(running: ReturnType<typeof task>[], finished: ReturnType<typeof task>[] = []) {
  return { data: { running, finished } } as never;
}

let seq = 0;
/** 往事件 store 塞一条 WS 事件（shape 与 events store 入口一致） */
function pushEvent(type: string): void {
  const store = useEventsStore();
  seq += 1;
  store.events = [...store.events, { id: `e${seq}`, type, timestamp_ms: 1000, data: {} }];
}

describe('useAgentTasks', () => {
  let scope: EffectScope;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    scope = effectScope();
    listTasksMock.mockReset();
    cancelAgentTaskMock.mockReset();
    listTasksMock.mockResolvedValue(snapshot([]));
    useEventsStore().events = [];
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function mount(target: Ref<string | null>) {
    return scope.run(() => useAgentTasks(target))!;
  }

  it('按 executor 过滤进行中与已完结', async () => {
    listTasksMock.mockResolvedValue(
      snapshot(
        [task('minecraft', 'm1'), task('maicraft', 'x1'), task('streamer', 's1')],
        [task('minecraft', 'm0', 'succeeded'), task('streamer', 's0', 'succeeded')],
      ),
    );
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.runningTasks.value.map(t => t.task_id)).toEqual(['m1']);
    expect(composable.finishedTasks.value.map(t => t.task_id)).toEqual(['m0']);
  });

  it('目标切换重新过滤，不重复拉快照', async () => {
    listTasksMock.mockResolvedValue(snapshot([task('minecraft', 'm1'), task('streamer', 's1')]));
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.runningTasks.value.map(t => t.task_id)).toEqual(['m1']);

    target.value = 'streamer';
    await vi.advanceTimersByTimeAsync(0);
    expect(composable.runningTasks.value.map(t => t.task_id)).toEqual(['s1']);
    // 就绪一次 + 切换一次共两次拉取；重过滤本身不新增请求
    expect(listTasksMock).toHaveBeenCalledTimes(2);
  });

  it('目标就绪即拉快照；目标为 null 时不拉', async () => {
    const target = ref<string | null>(null);
    mount(target);
    await vi.advanceTimersByTimeAsync(0);
    expect(listTasksMock).not.toHaveBeenCalled();

    target.value = 'minecraft';
    await vi.advanceTimersByTimeAsync(0);
    expect(listTasksMock).toHaveBeenCalledTimes(1);
  });

  it('task.changed 计数变化触发静默刷新；其他事件不触发', async () => {
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    await vi.advanceTimersByTimeAsync(0);
    expect(listTasksMock).toHaveBeenCalledTimes(1);

    pushEvent('planner.decision');
    await vi.advanceTimersByTimeAsync(0);
    expect(listTasksMock).toHaveBeenCalledTimes(1);

    pushEvent('task.changed');
    await vi.advanceTimersByTimeAsync(0);
    expect(listTasksMock).toHaveBeenCalledTimes(2);
    // 刷新期间不在途转圈（静默语义）
    expect(composable.refreshing.value).toBe(false);
  });

  it('cancelTask 调对 API 并刷新账面', async () => {
    cancelAgentTaskMock.mockResolvedValue({ data: { cancelled: true } } as never);
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    await vi.advanceTimersByTimeAsync(0);

    await composable.cancelTask('task_9');
    expect(cancelAgentTaskMock).toHaveBeenCalledWith('minecraft', 'task_9');
    expect(ElMessage.success).toHaveBeenCalledWith('任务已取消');
    expect(listTasksMock).toHaveBeenCalledTimes(2);
  });

  it('cancelTask 失败提示错误且不抛出', async () => {
    cancelAgentTaskMock.mockRejectedValue(new Error('任务未知'));
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    await vi.advanceTimersByTimeAsync(0);

    await expect(composable.cancelTask('task_9')).resolves.toBeUndefined();
    expect(ElMessage.error).toHaveBeenCalledWith('任务未知');
  });

  it('静默刷新失败不打扰；显式刷新失败弹错', async () => {
    listTasksMock.mockRejectedValue(new Error('后端未装配'));
    const target = ref<string | null>('minecraft');
    const composable = mount(target);
    // 目标就绪触发的静默刷新失败：不抛错、不弹提示
    await vi.advanceTimersByTimeAsync(0);
    expect(ElMessage.error).not.toHaveBeenCalled();

    await expect(composable.refresh(false)).resolves.toBeUndefined();
    expect(ElMessage.error).toHaveBeenCalledWith('后端未装配');
    expect(composable.refreshing.value).toBe(false);
  });

  it('taskStatusText：已知状态映射，未知原样返回', () => {
    expect(taskStatusText('running')).toBe('进行中');
    expect(taskStatusText('waiting_for_decision')).toBe('待定夺');
    expect(taskStatusText('mystery')).toBe('mystery');
  });
});
