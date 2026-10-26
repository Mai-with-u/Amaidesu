<template>
  <div class="detail-dialog-host">
    <el-dialog
      :model-value="visible"
      title="请求详情"
      width="min(1800px, 95%)"
      :close-on-click-modal="false"
      destroy-on-close
      @update:model-value="handleClose"
    >
      <div v-if="detail" class="detail-content">
        <!-- 基本信息：芯片行，一眼扫完不占阅读面积 -->
        <div class="info-chips">
          <span class="chip">
            <span class="chip-label">ID</span>
            <span class="mono">{{ detail.request_id }}</span>
          </span>
          <span class="chip">
            <span class="chip-label">时间</span>
            {{ formatDateTime(detail.timestamp_ms) }}
          </span>
          <span class="chip">
            <span class="chip-label">用途</span>
            {{ getProfileNameLabel(detail.profile_name) }}
          </span>
          <span class="chip">
            <span class="chip-label">模型</span>
            {{ detail.model_name }}
          </span>
          <span class="chip">
            <span class="chip-label">状态</span>
            <span :class="detail.success ? 'is-ok' : 'is-fail'">
              {{ detail.success ? '成功' : '失败' }}
            </span>
          </span>
          <span class="chip">
            <span class="chip-label">延迟</span>
            {{ formatLatency(detail.latency_ms) }}
          </span>
          <span v-if="detail.usage" class="chip">
            <span class="chip-label">Token</span>
            输入 {{ formatNumber(detail.usage.prompt_tokens) }} / 输出
            {{ formatNumber(detail.usage.completion_tokens) }} / 总计
            {{ formatNumber(detail.usage.total_tokens) }}
          </span>
          <span v-if="detail.reasoning_tokens != null" class="chip">
            <span class="chip-label">思考 token</span>
            {{ formatNumber(detail.reasoning_tokens) }}
          </span>
          <span v-if="cacheReported" class="chip">
            <span class="chip-label">缓存</span>
            命中 {{ formatNumber(detail.cache_hit_tokens) }} / 未中
            {{ formatNumber(detail.cache_miss_tokens) }}（{{ cacheRateText }}）
          </span>
          <span v-if="detail.cost !== undefined" class="chip">
            <span class="chip-label">费用</span>
            {{ formatCost(detail.cost) }}
          </span>
        </div>

        <!-- 内容页签：每页独占剩余满高、独立滚动 -->
        <el-tabs v-model="activeTab" class="detail-tabs">
          <el-tab-pane label="提示词" name="prompt">
            <div class="pane-scroll">
              <p
                v-if="!promptMessages.length && !promptSystem && !promptFallback"
                class="empty-hint"
              >
                请求正文未找到：正文存于日志诊断文件，可能已被清理。
              </p>
              <div v-else class="prompt-preview">
                <div v-if="promptSystem" class="message-card">
                  <div class="message-header">
                    <span class="prompt-role is-system">system</span>
                    <span class="message-meta header-tokens" :title="TOKEN_ESTIMATE_HINT">
                      ≈ {{ formatNumber(estimateTextTokens(promptSystem)) }}
                    </span>
                  </div>
                  <div class="prompt-message-body" v-html="renderMarkdown(promptSystem)"></div>
                </div>
                <div v-for="(m, i) in promptMessages" :key="i" class="message-card">
                  <div class="message-header">
                    <span class="prompt-role" :class="`is-${m.role}`">{{ m.role }}</span>
                    <span v-if="m.toolCallId" class="message-meta" title="对应请求的 tool_call_id"
                      >↩ {{ shortId(m.toolCallId) }}</span
                    >
                    <span class="message-meta header-tokens" :title="TOKEN_ESTIMATE_HINT">
                      ≈ {{ formatNumber(estimateMessageTokens(m)) }}
                    </span>
                  </div>
                  <!-- tool 返回是 JSON 字符串，用代码块美化；其余走 markdown -->
                  <pre
                    v-if="m.role === 'tool' && m.content"
                    class="tool-result"
                  ><code>{{ prettyMaybeJson(m.content) }}</code></pre>
                  <div
                    v-else-if="m.content"
                    class="prompt-message-body"
                    v-html="renderMarkdown(m.content)"
                  ></div>
                  <!-- 注入截图：默认只渲染占位符，点击才把该张渲染成 <img> -->
                  <div v-if="m.images.length" class="image-chip-list">
                    <template v-for="(img, j) in m.images" :key="`${i}:${j}`">
                      <img
                        v-if="isImageExpanded(i, j)"
                        class="image-preview"
                        :src="img.data"
                        alt="注入截图"
                        @click="toggleImage(i, j)"
                      />
                      <button
                        v-else
                        type="button"
                        class="image-chip"
                        :title="`约 ${formatImageSize(img.sizeBytes)}，点击查看`"
                        @click="toggleImage(i, j)"
                      >
                        🖼 截图{{ m.images.length > 1 ? ` #${j + 1}` : '' }}（{{
                          formatImageSize(img.sizeBytes)
                        }}，点击查看）
                      </button>
                    </template>
                  </div>
                  <div v-if="m.toolCalls.length" class="tool-calls-list">
                    <div v-for="tc in m.toolCalls" :key="tc.id ?? tc.name" class="tool-call-item">
                      <div class="tool-call-head">
                        <span class="tool-call-name">{{ tc.name }}</span>
                        <span class="message-meta" :title="tc.id">{{ shortId(tc.id) }}</span>
                      </div>
                      <pre
                        class="tool-call-args"
                      ><code>{{ formatArguments(tc.arguments) }}</code></pre>
                    </div>
                  </div>
                </div>
                <div v-if="promptFallback" class="message-card">
                  <div class="message-header">
                    <span class="prompt-role is-system">prompt</span>
                    <span class="message-meta header-tokens" :title="TOKEN_ESTIMATE_HINT">
                      ≈ {{ formatNumber(estimateTextTokens(promptFallback)) }}
                    </span>
                  </div>
                  <div class="prompt-message-body" v-html="renderMarkdown(promptFallback)"></div>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="detail.response_content" label="响应" name="response">
            <div class="pane-scroll">
              <div class="resp-block">{{ detail.response_content }}</div>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="detail.reasoning_content" label="推理链" name="reasoning">
            <div class="pane-scroll">
              <div class="resp-block">{{ detail.reasoning_content }}</div>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="detail.tool_calls?.length" label="工具调用" name="toolCalls">
            <div class="pane-scroll">
              <div class="pane-toolbar is-end">
                <el-icon class="copy-icon" title="复制 JSON" @click="copyJson(detail.tool_calls)">
                  <CopyDocument />
                </el-icon>
              </div>
              <div class="json-block">
                <VueJsonPretty :data="detail.tool_calls" theme="dark" show-line />
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="detail.usage_raw_json" label="用量单" name="usageRaw">
            <div class="pane-scroll">
              <div class="pane-toolbar is-end">
                <el-icon
                  class="copy-icon"
                  title="复制 JSON"
                  @click="copyJson(detail.usage_raw_json)"
                >
                  <CopyDocument />
                </el-icon>
              </div>
              <div v-if="parsedUsageRaw !== null" class="json-block">
                <VueJsonPretty :data="parsedUsageRaw" theme="dark" show-line />
              </div>
              <pre v-else class="code-block"><code>{{ detail.usage_raw_json }}</code></pre>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="detail.error" label="错误" name="error">
            <div class="pane-scroll">
              <div class="error-content">{{ detail.error }}</div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="参数" name="params">
            <div class="pane-scroll">
              <div class="pane-toolbar">
                <!-- messages 已在提示词页逐条展示，树中去重避免大请求双份 DOM -->
                <p v-if="messagesCount !== null" class="empty-hint">
                  messages（{{ messagesCount }} 条）已在「提示词」页逐条展示，此处不再重复渲染。
                </p>
                <span v-else></span>
                <el-icon
                  class="copy-icon"
                  title="复制 JSON（含 messages 的完整原始参数）"
                  @click="copyJson(detail.request_params)"
                >
                  <CopyDocument />
                </el-icon>
              </div>
              <div class="json-block">
                <!-- 默认全展开，仅 tools 字段可折叠（messages 剔除后 tools 是剩余的最大字段） -->
                <VueJsonPretty
                  :data="paramsTree"
                  :path-collapsible="node => node.path === 'root.tools'"
                  theme="dark"
                  show-line
                />
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>

      <template #footer>
        <div class="footer-nav">
          <div v-if="showNav" class="nav-buttons">
            <el-button :disabled="!hasPrev" @click="emit('prev')">上一条</el-button>
            <el-button :disabled="!hasNext" @click="emit('next')">下一条</el-button>
          </div>
          <el-button @click="handleClose(false)">关闭</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { ElMessage } from 'element-plus';
