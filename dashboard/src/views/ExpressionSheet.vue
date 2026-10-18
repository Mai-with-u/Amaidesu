<template>
  <div class="sheet-container">
    <img
      v-if="displayState !== 'unconfigured'"
      class="sheet-image"
      :src="currentSrc"
      alt="主播差分立绘"
      @error="onImageError"
    />
    <div v-else class="sheet-placeholder">
      <span>表情差分形象未配置</span>
      <span class="placeholder-hint">请在 avatar.toml [avatar.platform.expression_sheet] 配置 image_dir</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';

import { NEUTRAL_EMOTION, useExpressionSheetWidget } from '@/composables/useExpressionSheetWidget';
import { useWebSocketStore } from '@/stores/websocket';

/**
 * 表情差分小部件页（给 OBS 浏览器源嵌的全屏展示页，形态参照 Subtitle.vue）。
 *
 * 纯展示页：只跟随 streamer.speech 事件切换差分图，不渲染任何控制台 UI。
 * 展示回退链：对应情绪差分图 404 → neutral 差分图；neutral 也 404 →
 * "未配置"占位。差分图由后端 GET /api/v1/avatar/expression_sheet/assets/<文件名>
 * 服务（emotion 文件名 = 情绪枚举值 + .png）。
 *
 * 事件消费边界（v1 范围）：LLM 直调 expression_sheet_set_expression 工具的
 * 场景不经 streamer.speech 事件流，不反射到本页。
 */

// WS 复用共享事件流连接（useWebSocketStore → api/websocket，重连循环收敛在共享客户端内）
const wsStore = useWebSocketStore();
const { emotion } = useExpressionSheetWidget({
  subscribe: wsStore.subscribe,
  unsubscribe: wsStore.unsubscribe,
  connect: wsStore.connect,
});

/** 展示态：normal=情绪差分图；fallback=neutral 兜底图；unconfigured=未配置占位 */
const displayState = ref<'normal' | 'fallback' | 'unconfigured'>('normal');

const assetUrl = (name: string): string => `/api/v1/avatar/expression_sheet/assets/${name}.png`;

const currentSrc = computed(() =>
  displayState.value === 'fallback' ? assetUrl(NEUTRAL_EMOTION) : assetUrl(emotion.value),
);

/** 图片加载失败：情绪图 → neutral 兜底；neutral 也失败 → 未配置占位 */
const onImageError = (): void => {
  if (displayState.value === 'normal') {
    displayState.value = 'fallback';
    return;
  }
  displayState.value = 'unconfigured';
};

// 情绪变化即回到正常展示链（上一次的 404 回退态不跨情绪沿用）
watch(emotion, () => {
  if (displayState.value !== 'unconfigured') displayState.value = 'normal';
});
</script>

<style>
html,
body,
#app {
  background: transparent !important;
}
</style>

<style scoped>
.sheet-container {
  width: 100%;
  height: 100vh;
  background: transparent !important;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  overflow: hidden;
}

.sheet-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.sheet-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: rgba(128, 128, 128, 0.85);
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  font-size: 20px;
}

.placeholder-hint {
  font-size: 12px;
  opacity: 0.7;
}
</style>
