<template>
  <div class="settings-page">
    <!-- 页面头部 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">系统设置</h1>
        <p class="page-subtitle">配置管理</p>
      </div>
      <div class="header-actions">
        <el-link
          v-if="settingsStore.hasChanges"
          type="primary"
          :underline="false"
          class="changes-panel-link"
          @click="showChangesPanel = true"
        >
          查看 {{ settingsStore.changeCount }} 项变更
        </el-link>
        <el-badge :value="settingsStore.changeCount" :hidden="!settingsStore.hasChanges">
          <el-button
            type="primary"
            :loading="settingsStore.saving"
            :disabled="!settingsStore.hasChanges"
            @click="handleSave"
          >
            <el-icon><Check /></el-icon>
            保存更改
          </el-button>
        </el-badge>
        <el-button :disabled="!settingsStore.hasChanges" @click="handleDiscard">
          <el-icon><RefreshLeft /></el-icon>
          重置
        </el-button>
      </div>
    </header>

    <!-- 待重启横幅：已落盘但需重启生效（用户选过「稍后重启」后常驻可见） -->
    <el-alert
      v-if="settingsStore.pendingRestart && !settingsStore.loading"
      type="warning"
      show-icon
      :closable="false"
      class="restart-banner"
    >
      <template #title>有已保存的配置需要重启服务才能生效</template>
      <el-button size="small" type="primary" @click="showRestartDialog = true">立即重启</el-button>
    </el-alert>

    <!-- 保存失败逐 key 定位：点击 key 切到所属文件 Tab 并滚动高亮字段 -->
    <el-alert
      v-if="saveErrors.length > 0"
      type="error"
      show-icon
      class="save-error-alert"
      @close="saveErrors = []"
    >
      <template #title>保存失败：{{ saveErrors.length }} 项更改未落盘，点击配置项定位</template>
      <div v-for="err in saveErrors" :key="err.key || err.message" class="save-error-row">
        <el-link
          v-if="err.key"
          type="primary"
          :underline="false"
          class="save-error-link"
          @click="jumpToField(err.key)"
        >
          {{ err.key }}
        </el-link>
        <span v-else class="save-error-msg">（未能定位到具体字段）</span>
        <span class="save-error-msg">{{ err.message }}</span>
      </div>
    </el-alert>

    <!-- 加载状态 -->
    <div v-if="settingsStore.loading" class="loading-container">
      <el-icon class="is-loading" :size="48"><Loading /></el-icon>
      <p>加载配置中...</p>
    </div>

    <!-- 错误状态 -->
    <el-alert
      v-else-if="settingsStore.error"
      type="error"
      :title="settingsStore.error"
      show-icon
      class="error-alert"
    />

    <!-- 主内容 -->
    <div v-else class="settings-content">
      <!-- 顶部栏：搜索 + Tab -->
      <div class="settings-toolbar">
        <el-input
          v-model="searchQuery"
          placeholder="搜索配置项..."
          :prefix-icon="Search"
          clearable
          size="default"
          class="search-input"
        />
        <div class="toolbar-actions">
          <el-button size="default" @click="issueExpandCommand('expand')">
            <el-icon><Expand /></el-icon>
            全部展开
          </el-button>
          <el-button size="default" @click="issueExpandCommand('collapse')">
            <el-icon><Fold /></el-icon>
            全部收起
          </el-button>
        </div>
      </div>

      <!-- 文件 Tab 导航 -->
      <el-tabs
        v-model="activeFileTab"
        class="file-tabs"
        :stretch="false"
        :before-leave="handleTabBeforeLeave"
      >
        <el-tab-pane v-for="tab in FILE_TABS" :key="tab.key" :name="tab.key" lazy>
          <template #label>
            <span class="file-tab-label">
              <el-icon class="file-tab-icon"><component :is="tab.icon" /></el-icon>
              <span>{{ tab.label }}</span>
              <!-- 搜索态：各文件命中数徽标（零命中不显示），点 Tab 即退出搜索进入该文件 -->
              <el-badge
                v-if="searchQuery && getSearchHitCount(tab.key) > 0"
                :value="getSearchHitCount(tab.key)"
                type="primary"
                class="file-tab-hit-badge"
              />
              <el-icon
                v-if="tab.restart && getFileChangeCount(tab.key) > 0"
                class="file-tab-restart"
                color="var(--color-warning)"
              >
                <WarningFilled />
              </el-icon>
            </span>
          </template>
          <!-- 搜索模式：跨文件结果 -->
          <div v-if="searchQuery" class="search-results">
            <div
              v-for="match in globalSearchResults"
              :key="match.section.key"
              class="search-section"
            >
              <div class="search-section-header">
                <el-icon class="section-header-icon"
                  ><component :is="getIcon(match.section.icon)"
                /></el-icon>
                <span class="search-section-title">{{ match.section.label }}</span>
                <el-tag size="small" type="info">{{
                  getFileTabLabel(match.section.file_name)
                }}</el-tag>
              </div>
              <div class="section-fields">
                <SubFieldGroup
                  :fields="match.fields"
                  :get-value="getFieldValue"
                  :get-original="settingsStore.originalValueAt"
                  :update-value="updateFieldValue"
                  :get-change-count="getPendingChangeCount"
                />
              </div>
            </div>
            <el-empty
              v-if="globalSearchResults.length === 0"
              description="没有找到匹配的配置项"
              :image-size="80"
            />
          </div>

          <!-- 正常浏览模式：分区卡片 -->
          <div v-else class="section-cards stagger-in">
            <div
              v-for="section in getFileSections(tab.key)"
              :key="section.key"
              class="section-card"
            >
              <div class="section-header">
                <div class="section-header-left">
                  <div class="section-icon-wrapper">
                    <el-icon class="section-icon"
                      ><component :is="getIcon(section.icon)"
                    /></el-icon>
                  </div>
                  <div class="section-title-area">
                    <div class="section-title-row">
                      <h3 class="section-title">{{ section.label }}</h3>
                      <el-tag v-if="section.version" size="small" type="info" effect="plain">
                        v{{ section.version }}
                      </el-tag>
                    </div>
                    <p v-if="section.description" class="section-desc">{{ section.description }}</p>
                  </div>
                </div>
                <el-badge
                  v-if="getSectionChangeCount(section.key) > 0"
                  :value="getSectionChangeCount(section.key)"
                  type="warning"
                />
              </div>
              <div class="section-fields">
                <!-- 浏览模式统一走卡片列表：实体卡 + 分类分组 + 紧凑元数据条 -->
                <ComponentCardList
                  :fields="getSectionFields(section)"
                  :enabled-field-key="ENABLED_LIST_KEYS[section.key] ?? null"
                  :get-value="getFieldValue"
                  :get-original="settingsStore.originalValueAt"
                  :update-value="updateFieldValue"
                  :get-change-count="getPendingChangeCount"
                  :expand-command="cardExpandCommand"
                />
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 变更清单面板：保存前 diff 预览 + 逐项撤销（全部撤销走页头「重置」，不在此重复入口） -->
    <el-dialog v-model="showChangesPanel" title="变更清单" width="680px" class="changes-panel">
      <el-empty
        v-if="settingsStore.pendingChanges.length === 0"
        description="没有待保存的更改"
        :image-size="80"
      />
      <div v-else class="changes-list">
        <div v-for="change in settingsStore.pendingChanges" :key="change.key" class="change-row">
          <div class="change-row-main">
            <el-link
              type="primary"
              :underline="false"
              class="change-key"
              @click="jumpFromChangesPanel(change.key)"
            >
              {{ change.key }}
            </el-link>
            <span class="change-diff">
              <code class="diff-old">{{ formatValue(change.oldValue) }}</code>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <code class="diff-new">{{ formatValue(change.newValue) }}</code>
            </span>
          </div>
          <el-button
            size="small"
            type="danger"
            plain
            @click="settingsStore.revertChange(change.key)"
          >
            撤销
          </el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="showChangesPanel = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 重启确认对话框 -->
    <el-dialog
      v-model="showRestartDialog"
      title="重启服务"
      width="400px"
      :close-on-click-modal="false"
    >
      <el-alert type="warning" title="部分配置更改需要重启服务才能生效" show-icon :closable="false">
        <p>是否立即重启服务？</p>
        <p class="restart-warning">注意：重启期间服务将暂时不可用</p>
      </el-alert>

      <template #footer>
        <el-button @click="showRestartDialog = false">稍后重启</el-button>
        <el-button type="primary" :loading="restarting" @click="handleRestart">
          立即重启
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { Check, RefreshLeft, Loading, Search } from '@element-plus/icons-vue';
import {
  Setting,
  User,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Cpu,
  DataAnalysis,
  Tools,
  Key,
  Connection,
  Management,
  WarningFilled,
  Expand,
  Fold,
  Right,
} from '@element-plus/icons-vue';
import { useSettingsStore } from '@/stores/settings';
import type { ConfigFieldSchema, ConfigGroupSchema } from '@/types/settings';
import SubFieldGroup from '@/components/settings/SubFieldGroup.vue';
import ComponentCardList from '@/components/settings/ComponentCardList.vue';

