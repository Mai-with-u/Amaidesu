<template>
  <div class="skills-shell">
    <!-- 页面头：身份 + 刷新                                               -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">技能</h1>
        <p class="page-subtitle">
          她做成一类事的做法文档 · 本地策略随仓库发布，玩法技能由游戏内服务端发布
        </p>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <!-- 远端登记状态：未连接 / 服务端未发布 / 已登记，各有各的文案             -->
    <el-alert
      v-if="remoteHint"
      :title="remoteHint.text"
      :type="remoteHint.type"
      :closable="false"
      show-icon
      class="remote-status"
    />

    <div class="skills-page">
      <!-- LEFT：技能目录（按分类分组）                                      -->
      <aside class="md-list-panel" aria-label="技能目录">
        <div v-if="loading" class="md-list-empty">
          <el-skeleton :rows="4" animated />
        </div>
        <el-alert
          v-else-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
          class="list-error"
        >
          <el-button type="primary" @click="reload">重试</el-button>
        </el-alert>
        <div v-else-if="entries.length === 0" class="md-list-empty">
          <el-empty :image-size="64" description="还没有任何技能" />
        </div>
        <ul v-else class="md-list" role="listbox">
          <template v-for="group in groupedEntries" :key="group.category">
            <li class="category-title">{{ group.category }}</li>
            <li
              v-for="entry in group.items"
              :key="entry.name"
              class="md-row"
              :class="{ 'is-selected': entry.name === selectedName }"
              role="option"
              :aria-selected="entry.name === selectedName"
              tabindex="0"
              @keydown.enter.prevent="select(entry)"
              @keydown.space.prevent="select(entry)"
              @click="select(entry)"
            >
              <span class="md-row-name" :title="entry.name">{{ entry.name }}</span>
              <el-tag
                size="small"
                :type="entry.origin === 'local' ? 'info' : 'warning'"
                effect="plain"
                class="origin-tag"
              >
                {{ entry.origin === 'local' ? '本地' : entry.origin }}
              </el-tag>
            </li>
          </template>
        </ul>
      </aside>

      <!-- RIGHT：技能详情（元信息 + 本地正文预览）                          -->
      <main class="md-detail-panel" aria-label="技能详情">
        <div v-if="!active" class="detail-placeholder">
          <el-empty :image-size="72" description="从左侧选一项技能，看它写给模型的做法" />
        </div>
        <template v-else>
          <header class="md-detail-header">
            <div class="md-detail-title-block">
              <div class="md-detail-title-row">
                <h1 class="md-detail-name">{{ active.name }}</h1>
                <el-tag
                  size="small"
                  :type="active.origin === 'local' ? 'info' : 'warning'"
                  effect="plain"
                >
                  {{ active.origin === 'local' ? '本地文档' : active.origin }}
                </el-tag>
              </div>
              <p class="md-detail-description">{{ active.description }}</p>
            </div>
          </header>
          <section class="detail-meta">
            <div class="meta-row">
              <dt>受众</dt>
              <dd>{{ agentsLabel(active) }}</dd>
            </div>
            <div v-if="active.path" class="meta-row">
              <dt>文件</dt>
              <dd>
                <code class="meta-path">{{ active.path }}</code>
              </dd>
            </div>
            <div v-else-if="active.uri" class="meta-row">
              <dt>地址</dt>
              <dd>
                <code class="meta-path">{{ active.uri }}</code>
              </dd>
            </div>
          </section>
          <section class="detail-body">
            <el-skeleton v-if="bodyLoading" :rows="6" animated />
            <el-alert
              v-else-if="bodyError"
              :title="bodyError"
              type="error"
              :closable="false"
              show-icon
            />
            <!-- 技能文档本身是 Markdown；内容经 renderMarkdown 的 DOMPurify 净化后输出 -->
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div v-else class="markdown-body body-text" v-html="bodyHtml"></div>
          </section>
        </template>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 技能检视页 —— 她带着哪些"怎么做"文档、装上没、长什么样（纯只读）
 *
 * 数据来自 /api/v1/skills 快照：本地技能随仓库发布，远端玩法技能由游戏 Agent
 * 运行期从 MaiCraft 登记（内存持有，Agent 未连接时远端为空）。左侧目录按分类
 * 分组列全量条目，右侧看正文预览——本地直接读，远端经游戏 Agent 的
 * minecraft_skill 同通道向 MaiCraft 代读（模型读到什么这里显示什么），Agent
 * 未运行时预览区给出可读的原因。顶部的远端登记
 * 状态按"可观测 → 在运行 → 已读过目录"逐级降级，未连接与服务端未发布是
 * 两种不同的空态，不混成一个灰色空表。
 */
