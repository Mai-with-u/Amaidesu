<script setup lang="ts">
import { onMounted, ref } from 'vue';
import AppLayout from '@/components/layout/AppLayout.vue';
// 导演命令面板挂全局层：热键与浮层都不依赖具体页面（keep-alive 之外，切页不销毁）
import CommandPalette from '@/components/dashboard/CommandPalette.vue';
// 断线续播弹窗挂全局层：上次进程崩溃留下的未结账场次，用户落在任何页面都要能拍板
import ResidualSessionDialog from '@/components/live/ResidualSessionDialog.vue';
import { liveSessionsApi } from '@/api';

const residualDialogVisible = ref(false);

onMounted(async () => {
  // 启动探测待决残留场次：有就弹续播弹窗；查询失败静默（场次列表页仍可处置）
  try {
    const response = await liveSessionsApi.listResidual();
    if (response.data.items.length > 0) {
      residualDialogVisible.value = true;
    }
  } catch {
    // 后端未就绪或会话管理器未装配时不弹窗，不打扰正常使用
  }
});
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
  <!-- 断线续播弹窗：待决残留场次有就弹，用户拍板续播或收尾 -->
  <ResidualSessionDialog v-model="residualDialogVisible" />
</template>

<style>
#app {
  width: 100%;
  height: 100vh;
}
</style>
