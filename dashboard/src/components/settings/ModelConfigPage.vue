<template>
  <div class="model-config-page">
    <!-- ── 详情形态：单实体全宽编辑（设置壳目录/表格/搜索进入） ── -->
    <template v-if="detailKey">
      <div v-if="!detailTarget" class="detail-missing">
        该条目已不存在（可能已被删除或改名）
      </div>

      <!-- 用途详情：主模型与备选链 + 子字段 -->
      <div
        v-else-if="detailTarget.kind === 'profile' && detailProfileEntry"
        class="entity-detail"
        :data-config-key="detailKey"
      >
        <div
          v-if="danglingRefs(detailProfileEntry).length > 0"
          class="warn"
        >
          引用的 {{ danglingRefs(detailProfileEntry).join('、') }} 已不在注册表
        </div>
        <div class="detail-block">
          <div class="block-title">主模型与备选链</div>
          <div class="use-main">
            <span class="u-main-label">主模型</span>
            <el-select
              :model-value="detailProfileEntry.modelList[0] ?? ''"
              filterable
              allow-create
              default-first-option
              placeholder="未设置"
              @update:model-value="setMainModel(detailProfileEntry, $event as string)"
            >
              <el-option v-for="name in modelNames" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
          <div class="use-chain">
            <span
              v-for="(name, i) in detailProfileEntry.modelList.slice(1)"
              :key="`${detailProfileEntry.key}-fallback-${i}`"
              class="chip"
            >
              {{ name }}
              <el-icon class="chip-x" @click="removeFallback(detailProfileEntry, i + 1)">
                <Close />
              </el-icon>
            </span>
            <el-select
              :model-value="''"
              filterable
              allow-create
              default-first-option
              placeholder="＋备选"
              size="small"
              class="add-fallback"
              @update:model-value="addFallback(detailProfileEntry, $event as string)"
            >
              <el-option v-for="name in modelNames" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
        </div>
        <div v-if="detailProfileEntry.otherChildren.length > 0" class="detail-block">
          <div class="block-title">更多设置</div>
          <SubFieldGroup
            :fields="detailProfileEntry.otherChildren"
            :get-value="getValue"
            :get-original="getOriginal"
            :update-value="updateValue"
            :get-change-count="getChangeCount"
            :show-advanced="showAdvanced"
          />
        </div>
      </div>

      <!-- 模型详情：身份 + 全宽字段 + 测试连通 + 反向引用 -->
      <div
        v-else-if="detailTarget.kind === 'model'"
        class="entity-detail"
        :data-config-key="`${MODELS_KEY}@${itemDisplayName(models[detailTarget.index])}`"
      >
        <div class="name-row">
          <div class="name-field">
            <label class="name-field-label">模型名称<span class="required-mark">*</span></label>
            <CommitOnBlurInput
              :value="itemDisplayName(models[detailTarget.index])"
              placeholder="模型唯一名"
              @commit="commitModelName(detailTarget.index, $event)"
            />
          </div>
          <div class="name-field">
            <label class="name-field-label">所属提供商</label>
            <el-select
              :model-value="str(models[detailTarget.index]?.api_provider)"
              filterable
              allow-create
              default-first-option
              placeholder="选择提供商"
              @update:model-value="updateModelField(detailTarget.index, 'api_provider', $event)"
            >
              <el-option v-for="name in providerNames" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
          <div class="detail-test">
            <el-button
              :icon="Connection"
              :loading="testStates[detailTarget.index]?.status === 'loading'"
              @click="testModelConnectivity(detailTarget.index)"
            >
              测试连通
            </el-button>
            <el-tooltip
              v-if="testStates[detailTarget.index]?.status === 'ok'"
              :content="`服务端模型：${testStates[detailTarget.index]?.model ?? '未知'}${
                testStates[detailTarget.index]?.finishReason
                  ? ' · finish_reason: ' + testStates[detailTarget.index]?.finishReason
                  : ''
              }`"
              placement="top"
            >
              <span class="test-ok mono">
                连通 {{ testStates[detailTarget.index]?.latencyMs }}ms
              </span>
            </el-tooltip>
            <el-tooltip
              v-else-if="testStates[detailTarget.index]?.status === 'error'"
              :content="testStates[detailTarget.index]?.error ?? ''"
              placement="top"
            >
              <span class="test-fail">失败</span>
            </el-tooltip>
          </div>
        </div>
        <div class="field-grid">
          <FieldRenderer
            v-for="sub in modelEditorFields"
            :key="`${MODELS_KEY}.${detailTarget.index}.${sub.key}`"
            :field="makeItemField(sub, MODELS_KEY, detailTarget.index)"
            :model-value="itemValue(models[detailTarget.index], sub.key)"
            :original-value="originalItemValue(MODELS_KEY, detailTarget.index, sub.key)"
            @update:model-value="updateModelField(detailTarget.index, sub.key, $event)"
          />
        </div>
        <details v-if="showAdvanced && modelAdvancedFields.length > 0" class="advanced-block">
          <summary class="advanced-toggle">高级参数（{{ modelAdvancedFields.length }}）</summary>
          <div class="field-grid">
            <FieldRenderer
              v-for="sub in modelAdvancedFields"
              :key="`${MODELS_KEY}.${detailTarget.index}.${sub.key}`"
              :field="makeItemField(sub, MODELS_KEY, detailTarget.index)"
              :model-value="itemValue(models[detailTarget.index], sub.key)"
              :original-value="originalItemValue(MODELS_KEY, detailTarget.index, sub.key)"
              @update:model-value="updateModelField(detailTarget.index, sub.key, $event)"
            />
          </div>
        </details>
        <div class="detail-references">
          <span class="ref-label">被用途引用</span>
          <template v-if="detailModelUsages.length > 0">
            <button
              v-for="usage in detailModelUsages"
              :key="usage.key"
              type="button"
              class="usage-link"
              @click="emit('navigate', usage.key)"
            >
              {{ usage.label }}
            </button>
          </template>
          <span v-else class="unused">未投入使用</span>
        </div>
      </div>

      <!-- 提供商详情：身份 + 全宽字段 + 名下模型 -->
      <div
        v-else-if="detailTarget.kind === 'provider'"
        class="entity-detail"
        :data-config-key="`${PROVIDERS_KEY}@${itemDisplayName(providers[detailTarget.index])}`"
      >
        <div class="name-row">
          <div class="name-field">
            <label class="name-field-label">提供商名称<span class="required-mark">*</span></label>
            <CommitOnBlurInput
              :value="itemDisplayName(providers[detailTarget.index])"
              placeholder="provider 唯一名称"
              @commit="commitProviderName(detailTarget.index, $event)"
            />
          </div>
        </div>
        <div class="field-grid">
          <FieldRenderer
            v-for="sub in providerEditorFields"
            :key="`${PROVIDERS_KEY}.${detailTarget.index}.${sub.key}`"
            :field="makeItemField(sub, PROVIDERS_KEY, detailTarget.index)"
            :model-value="itemValue(providers[detailTarget.index], sub.key)"
            :original-value="originalItemValue(PROVIDERS_KEY, detailTarget.index, sub.key)"
            @update:model-value="updateProviderField(detailTarget.index, sub.key, $event)"
          />
        </div>
        <details v-if="showAdvanced && providerAdvancedFields.length > 0" class="advanced-block">
          <summary class="advanced-toggle">高级参数（{{ providerAdvancedFields.length }}）</summary>
          <div class="field-grid">
            <FieldRenderer
              v-for="sub in providerAdvancedFields"
              :key="`${PROVIDERS_KEY}.${detailTarget.index}.${sub.key}`"
              :field="makeItemField(sub, PROVIDERS_KEY, detailTarget.index)"
              :model-value="itemValue(providers[detailTarget.index], sub.key)"
              :original-value="originalItemValue(PROVIDERS_KEY, detailTarget.index, sub.key)"
              @update:model-value="updateProviderField(detailTarget.index, sub.key, $event)"
            />
          </div>
        </details>
        <div class="detail-references">
          <span class="ref-label">名下模型</span>
          <template v-if="detailProviderModels.length > 0">
            <button
              v-for="entry in detailProviderModels"
              :key="entry.index"
              type="button"
              class="usage-link"
              @click="emit('navigate', `${MODELS_KEY}@${itemDisplayName(entry.model)}`)"
            >
              {{ itemDisplayName(entry.model) || '未命名' }}
            </button>
          </template>
          <span v-else class="unused">暂无模型挂在该提供商下</span>
        </div>
      </div>

      <!-- 语义检索详情：运行状态 + 字段组 -->
      <div
        v-else-if="detailTarget.kind === 'embedding' && embeddingField"
        class="entity-detail"
        :data-config-key="EMBEDDING_KEY"
      >
        <div class="detail-embed-status">
          <el-tag :type="embeddingState.ready ? 'success' : 'warning'" effect="plain">
            {{ embeddingState.text }}
          </el-tag>
        </div>
        <SubFieldGroup
          :fields="embeddingField.children ?? []"
          :get-value="getValue"
          :get-original="getOriginal"
          :update-value="updateValue"
          :get-change-count="getChangeCount"
          :show-advanced="showAdvanced"
        />
      </div>
    </template>

    <!-- ── 总览：三层关系 + 语义检索（关系视图） ── -->
    <template v-else>
    <!-- ── 第一层：用途分配（llm_profiles） ── -->
    <section class="zone" :data-config-key="PROFILES_CONTAINER_KEY">
      <div class="zone-head">
        <h2>用途分配</h2>
        <span class="cnt">{{ profileEntries.length }} 个用途</span>
      </div>
      <div class="use-grid">
        <div
          v-for="profile in profileEntries"
          :key="profile.key"
          class="use-card"
          :data-config-key="profile.key"
        >
          <div class="use-name">
            <span class="u-dot"></span>{{ profileName(profile.key) }}
            <el-tag
              v-if="getChangeCount(`${profile.key}.model_list`) > 0"
              size="small"
              type="warning"
              effect="plain"
            >
              已修改
            </el-tag>
          </div>
          <div v-if="danglingRefs(profile).length > 0" class="warn">
            引用的 {{ danglingRefs(profile).join('、') }} 已不在注册表
          </div>
          <div class="use-main">
            <span class="u-main-label">主模型</span>
            <el-select
              :model-value="profile.modelList[0] ?? ''"
              filterable
              allow-create
              default-first-option
              placeholder="未设置"
              class="use-main-select"
              @update:model-value="setMainModel(profile, $event as string)"
            >
              <el-option v-for="name in modelNames" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
          <div class="use-chain">
            <span
              v-for="(name, i) in profile.modelList.slice(1)"
              :key="`${profile.key}-fallback-${i}`"
              class="chip"
            >
              {{ name }}
              <el-icon class="chip-x" @click="removeFallback(profile, i + 1)"><Close /></el-icon>
            </span>
            <el-select
              :model-value="''"
              filterable
              allow-create
              default-first-option
              placeholder="＋备选"
              size="small"
              class="add-fallback"
              @update:model-value="addFallback(profile, $event as string)"
            >
              <el-option v-for="name in modelNames" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
          <!-- 其余真实子字段：低调折叠条（控件非指引），无子字段的卡不渲染 -->
          <details v-if="profile.otherChildren.length > 0" class="more-block">
            <summary class="more-toggle">更多设置（{{ profile.otherChildren.length }}）</summary>
            <SubFieldGroup
              :fields="profile.otherChildren"
              :get-value="getValue"
              :get-original="getOriginal"
              :update-value="updateValue"
              :get-change-count="getChangeCount"
              :show-advanced="showAdvanced"
            />
          </details>
        </div>
      </div>
    </section>

    <!-- ── 第二层：模型注册（llm_models） ── -->
    <section class="zone" :data-config-key="MODELS_KEY">
      <div class="zone-head">
        <h2>模型注册</h2>
        <span class="cnt">{{ models.length }} 个模型 · {{ unusedCount }} 个未投入使用</span>
      </div>
      <div class="filters">
        <button
          type="button"
          class="fbtn"
          :class="{ on: providerFilter === 'all' }"
          @click="providerFilter = 'all'"
        >
          全部
        </button>
        <button
          v-for="name in providerNames"
          :key="name"
          type="button"
          class="fbtn"
          :class="{ on: providerFilter === name }"
          @click="providerFilter = name"
        >
          {{ name }}
        </button>
      </div>
      <table class="model-table">
        <thead>
          <tr>
            <th>模型标识</th>
            <th>提供商</th>
            <th>输入 ¥/M</th>
            <th>输出 ¥/M</th>
            <th>能力</th>
            <th>被用途引用</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="idx in filteredModelIndices" :key="idx">
            <tr class="model-row" @click="toggleModelExpand(idx)">
              <td class="mono">
                <el-icon class="row-toggle">
                  <ArrowRight v-if="modelExpanded[idx] !== true" />
                  <ArrowDown v-else />
                </el-icon>
                {{ itemDisplayName(models[idx]) || '未命名' }}
              </td>
              <td :class="{ 'dangling-cell': isProviderDangling(models[idx]) }">
                {{ str(models[idx]?.api_provider) || '—' }}
              </td>
              <td class="mono">{{ formatPrice(models[idx]?.price_in) }}</td>
              <td class="mono">{{ formatPrice(models[idx]?.price_out) }}</td>
              <td>
                <span v-if="models[idx]?.visual === true" class="tag">视觉</span>
                <span v-if="models[idx]?.cache === 'hit_discount'" class="tag">缓存 5 折</span>
                <span v-if="modelAbilityTags(models[idx]).length === 0" class="tag grey">—</span>
              </td>
              <td>
                <template v-if="usageChips(models[idx]).length > 0">
                  <span v-for="chip in usageChips(models[idx])" :key="chip" class="ref">{{
                    chip
                  }}</span>
                </template>
                <span v-else class="unused">未投入使用</span>
              </td>
              <td @click.stop>
                <div class="row-actions">
                  <el-button
                    size="small"
                    text
                    :icon="Connection"
                    :loading="testStates[idx]?.status === 'loading'"
                    @click="testModelConnectivity(idx)"
                    >测试</el-button
                  >
                  <el-tooltip
                    v-if="testStates[idx]?.status === 'ok'"
                    :content="`服务端模型：${testStates[idx]?.model ?? '未知'}${
                      testStates[idx]?.finishReason
                        ? ' · finish_reason: ' + testStates[idx]?.finishReason
                        : ''
                    }`"
                    placement="top"
                  >
                    <span class="test-ok mono">连通 {{ testStates[idx]?.latencyMs }}ms</span>
                  </el-tooltip>
                  <el-tooltip
                    v-else-if="testStates[idx]?.status === 'error'"
                    :content="testStates[idx]?.error ?? ''"
                    placement="top"
                  >
                    <span class="test-fail">失败</span>
                  </el-tooltip>
                  <el-button
                    type="danger"
                    size="small"
                    text
                    :icon="Delete"
                    @click="removeModel(idx)"
                  />
                </div>
              </td>
            </tr>
            <tr v-if="modelExpanded[idx] === true" class="model-edit-row">
              <td colspan="7">
                <div class="edit-body">
                  <!-- 身份字段（name）：失焦/回车提交——改名触发引用守护，
                       逐键提交会把中间态名字写进引用方 -->
                  <div class="name-field">
                    <label class="name-field-label"
                      >模型名称<span class="required-mark">*</span></label
                    >
                    <CommitOnBlurInput
                      :value="itemDisplayName(models[idx])"
                      placeholder="模型唯一名"
                      @commit="commitModelName(idx, $event)"
                    />
                  </div>
                  <div class="name-field">
                    <label class="name-field-label">所属提供商</label>
                    <el-select
                      :model-value="str(models[idx]?.api_provider)"
                      filterable
                      allow-create
                      default-first-option
                      placeholder="选择提供商"
                      @update:model-value="updateModelField(idx, 'api_provider', $event)"
                    >
                      <el-option
                        v-for="name in providerNames"
                        :key="name"
                        :label="name"
                        :value="name"
                      />
                    </el-select>
                  </div>
                  <div class="field-grid">
                    <FieldRenderer
                      v-for="sub in modelEditorFields"
                      :key="`${MODELS_KEY}.${idx}.${sub.key}`"
                      :field="makeItemField(sub, MODELS_KEY, idx)"
                      :model-value="itemValue(models[idx], sub.key)"
                      :original-value="originalItemValue(MODELS_KEY, idx, sub.key)"
                      @update:model-value="updateModelField(idx, sub.key, $event)"
                    />
                  </div>
                  <details
                    v-if="showAdvanced && modelAdvancedFields.length > 0"
                    class="advanced-block"
                  >
                    <summary class="advanced-toggle">
                      高级参数（{{ modelAdvancedFields.length }}）
                    </summary>
                    <div class="field-grid">
                      <FieldRenderer
                        v-for="sub in modelAdvancedFields"
                        :key="`${MODELS_KEY}.${idx}.${sub.key}`"
                        :field="makeItemField(sub, MODELS_KEY, idx)"
                        :model-value="itemValue(models[idx], sub.key)"
                        :original-value="originalItemValue(MODELS_KEY, idx, sub.key)"
                        @update:model-value="updateModelField(idx, sub.key, $event)"
                      />
                    </div>
                  </details>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div class="zone-foot">
        <el-button type="primary" :icon="Plus" @click="addModel">＋ 添加模型</el-button>
      </div>
    </section>

    <!-- ── 第三层：提供商接入（llm_providers） ── -->
    <section class="zone" :data-config-key="PROVIDERS_KEY">
      <div class="zone-head">
        <h2>提供商接入</h2>
        <span class="cnt">{{ providers.length }} 个提供商</span>
        <span class="side">
          <el-button type="primary" :icon="Plus" @click="addProvider">＋ 添加提供商</el-button>
        </span>
      </div>
      <div v-for="entry in providerEntries" :key="entry.index" class="prov">
        <div class="prov-head" @click="toggleProviderOpen(entry.index)">
          <el-icon class="row-toggle">
            <ArrowRight v-if="providerOpen[entry.index] !== true" />
            <ArrowDown v-else />
          </el-icon>
          <span class="name">{{ itemDisplayName(entry.item) || '未命名' }}</span>
          <span class="host mono">{{ str(entry.item.base_url) }}</span>
          <span class="key" :class="apiKeySet(entry.item.api_key) ? 'is-set' : 'is-unset'">
            {{ apiKeySet(entry.item.api_key) ? '● 密钥已设置' : '○ 未设置' }}
          </span>
          <span class="models-count"
            >{{ modelsOfProvider(itemDisplayName(entry.item)).length }} 个模型</span
          >
          <el-button
            type="danger"
            size="small"
            text
            :icon="Delete"
            @click.stop="removeProvider(entry.index)"
          />
        </div>
        <div v-if="providerOpen[entry.index] === true" class="prov-body">
          <!-- name 失焦提交 + 引用守护级联；其余叶字段一律 FieldRenderer -->
          <div class="name-field">
            <label class="name-field-label">提供商名称<span class="required-mark">*</span></label>
            <CommitOnBlurInput
              :value="itemDisplayName(entry.item)"
              placeholder="provider 唯一名称"
              @commit="commitProviderName(entry.index, $event)"
            />
          </div>
          <div class="field-grid">
            <FieldRenderer
              v-for="sub in providerEditorFields"
              :key="`${PROVIDERS_KEY}.${entry.index}.${sub.key}`"
              :field="makeItemField(sub, PROVIDERS_KEY, entry.index)"
              :model-value="itemValue(entry.item, sub.key)"
              :original-value="originalItemValue(PROVIDERS_KEY, entry.index, sub.key)"
              @update:model-value="updateProviderField(entry.index, sub.key, $event)"
            />
          </div>
          <details v-if="showAdvanced && providerAdvancedFields.length > 0" class="advanced-block">
            <summary class="advanced-toggle">
              高级参数（{{ providerAdvancedFields.length }}）
            </summary>
            <div class="field-grid">
              <FieldRenderer
                v-for="sub in providerAdvancedFields"
                :key="`${PROVIDERS_KEY}.${entry.index}.${sub.key}`"
                :field="makeItemField(sub, PROVIDERS_KEY, entry.index)"
                :model-value="itemValue(entry.item, sub.key)"
                :original-value="originalItemValue(PROVIDERS_KEY, entry.index, sub.key)"
                @update:model-value="updateProviderField(entry.index, sub.key, $event)"
              />
            </div>
          </details>
        </div>
      </div>
    </section>

    <!-- ── 第四层：语义检索（embedding）：引用一个提供商，状态标签以后端实际状态为准 ── -->
    <section v-if="embeddingField" class="zone" :data-config-key="EMBEDDING_KEY">
      <div class="zone-head">
        <h2>语义检索</h2>
        <span class="side">
          <el-tag :type="embeddingState.ready ? 'success' : 'warning'" effect="plain">
            {{ embeddingState.text }}
          </el-tag>
        </span>
      </div>
      <SubFieldGroup
        :fields="embeddingField.children ?? []"
        :get-value="getValue"
        :get-original="getOriginal"
        :update-value="updateValue"
        :get-change-count="getChangeCount"
        :show-advanced="showAdvanced"
      />
    </section>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 模型域专用配置页
 *
 * model.toml 的三类数据是相互引用的关系结构（provider ← model.api_provider、
 * model ← profile.model_list）。两种形态：
 * - 总览（默认）：关系视图——用途分配卡区（主模型 + 备选链 + 悬空引用警示）、
 *   模型注册表（行展开编辑 + 反向引用列）、提供商接入抽屉、语义检索区；
 * - 详情（detailKey 传入时）：单实体全宽编辑，键格式见 detailTarget。
 * 叶字段编辑一律复用 FieldRenderer（同一条 store 更新路径），不 fork 第二条
 * 保存/脏跟踪路径；数组整值写回（与 ArrayEditor 同机制），provider/model
 * 改名经引用守护确认后级联更新引用方。详情里的交叉引用（被用途引用 /
 * 名下模型）点击后经 navigate 事件交由设置壳切换详情目标。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowDown, ArrowRight, Close, Connection, Delete, Plus } from '@element-plus/icons-vue';
import FieldRenderer from './FieldRenderer.vue';
import SubFieldGroup from './SubFieldGroup.vue';
import CommitOnBlurInput from './CommitOnBlurInput.vue';
import { llmApi } from '@/api';
import { confirmAction } from '@/utils/confirmAction';
import { getApiErrorMessage } from '@/utils/apiError';
import type { ConfigFieldSchema } from '@/types/settings';
import type { EmbeddingRuntimeStatus } from '@/types';
import {
  EMBEDDING_KEY,
  MODELS_KEY,
  PROVIDERS_KEY,
  PROFILES_CONTAINER_KEY,
  type ConfigItem,
  apiKeySet,
  applyModelRenameToProfileLists,
  applyProviderRenameToModels,
  buildDefaultItem,
  danglingModelRefs,
  embeddingStatus,
  embeddingTag,
  formatPrice,
  groupModelsByProvider,
  makeItemField,
  modelUsageChips,
  profileName,
  providerNameReferences,
  uniqueName,
} from '@/utils/modelConfig';

const props = defineProps<{
  /** model 分组的顶层字段（含 llm_providers / llm_models 数组与 llm_profiles 容器） */
  fields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  getChangeCount: (key: string) => number;
  showAdvanced: boolean;
  /** 页头"全部展开/收起"命令（与 ComponentCardList 同源），seq 变化即执行 */
  expandCommand?: { action: 'expand' | 'collapse'; seq: number } | null;
  /**
   * 详情目标键：null/undefined = 总览形态。四种取值——
   * `model.llm_profiles.<name>`（用途，schema 真实键）、
   * `model.llm_models@<name>` 与 `model.llm_providers@<name>`（数组条目按名字
   * 寻址，schema 无对应实体，@ 避免与点分 schema 键混淆）、
   * `model.embedding`（语义检索）。
   */
  detailKey?: string | null;
}>();

const emit = defineEmits<{ navigate: [key: string] }>();

// ── Schema 定位 ──────────────────────────────────────────
const providersField = computed(() => props.fields.find(f => f.key === PROVIDERS_KEY));
const modelsField = computed(() => props.fields.find(f => f.key === MODELS_KEY));
const profilesContainer = computed(() => props.fields.find(f => f.key === PROFILES_CONTAINER_KEY));
const embeddingField = computed(() => props.fields.find(f => f.key === EMBEDDING_KEY));
const providerItemFields = computed(() => providersField.value?.items?.fields ?? []);
const modelItemFields = computed(() => modelsField.value?.items?.fields ?? []);

/** 模型/提供商编辑区字段：name 与 api_provider 走专用控件，从 FieldRenderer 名单排除 */
const modelEditorFields = computed(() =>
  modelItemFields.value.filter(f => f.key !== 'name' && f.key !== 'api_provider' && !f.advanced),
);
const modelAdvancedFields = computed(() =>
  modelItemFields.value.filter(
    f => f.key !== 'name' && f.key !== 'api_provider' && f.advanced === true,
  ),
);
const providerEditorFields = computed(() =>
  providerItemFields.value.filter(f => f.key !== 'name' && !f.advanced),
);
const providerAdvancedFields = computed(() =>
  providerItemFields.value.filter(f => f.key !== 'name' && f.advanced === true),
);

function str(v: unknown): string {
  return String(v ?? '');
}

function itemValue(item: ConfigItem | undefined, relKey: string): unknown {
  return item?.[relKey];
}

function itemDisplayName(item: ConfigItem | undefined): string {
  return str(itemValue(item, 'name'));
}

/** 数组第 index 项的原始值（合成 key 可被 store 的点分遍历读取，数组下标按字符串寻址） */
function originalItemValue(arrayKey: string, index: number, relKey: string): unknown {
  return props.getOriginal(`${arrayKey}.${index}.${relKey}`);
}

// ── 展开状态（模型行/提供商行） ──────────────────────────
const modelExpanded = ref<Record<number, boolean>>({});
const providerOpen = ref<Record<number, boolean>>({});

function toggleModelExpand(index: number) {
  modelExpanded.value[index] = modelExpanded.value[index] !== true;
}

function toggleProviderOpen(index: number) {
  providerOpen.value[index] = providerOpen.value[index] !== true;
}

// 页头"全部展开/收起"：expand 清空显式状态（模型收起/提供商收起），collapse 全量收起
watch(
  () => props.expandCommand?.seq,
  () => {
    const cmd = props.expandCommand;
    if (!cmd) return;
    if (cmd.action === 'expand') {
      modelExpanded.value = {};
      providerOpen.value = {};
      return;
    }
    const collapsedModels: Record<number, boolean> = {};
    models.value.forEach((_, index) => {
      collapsedModels[index] = false;
    });
    modelExpanded.value = collapsedModels;
    const closedProviders: Record<number, boolean> = {};
    providers.value.forEach((_, index) => {
      closedProviders[index] = false;
    });
    providerOpen.value = closedProviders;
  },
);

// ── 数组读写（整值写回，与 ArrayEditor 同机制） ──────────
function readArray(field: ConfigFieldSchema | undefined): ConfigItem[] {
  if (!field) return [];
  const v = props.getValue(field.key);
  return Array.isArray(v) ? (v as ConfigItem[]) : [];
}

function writeArray(field: ConfigFieldSchema | undefined, arr: ConfigItem[]) {
  if (field) props.updateValue(field, arr);
}

function setItemField(
  field: ConfigFieldSchema | undefined,
  index: number,
  relKey: string,
  value: unknown,
) {
  if (!field) return;
  const arr = [...readArray(field)];
  if (!arr[index] || typeof arr[index] !== 'object') return;
  arr[index] = { ...arr[index], [relKey]: value };
  writeArray(field, arr);
}

// ── 数据视图 ─────────────────────────────────────────────
const providers = computed(() => readArray(providersField.value));
const models = computed(() => readArray(modelsField.value));
const providerEntries = computed(() => providers.value.map((item, index) => ({ index, item })));
const providerNames = computed(() => [
  ...new Set(providers.value.map(p => itemDisplayName(p)).filter(n => n !== '')),
]);
const modelNames = computed(() => models.value.map(m => itemDisplayName(m)).filter(n => n !== ''));
const grouped = computed(() => groupModelsByProvider(models.value, providers.value));
function modelsOfProvider(providerName: string): { index: number; model: ConfigItem }[] {
  return grouped.value.byProvider.get(providerName) ?? [];
}

// ── 第四层：语义检索 ─────────────────────────────────────
// 开没开看后端实际状态（启动时嵌入客户端建没建起来）；页面配置只用来提示"改完要重启"。
// 进页面读一次：嵌入服务只在启动时读配置，页面开着期间不会自己变
const embeddingRuntime = ref<EmbeddingRuntimeStatus | null>(null);
onMounted(async () => {
  try {
    const { data } = await llmApi.getEmbeddingStatus();
    embeddingRuntime.value = data;
  } catch (error) {
    console.warn(getApiErrorMessage(error, '读语义检索运行状态失败'));
    embeddingRuntime.value = null;
  }
});
const embeddingState = computed(() =>
  embeddingTag(
    embeddingRuntime.value,
    embeddingStatus(
      {
        enabled: props.getValue(`${EMBEDDING_KEY}.enabled`),
        provider: props.getValue(`${EMBEDDING_KEY}.provider`),
        model: props.getValue(`${EMBEDDING_KEY}.model_identifier`),
      },
      providers.value,
    ),
  ),
);

const profileEntries = computed(() =>
  (profilesContainer.value?.children ?? []).map(profile => {
    const modelListField = profile.children?.find(c => c.key === `${profile.key}.model_list`);
    const modelList = props.getValue(`${profile.key}.model_list`);
    return {
      key: profile.key,
      modelListField,
      modelList: Array.isArray(modelList) ? (modelList as string[]) : [],
      // model_list 之外的真实子字段（如 selection_strategy），进卡底"更多设置"折叠条
      otherChildren: (profile.children ?? []).filter(c => c !== modelListField),
    };
  }),
);

const profileListEntries = computed(() =>
  profileEntries.value.map(p => ({ key: p.key, modelList: p.modelList })),
);

// ── 详情目标解析（detailKey → 实体） ──────────────────────
type DetailTarget =
  | { kind: 'profile'; name: string }
  | { kind: 'model'; index: number }
  | { kind: 'provider'; index: number }
  | { kind: 'embedding' };

const detailTarget = computed<DetailTarget | null>(() => {
  const key = props.detailKey;
  if (!key) return null;
  if (key.startsWith(PROFILES_CONTAINER_KEY + '.')) {
    return { kind: 'profile', name: profileName(key) };
  }
  if (key === EMBEDDING_KEY) return { kind: 'embedding' };
  if (key.startsWith(MODELS_KEY + '@')) {
    const name = key.slice(MODELS_KEY.length + 1);
    const index = models.value.findIndex(m => itemDisplayName(m) === name);
    return index >= 0 ? { kind: 'model', index } : null;
  }
  if (key.startsWith(PROVIDERS_KEY + '@')) {
    const name = key.slice(PROVIDERS_KEY.length + 1);
    const index = providers.value.findIndex(p => itemDisplayName(p) === name);
    return index >= 0 ? { kind: 'provider', index } : null;
  }
  return null;
});

const detailProfileEntry = computed(() => {
  const target = detailTarget.value;
  if (!target || target.kind !== 'profile') return null;
  return profileEntries.value.find(p => p.key === `${PROFILES_CONTAINER_KEY}.${target.name}`) ?? null;
});

/** 模型详情底部的反向引用：被哪些用途以什么角色引用（点击跳到对应用途详情） */
const detailModelUsages = computed(() => {
  const target = detailTarget.value;
  if (!target || target.kind !== 'model') return [];
  const name = itemDisplayName(models.value[target.index]);
  return profileEntries.value
    .filter(p => p.modelList.includes(name))
    .map(p => ({
      key: p.key,
      label: p.modelList[0] === name ? profileName(p.key) : `${profileName(p.key)}·备选`,
    }));
});

/** 提供商详情底部的名下模型（点击跳到对应模型详情） */
const detailProviderModels = computed(() => {
  const target = detailTarget.value;
  if (!target || target.kind !== 'provider') return [];
  return modelsOfProvider(itemDisplayName(providers.value[target.index]));
});

// ── 第一层：用途分配 ─────────────────────────────────────
function danglingRefs(profile: { modelList: string[] }): string[] {
  return danglingModelRefs(profile.modelList, modelNames.value);
}

/** 整条 model_list 数组写回（所有用途编辑的唯一写路径） */
function updateModelList(profile: { modelListField?: ConfigFieldSchema }, value: string[]) {
  if (profile.modelListField) props.updateValue(profile.modelListField, value);
}

function setMainModel(
  profile: { key: string; modelListField?: ConfigFieldSchema; modelList: string[] },
  name: string,
) {
  if (name === '') return;
  updateModelList(profile, [name, ...profile.modelList.slice(1)]);
}

function addFallback(
  profile: { key: string; modelListField?: ConfigFieldSchema; modelList: string[] },
  name: string,
) {
  if (name === '') return;
  if (profile.modelList.includes(name)) {
    ElMessage.warning(`「${name}」已在此用途的名单里（主模型或备选），无需重复添加`);
    return;
  }
  updateModelList(profile, [...profile.modelList, name]);
}

function removeFallback(
  profile: { key: string; modelListField?: ConfigFieldSchema; modelList: string[] },
  index: number,
) {
  updateModelList(
    profile,
    profile.modelList.filter((_, i) => i !== index),
  );
}

// ── 第二层：模型注册表 ───────────────────────────────────
const providerFilter = ref<string>('all');

const filteredModelIndices = computed(() =>
  models.value
    .map((_, index) => index)
    .filter(
      index =>
        providerFilter.value === 'all' ||
        str(models.value[index]?.api_provider) === providerFilter.value,
    ),
);

const unusedCount = computed(() => models.value.filter(m => usageChips(m).length === 0).length);

function modelAbilityTags(item: ConfigItem | undefined): string[] {
  const tags: string[] = [];
  if (item?.visual === true) tags.push('视觉');
  if (item?.cache === 'hit_discount') tags.push('缓存 5 折');
  return tags;
}

function usageChips(model: ConfigItem | undefined): string[] {
  const name = itemDisplayName(model);
  if (name === '') return [];
  return modelUsageChips(profileListEntries.value, name);
}

function isProviderDangling(model: ConfigItem | undefined): boolean {
  const name = str(model?.api_provider);
  return name !== '' && !providerNames.value.includes(name);
}

// ── 连通性测试（模型行"测试"按钮） ───────────────────────
/** 单行测试态：loading → ok（往返时延 + 服务端模型名）/ error（失败原因） */
interface ModelTestState {
  status: 'loading' | 'ok' | 'error';
  latencyMs?: number;
  model?: string;
  finishReason?: string;
  error?: string;
}
const testStates = ref<Record<number, ModelTestState>>({});

/**
 * 测试该模型的连通性：前端只报提供商名 + 模型标识，连接参数与密钥由
 * 服务端按名从已保存配置解析（改完先保存再测试）；结果就地呈现在按钮旁。
 */
async function testModelConnectivity(idx: number) {
  const model = models.value[idx];
  const identifier = str(model?.model_identifier) || itemDisplayName(model);
  if (identifier === '') {
    ElMessage.warning('该模型未填写模型标识，无法测试');
    return;
  }
  const providerName = str(model?.api_provider);
  if (providerName === '') {
    ElMessage.warning('该模型未设置所属提供商，无法测试');
    return;
  }
  if (!providers.value.find(p => itemDisplayName(p) === providerName)) {
    ElMessage.warning(`所属提供商「${providerName}」不存在，请先接入该提供商`);
    return;
  }
  testStates.value[idx] = { status: 'loading' };
  try {
    const { data } = await llmApi.testModel({ provider: providerName, model: identifier });
    testStates.value[idx] = data.ok
      ? {
          status: 'ok',
          latencyMs: data.latency_ms ?? undefined,
          model: data.model ?? undefined,
          finishReason: data.finish_reason ?? undefined,
        }
      : { status: 'error', error: data.error ?? '测试失败' };
  } catch (e) {
    testStates.value[idx] = {
      status: 'error',
      error: getApiErrorMessage(e, '测试请求失败，请确认服务端可访问'),
    };
  }
}

// ── 常规字段更新（FieldRenderer 出口统一走整值写回） ────
function updateProviderField(index: number, relKey: string, value: unknown) {
  setItemField(providersField.value, index, relKey, value);
}

function updateModelField(index: number, relKey: string, value: unknown) {
  setItemField(modelsField.value, index, relKey, value);
}

// ── 引用守护与改名级联 ──────────────────────────────────
/**
 * provider 改名提交（失焦/回车触发）：先写名字，再算引用方弹确认；
 * 确认级联后指向旧名的模型经 llm_models 数组整值条目更新（变更清单可见、可整体撤销）。
 */
async function commitProviderName(index: number, input: string) {
  const arr = [...providers.value];
  const current = arr[index];
  if (!current || typeof current !== 'object') return;
  const oldName = itemDisplayName(current);
  const newName = input.trim();
  if (newName === '') {
    ElMessage.warning('提供商名称不能为空');
    return;
  }
  if (newName === oldName) return;
  arr[index] = { ...current, name: newName };
  writeArray(providersField.value, arr);
  if (oldName === '') return;
  const referencing = providerNameReferences(models.value, oldName);
  if (referencing.length === 0) return;
  const ok = await confirmAction(
    `提供商「${oldName}」改名为「${newName}」，有 ${referencing.length} 个模型通过 api_provider 引用它：\n${referencing.join('、')}\n\n是否同步更新这些引用？`,
    '检测到引用该提供商的模型',
    { confirmButtonText: '级联更新引用', cancelButtonText: '仅改提供商名' },
  );
  if (ok) {
    writeArray(modelsField.value, applyProviderRenameToModels(models.value, oldName, newName));
    ElMessage.success(`已级联更新 ${referencing.length} 个模型的 api_provider`);
  }
}

/**
 * model 改名提交：每个 model_list 含旧名的 profile 是独立的数组叶 key，
 * 级联更新各自走逐 key 更新路径——变更清单逐条可见、可逐条撤销。
 */
async function commitModelName(index: number, input: string) {
  const arr = [...models.value];
  const current = arr[index];
  if (!current || typeof current !== 'object') return;
  const oldName = itemDisplayName(current);
  const newName = input.trim();
  if (newName === '') {
    ElMessage.warning('模型名称不能为空');
    return;
  }
  if (newName === oldName) return;
  arr[index] = { ...current, name: newName };
  writeArray(modelsField.value, arr);
  if (oldName === '') return;
  const referencing = modelUsageChips(profileListEntries.value, oldName);
  if (referencing.length === 0) return;
  const ok = await confirmAction(
    `模型「${oldName}」改名为「${newName}」，有 ${referencing.length} 处用途引用它：\n${referencing.join('、')}\n\n是否同步更新这些引用？`,
    '检测到引用该模型的用途',
    { confirmButtonText: '级联更新引用', cancelButtonText: '仅改模型名' },
  );
  if (ok) {
    const changed = applyModelRenameToProfileLists(profileListEntries.value, oldName, newName);
    for (const entry of changed) {
      const field = profileEntries.value.find(p => p.key === entry.key)?.modelListField;
      if (field) props.updateValue(field, entry.modelList);
    }
    ElMessage.success(`已级联更新 ${changed.length} 处 model_list 引用`);
  }
}

// ── 增删条目 ────────────────────────────────────────────
function addProvider() {
  const arr = [...providers.value];
  const item = buildDefaultItem(providerItemFields.value);
  item.name = uniqueName(
    arr.map(p => itemDisplayName(p)),
    'new-provider',
  );
  arr.push(item);
  writeArray(providersField.value, arr);
  providerOpen.value[arr.length - 1] = true;
}

function addModel() {
  const arr = [...models.value];
  const item = buildDefaultItem(modelItemFields.value);
  item.name = uniqueName(modelNames.value, 'new-model');
  arr.push(item);
  writeArray(modelsField.value, arr);
  modelExpanded.value[arr.length - 1] = true;
  providerFilter.value = 'all';
}

async function removeProvider(index: number) {
  const name = itemDisplayName(providers.value[index]);
  const owned = providerNameReferences(models.value, name);
  const ok = await confirmAction(
    owned.length > 0
      ? `删除提供商「${name}」后，其名下 ${owned.length} 个模型将变为「未归属」（数据不丢失，可改挂其他提供商）。确定删除吗？`
      : `确定删除提供商「${name}」吗？`,
    '删除提供商',
    { confirmButtonText: '删除' },
  );
  if (!ok) return;
  const arr = [...providers.value];
  arr.splice(index, 1);
  writeArray(providersField.value, arr);
}

async function removeModel(index: number) {
  const name = itemDisplayName(models.value[index]);
  const referencing = name === '' ? [] : modelUsageChips(profileListEntries.value, name);
  const ok = await confirmAction(
    referencing.length > 0
      ? `模型「${name}」仍被 ${referencing.join('、')} 引用，删除后这些引用将悬空（schema 校验可能在保存时拒绝）。确定删除吗？`
      : `确定删除模型「${name || '未命名模型'}」吗？`,
    '删除模型',
    { confirmButtonText: '删除' },
  );
  if (!ok) return;
  const arr = [...models.value];
  arr.splice(index, 1);
  writeArray(modelsField.value, arr);
}
</script>

<style scoped>
.model-config-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
}

.zone {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.zone-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
}

.zone-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.zone-head .cnt {
  font-size: 12px;
  color: var(--text-secondary);
}

.zone-head .side {
  margin-left: auto;
}

.zone-foot {
  display: flex;
  justify-content: flex-start;
  margin-top: var(--spacing-xs);
}

.mono {
  font-family: Consolas, 'Courier New', monospace;
}

/* ── 第一层：用途卡 ───────────────────────────────────── */
.use-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--spacing-sm);
}

.use-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-sm) var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.use-name {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.u-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary);
  flex: none;
}

.warn {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--color-warning);
  background: var(--color-warning-bg);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
}

.use-main {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.u-main-label {
  font-size: 11px;
  color: var(--text-secondary);
  flex: none;
}

.use-main-select {
  flex: 1;
}

.use-main-select :deep(.el-select__wrapper) {
  font-family: Consolas, 'Courier New', monospace;
  font-weight: 500;
}

.use-chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: 4px;
  padding: 2px 7px;
}

.chip-x {
  font-size: 11px;
  cursor: pointer;
  color: var(--text-secondary);
}

.chip-x:hover {
  color: var(--color-danger);
}

.add-fallback {
  width: 96px;
}

/* ── 用途卡"更多设置"折叠条（卡片一体，不套节卡头） ──── */
.more-block {
  border-top: 1px dashed var(--border-color-light);
  padding-top: var(--spacing-xs);
  margin-top: auto;
}

.more-toggle {
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.more-toggle:hover {
  color: var(--color-primary);
}

/* ── 第二层：模型注册表 ───────────────────────────────── */
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.fbtn {
  font-size: 12px;
  border: 1px solid var(--border-color-light);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  border-radius: 14px;
  padding: 4px 14px;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.fbtn.on {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}

.model-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.model-table th {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-secondary);
  text-align: left;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border-color-light);
  background: var(--bg-card);
}

.model-table td {
  font-size: 12.5px;
  color: var(--text-regular);
  padding: 9px 12px;
  border-bottom: 1px solid var(--border-color-light);
  vertical-align: middle;
}

.model-row {
  cursor: pointer;
}

.model-row:hover td {
  background: var(--color-primary-bg);
}

.model-edit-row td {
  background: var(--bg-card);
}

.row-toggle {
  font-size: 12px;
  color: var(--text-secondary);
  vertical-align: middle;
  margin-right: 4px;
}

.dangling-cell {
  color: var(--color-warning);
}

.tag {
  display: inline-block;
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 7px;
  margin-right: 4px;
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.tag.grey {
  background: var(--bg-card);
  color: var(--text-secondary);
}

.ref {
  font-size: 11px;
  color: var(--color-primary);
  margin-right: 6px;
}

.unused {
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 连通性测试：按钮组右对齐，结果就地呈现，详情走悬停 */
.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  white-space: nowrap;
}

.test-ok {
  font-size: 11px;
  color: var(--color-success);
}

.test-fail {
  font-size: 11px;
  color: var(--color-danger);
}

/* ── 展开编辑区（模型行 / 提供商抽屉共用） ────────────── */
.edit-body,
.prov-body {
  padding: var(--spacing-sm) var(--spacing-sm) var(--spacing-sm) 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.prov-body {
  padding: var(--spacing-sm) var(--spacing-md);
}

.name-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 420px;
}

.name-field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--spacing-sm);
  align-items: start;
}

.advanced-toggle {
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.advanced-toggle:hover {
  color: var(--color-primary);
}

/* ── 第三层：提供商抽屉 ───────────────────────────────── */
.prov {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.prov-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 10px var(--spacing-md);
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}

.prov-head:hover {
  background: var(--bg-hover);
}

.prov-head .name {
  font-weight: 600;
  color: var(--text-primary);
}

.prov-head .host {
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prov-head .key {
  font-size: 11px;
  flex: none;
}

.prov-head .key.is-set {
  color: var(--color-success);
}

.prov-head .key.is-unset {
  color: var(--text-secondary);
}

.prov-head .models-count {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--text-secondary);
  flex: none;
}

/* ── 详情形态（单实体全宽） ────────────────────────────── */
.entity-detail {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.detail-missing {
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border: 1px dashed var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
  text-align: center;
}

.detail-block {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
}

.block-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
}

.detail-block .use-main .el-select {
  flex: 1;
  max-width: 480px;
}

/* 身份行：名称 + 提供商 + 测试连通，横向铺开 */
.name-row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  flex-wrap: wrap;
}

.name-row .name-field {
  flex: 1;
  min-width: 220px;
  max-width: 420px;
}

.name-row .name-field:last-of-type {
  max-width: 360px;
}

.detail-test {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding-top: 22px; /* 与左侧标签行对齐 */
}

.detail-references {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-elevated);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.ref-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-right: var(--spacing-xs);
}

.usage-link {
  font-size: 12px;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: none;
  border-radius: 4px;
  padding: 2px 9px;
  cursor: pointer;
}

.usage-link:hover {
  text-decoration: underline;
}

.detail-embed-status {
  font-size: 12.5px;
}
</style>
