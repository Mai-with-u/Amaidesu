<template>
  <div class="settings-page">
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

    <!-- 两栏主体：左侧（文件导航 + 本页目录）/ 右侧（复合页头 + 滚动内容） -->
    <div v-else class="settings-shell">
      <aside class="settings-sidebar">
        <div class="sidebar-brand">
          <span class="sidebar-brand-title">系统设置</span>
          <span class="sidebar-brand-sub">配置管理</span>
        </div>

        <!-- 文件导航：七个配置文件 -->
        <nav class="file-nav">
          <button
            v-for="tab in FILE_TABS"
            :key="tab.key"
            type="button"
            class="file-nav-item"
            :class="{ active: tab.key === activeFileTab }"
            @click="switchFile(tab.key)"
          >
            <el-icon class="file-nav-icon"><component :is="tab.icon" /></el-icon>
            <span class="file-nav-label">{{ tab.label }}</span>
            <el-badge
              v-if="getFileChangeCount(tab.key) > 0"
              :value="getFileChangeCount(tab.key)"
              type="warning"
              size="small"
            />
            <el-icon v-if="tab.restart && getFileChangeCount(tab.key) > 0" class="file-nav-restart">
              <WarningFilled />
            </el-icon>
          </button>
        </nav>

        <!-- 常用分组：钉固的跨文件收藏（localStorage 持久化） -->
        <template v-if="pinnedEntries.length > 0">
          <div class="sidebar-divider"></div>
          <div class="toc-title">常用分组</div>
          <nav class="pinned-list">
            <div v-for="p in pinnedEntries" :key="p.key" class="pinned-item">
              <button type="button" class="pinned-open" :title="p.key" @click="openPinned(p)">
                <el-icon class="pinned-star" :size="15"><StarFilled /></el-icon>
                <span class="pinned-label">{{ p.label }}</span>
              </button>
              <el-icon class="pinned-remove" title="移除钉固" @click.stop="removePin(p.key)">
                <Close />
              </el-icon>
            </div>
          </nav>
        </template>

        <div class="sidebar-divider"></div>

        <!-- 本页目录：当前文件的分区 → 实体 → 子分组，点击滚动定位 -->
        <div class="toc-title">本页目录</div>
        <nav class="toc">
          <div
            v-for="entry in tocEntries"
            :key="entry.key"
            class="toc-entry"
            :class="{ 'toc-entry--pinned': isPinned(entry.key) }"
          >
            <button
              type="button"
              class="toc-item"
              :class="[`toc-item--l${entry.level}`, { active: entry.key === activeTocKey }]"
              :title="entry.label"
              @click="scrollToTocEntry(entry.key)"
            >
              <span class="toc-label">{{ entry.label }}</span>
              <el-badge
                v-if="entry.changeCount > 0"
                :value="entry.changeCount"
                type="warning"
                size="small"
              />
            </button>
            <!-- 图钉：把实体/子分组收藏进「常用分组」（模型页无详情形态不提供） -->
            <el-icon
              v-if="isDetailEnabled && entry.level >= 2"
              class="toc-pin"
              :title="isPinned(entry.key) ? '取消钉固' : '钉固到常用分组'"
              @click.stop="togglePin(entry)"
            >
              <el-icon class="toc-pin" :size="15">
                <StarFilled v-if="isPinned(entry.key)" />
                <Star v-else />
              </el-icon>
            </el-icon>
          </div>
          <p v-if="tocEntries.length === 0" class="toc-empty">本页暂无可定位的分组</p>
        </nav>
      </aside>

      <div class="settings-main">
        <!-- 待重启横幅：已落盘但需重启生效（用户选过「稍后重启」后常驻可见） -->
        <el-alert
          v-if="settingsStore.pendingRestart"
          type="warning"
          show-icon
          :closable="false"
          class="restart-banner"
        >
          <template #title>有已保存的配置需要重启服务才能生效</template>
          <el-button type="primary" @click="showRestartDialog = true">立即重启</el-button>
        </el-alert>

        <!-- 保存失败逐 key 定位：点击 key 切到所属文件并滚动高亮字段 -->
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

        <!-- 复合页头：搜索 + 高级开关 + 展开/收起 + 保存动作，单行收纳 -->
        <header class="settings-header">
          <div class="search-area">
            <el-input
              v-model="searchQuery"
              placeholder="搜索配置项（名称 / 描述 / 键名）..."
              :prefix-icon="Search"
              clearable
              class="search-input"
              @keydown.down.prevent="onSearchKeydown('down')"
              @keydown.up.prevent="onSearchKeydown('up')"
              @keydown.enter="onSearchKeydown('enter')"
              @keydown.esc="searchQuery = ''"
            />
            <!-- 搜索结果面板：命令面板式，点结果跳转定位（不再页内过滤渲染） -->
            <div v-if="searchQuery" class="search-panel">
              <template v-if="searchResults.hits.length > 0">
                <button
                  v-for="(hit, i) in searchResults.hits"
                  :key="hit.field.key"
                  type="button"
                  class="search-hit"
                  :class="{ active: i === searchHitIndex }"
                  @click="selectHit(hit)"
                  @mousemove="searchHitIndex = i"
                >
                  <span class="search-hit-title">{{ hit.field.label }}</span>
                  <span class="search-hit-path">
                    {{ searchHitPath(hit) }}
                  </span>
                </button>
                <div v-if="searchResults.total > searchResults.hits.length" class="search-more">
                  共 {{ searchResults.total }} 条命中，仅显示前 {{ searchResults.hits.length }} 条
                </div>
              </template>
              <div v-else class="search-empty">没有找到匹配的配置项</div>
            </div>
          </div>

          <!-- 移动端文件切换（桌面端走左侧导航） -->
          <el-select
            v-model="activeFileTab"
            class="file-select-mobile"
            size="default"
            @change="onMobileFileChange"
          >
            <el-option
              v-for="tab in FILE_TABS"
              :key="tab.key"
              :label="tab.label"
              :value="tab.key"
            />
          </el-select>

          <div class="header-actions">
            <!-- 显示模式：简洁 = 隐藏高级字段（本页目录同步隐藏全高级分组）；详细 = 全量 -->
            <el-radio-group v-model="displayMode" size="default">
              <el-radio-button value="simple">简洁模式</el-radio-button>
              <el-radio-button value="detailed">详细模式</el-radio-button>
            </el-radio-group>
            <!-- 全部展开/收起只作用于总览长页；详情视图单实体无需批量折叠 -->
            <el-button-group v-if="!isDetailMode" class="expand-group">
              <el-button size="default" @click="issueExpandCommand('expand')">
                <el-icon><ArrowDownBold /></el-icon>
                全部展开
              </el-button>
              <el-button size="default" @click="issueExpandCommand('collapse')">
                <el-icon><ArrowUpBold /></el-icon>
                全部收起
              </el-button>
            </el-button-group>
            <el-link
              v-if="settingsStore.hasChanges"
              type="primary"
              :underline="false"
              class="changes-panel-link"
              @click="showChangesPanel = true"
            >
              查看 {{ settingsStore.changeCount }} 项变更
            </el-link>
            <el-button :disabled="!settingsStore.hasChanges" @click="handleDiscard">
              <el-icon><RefreshLeft /></el-icon>
              重置
            </el-button>
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
          </div>
        </header>

        <!-- 滚动内容区：详情模式只渲染选中实体；总览/模型页渲染分区卡片长页 -->
        <div ref="scrollEl" class="settings-body" @scroll.passive="onScroll">
          <div class="section-cards">
            <!-- 实体详情模式：文件级元数据条 + 选中实体卡（含其全部子分组） -->
            <template v-if="isDetailMode && detailField">
              <ComponentCardList
                v-if="detailMetaFields.length > 0"
                :fields="detailMetaFields"
                :enabled-field-key="ENABLED_LIST_KEYS[detailSection?.key ?? ''] ?? null"
                :get-value="getFieldValue"
                :get-original="settingsStore.originalValueAt"
                :update-value="updateFieldValue"
                :get-change-count="getPendingChangeCount"
                :show-advanced="showAdvanced"
              />
              <div class="detail-header">
                <el-button link type="primary" @click="exitDetail">
                  <el-icon><ArrowLeft /></el-icon>
                  总览
                </el-button>
                <div class="detail-title-area">
                  <span class="detail-title">{{ detailField.label }}</span>
                  <span class="detail-key">{{ detailField.key }}</span>
                </div>
                <el-button-group>
                  <el-button size="small" :disabled="!canStepEntity(-1)" @click="stepEntity(-1)">
                    <el-icon><ArrowLeft /></el-icon>
                  </el-button>
                  <el-button size="small" :disabled="!canStepEntity(1)" @click="stepEntity(1)">
                    <el-icon><ArrowRight /></el-icon>
                  </el-button>
                </el-button-group>
              </div>
              <ComponentCardList
                :fields="[detailField]"
                :enabled-field-key="null"
                :forced-enabled="detailOwnerEnabled ?? true"
                :get-value="getFieldValue"
                :get-original="settingsStore.originalValueAt"
                :update-value="updateFieldValue"
                :get-change-count="getPendingChangeCount"
                :show-advanced="showAdvanced"
                :expand-target-key="expandTargetKey"
              />
            </template>

            <!-- 总览模式 / 模型专用页：分区卡片 -->
            <template v-else>
              <div
                v-for="section in getFileSections(activeFileTab)"
                :key="section.key"
                class="section-card"
                :data-config-key="section.key"
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
                      <p v-if="section.description" class="section-desc">
                        {{ section.description }}
                      </p>
                    </div>
                  </div>
                  <el-badge
                    v-if="getSectionChangeCount(section.key) > 0"
                    :value="getSectionChangeCount(section.key)"
                    type="warning"
                  />
                </div>
                <div class="section-fields">
                  <!-- 模型域：三层关系结构专用页（用途分配 → 模型注册 → 提供商接入），不支持实体详情形态 -->
                  <ModelConfigPage
                    v-if="section.key === 'model'"
                    :fields="getSectionFields(section)"
                    :get-value="getFieldValue"
                    :get-original="settingsStore.originalValueAt"
                    :update-value="updateFieldValue"
                    :get-change-count="getPendingChangeCount"
                    :show-advanced="showAdvanced"
                    :expand-command="cardExpandCommand"
                  />
                  <!-- 总览模式：实体卡列表（与详情模式共用卡片渲染） -->
                  <ComponentCardList
                    v-else
                    :fields="getSectionFields(section)"
                    :enabled-field-key="ENABLED_LIST_KEYS[section.key] ?? null"
                    :get-value="getFieldValue"
                    :get-original="settingsStore.originalValueAt"
                    :update-value="updateFieldValue"
                    :get-change-count="getPendingChangeCount"
                    :expand-command="cardExpandCommand"
                    :show-advanced="showAdvanced"
                    :expand-target-key="expandTargetKey"
                  />
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
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
        <el-button type="primary" :loading="appRestart.restarting" @click="handleRestart">
          立即重启
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
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
  ArrowDownBold,
  ArrowUpBold,
  Star,
  StarFilled,
  Close,
  Right,
  ArrowLeft,
  ArrowRight,
} from '@element-plus/icons-vue';
import { loadPinnedEntries, savePinnedEntries, type PinnedEntry } from '@/utils/settingsPins';
import { useSettingsStore } from '@/stores/settings';
import { useAppRestartStore } from '@/stores/appRestart';
import type { ConfigFieldSchema, ConfigGroupSchema } from '@/types/settings';
import {
  buildSearchResults,
  findFieldInSchema,
  findFieldValue,
  isAdvancedField,
  updateFieldValueImmutably,
  type SearchHit,
} from '@/utils/settingsSchema';
import ComponentCardList from '@/components/settings/ComponentCardList.vue';
import ModelConfigPage from '@/components/settings/ModelConfigPage.vue';

