// 模型专用页三层信息架构守护测试
//
// 项目无组件挂载测试环境（无 jsdom / @vue/test-utils），按 FieldRenderer.template.test.ts
// 的既有惯例以源码结构断言守护不变量：
// 1. 三层结构：用途分配卡区（主模型 = el-select）、模型注册表（table + 行展开）、
//    提供商接入抽屉——模型不再嵌套在 provider 节下；
// 2. 零指引文案：不出现任何解释页面用法/数据流向的提示段落；
// 3. 叶字段一律经 FieldRenderer 渲染，改名守护走同一条 store 更新路径。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const pageSource = readFileSync(
  fileURLToPath(new URL('./ModelConfigPage.vue', import.meta.url)),
  'utf-8',
);

describe('ModelConfigPage 三层结构', () => {
  it('第一层用途分配：profile 主模型走 el-select 绑 model_list[0]，选项来自已注册模型名', () => {
    expect(pageSource).toContain('setMainModel(profile, $event as string)');
    expect(pageSource).toMatch(/<el-select[\s\S]*?:model-value="profile\.modelList\[0\] \?\? ''"/);
    expect(pageSource.match(/v-for="name in modelNames"/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });

  it('第一层备选链：chips 带 × 移除，"＋备选"追加，整条 model_list 数组写回', () => {
    expect(pageSource).toContain('removeFallback(profile, i + 1)');
    expect(pageSource).toContain('addFallback(profile, $event as string)');
    expect(pageSource).toMatch(/props\.updateValue\(profile\.modelListField, value\)/);
  });

  it('profile 卡底"更多设置"折叠条仅在存在 otherChildren 时渲染，内部走 SubFieldGroup', () => {
    expect(pageSource).toMatch(
      /<details v-if="profile\.otherChildren\.length > 0" class="more-block">/,
    );
    expect(pageSource).toMatch(/<SubFieldGroup[\s\S]*?:fields="profile\.otherChildren"/);
    // 同一条逐 key 更新路径：SubFieldGroup 直接接注入的 updateValue
    expect(pageSource).toMatch(/<SubFieldGroup[\s\S]*?:update-value="updateValue"/);
    // 卡片一体，不套节卡头结构
    expect(pageSource).not.toMatch(/class="more-block[\s\S]{0,120}entity-section-header/s);
  });

  it('第一层悬空引用警示条只在有悬空引用时出现', () => {
    expect(pageSource).toContain('v-if="danglingRefs(profile).length > 0"');
    expect(pageSource).toContain('danglingModelRefs(profile.modelList, modelNames.value)');
  });

  it('第二层模型注册：表格 + 按提供商筛选 chips + 行展开编辑，不嵌套在 provider 下', () => {
    expect(pageSource).toContain('<table class="model-table"');
    expect(pageSource).toContain("providerFilter = 'all'");
    expect(pageSource).toContain('toggleModelExpand(idx)');
    // 旧结构（provider 节内嵌模型子卡）不得残留
    expect(pageSource).not.toContain('owned-models');
    expect(pageSource).not.toContain('ModelItemCard');
    expect(pageSource).not.toContain('modelsOf(entry.item)');
  });

  it('第二层表格列：单价列头自带单位、能力 tags、反向引用列、未投入使用灰字', () => {
    expect(pageSource).toContain('<th>输入 ¥/M</th>');
    expect(pageSource).toContain('<th>输出 ¥/M</th>');
    expect(pageSource).toContain("models[idx]?.cache === 'hit_discount'");
    expect(pageSource).toContain('modelUsageChips(profileListEntries.value, name)');
    expect(pageSource).toContain('class="unused">未投入使用</span>');
  });

  it('第三层提供商抽屉：默认收起（!== true 才展开），密钥状态二态，展开区 FieldRenderer 全字段', () => {
    expect(pageSource).toContain('providerOpen[entry.index] !== true');
    expect(pageSource).toContain("apiKeySet(entry.item.api_key) ? '● 密钥已设置' : '○ 未设置'");
    expect(pageSource).toContain('providerEditorFields');
    expect(pageSource).toContain('toggleProviderOpen(entry.index)');
  });

  it('添加模型/提供商走 buildDefaultItem 追加并自动展开新行', () => {
    expect(pageSource).toMatch(
      /buildDefaultItem\(modelItemFields\.value\)[\s\S]*?modelExpanded\.value\[arr\.length - 1\] = true/,
    );
    expect(pageSource).toMatch(
      /buildDefaultItem\(providerItemFields\.value\)[\s\S]*?providerOpen\.value\[arr\.length - 1\] = true/,
    );
  });

  it('页头全部展开/收起命令接入专用页', () => {
    expect(pageSource).toContain('expandCommand');
    expect(pageSource).toMatch(/watch\(\s*\(\) => props\.expandCommand\?\.seq/);
    expect(pageSource).toContain('providerOpen.value = {};');
  });
});

describe('ModelConfigPage 零指引文案', () => {
  it('不出现解释页面用法/数据流向的提示段落', () => {
    expect(pageSource).not.toMatch(
      /候选为|改动会?进入|保存更改|这个页面|按顺序|可手敲|字段说明|zone-tip|field-hint/,
    );
  });

  it('警示条是条件渲染且无静态提示段落', () => {
    // 悬空引用警示条必须挂在 v-if 上（actionable 才出现），不允许常驻提示文案
    expect(pageSource).toMatch(/<div v-if="danglingRefs\(profile\)\.length > 0" class="warn">/);
    expect(pageSource).not.toContain('field-hint');
  });
});

describe('ModelConfigPage 结构性绑定', () => {
  it('模型/提供商编辑区 name 与 api_provider 排除在 FieldRenderer 字段列表外', () => {
    expect(pageSource).toContain("f.key !== 'name' && f.key !== 'api_provider'");
    expect(pageSource).toContain("f.key !== 'name'");
  });

  it('api_provider 用 el-select 绑 provider 名单，仍写字符串字段', () => {
    expect(pageSource).toContain("updateModelField(idx, 'api_provider', $event)");
  });

  it('叶字段编辑仍经 FieldRenderer（复用同一条控件与校验路径）', () => {
    expect(pageSource.match(/<FieldRenderer/g)?.length ?? 0).toBeGreaterThanOrEqual(4);
  });

  it('连通性测试：模型行只报提供商名 + 模型标识，凭据由后端按名解析', () => {
    expect(pageSource).toContain('testModelConnectivity(idx)');
    // 密钥不出前端：请求体只含名字与标识两个非敏感字符串
    expect(pageSource).toMatch(
      /llmApi\.testModel\(\{ provider: providerName, model: identifier \}\)/,
    );
    expect(pageSource).toContain('class="test-ok mono"');
    expect(pageSource).toContain('class="test-fail"');
  });
});

describe('ModelConfigPage 改名引用守护', () => {
  it('provider/model 改名都先经 confirmAction 确认，再走级联写回', () => {
    expect(pageSource.match(/confirmAction\(/g)?.length ?? 0).toBe(4); // 两个改名 + 两个删除确认
    expect(pageSource).toContain('providerNameReferences(models.value, oldName)');
    expect(pageSource).toContain('modelUsageChips(profileListEntries.value, oldName)');
  });

  it('provider 改名级联写 llm_models 数组整值，model 改名级联按 profile 独立 key 逐条写', () => {
    expect(pageSource).toContain('applyProviderRenameToModels(models.value, oldName, newName)');
    // 每 profile 一次独立 updateValue 调用 → 变更清单逐条可见、可逐条撤销
    expect(pageSource).toMatch(
      /for \(const entry of changed\) \{[\s\S]*?props\.updateValue\(field, entry\.modelList\)/,
    );
  });

  it('不引入第二套保存/脏跟踪：写回统一经注入的 updateValue', () => {
    expect(pageSource).not.toContain('applyFieldChange');
    expect(pageSource).not.toContain('pendingChanges');
  });
});
