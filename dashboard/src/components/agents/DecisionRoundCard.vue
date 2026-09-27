<template>
  <article
    class="round-card"
    :class="{ 'is-highlighted': highlighted, 'is-failed': Boolean(card.error) }"
    :data-round-id="card.roundId"
    :data-highlighted="String(highlighted)"
  >
    <header class="round-head">
      <span class="round-kind">决策</span>
      <code class="round-id mono">{{ card.roundId }}</code>
      <span v-if="card.triggerReason" class="round-trigger mono" :title="'触发原因（机器可读码）'">
        {{ card.triggerReason }}
      </span>
      <span v-if="card.proactive" class="round-chip">主动</span>
      <span class="grow" />
      <time class="round-time mono">{{ timeLabel }}</time>
    </header>

    <!-- 看到了什么：本批弹幕（≤3 条默认展开，更多折叠点开看全） -->
    <details v-if="card.batch.length > 0" class="round-fold" :open="card.batch.length <= 3">
      <summary>本批消息 {{ card.batch.length }} 条</summary>
      <ul class="batch-list">
        <li v-for="(item, i) in card.batch" :key="i" class="batch-item">
          <span class="batch-sender">{{ item.sender || '匿名观众' }}</span>
          <span class="batch-content">{{ item.content }}</span>
        </li>
      </ul>
    </details>

    <!-- 说或不说：失败 / 回应（含发言与情绪）/ 沉默（含原因着色与置信度） -->
    <div v-if="card.error" class="round-verdict is-error">
      <span class="verdict-tag">失败</span>
      <p class="verdict-text">{{ card.error }}</p>
    </div>
    <div v-else-if="card.shouldReply" class="round-verdict is-reply">
      <span class="verdict-tag">回应</span>
      <p v-if="card.speech" class="round-speech">{{ card.speech }}</p>
      <span v-else class="verdict-text">决定发言</span>
      <span v-if="card.emotion" class="round-emotion">{{ card.emotion }}</span>
    </div>
    <div v-else class="round-verdict is-silent">
      <span class="verdict-tag">不说</span>
      <span
        v-if="card.silentReason"
        class="silent-reason"
        :class="silentTone"
        :title="card.silentReason"
      >
        {{ silentReasonLabel }}
      </span>
      <span v-if="card.confidence !== undefined" class="round-conf mono">
        置信 {{ card.confidence.toFixed(2) }}
      </span>
    </div>

    <!-- 本轮工具调用：名 + 成败标记，摘要进悬浮提示 -->
    <ul v-if="card.tools.length > 0" class="round-tools">
      <li v-for="(tool, i) in card.tools" :key="i" class="tool-item" :title="toolTitle(tool)">
        <span class="tool-name mono">{{ tool.name }}</span>
        <span class="tool-status" :class="{ 'is-failed': tool.failed }">
          {{ tool.failed ? '失败' : '成功' }}
        </span>
      </li>
    </ul>

    <footer class="round-meta">
      <span v-if="durationLabel" class="round-duration mono">{{ durationLabel }}</span>
      <span class="grow" />
      <router-link
        v-if="card.llmRequestId"
        class="round-llm-link"
        :to="`/llm/history?request_id=${encodeURIComponent(card.llmRequestId)}`"
      >
        看它发给 AI 的原文 ↗
      </router-link>
      <details v-if="card.plannerRaw" class="round-fold round-raw">
        <summary>原始输出</summary>
        <pre class="mono">{{ card.plannerRaw }}</pre>
      </details>
    </footer>
  </article>
</template>

<script setup lang="ts">
/**
 * 决策轮决定卡（纯展示）：一轮"为什么这么做 / 说了什么"的完整账面。
 * 数据面是 groupDecisionRounds 产出的 DecisionRoundCard，本组件不做任何
 * 取数与业务判断；高亮态与轮次锚点属性（data-round-id / data-highlighted）
 * 供深链定位使用。
 */
import { computed } from 'vue';
import {
  type DecisionRoundCard as CardModel,
  type DecisionRoundToolItem,
} from '@/utils/decisionRounds';
import { relativeTime } from '@/utils/liveFeed';
import { useNowTick } from '@/composables/useNowTick';

const props = withDefaults(
  defineProps<{
    card: CardModel;
    /** 深链定位高亮（由页面按目标轮次传入） */
    highlighted?: boolean;
  }>(),
  { highlighted: false },
);

const nowMs = useNowTick();
const timeLabel = computed(() => relativeTime(nowMs.value, props.card.timestampMs));

/** 静默原因着色：低置信度黄 / LLM 异常红 / 超步数橙 / 其余中性 */
const SILENT_TONE: Record<string, string> = {
  low_confidence: 'is-warn',
  llm_error: 'is-danger',
  llm_failed: 'is-danger',
  max_steps: 'is-orange',
};
const silentTone = computed(() => SILENT_TONE[props.card.silentReason ?? ''] ?? 'is-neutral');

