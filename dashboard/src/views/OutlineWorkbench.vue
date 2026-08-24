<template>
  <div class="agenda-workbench">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">Agenda 工作台</h1>
        <p class="page-subtitle">直播环节运行态监控 · 推进控制 · AI 发言追踪</p>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="workbench-tabs">
      <!-- ===================== 运行 Tab ===================== -->
      <el-tab-pane label="运行" name="run">
        <div class="tab-body">
          <!-- v2 后端尚未暴露 agenda 端点，展示统一空态 -->
          <section class="card empty-state-card">
            <el-empty
              v-if="!loading && !loadError"
              description="Agenda 引擎尚未接入后端管理通道"
              :image-size="120"
            >
              <template #default>
                <p class="empty-hint">
                  v2 主播 Planner 的 Agenda 状态由 Agent 内部维护；
                  前端管理通道需在后续波次接入。当前展示占位空态。
                </p>
              </template>
            </el-empty>
            <el-alert
              v-else-if="loadError"
              :title="loadError"
              type="warning"
              show-icon
              :closable="false"
            />
            <div v-if="loading" class="loading-container">
              <el-icon class="is-loading" :size="32"><Loading /></el-icon>
              <p>正在查询 Agenda 状态...</p>
            </div>
          </section>
        </div>
      </el-tab-pane>

      <!-- ===================== 编辑 Tab ===================== -->
      <el-tab-pane label="编辑" name="edit">
        <div class="tab-body">
          <section class="card empty-state-card">
            <el-empty description="Agenda 编辑面板待 Agenda 引擎接口落地后启用" :image-size="120" />
          </section>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
/**
 * Agenda Workbench（v2.0 占位版）
 *
 * W8 证据：后端 `/api/v1/outline/*` 子路由已删除；agenda 端点（`/api/v1/agenda/*`）
 * 尚未实现。W8 plan 8 收官要求："Dashboard 后端 API 适配（前端依赖，必须先稳）：
 *   api/outline.py→agenda、api/proactive.py→Agent、删除 api/maibot.py、
 *   api/simulator.py→mock 控制面" —— 但当前 main.py 未注入这些新 manager，
 * 因此本前端只能展示空态。
 *
 * 当后端补齐 `/api/v1/agenda/state` / `/agenda/segments` / `/agenda/control` 后，
 * 此组件替换为完整 Agenda Workbench；当前保留 `OutlineEditorPanel` 引用在原 OutlineEditorPanel.vue
 * 组件内（已标注 deprecated，等待后端接口就绪）。
 */
import { ref } from 'vue';

const activeTab = ref<'run' | 'edit'>('run');
const loading = ref(false);
const loadError = ref<string | null>(
  '当前 Amaidesu v2 后端尚未暴露 /api/v1/agenda/* 端点；W8 收尾后将在后续波次接入。',
);
</script>

<style scoped>
.agenda-workbench {
  display: flex;
  flex-direction: column;
  max-width: 1180px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-sm);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}

.page-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 2px 0 0 0;
}

.tab-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  padding-bottom: var(--spacing-lg);
}

.card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
  box-shadow: var(--shadow-sm);
}

.empty-hint {
  margin: var(--spacing-md) auto 0;
  max-width: 480px;
  text-align: center;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-xl) 0;
  color: var(--text-secondary);
}
</style>
