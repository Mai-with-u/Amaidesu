<template>
  <div class="history-bar">
    <el-button size="small" :loading="loading" :disabled="!hasOlder" @click="emit('load-older')">
      <el-icon><ArrowUp /></el-icon>
      加载更早
    </el-button>
    <span v-if="!hasOlder" class="history-exhausted">{{ exhaustedText }}</span>
    <el-button
      v-if="windowActive && hasNewer"
      size="small"
      :loading="loading"
      @click="emit('load-newer')"
    >
      <el-icon><ArrowDown /></el-icon>
      加载更晚
    </el-button>

    <el-date-picker
      v-model="jumpTime"
      type="datetime"
      size="small"
      placeholder="跳转到时间…"
      format="YYYY-MM-DD HH:mm:ss"
      :clearable="false"
      :disabled="loading"
      class="history-jump-picker"
    />
    <el-button size="small" :disabled="!jumpTime || loading" @click="emitJump">跳转</el-button>

    <el-button v-if="windowActive" size="small" type="primary" plain @click="emit('exit')">
      <el-icon><RefreshLeft /></el-icon>
      回到实时
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ArrowDown, ArrowUp, RefreshLeft } from '@element-plus/icons-vue';

/**
 * 诊断页共用历史查询栏：加载更早 / 加载更晚 / 时间跳转 / 回到实时。
 *
 * 日志页与事件页的数据形态不同，本组件只管动作与状态展示，查询均由
 * 父页调各自 store 完成（jump 事件携带所选时刻的 Unix 毫秒）。
 */

defineProps<{
  /** 查询进行中（按钮转圈并禁用） */
  loading: boolean;
  /** 是否还有更早条目；false 时显示 exhaustedText */
  hasOlder: boolean;
  /** 是否有更晚条目（仅窗口浏览态显示"加载更晚"） */
  hasNewer: boolean;
  /** 是否处于历史浏览窗口（控制"加载更晚/回到实时"显隐） */
  windowActive: boolean;
  /** 更早耗尽时的提示文案（如"已到本次启动最早的记录"） */
  exhaustedText: string;
}>();

const emit = defineEmits<{
  (e: 'load-older'): void;
  (e: 'load-newer'): void;
  (e: 'jump', timestampMs: number): void;
  (e: 'exit'): void;
}>();

const jumpTime = ref<Date | null>(null);

function emitJump() {
  if (!jumpTime.value) return;
  emit('jump', jumpTime.value.getTime());
}
</script>

<style scoped>
.history-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.history-exhausted {
  font-size: 12px;
  color: var(--text-placeholder);
}

.history-jump-picker {
  width: 200px;
}
</style>