/** 静默原因可读文案（未知码原样展示，不静默丢信息） */
const SILENT_REASON_LABEL: Record<string, string> = {
  natural: '自然终止',
  max_steps: '超出步数上限',
  llm_error: 'LLM 调用异常',
  llm_failed: 'LLM 返回失败',
  prompt_render_failed: '提示词渲染失败',
  assembler_failed: '上下文组装失败',
  low_confidence: '置信度不足',
};
const silentReasonLabel = computed(() => {
  const reason = props.card.silentReason ?? '';
  return SILENT_REASON_LABEL[reason] ?? reason;
});

const durationLabel = computed(() => {
  const d = props.card.durations;
  if (!d) return '';
  return [
    d.plannerMs ? `决策 ${d.plannerMs}ms` : '',
    d.replyMs ? `生成 ${d.replyMs}ms` : '',
    d.totalMs ? `共 ${d.totalMs}ms` : '',
  ]
    .filter(Boolean)
    .join(' · ');
});

/** 工具条目悬浮提示：入参 / 结果 / 错误摘要 */
function toolTitle(tool: DecisionRoundToolItem): string {
  return [tool.argsSummary, tool.resultSummary, tool.error].filter(Boolean).join('\n');
}
</script>

<style scoped>
.round-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px;
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-agent);
  border-radius: var(--radius-md);
  background: var(--bg-page);
}

.round-card.is-failed {
  border-left-color: var(--color-danger);
}

/* 深链定位高亮：描边 + 短暂呼吸动画，3 秒后由页面移除 */
.round-card.is-highlighted {
  border-color: var(--color-primary);
  animation: roundGlow 1.2s ease-in-out 2;
}

@keyframes roundGlow {
  0%,
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
  50% {
    box-shadow: 0 0 0 4px var(--el-color-primary-light-8, rgba(64, 158, 255, 0.18));
  }
}

.round-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.round-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-agent);
  flex-shrink: 0;
}

.round-id {
  font-size: 10px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.round-trigger {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color-light);
  background: var(--bg-card);
  font-size: 10px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
  cursor: default;
}

.round-chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--color-agent-bg);
  color: var(--color-agent);
  flex-shrink: 0;
}

.round-time {
  font-size: 10px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

.round-grow {
  flex: 1;
}

/* 折叠块：本批消息 / 原始输出共用 */
.round-fold summary {
  cursor: pointer;
  user-select: none;
  list-style: none;
  font-size: 11px;
  color: var(--text-secondary);
}
.round-fold summary::-webkit-details-marker {
  display: none;
}
.round-fold summary::before {
  content: '▸';
  margin-right: 4px;
  font-size: 10px;
}
.round-fold[open] summary::before {
  content: '▾';
}

.batch-list {
  list-style: none;
  margin: 6px 0 0;
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}

.batch-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.batch-sender {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--color-primary);
  max-width: 160px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.batch-content {
  color: var(--text-regular);
  word-break: break-word;
}

/* 说或不说 */
.round-verdict {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.verdict-tag {
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-dark);
}

.round-verdict.is-reply .verdict-tag {
  background: var(--color-agent-bg);
  color: var(--color-agent);
  border-color: transparent;
}

.round-verdict.is-error .verdict-tag {
  background: var(--color-danger-bg);
  color: var(--color-danger);
  border-color: transparent;
}

.verdict-text {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
  word-break: break-word;
}

.round-speech {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
}

.round-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  color: var(--color-agent);
  background: var(--color-agent-bg);
  flex-shrink: 0;
}

.silent-reason {
  font-size: 12px;
  font-weight: 600;
}
.silent-reason.is-warn {
  color: var(--color-warning);
}
.silent-reason.is-danger {
  color: var(--color-danger);
}
.silent-reason.is-orange {
  color: #f2711c;
}
.silent-reason.is-neutral {
  color: var(--text-secondary);
}

.round-conf {
  font-size: 11px;
  color: var(--text-secondary);
}

/* 工具列表 */
.round-tools {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color-light);
  background: var(--bg-card);
  max-width: 100%;
  cursor: default;
}

.tool-name {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-status {
  font-size: 10px;
  font-weight: 700;
  color: var(--color-success);
  flex-shrink: 0;
}
.tool-status.is-failed {
  color: var(--color-danger);
}

/* 底部：耗时 + 原文入口 + 原始输出 */
.round-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-height: 18px;
}

.round-duration {
  font-size: 10px;
  color: var(--text-placeholder);
}

.round-llm-link {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  text-decoration: none;
}
.round-llm-link:hover {
  text-decoration: underline;
}

.round-raw {
  flex-basis: 100%;
}
.round-raw pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

.grow {
  flex: 1;
}
</style>
