<!-- 主播发言（streamer.speech）：表达语义 + Replyer 统计回看。
     统计胶囊取 Replyer 表达请求（决策卡上是 Planner 请求，二者互补）；
     无指针的旧事件不渲染统计行 -->
<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { debugApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatTimeHMS } from '@/utils/format';
import { useTimelineCardHost } from './context';
import CardMenu from './CardMenu.vue';
import type { CardProps } from './registry';
import CtxPill from './CtxPill.vue';

defineProps<CardProps>();

const host = useTimelineCardHost();

/** TTS 播报态 → 头部短标签；skipped 的具体原因放 title（由 buildLiveEntries 回填 ttsReason） */
function ttsStateLabel(state: CardProps['entry']['ttsState']): string {
  if (state === 'speaking') return '播报中';
  if (state === 'done') return '已播完';
  if (state === 'failed') return '失败';
  return '未播出';
}

/** 非常态来源徽标：只标需要运营注意的表达来源（主控游戏自主解说），常态来源不标 */
const sourceBadge = (entry: CardProps['entry']): string | null =>
  entry.speechSource === 'game_commentary' ? '游戏解说' : null;

/** 非常态优先级徽标：P3 紧急（醒目）、P1 高光；P0 观众回复与 P2 常规解说保持干净不标 */
const priorityBadge = (entry: CardProps['entry']): { label: string; danger: boolean } | null => {
  if (entry.priority === 3) return { label: '紧急', danger: true };
  if (entry.priority === 1) return { label: '高光', danger: false };
  return null;
};

// 台前重播：同文本重合成（debug/tts/say，走引擎完整生产链路，声卡出声）。
// 同步长请求，按钮 loading 防连点；失败保留重试入口，错误提示统一走
// getApiErrorMessage
const replaying = ref(false);
const replayFailed = ref(false);

async function replay(entryText: string): Promise<void> {
  if (replaying.value || !entryText) return;
  replaying.value = true;
  replayFailed.value = false;
  try {
    const response = await debugApi.sayTts({ text: entryText });
    if (!response.data.success) {
      replayFailed.value = true;
      ElMessage.error(response.data.error || 'TTS 重播失败');
    }
  } catch (error) {
    replayFailed.value = true;
    ElMessage.error(getApiErrorMessage(error, 'TTS 重播失败'));
  } finally {
    replaying.value = false;
  }
}
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
      <!-- 非常态来源/优先级徽标：只标游戏解说与 P1/P3，常态发言保持干净 -->
      <span v-if="sourceBadge(entry)" class="speech-flag">{{ sourceBadge(entry) }}</span>
      <span
        v-if="priorityBadge(entry)"
        class="speech-flag"
        :class="{ 'speech-flag--danger': priorityBadge(entry)!.danger }"
      >
        {{ priorityBadge(entry)!.label }}
      </span>
      <!-- TTS 播报态标记：tts.utterance.started/finished/failed/dropped 按 utterance_id 回填 -->
      <span
        v-if="entry.ttsState"
        class="tts-state"
        :class="{ 'tts-state--danger': entry.ttsState === 'failed' }"
        :title="entry.ttsState === 'skipped' ? entry.ttsReason : undefined"
      >
        {{ ttsStateLabel(entry.ttsState) }}
      </span>
      <!-- 台前重播：原文本重新合成播放（合成失败句保留重试入口） -->
      <el-button
        class="replay-btn"
        link
        size="small"
        :loading="replaying"
        :title="replayFailed ? '上次重播失败，点击重试' : '重新合成播放这句发言'"
        @click.stop="replay(entry.text)"
      >
        {{ replayFailed ? '重试' : '▶ 重播' }}
      </el-button>
      <span class="grow" />
      <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
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
      <CardMenu>
        <a :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`" @click.stop>
          完整请求 ↗
        </a>
      </CardMenu>
    </div>
  </div>
</template>

<style scoped>
/* 发言卡面级色：绿 = 产出/成功语义（"说出来了"）。与工具边线、成功徽标同属
 * 绿色语义族，底色面/边线/徽标是同一语义的三个处理档位 */
.act.is-speech {
  background: var(--color-tool-bg);
  border-left: 2px solid var(--color-tool);
}

.act-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-tool);
  background: var(--color-tool-bg);
  flex-shrink: 0;
}

/* 台前重播入口：弱化为头部小链接钮，不与统计胶囊争注意力 */
.replay-btn {
  font-size: 11px;
  flex-shrink: 0;
}

/* TTS 播报态标记（发言卡头部小字）：播报中/已播完=主色 / 失败=错误色 / 未播出=中性弱色 */
.tts-state {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  background: rgba(64, 158, 255, 0.12);
  flex-shrink: 0;
}

.tts-state--danger {
  color: var(--color-danger);
  background: rgba(245, 108, 108, 0.12);
}

/* 来源/优先级小徽标：与播报态同规格的中性小字，紧急用错误色 */
.speech-flag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-hover);
  flex-shrink: 0;
}

.speech-flag--danger {
  color: var(--color-danger);
  background: rgba(245, 108, 108, 0.12);
}
</style>
