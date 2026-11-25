// components/master-detail/ 三共享组件挂载与契约守护测试
//
// 遵循项目 .template.test.ts 惯例（无 SFC 编译环境下用源码结构断言守护关键契约）：
// - 采集器 / Agent / 工具三页均挂载 ComponentListPanel + DetailHeader（+ 工具页 StatChipBar）；
//   三页的左栏列表 / 详情头骨架不再各自复制模板；
// - ComponentListPanel 自带加载 / 错误 / 空三态与 listbox 键盘导航；
// - 批量启停确认、单停止确认、状态中文化、深链、轮询、跨分类搜索等止血契约落位。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const dir = '../..';
const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8');

const listPanelSource = read('./ComponentListPanel.vue');
const detailHeaderSource = read('./DetailHeader.vue');
const statChipBarSource = read('./StatChipBar.vue');
const collectorsSource = read(`${dir}/views/Collectors.vue`);
const agentsSource = read(`${dir}/views/Agents.vue`);
const toolsSource = read(`${dir}/views/Tools.vue`);
const toolCatalogSource = read(`${dir}/composables/tools/useToolCatalog.ts`);
const componentsStoreSource = read(`${dir}/stores/components.ts`);
const sharedCss = read(`${dir}/styles/component-master-detail.css`);

describe('共享骨架挂载关系', () => {
  it('三页均引入并挂载 ComponentListPanel 与 DetailHeader', () => {
    for (const source of [collectorsSource, agentsSource, toolsSource]) {
      expect(source).toContain("from '@/components/master-detail/ComponentListPanel.vue'");
      expect(source).toContain('<ComponentListPanel');
      expect(source).toContain("from '@/components/master-detail/DetailHeader.vue'");
      expect(source).toContain('<DetailHeader');
    }
  });

  it('三页均不再自带左栏 aside 面板与 md-list 行模板（骨架单一实现）', () => {
    for (const source of [collectorsSource, agentsSource, toolsSource]) {
      expect(source).not.toContain('<aside class="md-list-panel"');
      expect(source).not.toContain('md-list-header-main');
    }
  });

  it('采集器与 Agent 页挂载 StatChipBar，不再自带 md-details-strip 模板', () => {
    for (const source of [collectorsSource, agentsSource]) {
      expect(source).toContain("from '@/components/master-detail/StatChipBar.vue'");
      expect(source).toContain('<StatChipBar');
      expect(source).not.toContain('class="md-details-strip"');
    }
    expect(statChipBarSource).toContain('md-details-strip');
  });

  it('工具页不再保留与共享 CSS 平行的左栏/详情头/chip 样式实现', () => {
    for (const dead of ['.list-panel {', '.category-row {', '.detail-header {', '.stat-chip {']) {
      expect(toolsSource).not.toContain(dead);
    }
  });
});

describe('ComponentListPanel 契约', () => {
  it('加载 / 错误 / 空三态齐备，错误态带重试按钮', () => {
    expect(listPanelSource).toContain('v-if="loading"');
    expect(listPanelSource).toContain('el-skeleton');
    expect(listPanelSource).toContain('v-else-if="error"');
    expect(listPanelSource).toContain('@click="emit(\'retry\')">重试');
    expect(listPanelSource).toContain('v-else-if="items.length === 0"');
    expect(listPanelSource).toContain(':description="emptyText"');
  });

  it('行渲染保持 listbox/option 键盘导航与状态点语义', () => {
    expect(listPanelSource).toContain('role="listbox"');
    expect(listPanelSource).toContain('role="option"');
    expect(listPanelSource).toContain('@keydown.enter.prevent');
    expect(listPanelSource).toContain('@keydown.space.prevent');
    expect(listPanelSource).toContain("'is-running': item.isStarted");
    expect(listPanelSource).toContain("'is-stopped': !item.isStarted && item.isEnabled");
    expect(listPanelSource).toContain("'is-disabled': item.isEnabled === false");
  });

  it('批量操作与面板附加内容走插槽，计数支持运行态与 total-only 两形态', () => {
    expect(listPanelSource).toContain('<slot name="batch" />');
    expect(listPanelSource).toContain('<slot name="below-header" />');
    expect(listPanelSource).toContain('v-if="countRunning !== undefined"');
    expect(listPanelSource).toContain('countSuffix');
  });

  it('无障碍标签 prop 不用 aria 前缀命名（aria-* 属性 Vue 不映射为 prop）', () => {
    expect(listPanelSource).toContain('listLabel');
    expect(listPanelSource).not.toContain('ariaLabel');
  });
});

