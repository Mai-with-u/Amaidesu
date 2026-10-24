<!-- 工具调用卡（tool.result.*）：状态徽标（成功/失败）+ 来源徽标（主播决策/游戏 Agent）拆双槽。
     工具卡一律中性底——状态由左边线色 + 徽标承载，避免高频工具行刷成警报墙。
     轮次跳转收在头部蓝字链接（整卡可点误触率高）；compact 下成功徽标与载荷折叠区不渲染 -->
<script setup lang="ts">
import { ref } from 'vue';
import { CopyDocument } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import { agentGroupOf, relativeTime } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';
import type { CardProps } from './registry';

defineProps<CardProps>();

const host = useTimelineCardHost();

/** 载荷懒渲染：首次展开才挂载 JSON 树。声明在卡实例内——条目卸载即随实例回收，
 *  外壳无需再做展开集合的随列修剪 */
const payloadOpened = ref(false);

function onPayloadToggle(event: Event): void {
  if ((event.target as HTMLDetailsElement).open) payloadOpened.value = true;
}

/** 工具卡的 detail 字段读取守卫——liveFeed.fromToolResult 总是写入 detail，但回看路径或老事件可能为 null */
function toolDetail(entry: CardProps['entry']): {
  args: unknown;
  result: unknown;
  error_message: string;
} {
  const detail = entry.detail;
  if (!detail) return { args: null, result: null, error_message: '' };
  return {
    args: detail.args ?? null,
    result: detail.result ?? null,
    error_message: typeof detail.error_message === 'string' ? detail.error_message : '',
  };
}

/** 折叠区可见性：args / result / error_message 任一非空即视为有可展示载荷 */
function hasToolDetail(entry: CardProps['entry']): boolean {
  const d = toolDetail(entry);
  return hasToolArgs(entry) || hasToolResult(entry) || (entry.failed && d.error_message.length > 0);
}
function hasPayloadValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.length > 0;
  if (typeof value === 'object' && !Array.isArray(value)) return Object.keys(value).length > 0;
  return true;
}
function hasToolArgs(entry: CardProps['entry']): boolean {
  return hasPayloadValue(toolDetail(entry).args);
}
function hasToolResult(entry: CardProps['entry']): boolean {
  return hasPayloadValue(toolDetail(entry).result);
}
function toolErrorOf(entry: CardProps['entry']): string {
  return toolDetail(entry).error_message;
}

/** vue-json-pretty 的 data 仅接受 JSON 结构；事件 payload 来自线上数据用 JSON 往返归一化
 * （剥掉 undefined / 函数等非 JSON 值），避免直接强转掩盖真实脏数据。
 * 序列化结果按载荷对象引用缓存（WeakMap）：同一事件的 args/result 在缓冲区里引用稳定，
 * 列表每次重渲染不必对全量载荷重复 JSON 往返——高频流式更新下的主要 CPU 开销来源 */
type JsonData = string | number | boolean | null | JsonData[] | { [key: string]: JsonData };
const jsonDataCache = new WeakMap<object, JsonData>();
function toJsonData(value: unknown): JsonData {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'object') return JSON.parse(JSON.stringify(value)) as JsonData;
  const cached = jsonDataCache.get(value);
  if (cached !== undefined) return cached;
  const parsed = JSON.parse(JSON.stringify(value)) as JsonData;
  jsonDataCache.set(value, parsed);
  return parsed;
}

/** 复制交互与 LLM 历史页一致：剪贴板写入格式化 JSON；循环引用等异常落兜底文本 */
async function copyJson(data: unknown): Promise<void> {
  let text = '';
  try {
    text = JSON.stringify(data, null, 2);
  } catch {
    text = '[不可序列化]';
  }
  await navigator.clipboard.writeText(text);
}
async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}
</script>

