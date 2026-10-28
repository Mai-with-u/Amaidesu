<template>
  <el-dialog
    class="start-stream-dialog"
    :model-value="modelValue"
    title="开播"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    @update:model-value="onVisibleChange"
  >
    <div v-if="loadError" class="ss-load-error">
      <span>{{ loadError }}</span>
      <el-button size="small" @click="reload">重试</el-button>
    </div>
    <div v-else v-loading="loading" class="ss-body">
      <div class="ss-field">
        <label class="ss-field-label" for="start-stream-title">场次标题</label>
        <el-input id="start-stream-title" v-model="title" placeholder="留空使用默认名" clearable />
      </div>

      <div class="ss-field">
        <span class="ss-field-label">场次类型</span>
        <el-radio-group v-model="kind" size="small" aria-label="场次类型">
          <el-radio-button value="live">正式直播</el-radio-button>
          <el-radio-button value="rehearsal">彩排（模拟 / 回放 / 调试）</el-radio-button>
        </el-radio-group>
        <p class="ss-section-hint">
          {{
            kind === 'live'
              ? '面向真实观众：回顾进正式记忆线，近况同步给 MaiBot。'
              : '观众是假的或只有自己：回顾只在彩排时被读到，不对外同步；模拟器只能在彩排里开。'
          }}
        </p>
      </div>

      <div class="ss-section-title">本次直播内容</div>
      <p class="ss-section-hint">
        勾选的开播时运行；正在运行但未勾选的会被关闭——调试残留看这里清掉。
      </p>

      <div v-for="group in planGroups" :key="group.id" class="ss-group">
        <div class="ss-group-title">{{ group.title }}</div>

        <!-- 弹幕接入为三选一（身份不互通），单选而非复选 -->
        <div v-if="group.id === 'input'" class="ss-row ss-danmaku-row">
          <el-radio-group v-model="danmaku" size="small" aria-label="B站弹幕接入">
            <el-radio-button v-for="variant in DANMAKU_VARIANTS" :key="variant" :value="variant">
              {{ DANMAKU_LABELS[variant] }}
            </el-radio-button>
            <el-radio-button value="">不接入</el-radio-button>
          </el-radio-group>
        </div>

        <div v-for="item in group.items" :key="`${item.group}/${item.name}`" class="ss-row">
          <el-checkbox
            :model-value="isChecked(item)"
            @update:model-value="toggle(item, $event as boolean)"
          >
            {{ labelOf(item) }}
          </el-checkbox>
          <span
            v-if="item.is_started && !isChecked(item)"
            class="ss-stop-hint"
            title="正在运行，且不在本次开播清单里"
          >
            开播时关闭
          </span>
          <span v-if="companionNotes[item.name]" class="ss-companion-note">
            附属：{{ companionNotes[item.name] }}
          </span>
          <span
            class="ss-run-dot"
            :class="{ 'is-on': item.is_started }"
            :title="item.is_started ? '运行中' : '未运行'"
          />
        </div>
      </div>

      <div class="ss-group">
        <div class="ss-group-title">流程单</div>
        <div class="ss-row">
          <el-select
            v-model="selectedRundownId"
            class="ss-rundown-select"
            aria-label="选择本次直播的流程单"
          >
            <el-option
              v-for="option in rundownOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
      </div>

      <!-- 提交结果：失败不清弹窗，可改单重试 -->
      <div v-if="report" class="ss-report" :class="{ 'is-clean': !hasFailures }">
        <template v-if="!hasFailures"> 场次已开启，组件已按清单对齐。 </template>
        <template v-else>
          <div class="ss-report-title">场次已开启，但部分组件未对齐：</div>
          <div
            v-for="item in failedItems"
            :key="`${item.group}/${item.name}`"
            class="ss-report-item"
          >
            {{ item.name }}（{{ actionLabel(item.action) }}失败）：{{ item.detail || '原因未知' }}
          </div>
          <div v-if="report.config_error" class="ss-report-item">
            启用名单写盘失败：{{ report.config_error }}
          </div>
          <div class="ss-report-hint">
            可修正后重新点「开播」重试；未对齐项也可稍后在组件页处理。
          </div>
        </template>
      </div>
      <div v-if="submitError" class="ss-submit-error">{{ submitError }}</div>
    </div>

    <template #footer>
      <div class="ss-footer">
        <span class="ss-footer-note">
          主播与语音合成等基础设施常驻运行，不在此清单内<span v-if="hasActiveSessionHint"
            >；已有进行中场次，开播将自动结束它</span
          >
        </span>
        <span class="ss-footer-actions">
          <el-button :disabled="submitting" @click="onVisibleChange(false)">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="onSubmit">{{
            kind === 'live' ? '开播' : '开始彩排'
          }}</el-button>
        </span>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 开播引导弹窗：声明场次类型（正式 / 彩排）、配置本次直播要开哪些内容（组件
 * 意图清单）+ 流程单选择，
 * 单请求完成开场次与组件对齐。清单数据与提交逻辑在 useStartStream；
 * 本组件只负责展示与交互编排。干净成功即关弹窗并上抛 opened（父级刷新
 * 侧栏、回实时视图）；部分失败保留弹窗展示回执，允许改单重试。
 */
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { useStartStream } from '@/composables/live/useStartStream';

