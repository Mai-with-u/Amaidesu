<template>
  <div class="widgets-page">
    <aside class="list-panel" aria-label="小部件列表">
      <header class="list-header">
        <h2 class="list-title">小部件</h2>
        <span class="list-count">{{ widgets.length }} 个</span>
      </header>
      <div class="list-body">
        <button
          v-for="widget in widgets"
          :key="widget.key"
          type="button"
          class="widget-item"
          :class="{ selected: widget.key === selectedKey }"
          @click="selectedKey = widget.key"
        >
          <span class="widget-name">{{ widget.name }}</span>
        </button>
      </div>
    </aside>

    <section v-if="selected" class="detail-panel" aria-label="小部件详情">
      <header class="detail-header">
        <h3 class="detail-title">{{ selected.name }}</h3>
      </header>
      <p class="detail-desc">{{ selected.description }}</p>
      <dl class="detail-fields">
        <div class="field">
          <dt class="field-label">嵌入网址路径</dt>
          <dd class="field-value"><code class="path-code">{{ selected.path }}</code></dd>
        </div>
        <div class="field">
          <dt class="field-label">推荐浏览器源尺寸</dt>
          <dd class="field-value">{{ selected.recommendedSize }}</dd>
        </div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';

import { useWidgetCatalog } from '@/composables/useWidgetCatalog';

/**
 * 小部件管理页（/widgets）：左侧目录、右侧详情的常规控制台页面。
 *
 * 嵌入页本身是供浏览器源捕获的全屏透明页（/widget/<name>，不在壳内导航），
 * 本页承担它们的管理面：查看用途与推荐尺寸、获取嵌入网址。选中态是纯
 * 本地状态，无定时器与订阅，keep-alive 生命周期无额外语义。
 */

const { widgets } = useWidgetCatalog();
const selectedKey = ref(widgets[0]?.key ?? '');
const selected = computed(() => widgets.find(widget => widget.key === selectedKey.value));
</script>

<style scoped>
.widgets-page {
  display: flex;
  height: 100%;
  min-height: 0;
}

.list-panel {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border-color-light);
  background-color: var(--bg-sidebar);
}

.list-header {
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
}

.list-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.list-count {
  font-size: 12px;
  color: var(--text-placeholder);
}

.list-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-sm);
}

.widget-item {
  display: block;
  width: 100%;
  padding: 10px var(--spacing-md);
  margin-bottom: 2px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-regular);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}

.widget-item:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.widget-item.selected {
  background-color: var(--bg-active);
  color: var(--color-primary);
  font-weight: 500;
}

.widget-name {
  font-size: 14px;
}

.detail-panel {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--spacing-lg);
}

.detail-title {
  margin: 0 0 var(--spacing-sm);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.detail-desc {
  margin: 0 0 var(--spacing-lg);
  color: var(--text-regular);
  line-height: 1.6;
}

.detail-fields {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.field-label {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-bottom: 4px;
}

.field-value {
  margin: 0;
  color: var(--text-primary);
}

.path-code {
  padding: 4px 8px;
  border-radius: var(--radius-md);
  background-color: var(--bg-hover);
  font-size: 13px;
  color: var(--color-primary);
}
</style>
