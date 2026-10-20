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
              <a class="action-button" :href="fullUrl" target="_blank" rel="noopener" title="与 OBS 嵌入效果完全一致">
                打开正式
              </a>
              <a class="action-button" :href="previewUrl" target="_blank" rel="noopener" title="额外显示排查提示（如未配置占位）">
                打开预览
              </a>
            </div>
          </dd>
        </div>
        <div class="field">
          <dt class="field-label">推荐浏览器源尺寸</dt>
          <dd class="field-value">{{ selected.recommendedSize }}</dd>
        </div>
        <div v-if="formFields.length && formModel[selected.key]" class="field">
          <dt class="field-label">配置（保存后须重启生效）</dt>
          <dd class="field-value">
            <div class="form-grid">
              <div
                v-for="f in formFields"
                :key="f.key"
                class="form-row"
                :class="{ 'form-row-wide': f.type === 'text' }"
              >
                <label class="form-label" :title="f.label">{{ f.label }}</label>
                <component
                  :is="componentOf(f)"
                  :model-value="formModel[selected.key]?.[f.key]"
                  v-bind="controlAttrs(f)"
                  size="small"
                  class="form-control"
                  @update:model-value="(value: unknown) => setField(f.key, value)"
                />
              </div>
            </div>
            <div class="form-actions">
              <button
                type="button"
                class="action-button primary"
                :disabled="saving || !dirty"
                @click="saveWidgetConfig"
              >
                {{ saving ? '保存中…' : '保存配置' }}
              </button>
              <span v-if="dirty" class="dirty-hint">有未保存改动</span>
            </div>
          </dd>
        </div>
        <div v-else-if="selected.key === 'expression_sheet'" class="field">
          <dt class="field-label">配置</dt>
          <dd class="field-value">此小部件无管理页配置项；素材目录在设置页 avatar 域配置。</dd>
        </div>
        <div v-else-if="formFields.length" class="field">
          <dt class="field-label">配置</dt>
          <dd class="field-value">
            {{ configLoadFailed ? '配置读取失败' : '配置加载中…' }}
            <button v-if="configLoadFailed" type="button" class="action-button" @click="refreshConfig">
              重试
            </button>
          </dd>
        </div>
        <div class="field">
          <dt class="field-label">使用方式</dt>
          <dd class="field-value">
            在直播软件里添加「浏览器源」，粘贴上方网址，宽高按推荐尺寸设置。
            「打开正式」与 OBS 嵌入后的效果完全一致；「打开预览」会额外显示排查提示。
          </dd>
        </div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue';

import { computed, onActivated, onMounted, ref } from 'vue';
import { ElInput, ElInputNumber, ElMessage, ElSelect, ElSwitch } from 'element-plus';

import { configApi } from '@/api';
import { buildWidgetUrl, useWidgetCatalog } from '@/composables/useWidgetCatalog';
import { useAppRestartStore } from '@/stores/appRestart';
import { confirmAction } from '@/utils/confirmAction';
import { getApiErrorMessage } from '@/utils/apiError';

/**
 * 小部件管理页（/widgets）：左侧目录、右侧详情的常规控制台页面。
 *
 * 嵌入页本身是供浏览器源捕获的全屏透明页（/widget/<name>，不在壳内导航），
 * 本页承担它们的管理面：查看用途与推荐尺寸、复制嵌入网址、打开预览、
 * 调整配置。配置读改写都走既有配置接口（合并视图读取 + 批量原子写回）。
 * 保存以整段对象写回，热冷段以响应 requires_restart 为准：热段即时生效
 * 直接提示，冷段弹窗确认是否立即重启（重启遮罩复用全局 appRestart store，
 * 挂在 AppLayout 的唯一挂点上）。无长驻定时器与事件订阅；onActivated
 * 重读一次配置以跟随最新落盘值。
 */

const { widgets } = useWidgetCatalog();
const appRestart = useAppRestartStore();
const selectedKey = ref(widgets[0]?.key ?? '');
const selected = computed(() => widgets.find(widget => widget.key === selectedKey.value));

/** 启用状态：缺键 = 尚未加载或读取失败，徽标显示未知 */
const enabledByKey = ref<Record<string, boolean | undefined>>({});
const configLoadFailed = ref(false);