// ── 文件导航定义（v2：7 文件配置树） ──────────────────────
// 列表为前端硬编码（此处顺序即展示顺序）；后端 /config/schema 的 groups
// 自带 file_name/file_label，仅用于把分组归位到对应文件（groupsByFile），
// 文件列表本身不从 schema 动态推断。
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
    label: '虚拟形象',
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
const appRestart = useAppRestartStore();
const showRestartDialog = ref(false);
const searchQuery = ref('');
const activeFileTab = ref('agents.toml');
/** 高级字段总开关（true = 详细模式）：简洁模式浏览与目录都不含 advanced 内容 */
const showAdvanced = ref(false);
/** 显示模式双向映射：简洁模式 = showAdvanced false */
const displayMode = computed<'simple' | 'detailed'>({
  get: () => (showAdvanced.value ? 'detailed' : 'simple'),
  set: v => {
    showAdvanced.value = v === 'detailed';
  },
});
/** 最近一次保存失败的逐 key 错误；可点 key 跳转定位，关闭面板或再次保存时清除 */
const saveErrors = ref<{ key: string; message: string }[]>([]);
/** 变更清单面板（保存前 diff 预览 + 逐项撤销） */
const showChangesPanel = ref(false);
/** 滚动内容容器（scrollspy 与跳转定位都作用于此） */
const scrollEl = ref<HTMLElement | null>(null);
/** 侧栏目录当前高亮项（滚动跟随） */
const activeTocKey = ref('');
/** 跳转展开目标 key：组件链上祖先容器自动展开，null = 无目标 */
const expandTargetKey = ref<string | null>(null);
/** 总览目录条目的伪 key（详情模式下「返回长页」入口） */
const OVERVIEW_KEY = '__overview__';
/**
 * 详情模式的选中容器 key（实体或子分组）；null = 总览长页。
 * 模型页是专用三层结构，不支持详情形态，固定总览。
 */
