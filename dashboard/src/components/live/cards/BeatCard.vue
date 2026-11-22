<!-- 环节推进 / 主控权切换 / 场次边界：横贯分隔行（rundown / lead_change / boundary 共用） -->
<script setup lang="ts">
import { computed, ref } from 'vue';
import { formatElapsedMs, formatTimeHMS } from '@/utils/format';
import type { CardProps } from './registry';

const props = defineProps<CardProps>();

/** 横贯行眉标：按条目类型区分场次、主控与环节 */
const eyebrow = computed<string>(() => {
  if (props.entry.kind === 'boundary') return '场次';
  if (props.entry.kind === 'lead_change') return '主控';
  return '环节';
});

// 交接记忆摘要展开态：默认收起，只留一行入口；收回方向（brief 为 null）不渲染入口
const briefOpen = ref(false);
const brief = computed(() => props.entry.brief ?? null);

/** 环节预期时长（毫秒 → mm:ss）；缺失时为空串不渲染 */
const expectedLabel = computed<string>(() => {
  const ms = brief.value?.expected_ms ?? 0;
  return ms > 0 ? formatElapsedMs(ms) : '';
});
</script>

<template>
  <div class="beat" :class="{ 'is-lead': entry.kind === 'lead_change' }">
    <span class="beat-rule" aria-hidden="true" />
    <span class="beat-body">
      <span class="beat-eyebrow">{{ eyebrow }}</span>
      <span class="beat-label">{{ entry.text }}</span>
      <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
      <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
      <!-- 交接记忆摘要入口：仅交权方向（brief 非空）显示，默认收起 -->
      <button
        v-if="brief"
        type="button"
        class="beat-brief-toggle"
        :aria-expanded="briefOpen"
        @click="briefOpen = !briefOpen"
      >
        {{ briefOpen ? '收起交接记忆摘要' : '查看交接记忆摘要' }}
      </button>
    </span>
    <span class="beat-rule" aria-hidden="true" />
    <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
    <!-- 记忆摘要正文：展开后分节显示；空字段跳过不渲染 -->
    <div v-if="brief && briefOpen" class="beat-brief">
      <div v-if="brief.segment_title || brief.segment_goal || expectedLabel" class="brief-block">
        <span class="brief-cap">环节</span>
        <div class="brief-body">
          <p v-if="brief.segment_title" class="brief-line">{{ brief.segment_title }}</p>
          <p v-if="brief.segment_goal" class="brief-line">{{ brief.segment_goal }}</p>
          <p v-if="expectedLabel" class="brief-line brief-dim">预期时长 {{ expectedLabel }}</p>
        </div>
      </div>
      <div v-if="brief.save_name" class="brief-block">
        <span class="brief-cap">存档</span>
        <div class="brief-body">
          <p class="brief-line">{{ brief.save_name }}</p>
        </div>
      </div>
      <div v-if="brief.memory_brief" class="brief-block">
        <span class="brief-cap">记忆</span>
        <div class="brief-body">
          <!-- 多行文本：white-space: pre-line 保留原始换行 -->
          <p class="brief-line brief-pre">{{ brief.memory_brief }}</p>
        </div>
      </div>
      <div v-if="brief.operator_notes.length > 0" class="brief-block">
        <span class="brief-cap">运营备忘</span>
        <ul class="brief-list">
          <li v-for="(item, i) in brief.operator_notes" :key="`note-${i}`">{{ item }}</li>
        </ul>
      </div>
      <div v-if="brief.recent_speech.length > 0" class="brief-block">
        <span class="brief-cap">最近台词</span>
        <ul class="brief-list">
          <li v-for="(item, i) in brief.recent_speech" :key="`speech-${i}`">{{ item }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
  flex-wrap: wrap;
}
.beat-rule {
  height: 1px;
  background: var(--color-rundown);
  opacity: 0.45;
}
.beat-rule:first-child {
  width: 24px;
  flex-shrink: 0;
}
.beat-rule:last-of-type {
  flex: 1;
}
.beat-body {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.beat-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.beat-action {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: var(--color-rundown-bg);
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 主控权切换：用 Agent 语义色（紫）与流程单环节（琥珀）视觉区分 */
.beat.is-lead .beat-rule {
  background: var(--color-agent);
}
.beat.is-lead .beat-eyebrow {
  color: var(--color-agent);
}
.beat.is-lead .beat-action {
  background: var(--color-agent-bg);
  color: var(--color-agent);
}
/* 交接记忆摘要入口：文本按钮，对齐卡片既有克制视觉 */
.beat-brief-toggle {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 11px;
  color: var(--color-agent);
  cursor: pointer;
  flex-shrink: 0;
  text-decoration: underline dotted;
  text-underline-offset: 2px;
}
.beat-brief-toggle:hover {
  filter: brightness(0.9);
}
/* 记忆摘要正文：独占一行，紧贴分隔行下方，弱化底色不抢主内容 */
.beat-brief {
  flex-basis: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 4px 0 6px;
  padding: 8px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}
.brief-block {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}
.brief-cap {
  flex-shrink: 0;
  width: 56px;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-agent);
}
.brief-body {
  min-width: 0;
}
.brief-line {
  margin: 0;
  font-size: 11px;
  color: var(--text-primary);
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.brief-line.brief-dim {
  color: var(--text-secondary);
}
/* 多行文本（记忆记忆摘要）：保留原始换行 */
.brief-line.brief-pre {
  white-space: pre-line;
}
.brief-list {
  margin: 0;
  padding-left: 16px;
  font-size: 11px;
  color: var(--text-primary);
  line-height: 1.5;
}
</style>
