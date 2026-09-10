<template>
  <el-dialog
    :model-value="visible"
    title="请求详情"
    width="min(1800px, 95%)"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="handleClose"
  >
    <div v-if="detail" class="detail-content">
      <!-- 基本信息 -->
      <el-descriptions :column="2" border class="detail-section">
        <el-descriptions-item label="请求 ID">
          <span class="mono">{{ detail.request_id }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="时间">
          {{ formatDateTime(detail.timestamp) }}
        </el-descriptions-item>
        <el-descriptions-item label="客户端类型">
          <el-tag size="small" effect="plain">
            {{ getClientTypeLabel(detail.client_type) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="模型">
          {{ detail.model_name }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="detail.success ? 'success' : 'danger'" size="small">
            {{ detail.success ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="延迟">
          {{ formatLatency(detail.latency_ms) }}
        </el-descriptions-item>
        <el-descriptions-item v-if="detail.usage" label="Token 用量">
          Prompt: {{ detail.usage.prompt_tokens }} / Completion:
          {{ detail.usage.completion_tokens }} / 总计:
          {{ detail.usage.total_tokens }}
        </el-descriptions-item>
        <el-descriptions-item v-if="detail.cost !== undefined" label="费用">
          ¥{{ detail.cost.toFixed(6) }}
        </el-descriptions-item>
      </el-descriptions>

      <!-- 提示词预览：messages 按序卡片化渲染完整对话流 -->
      <div v-if="promptMessages.length || promptFallback" class="detail-section">
        <h4 class="section-title">提示词预览</h4>
        <div class="prompt-preview">
          <div v-for="(m, i) in promptMessages" :key="i" class="message-card">
            <div class="message-header">
              <span class="prompt-role" :class="`is-${m.role}`">{{ m.role }}</span>
              <span v-if="m.tool_call_id" class="message-meta" title="对应请求的 tool_call_id"
                >↩ {{ shortId(m.tool_call_id) }}</span
              >
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
            <div v-if="m.tool_calls?.length" class="tool-calls-list">
              <div v-for="tc in m.tool_calls" :key="tc.id" class="tool-call-item">
                <div class="tool-call-head">
                  <span class="tool-call-name">{{ tc.function?.name || 'unknown' }}</span>
                  <span class="message-meta" :title="tc.id">{{ shortId(tc.id) }}</span>
                </div>
                <pre
                  class="tool-call-args"
                ><code>{{ formatArguments(tc.function?.arguments) }}</code></pre>
              </div>
            </div>
          </div>
          <div v-if="promptFallback" class="message-card">
            <div class="message-header">
              <span class="prompt-role is-system">prompt</span>
            </div>
            <div class="prompt-message-body" v-html="renderMarkdown(promptFallback)"></div>
          </div>
        </div>
      </div>

      <!-- 响应内容 -->
      <div v-if="detail.response_content" class="detail-section">
        <h4 class="section-title">响应内容</h4>
        <div class="response-content">
          {{ detail.response_content }}
        </div>
      </div>

      <!-- 推理链 -->
      <div v-if="detail.reasoning_content" class="detail-section">
        <h4 class="section-title">推理链</h4>
        <div class="reasoning-content">
          {{ detail.reasoning_content }}
        </div>
      </div>

      <!-- 工具调用 -->
      <div v-if="detail.tool_calls?.length" class="detail-section">
        <div class="section-header">
          <h4 class="section-title">工具调用</h4>
          <el-icon class="copy-icon" title="复制 JSON" @click="copyJson(detail.tool_calls)">
            <CopyDocument />
          </el-icon>
        </div>
        <div class="tool-calls-block">
          <VueJsonPretty :data="detail.tool_calls" theme="dark" show-line />
        </div>
      </div>

      <!-- 错误信息 -->
      <div v-if="detail.error" class="detail-section">
        <h4 class="section-title error-title">错误信息</h4>
        <div class="error-content">
          {{ detail.error }}
        </div>
      </div>

      <!-- 请求参数（低频查看，置于末尾；可折叠 JSON 树） -->
      <div class="detail-section">
        <div class="section-header">
          <h4 class="section-title">请求参数</h4>
          <el-icon class="copy-icon" title="复制 JSON" @click="copyJson(detail.request_params)">
            <CopyDocument />
          </el-icon>
        </div>
        <div class="params-block">
          <!-- 默认全展开，仅 tools 字段强制折叠 -->
          <VueJsonPretty
            :data="detail.request_params"
            :path-collapsible="node => node.path === 'root.tools'"
            theme="dark"
            show-line
          />
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="handleClose(false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { ElMessage } from 'element-plus';
import { CopyDocument } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import type { LLMRequestHistory } from '@/types';

interface Props {
  visible: boolean;
  detail: LLMRequestHistory | null;
}

interface Emits {
  (e: 'update:visible', value: boolean): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

interface ToolCall {
  id?: string;
  function?: { name?: string; arguments?: string };
}

interface PromptMessage {
  role: string;
  content?: string;
  tool_call_id?: string;
  tool_calls?: ToolCall[];
}

// 提示词消息按序完整渲染（含 assistant 的 tool_calls 与 tool 返回），content 为原始 markdown
const promptMessages = computed<PromptMessage[]>(() => {
  const messages = props.detail?.request_params?.messages as
    | Array<{ role?: string; content?: unknown; tool_call_id?: string; tool_calls?: ToolCall[] }>
    | undefined;

  if (messages && Array.isArray(messages)) {
    return messages.map(m => ({
      role: m.role || 'unknown',
      // content 理论上为字符串，非字符串形态（多模态 parts 等）退化为 JSON 文本
      content:
        typeof m.content === 'string'
          ? m.content
          : m.content
            ? JSON.stringify(m.content, null, 2)
            : '',
      tool_call_id: m.tool_call_id,
      tool_calls: m.tool_calls,
    }));
  }
  return [];
});

// 非 messages 形态的请求（纯 prompt 字段）兜底展示
const promptFallback = computed(() => {
  const prompt = props.detail?.request_params?.prompt as string | undefined;
  return prompt || '';
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

// 格式化日期时间
function formatDateTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// 格式化延迟
function formatLatency(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(2)}s`;
}

// 获取客户端类型标签文字
function getClientTypeLabel(type: string): string {
  const labelMap: Record<string, string> = {
    llm: 'LLM',
    llm_fast: 'Fast',
    vlm: 'VLM',
    llm_local: 'Local',
  };
  return labelMap[type] || type;
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
.detail-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}

.detail-section {
  margin: 0;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--spacing-sm) 0;
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--border-color-light);
}

/* 带复制按钮的区块头：分隔线由 header 承担 */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--border-color-light);
  margin-bottom: var(--spacing-sm);
}

.section-header .section-title {
  border-bottom: none;
  padding-bottom: 0;
  margin-bottom: 0;
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

.error-title {
  color: var(--color-danger);
}

.mono {
  font-family: var(--font-mono);
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
  max-height: 400px;
  overflow-y: auto;
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
  color: #7c9cff;
  background: rgba(124, 156, 255, 0.15);
}

.prompt-role.is-user {
  color: #67c23a;
  background: rgba(103, 194, 58, 0.15);
}

.prompt-role.is-assistant {
  color: #409eff;
  background: rgba(64, 158, 255, 0.15);
}

.prompt-role.is-tool {
  color: #e6a23c;
  background: rgba(230, 162, 60, 0.15);
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

/* JSON 树容器（vue-json-pretty）：固定高度滚动 */
.params-block {
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  max-height: 400px;
  overflow: auto;
  font-size: 13px;
}

.tool-calls-block {
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  max-height: 400px;
  overflow: auto;
  font-size: 13px;
}

.response-content,
.reasoning-content {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
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