const activeEntityKey = ref<string | null>(null);

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

// ── 实体详情模式 ──────────────────────────────────────────
const isDetailEnabled = computed(() => activeFileTab.value !== 'model.toml');

/** 详情模式判定：选中容器存在且当前文件支持详情形态（模型页固定总览） */
const isDetailMode = computed(
  () => isDetailEnabled.value && activeEntityKey.value !== null && detailField.value !== null,
);

/** 选中容器字段：跨当前文件各分区树查找（实体位于解包后的顶层或分类容器内） */
const detailField = computed<ConfigFieldSchema | null>(() => {
  if (!activeEntityKey.value || !isDetailEnabled.value) return null;
  for (const section of getFileSections(activeFileTab.value)) {
    const found = findFieldInSchema(getSectionFields(section), activeEntityKey.value);
    if (found) return found;
  }
  return null;
});

/** 选中容器所属分区（详情页头元数据条启用名单寻址用） */
const detailSection = computed<ConfigGroupSchema | null>(() => {
  if (!detailField.value) return null;
  for (const section of getFileSections(activeFileTab.value)) {
    if (findFieldInSchema(getSectionFields(section), detailField.value.key)) return section;
  }
  return null;
});

/** 详情视图顶部的文件级元数据条：所属分区顶层叶子（如「启用的 Agent」名单），选中实体自身除外 */
const detailMetaFields = computed<ConfigFieldSchema[]>(() => {
  const section = detailSection.value;
  if (!section) return [];
  return getSectionFields(section).filter(
    f => !f.children?.length && f.key !== activeEntityKey.value,
  );
});

