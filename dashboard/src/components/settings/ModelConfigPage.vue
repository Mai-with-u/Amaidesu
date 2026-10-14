<template>
  <div class="model-config-page">
    <!-- ── Provider 节：每 provider 一节，节内自身字段 + 名下模型子卡 ── -->
    <div
      v-for="entry in providerEntries"
      :key="entry.index"
      class="entity-section"
      :class="{ 'is-disabled': providerEnabledKey !== null && isProviderEnabled(entry.item) === false }"
      :data-config-key="PROVIDERS_KEY"
    >
      <div class="entity-section-header" @click="toggleProviderExpand(entry.index)">
        <el-icon class="entity-section-toggle">
          <ArrowRight v-if="providerExpanded[entry.index] === false" />
          <ArrowDown v-else />
        </el-icon>
        <el-icon class="entity-section-icon"><Connection /></el-icon>
        <span class="entity-section-title">{{ itemDisplayName(entry.item) }}</span>
        <el-tag v-if="itemDisplayName(entry.item) === ''" size="small" type="info">未命名</el-tag>
        <el-tag v-if="isProviderDirty(entry.index)" size="small" type="warning" effect="plain">
          已修改
        </el-tag>
        <div class="entity-section-actions" @click.stop>
          <!-- 节头 enabled 开关：与 ComponentCard 同视觉，仍写 provider 条目的布尔子字段 -->
          <el-switch
            v-if="providerEnabledKey"
            :model-value="isProviderEnabled(entry.item)"
            size="small"
            @change="toggleProviderEnabled(entry.index, $event as boolean)"
          />
          <el-button
            type="danger"
            size="small"
            text
            :icon="Delete"
            @click="removeProvider(entry.index)"
          />
        </div>
      </div>

      <div v-if="providerExpanded[entry.index] !== false" class="entity-section-body">
        <!-- 身份字段（name）：专用输入，失焦/回车提交——改名触发引用守护，
             不能逐键提交（逐键弹窗+级联会把中间态名字写进引用方） -->
        <div class="name-field">
          <label class="name-field-label">
            提供商名称<span class="required-mark">*</span>
            <el-tooltip content="provider 唯一名称，被 model.api_provider 引用；改名时可级联更新引用方" placement="top">
              <el-icon class="description-icon"><InfoFilled /></el-icon>
            </el-tooltip>
          </label>
          <el-input
            :model-value="str(entry.item.name)"
            placeholder="provider 唯一名称"
            @change="commitProviderName(entry.index, $event)"
          />
        </div>

        <!-- 其余常规字段：叶字段一律复用 FieldRenderer（合成 key，整值写回） -->
        <div class="field-grid">
          <FieldRenderer
            v-for="sub in normalFields(providerItemFields, providerExcludedKeys)"
            :key="`${PROVIDERS_KEY}.${entry.index}.${sub.key}`"
            :field="makeItemField(sub, PROVIDERS_KEY, entry.index)"
            :model-value="itemValue(entry.item, sub.key)"
            :original-value="originalItemValue(PROVIDERS_KEY, entry.index, sub.key)"
            @update:model-value="updateProviderField(entry.index, sub, $event)"
          />
        </div>

        <!-- 高级字段：跟随页面高级总开关（与 SubFieldGroup 行为一致） -->
        <details v-if="showAdvanced && advancedFields(providerItemFields, providerExcludedKeys).length > 0" class="advanced-block">
          <summary class="advanced-toggle">高级参数（{{ advancedFields(providerItemFields, providerExcludedKeys).length }}）</summary>
          <div class="field-grid">
            <FieldRenderer
              v-for="sub in advancedFields(providerItemFields, providerExcludedKeys)"
              :key="`${PROVIDERS_KEY}.${entry.index}.${sub.key}`"
              :field="makeItemField(sub, PROVIDERS_KEY, entry.index)"
              :model-value="itemValue(entry.item, sub.key)"
              :original-value="originalItemValue(PROVIDERS_KEY, entry.index, sub.key)"
              @update:model-value="updateProviderField(entry.index, sub, $event)"
            />
          </div>
        </details>

        <!-- 名下模型：按 model.api_provider == provider.name 派生分组（底层数据仍是扁平数组） -->
        <div class="owned-models">
          <div class="owned-models-header">
            <span class="owned-models-title">该提供商下的模型（{{ modelsOf(entry.item).length }}）</span>
            <el-button type="primary" size="small" :icon="Plus" @click="addModel(str(entry.item.name))">
              添加模型
            </el-button>
          </div>
          <p v-if="modelsOf(entry.item).length === 0" class="empty-hint">暂无模型</p>
          <ModelItemCard
            v-for="m in modelsOf(entry.item)"
            :key="m.index"
            :entry="m"
            :provider-names="providerNames"
            :model-item-fields="modelItemFields"
            :show-advanced="showAdvanced"
            :get-value="getValue"
            :get-original="getOriginal"
            @update-field="updateModelField(m.index, $event.relKey, $event.value)"
            @commit-name="commitModelName(m.index, $event)"
            @remove="removeModel(m.index)"
          />
        </div>
      </div>
    </div>

    <!-- 页面级添加提供商 -->
    <div class="page-actions">
      <el-button type="primary" :icon="Plus" @click="addProvider">添加提供商</el-button>
    </div>

    <!-- 悬空模型：api_provider 指向不存在/已删除的 provider，不丢不藏 -->
    <div v-if="unassigned.length > 0" class="entity-section unassigned-section">
      <div class="entity-section-header">
        <el-icon class="entity-section-icon unassigned-icon"><WarningFilled /></el-icon>
        <span class="entity-section-title">未归属的模型（{{ unassigned.length }}）</span>
        <span class="unassigned-hint">api_provider 指向不存在的提供商</span>
      </div>
      <div class="entity-section-body">
        <ModelItemCard
          v-for="m in unassigned"
          :key="m.index"
          :entry="m"
          :provider-names="providerNames"
          :model-item-fields="modelItemFields"
          :show-advanced="showAdvanced"
          :get-value="getValue"
          :get-original="getOriginal"
          @update-field="updateModelField(m.index, $event.relKey, $event.value)"
          @commit-name="commitModelName(m.index, $event)"
          @remove="removeModel(m.index)"
        />
      </div>
    </div>

    <!-- ── Profile 节：固定用途集合，model_list 为已注册模型名的多选 ── -->
    <div
      v-for="profile in profileEntries"
      :key="profile.key"
      class="entity-section"
      :data-config-key="profile.key"
    >
      <div class="entity-section-header" @click="toggleProfileExpand(profile.key)">
        <el-icon class="entity-section-toggle">
          <ArrowRight v-if="profileExpanded[profile.key] === false" />
          <ArrowDown v-else />
        </el-icon>
        <el-icon class="entity-section-icon"><Collection /></el-icon>
        <span class="entity-section-title">{{ profileLabel(profile.key) }}</span>
        <el-tag v-if="getChangeCount(`${profile.key}.model_list`) > 0" size="small" type="warning" effect="plain">
          已修改
        </el-tag>
      </div>
      <div v-if="profileExpanded[profile.key] !== false" class="entity-section-body">
        <!-- 结构性绑定：model_list 用 el-select multiple（选项 = 已注册模型名），
             仍写数组叶字段，走同一条逐 key 更新路径 -->
        <div class="model-list-field" :data-config-key="profile.modelListField?.key">
          <label class="name-field-label">模型列表（按顺序选择）</label>
          <el-select
            :model-value="profile.modelList"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择已注册模型，可多个"
            style="width: 100%"
            @update:model-value="updateModelList(profile, $event as string[])"
          >
            <el-option v-for="name in modelNames" :key="name" :label="name" :value="name" />
          </el-select>
          <p class="field-hint">候选为 llm_models 注册表中的模型名；可手敲尚未注册的名字（悬空引用会走 schema 校验）</p>
        </div>

        <!-- profile 其余子字段：真实 key，直接复用 SubFieldGroup -->
        <SubFieldGroup
          v-if="profile.otherChildren.length > 0"
          :fields="profile.otherChildren"
          :get-value="getValue"
          :get-original="getOriginal"
          :update-value="updateValue"
          :get-change-count="getChangeCount"
          :show-advanced="showAdvanced"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 模型域专用配置页
 *
 * model.toml 的三类数据是相互引用的关系结构（provider ← model.api_provider、
 * model ← profile.model_list），通用卡片列表按扁平字段呈现这条关系是断裂的；
 * 本组件按"provider 节 → 名下模型子卡 → profile 节"表达关系层，叶字段编辑
 * 一律复用 FieldRenderer / SubFieldGroup（同一条 store 更新路径），不 fork
 * 第二条保存/脏跟踪路径。数组整值写回（与 ArrayEditor 同机制），provider/model
 * 改名经引用守护确认后级联更新引用方（每条引用更新是独立变更条目或数组整值条目）。
 */
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  ArrowDown,
  ArrowRight,
  Connection,
  Collection,
  Delete,
  Plus,
  WarningFilled,
  InfoFilled,
} from '@element-plus/icons-vue';
import FieldRenderer from './FieldRenderer.vue';
import SubFieldGroup from './SubFieldGroup.vue';
import ModelItemCard from './ModelItemCard.vue';
import { confirmAction } from '@/utils/confirmAction';
import type { ConfigFieldSchema } from '@/types/settings';
import {
  MODELS_KEY,
  PROVIDERS_KEY,
  PROFILES_CONTAINER_KEY,
  type ConfigItem,
  applyModelRenameToProfileLists,
  applyProviderRenameToModels,
  buildDefaultItem,
  diffCountAgainst,
  groupModelsByProvider,
  makeItemField,
  modelNameReferences,
  profileLabel,
  providerNameReferences,
  uniqueName,
} from '@/utils/modelConfig';

