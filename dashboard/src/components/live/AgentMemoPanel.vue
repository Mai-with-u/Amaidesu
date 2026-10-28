<!--
  Agent 备忘录/待办常驻面板（直播控制台，纯观测面——只读快照 + 事件刷新，无任何写操作）

  展示：跟随时间线来源过滤（props.target）——全部=四节全显 / 主播=仅主播
  备忘录一节 / 游戏 Agent=仅 minecraft 三节。数据流：REST 快照 + 事件触发
  刷新 + 30 秒轮询兜底，双 Agent 常拉，切换零等待。整体可折叠，默认展开；
  空文档显示
  轻量占位不隐藏节（保留版面稳定）。
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { agentsApi } from '@/api';
import { useEventsStore } from '@/stores';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';
import type { MemoTodoItem } from '@/types';

const props = defineProps<{ target: string }>();

// 数据流：主播备忘录 + minecraft 工作文档（todo / 工作笔记 / 近期上报）混合视图
//
// 数据通道：挂载时各拉一次 REST 快照；此后由事件触发定向刷新——主播侧听
// `streamer.notebook.changed`（写入/清空即广播全文，但面板统一走快照重拉，
// 不直接消费 payload）；minecraft 侧听 `tool.result.*` 中 tool_name 为
// minecraft_notebook / minecraft_todo / minecraft_report 的条目。事件可能
// 漏接（页面开在写入前、WS 断连），另有 30 秒级轮询兜底追平。快照端点是
// 纯内存读取，失败（后端未装配 503 / Agent 未运行 404）属常态，静默不打扰。

/** 静默轮询周期：备忘录写入频率低，30 秒级兜底足够 */
const MEMO_POLL_INTERVAL_MS = 30000;

/** 触发 minecraft 侧刷新的工具结果（tool.result 事件 payload 的 tool_name） */
const MINECRAFT_MEMO_TOOL_NAMES = new Set([
  'minecraft_notebook',
  'minecraft_todo',
  'minecraft_report',
]);

/** WebSocketMessage 最小形状（events store 条目；tool_name 读取只关心这一段） */
interface StoreEvent {
  type: string;
  data?: { tool_name?: string } | null;
}

/** minecraft 工作文档三元组（todos/reports 拉取失败时为 null，面板显示占位） */
interface MinecraftMemo {
  notebook: string;
  todos: MemoTodoItem[] | null;
  reports: Array<Record<string, string>> | null;
}

const streamerNotebook = ref('');
const minecraftMemo = ref<MinecraftMemo>({ notebook: '', todos: null, reports: null });

async function refreshStreamers(): Promise<void> {
  try {
    const res = await agentsApi.getAgentMemo('streamer');
    streamerNotebook.value = res.data.notebook;
  } catch {
    // 后端未装配（503）/ Agent 未运行（404）属常态：保留旧值，静默不打扰
  }
}

async function refreshMinecraft(): Promise<void> {
  try {
    const res = await agentsApi.getAgentMemo('minecraft');
    minecraftMemo.value = {
      notebook: res.data.notebook,
      todos: res.data.todos ?? null,
      reports: res.data.reports ?? null,
    };
  } catch {
    // 同上：静默保留旧值
  }
}

async function refreshMemo(): Promise<void> {
  await Promise.all([refreshStreamers(), refreshMinecraft()]);
}

// 挂载即拉一次快照：后开的面板也能立刻看到当前文档
void refreshMemo();

// 事件触发定向刷新（WS 全量订阅已入 events store；这里按计数增量触发）
const eventsStore = useEventsStore();
let lastNotebookCount = 0;
let lastToolResultCount = 0;
watch(
  () => eventsStore.events,
  events => {
    const storeEvents = events as unknown as StoreEvent[];
    const notebookCount = storeEvents.reduce(
      (count, e) => (e.type === 'streamer.notebook.changed' ? count + 1 : count),
      0,
    );
    if (notebookCount !== lastNotebookCount) {
      lastNotebookCount = notebookCount;
      void refreshStreamers();
    }
    const toolCount = storeEvents.reduce(
      (count, e) =>
        e.type.startsWith('tool.result.') && MINECRAFT_MEMO_TOOL_NAMES.has(e.data?.tool_name ?? '')
          ? count + 1
          : count,
      0,
    );
    if (toolCount !== lastToolResultCount) {
      lastToolResultCount = toolCount;
      void refreshMinecraft();
    }
  },
);

// 轮询兜底：事件只在写入时刻发射，漏接一次快照就停在旧数据。
// 面板多在全局 keep-alive 内——setup 即启动 + 激活态启停 + 销毁兜底
useKeepAlivePolling(() => refreshMemo(), MEMO_POLL_INTERVAL_MS, {
  startOnSetup: true,
  stopOnDispose: true,
});

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
  /* 纵向 flex：面板被外层压缩时由内容区让位并就地滚动（见 .memo-panel-body
     的 min-height），而不是裁掉滚动条让下半截内容不可达 */
  display: flex;
  flex-direction: column;
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
  min-height: 0;
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
