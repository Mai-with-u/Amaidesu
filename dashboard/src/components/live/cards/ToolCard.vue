<!-- 工具调用卡（tool.result.*）：状态徽标（成功/失败）+ 来源徽标（主播决策/游戏 Agent）拆双槽。
     工具卡一律中性底——状态由左边线色 + 徽标承载，避免高频工具行刷成警报墙。
     默认单行：头行内并排工具名、状态徽标、参数药丸（超宽截断）与时间戳；
     载荷入口收在头行右侧小字（失败卡显示"错误"并用错误色）。
     轮次跳转收在头部蓝字链接（整卡可点误触率高）；compact 下成功徽标与载荷入口不渲染。
     maicraft_execute（游戏 Agent 下达 Minecraft 目标的调用）按 actor 分流出专用卡面：
     头行同构，正文默认展开能力/意图/要点，steps 可展开，完成时显示结果总结。
     framework_delegate（主播 Agent 派活给游戏 Agent）同款分流：徽标改已受理/已排队，
     头行尾缀目标 Agent，正文为指令与执行方复述（均 2 行钳制 + 合并展开开关） -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { CopyDocument } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import { formatLatency, formatTimeHMS } from '@/utils/format';
import {
  agentGroupOf,
  frameworkDelegateSummary,
  isFrameworkDelegateCard,
  isMinecraftExecuteEntry,
  maicraftExecuteSummary,
  minecraftGoalIdOf,
  minecraftGoalOperationOf,
} from '@/utils/liveFeed';
import type { FrameworkDelegateSummary, MinecraftExecuteSummary } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';
import type { CardProps } from './registry';

const props = defineProps<CardProps>();

const host = useTimelineCardHost();

/** 载荷懒渲染：首次展开才挂载 JSON 树。声明在卡实例内——条目卸载即随实例回收，
 *  外壳无需再做展开集合的随列修剪 */
const payloadOpen = ref(false);

