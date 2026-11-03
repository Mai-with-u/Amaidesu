// 人设字段呈现预设的单元测试
//
// 覆盖：老梗栏空串字段补成多行文本域（FieldRenderer 的文本域判定同口径）、
// 后端显式 widget 优先、提示文案补齐且不重复追加、只动老梗栏（键/类型/约束/
// 默认值原样）、不修改传入的 schema 树。

import { describe, expect, it } from 'vitest';
import type { ConfigFieldSchema, ConfigFieldType, ConfigGroupSchema } from '@/types/settings';
import { isLongTextOf } from '@/utils/configField';
import { findFieldInSchema } from '@/utils/settingsSchema';
import {
  INSIDE_JOKES_KEY,
  completeInsideJokesDescription,
  insideJokesHintCovered,
  withPersonaFieldPresets,
} from '@/utils/personaFields';

function makeField(overrides: Partial<ConfigFieldSchema> & { key: string }): ConfigFieldSchema {
  return {
    label: overrides.key,
    type: 'string' as ConfigFieldType,
    required: false,
    sensitive: false,
    ...overrides,
  };
}

/** 与设置页真实层级一致：agents 组 → streamer 的人设容器 → 人设字段 */
function makeGroups(insideJokes: ConfigFieldSchema): ConfigGroupSchema[] {
  return [
    {
      key: 'agents',
      label: '业务 Agent',
      order: 1,
      fields: [
        makeField({
          key: 'agents.agents.streamer',
          label: '主播 Agent',
          type: 'object' as ConfigFieldType,
          children: [
            makeField({
              key: 'agents.agents.streamer.persona',
              label: '人设配置',
              type: 'object' as ConfigFieldType,
              children: [
                makeField({
                  key: 'agents.agents.streamer.persona.bot_name',
                  label: 'VTuber 名字',
                  default: '麦麦',
                }),
                insideJokes,
                makeField({
                  key: 'agents.agents.streamer.persona.personality',
                  label: '性格描述',
                  validation: { max_length: 120 },
                }),
              ],
            }),
          ],
        }),
      ],
    },
  ];
}

function findInsideJokes(groups: ConfigGroupSchema[]): ConfigFieldSchema {
  const found = findFieldInSchema(groups[0].fields, INSIDE_JOKES_KEY);
  if (!found) throw new Error('预设后找不到老梗栏字段');
  return found;
}

describe('withPersonaFieldPresets', () => {
  it('老梗栏默认空串也渲染成多行文本域', () => {
    const plain = makeField({ key: INSIDE_JOKES_KEY, label: '老梗栏', default: '', value: '' });
    expect(isLongTextOf(plain, '')).toBe(false);

    const field = findInsideJokes(withPersonaFieldPresets(makeGroups(plain)));

    expect(field.widget).toBe('textarea');
    expect(isLongTextOf(field, '')).toBe(true);
  });

  it('后端显式声明的控件形态优先，不被预设覆盖', () => {
    const declared = makeField({ key: INSIDE_JOKES_KEY, label: '老梗栏', widget: 'input' });
    const field = findInsideJokes(withPersonaFieldPresets(makeGroups(declared)));
    expect(field.widget).toBe('input');
  });

  it('提示文案补齐四条填写约束，并保留后端原有说明', () => {
    const plain = makeField({
      key: INSIDE_JOKES_KEY,
      label: '老梗栏',
      description: '直播间老梗，一行一条（如「倒立洗头」）',
    });
    const field = findInsideJokes(withPersonaFieldPresets(makeGroups(plain)));

    expect(field.description).toContain('直播间老梗');
    for (const marker of [
      '一行一条',
      '观众提起时才接',
      '别自己翻出来',
      '别写观众昵称',
      '删除函数',
    ]) {
      expect(field.description).toContain(marker);
    }
  });

  it('没有后端描述时提示文案自足成句', () => {
    const field = findInsideJokes(
      withPersonaFieldPresets(makeGroups(makeField({ key: INSIDE_JOKES_KEY, label: '老梗栏' }))),
    );
    expect(field.description).toContain('填写要求');
    expect(field.description).toContain('删除函数管不到配置文本');
  });

  it('后端描述已覆盖全部约束时不重复追加', () => {
    const covered =
      '直播间老梗：一行一条；观众提起时才接，别自己翻出来；别写观众昵称（删除函数管不到配置文本）';
    expect(insideJokesHintCovered(covered)).toBe(true);
    expect(completeInsideJokesDescription(covered)).toBe(covered);

    const field = findInsideJokes(
      withPersonaFieldPresets(
        makeGroups(makeField({ key: INSIDE_JOKES_KEY, description: covered })),
      ),
    );
    expect(field.description).toBe(covered);
  });

  it('只动老梗栏：其他字段、键、类型、约束与默认值原样', () => {
    const plain = makeField({ key: INSIDE_JOKES_KEY, label: '老梗栏', default: '' });
    const groups = makeGroups(plain);
    const preset = withPersonaFieldPresets(groups);

    const botName = findFieldInSchema(preset[0].fields, 'agents.agents.streamer.persona.bot_name');
    const personality = findFieldInSchema(
      preset[0].fields,
      'agents.agents.streamer.persona.personality',
    );
    expect(botName).toEqual(
      findFieldInSchema(groups[0].fields, 'agents.agents.streamer.persona.bot_name'),
    );
    expect(personality?.validation).toEqual({ max_length: 120 });

    const field = findInsideJokes(preset);
    expect(field.key).toBe(INSIDE_JOKES_KEY);
    expect(field.type).toBe('string');
    expect(field.default).toBe('');
  });

  it('返回新树，不修改传入的 schema', () => {
    const groups = makeGroups(makeField({ key: INSIDE_JOKES_KEY, label: '老梗栏' }));
    const snapshot = JSON.stringify(groups);

    withPersonaFieldPresets(groups);

    expect(JSON.stringify(groups)).toBe(snapshot);
  });
});
