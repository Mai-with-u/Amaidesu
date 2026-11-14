<template>
  <div class="sub-field-group">
    <template v-for="unit in renderUnits" :key="unit.key">
      <!-- 有子字段 → 分组标题行 + 缩进内容（无边框卡片，层级靠排版承载）；高级总开关显式关闭时不渲染 advanced 容器 -->
      <div v-if="unit.container" class="sub-group" :data-config-key="unit.container.key">
        <div class="sub-group-header" @click="toggleCard(unit.container.key)">
          <div class="sub-group-header-left">
            <el-icon class="sub-group-toggle">
              <ArrowRight v-if="!expanded.has(unit.container.key)" />
              <ArrowDown v-else />
            </el-icon>
            <span class="sub-group-title">{{ unit.container.label || unit.container.key }}</span>
          </div>
          <div class="sub-group-header-right">
            <!-- 容器自带的布尔 enabled 子字段提为标题行开关（如 streamer.background.enabled）；
                 裸开关与实体卡头一致，不印启用/禁用文字 -->
            <el-switch
              v-if="enabledLeaf(unit.container)"
              :model-value="getValue(enabledLeaf(unit.container)!.key) === true"
              size="small"
              @click.stop
              @change="(v: boolean) => unit.container && toggleSubEnabled(unit.container, v)"
            />
            <el-badge
              v-if="getChildrenChangeCount(unit.container) > 0"
              :value="getChildrenChangeCount(unit.container)"
              type="warning"
              size="small"
            />
          </div>
        </div>
        <div v-if="unit.container.description" class="sub-group-desc">
          {{ unit.container.description }}
        </div>
        <div v-show="expanded.has(unit.container.key)" class="sub-group-body">
          <SubFieldGroup
            :fields="bodyFields(unit.container)"
            :get-value="getValue"
            :get-original="getOriginal"
            :update-value="updateValue"
            :get-change-count="getChangeCount"
            :show-advanced="showAdvanced"
            :expand-target-key="expandTargetKey"
          />
        </div>
      </div>

      <!-- 声明式行分组（x-ui-row）：同组叶子并成一个横行（标签在上），整组围合成小框 -->
      <div v-else-if="unit.tag" class="field-row-wrap">
        <div v-if="unit.rowLabel" class="field-row-caption">{{ unit.rowLabel }}</div>
        <div class="field-row">
          <div v-for="f in unit.fields" :key="f.key" class="field-row-item">
            <!-- 行成员用标签在上（紧凑参数格）：4 栏 × 标签在左在常规屏宽下控件只剩 ~80px，数值必截断 -->
            <FieldRenderer
              :field="f"
              :model-value="getValue(f.key)"
              :original-value="getOriginal(f.key)"
              :description-as-icon="true"
              @update:model-value="updateValue(f, $event)"
            />
          </div>
        </div>
      </div>

      <!-- 叶子字段 → 设置表单行（标签左/控件右；数组/对象/长文本由 FieldRenderer 自动回退上下堆叠） -->
      <div v-else class="field-cell">
        <FieldRenderer
          :field="unit.fields[0]!"
          :model-value="getValue(unit.fields[0]!.key)"
          :original-value="getOriginal(unit.fields[0]!.key)"
          :horizontal="true"
          @update:model-value="updateValue(unit.fields[0]!, $event)"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ArrowRight, ArrowDown } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import FieldRenderer from './FieldRenderer.vue';

const props = defineProps<{
  fields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  /**
   * 父级下发的「某字段 key 下的待保存变更条数」查询函数；
   * 用于分组标题行徽标。未提供时徽标隐藏（保留向后兼容）。
   */
  getChangeCount?: (key: string) => number;
  /** 高级字段总开关：false 时不渲染 advanced 字段（含容器与叶子）；缺省 true = 全显示 */
  showAdvanced?: boolean;
  /**
   * 搜索/清单跳转下发的展开目标 key：链路上每个祖先容器自动展开自身，
   * 使目标字段滚动定位前可见。展开后不清除（收起由用户手动）。
   */
  expandTargetKey?: string | null;
}>();

