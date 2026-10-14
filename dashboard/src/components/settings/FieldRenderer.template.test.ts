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

/** 截取 field-input 容器内的模板片段（输入分支链所在区域）。
 *  不能用「首个 </div>」截断：链头 slider 分支内部含 <div class="slider-control">，
 *  会提前闭合导致链体为空；改以错误提示段落为右边界。 */
function extractFieldInputBlock(source: string): string {
  const start = source.indexOf('<div class="field-input">');
  const end = source.indexOf('<!-- 错误提示 -->', start);
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

  it('slider 分支占据链头（v-if），滑条与数字框绑同一计算值', () => {
    // slider 必须先于 type 分支判定：slider 字段的 type 多为 integer/float，
    // 排在 type 分支之后会被先拦截，滑条永不渲染
    const block = extractFieldInputBlock(sfcSource);
    expect(block).toContain('<template v-if="field.widget === \'slider\'">');
    expect(sfcSource).not.toMatch(/v-else-if="field\.widget === 'slider'"/);
    // 两个控件共用 sliderModel，避免出现第二套绑 localValue 的输入
    expect(sfcSource.match(/v-model="sliderModel"/g)).toHaveLength(2);
  });
});

describe('长文本判定（isLongText）', () => {
  it('显式 widget 声明优先，按值形态判断，不用描述长度', () => {
    // 回归背景：旧启发式按 description 长度 >50 判文本域，短枚举值配长描述
    // （如 思考强度="max"）被误判成多行框；描述长度衡量的是说明文案，与输入形态无关
    expect(sfcSource).toContain("props.field.widget === 'textarea'");
    expect(sfcSource).toContain("props.field.widget === 'input'");
    expect(sfcSource).toContain("value.includes('\\n')");
    expect(sfcSource).toContain('value.length > 80');
    expect(sfcSource).not.toContain('desc.length');
  });
});
