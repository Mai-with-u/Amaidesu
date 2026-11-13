/**
 * 直播切片类型（后端契约：src/modules/dashboard/schemas/clips.py + storage models/clipping.py）
 */

/** 段来源：ai = AI 提议；manual = 运营手建 */
export type ClipSegmentOrigin = 'ai' | 'manual';

/** 段状态：suggested 提议 → confirmed 待切 → cutting → done / failed；dismissed 弃用 */
export type ClipSegmentStatus =
  | 'suggested'
  | 'confirmed'
  | 'cutting'
  | 'done'
  | 'failed'
  | 'dismissed';

/** 一行切片段（时间用墙钟毫秒，场时间线坐标） */
export interface ClipSegment {
  id: number;
  live_session_id: number;
  origin: ClipSegmentOrigin;
  start_ms: number;
  end_ms: number;
  title: string;
  reason: string;
  status: ClipSegmentStatus;
  output_path: string;
  error_message: string;
  created_at_ms: number;
  updated_at_ms: number;
}

/** 一个匹配到的录播文件 */
export interface RecordingFileInfo {
  path: string;
  file_name: string;
  started_at_ms: number;
  title: string;
}

/** 一个文件名解析不出录制开始时刻的视频文件（无法自动匹配场次） */
export interface UnmatchedFileInfo {
  path: string;
  file_name: string;
}

/** 切片页场次列表一行 */
export interface ClippedSessionItem {
  live_session_id: number;
  started_at_ms: number;
  ended_at_ms: number | null;
  title: string;
  file_count: number;
}

/** [clipping] 配置段（页内配置卡读写） */
export interface ClipConfig {
  enabled: boolean;
  recording_dir: string;
  ffmpeg_path: string;
  output_dir: string;
  llm_profile: string;
  reencode: boolean;
  min_segment_seconds: number;
  max_segment_seconds: number;
}

/** 切片配置读面（含装配态） */
export interface ClipConfigState {
  assembled: boolean;
  config: ClipConfig;
  recording_dir_exists: boolean;
}

/** 配置保存结果（写盘 + 免重启重装配） */
export interface ClipConfigApplyResult {
  success: boolean;
  message: string;
  assembled: boolean;
  recording_dir_exists: boolean;
}
