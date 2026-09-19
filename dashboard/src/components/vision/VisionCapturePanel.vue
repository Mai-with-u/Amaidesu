<!--
视觉捕获配置面板：
- 显示器下拉（单选，mss 枚举）
- 预览区（后端叠框，拖框反向回写显示器相对坐标）
- 保存 [tools.vision].config.monitor_index + default_region

独立组件（不走 ConfigFieldType / x-ui-type；schema 渲染不识别自定义控件）。
-->

<template>
  <section class="vision-capture-panel" aria-label="视觉捕获区域">
    <header class="panel-head">
      <div class="panel-title-block">
        <h3 class="panel-title">视觉捕获区域</h3>
        <p class="panel-desc">
          选择显示器并在预览图上拖框，区域将作为
          <code class="mono">look_at_screen</code>
          工具的默认捕获范围写入 <code class="mono">[tools.vision].config</code>。
        </p>
      </div>
    </header>

    <div class="panel-body">
      <!-- 左侧：预览与拖框 -->
      <div class="preview-area">
        <div
          ref="previewWrap"
          class="preview-wrap"
          :class="{ 'is-dragging': isDragging }"
          @mousedown="onDragStart"
          @mousemove="onDragMove"
          @mouseup="onDragEnd"
          @mouseleave="onDragEnd"
        >
          <img
            v-if="previewSrc"
            ref="previewImg"
            class="preview-img"
            :src="previewSrc"
            alt="屏幕预览"
            draggable="false"
            data-testid="vision-preview-img"
          />
          <div v-else class="preview-placeholder">
            <span v-if="previewLoading">加载中...</span>
            <span v-else-if="previewError" class="preview-error">{{ previewError }}</span>
            <span v-else>暂无预览</span>
          </div>

          <!-- 拖框叠加层（绝对定位在图上） -->
          <div
            v-if="dragRectPx"
            class="drag-rect"
            data-testid="vision-drag-rect"
            :style="{
              left: dragRectPx.left + 'px',
              top: dragRectPx.top + 'px',
              width: dragRectPx.width + 'px',
              height: dragRectPx.height + 'px',
            }"
          />
        </div>
        <p class="preview-hint">
          在预览图上按下并拖动鼠标以框选区域。释放后坐标自动换算为显示器像素并回填。
        </p>
      </div>

      <!-- 右侧：控件 -->
      <div class="control-area">
        <div class="control-row">
          <label class="control-label">显示器</label>
          <el-select
            v-model="monitorIndex"
            placeholder="选择显示器"
            class="control-select"
            :loading="monitorsLoading"
            :disabled="monitorsLoading || monitors.length === 0"
            data-testid="vision-monitor-select"
            @change="onMonitorChange"
          >
            <el-option
              v-for="m in physicalMonitors"
              :key="m.index"
              :label="formatMonitorLabel(m)"
              :value="m.index"
            />
          </el-select>
          <el-button size="small" :loading="monitorsLoading" @click="loadMonitors">
            刷新显示器
          </el-button>
        </div>

        <div class="control-row">
          <label class="control-label">分辨率</label>
          <span class="control-value mono">
            {{ currentMonitor ? `${currentMonitor.width} × ${currentMonitor.height}` : '—' }}
          </span>
        </div>

        <div class="control-row">
          <label class="control-label">区域 (x1, y1, x2, y2)</label>
          <div class="region-inputs">
            <el-input-number
              v-model="regionInput[0]"
              :min="0"
              :max="100000"
              :step="1"
              size="small"
              controls-position="right"
              data-testid="vision-region-x1"
            />
            <el-input-number
              v-model="regionInput[1]"
              :min="0"
              :max="100000"
              :step="1"
              size="small"
              controls-position="right"
              data-testid="vision-region-y1"
            />
            <el-input-number
              v-model="regionInput[2]"
              :min="0"
              :max="100000"
              :step="1"
              size="small"
              controls-position="right"
              data-testid="vision-region-x2"
            />
            <el-input-number
              v-model="regionInput[3]"
              :min="0"
              :max="100000"
              :step="1"
              size="small"
              controls-position="right"
              data-testid="vision-region-y2"
            />
          </div>
        </div>

        <div class="control-actions">
          <el-button @click="clearRegion" data-testid="vision-clear-region"> 清空区域 </el-button>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="!hasPendingChange"
            data-testid="vision-save"
            @click="saveConfig"
          >
            保存
          </el-button>
        </div>

        <div class="status-line">
          <span v-if="lastSavedAt" class="status-ok"> 已保存于 {{ formatTime(lastSavedAt) }} </span>
          <span v-else-if="hasPendingChange" class="status-pending">有未保存的变更</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { visionApi } from '@/api';