const props = defineProps<{
  /** model 分组的顶层字段（含 llm_providers / llm_models 数组与 llm_profiles 容器） */
  fields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  getChangeCount: (key: string) => number;
  showAdvanced: boolean;
  /** 页头"全部展开/收起"命令（与 ComponentCardList 同源），seq 变化即执行 */
  expandCommand?: { action: 'expand' | 'collapse'; seq: number } | null;
}>();

// ── Schema 定位 ──────────────────────────────────────────
const providersField = computed(() => props.fields.find(f => f.key === PROVIDERS_KEY));
const modelsField = computed(() => props.fields.find(f => f.key === MODELS_KEY));
const profilesContainer = computed(() => props.fields.find(f => f.key === PROFILES_CONTAINER_KEY));
const providerItemFields = computed(() => providersField.value?.items?.fields ?? []);
const modelItemFields = computed(() => modelsField.value?.items?.fields ?? []);

function str(v: unknown): string {
  return String(v ?? '');
}

function itemValue(item: ConfigItem | undefined, relKey: string): unknown {
  return item?.[relKey];
}

function itemDisplayName(item: ConfigItem | undefined): string {
  return str(itemValue(item, 'name'));
}

/** 数组第 index 项的原始值（合成 key 可被 store 的点分遍历读取，数组下标按字符串寻址） */
function originalItemValue(arrayKey: string, index: number, relKey: string): unknown {
  return props.getOriginal(`${arrayKey}.${index}.${relKey}`);
}