// ── 文件 Tab 定义（v2：7 文件配置树） ──────────────────────
// Tab 列表为前端硬编码（此处顺序即展示顺序）；后端 /config/schema 的 groups
// 自带 file_name/file_label，仅用于把分组归位到对应 Tab（groupsByFile），
// Tab 本身不从 schema 动态推断。
const FALLBACK_FILE_TABS = [
  {
    key: 'agents.toml',
    label: '业务 Agent',
    icon: Monitor,
    desc: '主播 Agent / 游戏 Agent / 文本_adv',
    restart: true,
  },
  {
    key: 'collectors.toml',
    label: '采集器',
    icon: Microphone,
    desc: '弹幕 / 语音 / 屏幕采集',
    restart: true,
  },
  { key: 'tools.toml', label: '工具包', icon: Tools, desc: '工具域开关与提供者', restart: true },
  {
    key: 'avatar.toml',
    label: '皮套',
    icon: Picture,
    desc: '虚拟形象平台 / 口型同步',
    restart: true,
  },
  {
    key: 'model.toml',
    label: '模型',
    icon: Cpu,
    desc: 'Provider / 模型 / 用途 Profile',
    restart: true,
  },
  { key: 'storage.toml', label: '存储', icon: Document, desc: 'SQLite / 记忆后端', restart: true },
  {
    key: 'infra.toml',
    label: '基础设施',
    icon: Setting,
    desc: 'TTS / 字幕 / Dashboard / 日志',
    restart: false,
  },
];