const props = defineProps<{
  /** 弹窗显隐（v-model） */
  modelValue: boolean;
  /** 打开时是否已有进行中场次（提示将自动结束） */
  activeSessionExists: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
  /** 干净成功（场次开启且组件全对齐）后上抛，父级刷新场次侧栏 */
  opened: [];
}>();

const stream = useStartStream();
const {
  loading,
  loadError,
  submitting,
  submitError,
  report,
  hasFailures,
  failedItems,
  title,
  kind,
  planGroups,
  companionNotes,
  rundowns,
  currentRundownId,
  selectedRundownId,
  danmaku,
  hasActiveSessionHint,
  labelOf,
  DANMAKU_VARIANTS,
  DANMAKU_LABELS,
  isChecked,
  toggle,
} = stream;

const lastActiveHint = ref(props.activeSessionExists);

watch(
  () => props.modelValue,
  visible => {
    if (visible) {
      lastActiveHint.value = props.activeSessionExists;
      void stream.load(props.activeSessionExists);
    }
  },
);

function reload(): void {
  void stream.load(lastActiveHint.value);
}

/** 流程单选项：首位恒为「跟随当前」（不切换就不调 activate），其余为库内模板 */
const rundownOptions = computed(() => {
  const current = currentRundownId.value;
  const options: Array<{ value: string; label: string }> = [];
  const currentDef = rundowns.value.find(r => r.rundown_id === current);
  options.push({
    value: current,
    label: current ? `跟随当前：${currentDef?.title ?? current}` : '跟随当前（内置默认单）',
  });
  for (const definition of rundowns.value) {
    if (definition.rundown_id && definition.rundown_id !== current) {
      options.push({
        value: definition.rundown_id,
        label: `${definition.title}（${definition.segments.length} 环节）`,
      });
    }
  }
  return options;
});

function actionLabel(action: 'start' | 'stop' | 'skip'): string {
  if (action === 'start') return '启动';
  if (action === 'stop') return '关闭';
  return '处理';
}

function onVisibleChange(visible: boolean): void {
  if (submitting.value) return;
  emit('update:modelValue', visible);
}

async function onSubmit(): Promise<void> {
  const clean = await stream.submit();
  if (!clean) return;
  ElMessage.success('场次已开启');
  emit('opened');
  emit('update:modelValue', false);
}
</script>

<style scoped>
.ss-body {
  min-height: 120px;
}

.ss-load-error {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--color-danger);
}

.ss-field {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.ss-field-label {
  flex-shrink: 0;
  width: 56px;
  color: var(--el-text-color-regular);
}

.ss-section-title {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.ss-section-hint {
  margin: 4px 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ss-group {
  padding: 8px 0;
}

.ss-group + .ss-group {
  border-top: 1px solid var(--el-border-color-lighter);
}

.ss-group-title {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ss-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 28px;
}

.ss-row .el-checkbox {
  margin-right: 0;
}

.ss-danmaku-row {
  padding: 2px 0 6px;
}

.ss-stop-hint {
  font-size: 12px;
  color: var(--color-warning);
}

.ss-companion-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ss-run-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  margin-left: auto;
  border-radius: 50%;
  background: var(--el-border-color);
}

.ss-run-dot.is-on {
  background: var(--color-success);
}

.ss-rundown-select {
  width: 100%;
}

.ss-report {
  margin-top: 12px;
  padding: 10px 12px;
  font-size: 13px;
  border-radius: 6px;
  background: var(--color-success-bg);
  color: var(--color-success);
}

.ss-report.is-clean {
  color: var(--color-success);
}

.ss-report:not(.is-clean) {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.ss-report-title {
  font-weight: 600;
}

.ss-report-item {
  margin-top: 4px;
}

.ss-report-hint {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
}

.ss-submit-error {
  margin-top: 8px;
  font-size: 13px;
  color: var(--color-danger);
}

.ss-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.ss-footer-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ss-footer-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}
</style>
