/**
 * 后台任务底栏的数据面：维护「当前在跑的后台任务」列表。
 *
 * 数据来源两路：
 * - 订阅 background.task.changed（经共享 websocket store 派发）：running 载荷
 *   增量更新（登记/进度都走它）；finished/failed 载荷把该任务移出列表——
 *   终态行不留在面板（终态提示由 useSystemNotifications 弹通知）。
 * - 挂载时 GET /api/v1/background-tasks 恢复：浏览器刷新后重建在跑任务表
 *   （后端注册表只存当前在跑的行，无历史）。恢复失败静默降级为空列表，
 *   只记 console——底栏是观测面，不值得为它打断用户。
 *
 * 生命周期语义：挂载在 AppLayout（无 keep-alive 包裹，整会话单实例），
 * setup 时订阅并恢复、作用域销毁时退订——订阅类组合式按激活态启停，
 * 本处无 keep-alive 场景，卸载即彻底停。
 */

import { computed, onScopeDispose, ref } from 'vue';
import { backgroundTasksApi } from '@/api';
import { useWebSocketStore } from '@/stores';
import { EVENT } from '@/types/events';
import type { BackgroundTask } from '@/types/events';
import type { WebSocketMessage } from '@/types';

export function useBackgroundTasks() {
  /** task_id → 任务行（对象键序即插入序，列表按登记先后展示） */
  const tasksById = ref<Record<string, BackgroundTask>>({});

  const taskList = computed(() => Object.values(tasksById.value));

  /** 应用一条变更事件：running 增量更新，终态移除 */
  function applyChange(task: BackgroundTask): void {
    if (task.state === 'running') {
      tasksById.value[task.task_id] = task;
    } else {
      delete tasksById.value[task.task_id];
    }
  }

  function handleMessage(msg: WebSocketMessage): void {
    if (msg.type !== EVENT.backgroundTaskChanged) return;
    applyChange(msg.data as unknown as BackgroundTask);
  }

  /** 挂载恢复：查询口拉当前在跑任务表（失败降级为空） */
  async function restore(): Promise<void> {
    try {
      const { data } = await backgroundTasksApi.list();
      const next: Record<string, BackgroundTask> = {};
      for (const task of data) {
        if (task.state === 'running') next[task.task_id] = task;
      }
      tasksById.value = next;
    } catch (error) {
      console.error('后台任务恢复失败（底栏按空列表展示）:', error);
    }
  }

  const wsStore = useWebSocketStore();
  wsStore.subscribe(handleMessage);
  void restore();

  onScopeDispose(() => {
    wsStore.unsubscribe(handleMessage);
  });

  return { tasks: taskList };
}
