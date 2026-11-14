// 设置页钉固条目的 localStorage 持久化。
// 钉固是纯前端收藏（记录分组 key 与展示名），不进配置文件；
// 数据损坏时按空列表处理并重写，不让坏数据卡住页面。

export interface PinnedEntry {
  /** 分组完整 API 键（如 agents.agents.minecraft.context） */
  key: string;
  /** 收藏时的展示名（分组中文名可能随后端变化，容忍过期） */
  label: string;
  /** 所属配置文件 Tab 键（如 agents.toml） */
  fileKey: string;
}

const STORAGE_KEY = 'settings-pinned-entries';
const MAX_ENTRIES = 12;

export function loadPinnedEntries(): PinnedEntry[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const valid = parsed.filter(
      (item): item is PinnedEntry =>
        !!item &&
        typeof item === 'object' &&
        typeof (item as PinnedEntry).key === 'string' &&
        typeof (item as PinnedEntry).label === 'string' &&
        typeof (item as PinnedEntry).fileKey === 'string',
    );
    return valid.slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

export function savePinnedEntries(entries: PinnedEntry[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  } catch {
    // 存储不可用（隐私模式/配额满）时钉固退化为会话内生效，静默降级
  }
}