/** 解析实体启用态：布尔 enabled 子字段优先，其次名单语义；无语义返回 null */
function resolveEntityEnabled(field: ConfigFieldSchema, scope: string): boolean | null {
  const leaf = field.children?.find(
    c => !c.children?.length && c.type === 'boolean' && c.key === `${field.key}.enabled`,
  );
  if (leaf) return getFieldValue(leaf.key) === true;
  const listKey = ENABLED_LIST_KEYS[scope];
  if (listKey) {
    const val = getFieldValue(listKey);
    const name = field.key.split('.').pop() ?? '';
    return Array.isArray(val) ? val.includes(name) : false;
  }
  return null;
}

/**
 * 详情内容的外层启用态：取目录树中选中实体的顶层（level-2）属主实体解析。
 * 子分组自身不在启用名单/无 enabled 子字段，按卡片默认逻辑会误判为禁用（整卡暗淡）。
 */
const detailOwnerEnabled = computed<boolean | null>(() => {
  const field = detailField.value;
  if (!field) return null;
  const scope = activeFileTab.value.replace(/\.toml$/, '');
  const owner = tocEntries.value.find(
    e => e.level === 2 && (e.key === field.key || field.key.startsWith(e.key + '.')),
  );
  if (!owner) return null;
  for (const section of getFileSections(activeFileTab.value)) {
    const found = findFieldInSchema(getSectionFields(section), owner.key);
    if (found) return resolveEntityEnabled(found, scope);
  }
  return null;
});

/** 详情模式的上一项/下一项序列：当前文件全部容器目录条目（树序），总览条目不参与 */
const detailableEntries = computed(() => tocEntries.value.filter(e => e.key !== OVERVIEW_KEY));

function canStepEntity(offset: number): boolean {
  const idx = detailableEntries.value.findIndex(e => e.key === activeEntityKey.value);
  if (idx === -1) return detailableEntries.value.length > 0;
  const next = idx + offset;
  return next >= 0 && next < detailableEntries.value.length;
}

function stepEntity(offset: number) {
  const list = detailableEntries.value;
  const idx = list.findIndex(e => e.key === activeEntityKey.value);
  const start = idx === -1 ? 0 : idx + offset;
  const next = list[Math.min(Math.max(start, 0), list.length - 1)];
  if (!next) return;
  activeEntityKey.value = next.key;
  activeTocKey.value = next.key;
  syncUrl(next.key);
  nextTick(() => scrollEl.value?.scrollTo({ top: 0 }));
}

function exitDetail() {
  activeEntityKey.value = null;
  activeTocKey.value = '';
  syncUrl(null);
  nextTick(() => {
    scrollEl.value?.scrollTo({ top: 0, behavior: 'smooth' });
    updateScrollSpy();
  });
}

// ── 侧栏本页目录 ──────────────────────────────────────────
interface TocEntry {
  key: string;
  label: string;
  /** 层级：1 = 分区标题，2 = 实体卡/顶层容器，3 = 实体内子分组 */
  level: 1 | 2 | 3;
  changeCount: number;
}

/**
 * 目录数据源与内容渲染同源（getSectionFields），实体/子分组条目与
 * 内容区的 data-config-key 锚点一一对应，滚动跟随据此反算当前位置。
 */
/** 容器在当前显示模式下是否还有可见内容（叶子看 advanced，容器递归；与子组件过滤同口径）。
 * 属主自身的 enabled 布尔叶子不计——它已升格为卡头开关，不算正文，全高级容器照常隐藏 */
function hasVisibleContent(field: ConfigFieldSchema): boolean {
  if (!field.children?.length) {
    return showAdvanced.value ? true : !field.advanced;
  }
  const enabledKey = `${field.key}.enabled`;
  return field.children.some(c => c.key !== enabledKey && hasVisibleContent(c));
}

