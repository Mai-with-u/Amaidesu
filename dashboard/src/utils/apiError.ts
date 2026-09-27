/**
 * API 错误消息提取（多页面共享的单一事实源）。
 *
 * axios 错误穿透后端 `{ detail }`（FastAPI 的 400/404/409/422 detail 均为
 * 中文业务文案），其次取 Error.message，兜底返回调用方给定的 fallback。
 * 与既有局部提取器（extractReconnectDetail / extractInvokeDetail /
 * extractHttpError / extractAgentError）同语义，属其收敛归一。
 */

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const data = (error as { response?: { data?: { detail?: unknown } } }).response?.data;
    if (data && typeof data.detail === 'string' && data.detail) return data.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