/** 叶字段拆分：identity/enabled 字段走节头专用控件（结构性绑定），其余进 FieldRenderer */
function normalFields(fields: ConfigFieldSchema[], excluded: string[]): ConfigFieldSchema[] {
  return fields.filter(f => !excluded.includes(f.key) && !f.advanced);
}

// ── 节折叠与 provider 启用开关（沿用 ComponentCard 节卡视觉） ──
// 折叠语义：undefined = 展开（默认全展开，与节卡约定一致），false = 收起，true = 展开
const providerExpanded = ref<Record<number, boolean>>({});
const profileExpanded = ref<Record<string, boolean>>({});

function toggleProviderExpand(index: number) {
  providerExpanded.value[index] = providerExpanded.value[index] === false;
}

function toggleProfileExpand(key: string) {
  profileExpanded.value[key] = profileExpanded.value[key] === false;
}

// 页头"全部展开/收起"：expand 清空显式收起记录（undefined 即展开），collapse 对现存节全量置 false
watch(
  () => props.expandCommand?.seq,
  () => {
    const cmd = props.expandCommand;
    if (!cmd) return;
    if (cmd.action === 'expand') {
      providerExpanded.value = {};
      profileExpanded.value = {};
      return;
    }
    const collapsedProviders: Record<number, boolean> = {};
    providerEntries.value.forEach(entry => {
      collapsedProviders[entry.index] = false;
    });
    providerExpanded.value = collapsedProviders;
    const collapsedProfiles: Record<string, boolean> = {};
    profileEntries.value.forEach(profile => {
      collapsedProfiles[profile.key] = false;
    });
    profileExpanded.value = collapsedProfiles;
  },
);

