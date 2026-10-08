// FieldRenderer 输入区条件链完整性测试
//
// 回归背景：fixed-tuple 分支曾用裸 v-if 脱离 type 分支链，导致 widget 字段
// （如 agents.text_adv.region，type=array + widget=fixed-tuple）同时渲染
// type 控件与元组控件两套输入——两者都绑同一 localValue，在一处输入另一处
// 同步变化（用户实测的"同一配置项两个输入框"bug）。
//
// 守护方式：field-input 容器内所有输入分支必须构成**单条** v-if / v-else-if
// 链——链内只允许一个 v-if（链头），其余分支一律 v-else-if。任何新分支用
// 裸 v-if 插入链中都会在此失败。项目无组件挂载测试环境（无 jsdom /
// @vue/test-utils），按现有惯例以源码结构断言守护模板不变量。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./FieldRenderer.vue', import.meta.url)),
  'utf-8',
);

/** 截取 field-input 容器内的模板片段（输入分支链所在区域） */
function extractFieldInputBlock(source: string): string {
  const start = source.indexOf('<div class="field-input">');
  const end = source.indexOf('</div>', start);
  expect(start, '找不到 field-input 容器').toBeGreaterThan(-1);
  expect(end, 'field-input 容器未闭合').toBeGreaterThan(start);
  return source.slice(start, end);
}

describe('FieldRenderer 输入分支条件链', () => {
  it('field-input 内只允许一个链头 v-if，其余分支均为 v-else-if', () => {
    const block = extractFieldInputBlock(sfcSource);
    // 只统计 template 标签上的分支指令：分支链全部由 <template> 承载，
    // 分支内部的 el-input 等元素级 v-if 不属于本链
    const branches = [...block.matchAll(/<template[^>]*?(v-else-if|v-if)="/g)].map(m => m[1]);
    const heads = branches.filter(d => d === 'v-if');
    expect(branches.length, '输入分支链为空，模板结构异常').toBeGreaterThan(1);
    expect(heads, '输入区存在多条 v-if 链：分支脱链会双渲染同一字段').toHaveLength(1);
  });

  it('fixed-tuple 分支挂在条件链上（v-else-if），不与 type 控件并存双渲染', () => {
    expect(sfcSource).toContain('v-else-if="field.widget === \'fixed-tuple\'"');
    expect(sfcSource).not.toMatch(/v-if="field\.widget === 'fixed-tuple'"/);
  });
});