const tocEntries = computed<TocEntry[]>(() => {
  const entries: TocEntry[] = [];
  for (const section of getFileSections(activeFileTab.value)) {
    if (isDetailEnabled.value) {
      // 详情形态的文件：分区头条目让位给「总览」入口（长页形态从这里回）
      entries.push({
        key: OVERVIEW_KEY,
        label: '总览',
        level: 1,
        changeCount: getSectionChangeCount(section.key),
      });
    } else {
      entries.push({
        key: section.key,
        label: section.label,
        level: 1,
        changeCount: getSectionChangeCount(section.key),
      });
    }
    for (const field of getSectionFields(section)) {
      if (!field.children?.length) continue;
      // 简洁模式下隐藏「全部为高级设置」的分组——点进去只看到空提示的条目没有意义
      if (!hasVisibleContent(field)) continue;
      entries.push({
        key: field.key,
        label: field.label || field.key,
        level: 2,
        changeCount: getPendingChangeCount(field.key),
      });
      for (const child of field.children) {
        if (!child.children?.length) continue;
        if (!hasVisibleContent(child)) continue;
        entries.push({
          key: child.key,
          label: child.label || child.key,
          level: 3,
          changeCount: getPendingChangeCount(child.key),
        });
      }
    }
  }
  return entries;
});

// 初始化：默认选中第一个非空文件
watch(
  () => settingsStore.groups,
  groups => {
    if (groups.length === 0) return;
    const hasCurrent = getFileSections(activeFileTab.value).length > 0;
    if (!hasCurrent) {
      const firstNonEmpty = FILE_TABS.find(t => getFileSections(t.key).length > 0);
      if (firstNonEmpty) activeFileTab.value = firstNonEmpty.key;
    }
  },
  { immediate: true },
);

// ── URL 深链与钉固 ────────────────────────────────────────
// ?key=<分组键> 深链：进入页面/外部改地址时定位到对应分组；
// 页内切换导航时用 router.replace 反向同步（不堆积历史栈）。
const route = useRoute();
const router = useRouter();
let lastSyncedKey: string | null | undefined;

function syncUrl(entityKey: string | null) {
  lastSyncedKey = entityKey;
  const query = { ...route.query };
  if (entityKey) {
    query.key = entityKey;
  } else {
    delete query.key;
  }
  void router.replace({ query }).catch(() => {});
}

// 外部地址变化（粘贴新链接/浏览器前进后退）触发定位；页内自己写入的值跳过
watch(
  () => route.query.key,
  k => {
    const key = typeof k === 'string' && k ? k : null;
    if (key === lastSyncedKey) return;
    lastSyncedKey = key;
    if (key) void jumpToField(key);
  },
);

/** 钉固条目：localStorage 持久化的跨文件常用分组 */
const pinnedEntries = ref<PinnedEntry[]>(loadPinnedEntries());

function persistPins() {
  savePinnedEntries(pinnedEntries.value);
}

function isPinned(key: string): boolean {
  return pinnedEntries.value.some(p => p.key === key);
}

function togglePin(entry: TocEntry) {
  const fileKey = `${entry.key.split('.')[0]!}.toml`;
  if (isPinned(entry.key)) {
    pinnedEntries.value = pinnedEntries.value.filter(p => p.key !== entry.key);
  } else {
    pinnedEntries.value = [...pinnedEntries.value, { key: entry.key, label: entry.label, fileKey }];
  }
  persistPins();
}

function removePin(key: string) {
  pinnedEntries.value = pinnedEntries.value.filter(p => p.key !== key);
  persistPins();
}

