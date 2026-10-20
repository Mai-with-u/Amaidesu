<!--
  Agent 备忘录/待办常驻面板（直播控制台，纯观测面——只读快照 + 事件刷新，无任何写操作）

  展示：跟随时间线来源过滤（props.target）——全部=四节全显 / 主播=仅主播
  备忘录一节 / 游戏 Agent=仅 minecraft 三节。数据流在 useAgentMemo（REST
  快照 + 事件触发刷新 + 30 秒轮询兜底，双 Agent 常拉，切换零等待）。
  整体可折叠，默认展开；空文档显示轻量占位不隐藏节（保留版面稳定）。
-->
<script setup lang="ts">
import { computed, ref } from 'vue';
import { useAgentMemo } from '@/composables/useAgentMemo';

const props = defineProps<{ target: string }>();

const { streamerNotebook, minecraftMemo } = useAgentMemo();

/** 过滤值 → 节显隐：两端都显即「全部」；单节时版面撑满整行 */
const showStreamer = computed(() => props.target !== 'game');
const showMinecraft = computed(() => props.target !== 'streamer');

/** minecraft 待办状态 → 面板可读文案（未知状态原样展示） */
const TODO_STATUS_TEXT: Record<string, string> = {
  pending: '待办',
  in_progress: '进行中',
  done: '已完成',
};

function todoStatusText(status: string): string {
  return TODO_STATUS_TEXT[status] ?? status;
}

const expanded = ref(true);

function toggleExpanded(): void {
  expanded.value = !expanded.value;
}
</script>

<template>
  <div class="memo-panel">
    <button class="memo-panel-header" type="button" @click="toggleExpanded">
      <span class="memo-panel-title">备忘录 / 待办</span>
      <span class="memo-panel-toggle">{{ expanded ? '收起 ▾' : '展开 ▸' }}</span>
    </button>
    <div v-show="expanded" class="memo-panel-body" :class="{ 'is-single': !showMinecraft }">
      <section v-if="showStreamer" class="memo-section">
        <h4 class="memo-section-title">主播 · 备忘录</h4>
        <pre class="memo-text">{{ streamerNotebook || '（空）' }}</pre>
      </section>
      <template v-if="showMinecraft">
        <section class="memo-section">
          <h4 class="memo-section-title">minecraft · 待办</h4>
          <ul v-if="minecraftMemo.todos && minecraftMemo.todos.length > 0" class="memo-todo-list">
            <li v-for="(todo, index) in minecraftMemo.todos" :key="index" class="memo-todo-item">
              <span class="memo-todo-status" :class="`is-${todo.status}`">
                {{ todoStatusText(todo.status) }}
              </span>
              <span class="memo-todo-content">{{ todo.content }}</span>
            </li>
          </ul>
          <p v-else class="memo-empty">（空）</p>
        </section>
        <section class="memo-section">
          <h4 class="memo-section-title">minecraft · 工作笔记</h4>
          <pre class="memo-text">{{ minecraftMemo.notebook || '（空）' }}</pre>
        </section>
        <section class="memo-section">
          <h4 class="memo-section-title">minecraft · 近期上报</h4>
          <ul
            v-if="minecraftMemo.reports && minecraftMemo.reports.length > 0"
            class="memo-report-list"
          >
            <li
              v-for="(report, index) in minecraftMemo.reports"
              :key="index"
              class="memo-report-item"
            >
              <span class="memo-report-kind">{{ report.kind || '上报' }}</span>
              <span class="memo-report-content">{{ report.content }}</span>
            </li>
          </ul>
          <p v-else class="memo-empty">（空）</p>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.memo-panel {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-extra-light);
  overflow: hidden;
}

.memo-panel-header {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.memo-panel-header:hover {
  color: var(--el-text-color-primary);
}

.memo-panel-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  padding: 4px 10px 10px;
  max-height: 240px;
  overflow-y: auto;
}

/* 单节目标（主播）撑满整行，避免半宽空档 */
.memo-panel-body.is-single {
  grid-template-columns: 1fr;
}

.memo-section {
  min-width: 0;
}

.memo-section-title {
  margin: 0 0 2px;
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.memo-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  font-family: inherit;
}

.memo-empty {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.memo-todo-list,
.memo-report-list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.memo-todo-item,
.memo-report-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  line-height: 1.6;
}

.memo-todo-status {
  flex: none;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.memo-todo-status.is-in_progress {
  color: var(--el-color-primary);
}

.memo-todo-status.is-done {
  color: var(--el-color-success);
}

.memo-todo-content,
.memo-report-content {
  min-width: 0;
  word-break: break-word;
  color: var(--el-text-color-primary);
}

.memo-report-kind {
  flex: none;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
</style>