function togglePayload(): void {
  payloadOpen.value = !payloadOpen.value;
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

// maicraft_execute 专用卡面：按 actor 分流（工具名两种拼写都覆盖）。
// 失败卡不进专用面（走通用错误路径）；入参解析不出 ability/purpose/steps 时
// 同样退回通用形态，避免老事件渲染成空卡
const exec = computed<MinecraftExecuteSummary | null>(() => {
  if (!isMinecraftExecuteEntry(props.entry) || props.entry.failed) return null;
  const d = toolDetail(props.entry);
  const summary = maicraftExecuteSummary(d.args, d.result);
  const usable =
    summary.ability || summary.purpose || summary.keyPoints || summary.steps.length > 0;
  return usable ? summary : null;
});

/** 状态徽标文案：异步受理态 running=执行中，finished=完成；其余（缺失等）按执行中处理 */
const execStateBadge = computed(() => (exec.value?.state === 'finished' ? '完成' : '执行中'));

/** 任务终态徽标：execute 卡与 task.changed 任务窗口按 goal_id 配对后回填的
 *  "终态 · 耗时"（如"完成 · 9.8s"），替换掉"执行中/完成"；未配对为 null */
const execEndBadge = computed(() => {
  const end = props.entry.taskEnd;
  if (!end) return null;
  const duration = end.durationMs != null ? ` · ${formatLatency(end.durationMs)}` : '';
  return { label: `${end.statusLabel}${duration}`, failed: end.failed };
});

/** 步骤单行预览："步骤 N：①〈purpose〉②…"，缺 purpose 的步骤回退能力名 */
const execStepsPreview = computed(() => {
  const steps = exec.value?.steps ?? [];
  const items = steps
    .map((s, i) => `${stepMark(i)}${s.purpose || s.ability}`)
    .join(' ')
    .trim();
  return `步骤 ${steps.length}：${items}`;
});

/** 步骤序号标记：圈号 ①-⑳，超出回退数字 */
const STEP_MARKS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳';
function stepMark(index: number): string {
  return STEP_MARKS[index] ?? `${index + 1}.`;
}

/** 步骤展开态：声明在卡实例内，条目卸载即随实例回收 */
const execStepsOpen = ref(false);

function toggleExecSteps(): void {
  execStepsOpen.value = !execStepsOpen.value;
}

/** 进度查询明细展开态：同样由卡实例自持 */
const pollsOpen = ref(false);

function togglePolls(): void {
  pollsOpen.value = !pollsOpen.value;
}

// framework_delegate 专用卡面：按 actor 分流（失败卡走通用错误路径）。
// 受理态缺失（accepted=false 且未排队）或指令为空的旧事件退回通用形态
const delegate = computed<FrameworkDelegateSummary | null>(() => {
  if (!isFrameworkDelegateCard(props.entry)) return null;
  const d = toolDetail(props.entry);
  const summary = frameworkDelegateSummary(d.args, d.result);
  return summary.instruction && (summary.accepted || summary.queued) ? summary : null;
});

/** 状态徽标：排队优先于受理（排队必然也受理了，"已排队 · 第 N 位"信息量更大） */
const delegateBadge = computed(() => {
  const s = delegate.value;
  if (!s) return null;
  if (s.queued) {
    return {
      label: s.queuePosition != null ? `已排队 · 第 ${s.queuePosition} 位` : '已排队',
      success: true,
    };
  }
  return { label: '已受理', success: true };
});

// 指令/复述两行共用一个展开开关：两行是对照阅读的关系，合并开关一次放开
// 双行钳制；行数未超 2 行时不渲染入口（渲染后按实际溢出量测）
const delegateExpanded = ref(false);
const delegateOverflow = ref(false);
/** 钳制行元素（指令行 + 复述行）：函数式 ref 收集，溢出量测用；卸载回调入参为 null 时跳过 */
const delegateLinesEl = ref<HTMLElement[]>([]);

function setDelegateLineEl(el: unknown): void {
  if (el instanceof HTMLElement && !delegateLinesEl.value.includes(el)) {
    delegateLinesEl.value.push(el);
  }
}

function measureDelegateOverflow(): void {
  delegateOverflow.value = delegateLinesEl.value.some(
    el => el != null && el.scrollHeight > el.clientHeight + 1,
  );
}

function toggleDelegateExpand(): void {
  delegateExpanded.value = !delegateExpanded.value;
}

// 溢出量测依赖钳制样式已生效：挂载后与摘要变化（受理结果异步补齐 own_words 等）后各测一次
onMounted(() => nextTick(measureDelegateOverflow));
watch(delegate, () => {
  delegateExpanded.value = false;
  delegateLinesEl.value = [];
  nextTick(measureDelegateOverflow);
});

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
      <!-- 专用卡面状态徽标：配对到任务终态时显示"终态 · 耗时"（失败类红色），
           未配对维持执行中/完成（完成沿用成功徽标样式），预演加灰色徽标 -->
      <template v-if="exec && !host.compact">
        <span
          v-if="execEndBadge"
          class="act-badge"
          :class="{ 'is-success-badge': !execEndBadge.failed }"
        >
          {{ execEndBadge.label }}
        </span>
        <span
          v-else
          class="act-badge"
          :class="exec.state === 'finished' ? 'is-success-badge' : 'is-silent-badge'"
        >
          {{ execStateBadge }}
        </span>
        <span v-if="exec.dryRun" class="act-badge is-dry-run-badge">预演</span>
      </template>
      <!-- 派活卡状态徽标：已受理 / 已排队 · 第 N 位（绿色成功样式） -->
      <template v-else-if="delegate && !host.compact">
        <span v-if="delegateBadge" class="act-badge is-success-badge">
          {{ delegateBadge.label }}
        </span>
      </template>
      <span
        v-else-if="entry.badge && (!host.compact || entry.failed)"
        class="act-badge"
        :class="{ 'is-success-badge': entry.badge === '成功' }"
      >
        {{ entry.badge }}
      </span>
      <!-- 来源徽标（中性色，与状态徽标区分不抢视觉）；派活卡换成目标 Agent（游戏色） -->
      <span v-if="delegate" class="act-source is-delegate-target" :title="delegate.targetAgent">
        → {{ delegate.targetAgent || '游戏 Agent' }}
      </span>
      <span v-else-if="entry.source" class="act-source">{{ entry.source }}</span>
      <!-- 参数药丸并入头行：超宽整体截断，完整参数在载荷区；入参为空时回退纯文本正文。
           专用卡面头行不塞药丸——要点在第 2 行平文展示 -->
      <div v-if="!exec && !delegate && entry.argPills.length > 0" class="act-args">
        <span v-for="pill in entry.argPills" :key="pill.key" class="arg-pill">
          <span class="arg-key">{{ pill.key }}</span>
          <span class="arg-value">{{ pill.value }}</span>
        </span>
      </div>
      <p v-else-if="!exec && !delegate && entry.text" class="act-text">{{ entry.text }}</p>
      <span class="grow" />
      <!-- 载荷入口：头行右侧小字，失败卡点名"错误"并用错误色 -->
      <button
        v-if="!host.compact && hasToolDetail(entry)"
        type="button"
        class="payload-toggle"
        :class="{ 'is-error': entry.failed }"
        @click.stop="togglePayload"
      >
        {{ entry.failed ? '错误 ▸' : '载荷 ▸' }}
      </button>
      <a
        v-if="host.isRoundClickable(entry)"
        class="d-link"
        title="在 Agent 页查看这轮决策"
        @click.stop="host.onRowClick(entry)"
      >
        这轮决策 ↗
      </a>
      <time class="stamp mono">{{ formatTimeHMS(entry.tsMs) }}</time>
    </div>
    <!-- maicraft_execute 专用正文：意图行默认展开（这是实际进行游戏操作的卡，信息量最大）；
         有分步计划时单行预览可点开纵向列；完成态第三行显示结果总结，执行中不渲染 -->
    <div v-if="exec" class="exec-body">
      <p class="exec-line exec-main">
        <code v-if="exec.ability" class="exec-ability mono">{{ exec.ability }}</code>
        <span v-if="exec.purpose" class="exec-purpose">{{ exec.purpose }}</span>
        <span v-if="exec.keyPoints" class="exec-points">{{ exec.keyPoints }}</span>
      </p>
      <div v-if="exec.steps.length > 0" class="exec-steps">
        <button
          type="button"
          class="exec-steps-line"
          :title="execStepsOpen ? '收起分步计划' : '展开分步计划'"
          @click.stop="toggleExecSteps"
        >
          {{ execStepsPreview }}
        </button>
        <ol v-if="execStepsOpen" class="exec-steps-list">
          <li v-for="(step, i) in exec.steps" :key="i" class="exec-step">
            <span class="exec-step-mark">{{ stepMark(i) }}</span>
            <code v-if="step.ability" class="exec-ability mono">{{ step.ability }}</code>
            <span v-if="step.purpose">{{ step.purpose }}</span>
          </li>
        </ol>
      </div>
      <p v-if="exec.state === 'finished' && exec.summary" class="exec-line exec-summary">
        {{ exec.summary }}
      </p>
      <!-- 收拢的进度查询（liveLanes.collapseGoalPolls 挂到 polls 字段）：默认一行小字，
           展开逐条列出时刻 + goal_id + 操作；展开状态由卡实例自持 -->
      <div v-if="entry.polls && entry.polls.length > 0" class="exec-polls">
        <button
          type="button"
          class="exec-polls-toggle"
          :title="pollsOpen ? '收起进度查询' : '展开进度查询'"
          @click.stop="togglePolls"
        >
          进度查询 ×{{ entry.polls.length }} {{ pollsOpen ? '▴' : '▸' }}
        </button>
        <ul v-if="pollsOpen" class="exec-polls-list">
          <li v-for="poll in entry.polls" :key="poll.id" class="exec-polls-item">
            <time class="mono">{{ formatTimeHMS(poll.tsMs) }}</time>
            <span class="mono">goal_id {{ minecraftGoalIdOf(poll) ?? '—' }}</span>
            <span>{{ minecraftGoalOperationOf(poll) || 'get' }}</span>
          </li>
        </ul>
      </div>
    </div>
    <!-- framework_delegate 专用正文：指令行默认展开（2 行钳制 + 展开全文），
         执行方复述与指令字面不同才渲染；排队时一行小字说明（全文在载荷区） -->
    <div v-else-if="delegate" class="delegate-body">
      <p
        :ref="el => setDelegateLineEl(el)"
        class="delegate-line"
        :class="{ 'is-clamped': !delegateExpanded }"
      >
        <span class="delegate-label">指令：</span><span class="delegate-text">{{ delegate.instruction }}</span>
      </p>
      <p
        v-if="delegate.ownWords"
        :ref="el => setDelegateLineEl(el)"
        class="delegate-line"
        :class="{ 'is-clamped': !delegateExpanded }"
      >
        <span class="delegate-label">复述：</span><span class="delegate-text">{{ delegate.ownWords }}</span>
      </p>
      <button
        v-if="delegateOverflow"
        type="button"
        class="delegate-toggle"
        :title="delegateExpanded ? '收起' : '展开全文'"
        @click.stop="toggleDelegateExpand"
      >
        {{ delegateExpanded ? '收起 ▴' : '展开 ▾' }}
      </button>
      <p v-if="delegate.queued && delegate.queueNote" class="delegate-queue-note">
        {{ delegate.queueNote }}
      </p>
    </div>
    <p v-else-if="entry.note" class="act-note">{{ entry.note }}</p>
    <!-- 参数/结果载荷区：compact 下不渲染（首页保持紧凑）；detail 为空时整段不渲染。
         JSON 树与复制交互对齐 LLM 历史页（vue-json-pretty + 剪贴板） -->
    <div v-if="!host.compact && payloadOpen" class="t-payload">
      <!-- 载荷懒渲染：展开才挂载——大 JSON 树在高频重渲染的时间线里是主要渲染成本 -->
      <template v-if="payloadOpen">
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
    </div>
  </div>
</template>

<style scoped>
/* 工具调用卡特例：失败/成功不再刷整行底色——工具调用是高频行，红底视觉噪音过大。
 * 状态由左边线色 + 徽标承载；卡体统一中性面板底（继承 .act 默认的 --color-tool-bg） */
.act.is-tool-neutral {
  background: var(--bg-card);
}
/* 工具卡正文（无药丸时的回退文本）并入头行：超宽截断，全文在载荷区 */
.act.is-tool-neutral .act-text {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 参数药丸并入头行：单行不换行，容器截断超宽部分——入参轮廓一眼可扫，全文在载荷区 */
.act-args {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  /* 药丸区是头行里的可让出部分：先压缩药丸，工具名与时间戳不被挤占 */
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
}
.arg-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  flex: none;
  max-width: 100%;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-active);
  border: 1px solid var(--border-color-light);
  font-size: 11px;
  line-height: 1.7;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
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

/* maicraft_execute 专用卡面：正文行单行截断（超长省略，全文在载荷区）；
 * 灰底"预演"徽标区别于状态徽标（执行中=中性灰、完成=绿色成功样式） */
.is-dry-run-badge {
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-dark);
  font-weight: 600;
}
.exec-body {
  margin-top: 4px;
  min-width: 0;
}
.exec-line {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
}
.exec-main {
  font-size: 12px;
  color: var(--text-primary);
}
.exec-ability {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--color-game);
}
.exec-purpose {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.exec-points {
  flex-shrink: 0;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
}
.exec-steps {
  margin-top: 2px;
  min-width: 0;
}
.exec-steps-line {
  display: block;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.exec-steps-line:hover {
  color: var(--text-primary);
}
.exec-steps-list {
  margin: 4px 0 0;
  padding-left: 0;
  list-style: none;
}
.exec-step {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
}
.exec-step-mark {
  flex-shrink: 0;
}
.exec-step > span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
}
.exec-summary {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-secondary);
}

