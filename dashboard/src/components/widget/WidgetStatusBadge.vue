<template>
  <div v-if="visible" class="ws-badge" :class="{ prominent }">已断线，重连中…</div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

import type { WidgetSocketStatus } from '@/composables/useRawWebSocket';
import { isPreviewMode } from '@/composables/useWidgetCatalog';

/**
 * 嵌入页断线指示：WebSocket 非连接态时右下角显示细条。
 * 预览态（管理页打开，?preview=1）醒目实底便于排查；嵌入态半透明淡化，
 * 断线仍可辨认但不抢直播画面。纯展示组件，无定时器与订阅。
 */
const props = defineProps<{ status: WidgetSocketStatus }>();
const visible = computed(() => props.status !== 'connected');
const prominent = isPreviewMode();
</script>

<style scoped>
.ws-badge {
  position: fixed;
  right: 10px;
  bottom: 10px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  color: #fff;
  background: rgba(0, 0, 0, 0.55);
  opacity: 0.45;
  pointer-events: none;
  z-index: 10;
}

.ws-badge.prominent {
  opacity: 1;
  background: rgba(196, 74, 74, 0.92);
}
</style>
