// settingsSchema 纯函数表驱动测试

import { describe, expect, it } from 'vitest';
import { ConfigFieldType, type ConfigFieldSchema, type ConfigGroupSchema } from '@/types/settings';
import {
  buildSearchResults,
  findFieldInSchema,
  findFieldValue,
  isAdvancedField,
  updateFieldValueImmutably,
} from './settingsSchema';

function field(partial: Partial<ConfigFieldSchema> & { key: string }): ConfigFieldSchema {
  return {
    label: partial.key,
    type: ConfigFieldType.STRING,
    required: false,
    sensitive: false,
    ...partial,
  } as ConfigFieldSchema;
}

describe('buildSearchResults', () => {
  function groupOf(fields: ConfigFieldSchema[]) {
    return { key: 'g', label: 'G', fields, order: 0, file_name: 'g.toml' } as ConfigGroupSchema;
  }

  it('三型下钻：children / properties / items.fields 内的命中都被收集', () => {
    const groups = [
      groupOf([
        field({ key: 'a.host', label: '主机' }),
        field({
          key: 'a.dict',
          label: '字典',
          properties: { inner: field({ key: 'a.dict.inner', label: '内部项' }) },
        }),
        field({
          key: 'a.arr',
          label: '数组',
          type: ConfigFieldType.ARRAY,
          items: field({
            key: 'a.arr.elem',
            type: ConfigFieldType.OBJECT,
            fields: [field({ key: 'a.arr.elem.api_key', label: '密钥' })],
          }),
        }),
      ]),
    ];
    const { hits, total } = buildSearchResults(groups, '密钥');
    expect(total).toBe(1);
    expect(hits[0]!.field.key).toBe('a.arr.elem.api_key');
    expect(hits[0]!.path).toEqual(['数组']);
    expect(hits[0]!.fileKey).toBe('g.toml');
  });

  it('容器自身可命中，分组链按从外到内携带祖先 label', () => {
    const groups = [
      groupOf([
        field({
          key: 'g.ctx',
          label: '上下文',
          children: [field({ key: 'g.ctx.max_tokens', label: '窗口上限' })],
        }),
      ]),
    ];
    const { hits, total } = buildSearchResults(groups, '上下文');
    expect(total).toBe(1);
    expect(hits[0]!.field.key).toBe('g.ctx');
    expect(hits[0]!.path).toEqual([]);
    // 子级字段不在本次命中中，但可通过其命中词搜到并携带分组链
    const byChild = buildSearchResults(groups, '窗口');
    expect(byChild.hits[0]!.field.key).toBe('g.ctx.max_tokens');
    expect(byChild.hits[0]!.path).toEqual(['上下文']);
  });

  it('跨分组去重：同一 key 只出现一次；limit 截断并保留 total', () => {
    const groups = [
      groupOf([field({ key: 'dup', label: '重复' })]),
      groupOf([field({ key: 'dup', label: '重复' }), field({ key: 'b', label: '重复' })]),
    ];
    const { hits, total } = buildSearchResults(groups, '重复', 1);
    expect(total).toBe(2);
    expect(hits).toHaveLength(1);
    expect(hits[0]!.fileKey).toBe('g.toml');
  });
});

describe('findFieldValue', () => {
  const values = { a: { b: { c: 1 } }, list: [2] };

  it('多级点路径读取', () => {
    expect(findFieldValue(values, 'a.b.c')).toBe(1);
    expect(findFieldValue(values, 'list')).toEqual([2]);
  });

  it('缺中间层或末层返回 undefined', () => {
    expect(findFieldValue(values, 'a.x.c')).toBeUndefined();
    expect(findFieldValue(values, 'a.b.d')).toBeUndefined();
    expect(findFieldValue(values, 'a.b.c.d')).toBeUndefined();
  });
});

describe('updateFieldValueImmutably', () => {
  it('写入叶子并返回新对象，原对象各级不被改写', () => {
    const original = { a: { b: 1 }, c: 2 };
    const snapshot = JSON.parse(JSON.stringify(original));
    const next = updateFieldValueImmutably(original, 'a.b', 9);
    expect((next.a as { b: number }).b).toBe(9);
    expect(original).toEqual(snapshot);
    expect(next).not.toBe(original);
    expect(next.a).not.toBe(original.a);
    // 未触及的分支保持同一引用
    expect(next.c).toBe(original.c);
  });

  it('中间层缺失时补空对象', () => {
    const next = updateFieldValueImmutably({}, 'x.y.z', true);
    expect(next).toEqual({ x: { y: { z: true } } });
  });
});

describe('findFieldInSchema / isAdvancedField', () => {
  const groups: ConfigGroupSchema[] = [
    {
      key: 'g',
      label: 'g',
      order: 0,
      fields: [
        field({ key: 'g.normal' }),
        field({ key: 'g.adv', advanced: true }),
        field({
          key: 'g.arr',
          type: ConfigFieldType.ARRAY,
          items: field({
            key: 'g.arr.elem',
            type: ConfigFieldType.OBJECT,
            fields: [field({ key: 'g.arr.elem.deep' })],
          }),
        }),
      ],
    },
  ];

  it('items.fields 三型下钻找到深层字段', () => {
    expect(findFieldInSchema(groups[0]!.fields, 'g.arr.elem.deep')?.key).toBe('g.arr.elem.deep');
    expect(findFieldInSchema(groups[0]!.fields, 'nope')).toBeUndefined();
  });

  it('isAdvancedField 按找到的字段判定，找不到按非高级', () => {
    expect(isAdvancedField('g.adv', groups)).toBe(true);
    expect(isAdvancedField('g.normal', groups)).toBe(false);
    expect(isAdvancedField('missing', groups)).toBe(false);
  });
});