import type { VisionMonitor } from '@/types';
import api from '@/api';

interface ConfigBatchChange {
  key: string;
  value: unknown;
}

interface ConfigBatchUpdateResponse {
  success: boolean;
  message: string;
  requires_restart?: boolean;
}

// 显示器状态

const monitors = ref<VisionMonitor[]>([]);
const monitorsLoading = ref(false);
const monitorIndex = ref<number>(1);

// 只列出物理显示器（index >= 1）；虚拟合屏 monitors[0] 不参与选择
const physicalMonitors = computed(() => monitors.value.filter(m => m.index >= 1));

const currentMonitor = computed(
  () => physicalMonitors.value.find(m => m.index === monitorIndex.value) ?? null,
);

function formatMonitorLabel(m: VisionMonitor): string {
  const tag = m.is_primary ? '（主屏）' : '';
  return `显示器 ${m.index} · ${m.width} × ${m.height}${tag}`;
}

// 预览状态

const previewImg = ref<HTMLImageElement | null>(null);
const previewWrap = ref<HTMLDivElement | null>(null);
const previewSrc = ref<string>('');
const previewLoading = ref(false);
const previewError = ref<string | null>(null);
const previewSize = ref<{ width: number; height: number } | null>(null);

const dragRectPx = ref<{ left: number; top: number; width: number; height: number } | null>(null);
const isDragging = ref(false);
const dragStart = ref<{ x: number; y: number } | null>(null);

// 区域状态

// 显示器相对坐标 [x1, y1, x2, y2]；null = 全屏（无区域）
const regionMonitor = ref<[number, number, number, number] | null>(null);

// 输入框双向绑定的中间值（el-input-number 不接受 null，做空值转 0 处理）
const regionInput = ref<[number, number, number, number]>([0, 0, 0, 0]);

function syncRegionInput() {
  if (regionMonitor.value) {
    regionInput.value = [...regionMonitor.value] as [number, number, number, number];
  } else {
    regionInput.value = [0, 0, 0, 0];
  }
}

watch(regionMonitor, syncRegionInput, { immediate: true });

// 把输入框写回 regionMonitor（用户手工改输入框时同步）
watch(
  regionInput,
  next => {
    if (!regionMonitor.value) return;
    const [x1, y1, x2, y2] = next;
    if (x1 === 0 && y1 === 0 && x2 === 0 && y2 === 0) {
      regionMonitor.value = null;
      return;
    }
    regionMonitor.value = [x1, y1, x2, y2];
  },
  { deep: true },
);

// 保存状态

const initialMonitorIndex = ref<number | null>(null);
const initialRegion = ref<[number, number, number, number] | null>(null);
const saving = ref(false);
const lastSavedAt = ref<number | null>(null);

const hasPendingChange = computed(() => {
  if (initialMonitorIndex.value === null) return true; // 尚未加载初值时也允许保存
  if (monitorIndex.value !== initialMonitorIndex.value) return true;
  const cur = regionMonitor.value;
  const init = initialRegion.value;
  if (cur === null && init === null) return false;
  if (cur === null || init === null) return true;
  return cur.some((v, i) => v !== init[i]);
});

// 配置键（与后端约定一致：scope + 文件内点分路径）

const KEY_MONITOR = 'tools.tools.vision.config.monitor_index';
const KEY_REGION = 'tools.tools.vision.config.default_region';

// 加载显示器

async function loadMonitors() {
  monitorsLoading.value = true;
  try {
    const resp = await visionApi.listMonitors();
    const data = resp.data;
    monitors.value = data.monitors ?? [];
    // 若当前 monitorIndex 不在新列表里，回退到首个物理显示器
    if (!physicalMonitors.value.some(m => m.index === monitorIndex.value)) {
      const fallback = physicalMonitors.value[0]?.index ?? 1;
      monitorIndex.value = fallback;
    }
  } catch (e) {
    monitors.value = [];
    ElMessage.error(`获取显示器列表失败：${extractError(e)}`);
  } finally {
    monitorsLoading.value = false;
  }
}

