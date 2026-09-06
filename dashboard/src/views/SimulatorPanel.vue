<template>
  <div class="simulator-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">世界模拟器（开发基础设施）</h1>
        <p class="page-subtitle">
          <code>SimulatorService</code> 是唯一的模拟消息发射器，三模式切换：
          <strong>generate</strong>（LLM 生成）/ <strong>replay</strong>（录制回放）/
          <strong>off</strong>。仅在 <code>[simulator].enabled = true</code> 时装配。
        </p>
      </div>
      <div class="header-actions">
        <el-button
          v-if="status.enabled && status.is_available"
          :type="status.is_running ? 'danger' : 'success'"
          :loading="toggling !== null"
          @click="toggleSimulator"
        >
          {{ status.is_running ? '停止世界循环' : '启动世界循环' }}
        </el-button>
        <el-tag v-if="!status.enabled" type="info" size="large" effect="plain"> 未启用 </el-tag>
        <el-tag v-else-if="!status.is_available" type="warning" size="large" effect="plain">
          未注入
        </el-tag>
        <el-tag v-else-if="status.is_running" type="success" size="large" effect="dark">
          运行中 · {{ modeLabel }}
        </el-tag>
        <el-tag v-else type="warning" size="large" effect="plain">已停止</el-tag>
      </div>
    </header>

    <div class="content">
      <el-alert
        v-if="!status.enabled"
        type="info"
        title="模拟器当前未启用"
        description="请在 config/core.toml 的 [simulator] 段将 enabled 设为 true 并重启应用。mode 字段决定启动时走生成还是回放。"
        show-icon
        :closable="false"
      />

      <el-alert
        v-else-if="!status.is_available"
        type="warning"
        title="配置启用但服务未注入"
        description="通常因为 SQLiteStore / LLMManager 缺失（如 --dry 模式），或组合根跳过 [simulator] 装配。请检查 config/core.toml 的 [simulator] 段。"
        show-icon
        :closable="false"
      />

      <el-tabs v-else v-model="activeTab" class="workbench-tabs">
        <!-- ============================================================ -->
        <!-- Tab 1：世界控制（模式 + 回放）                                -->
        <!-- ============================================================ -->
        <el-tab-pane label="世界控制" name="control">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-card shadow="never">
                <template #header>
                  <span>回放控制（replay 模式）</span>
                </template>
                <el-form label-width="90px" size="default">
                  <el-form-item label="录制日期">
                    <el-select
                      v-model="selectedReplayDate"
                      placeholder="选择 data/events 下的录制日期"
                      style="width: 100%"
                      :loading="loadingDates"
                    >
                      <el-option v-for="d in replayDates" :key="d" :label="d" :value="d" />
                    </el-select>
                  </el-form-item>
                  <el-form-item>
                    <el-button
                      type="primary"
                      :disabled="status.is_running || !selectedReplayDate"
                      :loading="toggling === 'start'"
                      @click="startReplay"
                    >
                      启动回放
                    </el-button>
                    <el-button
                      v-if="status.is_running && status.mode === 'replay'"
                      type="danger"
                      @click="stopSimulator"
                    >
                      停止回放
                    </el-button>
                  </el-form-item>
                </el-form>
                <div
                  v-if="status.is_running && status.mode === 'replay' && replayProgress"
                  class="replay-progress"
                >
                  <p>
                    正在回放 <strong>{{ replayProgress.date }}</strong
                    >： 剩余 {{ replayProgress.remaining }} / {{ replayProgress.total }} 条
                  </p>
                  <el-progress
                    :percentage="replayPercent"
                    :stroke-width="10"
                    :format="() => `${replayPercent}%`"
                  />
                </div>
                <p class="hint">
                  回放读取
                  <code>data/events/YYYY-MM-DD.jsonl</code>（事件历史录制），按原节奏重放弹幕；
                  回放消息带 <code>simulated</code> 标记，落库时间戳刷新为当前时刻。
                </p>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card shadow="never">
                <template #header>
                  <span>当前状态</span>
                </template>
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item label="运行模式">
                    <el-tag size="small" :type="modeTagType">{{ modeLabel }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="运行中">
                    {{ status.is_running ? '是' : '否' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="配置 mode（重启生效）">
                    {{ status.config.mode ?? 'generate' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="LLM client">
                    {{ status.config.llm_client_type || 'llm_fast' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="Token 预算">
                    {{ formatNumber(status.config.token_budget_per_hour) }} / 小时
                  </el-descriptions-item>
                </el-descriptions>
                <p class="hint">
                  启动按
                  <code>[simulator].mode</code>
                  走生成或回放；此处选日期启动回放可在运行期临时指定录制日。
                </p>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ============================================================ -->
        <!-- Tab 2：常驻人设                                               -->
        <!-- ============================================================ -->
        <el-tab-pane label="常驻人设" name="personas">
          <el-card shadow="never">
            <template #header>
              <div class="card-header-row">
                <span>常驻观众（SQLite sim_personas 表，增删改即时落库）</span>
                <el-button type="primary" size="small" @click="openPersonaDialog()"
                  >新增人设</el-button
                >
              </div>
            </template>
            <el-table :data="personas" stripe size="small">
              <el-table-column prop="user_nickname" label="昵称" width="140" />
              <el-table-column prop="role" label="角色" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="roleTagType(row.role)">{{ row.role }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="personality"
                label="性格"
                min-width="200"
                show-overflow-tooltip
              />
              <el-table-column
                prop="speaking_style"
                label="说话风格"
                min-width="160"
                show-overflow-tooltip
              />
              <el-table-column prop="fans_medal_level" label="牌级" width="70" />
              <el-table-column prop="guard_level" label="舰" width="60" />
              <el-table-column prop="context_window_size" label="窗口" width="70">
                <template #default="{ row }">
                  {{ row.context_window_size ?? '默认' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="130" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openPersonaDialog(row)"
                    >编辑</el-button
                  >
                  <el-button link type="danger" size="small" @click="removePersona(row)"
                    >删除</el-button
                  >
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- ============================================================ -->
        <!-- Tab 3：礼物目录                                               -->
        <!-- ============================================================ -->
        <el-tab-pane label="礼物目录" name="gifts">
          <el-card shadow="never">
            <template #header>
              <div class="card-header-row">
                <span>礼物清单（SQLite sim_gifts 表，按权重随机触发）</span>
                <el-button type="primary" size="small" @click="openGiftDialog()"
                  >新增礼物</el-button
                >
              </div>
            </template>
            <el-table :data="gifts" stripe size="small">
              <el-table-column prop="gift_id" label="ID" width="160" />
              <el-table-column prop="gift_name" label="名称" width="160" />
              <el-table-column prop="category" label="类别" width="110">
                <template #default="{ row }">
                  <el-tag size="small" :type="categoryTagType(row.category)">{{
                    row.category
                  }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="weight" label="权重" width="80" />
              <el-table-column prop="data_type" label="事件类型" width="120" />
              <el-table-column label="SC 金额" width="90">
                <template #default="{ row }">
                  {{ row.sc_amount_rmb != null ? `¥${row.sc_amount_rmb}` : '—' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="130" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openGiftDialog(row)"
                    >编辑</el-button
                  >
                  <el-button link type="danger" size="small" @click="removeGift(row)"
                    >删除</el-button
                  >
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- ============================================================ -->
        <!-- Tab 4：配置与说明                                             -->
        <!-- ============================================================ -->
        <el-tab-pane label="配置与说明" name="config">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-card shadow="never">
                <template #header>
                  <span>关键配置（只读）</span>
                </template>
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item label="基础消息率">
                    {{ formatNumber(status.config.base_rate_per_minute) }} 条/分钟
                  </el-descriptions-item>
                  <el-descriptions-item label="节奏模式">
                    {{ status.config.cadence_mode || 'uniform' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="礼物概率">
                    {{ formatPercent(status.config.gift_probability) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="SC 概率">
                    {{ formatPercent(status.config.sc_probability) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="LLM 温度">
                    {{ formatNumber(status.config.llm_temperature) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="生成语言">
                    {{ status.config.language || 'zh' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="回放默认日期">
                    {{ status.config.replay_date ?? '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="回放速度">
                    {{ formatNumber(status.config.replay_speed) }}x
                  </el-descriptions-item>
                </el-descriptions>
                <p class="hint">
                  修改任一字段请编辑 <code>config/core.toml</code> 的
                  <code>[simulator]</code> 段并重启应用（运行时不支持热改）。
                </p>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card shadow="never">
                <template #header>
                  <span>如何观测模拟消息</span>
                </template>
                <div class="explain-block">
                  <p>
                    模拟器的消息以 <code>room.message.*</code> 事件推送到 EventBus，payload 携带
                    <code>simulated: true</code> 溯源标记；统计查询会主动排除模拟数据。
                  </p>
                  <p>
                    观众上下文（世界窗口）从 SQLite
                    <code>live_chat</code> 公共流读取——弹幕与主播发言同表， per-persona
                    窗口大小按"角色天性 → 人设覆盖"两级裁剪。
                  </p>
                  <p>
                    预算耗尽（<code>token_budget_per_hour</code>）时，模拟器进入 5s
                    等待恢复，期间不产生新消息但仍响应启停信号。
                  </p>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>
      </el-tabs>

      <p v-if="lastError" class="error-hint">{{ lastError }}</p>
    </div>

    <!-- ============================================================ -->
    <!-- 人设编辑对话框                                                -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="personaDialogVisible"
      :title="personaForm.user_id ? '编辑人设' : '新增人设'"
      width="520px"
    >
      <el-form :model="personaForm" label-width="110px" size="default">
        <el-form-item label="昵称" required>
          <el-input v-model="personaForm.user_nickname" maxlength="50" />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-select v-model="personaForm.role" style="width: 100%">
            <el-option v-for="r in personaRoles" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="性格" required>
          <el-input v-model="personaForm.personality" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="说话风格" required>
          <el-input v-model="personaForm.speaking_style" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="粉丝牌等级">
          <el-input-number v-model="personaForm.fans_medal_level" :min="0" :max="40" />
        </el-form-item>
        <el-form-item label="舰长等级">
          <el-input-number v-model="personaForm.guard_level" :min="0" :max="3" />
        </el-form-item>
        <el-form-item label="上下文窗口">
          <el-input-number
            v-model="personaForm.context_window_size"
            :min="1"
            :max="50"
            placeholder="留空=角色默认"
          />
          <span class="hint" style="margin-left: 8px"
            >留空 = 按角色默认（老观众看得多、路人看得少）</span
          >
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="personaDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePersona">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============================================================ -->
    <!-- 礼物编辑对话框                                                -->
    <!-- ============================================================ -->
    <el-dialog
      v-model="giftDialogVisible"
      :title="giftForm.gift_id ? '编辑礼物' : '新增礼物'"
      width="480px"
    >
      <el-form :model="giftForm" label-width="110px" size="default">
        <el-form-item label="礼物 ID" required>
          <el-input
            v-model="giftForm.gift_id"
            :disabled="!!giftFormOriginalId"
            placeholder="英文/数字/下划线"
          />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="giftForm.gift_name" maxlength="50" />
        </el-form-item>
        <el-form-item label="类别" required>
          <el-select v-model="giftForm.category" style="width: 100%">
            <el-option label="normal 普通" value="normal" />
            <el-option label="medium 中级" value="medium" />
            <el-option label="premium 高级" value="premium" />
            <el-option label="sc 大额 SC" value="sc" />
          </el-select>
        </el-form-item>
        <el-form-item label="权重">
          <el-input-number v-model="giftForm.weight" :min="1" />
        </el-form-item>
        <el-form-item label="事件类型">
          <el-select v-model="giftForm.data_type" style="width: 100%">
            <el-option label="gift 礼物" value="gift" />
            <el-option label="super_chat 醒目留言" value="super_chat" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="giftForm.category === 'sc'" label="SC 金额(元)">
          <el-input-number v-model="giftForm.sc_amount_rmb" :min="1" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="giftDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveGift">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * SimulatorPanel —— 世界模拟器工作台
 *
 * 数据源：`/api/v1/simulator/*`（详见 src/modules/dashboard/api/simulator.py）。
 * 形态：Tabs 工作台（世界控制 / 常驻人设 / 礼物目录 / 配置说明）。
 * 人设与礼物 CRUD 写穿 SQLite；回放日期来自事件历史录制目录。
 */
import { onMounted, onUnmounted, reactive, ref, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { simulatorApi } from '@/api';
import type { SimulatorReplayProgress, SimPersona, SimGift } from '@/types';

interface SimulatorStatusState {
  enabled: boolean;
  is_available: boolean;
  is_running: boolean;
  mode: string;
  replay_progress: SimulatorReplayProgress | null;
  message: string;
  config: Record<string, unknown>;
}

const status = reactive<SimulatorStatusState>({
  enabled: false,
  is_available: false,
  is_running: false,
  mode: 'off',
  replay_progress: null,
  message: '',
  config: {},
});

const activeTab = ref('control');
const toggling = ref<'start' | 'stop' | null>(null);
const saving = ref(false);
const lastError = ref('');
let pollTimer: ReturnType<typeof setInterval> | null = null;

// ---- 回放 ----
const replayDates = ref<string[]>([]);
const selectedReplayDate = ref('');
const loadingDates = ref(false);

// ---- 人设 ----
const personas = ref<SimPersona[]>([]);
const personaDialogVisible = ref(false);
const personaFormOriginalId = ref('');

// ---- 礼物 ----
const gifts = ref<SimGift[]>([]);
const giftDialogVisible = ref(false);
const giftFormOriginalId = ref('');

const personaRoles = [
  { value: 'fan', label: 'fan 粉丝' },
  { value: 'veteran', label: 'veteran 老观众' },
  { value: 'teaser', label: 'teaser 爱调侃' },
  { value: 'newcomer', label: 'newcomer 新人' },
  { value: 'hater', label: 'hater 黑粉' },
];

const emptyPersonaForm = () => ({
  user_id: '',
  user_nickname: '',
  role: 'fan',
  personality: '',
  speaking_style: '',
  fans_medal_level: 0,
  guard_level: 0,
  context_window_size: undefined as number | undefined,
});

const personaForm = reactive(emptyPersonaForm());

const emptyGiftForm = () => ({
  gift_id: '',
  gift_name: '',
  category: 'normal',
  weight: 1,
  data_type: 'gift',
  sc_amount_rmb: undefined as number | undefined,
});

const giftForm = reactive(emptyGiftForm());

const modeLabel = computed(() => {
  if (!status.is_running) return status.config.mode === 'replay' ? 'REPLAY(待启动)' : 'OFF';
  if (status.mode === 'generate') return 'GENERATE';
  if (status.mode === 'replay') return 'REPLAY';
  return 'OFF';
});

const modeTagType = computed(() => {
  if (!status.is_running) return 'info' as const;
  return status.mode === 'replay' ? ('warning' as const) : ('success' as const);
});

const replayProgress = computed<SimulatorReplayProgress | null>(() => status.replay_progress);

const replayPercent = computed(() => {
  const p = status.replay_progress;
  if (!p || p.total === 0) return 0;
  return Math.round(((p.total - p.remaining) / p.total) * 100);
});

function roleTagType(role: string) {
  if (role === 'veteran') return 'warning';
  if (role === 'hater') return 'danger';
  if (role === 'newcomer') return 'success';
  return 'primary';
}

function categoryTagType(category: string) {
  if (category === 'sc') return 'danger';
  if (category === 'premium') return 'warning';
  if (category === 'medium') return 'primary';
  return 'info';
}

function formatNumber(v: unknown): string {
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toString();
    return v.toFixed(2);
  }
  return '—';
}

function formatPercent(v: unknown): string {
  if (typeof v === 'number') return `${(v * 100).toFixed(1)}%`;
  return '—';
}

async function fetchStatus() {
  try {
    const res = await simulatorApi.getStatus();
    const data = res.data;
    status.enabled = !!data.enabled;
    status.is_available = !!data.is_available;
    status.is_running = !!data.is_running;
    status.mode = typeof data.mode === 'string' ? data.mode : 'off';
    status.replay_progress = data.replay_progress ?? null;
    status.message = typeof data.message === 'string' ? data.message : '';
    status.config =
      data.config && typeof data.config === 'object' && !Array.isArray(data.config)
        ? (data.config as Record<string, unknown>)
        : {};
    lastError.value = '';
  } catch (err) {
    lastError.value = err instanceof Error ? `状态获取失败：${err.message}` : '状态获取失败';
  }
}

async function fetchReplayDates() {
  loadingDates.value = true;
  try {
    const res = await simulatorApi.listReplayDates();
    replayDates.value = Array.isArray(res.data.dates) ? res.data.dates : [];
  } catch {
    replayDates.value = [];
  } finally {
    loadingDates.value = false;
  }
}

async function fetchPersonas() {
  try {
    const res = await simulatorApi.listPersonas();
    personas.value = res.data.personas ?? [];
  } catch {
    personas.value = [];
  }
}

async function fetchGifts() {
  try {
    const res = await simulatorApi.listGifts();
    gifts.value = res.data.gifts ?? [];
  } catch {
    gifts.value = [];
  }
}

async function startReplay() {
  if (!selectedReplayDate.value) return;
  toggling.value = 'start';
  try {
    const res = await simulatorApi.start(selectedReplayDate.value);
    if (res.data.success) {
      ElMessage.success('回放已启动');
    } else {
      ElMessage.warning(res.data.message || '启动被拒绝');
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '启动失败');
  } finally {
    toggling.value = null;
    await fetchStatus();
  }
}

async function toggleSimulator() {
  if (!status.enabled || !status.is_available) return;
  const action = status.is_running ? 'stop' : 'start';
  toggling.value = action;
  try {
    const res = action === 'start' ? await simulatorApi.start() : await simulatorApi.stop();
    if (res.data.success) {
      ElMessage.success(res.data.message || (action === 'start' ? '已启动' : '已停止'));
    } else {
      ElMessage.warning(res.data.message || '操作被拒绝');
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败');
  } finally {
    toggling.value = null;
    await fetchStatus();
  }
}

async function stopSimulator() {
  toggling.value = 'stop';
  try {
    await simulatorApi.stop();
    ElMessage.success('已停止');
  } finally {
    toggling.value = null;
    await fetchStatus();
  }
}

// ---- 人设 CRUD ----

function openPersonaDialog(row?: SimPersona) {
  personaFormOriginalId.value = row?.user_id ?? '';
  Object.assign(personaForm, emptyPersonaForm());
  if (row) {
    personaForm.user_id = row.user_id;
    personaForm.user_nickname = row.user_nickname;
    personaForm.role = row.role;
    personaForm.personality = row.personality;
    personaForm.speaking_style = row.speaking_style;
    personaForm.fans_medal_level = row.fans_medal_level;
    personaForm.guard_level = row.guard_level;
    personaForm.context_window_size = row.context_window_size ?? undefined;
  }
  personaDialogVisible.value = true;
}

async function savePersona() {
  saving.value = true;
  try {
    if (personaFormOriginalId.value) {
      const payload = {
        user_nickname: personaForm.user_nickname,
        role: personaForm.role,
        personality: personaForm.personality,
        speaking_style: personaForm.speaking_style,
        fans_medal_level: personaForm.fans_medal_level,
        guard_level: personaForm.guard_level,
        context_window_size: personaForm.context_window_size ?? null,
      };
      const res = await simulatorApi.updatePersona(personaFormOriginalId.value, payload);
      res.data.success
        ? ElMessage.success('已保存')
        : ElMessage.warning(res.data.message || '保存失败');
    } else {
      const res = await simulatorApi.createPersona({
        user_nickname: personaForm.user_nickname,
        role: personaForm.role,
        personality: personaForm.personality,
        speaking_style: personaForm.speaking_style,
        fans_medal_level: personaForm.fans_medal_level,
        guard_level: personaForm.guard_level,
        context_window_size: personaForm.context_window_size ?? null,
      });
      res.data.success
        ? ElMessage.success('已新增')
        : ElMessage.warning(res.data.message || '新增失败');
    }
    personaDialogVisible.value = false;
    await fetchPersonas();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败');
  } finally {
    saving.value = false;
  }
}

async function removePersona(row: SimPersona) {
  try {
    await ElMessageBox.confirm(`确认删除人设「${row.user_nickname}」？`, '删除确认', {
      type: 'warning',
    });
  } catch {
    return;
  }
  try {
    const res = await simulatorApi.deletePersona(row.user_id);
    res.data.success
      ? ElMessage.success('已删除')
      : ElMessage.warning(res.data.message || '删除失败');
    await fetchPersonas();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败');
  }
}

// ---- 礼物 CRUD ----

function openGiftDialog(row?: SimGift) {
  giftFormOriginalId.value = row?.gift_id ?? '';
  Object.assign(giftForm, emptyGiftForm());
  if (row) {
    giftForm.gift_id = row.gift_id;
    giftForm.gift_name = row.gift_name;
    giftForm.category = row.category;
    giftForm.weight = row.weight;
    giftForm.data_type = row.data_type;
    giftForm.sc_amount_rmb = row.sc_amount_rmb ?? undefined;
  }
  giftDialogVisible.value = true;
}

async function saveGift() {
  saving.value = true;
  try {
    if (giftFormOriginalId.value) {
      const payload = {
        gift_name: giftForm.gift_name,
        category: giftForm.category,
        weight: giftForm.weight,
        data_type: giftForm.data_type,
        sc_amount_rmb: giftForm.sc_amount_rmb ?? null,
      };
      const res = await simulatorApi.updateGift(giftFormOriginalId.value, payload);
      res.data.success
        ? ElMessage.success('已保存')
        : ElMessage.warning(res.data.message || '保存失败');
    } else {
      const res = await simulatorApi.createGift({
        gift_id: giftForm.gift_id,
        gift_name: giftForm.gift_name,
        category: giftForm.category,
        weight: giftForm.weight,
        data_type: giftForm.data_type,
        sc_amount_rmb: giftForm.sc_amount_rmb ?? null,
      });
      res.data.success
        ? ElMessage.success('已新增')
        : ElMessage.warning(res.data.message || '新增失败');
    }
    giftDialogVisible.value = false;
    await fetchGifts();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败');
  } finally {
    saving.value = false;
  }
}

async function removeGift(row: SimGift) {
  try {
    await ElMessageBox.confirm(`确认删除礼物「${row.gift_name}」？`, '删除确认', {
      type: 'warning',
    });
  } catch {
    return;
  }
  try {
    const res = await simulatorApi.deleteGift(row.gift_id);
    res.data.success
      ? ElMessage.success('已删除')
      : ElMessage.warning(res.data.message || '删除失败');
    await fetchGifts();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败');
  }
}

onMounted(async () => {
  await fetchStatus();
  await Promise.all([fetchReplayDates(), fetchPersonas(), fetchGifts()]);
  // 5s 轮询以捕捉按钮外的状态变化（回放进度、预算耗尽等）
  pollTimer = setInterval(fetchStatus, 5000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.simulator-page {
  display: flex;
  flex-direction: column;
  padding: var(--spacing-lg);
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: var(--spacing-md);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  min-width: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.workbench-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.replay-progress {
  margin-top: var(--spacing-sm);
}

.replay-progress p {
  font-size: 13px;
  color: var(--text-regular);
  margin-bottom: var(--spacing-xs);
}

.explain-block {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-regular);
}

.explain-block code {
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: var(--spacing-sm);
  font-style: italic;
}

.hint code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
  font-style: normal;
}

.error-hint {
  font-size: 12px;
  color: var(--color-danger, #f56c6c);
  margin: var(--spacing-sm) 0 0;
}

code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}
</style>
