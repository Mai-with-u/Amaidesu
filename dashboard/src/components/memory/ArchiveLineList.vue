<template>
  <div class="archive-lines">
    <ul v-if="items.length > 0" class="line-list">
      <li v-for="line in items" :key="line.id" class="line-item">
        <div class="line-main">
          <div class="line-text">{{ line.text }}</div>
          <blockquote class="line-quote">“{{ line.quote }}”</blockquote>
        </div>
        <el-button
          v-if="deletable"
          link
          size="small"
          type="danger"
          :loading="busyLineId === line.id"
          @click="emit('delete', line)"
          >删除</el-button
        >
      </li>
    </ul>
    <p v-else class="empty-hint">{{ emptyText }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 场次档案行列表 —— 记忆页与场次复盘页共用的档案行展示
 *
 * 档案是"这一场她说过什么"的逐条记录（标题 + 原话）。删除的二次确认与接口
 * 调用放在父组件，本组件只在 deletable 时渲染删除按钮并抛出 delete 意图，
 * 由父组件决定删完怎么刷新。
 */
import type { ArchiveLineItem } from '@/types';

withDefaults(
  defineProps<{
    items: ArchiveLineItem[];
    /** 是否显示删除按钮（只读场景如场次复盘的历史回看可关掉） */
    deletable?: boolean;
    /** 正在删除的行 id（只对那一行转圈，不让整张表闪） */
    busyLineId?: number | null;
    emptyText?: string;
  }>(),
  { deletable: false, busyLineId: null, emptyText: '这一场还没有档案行' },
);

const emit = defineEmits<{ delete: [line: ArchiveLineItem] }>();
</script>

<style scoped>
.line-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.line-item {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  padding: 8px 0;
  border-top: 1px dashed var(--border-color-light);
}

.line-item:first-child {
  border-top: none;
}

.line-main {
  flex: 1;
  min-width: 0;
}

.line-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.line-quote {
  margin: 4px 0 0;
  padding-left: var(--spacing-sm);
  border-left: 2px solid var(--border-color-light);
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
}

.empty-hint {
  margin: 0;
  padding: var(--spacing-sm);
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
