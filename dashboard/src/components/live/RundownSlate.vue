<!--
  直播控制台的"当前环节"横幅：当前段 + 本段要聊什么 + 时间账 + 进度段，
  点整条横幅弹出流程单时间轴（悬浮小窗，不是对话框），列出走过与还没到的环节。

  数据由 useRundownConsole 提供（执行事实 + 实时快照合成），本组件只做展示
  与操作派发；控制动作（下一环节 / 暂停恢复 / 跳转 / 插话题）经 control 事件
  交回页面调用 rundownApi。
-->
<template>
  <section class="slate" :class="{ 'is-idle': !currentRow }" aria-label="当前环节">
    <el-popover
      placement="bottom-start"
      :width="440"
      trigger="click"
      popper-class="rundown-timeline-popper"
    >
      <template #reference>
        <div class="slate-bar">
          <span class="slate-eyebrow">当前环节</span>
          <template v-if="currentRow">
            <span class="slate-order mono">#{{ currentRow.order }}</span>
            <h2 class="slate-label" :title="currentRow.title">{{ currentRow.title }}</h2>
            <!-- 谁切的这段：Agent 自己 / 你手动 / 系统 -->
            <span v-if="actorLabel" class="slate-action">{{ actorLabel }}</span>
            <span v-if="paused" class="slate-action is-paused">已暂停</span>
            <!-- 本段要聊什么：提纲要点优先，没有要点就退回环节目标 -->
            <span class="slate-note" :title="noteTitle">{{ noteText }}</span>
            <!-- 接下来是哪个环节：横幅右半边不空着 -->
            <span v-if="nextRow" class="slate-next">
              <span class="slate-next-cap">接下来</span>
              <span class="slate-next-title" :title="nextRow.title">{{ nextRow.title }}</span>
              <span class="slate-next-expected mono">预计 {{ nextExpectedLabel }}</span>
            </span>
            <span class="grow" />
            <span
              class="slate-time mono"
              :class="{ 'is-over': currentRow.overExpected }"
              :title="timeTitle"
              >{{ dwellLabel }} / {{ expectedLabel }}</span
            >
            <!-- 进度段：每段一格，按预计时长定比例 -->
            <span class="slate-segments" aria-hidden="true">
              <span
                v-for="row in rows"
                :key="row.key"
                class="segment"
                :class="`is-${row.state}`"
                :style="{ flexGrow: row.expectedMs > 0 ? row.expectedMs : 1 }"
              />
            </span>
            <!-- 操作件：点它们不触发横幅的展开（事件不冒泡到触发元素） -->
            <span class="slate-actions" @click.stop>
              <el-button
                size="small"
                text
                :disabled="!live || actionLoading !== null"
                @click="emit('control', 'next')"
                >下一环节</el-button
              >
              <el-button
                size="small"
                text
                :disabled="!live || actionLoading !== null"
                @click="emit('control', paused ? 'resume' : 'pause')"
                >{{ paused ? '恢复' : '暂停' }}</el-button
              >
              <span class="slate-timeline-hint">时间轴 ▾</span>
            </span>
          </template>
          <span v-else class="slate-idle">{{ idleText }}</span>
        </div>
      </template>

      <!-- 悬浮时间轴：走过的环节（含谁切的、停留 vs 预计、暂停）+ 还没到的 -->
      <div class="timeline">
        <header class="timeline-head">
          <span class="timeline-title">流程单时间轴</span>
          <span class="timeline-meta">{{ timelineSummary }}</span>
        </header>
        <ul v-if="timelineRows.length > 0" class="timeline-list">
          <li
            v-for="row in timelineRows"
            :key="row.key"
            class="timeline-row"
            :class="`is-${row.state}`"
          >
            <span class="timeline-dot" aria-hidden="true" />
            <div class="timeline-body">
              <div class="timeline-line">
                <span class="timeline-name" :title="row.title">
                  <span class="timeline-order mono">#{{ row.order }}</span>
                  {{ row.title }}
                </span>
                <span class="grow" />
                <span
                  v-if="row.state !== 'upcoming'"
                  class="timeline-dwell mono"
                  :class="{ 'is-over': row.overExpected }"
                  >{{ row.dwellLabel }}</span
                >
                <span v-if="row.state === 'upcoming'" class="timeline-dwell mono is-plan"
                  >预计 {{ row.expectedLabel }}</span
                >
              </div>
              <div class="timeline-sub">
                <span v-if="row.enteredAtMs" class="mono">{{ row.enteredLabel }}</span>
                <span v-if="row.actorLabel" class="timeline-tag">{{ row.actorLabel }}</span>
                <span v-if="row.pauseCount > 0" class="timeline-tag"
                  >暂停 {{ row.pauseCount }} 次共 {{ row.pauseLabel }}</span
                >
                <span v-if="row.state === 'current'" class="timeline-tag is-current">{{
                  row.isOpen ? '还在这段' : '停在这段'
                }}</span>
              </div>
              <p v-if="row.beats.length > 0" class="timeline-beats">
                提纲：{{ row.beats.join(' · ') }}
              </p>
              <p v-else-if="row.taskDescription" class="timeline-beats">
                目标：{{ row.taskDescription }}
              </p>
              <p v-if="row.notes" class="timeline-notes">备注：{{ row.notes }}</p>
              <!-- 还没到的环节：实时模式下可直接跳过去 -->
              <el-button
                v-if="row.state === 'upcoming' && live"
                size="small"
                text
                class="timeline-goto"
                :disabled="actionLoading !== null"
                @click.stop="emit('control', 'goto', { segment_id: row.segId })"
                >跳到这段</el-button
              >
            </div>
          </li>
        </ul>
        <p v-else class="timeline-empty">本场没有流程单环节记录</p>

        <!-- 插一段应急话题：冷场时插在当前环节之后（实时模式可用） -->
        <div v-if="live" class="timeline-insert">
          <el-button
            size="small"
            text
            :disabled="actionLoading !== null"
            @click="insertOpen = !insertOpen"
            >插一段应急话题</el-button
          >
          <div v-if="insertOpen" class="insert-form">
            <el-input
              v-model="insertTitle"
              size="small"
              maxlength="50"
              placeholder="话题标题（必填）"
              aria-label="应急话题标题"
            />
            <el-input
              v-model="insertContent"
              size="small"
              type="textarea"
              :rows="2"
              maxlength="500"
              placeholder="话题内容 / 引导词（可空）"
              aria-label="应急话题内容"
            />
            <div class="insert-actions">
              <el-button size="small" @click="insertOpen = false">取消</el-button>
              <el-button
                size="small"
                type="primary"
                :loading="actionLoading === 'insert'"
                :disabled="insertTitle.trim() === ''"
                @click="submitInsert"
                >插入</el-button
              >
            </div>
          </div>
        </div>
      </div>
    </el-popover>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { formatElapsedMs, formatTimeHM } from '@/utils/format';