const FILE_TABS = FALLBACK_FILE_TABS;

// 简化图标映射（按使用频率排序，只保留用到的）
const iconMap: Record<string, unknown> = {
  Setting,
  User,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Cpu,
  DataAnalysis,
  Tools,
  Key,
  Connection,
  Management,
};

// ── 状态 ──────────────────────────────────────────────────
const settingsStore = useSettingsStore();
const showRestartDialog = ref(false);
const restarting = ref(false);
const searchQuery = ref('');
const activeFileTab = ref('agents.toml');
/** 最近一次保存失败的逐 key 错误；可点 key 跳转定位，关闭面板或再次保存时清除 */
const saveErrors = ref<{ key: string; message: string }[]>([]);
/** 变更清单面板（保存前 diff 预览 + 逐项撤销） */
const showChangesPanel = ref(false);

/** 工具栏「全部展开/全部收起」命令：seq 递增保证同方向连点也能触发子组件 watch */
const cardExpandCommand = ref<{ action: 'expand' | 'collapse'; seq: number } | null>(null);
let expandCommandSeq = 0;

function issueExpandCommand(action: 'expand' | 'collapse') {
  cardExpandCommand.value = { action, seq: ++expandCommandSeq };
}

// ── 计算属性 ──────────────────────────────────────────────
// 所有 group 按后端返回的 file_name 分组
const groupsByFile = computed(() => {
  const map = new Map<string, ConfigGroupSchema[]>();
  for (const group of settingsStore.groups) {
    const file = group.file_name;
    if (!file) continue;
    if (!map.has(file)) map.set(file, []);
    map.get(file)!.push(group);
  }
  return map;
});

