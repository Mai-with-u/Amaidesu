<!-- 主播发言（streamer.speech）：表达语义 + Replyer 统计回看。
     统计胶囊取 Replyer 表达请求（决策卡上是 Planner 请求，二者互补）；
     无指针的旧事件不渲染统计行 -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';
import type { CardProps } from './registry';
import CtxPill from './CtxPill.vue';

defineProps<CardProps>();

const host = useTimelineCardHost();
</script>

<template>
  <div
    class="act is-speech"
    :class="{ 'is-round-clickable': host.isRoundClickable(entry) }"
    :title="host.isRoundClickable(entry) ? '在 Agent 页查看这轮决策' : undefined"
    @click="host.onRowClick(entry)"
  >
    <div class="act-head">
      <span class="act-kind act-kind--speech">主播</span>
      <span v-if="entry.note" class="act-emotion">{{ entry.note }}</span>
      <!-- TTS 播报态标记：tts.utterance.started/finished 按 utterance_id 回填 -->
      <span v-if="entry.ttsState" class="tts-state">{{
        entry.ttsState === 'speaking' ? '播报中' : '已播完'
      }}</span>
      <span class="grow" />
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </div>
    <div v-if="entry.replyTo" class="reply-quote">
      <template v-if="host.replyQuoteOf(entry)">
        <span class="reply-quote-name">{{ host.replyQuoteOf(entry)!.actor }}</span>
        <span class="reply-quote-text">{{ host.replyQuoteOf(entry)!.text }}</span>
      </template>
      <span v-else class="reply-quote-fallback">回复了一条弹幕</span>
    </div>
    <p class="act-text">{{ entry.text }}</p>
    <div v-if="entry.llmRequestId" class="d-meta">
      <span
        v-if="host.cacheLabelOf(entry)"
        class="d-pill d-pill--cache"
        :title="host.cacheTitleOf(entry)"
      >
        {{ host.cacheLabelOf(entry) }}
      </span>
      <span
        v-if="host.tokensLabelOf(entry)"
        class="d-pill d-pill--token"
        :title="host.tokensTitleOf(entry)"
      >
        Token {{ host.tokensLabelOf(entry) }}
      </span>
      <span
        v-if="host.modelNameOf(entry)"
        class="d-pill d-pill--model"
        :title="host.modelNameOf(entry)"
      >
        {{ host.modelNameOf(entry) }}
      </span>
      <CtxPill :entry="entry" />
      <a
        class="d-link"
        :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
        @click.stop
      >
        完整请求 ↗
      </a>
    </div>
  </div>
</template>

<style scoped>
.act-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-agent);
  background: var(--color-agent-bg);
  flex-shrink: 0;
}

/* TTS 播报态标记（发言卡头部小字）：播报中=主色 / 已播完=中性弱色 */
.tts-state {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  background: rgba(64, 158, 255, 0.12);
  flex-shrink: 0;
}
</style>