async function refreshConfig(): Promise<void> {
  try {
    const resp = await configApi.getConfig();
    const cfg = resp.data?.config ?? {};
    // 合并视图按根段名展平（无域名前缀）：infra 文件的 [dashboard] 段就是顶层 dashboard
    const dashboard = readSection(cfg, 'dashboard');
    enabledByKey.value = {
      danmaku: readEnabled(dashboard, 'danmaku_widget'),
      subtitle: readEnabled(dashboard, 'subtitle_widget'),
      game2048: readEnabled(dashboard, 'game2048_widget'),
      // 差分立绘无启用开关，路由常驻
      expression_sheet: true,
    };
    hydrateForms(dashboard);
    configLoadFailed.value = false;
  } catch (error) {
    // 读取失败不打断页面：徽标退化为未知、表单区提示重试
    enabledByKey.value = {};
    configLoadFailed.value = true;
    console.error('读取小部件配置失败', error);
  }
}

function readSection(source: unknown, key: string): Record<string, unknown> | undefined {
  if (!source || typeof source !== 'object') return undefined;
  const value = (source as Record<string, unknown>)[key];
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : undefined;
}

function readEnabled(dashboard: Record<string, unknown> | undefined, key: string): boolean | undefined {
  const widget = readSection(dashboard, key);
  const enabled = widget?.['enabled'];
  return typeof enabled === 'boolean' ? enabled : undefined;
}

onMounted(refreshConfig);
onActivated(refreshConfig);

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

// ---- 配置表单 ----

type FieldValue = boolean | number | string;

interface WidgetFieldDef {
  /** widget 配置段内的字段名 */
  key: string;
  label: string;
  type: 'switch' | 'number' | 'text' | 'select';
  min?: number;
  max?: number;
  step?: number;
  options?: { label: string; value: string }[];
  /** 配置缺项时的兜底值（对齐后端 Schema 默认值） */
  fallback: FieldValue;
}

const SECTION_BY_WIDGET: Record<string, string> = {
  danmaku: 'danmaku_widget',
  subtitle: 'subtitle_widget',
  game2048: 'game2048_widget',
};

const FIELD_DEFS: Record<string, readonly WidgetFieldDef[]> = {
  danmaku: [
    { key: 'enabled', label: '启用小部件', type: 'switch', fallback: true },
    { key: 'show_danmaku', label: '普通弹幕', type: 'switch', fallback: true },
    { key: 'show_gift', label: '礼物', type: 'switch', fallback: true },
    { key: 'show_super_chat', label: 'SuperChat', type: 'switch', fallback: true },
    { key: 'show_guard', label: '大航海', type: 'switch', fallback: true },
    { key: 'show_enter', label: '进场消息', type: 'switch', fallback: false },
    { key: 'show_reply', label: 'AI 回复', type: 'switch', fallback: true },
    { key: 'min_importance', label: '最小重要性', type: 'number', min: 0, max: 1, step: 0.05, fallback: 0 },
    { key: 'max_messages', label: '最大消息数', type: 'number', min: 5, max: 100, step: 1, fallback: 30 },
  ],
  subtitle: [
    { key: 'enabled', label: '启用小部件', type: 'switch', fallback: true },
    { key: 'font_size', label: '字体大小', type: 'number', min: 12, max: 72, step: 1, fallback: 32 },
    { key: 'font_color', label: '字体颜色', type: 'text', fallback: '#ffffff' },
    { key: 'background_color', label: '背景颜色', type: 'text', fallback: 'rgba(0,0,0,0.45)' },
    { key: 'border_color', label: '边框颜色', type: 'text', fallback: '#ff8800' },
    {
      key: 'position',
      label: '显示位置',
      type: 'select',
      options: [
        { label: '底部', value: 'bottom' },
        { label: '顶部', value: 'top' },
        { label: '居中', value: 'center' },
      ],
      fallback: 'bottom',
    },
    // auto_hide_after_ms 不上表单：嵌入页字幕跟随语音生命周期（说完才清），
    // 该键当前已无任何展示面消费，配置域的键清理另行处置
  ],
  game2048: [{ key: 'enabled', label: '启用小部件', type: 'switch', fallback: true }],
};

