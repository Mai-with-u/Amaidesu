<template>
  <div class="archive-lines">
    <ul v-if="items.length > 0" class="line-list">
      <li v-for="line in items" :key="line.id" class="line-item">
        <div class="line-main">
          <div class="line-text">
            {{ line.text }}
            <el-tag v-if="line.game" size="small" type="info" effect="plain">{{
              gameSaveLabel(line)
            }}</el-tag>
            <template v-if="reviewable">
              <el-tag
                v-for="tag in archiveLineTags(line)"
                :key="tag.label"
                size="small"
                :type="tag.type"
                effect="plain"
                >{{ tag.label }}</el-tag
              >
            </template>
          </div>
          <blockquote class="line-quote">“{{ line.quote }}”</blockquote>
        </div>
        <div class="line-actions">
          <template v-if="reviewable">
            <el-button
              link
              size="small"
              :disabled="busyLineId === line.id"
              @click="emit('edit-title', line)"
              >改标题</el-button
            >
            <el-button
              v-if="line.in_snapshot"
              link
              size="small"
              :disabled="!canEditSnapshot || busyLineId === line.id"
              @click="emit('toggle-lasting', line)"
              >{{ line.lasting ? '取消稳定' : '标稳定' }}</el-button
            >
            <el-button
              v-else
              link
              size="small"
              type="primary"
              :disabled="!canEditSnapshot || busyLineId === line.id"
              @click="emit('pin', line)"
              >放回快照</el-button
            >
          </template>
          <el-button
            v-if="deletable"
            link
            size="small"
            type="danger"
            :loading="busyLineId === line.id"
            @click="emit('delete', line)"
            >删除</el-button
          >
        </div>
      </li>
    </ul>
    <p v-else class="empty-hint">{{ emptyText }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 场次档案行列表 —— 记忆页与场次复盘页共用的档案行展示
 *
 * 档案是"这一场她说过什么"的逐条记录（标题 + 原话）；游戏里的事标出游戏与存档，
 * 同一款游戏的不同存档是互不相干的世界，纠错时要看得出是哪个存档里的事。删除的二次确认与接口
 * 调用放在父组件，本组件只在 deletable 时渲染删除按钮并抛出 delete 意图，
 * 由父组件决定删完怎么刷新。
 *
 * reviewable 时（记忆页）再标出每行在不在最新一份快照里、稳定与否、人放回或改过，并抛出改标题、
 * 标稳定、放回快照三个意图：在快照里的行可以标或取消稳定，不在的行可以放回。还没有存下的快照时
 * （canEditSnapshot 为假）这两样做不了，按钮置灰。
 */
import type { ArchiveLineItem } from '@/types';
import { archiveLineTags, gameSaveLabel } from './ledgerView';

withDefaults(
  defineProps<{
    items: ArchiveLineItem[];
    /** 是否显示删除按钮（只读场景如场次复盘的历史回看可关掉） */
    deletable?: boolean;
    /** 记忆页审核：显示快照标签与改标题、标稳定、放回快照 */
    reviewable?: boolean;
    /** 有没有存下的最新快照（没有时标稳定、放回做不了） */
    canEditSnapshot?: boolean;
    /** 正在删除的行 id（只对那一行转圈，不让整张表闪） */
    busyLineId?: number | null;
    emptyText?: string;
  }>(),
  {
    deletable: false,
    reviewable: false,
    canEditSnapshot: false,
    busyLineId: null,
    emptyText: '这一场还没有档案行',
  },
);

const emit = defineEmits<{
  delete: [line: ArchiveLineItem];
  'edit-title': [line: ArchiveLineItem];
  'toggle-lasting': [line: ArchiveLineItem];
  pin: [line: ArchiveLineItem];
}>();
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

.line-actions {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
}

.line-text {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
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