/* 收拢的进度查询：入口与"载荷 ▸"同级低调（11px 次要色小字），展开为逐条明细 */
.exec-polls {
  margin-top: 2px;
  min-width: 0;
}
.exec-polls-toggle {
  display: block;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
}
.exec-polls-toggle:hover {
  color: var(--text-primary);
}
.exec-polls-list {
  margin: 2px 0 0;
  padding-left: 0;
  list-style: none;
}
.exec-polls-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
}

/* framework_delegate 专用卡面：指令/复述行自动换行，收起时 2 行钳制，
   展开开关放开钳制；排队说明单行截断（全文在载荷区） */
.delegate-body {
  margin-top: 4px;
  min-width: 0;
}
.delegate-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-primary);
  word-break: break-word;
}
.delegate-line.is-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.delegate-label {
  color: var(--text-secondary);
}
.delegate-toggle {
  display: block;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  user-select: none;
}
.delegate-toggle:hover {
  color: var(--text-primary);
}
.delegate-queue-note {
  margin: 2px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 目标 Agent 徽标：沿用 act-source 形状，文字与描边换游戏色（跨轨去向一眼可辨） */
.act-source.is-delegate-target {
  color: var(--color-game);
  border-color: var(--color-game-bg);
  max-width: 24ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  /* 工具名是头行里的不可让出部分：不许被药丸挤窄（工具名很短，截断即丢关键信息） */
  flex-shrink: 0;
  max-width: 30ch;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 参数/结果载荷区：形态对齐 LLM 历史页（暗底 JSON 树 + 悬浮复制），
   入口是头行右侧的 11px 小字按钮，避免与卡片主文案争夺层级 */
.payload-toggle {
  flex-shrink: 0;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}
.payload-toggle:hover {
  color: var(--text-primary);
}
.payload-toggle.is-error {
  color: var(--color-danger);
}
.t-payload {
  margin-top: 6px;
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