import { CopyDocument } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import {
  estimateMessageTokens,
  estimateTextTokens,
  normalizeMessage,
  type PreviewMessage,
} from '@/utils/llmMessage';
import type { LLMRequestHistory } from '@/types';
import {
  formatCost,
  formatDateTime,
  formatImageSize,
  formatLatency,
  formatNumber,
  getProfileNameLabel,
} from '@/utils/format';

interface Props {
  visible: boolean;
  detail: LLMRequestHistory | null;
  /** 当前行是否存在于列表页数据中；?request_id= 直达而列表无此行时隐藏导航按钮 */
  showNav: boolean;
  hasPrev: boolean;
  hasNext: boolean;
}

interface Emits {
  (e: 'update:visible', value: boolean): void;
  (e: 'prev'): void;
  (e: 'next'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

/** 消息卡片右上角 token 标注的口径说明（悬浮可见） */
const TOKEN_ESTIMATE_HINT =
  '估算值：供应商只回报整次请求的总用量，没有逐消息计数；' +
  '按中文约 1 字/token、其他约 4 字符/token 粗估（图像不计），用于定位体积大头';

// 展开的截图占位符（"消息序号:图序号"）；仅展开的单张渲染 <img>，换详情即清空
const expandedImages = ref(new Set<string>());

// 内容页签：有错误先看错误，其余落在提示词
const activeTab = ref('prompt');

watch(
  () => props.detail?.request_id,
  () => {
    expandedImages.value = new Set();
    activeTab.value = props.detail?.error ? 'error' : 'prompt';
  },
);

// 弹窗内 ←/→ 快捷翻阅；页签栏聚焦时方向键归 Element Plus 切页签，让位不抢
function handleKeydown(e: KeyboardEvent): void {
  if (!props.visible) return;
  const target = e.target as HTMLElement | null;
  if (
    target &&
    (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  ) {
    return;
  }
  if (target && target.closest('.el-tabs__header')) return;
  if (e.key === 'ArrowLeft' && props.hasPrev) {
    emit('prev');
  } else if (e.key === 'ArrowRight' && props.hasNext) {
    emit('next');
  }
}

onMounted(() => window.addEventListener('keydown', handleKeydown));
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown));

function imageKey(messageIndex: number, imageIndex: number): string {
  return `${messageIndex}:${imageIndex}`;
}

function isImageExpanded(messageIndex: number, imageIndex: number): boolean {
  return expandedImages.value.has(imageKey(messageIndex, imageIndex));
}

function toggleImage(messageIndex: number, imageIndex: number): void {
  const key = imageKey(messageIndex, imageIndex);
  const next = new Set(expandedImages.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expandedImages.value = next;
}

// 提示词消息按序完整渲染（含 assistant 的 tool_calls 与 tool 返回）
const promptMessages = computed<PreviewMessage[]>(() => {
  const messages = props.detail?.request_params?.messages;
  if (!Array.isArray(messages)) return [];
  return messages.map(normalizeMessage);
});

// 中立契约的 system 不在 messages 里，独立成键，置顶单独成卡
const promptSystem = computed<string>(() => {
  const system = props.detail?.request_params?.system;
  return typeof system === 'string' ? system : '';
});

// 非 messages 形态的请求（纯 prompt 字段）兜底展示
const promptFallback = computed(() => {
  const prompt = props.detail?.request_params?.prompt as string | undefined;
  return prompt || '';
});

// 请求体 messages 条数；无 messages 键时为 null（说明行不显示）
const messagesCount = computed<number | null>(() => {
  const messages = props.detail?.request_params?.messages;
  return Array.isArray(messages) ? messages.length : null;
});

// 参数树数据：剔除 messages 的浅拷贝，避免与提示词预览双份渲染大正文；
// 复制按钮仍复制完整原始 request_params
const paramsTree = computed<Record<string, unknown>>(() => {
  const params = props.detail?.request_params;
  if (!params) return {};
  if (messagesCount.value === null) return params;
  const rest: Record<string, unknown> = { ...params };
  delete rest.messages;
  return rest;
});

// 缓存命中：上游上报了（hit+miss>0）才展示，0/0 是"未上报"而非零命中
const cacheReported = computed(() => {
  const d = props.detail;
  if (!d) return false;
  return d.cache_hit_tokens + d.cache_miss_tokens > 0;
});

const cacheRateText = computed(() => {
  const d = props.detail;
  if (!d) return '—';
  const total = d.cache_hit_tokens + d.cache_miss_tokens;
  if (total <= 0) return '—';
  return `${((d.cache_hit_tokens / total) * 100).toFixed(1)}%`;
});

// 原始 usage 单：JSON 字符串解析成功则用 JSON 树展示，失败回退原文
type JsonValue = string | number | boolean | null | { [k: string]: JsonValue } | JsonValue[];

const parsedUsageRaw = computed<JsonValue | null>(() => {
  const raw = props.detail?.usage_raw_json;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as JsonValue;
  } catch {
    return null;
  }
});

// 长标识截短展示，完整值走悬浮提示
function shortId(id?: string): string {
  if (!id) return '';
  return id.length > 16 ? `${id.slice(0, 16)}…` : id;
}

// tool 返回/调用参数是 JSON 字符串：解析成功则美化，失败原样展示
function prettyMaybeJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function formatArguments(args?: string): string {
  return prettyMaybeJson(args || '{}');
}

// 复制区域 JSON 到剪贴板
async function copyJson(data: unknown): Promise<void> {
  try {
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    ElMessage.success('已复制到剪贴板');
  } catch (error) {
    console.error('复制 JSON 失败', error);
    ElMessage.error('复制失败');
  }
}

// Markdown 渲染（带 XSS 防护）：GFM 完整渲染，单换行视作换行以贴合 LLM 提示词排版
marked.setOptions({ gfm: true, breaks: true });

function renderMarkdown(text: string | null): string {
  if (!text) return '';
  const html = marked.parse(text);
  return DOMPurify.sanitize(typeof html === 'string' ? html : '');
}

function handleClose(value: boolean) {
  emit('update:visible', value);
}
</script>

<style scoped>
/* 满高应用式弹窗：高度定格视口占比，body 接管内部布局，footer 常驻可见。
   外层宿主节点供 :deep 穿透（el-dialog 默认不传送出组件子树） */
.detail-dialog-host :deep(.el-dialog) {
  height: 92vh;
  margin: 3vh auto;
  display: flex;
  flex-direction: column;
}

.detail-dialog-host :deep(.el-dialog__header),
.detail-dialog-host :deep(.el-dialog__footer) {
  flex: none;
}

.detail-dialog-host :deep(.el-dialog__body) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding-top: 10px;
}

