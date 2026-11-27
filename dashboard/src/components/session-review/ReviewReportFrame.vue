<template>
  <div :class="flat ? undefined : 'rv-panel'" aria-label="复盘报告">
    <div v-if="!flat" class="rv-panel-title rv-panel-head">
      <span>复盘报告</span>
      <a class="rv-link report-open" :href="src" target="_blank" rel="noopener">新标签页打开</a>
    </div>
    <p v-if="flat" class="flat-open">
      <a class="rv-link" :href="src" target="_blank" rel="noopener">新标签页打开</a>
    </p>
    <iframe class="report-frame" :src="src" sandbox="allow-scripts" title="复盘可视化报告"></iframe>
  </div>
</template>

<script setup lang="ts">
/**
 * 复盘报告面板：logs/reviews 可视化页（由直播复盘流程离线生成）的沙箱嵌入，
 * 新标签页打开兜底。iframe 内文档自带版式（浅色白底），面板底色跟主题走。
 */
defineProps<{
  src: string;
  /** tab 化呈现：去面板外框与标题（tab 标签已命名），保留新标签页打开入口 */
  flat?: boolean;
}>();
</script>

<style scoped>
/* flat 形态的打开入口与 iframe 留一行间距 */
.flat-open {
  margin: 0 0 8px;
}

.report-frame {
  width: 100%;
  height: 70vh;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  /* 加载前与边缘露出的底色跟主题；内嵌文档自身的浅色版式保持原样 */
  background: var(--bg-card);
}
</style>
