// 模型域专用配置页纯逻辑工具的测试
//
// 覆盖：分组计算（groupModelsByProvider 含悬空引用归桶）、改名引用方计算、
// 改名级联（模型侧 / profile 名单侧）、合成 key 解析、新条目默认值与唯一名。

import { describe, expect, it } from 'vitest';
import { ConfigFieldType, type ConfigFieldSchema } from '@/types/settings';
import {
  MODELS_KEY,
  PROVIDERS_KEY,
  apiKeySet,
  applyModelRenameToProfileLists,
  applyProviderRenameToModels,
  buildDefaultItem,
  danglingModelRefs,
  diffCountAgainst,
  formatPrice,
  groupModelsByProvider,
  makeItemField,
  modelNameReferences,
  modelUsageChips,
  parseArrayItemKey,
  profileLabel,
  providerNameReferences,
  uniqueName,
} from './modelConfig';

function makeField(key: string, defaultVal?: unknown): ConfigFieldSchema {
  return {
    key,
    label: key,
    type: ConfigFieldType.STRING,
    default: defaultVal,
    required: false,
    sensitive: false,
  };
}

describe('parseArrayItemKey', () => {
  it('解析合成 key 为数组下标与相对字段名', () => {
    expect(parseArrayItemKey(`${PROVIDERS_KEY}.0.name`, PROVIDERS_KEY)).toEqual({
      index: 0,
      relKey: 'name',
    });
    expect(parseArrayItemKey(`${MODELS_KEY}.12.api_provider`, MODELS_KEY)).toEqual({
      index: 12,
      relKey: 'api_provider',
    });
  });

  it('前缀不符或格式非法时返回 null', () => {
    expect(parseArrayItemKey('model.llm_profiles.planner.temperature', PROVIDERS_KEY)).toBeNull();
    expect(parseArrayItemKey(`${PROVIDERS_KEY}.name`, PROVIDERS_KEY)).toBeNull();
    expect(parseArrayItemKey(`${PROVIDERS_KEY}.x.name`, PROVIDERS_KEY)).toBeNull();
    expect(parseArrayItemKey(`${PROVIDERS_KEY}.-1.name`, PROVIDERS_KEY)).toBeNull();
  });

  it('makeItemField 克隆字段并携带合成 key，原字段不被修改', () => {
    const sub = makeField('name', 'default');
    const cloned = makeItemField(sub, PROVIDERS_KEY, 2);
    expect(cloned.key).toBe(`${PROVIDERS_KEY}.2.name`);
    expect(cloned.label).toBe('name');
    expect(sub.key).toBe('name');
  });
});

describe('groupModelsByProvider', () => {
  const providers = [{ name: 'openai' }, { name: 'deepseek' }];
  const models = [
    { name: 'gpt-4o', api_provider: 'openai' },
    { name: 'deepseek-chat', api_provider: 'deepseek' },
    { name: 'gpt-mini', api_provider: 'openai' },
  ];

  it('按 api_provider 分桶并保留扁平数组下标', () => {
    const { byProvider, unassigned } = groupModelsByProvider(models, providers);
    expect(unassigned).toEqual([]);
    expect(byProvider.get('openai')!.map(e => e.model.name)).toEqual(['gpt-4o', 'gpt-mini']);
    expect(byProvider.get('openai')!.map(e => e.index)).toEqual([0, 2]);
    expect(byProvider.get('deepseek')!.map(e => e.model.name)).toEqual(['deepseek-chat']);
    expect(byProvider.has('不存在的')).toBe(false);
  });

  it('api_provider 指向不存在 provider 的模型归入 unassigned', () => {
    const dangling = [{ name: 'lost', api_provider: 'ghost' }];
    const { byProvider, unassigned } = groupModelsByProvider(dangling, providers);
    expect(byProvider.get('openai')).toEqual([]);
    expect(unassigned.map(e => e.model.name)).toEqual(['lost']);
  });
});

describe('改名引用方计算', () => {
  it('providerNameReferences 返回指向旧 provider 名的模型名', () => {
    const models = [
      { name: 'a', api_provider: 'openai' },
      { name: 'b', api_provider: 'deepseek' },
      { name: 'c', api_provider: 'openai' },
    ];
    expect(providerNameReferences(models, 'openai')).toEqual(['a', 'c']);
    expect(providerNameReferences(models, 'none')).toEqual([]);
  });

  it('modelNameReferences 返回 model_list 含旧模型名的 profile key', () => {
    const entries = [
      { key: 'model.llm_profiles.planner', modelList: ['a', 'b'] },
      { key: 'model.llm_profiles.replyer', modelList: ['c'] },
    ];
    expect(modelNameReferences(entries, 'a')).toEqual(['model.llm_profiles.planner']);
    expect(modelNameReferences(entries, 'zzz')).toEqual([]);
  });
});

