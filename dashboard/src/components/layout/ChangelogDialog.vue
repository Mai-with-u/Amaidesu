<template>
  <el-dialog
    :model-value="props.modelValue"
    title="更新日志"
    width="min(960px, 92vw)"
    top="6vh"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
    @open="loadChangelog"
  >
    <div v-loading="loading" class="changelog-layout">
      <el-empty v-if="errorMsg" :description="errorMsg" :image-size="80" />
      <template v-else-if="contentHtml">
        <aside class="changelog-toc">
          <div class="toc-heading">版本</div>
          <button
            v-for="entry in tocEntries"
            :key="entry.id"
            type="button"
            class="toc-item"
            :class="{ 'is-active': entry.id === activeId }"
            @click="scrollToVersion(entry.id)"
          >
            <span class="toc-version">v{{ entry.version }}</span>
            <span v-if="entry.date" class="toc-date">{{ entry.date }}</span>
          </button>
        </aside>
        <!-- 内容经 DOMPurify 净化后输出 -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div
          ref="contentRef"
          class="markdown-body changelog-content"
          @scroll="onContentScroll"
          v-html="contentHtml"
        />
      </template>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { systemApi } from '@/api';

interface TocEntry {
  id: string;
  version: string;
  date: string;
}

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>();

const loading = ref(false);
const errorMsg = ref('');
const contentHtml = ref('');
const tocEntries = ref<TocEntry[]>([]);
const activeId = ref('');
const contentRef = ref<HTMLElement | null>(null);

// CHANGELOG 内的仓库相对链接（docs/...）在 WebUI 无对应路由，
// 统一改写到 GitHub 仓库对应文件后新窗口打开
const REPO_BLOB_BASE = 'https://github.com/Mai-with-u/Amaidesu/blob/main';

marked.setOptions({ gfm: true, breaks: true });

function renderChangelog(md: string): void {
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
  // 版本级标题（## [x.y.z] - 日期）编入锚点 id，供侧栏目录定位与点亮
  const entries: TocEntry[] = [];
  doc.querySelectorAll('h2').forEach((h, index) => {
    const id = `changelog-version-${index}`;
    h.id = id;
    const text = (h.textContent ?? '').trim();
    const match = text.match(/^\[(.+?)\]\s*-\s*(.+)$/);
    entries.push({ id, version: match ? match[1] : text, date: match ? match[2] : '' });
  });
  tocEntries.value = entries;
  activeId.value = entries[0]?.id ?? '';
  contentHtml.value = doc.body.innerHTML;
}

function scrollToVersion(id: string): void {
  const container = contentRef.value;
  const target = container?.querySelector(`#${id}`);
  if (!container || !target) return;
  const top =
    target.getBoundingClientRect().top -
    container.getBoundingClientRect().top +
    container.scrollTop;
  container.scrollTo({ top: Math.max(top - 8, 0), behavior: 'smooth' });
  activeId.value = id;
}

// 滚动跟随：以容器顶部 80px 为界取最后越界的版本标题，触底时点亮最后一项
function onContentScroll(): void {
  const container = contentRef.value;
  if (!container || tocEntries.value.length === 0) return;
  if (container.scrollTop + container.clientHeight >= container.scrollHeight - 4) {
    activeId.value = tocEntries.value[tocEntries.value.length - 1].id;
    return;
  }
  const containerTop = container.getBoundingClientRect().top;
  let current = tocEntries.value[0].id;
  for (const entry of tocEntries.value) {
    const el = container.querySelector(`#${entry.id}`);
    if (!el || el.getBoundingClientRect().top - containerTop > 80) break;
    current = entry.id;
  }
  activeId.value = current;
}

async function loadChangelog(): Promise<void> {
  loading.value = true;
  errorMsg.value = '';
  contentHtml.value = '';
  tocEntries.value = [];
  try {
    const { data } = await systemApi.getChangelog();
    renderChangelog(data.content);
  } catch {
    errorMsg.value = '更新日志加载失败';
    ElMessage.error('更新日志加载失败');
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.changelog-layout {
  display: flex;
  gap: var(--spacing-lg);
  height: 66vh;
  min-height: 360px;
}

.changelog-toc {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 2px;
  width: 150px;
  padding-right: var(--spacing-sm);
  border-right: 1px solid var(--border-color-light);
  overflow-y: auto;
}

.toc-heading {
  padding: 0 10px var(--spacing-xs);
  font-size: 10px;
  font-weight: 600;
  color: var(--text-placeholder);
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.toc-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  padding: 6px 10px;
  border: none;
  border-radius: var(--radius-md);
  background-color: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.toc-item:hover {
  background-color: var(--bg-hover);
}

.toc-item.is-active {
  background-color: var(--bg-active);
  color: var(--color-primary);
}

.toc-version {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
}

.toc-date {
  font-size: 11px;
  color: var(--text-secondary);
}

.changelog-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
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