const formModel = ref<Record<string, Record<string, FieldValue>>>({});
/** 每个小部件加载/保存成功时的表单快照，用于脏判断 */
const snapshots = ref<Record<string, string>>({});
/** 合并视图里各配置段的原始对象——保存时整段写回（对象字段一等公民），保留表单外的字段 */
const viewSections = ref<Record<string, Record<string, unknown>>>({});
const saving = ref(false);

const formFields = computed(() => (selected.value ? FIELD_DEFS[selected.value.key] ?? [] : []));

const dirty = computed(() => {
  const key = selected.value?.key;
  if (!key || !snapshots.value[key] || !formModel.value[key]) return false;
  return JSON.stringify(formModel.value[key]) !== snapshots.value[key];
});

function hydrateForms(dashboard: Record<string, unknown> | undefined): void {
  for (const [widgetKey, fields] of Object.entries(FIELD_DEFS)) {
    const source = readSection(dashboard, SECTION_BY_WIDGET[widgetKey]);
    viewSections.value[widgetKey] = source ?? {};
    const model: Record<string, FieldValue> = {};
    for (const field of fields) {
      model[field.key] = coerceValue(field, source?.[field.key]);
    }
    formModel.value[widgetKey] = model;
    snapshots.value[widgetKey] = JSON.stringify(model);
  }
}

function coerceValue(field: WidgetFieldDef, raw: unknown): FieldValue {
  if (field.type === 'switch') return raw === true;
  if (field.type === 'number') return typeof raw === 'number' ? raw : field.fallback;
  return typeof raw === 'string' && raw !== '' ? raw : field.fallback;
}

function componentOf(field: WidgetFieldDef): Component {
  switch (field.type) {
    case 'switch':
      return ElSwitch;
    case 'number':
      return ElInputNumber;
    case 'select':
      return ElSelect;
    default:
      return ElInput;
  }
}

function controlAttrs(field: WidgetFieldDef): Record<string, unknown> {
  if (field.type === 'number') {
    return { min: field.min, max: field.max, step: field.step };
  }
  return {};
}

function setField(fieldKey: string, value: unknown): void {
  const key = selected.value?.key;
  const model = key ? formModel.value[key] : undefined;
  const field = formFields.value.find(f => f.key === fieldKey);
  if (!key || !model || !field) return;
  if (field.type === 'switch') {
    model[fieldKey] = value === true;
  } else if (field.type === 'number') {
    model[fieldKey] = typeof value === 'number' ? value : field.fallback;
  } else {
    model[fieldKey] = typeof value === 'string' ? value : '';
  }
}

async function saveWidgetConfig(): Promise<void> {
  const widget = selected.value;
  const model = widget ? formModel.value[widget.key] : undefined;
  const section = widget ? SECTION_BY_WIDGET[widget.key] : undefined;
  if (!widget || !model || !section) return;
  saving.value = true;
  try {
    const changes = [
      {
        // 整段对象写回（键带域名前缀，schema/batch 的键形态）：表单外字段原样保留
        key: `infra.dashboard.${section}`,
        value: { ...(viewSections.value[widget.key] ?? {}), ...model },
      },
    ];
    const resp = await configApi.saveBatch(changes);
    if (!resp.data.success) {
      ElMessage.error(resp.data.message || '保存失败');
      return;
    }
    snapshots.value[widget.key] = JSON.stringify(model);
    if (resp.data.requires_restart) {
      const restartNow = await confirmAction('已保存，重启后生效。立即重启服务吗？', '保存成功', {
        confirmButtonText: '立即重启',
      });
      if (restartNow) void appRestart.beginRestart();
    } else {
      ElMessage.success('已保存');
    }
  } catch (e) {
    ElMessage.error(getApiErrorMessage(e, '保存配置失败'));
  } finally {
    saving.value = false;
  }
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
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-regular);
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}

.action-button:hover {
  background-color: var(--bg-hover);
  color: var(--text-primary);
}

.action-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-button.primary {
  background-color: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px var(--spacing-lg);
  margin-bottom: var(--spacing-md);
  max-width: 720px;
}

.form-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
  min-height: 28px;
}

.form-row-wide {
  grid-column: 1 / -1;
}

.form-label {
  font-size: 13px;
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-control {
  flex-shrink: 0;
}

.form-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.dirty-hint {
  font-size: 12px;
  color: var(--el-color-warning, #e6a23c);
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