import { computed, onMounted, ref } from 'vue';
import '@/styles/component-master-detail.css';
import { skillsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { renderMarkdown } from '@/utils/markdown';
import type { SkillItem, SkillRemoteStatus, SkillsSnapshotResponse } from '@/types/skills';

const loading = ref(false);
const error = ref('');
const entries = ref<SkillItem[]>([]);
const remoteStatus = ref<SkillRemoteStatus | null>(null);

const selectedName = ref('');
const body = ref('');
const bodyLoading = ref(false);
const bodyError = ref('');

/** 技能正文渲染成 Markdown（renderMarkdown 内含 DOMPurify 净化） */
const bodyHtml = computed(() => renderMarkdown(body.value));

/** 当前选中的技能条目 */
const active = computed(() => entries.value.find(item => item.name === selectedName.value) ?? null);

/** 目录按分类分组（后端已按分类与名字排序，这里只做分组） */
const groupedEntries = computed(() => {
  const groups: { category: string; items: SkillItem[] }[] = [];
  for (const entry of entries.value) {
    const last = groups[groups.length - 1];
    if (last && last.category === entry.category) {
      last.items.push(entry);
    } else {
      groups.push({ category: entry.category, items: [entry] });
    }
  }
  return groups;
});

/** 远端登记状态提示：按可观测性逐级降级，各级空态文案分开写 */
const remoteHint = computed<{ text: string; type: 'success' | 'warning' | 'info' } | null>(() => {
  const status = remoteStatus.value;
  if (!status) return null;
  if (!status.observable) {
    return {
      text: '游戏 Agent 未注册或未装配，远端玩法技能状态不可观测；本地技能不受影响',
      type: 'info',
    };
  }
  if (status.agent_running === false) {
    return { text: '游戏 Agent 已停止，远端目录以下次启动后的连接为准', type: 'info' };
  }
  if (status.remote_loaded === false) {
    return { text: '还没读到 MaiCraft 的技能目录（未连接，或本轮还没轮到读）', type: 'warning' };
  }
  if (status.remote_count === 0) {
    return { text: 'MaiCraft 没有发布玩法技能（旧版 Mod 或没有资源目录接口）', type: 'warning' };
  }
  return { text: `已登记 MaiCraft 的 ${status.remote_count} 项玩法技能`, type: 'success' };
});

/** 受众展示：'*' 是给所有接入技能的 Agent 用 */
function agentsLabel(entry: SkillItem): string {
  if (entry.agents.includes('*')) return '全部 Agent';
  return entry.agents.join('、');
}

async function reload(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const resp = await skillsApi.getSnapshot();
    const data: SkillsSnapshotResponse = resp.data;
    entries.value = data.entries ?? [];
    remoteStatus.value = data.remote;
    // 刷新后原选中项可能已不在目录里（远端条目随连接进出），回退到首项
    if (!entries.value.some(item => item.name === selectedName.value)) {
      selectedName.value = entries.value[0]?.name ?? '';
    }
    if (selectedName.value) {
      void loadBody(selectedName.value);
    }
  } catch (e) {
    error.value = getApiErrorMessage(e, '无法加载技能数据');
    entries.value = [];
    remoteStatus.value = null;
  } finally {
    loading.value = false;
  }
}

/** 切换选中项并拉正文预览（本地直接读；远端由后端经游戏 Agent 代读，失败给原因） */
function select(entry: SkillItem): void {
  selectedName.value = entry.name;
  void loadBody(entry.name);
}

