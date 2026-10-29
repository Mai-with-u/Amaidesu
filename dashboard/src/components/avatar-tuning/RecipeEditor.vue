<template>
  <el-card class="recipe-editor" shadow="never">
    <template #header>
      <div class="card-header">
        <span>表情配方</span>
        <span class="hint">每个情绪满强度时各通道偏移多少；改完边看边调</span>
      </div>
    </template>

    <div class="recipe-body">
      <div class="emotion-list">
        <div
          v-for="emotion in emotions"
          :key="emotion"
          class="emotion-item"
          :class="{ active: emotion === selected }"
          @click="select(emotion)"
        >
          <span>{{ emotionLabel(emotion) }}</span>
          <el-tag v-if="recipes[emotion]?.overridden" size="small" type="warning">已改</el-tag>
        </div>
      </div>

      <div v-if="current" class="recipe-detail">
        <div class="preview-row">
          <span class="row-label">预览强度</span>
          <el-slider v-model="intensity" :min="0" :max="1" :step="0.05" size="small" class="grow" />
          <el-button type="primary" size="small" @click="preview">预览 4 秒</el-button>
          <el-checkbox v-model="livePreview">边调边预览</el-checkbox>
        </div>

        <div v-for="(value, key) in draft" :key="key" class="offset-row">
          <span class="row-label">{{ channelLabel(String(key)) }}</span>
          <el-slider
            :model-value="value"
            :min="-span(String(key))"
            :max="span(String(key))"
            :step="recipeSliderStep(span(String(key)))"
            size="small"
            class="grow"
            @input="(v: number | number[]) => setOffset(String(key), Number(v))"
          />
          <span class="row-value">{{ value.toFixed(2) }}</span>
          <el-button size="small" text type="danger" @click="removeKey(String(key))"
            >移除</el-button
          >
        </div>

        <div class="offset-row">
          <span class="row-label">头部微动倍数</span>
          <el-slider
            v-model="motion"
            :min="motionRange[0]"
            :max="motionRange[1]"
            :step="0.05"
            size="small"
            class="grow"
            @input="commit"
          />
          <span class="row-value">{{ motion.toFixed(2) }}</span>
          <span class="row-spacer" />
        </div>

        <div class="footer-row">
          <el-select
            v-model="keyToAdd"
            placeholder="加一个通道"
            size="small"
            class="add-select"
            filterable
            @change="addKey"
          >
            <el-option v-for="key in addable" :key="key" :label="channelLabel(key)" :value="key" />
          </el-select>
          <el-button size="small" :disabled="!current.overridden" @click="reset"
            >恢复默认</el-button
          >
          <span class="family">族：{{ current.family }}（决定浮现快慢，不随配方改）</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { avatarTuningApi } from '@/api';
import type { AvatarTuningRecipe } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import {
  addableRecipeKeys,
  channelLabel,
  emotionLabel,
  recipeSliderStep,
  throttleLatest,
} from '@/utils/avatarTuning';

/**
 * 表情配方编辑（形象调校页的一块）。
 *
 * 选一个情绪，按通道拖滑条改它满强度时的偏移，可加减通道、改头部微动倍数；每次
 * 改动整份写成该情绪的覆盖（只在运行中生效），勾着"边调边预览"时同时让形象摆出
 * 这个表情，拖着就能看到。"恢复默认"去掉覆盖。
 *
 * 生命周期语义：无定时器与订阅；限流的补发在 120 毫秒内完成。
 */

const props = defineProps<{
  recipes: Record<string, AvatarTuningRecipe>;
  recipeKeys: { key: string; span: number }[];
  motionRange: [number, number];
}>();

const emit = defineEmits<{
  /** 某个情绪的生效配方变了（页面据此更新本地数据并提示"有未保存的修改"） */
  changed: [emotion: string, recipe: AvatarTuningRecipe];
}>();

const emotions = computed(() => Object.keys(props.recipes));
const selected = ref('happy');
const current = computed(() => props.recipes[selected.value]);
const draft = ref<Record<string, number>>({});
const motion = ref(1);
const intensity = ref(0.8);
const livePreview = ref(true);
const keyToAdd = ref('');

const spans = computed(() => new Map(props.recipeKeys.map(k => [k.key, k.span])));
const addable = computed(() =>
  addableRecipeKeys(
    props.recipeKeys.map(k => k.key),
    draft.value,
  ),
);

function span(key: string): number {
  return spans.value.get(key) ?? 1;
}

function loadDraft(): void {
  const recipe = current.value;
  if (!recipe) return;
  draft.value = { ...recipe.offsets };
  motion.value = recipe.motion;
}

watch(selected, loadDraft, { immediate: true });
watch(
  () => props.recipes,
  () => {
    if (!draft.value || !Object.keys(draft.value).length) loadDraft();
  },
);

function select(emotion: string): void {
  selected.value = emotion;
  if (livePreview.value) void preview();
}

async function preview(): Promise<void> {
  try {
    await avatarTuningApi.preview(selected.value, intensity.value, 4);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '预览失败'));
  }
}

const send = throttleLatest(
  (emotion: string, offsets: Record<string, number>, motionValue: number) => {
    avatarTuningApi
      .putRecipe(emotion, offsets, motionValue)
      .then(({ data }) => {
        emit('changed', emotion, data);
        if (livePreview.value) void preview();
      })
      .catch(error => ElMessage.error(getApiErrorMessage(error, '修改配方失败')));
  },
  120,
);

function commit(): void {
  send(selected.value, { ...draft.value }, motion.value);
}

function setOffset(key: string, value: number): void {
  draft.value = { ...draft.value, [key]: value };
  commit();
}

function removeKey(key: string): void {
  const next = { ...draft.value };
  delete next[key];
  draft.value = next;
  commit();
}

function addKey(key: string): void {
  if (!key) return;
  draft.value = { ...draft.value, [key]: 0 };
  keyToAdd.value = '';
  commit();
}

async function reset(): Promise<void> {
  try {
    const { data } = await avatarTuningApi.resetRecipe(selected.value);
    emit('changed', selected.value, data);
    draft.value = { ...data.offsets };
    motion.value = data.motion;
    if (livePreview.value) void preview();
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '恢复默认失败'));
  }
}
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint,
.family {
  font-size: 12px;
  color: var(--text-secondary);
}

.recipe-body {
  display: flex;
  gap: var(--spacing-md);
}

.emotion-list {
  width: 150px;
  flex-shrink: 0;
  max-height: 460px;
  overflow-y: auto;
  border-right: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.emotion-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  font-size: 13px;
  border-radius: 4px;
  cursor: pointer;
}

.emotion-item:hover {
  background: var(--el-fill-color-light, #f5f7fa);
}

.emotion-item.active {
  background: var(--el-color-primary-light-9, #ecf5ff);
  color: var(--el-color-primary, #409eff);
}

.recipe-detail {
  flex: 1;
  min-width: 0;
}

.preview-row,
.offset-row,
.footer-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 32px;
}

.preview-row {
  margin-bottom: var(--spacing-sm);
  padding-bottom: var(--spacing-sm);
  border-bottom: 1px dashed var(--el-border-color-lighter, #ebeef5);
}

.footer-row {
  margin-top: var(--spacing-sm);
}

.row-label {
  width: 110px;
  flex-shrink: 0;
  font-size: 13px;
}

.grow {
  flex: 1;
}

.row-value {
  width: 44px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
}

.row-spacer {
  width: 44px;
}

.add-select {
  width: 160px;
}
</style>
