<!--
  顶栏告警铃铛 + 收件箱抽屉。

  铃铛常驻顶栏，徽标显示未读数；点开抽屉翻看告警收件箱（最新在上），
  条目按级别染色，点击条目标记已读并跳到对应页面继续处理。
  订阅在 alerts store 内完成，本组件只读 store、不持订阅，
  无 keep-alive 生命周期语义需要声明。
-->
<template>
  <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99" class="alert-badge">
    <el-tooltip content="打开告警收件箱" placement="bottom">
      <el-button
        class="alert-bell"
        circle
        aria-label="打开告警收件箱"
        @click="drawerVisible = true"
      >
        <el-icon><Bell /></el-icon>
      </el-button>
    </el-tooltip>
  </el-badge>

  <el-drawer v-model="drawerVisible" title="告警收件箱" size="400px" class="alert-drawer">
    <template #header>
      <div class="drawer-header">
        <span class="drawer-title">告警收件箱</span>
        <el-button
          v-if="store.alerts.length > 0"
          text
          size="small"
          :disabled="store.unreadCount === 0"
          @click="store.markAllRead()"
        >
          全部已读
        </el-button>
      </div>
    </template>

    <div v-if="store.alerts.length === 0" class="empty-hint">目前没有待处理的告警</div>

    <ul v-else class="alert-list">
      <li v-for="item in store.alerts" :key="item.id">
        <button
          type="button"
          class="alert-item"
          :class="[item.level, { read: item.read }]"
          @click="openAlert(item)"
        >
          <span class="alert-main">
            <span class="alert-title">
              <el-badge v-if="!item.read" is-dot class="unread-dot" />
              {{ item.title }}
              <span v-if="item.count > 1" class="alert-count">×{{ item.count }}</span>
            </span>
            <span v-if="item.detail" class="alert-detail">{{ item.detail }}</span>
          </span>
          <span class="alert-meta">{{ relativeAge(item.ts_ms, now) }}</span>
        </button>
      </li>
    </ul>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { Bell } from '@element-plus/icons-vue';
import { storeToRefs } from 'pinia';
import { useAlertsStore, type AlertItem } from '@/stores/alerts';
import { relativeAge } from '@/utils/format';
import { useNowTick } from '@/composables/useNowTick';

const router = useRouter();
const store = useAlertsStore();
const { unreadCount } = storeToRefs(store);
const drawerVisible = ref(false);
// 共享 1 秒时钟：驱动条目的相对时间标签自动刷新
const now = useNowTick();

/** 点击条目：标记已读、跳到对应页面处理、收起抽屉 */
function openAlert(item: AlertItem): void {
  store.markRead(item.id);
  drawerVisible.value = false;
  void router.push(item.source_page);
}
</script>

<style scoped>
.alert-badge {
  display: inline-flex;
}

.alert-bell {
  border: 1px solid var(--border-color);
  background-color: var(--bg-elevated);
  color: var(--text-secondary);
}

.alert-bell:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background-color: var(--bg-active);
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-hint {
  padding: 32px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.alert-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.alert-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--spacing-sm);
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--border-color-light);
  border-radius: 8px;
  background-color: var(--bg-elevated);
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--transition-normal),
    border-color var(--transition-normal);
}

.alert-item:hover {
  background-color: var(--bg-active);
}

.alert-item.error {
  border-left-color: var(--color-danger);
}

.alert-item.warning {
  border-left-color: var(--color-warning, #e6a23c);
}

.alert-item.read {
  opacity: 0.65;
}

.alert-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.alert-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-all;
}

.unread-dot :deep(.el-badge__content) {
  background-color: var(--color-danger);
}

.alert-count {
  color: var(--text-secondary);
  font-weight: 400;
}

.alert-detail {
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-all;
}

.alert-meta {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