<template>
  <div
    class="act is-tool-neutral"
    :class="{
      'is-failed': entry.failed,
      'is-speak': entry.speak,
      'is-agent-streamer': agentGroupOf(entry) === 'streamer',
      'is-agent-game': agentGroupOf(entry) === 'game',
    }"
  >
    <div class="act-head">
      <span class="act-kind">工具调用</span>
      <code class="act-tool mono">{{ entry.actor }}</code>
      <!-- 状态徽标：成功/失败。compact 模式下成功徽标隐藏（失败照常显示） -->
      <span
        v-if="entry.badge && (!host.compact || entry.failed)"
        class="act-badge"
        :class="{ 'is-success-badge': entry.badge === '成功' }"
      >
        {{ entry.badge }}
      </span>
      <!-- 来源徽标（中性色，与状态徽标区分不抢视觉） -->
      <span v-if="entry.source" class="act-source">{{ entry.source }}</span>
      <span class="grow" />
      <a
        v-if="host.isRoundClickable(entry)"
        class="d-link"
        title="在 Agent 页查看这轮决策"
        @click.stop="host.onRowClick(entry)"
      >
        这轮决策 ↗
      </a>
      <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
    </div>
    <!-- 参数药丸：入参轮廓一眼可扫；入参为空/非对象时回退纯文本正文 -->
    <div v-if="entry.argPills.length > 0" class="act-args">
      <span v-for="pill in entry.argPills" :key="pill.key" class="arg-pill">
        <span class="arg-key">{{ pill.key }}</span>
        <span class="arg-value">{{ pill.value }}</span>
      </span>
    </div>
    <p v-else-if="entry.text" class="act-text">{{ entry.text }}</p>
    <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
    <!-- 参数/结果折叠区：compact 下不渲染（首页保持紧凑）；detail 为空时整段不渲染。
         JSON 树与复制交互对齐 LLM 历史页（vue-json-pretty + 剪贴板） -->
    <details
      v-if="!host.compact && hasToolDetail(entry)"
      class="t-payload"
      :class="{ 'is-failed': entry.failed }"
      @toggle="onPayloadToggle($event)"
    >
      <summary @click.stop>参数 / 结果</summary>
      <!-- 载荷懒渲染：展开过才挂载（details 折叠态下子节点仍会进 DOM，
           大 JSON 树在高频重渲染的时间线里是主要渲染成本） -->
      <template v-if="payloadOpened">
        <!-- 失败时错误文本优先展示（开发者定位异常最直接的线索） -->
        <div v-if="entry.failed && toolErrorOf(entry)" class="t-payload-block">
          <div class="t-payload-head">
            <span class="t-payload-tag t-payload-tag--error">错误</span>
            <el-icon
              class="copy-icon"
              role="button"
              tabindex="0"
              aria-label="复制错误信息"
              title="复制错误信息"
              @click.stop="copyText(toolErrorOf(entry))"
              @keydown.enter.prevent="copyText(toolErrorOf(entry))"
              @keydown.space.prevent="copyText(toolErrorOf(entry))"
            >
              <CopyDocument />
            </el-icon>
          </div>
          <pre class="t-payload-text mono">{{ toolErrorOf(entry) }}</pre>
        </div>
        <div v-if="hasToolArgs(entry)" class="t-payload-block">
          <div class="t-payload-head">
            <span class="t-payload-tag">参数</span>
            <el-icon
              class="copy-icon"
              role="button"
              tabindex="0"
              aria-label="复制参数 JSON"
              title="复制 JSON"
              @click.stop="copyJson(toolDetail(entry).args)"
              @keydown.enter.prevent="copyJson(toolDetail(entry).args)"
              @keydown.space.prevent="copyJson(toolDetail(entry).args)"
            >
              <CopyDocument />
            </el-icon>
          </div>
          <div class="t-payload-json">
            <VueJsonPretty :data="toJsonData(toolDetail(entry).args)" theme="dark" show-line />
          </div>
        </div>
        <div v-if="hasToolResult(entry)" class="t-payload-block">
          <div class="t-payload-head">
            <span class="t-payload-tag">结果</span>
            <el-icon
              class="copy-icon"
              role="button"
              tabindex="0"
              aria-label="复制结果 JSON"
              title="复制 JSON"
              @click.stop="copyJson(toolDetail(entry).result)"
              @keydown.enter.prevent="copyJson(toolDetail(entry).result)"
              @keydown.space.prevent="copyJson(toolDetail(entry).result)"
            >
              <CopyDocument />
            </el-icon>
          </div>
          <div class="t-payload-json">
            <VueJsonPretty :data="toJsonData(toolDetail(entry).result)" theme="dark" show-line />
          </div>
        </div>
      </template>
    </details>
  </div>
