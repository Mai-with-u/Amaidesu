// useBackgroundTasks.ts 后台任务底栏数据面的测试
//
// 覆盖：挂载恢复（查询口拉当前在跑任务表，终态行不收；失败静默降级为空）、
// running 事件增量更新（登记新任务 / 覆盖同任务进度，顺序 = 登记先后）、
// 终态事件移除、非本事件不处理、作用域销毁后退订。
// 惯例：vi.mock 掉 @/api（查询口）与 @/api/websocket 的 wsClient，经真实
// websocket store 的 subscribe 捕获 handler，直接投喂消息驱动。

import { afterEach, describe, expect, it, vi } from 'vitest';
import { effectScope, type EffectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';

import { useBackgroundTasks } from '@/composables/useBackgroundTasks';
import { useWebSocketStore } from '@/stores';
import type { WebSocketMessage, WsMessageType } from '@/types';
import type { BackgroundTask } from '@/types/events';

const { listMock } = vi.hoisted(() => ({ listMock: vi.fn() }));

vi.mock('@/api', () => ({
  backgroundTasksApi: {
    list: listMock,
  },
}));

vi.mock('@/api/websocket', () => ({
  wsClient: {
    onConnect: vi.fn(),
    onDisconnect: vi.fn(),
    onMessage: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn(),
    isConnected: vi.fn(() => false),
  },
}));

/** 构造一条任务行（缺省字段按契约补齐） */
function taskRow(overrides: Partial<BackgroundTask> = {}): BackgroundTask {
  return {
    task_id: 'settle-58',
    title: '整理场次：58',
    state: 'running',
    progress_text: '并行模型整理中（已完成 0/3 笔）',
    started_at_ms: 1_706_745_600_000,
    ...overrides,
  };
}

function taskEvent(payload: BackgroundTask): WebSocketMessage {
  return {
    type: 'background.task.changed' as WsMessageType,
    timestamp_ms: 1_706_745_600_000,
    data: payload as unknown as Record<string, unknown>,
  };
}

/** 挂起组合式并捕获其订阅的 handler；scope.stop() 即模拟组件卸载 */
async function mount(): Promise<{
  feed: (msg: WebSocketMessage) => void;
  scope: EffectScope;
  unsubscribed: unknown[];
  tasksRef: ReturnType<typeof useBackgroundTasks>;
}> {
  setActivePinia(createPinia());
  let handler: ((msg: WebSocketMessage) => void) | null = null;
  const unsubscribed: unknown[] = [];
  const wsStore = useWebSocketStore();
  const originalSubscribe = wsStore.subscribe.bind(wsStore);
  const originalUnsubscribe = wsStore.unsubscribe.bind(wsStore);
  vi.spyOn(wsStore, 'subscribe').mockImplementation(fn => {
    handler = fn;
    return originalSubscribe(fn);
  });
  vi.spyOn(wsStore, 'unsubscribe').mockImplementation(fn => {
    unsubscribed.push(fn);
    return originalUnsubscribe(fn);
  });
  let tasksRef: ReturnType<typeof useBackgroundTasks> | null = null;
  const scope = effectScope();
  scope.run(() => {
    tasksRef = useBackgroundTasks();
  });
  // restore 是 setup 内发起的 async 调用，让微队列走完
  await vi.waitFor(() => {
    expect(listMock).toHaveBeenCalled();
  });
  return {
    feed: (msg: WebSocketMessage) => handler?.(msg),
    scope,
    unsubscribed,
    tasksRef: tasksRef as unknown as ReturnType<typeof useBackgroundTasks>,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('useBackgroundTasks', () => {
  it('挂载时经查询口恢复：在跑任务进列表，终态行不收', async () => {
    listMock.mockResolvedValue({
      data: [taskRow(), taskRow({ task_id: 'gone', state: 'finished' })],
    });
    const { tasksRef } = await mount();
    expect(tasksRef.tasks.value.map(t => t.task_id)).toEqual(['settle-58']);
  });

  it('查询口失败时静默降级为空列表', async () => {
    listMock.mockRejectedValue(new Error('网络断开'));
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { tasksRef } = await mount();
    await Promise.resolve();
    expect(tasksRef.tasks.value).toEqual([]);
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it('running 事件登记新任务并覆盖同任务进度', async () => {
    listMock.mockResolvedValue({ data: [] });
    const { tasksRef, feed } = await mount();
    feed(taskEvent(taskRow({ progress_text: '并行模型整理中（已完成 0/3 笔）' })));
    expect(tasksRef.tasks.value.map(t => t.task_id)).toEqual(['settle-58']);
    feed(
      taskEvent(
        taskRow({
          progress_text: '第 2 笔/共 3 笔 · 模型输出中（已 3 字，最后活动 0s 前）',
          output_chars: 3,
          output_tail: '整理中',
        }),
      ),
    );
    expect(tasksRef.tasks.value).toHaveLength(1);
    expect(tasksRef.tasks.value[0]?.progress_text).toBe('第 2 笔/共 3 笔 · 模型输出中（已 3 字，最后活动 0s 前）');
    // 模型调用期间的输出信号原样透传，浮层据此画实时输出预览
    expect(tasksRef.tasks.value[0]?.output_chars).toBe(3);
    expect(tasksRef.tasks.value[0]?.output_tail).toBe('整理中');
    // 第二个任务排在后面：列表顺序 = 登记先后
    feed(
      taskEvent(
        taskRow({
          task_id: 'memory-index-backfill',
          title: '记忆索引补算',
          progress_text: '还缺 320 行',
        }),
      ),
    );
    expect(tasksRef.tasks.value.map(t => t.task_id)).toEqual([
      'settle-58',
      'memory-index-backfill',
    ]);
  });

  it('并行子步骤进度行（progress_lines）随载荷原样透传', async () => {
    listMock.mockResolvedValue({ data: [] });
    const { tasksRef, feed } = await mount();
    feed(
      taskEvent(
        taskRow({
          progress_lines: [
            { label: '整理本场档案', progress_text: '模型输出中（已 312 字）', output_chars: 312, output_tail: '这场她' },
            { label: '找新承诺', progress_text: '模型整理中（已 8 秒）' },
          ],
        }),
      ),
    );
    expect(tasksRef.tasks.value[0]?.progress_lines).toEqual([
      { label: '整理本场档案', progress_text: '模型输出中（已 312 字）', output_chars: 312, output_tail: '这场她' },
      { label: '找新承诺', progress_text: '模型整理中（已 8 秒）' },
    ]);
    // 覆盖为不带行的载荷（调用全部完成）后行清空
    feed(taskEvent(taskRow({ progress_text: '3 笔模型调用完成，校验输出中' })));
    expect(tasksRef.tasks.value[0]?.progress_lines).toBeUndefined();
  });

  it('终态事件把任务移出列表', async () => {
    listMock.mockResolvedValue({ data: [] });
    const { tasksRef, feed } = await mount();
    feed(taskEvent(taskRow()));
    expect(tasksRef.tasks.value).toHaveLength(1);
    feed(taskEvent(taskRow({ state: 'finished' })));
    expect(tasksRef.tasks.value).toEqual([]);
    feed(taskEvent(taskRow({ state: 'failed', failure_reason: '模型调用超时' })));
    expect(tasksRef.tasks.value).toEqual([]);
  });

  it('非 background.task.changed 事件不处理', async () => {
    listMock.mockResolvedValue({ data: [] });
    const { tasksRef, feed } = await mount();
    feed({
      type: 'core.error' as WsMessageType,
      timestamp_ms: 1_706_745_600_000,
      data: {
        task_id: 'x',
        title: 'x',
        state: 'running',
        progress_text: '',
        started_at_ms: 1,
      },
    });
    expect(tasksRef.tasks.value).toEqual([]);
  });

  it('作用域销毁后退订', async () => {
    listMock.mockResolvedValue({ data: [] });
    const { scope, unsubscribed } = await mount();
    scope.stop();
    expect(unsubscribed).toHaveLength(1);
  });
});
