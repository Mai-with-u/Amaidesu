<template>
  <div class="feed-timeline" :class="{ 'is-compact': compact }">
    <div v-if="entries.length === 0" class="stage-empty">
      <el-icon class="stage-empty-icon"><Monitor /></el-icon>
      <p class="stage-empty-text">{{ emptyText }}</p>
    </div>

    <ol v-else class="feed">
      <li v-for="entry in entries" :key="entry.id" class="feed-row">
        <!-- 环节推进 / 场次边界：横贯分隔行 -->
        <div v-if="entry.kind === 'agenda' || entry.kind === 'boundary'" class="beat">
          <span class="beat-rule" aria-hidden="true" />
          <span class="beat-body">
            <span class="beat-eyebrow">{{ entry.kind === 'boundary' ? '场次' : '环节' }}</span>
            <span class="beat-label">{{ entry.text }}</span>
            <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
            <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
          </span>
          <span class="beat-rule" aria-hidden="true" />
          <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
        </div>

        <!-- 里程碑：庆祝行 -->
        <div v-else-if="entry.kind === 'milestone'" class="milestone">
          <span class="milestone-mark" aria-hidden="true">★</span>
          <div class="milestone-body">
            <p class="milestone-text">{{ entry.text }}</p>
            <p v-if="entry.note" class="milestone-meta mono">{{ entry.note }}</p>
          </div>
          <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
        </div>

        <!-- 阶段状态：安静单行（决策管线在做什么/卡在哪） -->
        <div v-else-if="entry.kind === 'stage'" class="whisper">
          <span class="whisper-dot" aria-hidden="true" />
          <span class="whisper-text" :class="{ 'is-running': entry.speak }">
            {{ entry.text }}<template v-if="entry.note"> · {{ entry.note }}</template>
          </span>
          <span class="grow" />
          <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
        </div>

        <!-- 进场：安静单行 -->
        <div v-else-if="entry.kind === 'enter'" class="whisper">
          <span class="whisper-dot" aria-hidden="true" />
          <span class="whisper-text">{{ entry.text }}</span>
          <span class="grow" />
          <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
        </div>

        <!-- 决策卡：verdict（实时裁决）+ decision（沉默/失败轮或统计回填后）共用 -->
        <div
          v-else-if="entry.kind === 'decision' || entry.kind === 'verdict'"
          class="decision"
          :class="{ 'is-failed': entry.failed, 'is-silent': isSilentDecision(entry) }"
        >
          <div class="act-head">
            <span class="act-kind">决策</span>
            <code v-if="entry.roundId" class="d-round mono">{{ entry.roundId }}</code>
            <span v-if="confidenceLabel(entry)" class="d-conf mono">{{
              confidenceLabel(entry)
            }}</span>
            <span
              v-if="entry.badge"
              class="act-badge"
              :class="{ 'is-silent-badge': isSilentDecision(entry) }"
            >
              {{ entry.badge }}
            </span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
          </div>
          <div v-if="entry.replyTo" class="reply-quote">
            <template v-if="replyQuoteOf(entry)">
              <span class="reply-quote-name">{{ replyQuoteOf(entry)!.actor }}</span>
              <span class="reply-quote-text">{{ replyQuoteOf(entry)!.text }}</span>
            </template>
            <span v-else class="reply-quote-fallback">回复了一条弹幕</span>
          </div>
          <p class="act-text">{{ entry.text }}</p>
          <p v-if="guidanceOf(entry)" class="d-guidance">{{ guidanceOf(entry) }}</p>
          <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
          <div class="d-meta">
            <span v-if="batchSizeOf(entry) > 0" class="mono">批次 {{ batchSizeOf(entry) }} 条</span>
            <span v-if="plannerMsOf(entry)" class="mono">决策 {{ plannerMsOf(entry) }}ms</span>
            <span v-if="replyMsOf(entry)" class="mono">生成 {{ replyMsOf(entry) }}ms</span>
            <a
              v-if="entry.llmRequestId"
              class="d-link"
              :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
              @click.stop
            >
              完整请求 ↗
            </a>
            <details
              v-if="plannerThinking && plannerThinking(entry.roundId).length > 0"
              class="d-thinking"
            >
              <summary>思考过程</summary>
              <p
                v-for="seg in plannerThinking!(entry.roundId)"
                :key="seg.step"
                class="d-thinking-phase"
              >
                <span class="d-thinking-tag">Planner · 步骤 {{ seg.step }}</span>
                <span class="mono">{{ seg.text }}</span>
              </p>
            </details>
            <details v-if="rawOf(entry)" class="d-raw">
              <summary>原始输出</summary>
              <pre class="mono">{{ rawOf(entry) }}</pre>
            </details>
          </div>
        </div>

        <!-- 工具调用卡（tool.result.*）：来源徽标在右侧（主播决策 / 游戏 Agent） -->
        <div
          v-else-if="entry.kind === 'tool'"
          class="act"
          :class="{ 'is-failed': entry.failed, 'is-speak': entry.speak }"
        >
          <div class="act-head">
            <span class="act-kind">工具调用</span>
            <code class="act-tool mono">{{ entry.actor }}</code>
            <span class="act-arrow" aria-hidden="true">→</span>
            <span v-if="entry.badge" class="act-badge">{{ entry.badge }}</span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
          </div>
          <p class="act-text">{{ entry.text }}</p>
          <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
        </div>

        <!-- 主播发言（streamer.speech）：表达语义 + Replyer 思考回看 -->
        <div v-else-if="entry.kind === 'speech'" class="act is-speech">
          <div class="act-head">
            <span class="act-kind act-kind--speech">主播</span>
            <span v-if="entry.note" class="act-emotion">{{ entry.note }}</span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
          </div>
          <div v-if="entry.replyTo" class="reply-quote">
            <template v-if="replyQuoteOf(entry)">
              <span class="reply-quote-name">{{ replyQuoteOf(entry)!.actor }}</span>
              <span class="reply-quote-text">{{ replyQuoteOf(entry)!.text }}</span>
            </template>
            <span v-else class="reply-quote-fallback">回复了一条弹幕</span>
          </div>
          <p class="act-text">🎤 {{ entry.text }}</p>
          <details v-if="replyerThinking && replyerThinking(entry.roundId)" class="d-thinking">
            <summary>生成思考</summary>
            <p class="d-thinking-phase">
              <span class="d-thinking-tag">Replyer</span>
              <span class="mono">{{ replyerThinking!(entry.roundId) }}</span>
            </p>
          </details>
        </div>

        <!-- 观众发声：弹幕 / 礼物 / SC -->
        <div v-else class="chat" :class="`chat--${entry.kind}`">
          <span class="avatar" aria-hidden="true">{{ entry.initial }}</span>
          <div class="bubble">
            <div class="bubble-head">
              <span class="who" :title="entry.actor">{{ entry.actor }}</span>
              <span v-if="entry.badge" class="chip">{{ entry.badge }}</span>
              <span v-if="entry.money" class="money mono">{{ entry.money }}</span>
              <span class="grow" />
              <time class="stamp mono">{{ relativeTime(nowSec, entry.tsSec) }}</time>
            </div>
            <p class="say">{{ entry.text }}</p>
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播时间线视图（直播控制台 + 首页共用）
 *
 * 输入：已折叠的 ShowEntry[]（控制台走 buildLiveEntries，首页可只取最近 N 条）
 * 渲染：所有控制台当前支持的行类型——环节/边界、里程碑、阶段、进场、
 *      决策/裁决、工具调用、主播发言、弹幕/礼物/SC 气泡。
 *
 * 控制台独占能力（思考流尾部、暂停/清空、注入面板、滚动跟随）留在 LiveObserver；
 * 本组件只负责"按条目渲染"，对上游数据来源无要求，可被任何 Vue 页面复用。
 */
