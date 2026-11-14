<template>
  <div class="component-card" :class="{ 'is-disabled': !enabled }" :data-config-key="componentKey">
    <!-- 卡片头部：图标 + 名称 + 开关 -->
    <div class="card-header" @click="toggleExpand">
      <div class="card-header-left">
        <el-icon class="card-toggle">
          <ArrowRight v-if="!expanded" />
          <ArrowDown v-else />
        </el-icon>
        <el-icon class="card-icon"><Coin /></el-icon>
        <span class="card-title">{{ label }}</span>
      </div>
      <div class="card-header-right">
        <!-- 开关仅在具备语义时显示：布尔 enabled 子字段，或名单驱动的文件。
             裸开关（不印启用/禁用文字）：开关位置固定在卡头，明暗态一眼可辨 -->
        <el-switch
          v-if="booleanEnabledKey || hasListSemantics"
          :model-value="enabled"
          size="small"
          @click.stop
          @change="toggleEnabled"
        />
        <el-badge v-if="changeCount > 0" :value="changeCount" type="warning" size="small" />
      </div>
    </div>

    <!-- 组件描述 -->
    <div v-if="description" class="card-desc">{{ description }}</div>

    <!-- 卡片体：展开时显示子字段（禁用时也可查看配置）；全部为高级字段时给出提示而非空壳 -->
    <div v-if="componentFields.length > 0 && expanded" class="card-body">
      <p v-if="!hasVisibleFields" class="body-hidden-hint">
        该分组的配置项全部为高级设置——打开顶部「高级设置」开关后显示
      </p>
      <SubFieldGroup
        v-else
        :fields="componentFields"
        :get-value="getValue"
        :get-original="getOriginal"
        :update-value="updateValue"
        :get-change-count="getChangeCount"
        :show-advanced="showAdvanced"
        :expand-target-key="expandTargetKey"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { ArrowRight, ArrowDown, Coin } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import SubFieldGroup from './SubFieldGroup.vue';

const props = defineProps<{
  label: string;
  description?: string;
  componentKey: string;
  enabledList: string[];
  /** 非空时卡片开关绑定该布尔字段（如 tools.studio.obs.enabled），优先于名单模式 */
  booleanEnabledKey?: string | null;
  /** 卡片处于名单驱动文件（collectors/agents）：开关写启用名单 */
  hasListSemantics?: boolean;
  componentFields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  onToggle: (name: string, newEnabled: boolean) => void;
  /** 透传给内部 SubFieldGroup 的子卡片徽标查询函数 */
  getChangeCount?: (key: string) => number;
  /** 工具栏「全部展开/全部收起」命令；seq 递增触发执行 */
  expandCommand?: { action: 'expand' | 'collapse'; seq: number } | null;
  /** 高级字段总开关（透传卡体 SubFieldGroup）；false = 卡内不渲染 advanced 字段 */
  showAdvanced?: boolean;
  /** 跳转展开目标 key（透传卡体 SubFieldGroup，祖先链自动展开）；见 SubFieldGroup 同名 prop */
  expandTargetKey?: string | null;
  /**
   * 外部强制的启用态（详情模式按属主实体解析后下发）：
   * null = 未提供，回退卡片自身的布尔 enabled 子字段 / 名单语义。
   */
  forcedEnabled?: boolean | null;
}>();

// 默认展开：配置卡内容即页面主体，折叠与否交给工具栏命令
const expanded = ref(true);

watch(
  () => props.expandCommand,
  cmd => {
    if (!cmd) return;
    expanded.value = cmd.action === 'expand';
  },
);

// 跳转目标在本卡子树内时展开卡体
watch(
  () => props.expandTargetKey,
  target => {
    if (!target) return;
    if (target === props.componentKey || target.startsWith(props.componentKey + '.')) {
      expanded.value = true;
    }
  },
);

/** 当前开关下是否有可见配置内容（叶子看 advanced，容器递归） */
const hasVisibleFields = computed(() => {
  function walk(f: ConfigFieldSchema): boolean {
    if (f.children?.length) return f.children.some(walk);
    return props.showAdvanced === false ? !f.advanced : true;
  }
  return props.componentFields.some(walk);
});

const compName = computed(() => props.componentKey.split('.').pop() || '');
const enabled = computed(() => {
  // 详情模式下属主实体语义在页面层解析（子分组自身不在启用名单里，按默认名单判定会误暗淡）
  if (props.forcedEnabled != null) return props.forcedEnabled;
  if (props.booleanEnabledKey) return props.getValue(props.booleanEnabledKey) === true;
  return props.enabledList.includes(compName.value);
});

const changeCount = computed(() => {
  let count = 0;
  const seen = new Set<string>();
  function walk(fields: ConfigFieldSchema[]) {
    for (const f of fields) {
      if (f.children) {
        walk(f.children);
        continue;
      }
      if (seen.has(f.key)) continue;
      seen.add(f.key);
      if (JSON.stringify(props.getValue(f.key)) !== JSON.stringify(props.getOriginal(f.key)))
        count++;
    }
  }
  walk(props.componentFields);
  return count;
});

function toggleExpand() {
  expanded.value = !expanded.value;
}

function toggleEnabled(val: boolean) {
  if (props.booleanEnabledKey) {
    // 合成最小字段对象：写路径只消费 key 与 label
    props.updateValue({ key: props.booleanEnabledKey, label: 'enabled' } as ConfigFieldSchema, val);
    return;
  }
  props.onToggle(props.componentKey, val);
}
</script>

<style scoped>
.component-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-card);
  transition:
    box-shadow var(--transition-fast),
    border-color var(--transition-fast);
}

.component-card:hover {
  box-shadow: var(--shadow-sm);
}

.component-card:not(.is-disabled) {
  border-left: 3px solid var(--color-primary);
}

/* 禁用节整卡降不透明度示意不可用；保持默认展开，配置内容仍可查看 */
.component-card.is-disabled {
  opacity: 0.55;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.card-header:hover {
  background: var(--bg-hover);
}

.card-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
}

.card-toggle {
  font-size: 14px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.card-icon {
  font-size: 18px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.card-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  padding: 0 var(--spacing-lg) var(--spacing-sm);
  margin-top: -4px;
}

.card-header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

/* 节体自然生长：不设高度上限，滚动交给页面级（卡内滚动会让长节来回找不到上下文） */
.card-body {
  border-top: 1px solid var(--border-color-light);
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-elevated);
}

.body-hidden-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
