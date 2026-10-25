<!--
  导演命令面板：全局 Ctrl/Cmd+K 呼出的命令列表，把常用干预动作（催主播 /
  控流程单 / 取消催办任务 / 页面跳转）收进一个键盘驱动的浮层。
  状态与动作注册表在 useCommandPalette 组合式，本组件只负责渲染与热键监听。
-->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch, type ComponentPublicInstance } from 'vue';
import { useRouter } from 'vue-router';
import { createDefaultActions, useCommandPalette } from '@/composables/useCommandPalette';

const router = useRouter();

const palette = useCommandPalette({
  actions: createDefaultActions(),
  navigate: (path: string) => {
    void router.push(path);
  },
});

// 热键监听的生命周期语义：本组件挂在 App.vue（keep-alive 之外、layout 之上），
// 整个应用生命周期只挂载一次——keydown 监听随 mounted 绑定、unmounted 移除，
// 不存在 deactivated 后仍响应热键的问题；隐式依赖全局唯一实例，勿放进 keep-alive 页面。
function onGlobalKeydown(event: KeyboardEvent): void {
  palette.onKeydown(event);
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown);
});

// 搜索词变化时把选择光标拨回第一条，避免高亮停留在被过滤掉的条目位置
watch(palette.query, () => {
  palette.activeIndex.value = 0;
});

// 浮层打开时聚焦搜索框，键盘即可直接打字过滤（函数 ref 的入参形态由 Vue 约定）
function focusInput(el: Element | ComponentPublicInstance | null): void {
  (el as HTMLInputElement | null)?.focus();
}
</script>

<template>
  <Teleport to="body">
    <transition name="palette">
      <div v-if="palette.visible.value" class="palette-overlay" @mousedown.self="palette.close()">
        <div class="palette-panel" role="dialog" aria-label="导演命令面板">
          <input
            :ref="focusInput"
            v-model="palette.query.value"
            class="palette-input"
            type="text"
            placeholder="输入关键词找动作（如：催、跳转、取消）…"
            @keydown.down.prevent="palette.activeIndex.value += 1"
            @keydown.up.prevent="palette.activeIndex.value -= 1"
            @keydown.enter.prevent="
              palette.execute(palette.flatActions.value[palette.activeIndex.value]!)
            "
          />
          <div class="palette-list">
            <template v-if="palette.flatActions.value.length === 0">
              <div class="palette-empty">没有匹配的动作，换个关键词试试</div>
            </template>
            <template v-for="group in palette.groupedActions.value" :key="group.group">
              <div class="palette-group-title">{{ group.group }}</div>
              <button
                v-for="item in group.items"
                :key="item.key"
                type="button"
                class="palette-item"
                :class="{
                  'is-active': palette.flatActions.value[palette.activeIndex.value] === item,
                  'is-danger': item.danger === true,
                }"
                @mousemove="palette.activeIndex.value = palette.flatActions.value.indexOf(item)"
                @click="palette.execute(item)"
              >
                <span class="palette-item-label">{{ item.label }}</span>
                <span v-if="item.danger" class="palette-item-badge">危险</span>
              </button>
            </template>
          </div>
          <div class="palette-footer">↑↓ 选择 · Enter 执行 · Esc 关闭</div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.palette-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 12vh;
}

.palette-panel {
  width: 560px;
  max-width: calc(100vw - 32px);
  background: var(--el-bg-color);
  border-radius: 10px;
  box-shadow: var(--el-box-shadow-dark);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.palette-input {
  border: none;
  outline: none;
  padding: 14px 16px;
  font-size: 15px;
  background: transparent;
  color: var(--el-text-color-primary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.palette-list {
  max-height: 50vh;
  overflow-y: auto;
  padding: 6px 0;
}

.palette-group-title {
  padding: 8px 16px 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 16px;
  border: none;
  background: transparent;
  text-align: left;
  font-size: 14px;
  color: var(--el-text-color-primary);
  cursor: pointer;
}

.palette-item.is-active {
  background: var(--el-fill-color-light);
}

.palette-item.is-danger .palette-item-label {
  color: var(--el-color-danger);
}

.palette-item-badge {
  font-size: 11px;
  color: var(--el-color-danger);
  border: 1px solid var(--el-color-danger);
  border-radius: 4px;
  padding: 0 4px;
}

.palette-empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.palette-footer {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-top: 1px solid var(--el-border-color-lighter);
}

.palette-enter-active,
.palette-leave-active {
  transition: opacity 0.15s ease;
}

.palette-enter-from,
.palette-leave-to {
  opacity: 0;
}
</style>