/** 打开钉固条目：切到所属文件并进入详情（模型页无详情形态，退化为锚点滚动） */
async function openPinned(p: PinnedEntry) {
  if (activeFileTab.value !== p.fileKey) {
    switchFile(p.fileKey);
  }
  if (!isDetailEnabled.value) {
    await nextTick();
    scrollEl.value
      ?.querySelector(`[data-config-key="${p.key}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  activeEntityKey.value = p.key;
  activeTocKey.value = p.key;
  syncUrl(p.key);
  await nextTick();
  scrollEl.value?.scrollTo({ top: 0 });
}

// 加载配置；URL 带深链时定位到对应分组
onMounted(async () => {
  await settingsStore.fetchSchema();
  nextTick(updateScrollSpy);
  const deepKey = route.query.key;
  if (typeof deepKey === 'string' && deepKey) {
    lastSyncedKey = deepKey;
    void jumpToField(deepKey);
  }
});

// 切换文件后回到总览形态、回到顶部并重算目录高亮
function switchFile(fileKey: string) {
  if (activeFileTab.value === fileKey) return;
  activeFileTab.value = fileKey;
  activeEntityKey.value = null;
  activeTocKey.value = '';
  syncUrl(null);
  nextTick(() => {
    scrollEl.value?.scrollTo({ top: 0 });
    updateScrollSpy();
  });
}

// 移动端下拉切换：v-model 已更新 activeFileTab，此处只负责复位详情/滚动与目录高亮
function onMobileFileChange() {
  activeEntityKey.value = null;
  activeTocKey.value = '';
  syncUrl(null);
  nextTick(() => {
    scrollEl.value?.scrollTo({ top: 0 });
    updateScrollSpy();
  });
}

// ── 滚动跟随（scrollspy） ─────────────────────────────────
let spyRaf = 0;

function onScroll() {
  if (spyRaf) return;
  spyRaf = requestAnimationFrame(() => {
    spyRaf = 0;
    updateScrollSpy();
  });
}

/**
 * 目录条目与 DOM 锚点按出现顺序一致：取视口上缘以上最后一个锚点为当前项。
 * 详情模式下跳过选中实体之前的条目（它们不在 DOM 里），并把当前实体兜底高亮——
 * 详情页头把实体卡压到视口 32px 以下时锚点不过线，不能让目录空无高亮。
 */
function updateScrollSpy() {
  const container = scrollEl.value;
  if (!container) return;
  const containerTop = container.getBoundingClientRect().top;
  let current = '';
  let inScope = activeEntityKey.value === null;
  for (const entry of tocEntries.value) {
    if (!inScope) {
      if (entry.key === activeEntityKey.value) inScope = true;
      else continue;
    }
    const el = container.querySelector(`[data-config-key="${entry.key}"]`);
    if (!el) continue;
    const top = el.getBoundingClientRect().top - containerTop;
    if (top <= 32) {
      current = entry.key;
    } else {
      break;
    }
  }
  if (activeEntityKey.value && !current) {
    current = activeEntityKey.value;
  } else if (!current && !activeEntityKey.value) {
    // 总览页顶部（无锚点过线）回落到「总览」条目
    current = OVERVIEW_KEY;
  }
  activeTocKey.value = current;
}

/** 侧栏目录点击：容器条目进入详情视图（详情形态文件），模型页或总览条目按锚点滚动 */
async function scrollToTocEntry(key: string) {
  if (key === OVERVIEW_KEY) {
    activeEntityKey.value = null;
    activeTocKey.value = OVERVIEW_KEY;
    syncUrl(null);
    await nextTick();
    scrollEl.value?.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  if (!isDetailEnabled.value) {
    // 模型专用页：保持锚点滚动定位
    activeTocKey.value = key;
    syncUrl(null);
    await nextTick();
    const el = scrollEl.value?.querySelector(`[data-config-key="${key}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  activeEntityKey.value = key;
  // 置顶后多数情况不触发 scroll 事件，乐观设置当前项，滚动事件再行校正
  activeTocKey.value = key;
  syncUrl(key);
  await nextTick();
  scrollEl.value?.scrollTo({ top: 0 });
}

// ── 全局搜索面板 ──────────────────────────────────────────
const searchResults = computed(() => buildSearchResults(settingsStore.groups, searchQuery.value));
const searchHitIndex = ref(-1);

watch(searchQuery, () => {
  searchHitIndex.value = -1;
});

function onSearchKeydown(action: 'down' | 'up' | 'enter') {
  const hits = searchResults.value.hits;
  if (action === 'down') {
    searchHitIndex.value = Math.min(searchHitIndex.value + 1, hits.length - 1);
  } else if (action === 'up') {
    searchHitIndex.value = Math.max(searchHitIndex.value - 1, 0);
  } else if (action === 'enter' && searchHitIndex.value >= 0 && hits[searchHitIndex.value]) {
    selectHit(hits[searchHitIndex.value]!);
  }
}

/** 面板行副标题：文件 › 分组链 › 键名尾段 */
function searchHitPath(hit: SearchHit): string {
  const fileLabel = getFileTabLabel(hit.fileKey);
  const tail = hit.field.key.split('.').pop() ?? hit.field.key;
  return [fileLabel, ...hit.path, tail].filter(Boolean).join(' › ');
}

function selectHit(hit: SearchHit) {
  searchQuery.value = '';
  jumpToField(hit.field.key);
}

// ── 图标 ──────────────────────────────────────────────────
function getIcon(iconName?: string) {
  if (!iconName) return Setting;
  return iconMap[iconName] || Setting;
}

// 文件名 → 导航标签：用于搜索结果行展示命中所属的真实文件
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
onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload);
  if (spyRaf) cancelAnimationFrame(spyRaf);
});

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
  return findFieldValue(settingsStore.currentValues, key);
}

