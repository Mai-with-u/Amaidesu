<script setup lang="ts">
import AppLayout from '@/components/layout/AppLayout.vue';
</script>

<template>
  <!-- 单 router-view：布局包在插槽内而非 v-if 双视图，避免布局分支切换时 patch 失序 -->
  <router-view v-slot="{ Component, route }">
    <AppLayout v-if="route.meta.layout !== false">
      <keep-alive>
        <component :is="Component" />
      </keep-alive>
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
