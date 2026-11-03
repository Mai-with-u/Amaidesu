// 主播人设字段的展示层补充：人设字段本身由后端 /config/schema 下发（含老梗栏
// inside_jokes），前端不另立字段定义；这里只补两处呈现缺省，key / 类型 / 约束 / 值
// 一律不动，保存与校验仍走设置页原有管线（编辑 → 待保存清单 → /config/batch）。
//
// 需要补充的原因：
// 1. 多行文本：老梗栏按"一行一条"填写，字段默认空串；FieldRenderer 的文本域判定
//    按字段值形态回退（空串判成短文本），运营者第一次打开只会看到一个单行输入框；
// 2. 提示文案：老梗栏最容易踩的坑是把她自己翻旧梗、把观众昵称写进配置（删除观众
//    的函数管不到配置文本）。后端描述补齐后这里的检测会跳过，不重复追加。

import type { ConfigFieldSchema, ConfigGroupSchema } from '@/types/settings';

/** 老梗栏字段的完整配置键（agents.toml → [agents.streamer.persona].inside_jokes） */
export const INSIDE_JOKES_KEY = 'agents.agents.streamer.persona.inside_jokes';

/** 提示文案必须覆盖的关键片段；缺任意一条就追加完整提示 */
const REQUIRED_HINT_MARKERS = [
  '一行一条',
  '观众提起时才接',
  '别自己翻出来',
  '别写观众昵称',
  '删除函数',
];

/** 与后端描述拼接的提示句（自足成句，不依赖上下文） */
const INSIDE_JOKES_HINT =
  '填写要求：一行一条；观众提起时才接，别自己翻出来；别写观众昵称（删除函数管不到配置文本）。';

/** 后端描述是否已覆盖提示要求 */
export function insideJokesHintCovered(description: string | undefined): boolean {
  const text = description ?? '';
  return REQUIRED_HINT_MARKERS.every(marker => text.includes(marker));
}

/** 补全提示文案：已覆盖时原样返回，否则在后端描述后追加一句填写要求 */
export function completeInsideJokesDescription(description: string | undefined): string {
  const current = (description ?? '').trim();
  if (insideJokesHintCovered(current)) return current;
  return current ? `${current} ${INSIDE_JOKES_HINT}` : INSIDE_JOKES_HINT;
}

/** 老梗栏字段的呈现预设：未声明 widget 时按多行文本渲染，并补全提示文案 */
function applyInsideJokesPreset(field: ConfigFieldSchema): ConfigFieldSchema {
  if (field.key !== INSIDE_JOKES_KEY) return field;
  return {
    ...field,
    // 后端显式声明 widget 时以声明为准（input = 单行，textarea = 多行）
    widget: field.widget ?? 'textarea',
    description: completeInsideJokesDescription(field.description),
  };
}

/** 深度遍历字段树（children / properties / 数组元素子字段），只替换老梗栏自身 */
function mapFieldTree(field: ConfigFieldSchema): ConfigFieldSchema {
  const next: ConfigFieldSchema = { ...applyInsideJokesPreset(field) };
  if (next.children) next.children = next.children.map(mapFieldTree);
  if (next.properties) {
    next.properties = Object.fromEntries(
      Object.entries(next.properties).map(([key, sub]) => [key, mapFieldTree(sub)]),
    );
  }
  if (next.items?.fields) {
    next.items = { ...next.items, fields: next.items.fields.map(mapFieldTree) };
  }
  return next;
}

/** 对设置页 schema 的全部字段树应用人设字段呈现预设；返回新树，不改入参 */
export function withPersonaFieldPresets(groups: ConfigGroupSchema[]): ConfigGroupSchema[] {
  return groups.map(group => ({ ...group, fields: group.fields.map(mapFieldTree) }));
}
