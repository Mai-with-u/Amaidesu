/**
 * API 错误消息提取（多页面共享的单一事实源）。
 *
 * axios 错误穿透后端 `{ detail }`：FastAPI 的业务错误 detail 是中文文案字符串；
 * 参数校验失败（422）的 detail 是 `[{ loc, msg, type }]` 数组，取首项 msg 作为
 * 文案。其次取 Error.message，兜底返回调用方给定的 fallback。
 */

/** 从 FastAPI 的 detail 取一句可展示文案：字符串直取，数组取首个可用项（msg 优先） */
function detailText(detail: unknown): string | null {
  if (typeof detail === 'string' && detail) return detail;
  if (!Array.isArray(detail)) return null;
  for (const item of detail) {
    if (typeof item === 'string' && item) return item;
    if (item && typeof item === 'object') {
      const msg = (item as { msg?: unknown }).msg;
      if (typeof msg === 'string' && msg) return msg;
    }
  }
  return null;
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const data = (error as { response?: { data?: { detail?: unknown } } }).response?.data;
    const text = detailText(data?.detail);
    if (text) return text;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** axios 错误的后端 HTTP 状态码；没有 response 的错误（网络/代码异常）返回 null */
export function getApiErrorStatus(error: unknown): number | null {
  if (error && typeof error === 'object' && 'response' in error) {
    const status = (error as { response?: { status?: unknown } }).response?.status;
    if (typeof status === 'number') return status;
  }
  return null;
}
