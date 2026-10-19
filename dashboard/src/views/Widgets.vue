<template>
  <div class="widgets-page">
    <aside class="list-panel" aria-label="小部件列表">
      <header class="list-header">
        <h2 class="list-title">小部件</h2>
        <span class="list-count">{{ widgets.length }} 个</span>
      </header>
      <div class="list-body">
        <button
          v-for="widget in widgets"
          :key="widget.key"
          type="button"
          class="widget-item"
          :class="{ selected: widget.key === selectedKey }"
          @click="selectedKey = widget.key"
        >
          <span class="widget-name">{{ widget.name }}</span>
          <span class="widget-status" :class="statusClassOf(widget.key)" />
        </button>
      </div>
    </aside>

    <section v-if="selected" class="detail-panel" aria-label="小部件详情">
      <header class="detail-header">
        <h3 class="detail-title">{{ selected.name }}</h3>
        <span class="status-line">{{ statusTextOf(selected.key) }}</span>
      </header>
      <p class="detail-desc">{{ selected.description }}</p>

      <dl class="detail-fields">
        <div class="field">
          <dt class="field-label">嵌入网址</dt>
          <dd class="field-value">
            <div class="url-row">
              <code class="path-code">{{ fullUrl }}</code>
              <button type="button" class="action-button" @click="copyEmbedUrl">复制</button>
            </div>
          </dd>
        </div>
        <div class="field">
          <dt class="field-label">推荐浏览器源尺寸</dt>
          <dd class="field-value">{{ selected.recommendedSize }}</dd>
        </div>
        <div class="field">
          <dt class="field-label">使用方式</dt>
          <dd class="field-value">
            在直播软件里添加「浏览器源」，粘贴上方网址，宽高按推荐尺寸设置。
            <a class="preview-link" :href="previewUrl" target="_blank" rel="noopener">打开预览</a>
            查看当前效果（预览态会显示排查提示，正式嵌入请使用复制的网址）。
          </dd>
        </div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';

import { configApi } from '@/api';
import { buildWidgetUrl, useWidgetCatalog } from '@/composables/useWidgetCatalog';

/**
 * 小部件管理页（/widgets）：左侧目录、右侧详情的常规控制台页面。
 *
 * 嵌入页本身是供浏览器源捕获的全屏透明页（/widget/<name>，不在壳内导航），
 * 本页承担它们的管理面：查看用途与推荐尺寸、复制嵌入网址、打开预览。
 * 启用状态读配置合并视图（差分立绘无开关，恒为已启用）；配置改动须重启
 * 生效，故 onActivated 时重读一次即可与最新落盘配置保持一致。无定时器
 * 与事件订阅，keep-alive 下仅有上述激活刷新这一额外语义。
 */

const { widgets } = useWidgetCatalog();
const selectedKey = ref(widgets[0]?.key ?? '');
const selected = computed(() => widgets.find(widget => widget.key === selectedKey.value));

/** 启用状态：缺键 = 尚未加载或读取失败，徽标显示未知 */
const enabledByKey = ref<Record<string, boolean | undefined>>({});

async function refreshStatus(): Promise<void> {
  try {
    const resp = await configApi.getConfig();
    const cfg = resp.data?.config ?? {};
    const infra = cfg['infra'];
    const dashboard =
      infra && typeof infra === 'object' ? (infra as Record<string, unknown>)['dashboard'] : undefined;
    enabledByKey.value = {
      danmaku: readEnabled(dashboard, 'danmaku_widget'),
      subtitle: readEnabled(dashboard, 'subtitle_widget'),
      game2048: readEnabled(dashboard, 'game2048_widget'),
      // 差分立绘无启用开关，路由常驻
      expression_sheet: true,
    };
  } catch (error) {
    // 状态读取失败不打断页面：徽标退化为未知，详情区仍可用
    enabledByKey.value = {};
    console.error('读取小部件启用状态失败', error);
  }
}

function readEnabled(section: unknown, key: string): boolean | undefined {
  if (!section || typeof section !== 'object') return undefined;
  const widget = (section as Record<string, unknown>)[key];
  if (!widget || typeof widget !== 'object') return undefined;
  const enabled = (widget as Record<string, unknown>).enabled;
  return typeof enabled === 'boolean' ? enabled : undefined;
}

onMounted(refreshStatus);
onActivated(refreshStatus);

const fullUrl = computed(() =>
  selected.value ? buildWidgetUrl(window.location.origin, selected.value.path) : '',
);
const previewUrl = computed(() =>
  selected.value ? buildWidgetUrl(window.location.origin, selected.value.path, { preview: true }) : '',
);

async function copyEmbedUrl(): Promise<void> {
  if (!fullUrl.value) return;
  try {
    await navigator.clipboard.writeText(fullUrl.value);
    ElMessage.success('嵌入网址已复制');
  } catch {
    ElMessage.error('复制失败，请手动复制网址');
  }
}

function statusTextOf(key: string): string {
  const enabled = enabledByKey.value[key];
  if (enabled === undefined) return '状态未知';
  return enabled ? '已启用' : '已停用（可在设置页开启，重启后生效）';
}

function statusClassOf(key: string): string {
  const enabled = enabledByKey.value[key];
  if (enabled === undefined) return 'status-unknown';
  return enabled ? 'status-on' : 'status-off';
}
</script>

<style scoped>
.widgets-page {
  display: flex;
  height: 100%;
  min-height: 0;
}

.list-panel {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border-color-light);
  background-color: var(--bg-sidebar);
}

.list-header {
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
}

.list-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.list-count {
  font-size: 12px;
  color: var(--text-placeholder);
}

.list-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-sm);
}

.widget-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 10px var(--spacing-md);
  margin-bottom: 2px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-regular);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}

.widget-item:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.widget-item.selected {
  background-color: var(--bg-active);
  color: var(--color-primary);
  font-weight: 500;
}

.widget-name {
  font-size: 14px;
}

.widget-status {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.detail-panel {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: var(--spacing-lg);
}

.detail-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-sm);
}

.detail-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.status-line {
  font-size: 12px;
  color: var(--text-regular);
}

.detail-desc {
  margin: 0 0 var(--spacing-lg);
  color: var(--text-regular);
  line-height: 1.6;
}

.detail-fields {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.field-label {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-bottom: 4px;
}

.field-value {
  margin: 0;
  color: var(--text-primary);
  line-height: 1.6;
}

.url-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.path-code {
  padding: 4px 8px;
  border-radius: var(--radius-md);
  background-color: var(--bg-hover);
  font-size: 13px;
  color: var(--color-primary);
  word-break: break-all;
}

.action-button {
  padding: 4px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-regular);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}

.action-button:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.preview-link {
  color: var(--color-primary);
  margin: 0 2px;
}

.status-on {
  background-color: var(--el-color-success, #67c23a);
}

.status-off {
  background-color: var(--el-color-danger, #f56c6c);
}

.status-unknown {
  background-color: var(--el-color-info-light-5, #a8abb2);
}
</style>
