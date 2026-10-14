/**
 * 模型域专用配置页的纯逻辑工具
 *
 * model.toml 的数据在后端是扁平结构：providers / models 是两个对象数组，
 * profiles 是固定键的用途字典；"provider → 该提供商下的模型"与
 * "模型 → 引用它的 profile"都是派生关系，页面上按关系分节呈现。
 * 本模块集中承载分组计算、引用方计算、改名级联与合成 key 解析，
 * 保持与 Vue 组件解耦以便单测。
 */

import type { ConfigFieldSchema } from '@/types/settings';

// model.toml 在前端 schema 中的三大字段/容器 key（scope 前缀 = 文件 scope "model"）
export const MODEL_GROUP_KEY = 'model';
export const PROVIDERS_KEY = 'model.llm_providers';
export const MODELS_KEY = 'model.llm_models';
export const PROFILES_CONTAINER_KEY = 'model.llm_profiles';

/** 数组元素对象（provider / model 条目），字段名动态，宽松索引签名 */
export type ConfigItem = Record<string, unknown>;

/**
 * 解析合成 key「<arrayKey>.<index>.<relKey>」。
 * 专用页里数组元素内的叶字段不能直接用点分 key 寻址 store（数组按整值写回），
 * 因此给 FieldRenderer 的字段描述符克隆出一个合成 key 用于展示与定位，
 * 实际写入由组件解析回「数组第 index 项的 relKey 字段」后整值提交。
 */
export function parseArrayItemKey(
  key: string,
  arrayKey: string,
): { index: number; relKey: string } | null {
  const prefix = arrayKey + '.';
  if (!key.startsWith(prefix)) return null;
  const rest = key.slice(prefix.length);
  const dot = rest.indexOf('.');
  if (dot < 0) return null;
  const index = Number(rest.slice(0, dot));
  if (!Number.isInteger(index) || index < 0) return null;
  return { index, relKey: rest.slice(dot + 1) };
}

/** 为数组第 index 项的相对子字段克隆出携带合成 key 的字段描述符 */
export function makeItemField(
  sub: ConfigFieldSchema,
  arrayKey: string,
  index: number,
): ConfigFieldSchema {
  return { ...sub, key: `${arrayKey}.${index}.${sub.key}` };
}

/** 数组元素卡与原始值的逐字段差异数（同下标对齐：增删只发生在尾部，下标稳定） */
export function diffCountAgainst(original: ConfigItem | undefined, current: ConfigItem): number {
  if (!original) return 0;
  let count = 0;
  for (const key of Object.keys(current)) {
    if (JSON.stringify(current[key]) !== JSON.stringify(original[key])) count++;
  }
  return count;
}

/** 分组结果：每个 provider 名 → 其下模型（携带在扁平数组中的下标，供整值写回定位） */
export interface ModelGroup {
  byProvider: Map<string, { index: number; model: ConfigItem }[]>;
  unassigned: { index: number; model: ConfigItem }[];
}

/**
 * 按 model.api_provider == provider.name 把扁平模型数组分桶。
 * 数据仍是扁平数组，分桶只是呈现层的派生视图；api_provider 指向不存在
 * provider 的模型落入 unassigned（编辑中途的悬空引用不丢不藏）。
 */
export function groupModelsByProvider(
  models: ConfigItem[],
  providers: ConfigItem[],
): ModelGroup {
  const providerNames = new Set(providers.map(p => String(p?.name ?? '')));
  const byProvider = new Map<string, { index: number; model: ConfigItem }[]>();
  for (const name of providerNames) byProvider.set(name, []);
  const unassigned: { index: number; model: ConfigItem }[] = [];
  models.forEach((model, index) => {
    const provider = String(model?.api_provider ?? '');
    const bucket = byProvider.get(provider);
    if (bucket) bucket.push({ index, model });
    else unassigned.push({ index, model });
  });
  return { byProvider, unassigned };
}

/** 改 provider 名时受影响的模型：api_provider 指向旧名的模型名列表（按当前值计算） */
export function providerNameReferences(models: ConfigItem[], oldProviderName: string): string[] {
  return models
    .filter(m => String(m?.api_provider ?? '') === oldProviderName)
    .map(m => String(m?.name ?? ''));
}