import {
  rundownActionLabel,
  rundownActorLabel,
  type RundownConsoleRow,
} from '@/utils/rundownConsole';
import type { RundownControlAction } from '@/types';

const props = defineProps<{
  /** 环节行（走过的 + 还没到的，有序） */
  rows: RundownConsoleRow[];
  /** 当前段（null = 没有流程单运行） */
  currentRow: RundownConsoleRow | null;
  /** 实时模式（回看模式没有运行中的流程单，控制件不可用） */
  live: boolean;
  /** 流程单已暂停 */
  paused: boolean;
  /** 正在执行的动作（用于禁用按钮） */
  actionLoading: RundownControlAction | null;
  /** 无流程单时的占位文案 */
  idleText?: string;
}>();

const emit = defineEmits<{
  (
    e: 'control',
    action: RundownControlAction,
    extra?: { segment_id?: string; payload?: { title: string; content?: string } },
  ): void;
}>();

const insertOpen = ref(false);
const insertTitle = ref('');
const insertContent = ref('');

/** 当前段是谁切的：Agent 自己 / 你手动 / 系统 */
const actorLabel = computed(() => {
  const row = props.currentRow;
  if (!row) return '';
  const actor = rundownActorLabel(row.by);
  const action = rundownActionLabel(row.action);
  if (!actor) return action;
  return action ? `${actor} ${action}` : actor;
});

