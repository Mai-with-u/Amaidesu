<template>
  <span class="stream-text">
    <span
      v-for="(segment, index) in segments"
      :key="index"
      class="stream-seg"
      :class="index < lit ? 'is-lit' : 'is-dim'"
      >{{ segment }}</span
    >
  </span>
</template>

<script setup lang="ts">
// 流式点亮文本：lit 之前的段落实色加粗（已出声），其余灰（还没出声）。
// 分段按句末标点近似引擎内部分句，粒度对齐 tts.utterance.sentence_boundary
// 的段计数；lit 随 WS 进度事件增长（数据面在 useTtsReplay）。
import { computed } from 'vue';
import { splitSpokenSegments } from '@/composables/useTtsReplay';

const props = defineProps<{
  /** 整句文本 */
  text: string;
  /** 已出声的段数 */
  lit: number;
}>();

const segments = computed(() => splitSpokenSegments(props.text));
</script>

<style scoped>
.stream-seg {
  transition: color 0.15s ease;
}

.stream-seg.is-dim {
  color: var(--text-placeholder);
}

.stream-seg.is-lit {
  color: var(--text-primary);
  font-weight: 600;
}

@media (prefers-reduced-motion: reduce) {
  .stream-seg {
    transition: none;
  }
}
</style>