.detail-content {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* 基本信息芯片行 */
.info-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 3px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  background: var(--bg-elevated);
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
}

.chip-label {
  flex: none;
  color: var(--text-secondary);
}

.is-ok {
  color: var(--color-success);
  font-weight: 600;
}

.is-fail {
  color: var(--color-danger);
  font-weight: 600;
}

/* 页签区：占满剩余高度，每页独立滚动 */
.detail-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.detail-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
}

.detail-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.pane-scroll {
  height: 100%;
  overflow-y: auto;
}

/* 页签内工具行：说明居左、复制按钮居右 */
.pane-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-sm);
}

.pane-toolbar.is-end {
  justify-content: flex-end;
}

.pane-toolbar .empty-hint {
  margin: 0;
}

/* muted 说明行：参数去重提示 / 正文缺失空态 */
.empty-hint {
  margin: 0 0 var(--spacing-sm) 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.copy-icon {
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 15px;
  transition: color 0.2s;
}

.copy-icon:hover {
  color: var(--color-primary);
}

/* 弹窗 footer：导航按钮居左、关闭居右 */
.footer-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.mono {
  font-size: 12px;
}

.code-block {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  margin: 0;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.code-block code {
  font-family: inherit;
}

/* 提示词预览样式（v-html 内容需 :deep 穿透 scoped） */
.prompt-preview {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
  line-height: 1.6;
}

/* 消息卡片：对话流逐条渲染 */
.message-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
}

.message-card + .message-card {
  margin-top: var(--spacing-sm);
}

.message-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-xs);
}