/** provider 条目的布尔 enabled 子字段（存在才在节头渲染开关，节体不再重复渲染） */
const providerEnabledKey = computed(() => {
  const f = providerItemFields.value.find(
    f => f.key === 'enabled' && f.type === 'boolean' && !f.children?.length,
  );
  return f?.key ?? null;
});

/** 节头字段排除名单：name（专用改名输入）+ enabled（节头开关接管时） */
const providerExcludedKeys = computed(() =>
  providerEnabledKey.value ? ['name', providerEnabledKey.value] : ['name'],
);

function isProviderEnabled(item: ConfigItem | undefined): boolean {
  return item?.[providerEnabledKey.value ?? ''] === true;
}

function toggleProviderEnabled(index: number, val: boolean) {
  if (providerEnabledKey.value) {
    setItemField(providersField.value, index, providerEnabledKey.value, val);
  }
}

function advancedFields(fields: ConfigFieldSchema[], excluded: string[]): ConfigFieldSchema[] {
  return fields.filter(f => !excluded.includes(f.key) && f.advanced === true);
}

// ── 数组读写（整值写回，与 ArrayEditor 同机制） ──────────
function readArray(field: ConfigFieldSchema | undefined): ConfigItem[] {
  if (!field) return [];
  const v = props.getValue(field.key);
  return Array.isArray(v) ? (v as ConfigItem[]) : [];
}

function writeArray(field: ConfigFieldSchema | undefined, arr: ConfigItem[]) {
  if (field) props.updateValue(field, arr);
}

function setItemField(
  field: ConfigFieldSchema | undefined,
  index: number,
  relKey: string,
  value: unknown,
) {
  if (!field) return;
  const arr = [...readArray(field)];
  if (!arr[index] || typeof arr[index] !== 'object') return;
  arr[index] = { ...arr[index], [relKey]: value };
  writeArray(field, arr);
}

// ── 数据视图 ─────────────────────────────────────────────
const providers = computed(() => readArray(providersField.value));
const models = computed(() => readArray(modelsField.value));
const providerEntries = computed(() =>
  providers.value.map((item, index) => ({ index, item })),
);
const providerNames = computed(() =>
  [...new Set(providers.value.map(p => itemDisplayName(p)).filter(n => n !== ''))],
);
const modelNames = computed(() =>
  models.value.map(m => itemDisplayName(m)).filter(n => n !== ''),
);
const grouped = computed(() => groupModelsByProvider(models.value, providers.value));
function modelsOf(providerItem: ConfigItem): { index: number; model: ConfigItem }[] {
  return grouped.value.byProvider.get(itemDisplayName(providerItem)) ?? [];
}

const unassigned = computed(() => grouped.value.unassigned);
const profileEntries = computed(() =>
  (profilesContainer.value?.children ?? []).map(profile => {
    const modelListField = profile.children?.find(c => c.key === `${profile.key}.model_list`);
    const modelList = props.getValue(`${profile.key}.model_list`);
    return {
      key: profile.key,
      modelListField,
      modelList: Array.isArray(modelList) ? (modelList as string[]) : [],
      otherChildren: (profile.children ?? []).filter(c => c !== modelListField),
    };
  }),
);

