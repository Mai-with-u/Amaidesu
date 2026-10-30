<template>
  <div class="sheet-container">
    <div v-if="displayState !== 'unconfigured'" class="sheet-stage">
      <img
        v-for="layer in layers"
        :key="layer.id"
        class="sheet-image"
        :class="{ visible: layer.visible }"
        :src="layer.src || undefined"
        alt="主播差分立绘"
        @load="onLayerLoad(layer)"
        @error="onImageError(layer)"
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

import { avatarApi } from '@/api';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';

/**
 * 表情差分嵌入页（给 OBS/直播姬浏览器源嵌的全屏透明页，形态参照 Subtitle.vue）。
 *
 * 纯展示页：显示哪张图由后端决定——情绪取面捕合成器此刻的主表情（语音开始播放才换、
 * 播完停一会儿再回落、主播"保持表情"也生效），图按差分图目录解析好（缺图已回退
 * neutral）。本页每 250 毫秒问一次 GET /api/v1/avatar/expression_sheet/state，图变了
 * 才换；404（未启用或未配置目录）显示"未配置"，网络抖动时保持当前图不动。
 *
 * 两态语义：嵌入态（直播软件里的裸网址）任何提示都不上屏——未配置时就是
 * 纯透明；预览态（管理页打开，带 ?preview=1）才显示占位文字供排查。
 * 展示层用双图交叉淡入（新图加载完成才切换可见层），换图不闪白。
 *
 * 生命周期语义：本页是给浏览器源嵌的全屏展示页，不在 keep-alive 容器里——setup 即开始
 * 轮询、作用域销毁时停止；若以后挂进 keep-alive，轮询随激活态启停（组合式已处理）。
 */

const route = useRoute();
/** 预览态（管理页"打开"按钮带 ?preview=1）才允许占位提示渲染 */
const isPreview = computed(() => route.query.preview === '1');

/** 轮询间隔：换图有 0.35 秒淡入，再快观众也看不出来 */
const POLL_INTERVAL_MS = 250;
/** 新图设好后最多等这么久的 load 事件，过了就直接切换（见 watch 里的兜底） */
const LOAD_FALLBACK_MS = 500;

/** 展示态：normal=显示后端给的差分图；unconfigured=未配置占位 */
const displayState = ref<'normal' | 'unconfigured'>('normal');
/** 后端解析好的当前差分图地址（空串 = 还没拿到） */
const currentSrc = ref('');

let inFlight = false;
/** 加载失败过的图地址（文件被删、目录换了没重启）：后端还给这张时不再反复加载、反复闪 */
let brokenSrc = '';

async function refresh(): Promise<void> {
  // 上一次还没回来就跳过这一拍，慢网络下请求不堆积
  if (inFlight) return;
  inFlight = true;
  try {
    const { data } = await avatarApi.getExpressionSheetState();
    if (data.image_url && data.image_url !== brokenSrc) {
      currentSrc.value = data.image_url;
      displayState.value = 'normal';
    } else {
      displayState.value = 'unconfigured';
    }
  } catch (error) {
    const status = (error as { response?: { status?: number } }).response?.status;
    // 404 = 未启用或未配置目录；其他错误（主程序重启、网络抖动）保持当前图
    if (status === 404) displayState.value = 'unconfigured';
  } finally {
    inFlight = false;
  }
}

void refresh();
useKeepAlivePolling(refresh, POLL_INTERVAL_MS, { startOnSetup: true, stopOnDispose: true });

/** 双层图交叉淡入：新图加载完成后才切换可见层，换图不再闪白 */
interface ImageLayer {
  id: number;
  src: string;
  visible: boolean;
  /** 新图是否已完成加载；完成才允许接管可见层 */
  loaded: boolean;
}

const layers = ref<ImageLayer[]>([
  { id: 0, src: '', visible: false, loaded: false },
  { id: 1, src: '', visible: false, loaded: false },
]);

watch(currentSrc, src => {
  if (!src) return;
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
  // 兜底：页面不在绘制时（窗口最小化、被遮住）load 事件可能迟迟不到，图其实已经加载完，
  // 切换却卡在隐藏层；过一会儿还没切过去就直接切（本机取图，这时早已加载好）
  setTimeout(() => {
    if (hidden.src === src && !hidden.visible && displayState.value === 'normal')
      onLayerLoad(hidden);
  }, LOAD_FALLBACK_MS);
});

function onLayerLoad(layer: ImageLayer): void {
  layer.loaded = true;
  if (!layer.visible) {
    for (const other of layers.value) {
      other.visible = other.id === layer.id;
    }
  }
}

/** 图片加载失败：记下这张、按未配置处理；后端给出别的图时恢复 */
function onImageError(layer: ImageLayer): void {
  if (!layer.src) return;
  brokenSrc = layer.src;
  displayState.value = 'unconfigured';
}
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