// 加载初始配置

async function loadInitialConfig() {
  try {
    const resp = await api.get<{ config: Record<string, unknown> }>('/config');
    const cfg = resp.data?.config ?? {};
    // 取 tools.vision.config.*
    const visionCfg = getNested(cfg, ['tools', 'vision', 'config']) as
      | Record<string, unknown>
      | undefined;
    if (visionCfg) {
      const m = visionCfg.monitor_index;
      if (typeof m === 'number' && m > 0) {
        monitorIndex.value = m;
        initialMonitorIndex.value = m;
      } else {
        initialMonitorIndex.value = monitorIndex.value;
      }
      const r = visionCfg.default_region;
      if (Array.isArray(r) && r.length === 4 && r.every(v => typeof v === 'number')) {
        const region: [number, number, number, number] = [
          Math.max(0, Math.floor(r[0])),
          Math.max(0, Math.floor(r[1])),
          Math.max(0, Math.floor(r[2])),
          Math.max(0, Math.floor(r[3])),
        ];
        regionMonitor.value = region;
        initialRegion.value = region;
      } else {
        initialRegion.value = null;
      }
    } else {
      initialMonitorIndex.value = monitorIndex.value;
    }
  } catch (e) {
    // 配置加载失败不阻塞显示器/预览流程
    initialMonitorIndex.value = monitorIndex.value;
    console.warn('加载视觉初始配置失败', e);
  }
}

// 加载预览

let previewSeq = 0;

async function loadPreview() {
  const mySeq = ++previewSeq;
  previewLoading.value = true;
  previewError.value = null;
  try {
    const region = regionMonitor.value;
    const params: { monitor_index: number; region?: string; max_width?: number } = {
      monitor_index: monitorIndex.value,
    };
    if (region) {
      params.region = region.join(',');
    }
    // 不传 max_width：让后端返回原图，便于拖框坐标精确换算
    const resp = await visionApi.preview(params);
    if (mySeq !== previewSeq) return; // 已被更新的请求覆盖
    const data = resp.data;
    previewSrc.value = `data:image/png;base64,${data.image_b64}`;
    previewSize.value = { width: data.width, height: data.height };
  } catch (e) {
    if (mySeq !== previewSeq) return;
    previewSrc.value = '';
    previewSize.value = null;
    previewError.value = extractError(e);
  } finally {
    if (mySeq === previewSeq) previewLoading.value = false;
  }
}

// 显示器 / 区域变化时刷新预览（节流）

let previewTimer: number | null = null;
function schedulePreviewRefresh() {
  if (previewTimer !== null) window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => {
    previewTimer = null;
    void loadPreview();
  }, 250);
}

function onMonitorChange() {
  // 显示器切换时清掉旧 region（不同显示器尺寸/坐标不一样）
  regionMonitor.value = null;
  dragRectPx.value = null;
  schedulePreviewRefresh();
}

watch(regionMonitor, () => {
  schedulePreviewRefresh();
});

// 拖框坐标换算

/**
 * 鼠标 client 坐标 → 图内 CSS 像素坐标。
 * 限定在图片框内；越界裁剪。
 */
function clientToImgCss(clientX: number, clientY: number): { x: number; y: number } | null {
  const el = previewImg.value;
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  const y = Math.max(0, Math.min(rect.height, clientY - rect.top));
  return { x, y };
}

/**
 * 图内 CSS 像素坐标 → 显示器原始像素坐标（相对显示器左上角）。
 * 用图自然宽高（后端返回的 width/height）做比例换算；
 * 若图片被 CSS 缩放显示，比例仍正确。
 */
function imgCssToMonitor(cssX: number, cssY: number): { x: number; y: number } {
  const el = previewImg.value;
  const rect = el?.getBoundingClientRect();
  const size = previewSize.value;
  if (!el || !rect || !size || rect.width === 0 || rect.height === 0) {
    return { x: Math.round(cssX), y: Math.round(cssY) };
  }
  const ratioX = size.width / rect.width;
  const ratioY = size.height / rect.height;
  return {
    x: Math.round(cssX * ratioX),
    y: Math.round(cssY * ratioY),
  };
}

function onDragStart(ev: MouseEvent) {
  if (!previewImg.value) return;
  const pt = clientToImgCss(ev.clientX, ev.clientY);
  if (!pt) return;
  isDragging.value = true;
  dragStart.value = pt;
  dragRectPx.value = { left: pt.x, top: pt.y, width: 0, height: 0 };
}