// 获取某个文件下的 sections（排序后）
function getFileSections(fileName: string): ConfigGroupSchema[] {
  return (groupsByFile.value.get(fileName) || []).sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
}

// ── 卡片级启用名单寻址（仅名单驱动的文件有；其余文件卡片走各自布尔 enabled 子字段） ──
const ENABLED_LIST_KEYS: Record<string, string> = {
  collectors: 'collectors.enabled',
  agents: 'agents.agents.enabled',
};

/**
 * 浏览模式的分区字段整形：
 * - meta 容器只装只读版本号，不占卡片——版本改由分组头角标展示；
 * - 仅剩一个与 scope 同名的容器时（agents.agents / tools.tools）解包一层，
 *   让真正的实体（Agent / 工具提供者分类）直接成为卡片。
 */
function getSectionFields(section: ConfigGroupSchema): ConfigFieldSchema[] {
  const fields = section.fields.filter(f => !f.key.endsWith('.meta'));
  const containers = fields.filter(f => f.children && f.children.length > 0);
  if (containers.length === 1 && containers[0].key === `${section.key}.${section.key}`) {
    return fields.filter(f => f.key !== containers[0].key).concat(containers[0].children ?? []);
  }
  return fields;
}

// 初始化：默认选中第一个非空 Tab
watch(
  () => settingsStore.groups,
  groups => {
    if (groups.length === 0) return;
    // 检查当前 tab 是否有内容，没有则跳到第一个有内容的
    const hasCurrent = getFileSections(activeFileTab.value).length > 0;
    if (!hasCurrent) {
      const firstNonEmpty = FILE_TABS.find(t => getFileSections(t.key).length > 0);
      if (firstNonEmpty) activeFileTab.value = firstNonEmpty.key;
    }
  },
  { immediate: true },
);

// 加载配置
onMounted(async () => {
  await settingsStore.fetchSchema();
});

// 搜索模式：全局跨文件结果
const globalSearchResults = computed(() => {
  if (!searchQuery.value) return [];
  const q = searchQuery.value.toLowerCase();
  const results: { section: ConfigGroupSchema; fields: ConfigFieldSchema[]; file: string }[] = [];

  /**
   * 递归遍历字段：children 是树形分组，properties 是字典式键值（来自 object 类型）。
   * 命中即收集；用 seenSet 在「全组」粒度去重，避免嵌套字段被父级和子级同时返回造成重复卡片。
   */
  for (const group of settingsStore.groups) {
    const seen = new Set<string>();
    const matched: ConfigFieldSchema[] = [];
    collectMatchingFields(group.fields, q, seen, matched);
    if (matched.length > 0) {
      results.push({ section: group, fields: matched, file: group.file_name ?? '' });
    }
  }
  return results;
});

function fieldMatches(f: ConfigFieldSchema, q: string): boolean {
  return (
    f.label.toLowerCase().includes(q) ||
    f.key.toLowerCase().includes(q) ||
    (f.description?.toLowerCase().includes(q) ?? false)
  );
}

function collectMatchingFields(
  fields: ConfigFieldSchema[],
  q: string,
  seen: Set<string>,
  out: ConfigFieldSchema[],
): void {
  for (const f of fields) {
    if (!seen.has(f.key) && fieldMatches(f, q)) {
      seen.add(f.key);
      out.push(f);
    } else if (seen.has(f.key)) {
      // 命中过 key 的父级不再重复收集，但子级分支仍要继续遍历
    }
    if (f.children && f.children.length > 0) {
      collectMatchingFields(f.children, q, seen, out);
    }
    if (f.properties) {
      collectMatchingFields(Object.values(f.properties), q, seen, out);
    }
  }
}

// 搜索态：按文件名聚合命中字段数（Tab 徽标数据源；零命中 Tab 不显示徽标）
const searchHitCountByFile = computed(() => {
  const map = new Map<string, number>();
  for (const result of globalSearchResults.value) {
    const file = result.section.file_name ?? '';
    map.set(file, (map.get(file) ?? 0) + result.fields.length);
  }
  return map;
});

