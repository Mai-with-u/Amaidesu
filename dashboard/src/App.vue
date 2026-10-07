<script setup lang="ts">
import AppLayout from '@/components/layout/AppLayout.vue';
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
</template>

<style>
#app {
  width: 100%;
  height: 100vh;
}
</style>
