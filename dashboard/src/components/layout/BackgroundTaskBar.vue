<template>
  <el-footer class="task-bar" height="28px">
    <el-popover
      placement="top-start"
      :width="440"
      trigger="click"
      :disabled="tasks.length === 0"
      popper-class="task-bar-popper"
    >
      <template #reference>
        <button class="task-bar-summary" type="button" :disabled="tasks.length === 0">
          <span class="summary-count">{{ tasks.length }} 个后台任务</span>
          <span v-if="firstTask" class="summary-first" :title="firstTask.title">
            {{ firstTask.title }} · {{ firstTask.progress_text }}
          </span>
        </button>
      </template>
      <!-- 任务清单浮层：行 = 标题 / 进度文本 / 已跑时长（共享时钟逐秒刷新） -->
      <div class="task-list">
        <div v-for="task in tasks" :key="task.task_id" class="task-row">
          <span class="task-title" :title="task.title">{{ task.title }}</span>
          <span class="task-progress">{{ task.progress_text }}</span>
          <span class="task-elapsed">{{ formatElapsedMs(now - task.started_at_ms) }}</span>
          <router-link
            v-if="isSettleTask(task)"
            class="task-llm-link"
            to="/llm/history?profile=settle"
          >
            查看模型调用
          </router-link>
        </div>
      </div>
    </el-popover>
  </el-footer>
</template>

<script setup lang="ts">
/**
 * 后台任务底栏：常驻 AppLayout 的状态条（常驻底栏，所有页面可见）。
 *
 * 左侧摘要 = 在跑任务数 + 首个任务标题与进度文本，点开向上弹浮层看全部
 * 任务行；下播整理的任务行另给一个「查看模型调用」入口，跳到按 settle
 * 档位筛选的模型调用历史。任务收到终态事件即从列表消失（完成/失败的提醒走右上角通知，
 * 见 useSystemNotifications）。数据面（订阅 background.task.changed +
 * 挂载恢复）在 useBackgroundTasks；本组件只做呈现。
 */
import { computed } from 'vue';
import { useBackgroundTasks } from '@/composables/useBackgroundTasks';
import { useNowTick } from '@/composables/useNowTick';
import { formatElapsedMs } from '@/utils/format';
import type { BackgroundTask } from '@/types/events';

const { tasks } = useBackgroundTasks();
const now = useNowTick();

const firstTask = computed(() => tasks.value[0] ?? null);

/**
 * 是不是下播整理任务：任务号形如 settle-58。
 *
 * 整理这一场调的是模型档位 settle，链接按该档位筛模型调用历史；
 * 其他后台任务（如记忆索引补算）走别的档位，不给这个链接。
 */
function isSettleTask(task: BackgroundTask): boolean {
  return task.task_id.startsWith('settle-');
}
</script>

<style scoped>
.task-bar {
  display: flex;
  align-items: center;
  background-color: var(--bg-header);
  border-top: 1px solid var(--border-color-light);
  font-size: 12px;
}

.task-bar-summary {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  height: 100%;
  padding: 0 var(--spacing-md);
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  max-width: 100%;
}

.task-bar-summary:hover:not(:disabled) {
  background-color: var(--bg-active);
  color: var(--text-primary);
}

.task-bar-summary:disabled {
  cursor: default;
}

.summary-count {
  flex-shrink: 0;
}

/* 首个任务摘要超长截断，不把底栏撑高 */
.summary-first {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.task-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.task-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.task-progress {
  flex-shrink: 0;
  color: var(--text-secondary);
}

.task-elapsed {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 跳到模型调用历史的入口：只在整理任务行上有 */
.task-llm-link {
  flex-shrink: 0;
  color: var(--text-primary);
  text-decoration: none;
}

.task-llm-link:hover {
  text-decoration: underline;
}
</style>
