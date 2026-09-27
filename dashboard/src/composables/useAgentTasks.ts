/**
 * 目标 Agent 的任务账本视图（进行中 + 已完结）：Agent 页任务板与直播控制台
 * 任务卡条共用的单一事实源。
 *
 * 数据通道：目标就绪/切换时拉一次快照；此后 task.changed 事件计数变化触发
 * 静默刷新（WS 全量订阅已入事件 store，这里只数增量）——但该事件只在任务
 * 状态跃迁时发射，稳态执行期一条都没有，漏接一次（页面开在委派前、WS 断连
 * 且回填失败）账面就会停在旧快照，故另有轻量轮询兜底追平。硬取消走 agents
 * REST。确认框等交互语义归页面层，本组合式只管数据。
 */
import { computed, onScopeDispose, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useEventsStore } from '@/stores';
import { agentsApi, tasksApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import type { TaskCard, TaskSnapshotResponse } from '@/types';

/** 静默轮询周期：快照接口是内存账本读取，代价极小 */
const POLL_INTERVAL_MS = 15000;

/** 任务状态 → 面板可读文案（未知状态原样展示） */
export const TASK_STATUS_TEXT: Record<string, string> = {
  accepted: '已受理',
  running: '进行中',
  waiting_for_decision: '待定夺',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timeout: '已超时',
};

export function taskStatusText(status: string): string {
  return TASK_STATUS_TEXT[status] ?? status;
}

export function useAgentTasks(target: Ref<string | null>) {
  const taskSnapshot = ref<TaskSnapshotResponse>({ running: [], finished: [] });
  const refreshing = ref(false);

  async function refresh(silent = false): Promise<void> {
    if (!silent) refreshing.value = true;
    try {
      const res = await tasksApi.listTasks();
      taskSnapshot.value = res.data;
    } catch (error) {
      // 已完结聚合仅运行内成立、后端未装配任务基建（503）皆属常态——静默轮询不打扰
      if (!silent) ElMessage.error(getApiErrorMessage(error, '获取任务快照失败'));
    } finally {
      if (!silent) refreshing.value = false;
    }
  }

  // 按"归属 Agent"过滤（后端推导：委派 = 执行 Agent；自驱执行 = 发起 Agent）。
  // executor 是执行侧标识（provider 型任务里是提供者名），不能当归属用
  const runningTasks = computed<TaskCard[]>(() =>
    taskSnapshot.value.running.filter(task => task.owner_agent === target.value),
  );
  const finishedTasks = computed<TaskCard[]>(() =>
    taskSnapshot.value.finished.filter(task => task.owner_agent === target.value),
  );

  // task.changed 实时刷新（WS 全量订阅已入 events store；这里只数增量触发拉取）
  const eventsStore = useEventsStore();
  watch(
    () =>
      eventsStore.events.reduce((count, e) => (e.type === 'task.changed' ? count + 1 : count), 0),
    () => void refresh(true),
  );

  // 目标就绪/切换即拉快照：后开的页面与切换目标的场景都能立刻看到当前账面
  watch(
    target,
    name => {
      if (name) void refresh(true);
    },
    { immediate: true },
  );

  // 轮询兜底：task.changed 只在状态跃迁时发射，稳态运行期无事件可依赖
  const pollTimer = setInterval(() => void refresh(true), POLL_INTERVAL_MS);
  onScopeDispose(() => clearInterval(pollTimer));

  /** 硬取消：清追踪 + 账面 cancelled + 通知停手；成败都刷新账面 */
  async function cancelTask(taskId: string): Promise<void> {
    const name = target.value;
    if (!name) return;
    try {
      await agentsApi.cancelAgentTask(name, taskId);
      ElMessage.success('任务已取消');
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '取消失败'));
    }
    await refresh(true);
  }

  return { runningTasks, finishedTasks, refreshing, refresh, cancelTask };
}
