/**
 * 技能检视页类型（对应后端 /api/v1/skills 只读端点）
 *
 * 技能是"怎么把一类事做成"的经验文档：本地技能随仓库发布（各 Agent 包内的
 * skills 子目录），远端玩法技能由游戏 Agent 运行期从 MaiCraft 登记（内存持有，不落库）。
 */

export interface SkillItem {
  /** 技能名（模型读取正文时填写的参数） */
  name: string;
  /** 一句话说明什么时候用 */
  description: string;
  /** 技能分类（目录按此分组） */
  category: string;
  /** 来源：local 为本地文档，其余为登记来源名（如 MaiCraft） */
  origin: string;
  /** 受众 Agent 注册名列表，或单独的 '*' */
  agents: string[];
  /** 本地技能的来源文件（仓库相对路径）；远端技能为空 */
  path: string;
  /** 远端技能的 skill:// 地址；本地技能为空 */
  uri: string;
}

/** 远端技能登记状态（按可观测性逐级降级） */
export interface SkillRemoteStatus {
  /** 游戏 Agent 是否可观测（已注册且实例可取） */
  observable: boolean;
  /** 游戏 Agent 是否在运行；不可观测时为 null */
  agent_running: boolean | null;
  /** 本轮连接是否已读过 MaiCraft 技能目录（读到空目录也算已读）；不可观测时为 null */
  remote_loaded: boolean | null;
  /** 技能库中 MaiCraft 来源的条目数 */
  remote_count: number;
}

export interface SkillsSnapshotResponse {
  entries: SkillItem[];
  remote: SkillRemoteStatus;
}

export interface SkillBodyResponse {
  name: string;
  /** 本地技能的来源文件（仓库相对路径）；远端技能为空 */
  path: string;
  content: string;
  /** 正文来源：local 为本地文档，其余为代读来源名（如 MaiCraft） */
  source: string;
}
