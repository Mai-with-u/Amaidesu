<template>
  <AppLayout>
    <router-view />
  </AppLayout>
</template>

<script setup lang="ts">
import AppLayout from '@/components/layout/AppLayout.vue';
</script>

<style>
#app {
  width: 100%;
  height: 100vh;
}
</style>