// ── 脏标记 ───────────────────────────────────────────────
/** provider 节脏标记：数组当前值与原始值不等即有未保存变更（整值写回，粒度为节） */
function isProviderDirty(index: number): boolean {
  const original = props.getOriginal(PROVIDERS_KEY);
  const current = readArray(providersField.value)[index];
  if (!Array.isArray(original) || !current) return false;
  return diffCountAgainst(original[index] as ConfigItem, current) > 0;
}

// 模型子卡的脏标记在 ModelItemCard 内按原始值逐字段计算（diffCountAgainst）

// ── 常规字段更新（FieldRenderer 出口统一走整值写回） ────
function updateProviderField(index: number, sub: ConfigFieldSchema, value: unknown) {
  setItemField(providersField.value, index, sub.key, value);
}

function updateModelField(index: number, relKey: string, value: unknown) {
  setItemField(modelsField.value, index, relKey, value);
}

// ── 引用守护与改名级联 ──────────────────────────────────
/**
 * provider 改名提交（失焦/回车触发）：先写名字，再算引用方弹确认；
 * 确认级联后指向旧名的模型经 llm_models 数组整值条目更新（变更清单可见、可整体撤销）。
 */
async function commitProviderName(index: number, input: string) {
  const arr = [...providers.value];
  const current = arr[index];
  if (!current || typeof current !== 'object') return;
  const oldName = itemDisplayName(current);
  const newName = input;
  if (newName === oldName) return;
  arr[index] = { ...current, name: newName };
  writeArray(providersField.value, arr);
  if (oldName === '') return;
  const referencing = providerNameReferences(models.value, oldName);
  if (referencing.length === 0) return;
  const ok = await confirmAction(
    `提供商「${oldName}」改名为「${newName}」，有 ${referencing.length} 个模型通过 api_provider 引用它：\n${referencing.join('、')}\n\n是否同步更新这些引用？`,
    '检测到引用该提供商的模型',
    { confirmButtonText: '级联更新引用', cancelButtonText: '仅改提供商名' },
  );
  if (ok) {
    writeArray(modelsField.value, applyProviderRenameToModels(models.value, oldName, newName));
    ElMessage.success(`已级联更新 ${referencing.length} 个模型的 api_provider`);
  }
}

/**
 * model 改名提交：每个 model_list 含旧名的 profile 是独立的数组叶 key，
 * 级联更新各自走逐 key 更新路径——变更清单逐条可见、可逐条撤销。
 */
async function commitModelName(index: number, input: string) {
  const arr = [...models.value];
  const current = arr[index];
  if (!current || typeof current !== 'object') return;
  const oldName = itemDisplayName(current);
  const newName = input;
  if (newName === oldName) return;
  arr[index] = { ...current, name: newName };
  writeArray(modelsField.value, arr);
  if (oldName === '') return;
  const entries = profileEntries.value.map(p => ({ key: p.key, modelList: p.modelList }));
  const referencing = modelNameReferences(entries, oldName);
  if (referencing.length === 0) return;
  const ok = await confirmAction(
    `模型「${oldName}」改名为「${newName}」，有 ${referencing.length} 个 profile 的 model_list 引用它：\n${referencing.map(profileLabel).join('、')}\n\n是否同步更新这些引用？`,
    '检测到引用该模型的 Profile',
    { confirmButtonText: '级联更新引用', cancelButtonText: '仅改模型名' },
  );
  if (ok) {
    const changed = applyModelRenameToProfileLists(entries, oldName, newName);
    for (const entry of changed) {
      const field = profileEntries.value.find(p => p.key === entry.key)?.modelListField;
      if (field) props.updateValue(field, entry.modelList);
    }
    ElMessage.success(`已级联更新 ${changed.length} 个 profile 的 model_list`);
  }
}

// ── 增删条目 ────────────────────────────────────────────
function addProvider() {
  const arr = [...providers.value];
  const item = buildDefaultItem(providerItemFields.value);
  item.name = uniqueName(arr.map(p => itemDisplayName(p)), 'new-provider');
  arr.push(item);
  writeArray(providersField.value, arr);
}