/**
 * 本段要聊什么：提纲要点优先，没有要点退回环节目标。
 * 环节目标往往是一长串（分句写给 AI 看），横幅只摆第一句，完整内容进悬停。
 */
const noteText = computed(() => {
  const row = props.currentRow;
  if (!row) return '';
  if (row.beats.length > 0) return row.beats.join(' · ');
  const firstClause = row.taskDescription.split(/[；;]/)[0]?.trim() ?? '';
  return firstClause;
});

/** 悬停说明：目标全文（横幅里只摆第一句） */
const noteTitle = computed(() => {
  const row = props.currentRow;
  if (!row) return '';
  if (row.beats.length > 0) return `本段提纲：${row.beats.join(' · ')}`;
  return row.taskDescription;
});

const dwellLabel = computed(() =>
  props.currentRow ? formatElapsedMs(props.currentRow.dwellMs) : '',
);

/** 下一个还没到的环节（横幅预告接下来干什么） */
const nextRow = computed(() => props.rows.find(row => row.state === 'upcoming') ?? null);

const nextExpectedLabel = computed(() =>
  nextRow.value ? formatElapsedMs(nextRow.value.expectedMs) : '',
);

const expectedLabel = computed(() =>
  props.currentRow ? formatElapsedMs(props.currentRow.expectedMs) : '',
);

/** 悬停说明：把"已播/预计"两个口径讲全 */
const timeTitle = computed(() => {
  const row = props.currentRow;
  if (!row) return '';
  const parts = [`已播 ${formatElapsedMs(row.dwellMs)}`, `预计 ${formatElapsedMs(row.expectedMs)}`];
  if (row.pauseCount > 0) {
    parts.push(`本段暂停 ${row.pauseCount} 次，共 ${formatElapsedMs(row.pauseMs)}`);
  }
  if (row.minDurationMs) {
    parts.push(`最少停留 ${formatElapsedMs(row.minDurationMs)}`);
  }
  return parts.join(' · ');
});

const timelineSummary = computed(() => {
  const done = props.rows.filter(row => row.state !== 'upcoming').length;
  const total = props.rows.length;
  return total > 0 ? `共 ${total} 段 · 已过 ${done} 段` : '';
});

/** 浮层里的行：补上展示用的派生字段（谁切的 / 停留文案） */
const timelineRows = computed(() =>
  props.rows.map(row => ({
    ...row,
    actorLabel: rundownActorLabel(row.by),
    dwellLabel: `${formatElapsedMs(row.dwellMs)} / ${formatElapsedMs(row.expectedMs)}`,
    expectedLabel: formatElapsedMs(row.expectedMs),
    pauseLabel: formatElapsedMs(row.pauseMs),
    enteredLabel: row.enteredAtMs ? `${formatTimeHM(row.enteredAtMs)} 进入` : '',
  })),
);

function submitInsert(): void {
  const title = insertTitle.value.trim();
  if (!title) return;
  const content = insertContent.value.trim();
  emit('control', 'insert', { payload: { title, ...(content ? { content } : {}) } });
  insertTitle.value = '';
  insertContent.value = '';
  insertOpen.value = false;
}
</script>

<style scoped>
.slate {
  flex-shrink: 0;
}

.slate-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 44px;
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-rundown);
  border-radius: var(--radius-md);
  background: var(--color-rundown-bg);
  cursor: pointer;
}

.slate.is-idle .slate-bar {
  border-left-color: var(--border-color-dark, var(--border-color-light));
  background: var(--bg-card);
  cursor: default;
}

.slate-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-rundown);
  flex-shrink: 0;
}

.slate.is-idle .slate-eyebrow {
  color: var(--text-placeholder);
}

.slate-order {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.slate-label {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 30%;
  /* 环节名是横幅的身份信息，不参与压缩——挤没了就等于横幅没说话 */
  flex-shrink: 0;
}

.slate-action {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-rundown);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-rundown);
  flex-shrink: 0;
}