</template>

<style scoped>
/* 工具调用卡特例：失败/成功不再刷整行底色——工具调用是高频行，红底视觉噪音过大。
 * 状态由左边线色 + 徽标承载；卡体统一中性面板底（继承 .act 默认的 --color-tool-bg） */
.act.is-tool-neutral {
  background: var(--bg-card);
}
/* 工具卡正文（入参摘要）钳制两行：长值全文在"参数/结果"折叠面板，正文只保留调用轮廓 */
.act.is-tool-neutral .act-text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
/* 参数药丸：键灰值亮的圆角浅底块——入参轮廓一眼可扫，全文在"参数/结果"折叠面板 */
.act-args {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 2px 0 0;
}
.arg-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  max-width: 100%;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-active);
  border: 1px solid var(--border-color-light);
  font-size: 11px;
  line-height: 1.7;
}
.arg-key {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-secondary);
}
.arg-key::after {
  content: '·';
  margin-left: 6px;
  color: var(--text-secondary);
}
.arg-value {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 36ch;
}
.act.is-tool-neutral.is-failed {
  background: var(--bg-card);
  border-left-color: var(--color-danger);
  border-left-width: 2px;
}
.act.is-tool-neutral.is-agent-streamer,
.act.is-tool-neutral.is-agent-game {
  background: var(--bg-card);
}
.act.is-tool-neutral.is-agent-game.is-failed {
  background: var(--bg-card);
  border-left-color: var(--color-danger);
}
/* 成功无显式 is-success 类——沿用 .act 默认的 --color-tool 边线（绿色系）即可 */
.act.is-tool-neutral.is-speak {
  background: var(--bg-card);
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 参数/结果折叠区：形态对齐 LLM 历史页（暗底 JSON 树 + 悬浮复制），
   summary 压到 11px 弱色，避免与卡片主文案争夺层级 */
.t-payload {
  margin-top: 6px;
}
.t-payload summary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.t-payload summary::-webkit-details-marker {
  display: none;
}
.t-payload summary::before {
  content: '▸';
  font-size: 11px;
  transition: transform 0.15s ease;
}
.t-payload[open] summary::before {
  transform: rotate(90deg);
}
.t-payload summary:hover {
  color: var(--text-primary);
}
.t-payload-block {
  margin-top: 6px;
}
.t-payload-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.t-payload-tag {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}
.t-payload-tag--error {
  color: var(--color-danger);
}
.t-payload-head .copy-icon {
  font-size: 12px;
  color: var(--text-placeholder);
  cursor: pointer;
}
.t-payload-head .copy-icon:hover {
  color: var(--text-primary);
}
.t-payload-json {
  margin-top: 4px;
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
}
/* vue-json-pretty 的 key 不自带颜色（继承容器文字色，浅色主题下是深灰），
   深底上必须显式给高对比配色；字符串/数字用比库默认更亮的变体 */
.t-payload-json :deep(.vjs-tree) {
  color: #d4d4d4;
}
.t-payload-json :deep(.vjs-value-string) {
  color: #7ee787;
}
.t-payload-json :deep(.vjs-value-number),
.t-payload-json :deep(.vjs-value-boolean) {
  color: #79c0ff;
}
.t-payload-json :deep(.vjs-value-null) {
  color: #79c0ff;
}
.t-payload-text {
  margin: 4px 0 0;
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-danger);
}
</style>