async function loadBody(name: string): Promise<void> {
  bodyLoading.value = true;
  bodyError.value = '';
  try {
    const resp = await skillsApi.fetchBody(name);
    body.value = resp.data.content;
  } catch (e) {
    body.value = '';
    bodyError.value = getApiErrorMessage(e, '无法读取技能正文');
  } finally {
    bodyLoading.value = false;
  }
}

onMounted(() => {
  void reload();
});
</script>

<style scoped>
/* 页面级：强调色注入 + 网格布局；主从通用样式见 styles/component-master-detail.css。
   技能目前没有专属语义色，暂借工具的绿色（技能与工具同属被动能力侧）。 */
.skills-shell {
  --md-accent: var(--color-tool);
  --md-accent-bg: var(--color-tool-bg);

  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 560px;
}

.skills-page {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--spacing-md);
  flex: 1;
  min-height: 0;
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.remote-status {
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

/* 目录里的分类小标题：分组边界，不参与选中 */
.category-title {
  padding: var(--spacing-sm) var(--spacing-md) var(--spacing-xs);
  font-size: var(--text-2xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--text-secondary);
  list-style: none;
  cursor: default;
}

.origin-tag {
  flex-shrink: 0;
}

.list-error {
  margin: var(--spacing-md);
}

/* 未选中时的详情占位 */
.detail-placeholder {
  flex: 1;
  display: grid;
  place-items: center;
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

/* 元信息条：受众与来源文件 */
.detail-meta {
  margin: 0;
  padding: var(--spacing-sm) var(--spacing-lg);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
}

.meta-row {
  display: flex;
  gap: var(--spacing-md);
  padding: 4px 0;
  font-size: 13px;
}

.meta-row dt {
  width: 3em;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.meta-row dd {
  margin: 0;
  min-width: 0;
  color: var(--text-regular);
}

.meta-path {
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-wrap: anywhere;
}

/* 正文预览：技能文档本身是 Markdown 源码，按等宽预格式文本呈现 */
.detail-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* 正文预览容器：技能文档是 Markdown，渲染样式按 ChangelogDialog 的 markdown-body 同款 */
.body-text {
  flex: 1;
  min-height: 0;
  padding: var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow-y: auto;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}

.body-text :deep(h1) {
  font-size: 17px;
  margin: 0 0 var(--spacing-sm);
  padding-bottom: var(--spacing-xs);
  border-bottom: 1px solid var(--border-color-light);
}

.body-text :deep(h2) {
  font-size: 15px;
  margin: var(--spacing-md) 0 var(--spacing-xs);
}

.body-text :deep(h3) {
  font-size: 14px;
  margin: var(--spacing-sm) 0 var(--spacing-xs);
}

.body-text :deep(p) {
  margin: var(--spacing-xs) 0;
}

.body-text :deep(ul),
.body-text :deep(ol) {
  padding-left: 20px;
  margin: var(--spacing-xs) 0;
}

.body-text :deep(li) {
  margin: 2px 0;
}

.body-text :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
}

.body-text :deep(a:hover) {
  text-decoration: underline;
}

.body-text :deep(code) {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background-color: var(--bg-hover);
  font-family: var(--font-mono);
  font-size: 12px;
}

.body-text :deep(pre) {
  padding: var(--spacing-sm);
  border-radius: var(--radius-md);
  background-color: var(--bg-hover);
  overflow-x: auto;
}

.body-text :deep(pre code) {
  padding: 0;
  background-color: transparent;
}

.body-text :deep(blockquote) {
  margin: var(--spacing-sm) 0;
  padding: 0 var(--spacing-md);
  border-left: 3px solid var(--border-color-light);
  color: var(--text-secondary);
}

.body-text :deep(table) {
  border-collapse: collapse;
  margin: var(--spacing-xs) 0;
}

.body-text :deep(th),
.body-text :deep(td) {
  padding: 4px 10px;
  border: 1px solid var(--border-color-light);
}

.body-text :deep(:first-child) {
  margin-top: 0;
}

.body-text :deep(:last-child) {
  margin-bottom: 0;
}

@media (max-width: 860px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .skills-shell {
    height: auto;
  }

  .skills-page {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