import { computed, onUnmounted, ref } from 'vue';
import { Monitor } from '@element-plus/icons-vue';
import {
  batchSizeOf,
  confidenceLabel,
  guidanceOf,
  isSilentDecision,
  plannerMsOf,
  rawOf,
  relativeTime,
  replyMsOf,
  type ShowEntry,
  type ThinkingStep,
} from '@/utils/liveFeed';

interface Props {
  /** 已折叠好的时间线条目（顺序即展示顺序） */
  entries: ShowEntry[];
  /** 紧凑模式：用于首页缩略展示，缩短行距/字号 */
  compact?: boolean;
  /** 决策卡"思考过程"折叠面板的数据源；缺失则该面板永不渲染 */
  plannerThinking?: (roundId: string) => ThinkingStep[];
  /** 发言卡"生成思考"折叠面板的文本；缺失则该面板永不渲染 */
  replyerThinking?: (roundId: string) => string;
  /** entries 为空时的提示语 */
  emptyText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  plannerThinking: undefined,
  replyerThinking: undefined,
  emptyText: '静候消息与决策',
});

// 1s tick：让相对时间标签（"刚刚 / 12s 前"）每秒刷新一次；首页独立维护不依赖父组件
const nowMs = ref(Date.now());
const nowSec = computed(() => Math.floor(nowMs.value / 1000));
const tickTimer = setInterval(() => {
  nowMs.value = Date.now();
}, 1000);
onUnmounted(() => {
  clearInterval(tickTimer);
});

