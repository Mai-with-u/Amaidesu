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
      <!-- 任务清单浮层：行 = 标题 / 进度文本 / 已跑时长（共享时钟逐秒刷新）；
           带并行子步骤的任务在行下堆叠子步骤行（每行一步，如整理的几笔模型调用）；
           单步骤模型调用期间另带实时输出预览（后端流式增量随事件刷新） -->
      <div class="task-list">
        <div v-for="task in tasks" :key="task.task_id" class="task-row">
          <div class="task-line">
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
          <div v-if="task.progress_lines?.length" class="task-sublines">
            <div v-for="line in task.progress_lines" :key="line.label" class="task-subline">
              <span class="task-subline-label">{{ line.label }}</span>
              <span class="task-subline-progress">{{ line.progress_text }}</span>
              <span v-if="line.output_tail" class="task-subline-output" :title="line.output_tail">
                正在写：{{ line.output_tail }}
              </span>
            </div>
          </div>
          <div v-else-if="task.output_tail" class="task-output" :title="task.output_tail">
            正在写：{{ task.output_tail }}
          </div>
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
 * 任务行；模型调用期间任务行下带实时输出预览（后端随事件带最近输出的文字尾巴）。
 * 带并行子步骤的任务（如下播整理的几笔模型调用）在任务行下堆叠子步骤行，
 * 每行一步、各带自己的阶段与输出预览。下播整理的任务行另给一个「查看模型调用」
 * 入口，跳到按 settle 档位筛选的模型调用历史。任务收到终态事件即从列表消失
 * （完成/失败的提醒走右上角通知，见 useSystemNotifications）。数据面
 * （订阅 background.task.changed + 挂载恢复）在 useBackgroundTasks；本组件只做呈现。
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
  flex-direction: column;
  gap: var(--spacing-xs);
}

.task-line {
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

/* 模型调用期间的实时输出预览：最近输出的文字尾巴，随事件刷新（非调用期间不渲染） */
.task-output {
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  color: var(--text-secondary);
  word-break: break-all;
  line-height: 1.4;
}

/* 并行子步骤堆叠行：下播整理的几笔模型调用各占一行（如 IDEA 后台任务清单） */
.task-sublines {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: var(--spacing-md);
  border-left: 2px solid var(--border-color-light);
}

.task-subline {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  min-width: 0;
}

.task-subline-label {
  flex-shrink: 0;
  color: var(--text-primary);
}

.task-subline-progress {
  flex-shrink: 0;
  color: var(--text-secondary);
}

/* 该笔的实时输出预览：超长截断不撑宽浮层，全文看 title 提示 */
.task-subline-output {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  word-break: break-all;
}
</style>
