<!-- 决策卡：verdict（实时裁决）+ decision（沉默/失败轮或统计回填后）共用。
     主行 = 表达指引（怎么讲），话题摘要（聊什么）降级次行；统计胶囊取 Planner 请求 -->
<script setup lang="ts">
import {
  batchSizeOf,
  guidanceOf,
  isSilentDecision,
  plannerMsOf,
  rawOf,
  relativeTime,
  replyMsOf,
  topicOf,
} from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';
import type { CardProps } from './registry';
import CtxPill from './CtxPill.vue';

defineProps<CardProps>();

const host = useTimelineCardHost();
</script>

<template>
  <div
    class="decision"
    :class="{
      'is-failed': entry.failed,
      'is-silent': isSilentDecision(entry),
      'is-round-clickable': host.isRoundClickable(entry),
    }"
    :title="host.isRoundClickable(entry) ? '在 Agent 页查看这轮决策' : undefined"
    @click="host.onRowClick(entry)"
  >
    <div class="act-head">
      <span class="act-kind">决策</span>
      <span class="d-internal" title="主播的内部盘算，不会作为台词念出来">内部</span>
      <code v-if="entry.roundId" class="d-round mono">{{ entry.roundId }}</code>
      <span
        v-if="entry.badge"
        class="act-badge"
        :class="{ 'is-silent-badge': isSilentDecision(entry) }"
      >
        {{ entry.badge }}
      </span>
      <span
        v-if="entry.simulated"
        class="act-source"
        title="由「立即决策测试」手动驱动，非真实弹幕触发"
      >
        测试
      </span>
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
    <!-- 主行 = 表达指引（决策的语义本体：决定怎么讲）→ 带「怎么讲」标签；
         失败轮与无指引的旧数据回退原文（错误/沉默说明/话题摘要） -->
    <p v-if="guidanceOf(entry) && !entry.failed" class="act-text">
      <span class="d-tag">怎么讲</span>{{ guidanceOf(entry) }}
    </p>
    <p v-else class="act-text">{{ entry.text }}</p>
    <!-- 话题摘要带「聊什么」标签降级为次行：Planner 用第一人称写的草稿腔，
         与发言卡台词同声同调，不标会被当成又一段台词 -->
    <p v-if="guidanceOf(entry) && !entry.failed && topicOf(entry)" class="d-topic">
      <span class="d-tag">聊什么</span><span class="d-topic-text">{{ topicOf(entry) }}</span>
    </p>
    <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
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
        <span v-if="batchSizeOf(entry) > 0" class="mono">批次 {{ batchSizeOf(entry) }} 条</span>
        <span v-if="plannerMsOf(entry)" class="mono">决策 {{ plannerMsOf(entry) }}ms</span>
        <span v-if="replyMsOf(entry)" class="mono">生成 {{ replyMsOf(entry) }}ms</span>
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
        v-if="entry.llmRequestId"
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
      <details v-if="rawOf(entry)" class="d-raw">
        <summary @click.stop>原始输出</summary>
        <pre class="mono">{{ rawOf(entry) }}</pre>
      </details>
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

<style scoped>
/* 决策卡走中性灰系：决策是主播的内部盘算（未发声不染产出色），产出绿只给说出口的发言卡——
 * "想的"与"说的"靠底色一眼分层，不依赖读头部标签 */
.decision.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.decision.is-silent {
  background: var(--bg-card);
  border-left-color: var(--border-color);
}
.decision.is-failed .act-text {
  color: var(--color-danger);
}
.decision.is-silent .act-text {
  color: var(--text-secondary);
}

/* 内部角标：与"回应/沉默"徽标（实底）区分，用描边中性款——只做分类标记，不抢状态语义 */
.d-internal {
  padding: 0 5px;
  border: 1px solid var(--border-color-dark);
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.d-round {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

/* 话题摘要行：带「聊什么」标签的次行——正文已让给表达指引，话题摘要降级展示，
 * 第一人称草稿腔不再与发言卡台词混作一团 */
.d-topic {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 5px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  word-break: break-word;
}
/* 行标签：「怎么讲」（表达指引主行）/「聊什么」（话题摘要次行）——决策卡两行自由文本
 * 不带标签就无法与台词区分，也互相分不清 */
.d-tag {
  display: inline-block;
  padding: 0 5px;
  border: 1px solid var(--border-color-dark);
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.act-text .d-tag {
  margin-right: 6px;
}
.d-topic-text {
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.d-raw {
  flex-basis: 100%;
}
.d-raw summary {
  cursor: pointer;
  font-size: 11px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-raw pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}
</style>
