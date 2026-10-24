<!-- Agent 响应卡（agent.replied）：命令驱动型 Agent 每步的行动说明，
     决定卡同款胶囊（缓存/Token/模型/上下文，按 llm_request_id 懒取） -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';
import type { CardProps } from './registry';
import CtxPill from './CtxPill.vue';

defineProps<CardProps>();

const host = useTimelineCardHost();

/** 响应卡步骤号（agent.replied payload.step；缺省 0 不渲染） */
function detailStepOf(entry: CardProps['entry']): number {
  const step = entry.detail ? Number(entry.detail.step ?? 0) : 0;
  return Number.isFinite(step) ? step : 0;
}
</script>

<template>
  <div class="decision is-agent-game">
    <div class="act-head">
      <span class="act-kind act-kind--game">响应</span>
      <span v-if="detailStepOf(entry) > 0" class="act-source mono"
        >步骤 {{ detailStepOf(entry) }}</span
      >
      <span class="grow" />
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </div>
    <p class="act-text">{{ entry.text }}</p>
    <div class="d-meta">
      <span
        v-if="host.tokensLabelOf(entry)"
        class="d-pill d-pill--token"
        :title="host.tokensTitleOf(entry)"
      >
        Token {{ host.tokensLabelOf(entry) }}
      </span>
      <details class="d-meta-more">
        <summary>诊断</summary>
        <span
          v-if="host.cacheLabelOf(entry)"
          class="d-pill d-pill--cache"
          :title="host.cacheTitleOf(entry)"
        >
          {{ host.cacheLabelOf(entry) }}
        </span>
        <span
          v-if="host.modelNameOf(entry)"
          class="d-pill d-pill--model"
          :title="host.modelNameOf(entry)"
        >
          {{ host.modelNameOf(entry) }}
        </span>
      </details>
      <CtxPill :entry="entry" />
      <a
        v-if="entry.llmRequestId"
        class="d-link"
        :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
        @click.stop.prevent="host.openLlmRequest(entry.llmRequestId)"
      >
        完整请求 ↗
      </a>
      <a
        class="d-link"
        role="button"
        tabindex="0"
        :title="
          host.isThinkingExpanded(entry)
            ? '收起模型推理正文'
            : '展开该请求的模型推理正文（LLM 请求历史）'
        "
        @keydown.enter.prevent="host.toggleThinking(entry)"
        @keydown.space.prevent="host.toggleThinking(entry)"
        @click.stop="host.toggleThinking(entry)"
      >
        {{ host.isThinkingExpanded(entry) ? '收起思考' : '思考' }}
      </a>
    </div>
    <div v-if="host.isThinkingExpanded(entry)" class="d-thinking">
      <pre v-if="host.reasoningOf(entry)" class="mono">{{ host.reasoningOf(entry) }}</pre>
      <span v-else-if="host.reasoningOf(entry) === ''" class="d-thinking-empty"
        >该请求无思考正文</span
      >
      <span v-else class="d-thinking-empty">思考正文加载中…</span>
    </div>
  </div>
</template>
