<template>
  <el-dialog
    :model-value="visible"
    title="请求详情"
    width="950px"
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

      <!-- 提示词预览 -->
      <div v-if="promptContent" class="detail-section">
        <h4 class="section-title">提示词预览</h4>
        <div class="prompt-preview" v-html="renderMarkdown(promptContent)"></div>
      </div>

      <!-- 请求参数 -->
      <div class="detail-section">
        <h4 class="section-title">请求参数</h4>
        <pre
          class="code-block json-highlight"
        ><code v-html="formatJsonHighlight(detail.request_params)"></code></pre>
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
        <h4 class="section-title">工具调用</h4>
        <pre class="code-block"><code>{{ formatJson(detail.tool_calls) }}</code></pre>
      </div>

      <!-- 错误信息 -->
      <div v-if="detail.error" class="detail-section">
        <h4 class="section-title error-title">错误信息</h4>
        <div class="error-content">
          {{ detail.error }}
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
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';
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

// 从 messages 中提取提示词内容
const promptContent = computed(() => {
  if (!props.detail?.request_params) return '';

  const params = props.detail.request_params;
  const messages = params.messages as Array<{ role?: string; content?: string }> | undefined;

  if (messages && Array.isArray(messages)) {
    return messages
      .filter((m) => m.role === 'user' || m.role === 'system')
      .map((m) => `**${m.role}**: ${m.content || ''}`)
      .join('\n\n');
  }

  // 尝试直接获取 prompt 字段
  const prompt = params.prompt as string | undefined;
  if (prompt) {
    return prompt;
  }

  return '';
});

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

// 格式化 JSON
function formatJson(data: unknown): string {
  return JSON.stringify(data, null, 2);
}

// JSON 格式化并语法高亮
function formatJsonHighlight(obj: unknown): string {
  const json = JSON.stringify(obj, null, 2);
  try {
    return hljs.highlight(json, { language: 'json' }).value;
  } catch {
    return json;
  }
}

// Markdown 渲染（带 XSS 防护）
function renderMarkdown(text: string | null): string {
  if (!text) return '';
  const html = marked(text) as string;
  return DOMPurify.sanitize(html);
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

/* 提示词预览样式 */
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

.prompt-preview :first-child {
  margin-top: 0;
}

.prompt-preview :last-child {
  margin-bottom: 0;
}

.prompt-preview h1,
.prompt-preview h2,
.prompt-preview h3,
.prompt-preview h4 {
  margin-top: var(--spacing-md);
  margin-bottom: var(--spacing-sm);
  color: var(--text-primary);
}

.prompt-preview h1:first-child,
.prompt-preview h2:first-child,
.prompt-preview h3:first-child,
.prompt-preview h4:first-child {
  margin-top: 0;
}

.prompt-preview p {
  margin: var(--spacing-sm) 0;
}

.prompt-preview code {
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12px;
}

.prompt-preview pre {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
  overflow-x: auto;
  margin: var(--spacing-sm) 0;
}

.prompt-preview pre code {
  background: transparent;
  padding: 0;
}

.prompt-preview ul,
.prompt-preview ol {
  margin: var(--spacing-sm) 0;
  padding-left: var(--spacing-lg);
}

.prompt-preview li {
  margin: var(--spacing-xs) 0;
}

.prompt-preview blockquote {
  border-left: 3px solid var(--color-primary);
  margin: var(--spacing-sm) 0;
  padding-left: var(--spacing-md);
  color: var(--text-secondary);
}

.prompt-preview strong {
  color: var(--color-primary);
}

/* JSON 高亮样式 */
.json-highlight {
  background: #1e1e1e;
  border: none;
}

.json-highlight code {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.5;
  color: #d4d4d4;
}

/* Highlight.js 主题覆盖 */
.json-highlight :deep(.hljs-attr) {
  color: #9cdcfe;
}

.json-highlight :deep(.hljs-string) {
  color: #ce9178;
}

.json-highlight :deep(.hljs-number) {
  color: #b5cea8;
}

.json-highlight :deep(.hljs-literal) {
  color: #569cd6;
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