/** 弹幕 message_id → 时间线条目（用于发言/决策卡回复引用反查）。
 * 仅索引观众消息类（弹幕 / SC / 礼物）；同一 ID 重复出现时取首次，时间线按 tsSec 正序遍历保证幂等。 */
const messageIndex = computed<Map<string, ShowEntry>>(() => {
  const map = new Map<string, ShowEntry>();
  for (const item of props.entries) {
    if (!item.messageId) continue;
    if (item.kind !== 'danmaku' && item.kind !== 'super_chat' && item.kind !== 'gift') continue;
    if (!map.has(item.messageId)) map.set(item.messageId, item);
  }
  return map;
});

/** 把决策/发言卡的 replyTo 反查成 QQ 风格引用块所需的两字段。
 * 未命中（时间线被清空、消息被水位裁剪、回看数据缺失 message_id 等）返回 null，调用方按占位降级渲染。 */
function replyQuoteOf(entry: ShowEntry): { actor: string; text: string } | null {
  const targetId = entry.replyTo;
  if (!targetId) return null;
  const target = messageIndex.value.get(targetId);
  if (!target) return null;
  return { actor: target.actor, text: target.text };
}
</script>

<style scoped>
/* ============================================================ */
/* 工具类：grow / mono 在行模板里被广泛使用，scoped 内保留副本       */
/* ============================================================ */
.grow {
  flex: 1;
  min-width: 0;
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* ============================================================ */
/* 空态                                                          */
/* ============================================================ */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: 40px 0;
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* ============================================================ */
/* 流：单列居左时间轴脊线——所有条目沿轴排布，靠样式区分             */
/* ============================================================ */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stamp {
  font-size: 10px;
  color: var(--text-placeholder);
  flex-shrink: 0;
  white-space: nowrap;
}

/* ============================================================ */
/* 观众发声：气泡                                                */
/* ============================================================ */
.chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 82%;
}

.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
  border-radius: 4px 12px 12px 12px;
  background: var(--bg-hover);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.who {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-collector);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.4px;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.money {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.say {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- 礼物：暖色高亮 --- */
.chat--gift {
  max-width: 88%;
}
.chat--gift .avatar {
  border-color: var(--color-warning);
  color: var(--color-warning);
}
.chat--gift .bubble {
  background: var(--color-warning-bg);
  border-left: 2px solid var(--color-warning);
  box-shadow: var(--shadow-sm);
}
.chat--gift .who {
  color: var(--color-warning);
}
.chat--gift .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- SC：最强高亮（暖色渐变 + 金额） --- */
.chat--super_chat {
  max-width: 92%;
}
.chat--super_chat .avatar {
  border-color: var(--color-danger);
  color: var(--color-danger);
}
.chat--super_chat .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-danger);
  background: linear-gradient(100deg, var(--color-danger-bg), var(--color-warning-bg));
  box-shadow: var(--shadow-md);
}
.chat--super_chat .who {
  font-size: 12px;
  color: var(--color-danger);
}
.chat--super_chat .chip {
  background: var(--color-danger);
  color: var(--text-inverse);
  border-color: var(--color-danger);
}
.chat--super_chat .money {
  font-size: 14px;
  color: var(--color-danger);
}
.chat--super_chat .say {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.55;
  color: var(--text-primary);
}

/* ============================================================ */
/* 安静单行：进场 / 阶段状态                                      */
/* ============================================================ */
.whisper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 18px;
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

/* ============================================================ */
/* 决策记录：居左宽卡——本轮为什么这么做                            */
/* ============================================================ */
.decision {
  margin-left: 38px; /* 28px 头像 + 10px 间距：与气泡体对齐 */
  max-width: 92%;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  background: var(--color-agent-bg);
  border-left: 3px solid var(--color-agent);
}
.decision.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.decision.is-silent {
  background: var(--bg-hover);
  border-left-color: var(--border-color-dark);
}

