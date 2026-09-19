<template>
  <el-dialog
    :model-value="props.modelValue"
    title="更新日志"
    width="680px"
    top="6vh"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
    @open="loadChangelog"
  >
    <div v-loading="loading" class="changelog-body">
      <el-empty v-if="errorMsg" :description="errorMsg" :image-size="80" />
      <!-- 内容经 DOMPurify 净化后输出 -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-else-if="renderedHtml" class="markdown-body" v-html="renderedHtml" />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { systemApi } from '@/api';

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>();

const loading = ref(false);
const errorMsg = ref('');
const content = ref('');

// CHANGELOG 内的仓库相对链接（docs/...）在 WebUI 无对应路由，
// 统一改写到 GitHub 仓库对应文件后新窗口打开
const REPO_BLOB_BASE = 'https://github.com/Mai-with-u/Amaidesu/blob/main';

marked.setOptions({ gfm: true, breaks: true });

const renderedHtml = computed(() => (content.value ? renderMarkdown(content.value) : ''));

function renderMarkdown(md: string): string {
  // async: false 锁定同步重载，parse 返回 string 而非 Promise
  const clean = DOMPurify.sanitize(marked.parse(md, { async: false }));
  const doc = new DOMParser().parseFromString(clean, 'text/html');
  doc.querySelectorAll('a').forEach(a => {
    const href = a.getAttribute('href') ?? '';
    if (href && !/^https?:\/\//i.test(href)) {
      a.setAttribute('href', `${REPO_BLOB_BASE}/${href.replace(/^\.\//, '')}`);
    }
    a.setAttribute('target', '_blank');
    a.setAttribute('rel', 'noopener noreferrer');
  });
  return doc.body.innerHTML;
}

async function loadChangelog(): Promise<void> {
  loading.value = true;
  errorMsg.value = '';
  content.value = '';
  try {
    const { data } = await systemApi.getChangelog();
    content.value = data.content;
  } catch {
    errorMsg.value = '更新日志加载失败';
    ElMessage.error('更新日志加载失败');
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.changelog-body {
  max-height: 70vh;
  overflow-y: auto;
  min-height: 120px;
}

.changelog-error {
  padding: var(--spacing-lg) 0;
}

.markdown-body {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}

.markdown-body :deep(h1) {
  font-size: 20px;
  margin: 0 0 var(--spacing-md);
  padding-bottom: var(--spacing-sm);
  border-bottom: 1px solid var(--border-color-light);
}

.markdown-body :deep(h2) {
  font-size: 16px;
  margin: var(--spacing-lg) 0 var(--spacing-sm);
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border-color-light);
}

.markdown-body :deep(h3) {
  font-size: 14px;
  margin: var(--spacing-md) 0 var(--spacing-xs);
}

.markdown-body :deep(p) {
  margin: var(--spacing-xs) 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: var(--spacing-xs) 0;
}

.markdown-body :deep(li) {
  margin: 2px 0;
}

.markdown-body :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
}

.markdown-body :deep(a:hover) {
  text-decoration: underline;
}

.markdown-body :deep(code) {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-hover);
  font-family: var(--font-mono);
  font-size: 12px;
}

.markdown-body :deep(pre) {
  padding: var(--spacing-sm);
  border-radius: var(--radius-md);
  background-color: var(--bg-hover);
  overflow-x: auto;
}

.markdown-body :deep(blockquote) {
  margin: var(--spacing-sm) 0;
  padding: 0 var(--spacing-md);
  border-left: 3px solid var(--border-color-light);
  color: var(--text-secondary);
}
</style>
