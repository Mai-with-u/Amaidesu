<!-- 安静单行卡：阶段状态 / 进场 / 游戏身体事件 / TTS 失败 / 系统事件 / 工具健康六类共用。
     单行形态一致，行文案拼装与修饰类按 kind 判别（判别知识内聚在此，不进外壳） -->
<script setup lang="ts">
import { computed } from 'vue';
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

const props = defineProps<CardProps>();

/** 修饰类：错误行错误色、身体事件游戏绿（阵亡红）、运行态高亮由 whisper-text.is-running 承载 */
const modifier = computed<string>(() => {
  switch (props.entry.kind) {
    case 'game_body':
      return props.entry.failed ? 'is-body-event is-body-danger' : 'is-body-event';
    case 'tts':
      return 'is-tts-failed';
    case 'system':
    case 'tool_health':
      return props.entry.failed ? 'is-system-error' : '';
    default:
      return '';
  }
});

/** 单行文案：与原各分支模板逐字同构（徽标紧贴正文、备注以「 · 」续接） */
const line = computed<string>(() => {
  const e = props.entry;
  switch (e.kind) {
    case 'tts':
      return `${e.note}${e.text ? ` · 「${e.text}」` : ''}`;
    case 'game_body':
    case 'system':
    case 'tool_health':
      return `${e.badge ? `[${e.badge}]` : ''}${e.text}${e.note ? ` · ${e.note}` : ''}`;
    case 'stage':
      return `${e.text}${e.note ? ` · ${e.note}` : ''}`;
    default:
      return e.text;
  }
});

const running = computed(() => props.entry.kind === 'stage' && props.entry.speak);
</script>

<template>
  <div class="whisper" :class="modifier">
    <span class="whisper-dot" aria-hidden="true" />
    <span class="whisper-text" :class="{ 'is-running': running }">{{ line }}</span>
    <span class="grow" />
    <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
  </div>
</template>

<style scoped>
.whisper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 18px;
}
/* 游戏身体事件：轻量行游戏域色（粉）；死亡（阵亡）红色醒目 */
.whisper.is-body-event .whisper-text {
  color: var(--color-game);
}
.whisper.is-body-event.is-body-danger .whisper-text {
  color: var(--color-danger);
  font-weight: 600;
}
/* 占位宽度与头像一致（28px），使圆点正落在时间轴脊线上 */
.whisper-dot {
  width: 28px;
  height: 12px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  z-index: 1;
}
.whisper-dot::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-placeholder);
  box-shadow: 0 0 0 3px var(--bg-card);
}
.whisper-text {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.whisper-text.is-running {
  color: var(--color-agent);
}

/* 语音合成失败行：错误色突出，与安静单行区分 */
.whisper.is-tts-failed .whisper-text {
  color: var(--color-danger);
}

/* 系统事件行：错误行错误色突出；启停行保持安静单行默认色 */
.whisper.is-system-error .whisper-text {
  color: var(--color-danger);
}
</style>
