<template>
  <section class="rv-panel" aria-label="场次档案">
    <div class="rv-panel-title rv-panel-head">
      <span>场次档案</span>
      <span class="rv-hint">整理自本场台词，下一场开播回忆“上一场”时用</span>
    </div>
    <p v-if="error" class="rv-line-error">{{ error }}</p>
    <template v-else>
      <p v-if="actionError" class="rv-line-error">{{ actionError }}</p>
      <div v-loading="loading" class="rv-lines">
        <ArchiveLineList
          :items="lines"
          deletable
          :busy-line-id="busyLineId"
          empty-text="这一场还没有档案行"
          @delete="emit('delete', $event)"
        />
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
/**
 * 场次档案面板（本场整理出的标题 + 原话，可删单行；删一行只影响下一场开播的
 * "上一场"回忆）。加载与删除状态由页面经组合式持有，本卡只负责展示与转发删除。
 * 单场语义（多选合并视图页面不传本卡数据）。
 */
import type { ArchiveLineItem } from '@/types';
import ArchiveLineList from '@/components/memory/ArchiveLineList.vue';

defineProps<{
  lines: ArchiveLineItem[];
  loading: boolean;
  /** 加载错误：非空时渲染错误、不渲染列表 */
  error: string;
  /** 删除单行的失败原因（加载错误由组合式管；这里只影响提示，不藏列表） */
  actionError: string;
  busyLineId: number | null;
}>();

const emit = defineEmits<{ delete: [line: ArchiveLineItem] }>();
</script>
