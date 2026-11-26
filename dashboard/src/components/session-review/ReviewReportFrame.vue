<template>
  <section class="rv-panel" aria-label="复盘报告">
    <div class="rv-panel-title rv-panel-head">
      <span>复盘报告</span>
      <a class="rv-link report-open" :href="src" target="_blank" rel="noopener">新标签页打开</a>
    </div>
    <iframe class="report-frame" :src="src" sandbox="allow-scripts" title="复盘可视化报告"></iframe>
  </section>
</template>

<script setup lang="ts">
/**
 * 复盘报告面板：logs/reviews 可视化页（由直播复盘流程离线生成）的沙箱嵌入，
 * 新标签页打开兜底。iframe 内文档自带版式（浅色白底），面板底色跟主题走。
 */
defineProps<{ src: string }>();
</script>

<style scoped>
.report-frame {
  width: 100%;
  height: 70vh;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  /* 加载前与边缘露出的底色跟主题；内嵌文档自身的浅色版式保持原样 */
  background: var(--bg-card);
}
</style>