.message-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}

/* 卡片头右侧的 token 估算标注 */
.header-tokens {
  margin-left: auto;
  flex: none;
}

.prompt-role {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 1px 8px;
  border-radius: 4px;
  user-select: none;
}

.prompt-role.is-system {
  color: var(--color-planner);
  background: var(--color-planner-bg);
}

.prompt-role.is-user {
  color: var(--color-success);
  background: var(--color-success-bg);
}

.prompt-role.is-assistant {
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.prompt-role.is-tool {
  color: var(--color-warning);
  background: var(--color-warning-bg);
}

/* tool 返回内容：JSON 美化代码块 */
.tool-result {
  margin: 0;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

/* assistant 的工具调用子块 */
.tool-calls-list {
  margin-top: var(--spacing-xs);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

/* 注入截图占位符行：默认占位、点击展开单张 */
.image-chip-list {
  margin-top: var(--spacing-xs);
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--spacing-sm);
}

.image-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
  transition:
    color 0.2s,
    border-color 0.2s;
}

.image-chip:hover {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.image-preview {
  max-width: 360px;
  max-height: 240px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  cursor: zoom-out;
  display: block;
}

.tool-call-item {
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
}

.tool-call-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-xs);
}

.tool-call-name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
}

.tool-call-args {
  margin: 0;
  background: var(--bg-elevated);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 150px;
  overflow-y: auto;
}