const expanded = ref<Set<string>>(new Set());

// ── 渲染单元：容器 / 行分组 / 单叶子 ──────────────────────
interface RenderUnit {
  key: string;
  /** 容器字段（本单元渲染为分组标题行 + 缩进体） */
  container?: ConfigFieldSchema;
  /** 行分组标记（x-ui-row）；null = 单叶子 */
  tag: string | null;
  /** 行显示标题（x-ui-row-label，取组内首个非空值） */
  rowLabel?: string;
  fields: ConfigFieldSchema[];
}

/**
 * 字段列表 → 渲染单元序列：高级开关先过滤，再按 x-ui-row 聚行。
 * 同 tag 的叶子按首次出现顺序归入同一行单元（无论是否相邻）；无 tag 叶子各自成行。
 * 高级开关关闭时，「后代全是 advanced」的容器整个不渲染——只露出一个空标题的
 * 分组既占位又像前端显示错误。
 */
const renderUnits = computed<RenderUnit[]>(() => {
  const visible =
    props.showAdvanced === false ? props.fields.filter(f => !f.advanced) : props.fields;
  const units: RenderUnit[] = [];
  const indexByTag = new Map<string, number>();
  for (const f of visible) {
    if (f.children && f.children.length > 0) {
      if (!hasVisibleContent(f)) continue;
      units.push({ key: f.key, container: f, tag: null, fields: [f] });
      continue;
    }
    const tag = f.row ?? null;
    if (!tag) {
      units.push({ key: f.key, tag: null, fields: [f] });
      continue;
    }
    const idx = indexByTag.get(tag);
    if (idx === undefined) {
      indexByTag.set(tag, units.length);
      units.push({ key: f.key, tag, rowLabel: f.rowLabel, fields: [f] });
    } else {
      const unit = units[idx]!;
      unit.fields.push(f);
      if (!unit.rowLabel && f.rowLabel) unit.rowLabel = f.rowLabel;
    }
  }
  return units;
});

/** 容器是否还有当前可见内容（叶子看 advanced 开关，容器递归）。
 * 属主自身的 enabled 布尔叶子不计——它已升格为卡头开关，不算正文 */
function hasVisibleContent(field: ConfigFieldSchema): boolean {
  if (!field.children?.length) {
    return props.showAdvanced === false ? !field.advanced : true;
  }
  const enabledKey = `${field.key}.enabled`;
  return field.children.some(c => c.key !== enabledKey && hasVisibleContent(c));
}

function initExpanded() {
  // 默认全部展开：与实体卡一致，内容即页面主体
  for (const f of props.fields) {
    if (f.children && f.children.length > 0) {
      expanded.value.add(f.key);
    }
  }
}
initExpanded();

// 跳转目标经过本层时展开对应子容器（目标 = 自身或自身后代）
watch(
  () => props.expandTargetKey,
  target => {
    if (!target) return;
    for (const f of props.fields) {
      if (f.children?.length && (target === f.key || target.startsWith(f.key + '.'))) {
        expanded.value.add(f.key);
      }
    }
  },
);

function toggleCard(key: string) {
  if (expanded.value.has(key)) {
    expanded.value.delete(key);
  } else {
    expanded.value.add(key);
  }
}

/** 容器字段自带的布尔 enabled 叶子（key 恰为 `<容器>.enabled`）；无则 undefined */
function enabledLeaf(field: ConfigFieldSchema): ConfigFieldSchema | undefined {
  return (field.children ?? []).find(
    c => !c.children?.length && c.type === 'boolean' && c.key === `${field.key}.enabled`,
  );
}