describe('改名级联', () => {
  it('applyProviderRenameToModels 只改指向旧名的 api_provider，且不共享元素引用', () => {
    const models = [
      { name: 'a', api_provider: 'openai' },
      { name: 'b', api_provider: 'deepseek' },
    ];
    const next = applyProviderRenameToModels(models, 'openai', 'gpt');
    expect(next[0]).toEqual({ name: 'a', api_provider: 'gpt' });
    expect(next[1]).toEqual({ name: 'b', api_provider: 'deepseek' });
    expect(next[0]).not.toBe(models[0]);
    expect(models[0].api_provider).toBe('openai');
  });

  it('applyModelRenameToProfileLists 只返回受影响 profile 的新名单', () => {
    const entries = [
      { key: 'model.llm_profiles.planner', modelList: ['a', 'b'] },
      { key: 'model.llm_profiles.replyer', modelList: ['b'] },
      { key: 'model.llm_profiles.summary', modelList: ['c'] },
    ];
    const changed = applyModelRenameToProfileLists(entries, 'b', 'bb');
    expect(changed).toEqual([
      { key: 'model.llm_profiles.planner', modelList: ['a', 'bb'] },
      { key: 'model.llm_profiles.replyer', modelList: ['bb'] },
    ]);
    // 原名单不被原地修改
    expect(entries[0].modelList).toEqual(['a', 'b']);
  });
});

describe('buildDefaultItem / uniqueName / diffCountAgainst', () => {
  it('buildDefaultItem 取子字段默认值并深拷贝对象默认', () => {
    const fields = [
      makeField('name', 'default'),
      makeField('max_retries', 3),
      makeField('extra_body', { a: 1 }),
    ];
    const item = buildDefaultItem(fields);
    expect(item).toEqual({ name: 'default', max_retries: 3, extra_body: { a: 1 } });
    const second = buildDefaultItem(fields);
    expect(item.extra_body).not.toBe(second.extra_body);
  });

  it('uniqueName 未占用直接用，占用则追加序号', () => {
    expect(uniqueName(['a', 'b'], 'c')).toBe('c');
    expect(uniqueName(['a'], 'a')).toBe('a-2');
    expect(uniqueName(['a', 'a-2'], 'a')).toBe('a-3');
  });

  it('diffCountAgainst 统计同下标对象的逐字段差异数', () => {
    expect(diffCountAgainst({ name: 'a', n: 1 }, { name: 'a', n: 1 })).toBe(0);
    expect(diffCountAgainst({ name: 'a', n: 1 }, { name: 'b', n: 1 })).toBe(1);
    expect(diffCountAgainst({ name: 'a', n: 1 }, { name: 'b', n: 2 })).toBe(2);
    expect(diffCountAgainst(undefined, { name: 'a' })).toBe(0);
  });
});

describe('profileLabel', () => {
  it('固定用途映射中文名，未知键回退短名', () => {
    expect(profileLabel('model.llm_profiles.planner')).toBe('决策 Profile');
    expect(profileLabel('model.llm_profiles.custom')).toBe('custom');
  });
});

describe('modelUsageChips / danglingModelRefs / apiKeySet / formatPrice', () => {
  const entries = [
    { key: 'model.llm_profiles.planner', modelList: ['a', 'b'] },
    { key: 'model.llm_profiles.replyer', modelList: ['a'] },
  ];

  it('modelUsageChips 主模型显示用途名，备选追加·备选后缀', () => {
    expect(modelUsageChips(entries, 'a')).toEqual(['决策 Profile', '表达 Profile']);
    expect(modelUsageChips(entries, 'b')).toEqual(['决策 Profile·备选']);
    expect(modelUsageChips(entries, 'c')).toEqual([]);
  });

  it('danglingModelRefs 返回不在注册表的名字并去重，空串不算', () => {
    expect(danglingModelRefs(['a', 'x', 'y', 'x'], ['a'])).toEqual(['x', 'y']);
    expect(danglingModelRefs(['', 'a'], ['a'])).toEqual([]);
  });

  it('apiKeySet 判定非空即已设置，undefined/空白为未设置', () => {
    expect(apiKeySet('sk-1')).toBe(true);
    expect(apiKeySet('  ')).toBe(false);
    expect(apiKeySet(undefined)).toBe(false);
  });

  it('formatPrice 数值带 ¥ 前缀，缺值显示 —', () => {
    expect(formatPrice(1)).toBe('¥1');
    expect(formatPrice(0)).toBe('¥0');
    expect(formatPrice(undefined)).toBe('—');
    expect(formatPrice('')).toBe('—');
  });
});