function getSearchHitCount(fileName: string): number {
  return searchHitCountByFile.value.get(fileName) ?? 0;
}

/**
 * 搜索态下点 Tab = 退出搜索并进入该文件：清空搜索词让目标 Tab 渲染浏览卡片，
 * 搜索框内容消失是预期行为（搜索=全局总览，点 Tab=深入该文件）。
 */
function handleTabBeforeLeave(): boolean {
  if (searchQuery.value) searchQuery.value = '';
  return true;
}

// ── 图标 ──────────────────────────────────────────────────
function getIcon(iconName?: string) {
  if (!iconName) return Setting;
  return iconMap[iconName] || Setting;
}

// 文件名 → Tab 标签：用于搜索结果行展示匹配所属的真实文件
function getFileTabLabel(fileName?: string): string {
  if (!fileName) return '';
  const tab = FILE_TABS.find(t => t.key === fileName);
  return tab?.label ?? fileName;
}

// ── 离开防护 ──────────────────────────────────────────────
// 站内路由切换：有未保存变更时先确认。更改仍留在 store 里，回到设置页可继续编辑；
// 真正的丢失路径是刷新/关闭标签页，由下方 beforeunload 兜底。
onBeforeRouteLeave(async () => {
  if (!settingsStore.hasChanges) return true;
  return await confirmAction(
    `有 ${settingsStore.changeCount} 项未保存的更改。离开后更改仍保留在本页，刷新或关闭浏览器才会丢失。确定离开吗？`,
    '未保存的更改',
    { confirmButtonText: '离开' },
  );
});

// 刷新/关闭标签页：浏览器原生确认（文案不可定制），未保存时挂拦截
function handleBeforeUnload(e: BeforeUnloadEvent) {
  if (!settingsStore.hasChanges) return;
  e.preventDefault();
  e.returnValue = '';
}
onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload));
onUnmounted(() => window.removeEventListener('beforeunload', handleBeforeUnload));

// ── 变更统计 ──────────────────────────────────────────────
// 某个文件下的变更数
function getFileChangeCount(fileName: string): number {
  const sections = getFileSections(fileName);
  const keys = new Set(sections.flatMap(s => s.fields.map(f => f.key)));
  return settingsStore.pendingChanges.filter(c => keys.has(c.key)).length;
}

// 某个 section 的变更数
function getSectionChangeCount(sectionKey: string): number {
  return settingsStore.pendingChanges.filter(c => c.key.startsWith(sectionKey + '.')).length;
}

/**
 * 子卡片徽标计数：传入任意字段 key，返回其下挂（严格前缀 `key.`）的待保存变更条数。
 * 子卡片自身若是被修改的叶子，依赖 FieldRenderer 的「已修改」标签；此处只标深层修改。
 */
function getPendingChangeCount(key: string): number {
  const prefix = key + '.';
  return settingsStore.pendingChanges.filter(c => c.key.startsWith(prefix)).length;
}