/** 分组体子字段：标题行开关已接管 enabled 叶子时不重复渲染 */
function bodyFields(field: ConfigFieldSchema): ConfigFieldSchema[] {
  const leaf = enabledLeaf(field);
  const children = field.children ?? [];
  return leaf ? children.filter(c => c !== leaf) : children;
}

function toggleSubEnabled(field: ConfigFieldSchema, val: boolean) {
  const leaf = enabledLeaf(field);
  if (leaf) props.updateValue(leaf, val);
}

/**
 * 分组标题行徽标：sum 父级注入的 getChangeCount(child.key)——子项的 key 前缀即
 * 「子字段下的待保存变更条数」。父级未注入时徽标隐藏（v-if 自动消失）。
 */
function getChildrenChangeCount(field: ConfigFieldSchema): number {
  if (!props.getChangeCount || !field.children) return 0;
  let count = 0;
  for (const child of field.children) {
    count += props.getChangeCount(child.key);
  }
  return count;
}
</script>

<style scoped>
.sub-field-group {
  display: flex;
  flex-direction: column;
}

/* ── 叶子字段行：行间细线分隔（子分组标题行自带 border-top，避免双重线） ── */
.field-cell {
  min-width: 0;
  padding: var(--spacing-sm) 0;
}

.field-cell + .field-cell {
  border-top: 1px solid var(--border-color-light);
}

.field-cell :deep(.field-renderer) {
  border: none;
  padding: 0;
  background: transparent;
  border-radius: 0;
}

.field-cell :deep(.field-label) {
  font-size: 13px;
  font-weight: 500;
}

.field-cell :deep(.field-description) {
  font-size: 11px;
}

/* ── 声明式行分组：整组围合成小框（与外层分组框同语言），成员等分一行、窄屏自动换行 ── */
.field-row-wrap {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-md) var(--spacing-md);
  margin-top: var(--spacing-sm);
}

/* 行标题：明显区别于字段标签的区块标题（更大间距 + 主色小标题） */
.field-row-caption {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
  letter-spacing: 0.04em;
  margin-bottom: var(--spacing-sm);
}

.field-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-sm) var(--spacing-lg);
}

.field-row-item {
  flex: 1 1 220px;
  min-width: 200px;
}

/* 行成员：标签在上的紧凑参数格（与单字段的标签在左行相区别，由蓝色行标题引领） */
.field-row-item {
  flex: 1 1 220px;
  min-width: 0;
}

.field-row-item :deep(.field-label) {
  font-size: 13px;
  font-weight: 500;
}

/* ── 分组围合框：边框 + 底色把分组围起来，层级一目了然 ── */
.sub-group {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-top: var(--spacing-sm);
}

.sub-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  cursor: pointer;
  user-select: none;
  background: var(--bg-card);
  transition: color var(--transition-fast);
}

.sub-group-header:hover {
  background: var(--bg-hover);
}

.sub-group-header:hover .sub-group-title {
  color: var(--color-primary);
}

.sub-group-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  min-width: 0;
}

.sub-group-toggle {
  font-size: 13px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.sub-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
  letter-spacing: 0.02em;
}

.sub-group-header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.sub-group-desc {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  padding: 0 var(--spacing-md);
  margin-top: -2px;
}

/* 分组体：围合框内部，不再用引导线缩进 */
.sub-group-body {
  padding: var(--spacing-xs) var(--spacing-md) var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
  background: var(--bg-elevated);
}

/* ── 叶子字段网格单元（无卡片外壳，修改/错误态由 FieldRenderer 自身高亮） ── */
.field-cell {
  min-width: 0;
}

.field-cell :deep(.field-renderer) {
  border: none;
  padding: var(--spacing-xs) 0;
  background: transparent;
  border-radius: 0;
}

.field-cell :deep(.field-label) {
  font-size: 13px;
  font-weight: 500;
}

.field-cell :deep(.field-description) {
  font-size: 11px;
  margin-bottom: var(--spacing-xs);
}
</style>
