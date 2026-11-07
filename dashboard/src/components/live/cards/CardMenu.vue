<!-- 卡片右上角的「⋯」菜单：把诊断类入口（完整请求、思考、原始输出）收起来。
     它们每张卡都有，平铺时占掉一行，把版面切成碎片；收起后卡片只剩读得懂的内容。 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';

const open = ref(false);
const root = ref<HTMLElement | null>(null);

function toggle(): void {
  open.value = !open.value;
}

function onDocumentClick(event: MouseEvent): void {
  if (!open.value) return;
  const target = event.target as Node | null;
  if (target && root.value && !root.value.contains(target)) open.value = false;
}

onMounted(() => document.addEventListener('click', onDocumentClick));
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick));
</script>

<template>
  <span ref="root" class="card-menu">
    <button
      type="button"
      class="card-menu-trigger"
      :class="{ 'is-open': open }"
      title="更多（完整请求 / 思考 / 原始输出）"
      @click.stop="toggle"
    >
      ⋯
    </button>
    <div v-if="open" class="card-menu-panel" @click.stop>
      <slot />
    </div>
  </span>
</template>

<style scoped>
.card-menu {
  position: relative;
  display: inline-flex;
}

.card-menu-trigger {
  border: none;
  background: none;
  padding: 0 4px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.card-menu-trigger:hover,
.card-menu-trigger.is-open {
  background: var(--bg-active);
  color: var(--text-primary);
}

.card-menu-panel {
  position: absolute;
  right: 0;
  top: 18px;
  z-index: 30;
  min-width: 132px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--bg-elevated, var(--bg-card));
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md, 6px);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
}

.card-menu-panel :deep(> *) {
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}

.card-menu-panel :deep(> *:hover) {
  background: var(--bg-active);
  color: var(--text-primary);
}
</style>
