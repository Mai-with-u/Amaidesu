<template>
  <div class="sheet-container">
    <div v-if="displayState !== 'unconfigured'" class="sheet-stage">
      <img
        v-for="layer in layers"
        :key="layer.id"
        class="sheet-image"
        :class="{ visible: layer.visible }"
        :src="layer.src"
        alt="主播差分立绘"
        @load="onLayerLoad(layer)"
        @error="onImageError"
      />
    </div>
    <div v-else-if="isPreview" class="sheet-placeholder">
      <span>表情差分形象未配置</span>
      <span class="placeholder-hint">在设置页 avatar 域配置素材目录后显示</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { NEUTRAL_EMOTION, useExpressionSheetWidget } from '@/composables/useExpressionSheetWidget';
import { useWebSocketStore } from '@/stores/websocket';

/**
 * 表情差分嵌入页（给 OBS/直播姬浏览器源嵌的全屏透明页，形态参照 Subtitle.vue）。
 *
 * 纯展示页：只跟随 streamer.speech 事件切换差分图，不渲染任何控制台 UI。
 * 展示回退链：对应情绪差分图 404 → neutral 差分图；neutral 也 404 →
 * "未配置"占位。差分图由后端 GET /api/v1/avatar/expression_sheet/assets/<文件名>
 * 服务（emotion 文件名 = 情绪枚举值 + .png）。
 *
 * 两态语义：嵌入态（直播软件里的裸网址）任何提示都不上屏——未配置时就是
 * 纯透明；预览态（管理页打开，带 ?preview=1）才显示占位文字供排查。
 * 展示层用双图交叉淡入（新图加载完成才切换可见层），换图不闪白。
 *
 * 事件消费边界（v1 范围）：LLM 直调 expression_sheet_set_expression 工具的
 * 场景不经 streamer.speech 事件流，不反射到本页。
 */

const route = useRoute();
/** 预览态（管理页"打开"按钮带 ?preview=1）才允许占位提示渲染 */
const isPreview = computed(() => route.query.preview === '1');

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

/** 双层图交叉淡入：新图加载完成后才切换可见层，换图不再闪白 */
interface ImageLayer {
  id: number;
  src: string;
  visible: boolean;
  /** 新图是否已完成加载；完成才允许接管可见层 */
  loaded: boolean;
}

const layers = ref<ImageLayer[]>([
  { id: 0, src: currentSrc.value, visible: true, loaded: true },
  { id: 1, src: '', visible: false, loaded: false },
]);

watch(currentSrc, src => {
  const hidden = layers.value.find(layer => !layer.visible);
  if (!hidden) return;
  if (hidden.src === src) {
    // 表情回到隐藏层已加载过的图（如 中立→笑脸→中立）：src 不变不会触发
    // load 事件，直接接管可见层，否则卡在旧表情不切换
    onLayerLoad(hidden);
    return;
  }
  hidden.loaded = false;
  hidden.src = src;
});

function onLayerLoad(layer: ImageLayer): void {
  layer.loaded = true;
  if (!layer.visible) {
    for (const other of layers.value) {
      other.visible = other.id === layer.id;
    }
  }
}

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

.sheet-stage {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sheet-image {
  position: absolute;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  opacity: 0;
  transition: opacity 0.35s ease;
}

.sheet-image.visible {
  opacity: 1;
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
