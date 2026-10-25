<script setup lang="ts">
import AppLayout from '@/components/layout/AppLayout.vue';
// 导演命令面板挂全局层：热键与浮层都不依赖具体页面（keep-alive 之外，切页不销毁）
import CommandPalette from '@/components/dashboard/CommandPalette.vue';
</script>

<template>
  <!-- 单 router-view：布局包在插槽内而非 v-if 双视图，避免布局分支切换时 patch 失序 -->
  <router-view v-slot="{ Component, route }">
    <AppLayout v-if="route.meta.layout !== false">
      <!-- 切页过渡：旧页淡出后新页淡入上浮；各视图均为单根节点，可直接套 Transition -->
      <transition name="page" mode="out-in">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </transition>
    </AppLayout>
    <component :is="Component" v-else />
  </router-view>
  <!-- 全局命令面板（Ctrl/Cmd+K），遮罩浮层形态，不进布局头部 -->
  <CommandPalette />
</template>

<style>
#app {
  width: 100%;
  height: 100vh;
}
</style>