// ── 字段读写 ──────────────────────────────────────────────
function getFieldValue(key: string): unknown {
  const keys = key.split('.');
  let current: unknown = settingsStore.currentValues;
  for (const k of keys) {
    if (current && typeof current === 'object' && k in current) {
      current = (current as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return current;
}

function updateFieldValue(field: ConfigFieldSchema, value: unknown) {
  const keys = field.key.split('.');
  const newValues = { ...settingsStore.currentValues };
  let current: Record<string, unknown> = newValues;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (!current[k] || typeof current[k] !== 'object') {
      current[k] = {};
    }
    current[k] = { ...(current[k] as Record<string, unknown>) };
    current = current[k] as Record<string, unknown>;
  }
  current[keys[keys.length - 1]] = value;
  settingsStore.updateCurrentValues(newValues);
  updatePendingChanges(field, value);
}

function updatePendingChanges(field: ConfigFieldSchema, newValue: unknown) {
  settingsStore.applyFieldChange(field, newValue, settingsStore.originalValueAt(field.key));
}

// ── 保存 / 重置 / 重启 ──────────────────────────────────
async function handleSave() {
  if (!settingsStore.hasChanges) return;
  saveErrors.value = [];
  try {
    const result = await settingsStore.saveChanges();
    if (result.success) {
      ElMessage.success(result.message);
      if (result.requires_restart) {
        showRestartDialog.value = true;
      }
    } else {
      saveErrors.value = result.errors ?? [];
      ElMessage.error(result.message);
    }
  } catch (error) {
    console.error('Save failed:', error);
    ElMessage.error('保存配置失败');
  }
}

/**
 * 保存失败定位：key 首段即文件 scope，切到对应文件 Tab、清搜索（搜索态下
 * 浏览卡片不渲染）后按 data-config-key 滚动高亮。
 */
async function jumpToField(key: string) {
  const fileKey = `${key.split('.')[0]}.toml`;
  if (FILE_TABS.some(t => t.key === fileKey)) {
    activeFileTab.value = fileKey;
  }
  if (searchQuery.value) searchQuery.value = '';
  await nextTick();
  const el = document.querySelector<HTMLElement>(`[data-config-key="${key}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.remove('field-flash');
  void el.offsetWidth; // 强制重排以重启动画，同一字段连续点击也能再次闪烁
  el.classList.add('field-flash');
  window.setTimeout(() => el.classList.remove('field-flash'), 1600);
}

// ── 变更清单面板 ────────────────────────────────────────
// 值统一 JSON 化展示，长值截断（undefined 显示为「未设置」，对应原路径缺失的回滚形态）
function formatValue(value: unknown): string {
  if (value === undefined) return '未设置';
  const text = JSON.stringify(value) ?? String(value);
  return text.length > 40 ? text.slice(0, 40) + '…' : text;
}

/** 面板内点字段 key：先关面板再滚动定位，避免高亮被遮挡 */
async function jumpFromChangesPanel(key: string) {
  showChangesPanel.value = false;
  await nextTick();
  await jumpToField(key);
}

// 清单逐项撤销至空时自动收起面板
watch(
  () => settingsStore.pendingChanges.length,
  len => {
    if (len === 0) showChangesPanel.value = false;
  },
);

async function handleDiscard() {
  if (!settingsStore.hasChanges) return;
  const ok = await confirmAction('确定要丢弃所有未保存的更改吗？', '确认丢弃', {
    confirmButtonText: '丢弃',
  });
  if (!ok) return;
  settingsStore.discardChanges();
  ElMessage.info('已丢弃所有更改');
}

async function handleRestart() {
  restarting.value = true;
  try {
    const result = await settingsStore.restartService();
    if (result.success) {
      ElMessage.success('服务正在重启...');
      showRestartDialog.value = false;
    } else {
      ElMessage.error(result.message);
    }
  } catch (error) {
    console.error('Restart failed:', error);
    ElMessage.error('重启服务失败');
  } finally {
    restarting.value = false;
  }
}
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

/* 变更清单入口与按钮基线对齐 */
.changes-panel-link {
  font-size: 13px;
  margin-right: var(--spacing-xs);
}

/* 搜索态 Tab 命中数徽标：覆盖 el-badge 默认绝对定位，改为内联跟随文本 */
.file-tab-hit-badge {
  position: static;
  transform: none;
}

.file-tab-hit-badge :deep(.el-badge__content) {
  position: static;
  transform: none;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--text-secondary);
}

.loading-container p {
  margin-top: var(--spacing-md);
}

.error-alert {
  flex-shrink: 0;
}

/* ── 待重启横幅 / 保存失败面板 ─────────────────────────── */
.restart-banner,
.save-error-alert {
  flex-shrink: 0;
  margin-bottom: var(--spacing-md);
}

.save-error-row {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  padding: 2px 0;
}

.save-error-link {
  font-family: var(--font-mono);
  font-size: 12px;
  flex-shrink: 0;
}

.save-error-msg {
  font-size: 12px;
  color: var(--text-secondary);
}

/* jumpToField 的滚动高亮闪烁：作用于子组件根节点（携带本组件作用域 id） */
.field-flash {
  animation: field-flash-anim 1.5s ease;
}

@keyframes field-flash-anim {
  0%,
  55% {
    box-shadow: 0 0 0 2px var(--color-danger);
  }
  100% {
    box-shadow: none;
  }
}

/* ── 主内容区 ──────────────────────────────────────────── */
.settings-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
}

/* 顶部栏：搜索框 + 全局展开/收起 */
.settings-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
  padding: var(--spacing-md) var(--spacing-lg) 0;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.search-input {
  max-width: 360px;
}

/* ── File Tabs ──────────────────────────────────────────── */
.file-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 var(--spacing-lg);
}

.file-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: var(--spacing-sm) 0 0;
  flex-shrink: 0;
}

.file-tabs :deep(.el-tabs__nav-wrap) {
  padding-left: 0;
}

.file-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
  padding: 0 20px;
  height: 40px;
  line-height: 40px;
  transition: color var(--transition-fast);
}

.file-tabs :deep(.el-tabs__item:hover) {
  color: var(--color-primary);
}

.file-tabs :deep(.el-tabs__active-bar) {
  height: 2px;
  background: var(--color-primary);
}

/* 文件 Tab 自定义 label：图标 + 文本 + 重启警告 */
.file-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.file-tab-icon {
  font-size: 14px;
}

.file-tab-restart {
  font-size: 14px;
}

/* Tab 内容区可滚动 */
.file-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}

.file-tabs :deep(.el-tab-pane) {
  height: 100%;
  overflow-y: auto;
  padding: var(--spacing-md) 0;
}

/* ── 搜索模式结果 ──────────────────────────────────────── */
.search-results {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.search-section {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.search-section-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
  color: var(--text-regular);
}

.section-header-icon {
  font-size: 16px;
  color: var(--color-primary);
}

.search-section-title {
  font-weight: 500;
  flex: 1;
}

/* ── 分区卡片列表 ──────────────────────────────────────── */
.section-cards {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.section-card {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
  transition: box-shadow var(--transition-fast);
}

.section-card:hover {
  box-shadow: var(--shadow-md);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
}

.section-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.section-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--color-primary-light-9);
  flex-shrink: 0;
}

.section-icon {
  font-size: 18px;
  color: var(--color-primary);
}

.section-title-area {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.section-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.section-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.section-fields {
  padding: var(--spacing-md) var(--spacing-lg);
}

/* ── 变更清单面板 ──────────────────────────────────────── */
.changes-list {
  display: flex;
  flex-direction: column;
}

.change-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--border-color-light);
}

.change-row:last-child {
  border-bottom: none;
}

.change-row-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.change-key {
  font-family: var(--font-mono);
  font-size: 12px;
  align-self: flex-start;
}

.change-diff {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
  font-size: 12px;
  min-width: 0;
  flex-wrap: wrap;
}

.change-diff code {
  font-family: var(--font-mono);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  word-break: break-all;
}

.diff-old {
  background: var(--color-danger-light-9, rgba(245, 108, 108, 0.1));
  color: var(--color-danger);
  text-decoration: line-through;
}

.diff-new {
  background: var(--color-success-light-9, rgba(103, 194, 58, 0.1));
  color: var(--color-success);
}

.diff-arrow {
  flex-shrink: 0;
  color: var(--text-secondary);
}

/* ── 重启警告 ──────────────────────────────────────────── */
.restart-warning {
  margin-top: var(--spacing-sm);
  font-size: 12px;
  color: var(--color-warning);
}

/* ── 滚动条 ────────────────────────────────────────────── */
.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar {
  width: 6px;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-track {
  background: transparent;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* ── 响应式 ────────────────────────────────────────────── */
@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .settings-toolbar {
    padding: var(--spacing-sm) var(--spacing-md) 0;
  }

  .search-input {
    max-width: 100%;
  }

  .file-tabs {
    padding: 0 var(--spacing-md);
  }

  .file-tabs :deep(.el-tabs__item) {
    padding: 0 12px;
    font-size: 13px;
  }

  .section-header {
    padding: var(--spacing-sm) var(--spacing-md);
  }

  .section-fields {
    padding: var(--spacing-xs) var(--spacing-md);
  }
}
</style>