.prompt-message-body {
  overflow-wrap: break-word;
}

.prompt-message-body :deep(:first-child) {
  margin-top: 0;
}

.prompt-message-body :deep(:last-child) {
  margin-bottom: 0;
}

/* 标题紧凑化，贴合预览场景 */
.prompt-message-body :deep(h1),
.prompt-message-body :deep(h2),
.prompt-message-body :deep(h3),
.prompt-message-body :deep(h4) {
  font-size: 14px;
  font-weight: 600;
  margin: var(--spacing-md) 0 var(--spacing-sm) 0;
  color: var(--text-primary);
  line-height: 1.4;
}

.prompt-message-body :deep(p) {
  margin: var(--spacing-sm) 0;
}

.prompt-message-body :deep(ul),
.prompt-message-body :deep(ol) {
  margin: var(--spacing-sm) 0;
  padding-left: var(--spacing-lg);
}

.prompt-message-body :deep(li) {
  margin: var(--spacing-xs) 0;
}

.prompt-message-body :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  margin: var(--spacing-sm) 0;
  padding-left: var(--spacing-md);
  color: var(--text-secondary);
}

.prompt-message-body :deep(strong) {
  color: var(--color-primary);
}

.prompt-message-body :deep(code) {
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12px;
}

.prompt-message-body :deep(pre) {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
  overflow-x: auto;
  margin: var(--spacing-sm) 0;
}

.prompt-message-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.prompt-message-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-color-light);
  margin: var(--spacing-md) 0;
}

.prompt-message-body :deep(table) {
  border-collapse: collapse;
  margin: var(--spacing-sm) 0;
}

.prompt-message-body :deep(th),
.prompt-message-body :deep(td) {
  border: 1px solid var(--border-color-light);
  padding: 4px 10px;
  font-size: 12px;
}

/* JSON 树容器（vue-json-pretty）：整页随 pane 滚动，不再单独限高 */
.json-block {
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
}

/* 响应 / 推理链正文 */
.resp-block {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.error-content {
  background: var(--color-danger-bg);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-danger);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