/** 改 model 名时受影响的 profile：model_list 含旧名的 profile key 列表 */
export function modelNameReferences(
  profileEntries: { key: string; modelList: string[] }[],
  oldModelName: string,
): string[] {
  return profileEntries.filter(e => e.modelList.includes(oldModelName)).map(e => e.key);
}

/** provider 改名的级联：api_provider 指向旧名的模型改为新名（其余原样返回，不共享引用） */
export function applyProviderRenameToModels(
  models: ConfigItem[],
  oldName: string,
  newName: string,
): ConfigItem[] {
  return models.map(m =>
    String(m?.api_provider ?? '') === oldName ? { ...m, api_provider: newName } : m,
  );
}

/**
 * model 改名的级联：model_list 含旧名的 profile 换成新名。
 * 只返回发生变化的 profile 条目（每个 profile.model_list 是独立的数组 key，
 * 由组件按各自 key 走同一条逐 key 更新路径提交）。
 */
export function applyModelRenameToProfileLists(
  profileEntries: { key: string; modelList: string[] }[],
  oldName: string,
  newName: string,
): { key: string; modelList: string[] }[] {
  const changed: { key: string; modelList: string[] }[] = [];
  for (const entry of profileEntries) {
    if (!entry.modelList.includes(oldName)) continue;
    changed.push({
      key: entry.key,
      modelList: entry.modelList.map(n => (n === oldName ? newName : n)),
    });
  }
  return changed;
}

/** 从数组元素的子字段树构造新条目默认值（默认值深拷贝，避免多个新条目共享引用） */
export function buildDefaultItem(fields: ConfigFieldSchema[]): ConfigItem {
  const item: ConfigItem = {};
  for (const f of fields) {
    item[f.key] = f.default === undefined ? undefined : JSON.parse(JSON.stringify(f.default));
  }
  return item;
}

/** 在既有名字集合上生成不重名的名字：base 未占用直接用，否则追加 -2、-3… */
export function uniqueName(existing: string[], base: string): string {
  if (!existing.includes(base)) return base;
  for (let n = 2; ; n++) {
    const candidate = `${base}-${n}`;
    if (!existing.includes(candidate)) return candidate;
  }
}

/** profile 展示名：固定用途集合的中文名映射，未知键回退原名 */
const PROFILE_LABELS: Record<string, string> = {
  planner: '决策 Profile',
  replyer: '表达 Profile',
  summary: '摘要 Profile',
  minecraft: '游戏 Profile',
  minecraft_builder: '建筑设计 Profile',
  vision: '视觉 Profile',
  simulator: '模拟 Profile',
  tts: '语音合成 Profile',
};

export function profileLabel(profileKey: string): string {
  const short = profileKey.slice(PROFILES_CONTAINER_KEY.length + 1);
  return PROFILE_LABELS[short] ?? short;
}

/**
 * 模型的反向引用 chips：被哪些 profile 引用及角色。
 * 主模型（model_list[0]）直接显示用途名，备选追加「·备选」后缀，
 * 供模型注册表"被用途引用"列与删除确认文案复用。
 */
export function modelUsageChips(
  profileEntries: { key: string; modelList: string[] }[],
  modelName: string,
): string[] {
  const chips: string[] = [];
  for (const entry of profileEntries) {
    if (!entry.modelList.includes(modelName)) continue;
    const label = profileLabel(entry.key);
    chips.push(entry.modelList[0] === modelName ? label : `${label}·备选`);
  }
  return chips;
}

/**
 * 悬空引用检测：model_list 中不在已注册模型名集合内的名字。
 * 用途卡上仅在有悬空引用时出警示条，正常引用零提示。
 */
export function danglingModelRefs(modelList: string[], registeredNames: string[]): string[] {
  const registered = new Set(registeredNames);
  return [...new Set(modelList.filter(n => n !== '' && !registered.has(n)))];
}

/** provider 密钥状态：api_key 有非空值即视为已设置（占位符 sk-dummy 也按已设置呈现） */
export function apiKeySet(value: unknown): boolean {
  return String(value ?? '').trim() !== '';
}

/** 单价显示：数值一律带 ¥ 前缀（0 也要显示，表示本地/免费），缺值显示 — */
export function formatPrice(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `¥${n}`;
}
