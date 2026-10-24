// configField 纯函数表驱动测试

import { describe, expect, it } from 'vitest';
import { ConfigFieldType, type ConfigFieldSchema } from '@/types/settings';
import {
  defaultPlaceholder,
  getDefaultValueForType,
  isEqualJSON,
  isLongTextOf,
  tupleJoin,
  tupleSplit,
  validateValue,
} from './configField';

function field(partial: Partial<ConfigFieldSchema> & { key: string }): ConfigFieldSchema {
  return {
    label: partial.key,
    type: ConfigFieldType.STRING,
    required: false,
    sensitive: false,
    ...partial,
  } as ConfigFieldSchema;
}

describe('validateValue', () => {
  const cases: {
    name: string;
    field: ConfigFieldSchema;
    value: unknown;
    expected: string | null;
  }[] = [
    {
      name: '必填-空值失败',
      field: field({ key: 'f', required: true }),
      value: '',
      expected: '此字段为必填项',
    },
    {
      name: '必填-null 失败',
      field: field({ key: 'f', required: true }),
      value: null,
      expected: '此字段为必填项',
    },
    {
      name: '必填-有值通过',
      field: field({ key: 'f', required: true }),
      value: 'x',
      expected: null,
    },
    {
      name: 'min-小于下界失败',
      field: field({ key: 'f', type: ConfigFieldType.INTEGER, validation: { min: 2 } }),
      value: 1,
      expected: '最小值为 2',
    },
    {
      name: 'max-大于上界失败',
      field: field({ key: 'f', type: ConfigFieldType.FLOAT, validation: { max: 10 } }),
      value: 11,
      expected: '最大值为 10',
    },
    {
      name: 'min/max-区间内通过',
      field: field({ key: 'f', type: ConfigFieldType.INTEGER, validation: { min: 0, max: 10 } }),
      value: 5,
      expected: null,
    },
    {
      name: 'min_length-过短失败',
      field: field({ key: 'f', validation: { min_length: 3 } }),
      value: 'ab',
      expected: '最少 3 个字符',
    },
    {
      name: 'max_length-过长失败',
      field: field({ key: 'f', validation: { max_length: 2 } }),
      value: 'abc',
      expected: '最多 2 个字符',
    },
    {
      name: 'pattern-不匹配失败',
      field: field({ key: 'f', validation: { pattern: '^\\d+$' } }),
      value: 'abc',
      expected: '格式不正确',
    },
    {
      name: 'pattern-匹配通过',
      field: field({ key: 'f', validation: { pattern: '^\\d+$' } }),
      value: '123',
      expected: null,
    },
    {
      name: '无约束通过',
      field: field({ key: 'f' }),
      value: 'anything',
      expected: null,
    },
  ];

  for (const c of cases) {
    it(c.name, () => {
      expect(validateValue(c.field, c.value)).toBe(c.expected);
    });
  }
});

describe('isLongTextOf', () => {
  it('widget 声明优先：textarea 恒真、input 恒假', () => {
    expect(isLongTextOf(field({ key: 'f', widget: 'textarea' }), '短')).toBe(true);
    expect(isLongTextOf(field({ key: 'f', widget: 'input' }), 'a\nb\n很长\n'.repeat(40))).toBe(
      false,
    );
  });

  it('非 string 类型按假处理', () => {
    expect(isLongTextOf(field({ key: 'f', type: ConfigFieldType.INTEGER }), 12345678)).toBe(false);
  });

  it('含换行、超长、max_length 超阈值任一即真', () => {
    expect(isLongTextOf(field({ key: 'f' }), 'a\nb')).toBe(true);
    expect(isLongTextOf(field({ key: 'f' }), 'x'.repeat(81))).toBe(true);
    expect(isLongTextOf(field({ key: 'f', validation: { max_length: 101 } }), '')).toBe(true);
  });

  it('短单行且 max_length 未超阈值按假处理', () => {
    expect(isLongTextOf(field({ key: 'f' }), '短文本')).toBe(false);
  });
});

describe('defaultPlaceholder', () => {
  it('敏感字段返回空串', () => {
    expect(defaultPlaceholder(field({ key: 'f', sensitive: true, default: 'sk-xxx' }))).toBe('');
  });

  it('有默认值加「默认」前缀，空默认值返回空串', () => {
    expect(defaultPlaceholder(field({ key: 'f', default: 'abc' }))).toBe('默认 abc');
    expect(defaultPlaceholder(field({ key: 'f', default: '' }))).toBe('');
    expect(defaultPlaceholder(field({ key: 'f', default: null }))).toBe('');
    expect(defaultPlaceholder(field({ key: 'f' }))).toBe('');
  });
});

describe('tupleSplit / tupleJoin', () => {
  it('拆分对齐长度，缺位以 undefined 占位', () => {
    expect(tupleSplit([1, 2], 4)).toEqual([1, 2, undefined, undefined]);
    expect(tupleSplit('not-array', 2)).toEqual([undefined, undefined]);
    expect(tupleSplit([1, 'x', 3], 3)).toEqual([1, undefined, 3]);
  });

  it('全填齐合并产出数值数组，缺位回退 null', () => {
    expect(tupleJoin([1, 2, 3, 4])).toEqual([1, 2, 3, 4]);
    expect(tupleJoin([1, undefined, 3])).toBeNull();
  });
});

describe('isEqualJSON', () => {
  it('序列化一致即相等；键序不同视为不等（语义保持，非深比较）', () => {
    expect(isEqualJSON({ a: 1, b: 2 }, { a: 1, b: 2 })).toBe(true);
    expect(isEqualJSON({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(false);
    expect(isEqualJSON(null, null)).toBe(true);
  });
});

describe('getDefaultValueForType', () => {
  it('各类型默认值表', () => {
    expect(getDefaultValueForType('string')).toBe('');
    expect(getDefaultValueForType('integer')).toBe(0);
    expect(getDefaultValueForType('float')).toBe(0);
    expect(getDefaultValueForType('boolean')).toBe(false);
    expect(getDefaultValueForType('object')).toEqual({});
    expect(getDefaultValueForType('array')).toEqual([]);
    expect(getDefaultValueForType(undefined)).toBe('');
    expect(getDefaultValueForType('mystery')).toBe('');
  });
});