.slate-action.is-paused {
  border-color: var(--el-color-warning, #e6a23c);
  color: var(--el-color-warning, #e6a23c);
}

.slate-note {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 34%;
  /* 目标文字最先被压缩（横幅里是补充信息，完整内容在悬停里） */
  flex-shrink: 1;
  min-width: 0;
}

.slate-time {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* 接下来是哪个环节：横幅右半边的内容，不让它空着 */
.slate-next {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding-left: 10px;
  border-left: 1px solid var(--border-color-light);
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 0;
  flex-shrink: 1;
}

.slate-next-cap {
  font-size: 10px;
  letter-spacing: 0.6px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

.slate-next-title {
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 18ch;
}

.slate-next-expected {
  font-size: 11px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

/* 超出预计时长：这段时间账不是正常节奏，标出来 */
.slate-time.is-over {
  color: var(--el-color-danger, #f56c6c);
  font-weight: 600;
}

/* 进度段：每段一格，宽度按预计时长分配（窄列里先于文字让位） */
.slate-segments {
  display: flex;
  gap: 2px;
  width: 96px;
  flex-shrink: 1;
  min-width: 48px;
  overflow: hidden;
}

.segment {
  min-width: 10px;
  height: 6px;
  border-radius: 2px;
  background: var(--border-color-light);
}

.segment.is-done {
  background: var(--color-rundown);
  opacity: 0.45;
}

.segment.is-current {
  background: var(--color-rundown);
}

.segment.is-upcoming {
  background: var(--text-placeholder);
  opacity: 0.35;
}

.slate-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.slate-timeline-hint {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 0 4px;
}

.slate-idle {
  font-size: 13px;
  color: var(--text-placeholder);
}

/* ==================== 悬浮时间轴 ==================== */

.timeline {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  max-height: 420px;
  overflow-y: auto;
}

.timeline-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
}

.timeline-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.timeline-meta {
  font-size: 11px;
  color: var(--text-secondary);
}

.timeline-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.timeline-row {
  display: flex;
  gap: var(--spacing-sm);
  padding: 6px 0;
}

/* 左侧竖线：把各环节串成一条时间轴 */
.timeline-dot {
  position: relative;
  width: 8px;
  flex-shrink: 0;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--text-placeholder);
}

.timeline-row:not(:last-child) .timeline-dot::after {
  content: '';
  position: absolute;
  left: 3px;
  top: 10px;
  width: 1px;
  height: calc(100% + 18px);
  background: var(--border-color-light);
}

.timeline-row.is-done .timeline-dot {
  background: var(--color-rundown);
  opacity: 0.5;
}

.timeline-row.is-current .timeline-dot {
  background: var(--color-rundown);
  box-shadow: 0 0 0 3px var(--color-rundown-bg);
}

.timeline-body {
  flex: 1;
  min-width: 0;
}

.timeline-line {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
}

.timeline-name {
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.timeline-row.is-upcoming .timeline-name {
  color: var(--text-secondary);
}

.timeline-order {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-right: 4px;
}

.timeline-dwell {
  font-size: 11px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.timeline-dwell.is-over {
  color: var(--el-color-danger, #f56c6c);
  font-weight: 600;
}

.timeline-dwell.is-plan {
  color: var(--text-placeholder);
}

.timeline-sub {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: 2px;
}

.timeline-tag {
  padding: 0 6px;
  border-radius: 999px;
  border: 1px solid var(--border-color-light);
  font-size: 10px;
  color: var(--text-secondary);
}

.timeline-tag.is-current {
  border-color: var(--color-rundown);
  color: var(--color-rundown);
}

.timeline-beats,
.timeline-notes {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
}

.timeline-notes {
  color: var(--text-placeholder);
}

.timeline-goto {
  margin-top: 2px;
  padding: 0;
  font-size: 11px;
}

.timeline-empty {
  margin: 0;
  font-size: 12px;
  color: var(--text-placeholder);
}

.timeline-insert {
  border-top: 1px solid var(--border-color-light);
  padding-top: var(--spacing-sm);
}

.insert-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}

.insert-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--spacing-sm);
}
</style>
