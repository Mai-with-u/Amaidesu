/**
 * 统一确认框 helper
 *
 * 收敛各页面 `try { await ElMessageBox.confirm(...) } catch { return }` 的
 * 样板：resolve=true 表示用户确认，取消/关闭一律 resolve=false，调用方以
 * `if (!(await confirmAction(...))) return;` 表达"取消即返回"。
 */

import { ElMessageBox } from 'element-plus';
import type { ElMessageBoxOptions } from 'element-plus';

export async function confirmAction(
  message: string,
  title: string,
  options?: ElMessageBoxOptions,
): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
      ...options,
    });
    return true;
  } catch {
    return false;
  }
}
