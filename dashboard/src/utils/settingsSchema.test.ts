// settingsSchema 纯函数表驱动测试

import { describe, expect, it } from 'vitest';
import { ConfigFieldType, type ConfigFieldSchema, type ConfigGroupSchema } from '@/types/settings';
import {
  collectMatchingFields,
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

describe('collectMatchingFields', () => {
  it('三型下钻：children / properties 内的命中都被收集', () => {
    const tree: ConfigFieldSchema[] = [
      field({ key: 'a.host', label: '主机' }),
      field({
        key: 'a.group',
        label: '分组',
        children: [field({ key: 'a.group.port', label: '端口' })],
      }),
      field({
        key: 'a.dict',
        label: '字典',
        properties: { inner: field({ key: 'a.dict.inner', label: '内部项' }) },
      }),
    ];
    const seen = new Set<string>();
    const out: ConfigFieldSchema[] = [];
    collectMatchingFields(tree, 'a.', seen, out);
    expect(out.map(f => f.key)).toEqual([
      'a.host',
      'a.group',
      'a.group.port',
      'a.dict',
      'a.dict.inner',
    ]);
  });

  it('seen 去重：父命中后父不重复收集，但子级分支仍要继续遍历', () => {
    const tree: ConfigFieldSchema[] = [
      field({
        key: 'grp',
        label: 'grp',
        children: [field({ key: 'grp.child', label: 'grp' })],
      }),
    ];
    const seen = new Set<string>(['grp']);
    const out: ConfigFieldSchema[] = [];
    collectMatchingFields(tree, 'grp', seen, out);
    // 父已在 seen 中不重复收集；子命中且被收集
    expect(out.map(f => f.key)).toEqual(['grp.child']);
    expect(seen.has('grp.child')).toBe(true);
  });

  it('同一轮遍历中重复 key 只收集一次', () => {
    const tree: ConfigFieldSchema[] = [
      field({ key: 'dup', label: 'dup' }),
      field({ key: 'dup', label: 'dup', children: [field({ key: 'dup.c', label: 'dup' })] }),
    ];
    const seen = new Set<string>();
    const out: ConfigFieldSchema[] = [];
    collectMatchingFields(tree, 'dup', seen, out);
    expect(out.map(f => f.key)).toEqual(['dup', 'dup.c']);
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
