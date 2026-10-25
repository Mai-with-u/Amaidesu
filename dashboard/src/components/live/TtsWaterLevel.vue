<!-- TTS 台前水位：在途句数 / 最近空窗秒 / 失败计数三胶囊。
     空窗口径与警戒线见 useTtsWaterLevel（约 2.5s 正常冲入，>3s 警戒） -->
<script setup lang="ts">
import { computed } from 'vue';
import { NORMAL_GAP_MS, WARN_GAP_MS, useTtsWaterLevel } from '@/composables/live/useTtsWaterLevel';

const { waterLevel, warning } = useTtsWaterLevel();

const gapLabel = computed(() => {
  const gapMs = waterLevel.value.gapMs;
  if (gapMs === null) return '—';
  if (gapMs === 0) return '出声中';
  return `${(gapMs / 1000).toFixed(1)}s`;
});

const gapTitle = computed(
  () =>
    `距上一句播完的空窗：约 ${NORMAL_GAP_MS / 1000}s 属正常冲入动作，超过 ${WARN_GAP_MS / 1000}s 提示引擎可能欠载`,
);
</script>

<template>
  <div class="tts-water" :class="{ 'is-warning': warning }" role="status">
    <span class="pill mono" title="已开始出声且未播完/未失败的发声实例数">
      <span class="pill-label">在途</span>{{ waterLevel.inFlightCount }}
    </span>
    <span class="pill mono" :title="gapTitle">
      <span class="pill-label">空窗</span>{{ gapLabel }}
    </span>
    <span class="pill mono" title="近期事件窗口内 tts.utterance.failed 计数">
      <span class="pill-label">失败</span>{{ waterLevel.failureCount }}
    </span>
    <span v-if="warning" class="warn-text">空窗超 3 秒，TTS 引擎可能欠载</span>
  </div>
</template>

<style scoped>
.tts-water {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border, #dcdfe6);
  font-size: 12px;
  color: var(--color-text, #303133);
  background: var(--color-bg, #fff);
}

.pill-label {
  font-size: 11px;
  color: var(--color-text-secondary, #909399);
}

/* 警戒态：空窗过线，胶囊与文案同切警戒色（琥珀警示族） */
.is-warning .pill {
  border-color: var(--color-warning, #e6a23c);
  color: var(--color-warning, #e6a23c);
}

.warn-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-warning, #e6a23c);
}
</style>
