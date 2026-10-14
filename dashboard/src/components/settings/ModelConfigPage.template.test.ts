// 模型专用页结构绑定守护测试
//
// 项目无组件挂载测试环境（无 jsdom / @vue/test-utils），按 FieldRenderer.template.test.ts
// 的既有惯例以源码结构断言守护不变量：
// 1. 叶字段一律经 FieldRenderer 渲染（身份字段 name 与结构性绑定 api_provider 除外）；
// 2. api_provider / model_list 走 el-select 结构性绑定，仍写字符串/数组字段；
// 3. 改名守护：确认后级联写回同一条 store 更新路径，不 fork 保存/脏跟踪。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const pageSource = readFileSync(
  fileURLToPath(new URL('./ModelConfigPage.vue', import.meta.url)),
  'utf-8',
);
const cardSource = readFileSync(
  fileURLToPath(new URL('./ModelItemCard.vue', import.meta.url)),
  'utf-8',
);

describe('ModelConfigPage 结构性绑定', () => {
  it('provider/model 子卡中 name 与 enabled 被排除在 FieldRenderer 字段列表外', () => {
    // 身份字段走专用控件（name 失焦提交 + 引用守护；enabled 由节头开关接管）
    expect(pageSource).toContain('normalFields(providerItemFields, providerExcludedKeys)');
    // 模型子卡由 ModelItemCard 承载，页面侧只传元素子字段树
    expect(pageSource).toContain(':model-item-fields="modelItemFields"');
    expect(cardSource).toContain("f.key !== 'name' && f.key !== 'api_provider'");
  });

  it('api_provider 用 el-select 绑 provider 名单，仍写 api_provider 字符串字段', () => {
    expect(cardSource).toContain("relKey: 'api_provider'");
    expect(cardSource).toMatch(/<el-select[\s\S]*?:model-value="providerBinding"/);
  });

  it('profile 的 model_list 用 el-select multiple，选项来自已注册模型名', () => {
    expect(pageSource).toMatch(/<el-select[\s\S]*?:model-value="profile\.modelList"[\s\S]*?multiple/);
    expect(pageSource).toContain('v-for="name in modelNames"');
  });

  it('叶字段编辑仍经 FieldRenderer（复用同一条控件与校验路径）', () => {
    expect(pageSource.match(/<FieldRenderer/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(cardSource).toContain('<FieldRenderer');
  });
});

describe('ModelConfigPage 节卡视觉', () => {
  it('provider/profile 节头带 chevron 折叠，禁用节降透明（沿用节卡视觉）', () => {
    expect(pageSource).toContain('toggleProviderExpand');
    expect(pageSource).toContain('toggleProfileExpand');
    // 默认全展开：undefined=展开，仅显式置 false 才收起（箭头与节体都按 ===false / !==false 判）
    expect(pageSource).toMatch(/ArrowRight v-if="providerExpanded\[entry\.index\] === false"/);
    expect(pageSource).toContain('providerExpanded[entry.index] !== false');
    expect(pageSource).toContain('profileExpanded[profile.key] !== false');
    expect(pageSource).toContain(".entity-section.is-disabled {\n  opacity: 0.55;\n}");
  });

  it('页头全部展开/收起命令接入专用页', () => {
    expect(pageSource).toContain('expandCommand');
    expect(pageSource).toMatch(/watch\(\s*\(\) => props\.expandCommand\?\.seq/);
    // expand = 清空显式收起记录（undefined 即展开）
    expect(pageSource).toContain('providerExpanded.value = {};');
  });

  it('provider 节头 enabled 开关写同一整值路径，节体不再重复渲染该字段', () => {
    expect(pageSource).toContain('toggleProviderEnabled(entry.index, $event as boolean)');
    expect(pageSource).not.toContain("normalFields(providerItemFields, ['name'])");
  });

  it('无 enabled 字段的 provider 不得灰显（灰显仅限显式停用）', () => {
    // provider schema 无 enabled 概念，fail-closed 判定会把全部节永久降透明
    expect(pageSource).toContain(
      "'is-disabled': providerEnabledKey !== null && isProviderEnabled(entry.item) === false",
    );
  });
});

describe('ModelConfigPage 改名引用守护', () => {
  it('provider/model 改名都先经 confirmAction 确认，再走级联写回', () => {
    expect(pageSource.match(/confirmAction\(/g)?.length ?? 0).toBe(4); // 两个改名 + 两个删除确认
    expect(pageSource).toContain('providerNameReferences(models.value, oldName)');
    expect(pageSource).toContain('modelNameReferences(entries, oldName)');
  });

  it('provider 改名级联写 llm_models 数组整值，model 改名级联按 profile 独立 key 逐条写', () => {
    expect(pageSource).toContain('applyProviderRenameToModels(models.value, oldName, newName)');
    // 每 profile 一次独立 updateValue 调用 → 变更清单逐条可见、可逐条撤销
    expect(pageSource).toMatch(/for \(const entry of changed\) \{[\s\S]*?props\.updateValue\(field, entry\.modelList\)/);
  });

  it('不引入第二套保存/脏跟踪：写回统一经注入的 updateValue', () => {
    expect(pageSource).not.toContain('applyFieldChange');
    expect(pageSource).not.toContain('pendingChanges');
    expect(cardSource).not.toContain('applyFieldChange');
  });
});