function addModel(providerName: string) {
  const arr = [...models.value];
  const item = buildDefaultItem(modelItemFields.value);
  item.name = uniqueName(modelNames.value, 'new-model');
  item.api_provider = providerName;
  arr.push(item);
  writeArray(modelsField.value, arr);
}

async function removeProvider(index: number) {
  const name = itemDisplayName(providers.value[index]);
  const owned = providerNameReferences(models.value, name);
  const ok = await confirmAction(
    owned.length > 0
      ? `删除提供商「${name}」后，其名下 ${owned.length} 个模型将变为「未归属」（数据不丢失，可改挂其他提供商）。确定删除吗？`
      : `确定删除提供商「${name}」吗？`,
    '删除提供商',
    { confirmButtonText: '删除' },
  );
  if (!ok) return;
  const arr = [...providers.value];
  arr.splice(index, 1);
  writeArray(providersField.value, arr);
}

async function removeModel(index: number) {
  const name = itemDisplayName(models.value[index]);
  const entries = profileEntries.value.map(p => ({ key: p.key, modelList: p.modelList }));
  const referencing = name === '' ? [] : modelNameReferences(entries, name);
  const ok = await confirmAction(
    referencing.length > 0
      ? `模型「${name}」仍被 ${referencing.map(profileLabel).join('、')} 的 model_list 引用，删除后这些引用将悬空（schema 校验可能在保存时拒绝）。确定删除吗？`
      : `确定删除模型「${name || '未命名模型'}」吗？`,
    '删除模型',
    { confirmButtonText: '删除' },
  );
  if (!ok) return;
  const arr = [...models.value];
  arr.splice(index, 1);
  writeArray(modelsField.value, arr);
}

// ── 结构性绑定写回 ──────────────────────────────────────
function updateModelList(
  profile: { modelListField?: ConfigFieldSchema },
  value: string[],
) {
  if (profile.modelListField) props.updateValue(profile.modelListField, value);
}
</script>

<style scoped>
.model-config-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* ── 节卡（沿用分节视觉语言） ─────────────────────────── */
.entity-section {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-primary);
  overflow: hidden;
  transition: box-shadow var(--transition-fast);
}

.entity-section:hover {
  box-shadow: var(--shadow-sm);
}

.entity-section-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.entity-section-header:hover {
  background: var(--bg-hover);
}

.entity-section-toggle {
  font-size: 14px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* 禁用节整节降不透明度示意不可用；保持默认展开，配置内容仍可查看 */
.entity-section.is-disabled {
  opacity: 0.55;
}

.entity-section-icon {
  font-size: 16px;
  color: var(--color-primary);
}

.unassigned-icon {
  color: var(--color-warning);
}

.entity-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.entity-section-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.unassigned-hint {
  font-size: 12px;
  color: var(--color-warning);
}

.entity-section-body {
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* ── 身份字段（name）专用输入 ─────────────────────────── */
.name-field,
.model-list-field {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.name-field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.required-mark {
  color: var(--color-danger);
  margin-left: 2px;
}

.description-icon {
  margin-left: 4px;
  font-size: 13px;
  color: var(--text-secondary);
  vertical-align: middle;
  cursor: help;
}

.field-hint {
  margin: 0;
  font-size: 11px;
  color: var(--text-secondary);
}

/* ── 字段双列网格（宽字段占整行的口径由 FieldRenderer 内容自适应） ── */
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--spacing-sm);
  align-items: start;
}

/* ── 高级参数折叠区 ───────────────────────────────────── */
.advanced-block {
  border-top: 1px dashed var(--border-color-light);
  padding-top: var(--spacing-xs);
}

.advanced-toggle {
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.advanced-toggle:hover {
  color: var(--color-primary);
}

/* ── 名下模型子区 ─────────────────────────────────────── */
.owned-models {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
  padding-top: var(--spacing-md);
}

.owned-models-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.owned-models-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
}

.empty-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-placeholder);
}

.page-actions {
  display: flex;
  justify-content: flex-start;
}

.unassigned-section {
  border-left-color: var(--color-warning);
}
</style>
