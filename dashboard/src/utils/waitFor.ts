/**
 * 有界轮询等待：以固定间隔反复探测条件，条件成立即返回；探测 tries 次
 * 仍未成立返回 false，调用方自行决定超时后的降级行为（继续执行 / 提前退出）。
 *
 * 服务"等异步渲染/异步加载就绪"的场景——条件的就绪时刻无法订阅（第三方
 * DOM 渲染、数据管线的旁路信号），只能探测；有界探测防止诊断信号缺失时
 * 无限空转。探测节奏与既有手写形态一致：先探测后等待（条件在首探即成立的
 * 同步场景零等待返回）。
 */

/** 探测参数：tries 次探测、每次间隔 intervalMs 毫秒 */
export interface WaitForOptions {
  tries: number;
  intervalMs: number;
}

export async function waitFor(predicate: () => boolean, options: WaitForOptions): Promise<boolean> {
  for (let attempt = 0; attempt < options.tries; attempt += 1) {
    if (predicate()) return true;
    await new Promise(resolve => setTimeout(resolve, options.intervalMs));
  }
  return false;
}