function onDragMove(ev: MouseEvent) {
  if (!isDragging.value || !dragStart.value) return;
  const pt = clientToImgCss(ev.clientX, ev.clientY);
  if (!pt) return;
  const start = dragStart.value;
  const left = Math.min(start.x, pt.x);
  const top = Math.min(start.y, pt.y);
  const width = Math.abs(pt.x - start.x);
  const height = Math.abs(pt.y - start.y);
  dragRectPx.value = { left, top, width, height };
}

function onDragEnd() {
  if (!isDragging.value) return;
  isDragging.value = false;
  const rect = dragRectPx.value;
  dragStart.value = null;
  if (!rect || rect.width < 3 || rect.height < 3) {
    // 误触（拖动距离过小）→ 不更新 region
    dragRectPx.value = null;
    return;
  }
  // 显示器原始像素坐标（监视器坐标系）
  const tl = imgCssToMonitor(rect.left, rect.top);
  const br = imgCssToMonitor(rect.left + rect.width, rect.top + rect.height);
  regionMonitor.value = [
    Math.max(0, tl.x),
    Math.max(0, tl.y),
    Math.max(0, br.x),
    Math.max(0, br.y),
  ];
  // 保留 overlay 显示至下一次刷新
}

// 保存

async function saveConfig() {
  if (saving.value) return;
  saving.value = true;
  try {
    const changes: ConfigBatchChange[] = [
      { key: KEY_MONITOR, value: monitorIndex.value },
      { key: KEY_REGION, value: regionMonitor.value ?? null },
    ];
    const resp = await api.post<ConfigBatchUpdateResponse>('/config/batch', { changes });
    const data = resp.data;
    if (data.success) {
      initialMonitorIndex.value = monitorIndex.value;
      initialRegion.value = regionMonitor.value ? [...regionMonitor.value] : null;
      lastSavedAt.value = Date.now();
      ElMessage.success(data.message || '已保存');
    } else {
      const detail = data.message || '保存失败';
      ElMessage.error(detail);
    }
  } catch (e) {
    ElMessage.error(`保存失败：${extractError(e)}`);
  } finally {
    saving.value = false;
  }
}

function clearRegion() {
  regionMonitor.value = null;
  dragRectPx.value = null;
}

// 工具

function extractError(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string; message?: string } }; message?: string };
  return ax?.response?.data?.detail ?? ax?.response?.data?.message ?? ax?.message ?? '未知错误';
}

function getNested(obj: Record<string, unknown>, path: string[]): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur && typeof cur === 'object' && k in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return cur;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// 生命周期

onMounted(async () => {
  // 并行启动：显示器枚举 + 配置加载
  await Promise.all([loadMonitors(), loadInitialConfig()]);
  await loadPreview();
});

onBeforeUnmount(() => {
  if (previewTimer !== null) window.clearTimeout(previewTimer);
});
</script>

<style scoped>
.vision-capture-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: var(--text-primary);
}

.panel-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

.panel-body {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: var(--spacing-lg);
  align-items: flex-start;
}

.preview-area {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.preview-wrap {
  position: relative;
  width: 100%;
  min-height: 240px;
  max-height: 480px;
  background: #0d1117;
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: crosshair;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-wrap.is-dragging {
  cursor: grabbing;
}

.preview-img {
  max-width: 100%;
  max-height: 480px;
  display: block;
  object-fit: contain;
  pointer-events: none;
}

.preview-placeholder {
  color: var(--text-tertiary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.preview-error {
  color: var(--el-color-danger);
}

.drag-rect {
  position: absolute;
  border: 2px solid #ff4d4f;
  background: rgba(255, 77, 79, 0.12);
  pointer-events: none;
}

.preview-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin: 0;
}

.control-area {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.control-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.control-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.control-select {
  width: 100%;
}

.control-value {
  font-size: 13px;
  color: var(--text-primary);
}

.region-inputs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
}

.region-inputs :deep(.el-input-number) {
  width: 100%;
}

.control-actions {
  display: flex;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-xs);
}

.status-line {
  font-size: 12px;
  color: var(--text-tertiary);
  min-height: 18px;
}

.status-ok {
  color: var(--el-color-success);
}

.status-pending {
  color: var(--el-color-warning);
}

@media (max-width: 960px) {
  .panel-body {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