describe('页面策略统一（止血契约）', () => {
  it('采集器批量启停经 confirmAction 确认（与 Agent 页同标准）', () => {
    expect(collectorsSource).toContain("from '@/utils/confirmAction'");
    expect(collectorsSource).toContain('async function handleBatch');
    expect(collectorsSource).toContain('@click="handleBatch(\'start\')"');
    expect(collectorsSource).toContain('@click="handleBatch(\'stop\')"');
    expect(collectorsSource).not.toContain('@click="runBatch(');
  });

  it('Agent 单停止经 handleStopWithConfirm 确认，不再直接 handleControl(\'stop\')', () => {
    expect(agentsSource).toContain('async function handleStopWithConfirm');
    expect(agentsSource).toContain('@click="handleStopWithConfirm"');
    expect(agentsSource).not.toContain('@click="handleControl(\'stop\')"');
  });

  it('Agent 状态枚举带中文映射（后端 AgentState 七值），暂停互斥判断同步中文', () => {
    expect(agentsSource).toContain('AGENT_STATE_LABELS');
    expect(agentsSource).toContain("errored: '异常'");
    expect(agentsSource).toContain("v-if=\"selectedState !== '已暂停'\"");
    expect(agentsSource).not.toContain("selectedState !== 'paused'");
  });

  it('采集器「查看配置」深链定位到设置页对应实体段', () => {
    expect(collectorsSource).toContain('/settings?key=collectors.${selectedCollector.name}');
    expect(collectorsSource).not.toContain('<router-link to="/settings"');
  });

  it('采集器页清单走 8s 静默轮询，store 提供不闪烁 loading 的静默形态', () => {
    expect(collectorsSource).toContain('useKeepAlivePolling');
    expect(collectorsSource).toContain('fetchComponents(true)');
    expect(componentsStoreSource).toContain('async function fetchComponents(silent = false)');
    expect(componentsStoreSource).toContain("console.warn('组件清单轮询失败（等待下轮重试）:'");
  });

  it('三页错误态消费数据源错误并提供重试', () => {
    expect(collectorsSource).toContain(':error="componentsStore.error"');
    expect(agentsSource).toContain(':error="componentsStore.error"');
    expect(toolsSource).toContain(':error="error"');
    expect(toolsSource).toContain('@retry="refreshAll"');
  });
});

describe('工具页搜索与计数语义', () => {
  it('搜索跨分类：搜索模式下在全部分类的提供者中检索', () => {
    expect(toolCatalogSource).toContain('const searchMode = computed');
    expect(toolCatalogSource).toContain('.flatMap(cat => cat.providers)');
    expect(toolCatalogSource).toContain('if (!searchMode.value && t.category !== activeCategory.value)');
  });

  it('切分类不再清空搜索词（跨分类搜索成立的前提）', () => {
    expect(toolCatalogSource).not.toContain('watch(activeCategory');
  });

  it('「工具」chip 显示当前分类数或搜索匹配数，不再显示全局总数', () => {
    expect(toolCatalogSource).toContain('const activeToolCount = computed');
    expect(toolsSource).toContain('value: String(activeToolCount.value)');
    expect(toolsSource).not.toContain('value: String(totalToolCount.value)');
  });

  it('搜索模式下不挂视觉捕获面板、批量开关与搜索视图互斥', () => {
    expect(toolsSource).toContain("v-if=\"activeCategory === 'vision' && !searchMode\"");
    expect(toolsSource).toContain('v-if="bulkSwitchable && !searchMode"');
  });
});

describe('DetailHeader 契约', () => {
  it('状态标签 / 类型条 / 描述为 props，操作与链接走插槽', () => {
    expect(detailHeaderSource).toContain('statusLabel');
    expect(detailHeaderSource).toContain('md-type-chip');
    expect(detailHeaderSource).toContain("{{ description || '（暂无描述）' }}");
    expect(detailHeaderSource).toContain('<slot name="actions" />');
    expect(detailHeaderSource).toContain('<slot name="links" />');
  });
});

describe('共享 CSS 缺口类', () => {
  it('错误态 / 警示 chip / 链接行 / 计数后缀 / 单动作占满均已入共享体系', () => {
    expect(sharedCss).toContain('.md-list-error');
    expect(sharedCss).toContain('.md-stat-chip--warning');
    expect(sharedCss).toContain('.md-detail-links');
    expect(sharedCss).toContain('.md-list-count-suffix');
    expect(sharedCss).toContain('.md-batch-actions > :only-child');
  });
});