.act-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.act-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-agent);
  flex-shrink: 0;
}
.act-kind--speech {
  color: var(--color-agent);
}
.act-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  color: var(--color-agent);
  background: var(--color-agent-bg);
  flex-shrink: 0;
}

.d-round {
  font-size: 10px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.d-conf {
  font-size: 10px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.act-badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--color-danger);
  color: var(--text-inverse);
  flex-shrink: 0;
}
.act-badge.is-silent-badge {
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-dark);
}

.act-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
.decision.is-failed .act-text {
  color: var(--color-danger);
}
.decision.is-silent .act-text {
  color: var(--text-secondary);
}

/* QQ 风格引用块：被回复弹幕的「昵称 + 原文」摘要 */
.reply-quote {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 6px 0 0;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  border-left: 3px solid var(--border-color-dark);
  min-width: 0;
}
.reply-quote-name {
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.reply-quote-text {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-regular);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-clamp: 2;
  overflow: hidden;
  word-break: break-word;
}
.reply-quote-fallback {
  font-size: 11px;
  font-style: italic;
  line-height: 1.5;
  color: var(--text-placeholder);
}

.d-guidance {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  word-break: break-word;
}

.d-speech {
  margin: 5px 0 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
}

.act-note {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-danger);
  word-break: break-word;
}

.d-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-placeholder);
}

.d-link {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  text-decoration: none;
}
.d-link:hover {
  text-decoration: underline;
}

.d-raw {
  flex-basis: 100%;
}
.d-raw summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-raw pre {
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

/* 思考过程：决策卡内回看（同 d-raw 形态） */
.d-thinking {
  flex-basis: 100%;
}
.d-thinking summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-thinking-phase {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
}
.d-thinking-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 9px;
  font-weight: 600;
  line-height: 16px;
  vertical-align: 1px;
}

/* ============================================================ */
/* 主播动作：居左卡（工具结果 / 发言）                             */
/* ============================================================ */
.act {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  min-width: 240px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-tool-bg);
  border-left: 2px solid var(--color-tool);
}
.act.is-speak {
  padding: 10px 14px;
  box-shadow: var(--shadow-sm);
}
.act.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.act.is-speech {
  background: var(--color-agent-bg);
  border-left: 2px solid var(--color-agent);
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.act-arrow {
  font-size: 11px;
  color: var(--text-placeholder);
  flex-shrink: 0;
}

.act.is-failed .act-kind {
  color: var(--color-danger);
}

.act.is-speak .act-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
}
.act.is-speak .act-text::before {
  content: '「';
  color: var(--color-tool);
}
.act.is-speak .act-text::after {
  content: '」';
  color: var(--color-tool);
}

/* ============================================================ */
/* 环节推进 / 场次边界：横贯分隔行                                 */
/* ============================================================ */
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.beat-rule {
  height: 1px;
  background: var(--color-agenda);
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
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-agenda);
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
  font-size: 10px;
  font-weight: 700;
  background: var(--color-agenda-bg);
  color: var(--color-agenda);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============================================================ */
/* 里程碑：庆祝行                                                */
/* ============================================================ */
.milestone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--color-game-bg);
  border: 1px dashed var(--color-game);
}
.milestone-mark {
  font-size: 15px;
  color: var(--color-game);
  flex-shrink: 0;
}
.milestone-body {
  flex: 1;
  min-width: 0;
}
.milestone-text {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}
.milestone-meta {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}

/* ============================================================ */
/* 紧凑模式（首页缩略视图）                                        */
/* ============================================================ */
.feed-timeline.is-compact .feed {
  gap: 6px;
}
.feed-timeline.is-compact .feed-row {
  /* 取消逐行入场动画，避免首页连续刷新时频繁闪烁 */
  animation: none;
}
.feed-timeline.is-compact .chat,
.feed-timeline.is-compact .decision,
.feed-timeline.is-compact .act {
  max-width: 100%;
}
.feed-timeline.is-compact .say,
.feed-timeline.is-compact .act-text {
  font-size: 12px;
}
.feed-timeline.is-compact .stamp {
  font-size: 9px;
}

/* ============================================================ */
/* 窄屏                                                          */
/* ============================================================ */
@media (max-width: 860px) {
  .chat,
  .chat--gift,
  .chat--super_chat,
  .decision,
  .act {
    max-width: 100%;
    margin-left: 0;
  }
}
</style>