function updateFieldValue(field: ConfigFieldSchema, value: unknown) {
  const newValues = updateFieldValueImmutably(settingsStore.currentValues, field.key, value);
  settingsStore.updateCurrentValues(newValues);
  settingsStore.applyFieldChange(field, value, settingsStore.originalValueAt(field.key));
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

/** 先清再设展开目标：同一 key 连续跳转也能重新触发组件链的 watch */
async function triggerExpandTarget(key: string) {
  expandTargetKey.value = null;
  await nextTick();
  expandTargetKey.value = key;
}

/**
 * 统一跳转定位：切文件、开高级开关、进入目标所属容器的详情视图、滚动居中并闪烁高亮。
 * 搜索面板、保存失败清单、变更清单三处入口共用。
 * 详情实体取「目录树中含目标的最深容器条目」——跳到 minecraft.context.max_tokens
 * 直接进「上下文」详情，页面最短；顶层叶子（如启用名单）留在总览定位。
 */
async function jumpToField(key: string) {
  const fileKey = `${key.split('.')[0]}.toml`;
  if (FILE_TABS.some(t => t.key === fileKey)) {
    if (activeFileTab.value !== fileKey) {
      switchFile(fileKey);
    }
  } else {
    return;
  }
  if (!showAdvanced.value && isAdvancedFieldInGroups(key)) {
    showAdvanced.value = true;
  }
  if (isDetailEnabled.value) {
    const ancestors = tocEntries.value.filter(
      e => e.level >= 2 && (key === e.key || key.startsWith(e.key + '.')),
    );
    const deepest = ancestors[ancestors.length - 1];
    activeEntityKey.value = deepest ? deepest.key : null;
    syncUrl(activeEntityKey.value);
  } else {
    activeEntityKey.value = null;
  }
  await triggerExpandTarget(key);
  await nextTick();
  const el = scrollEl.value?.querySelector(`[data-config-key="${key}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const target = el as HTMLElement;
  target.classList.remove('field-flash');
  void target.offsetWidth; // 强制重排以重启动画，同一字段连续点击也能再次闪烁
  target.classList.add('field-flash');
  window.setTimeout(() => target.classList.remove('field-flash'), 1600);
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

/** 在 schema 全字段树中按 key 查找并判断是否高级字段（查找逻辑见 utils/settingsSchema.ts） */
function isAdvancedFieldInGroups(key: string): boolean {
  return isAdvancedField(key, settingsStore.groups);
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

/** 重启走全局重启流程（遮罩 + 健康检查），成功失败均由 store 内提示，本页只负责收起对话框 */
async function handleRestart() {
  showRestartDialog.value = false;
  await appRestart.beginRestart();
}
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
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

/* ── 两栏主体 ──────────────────────────────────────────── */
.settings-shell {
  flex: 1;
  display: flex;
  gap: var(--spacing-md);
  overflow: hidden;
}

/* ── 侧栏：文件导航 + 本页目录 ─────────────────────────── */
.settings-sidebar {
  width: 224px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.sidebar-brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--spacing-md) var(--spacing-md) var(--spacing-sm);
}

.sidebar-brand-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.sidebar-brand-sub {
  font-size: 12px;
  color: var(--text-secondary);
}

.file-nav {
  display: flex;
  flex-direction: column;
  padding: 0 var(--spacing-sm);
}

.file-nav-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  width: 100%;
  padding: 7px var(--spacing-sm);
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background var(--transition-fast);
}

.file-nav-item:hover {
  background: var(--bg-hover);
}

.file-nav-item.active {
  background: var(--color-primary-light-9);
  /* 左侧色条：比底色更显眼的选中指示 */
  box-shadow: inset 3px 0 0 0 var(--color-primary);
}

.file-nav-item.active .file-nav-label {
  color: var(--color-primary);
  font-weight: 600;
}

.file-nav-icon {
  font-size: 15px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.file-nav-item.active .file-nav-icon {
  color: var(--color-primary);
}

.file-nav-label {
  font-size: 13px;
  color: var(--text-regular);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-nav-restart {
  font-size: 13px;
  color: var(--color-warning);
  flex-shrink: 0;
}

.sidebar-divider {
  height: 1px;
  background: var(--border-color-light);
  margin: var(--spacing-sm) var(--spacing-md);
}

.toc-title {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 0 var(--spacing-md) var(--spacing-xs);
  letter-spacing: 0.05em;
}

.toc {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 0 var(--spacing-sm) var(--spacing-sm);
}

.toc-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  width: 100%;
  padding: 5px var(--spacing-sm);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background var(--transition-fast);
}

.toc-item:hover {
  background: var(--bg-hover);
}

.toc-item.active {
  background: var(--color-primary-light-9);
  box-shadow: inset 2px 0 0 0 var(--color-primary);
}

.toc-item.active .toc-label {
  color: var(--color-primary);
  font-weight: 600;
}

.toc-item--l1 {
  padding-left: var(--spacing-sm);
}

.toc-item--l1 .toc-label {
  font-weight: 600;
  color: var(--text-primary);
}

.toc-item--l2 {
  padding-left: calc(var(--spacing-sm) + 14px);
}

.toc-item--l3 {
  padding-left: calc(var(--spacing-sm) + 28px);
}

.toc-label {
  font-size: 12px;
  color: var(--text-regular);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toc-empty {
  font-size: 12px;
  color: var(--text-secondary);
  padding: var(--spacing-sm);
  margin: 0;
}

/* ── 常用分组（钉固） ──────────────────────────────────── */
.pinned-list {
  display: flex;
  flex-direction: column;
  padding: 0 var(--spacing-sm) var(--spacing-xs);
}

.pinned-item {
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}

.pinned-item:hover {
  background: var(--bg-hover);
}

.pinned-open {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex: 1;
  min-width: 0;
  padding: 4px var(--spacing-xs) 4px var(--spacing-sm);
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.pinned-star {
  font-size: 12px;
  color: var(--color-warning);
  flex-shrink: 0;
}

.pinned-label {
  font-size: 12px;
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pinned-remove {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px;
  margin-right: 2px;
  flex-shrink: 0;
  opacity: 0;
  cursor: pointer;
  transition: opacity var(--transition-fast);
}

.pinned-item:hover .pinned-remove {
  opacity: 1;
}

.pinned-remove:hover {
  color: var(--color-danger);
}

/* 目录条目：包一层便于悬停露出图钉 */
.toc-entry {
  display: flex;
  align-items: center;
}

.toc-entry > .toc-item {
  flex: 1;
  min-width: 0;
}

.toc-pin {
  color: var(--text-secondary);
  padding: 2px;
  flex-shrink: 0;
  opacity: 0.35;
  cursor: pointer;
  transition:
    opacity var(--transition-fast),
    color var(--transition-fast);
}

.toc-entry:hover .toc-pin {
  opacity: 1;
}

.toc-pin:hover {
  color: var(--color-warning);
}

.toc-entry--pinned .toc-pin {
  opacity: 1;
  color: var(--color-warning);
}

/* ── 主列：横幅 + 复合页头 + 滚动内容 ──────────────────── */
.settings-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.restart-banner,
.save-error-alert {
  flex-shrink: 0;
  margin-bottom: var(--spacing-sm);
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

.settings-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  margin-bottom: var(--spacing-md);
}

.search-area {
  position: relative;
  flex: 1;
  min-width: 0;
  max-width: 480px;
}

.search-input {
  width: 100%;
}

.search-panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 420px;
  overflow-y: auto;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 30;
  padding: var(--spacing-xs);
}

.search-hit {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 7px var(--spacing-sm);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.search-hit.active {
  background: var(--color-primary-light-9);
}

.search-hit-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.search-hit-path {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-more,
.search-empty {
  padding: var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
}

/* 移动端文件切换：桌面隐藏 */
.file-select-mobile {
  display: none;
  width: 160px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.expand-group {
  display: inline-flex;
}

.changes-panel-link {
  font-size: 13px;
}

/* ── 滚动内容区 ────────────────────────────────────────── */
.settings-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md) var(--spacing-md) var(--spacing-lg);
}

.section-cards {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  max-width: 1600px;
  margin: 0 auto;
}

.section-card {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
  scroll-margin-top: var(--spacing-md);
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

/* 子组件根以外的锚点元素（sub-group 分组标题行）也保留定位呼吸空间 */
.settings-body :deep(.sub-group) {
  scroll-margin-top: var(--spacing-md);
}

/* ── 实体详情视图 ──────────────────────────────────────── */
.detail-view {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
}

.detail-title-area {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  min-width: 0;
  flex: 1;
}

.detail-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.detail-key {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.settings-body::-webkit-scrollbar,
.toc::-webkit-scrollbar,
.search-panel::-webkit-scrollbar {
  width: 6px;
}

.settings-body::-webkit-scrollbar-track,
.toc::-webkit-scrollbar-track,
.search-panel::-webkit-scrollbar-track {
  background: transparent;
}

.settings-body::-webkit-scrollbar-thumb,
.toc::-webkit-scrollbar-thumb,
.search-panel::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.settings-body::-webkit-scrollbar-thumb:hover,
.toc::-webkit-scrollbar-thumb:hover,
.search-panel::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* ── 响应式 ────────────────────────────────────────────── */
@media (max-width: 768px) {
  .settings-shell {
    gap: var(--spacing-sm);
  }

  .settings-sidebar {
    display: none;
  }

  .file-select-mobile {
    display: block;
  }

  .settings-header {
    flex-wrap: wrap;
  }

  .header-actions {
    flex-wrap: wrap;
  }

  .section-header {
    padding: var(--spacing-sm) var(--spacing-md);
  }

  .section-fields {
    padding: var(--spacing-xs) var(--spacing-md);
  }
}
</style>

<!-- 跳转闪烁高亮作用于子组件深层节点（无本组件 scope id），放非 scoped 块保证命中 -->
<style>
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
</style>
