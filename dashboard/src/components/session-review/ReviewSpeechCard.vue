<template>
  <section class="rv-panel" aria-label="本场台词">
    <div class="rv-panel-title rv-panel-head">
      <span>本场台词</span>
      <span class="rv-hint">按时间正序；删一句会删掉引用它的档案行，承诺保留但原话标为已删除</span>
    </div>
    <p v-if="error" class="rv-line-error error-first">{{ error }}</p>
    <template v-else>
      <p v-if="actionError" class="rv-line-error">{{ actionError }}</p>
      <div v-loading="loading" class="rv-lines">
        <ul v-if="lines.length > 0" class="rv-line-list">
          <li v-for="line in lines" :key="line.chat_id" class="rv-line-item">
            <span class="rv-mono rv-line-time">{{ formatTimeHMS(line.timestamp_ms) }}</span>
            <span class="rv-line-content" :class="{ 'rv-line-content--dropped': line.dropped }">
              {{ line.content }}
              <span v-if="line.memory_refs.length" class="speech-refs">{{
                line.memory_refs.join('、')
              }}</span>
            </span>
            <el-tag v-if="emotionTag(line)" size="small" effect="plain">{{
              emotionTag(line)
            }}</el-tag>
            <el-tag v-if="line.dropped" size="small" type="info" effect="plain">未送达</el-tag>
            <el-button
              link
              size="small"
              type="danger"
              :loading="busyChatId === line.chat_id"
              :disabled="busyChatId !== null"
              @click="emit('delete', line)"
              >删除这句</el-button
            >
          </li>
        </ul>
        <p v-else class="rv-line-empty">这一场还没有台词记录</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
/**
 * 本场台词面板（直播里说过的每一句，逐句可删；删除会连带删掉引用它的档案行）。
 * 加载与删除状态由页面经组合式持有，本卡只负责展示与转发删除意图（二次确认、
 * 删除后的档案联动刷新都在页面）。
 * 单场语义（多选合并视图页面不传本卡数据）。
 */
import type { ReviewSpeechLine } from '@/types';
import { formatTimeHMS } from '@/utils/format';
import { EMOTION_LABELS } from '@/utils/avatarTuning';

defineProps<{
  lines: ReviewSpeechLine[];
  loading: boolean;
  /** 加载错误：非空时渲染错误、不渲染列表（不与"这一场还没有台词"的空态混淆） */
  error: string;
  actionError: string;
  busyChatId: number | null;
}>();

const emit = defineEmits<{ delete: [line: ReviewSpeechLine] }>();

/** 情绪标签文案：中性不标（无表情信息量），强度有值时带百分比；旧场次数据为 null 不标 */
function emotionTag(line: ReviewSpeechLine): string | null {
  if (!line.emotion || line.emotion === 'neutral') return null;
  const label = EMOTION_LABELS[line.emotion] ?? line.emotion;
  const intensity = line.emotion_intensity;
  return intensity != null ? `${label} ${Math.round(intensity * 100)}%` : label;
}
</script>

<style scoped>
/* 台词加载错误紧贴标题（原页口径），与档案错误留 8px 下边距不同 */
.error-first {
  margin: 0;
}

/* 点名用上的记忆（约#7/档#41）：跟在台词后的弱化小字 */
.speech-refs {
  font-size: var(--text-2xs);
  color: var(--text-secondary);
}
</style>
