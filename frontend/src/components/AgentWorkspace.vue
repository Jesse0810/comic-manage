<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowRight, Check, Collection, Filter, Loading, MagicStick, Operation, Setting, VideoPause } from '@element-plus/icons-vue'
import { invoke } from '../api'
import { displayTitle, errorMessage } from '../types'
import type { Comic, ModelConfiguration, ModelOptions, ModelReasoning, ModelSpeed } from '../types'
import { reasoningLabel, reasoningOptions, speedLabel, speedOptions } from '../model-options'

const props = defineProps<{
  selected: Comic[]
  filtered: Comic[]
  configuration: ModelConfiguration
  disabled: boolean
}>()
const emit = defineEmits<{
  'model-settings': []
  applied: []
  'task-state': [working: boolean]
}>()

type Scope = 'selected' | 'filtered'
type Work = 'prepare' | 'apply'
interface Change {
  field: string
  before: unknown
  after: unknown
}
interface BookPlan {
  id: string
  title: string
  fields: Change[]
  warnings: string[]
  source?: string
}
interface AgentPlan {
  token: string | null
  books: BookPlan[]
  cancelled: boolean
}
interface ApplyResult {
  updated: number
  skipped: number
  errors: { id: string; title: string; message: string }[]
  cancelled: boolean
}
type QuickOptions = Partial<Pick<ModelOptions, 'reasoning' | 'speed'>>
interface TaskInput {
  scope: Scope
  ids: string[]
  model: string
  prompt: string
  sources: boolean
  options: QuickOptions
  modelRevision: string
  modelSummary: string[]
}

const MAX_BOOKS = 24
const labels: Record<string, string> = {
  title: '作品名称', authors: '作者', pencillers: '绘画者', publisher: '出版方',
  translator: '译者', genre: '内容类型', tags: '题材与属性', language: '语言',
  series: '系列名称', publication_format: '出版形态', work_group: '作品组', text_version: '文字版本', color: '色彩',
  release_type: '发行性质', release_platforms: '发布平台', release_date: '发行日期',
}
const examples = [
  { label: '规范标题', prompt: '去掉作品名称中作为说明的前后缀，保留原文标题。' },
  { label: '补齐署名', prompt: '核对作者、绘画者和译者，只补齐当前空缺且有明确依据的字段。' },
  { label: '整理分类', prompt: '根据已有资料补全内容类型和题材标签，保留明确的原有标签。' },
]

const scope = ref<Scope>('selected')
const prompt = ref('')
const model = ref('')
const reasoningOverride = ref<ModelReasoning | 'inherit'>('inherit')
const speedOverride = ref<ModelSpeed | 'inherit'>('inherit')
const useSources = ref(false)
const showTargets = ref(false)
const work = ref<Work | ''>('')
const cancelling = ref(false)
const progress = ref<string[]>([])
const problem = ref('')
const plan = ref<AgentPlan | null>(null)
const reviewAnchor = ref<HTMLElement | null>(null)
const submitted = ref<TaskInput | null>(null)
const submittedModel = ref('')
const report = ref<ApplyResult | null>(null)
const checked = ref(new Map<string, Set<string>>())
const expanded = ref(new Set<string>())
let unsubscribe: (() => void) | undefined

const modelProfile = computed(() => props.configuration.profiles.find(item => item.id === model.value))
const reasoningChoices = computed(() => modelProfile.value ? reasoningOptions(modelProfile.value) : [])
const speedChoices = computed(() => modelProfile.value ? speedOptions(modelProfile.value) : [])
const hasReasoning = computed(() => reasoningChoices.value.some(option => option.value !== 'default'))
const hasSpeed = computed(() => speedChoices.value.some(option => option.value !== 'default'))
const savedReasoning = computed(() => selectedOption(modelProfile.value?.options?.reasoning, reasoningChoices.value) ?? 'default')
const savedSpeed = computed(() => selectedOption(modelProfile.value?.options?.speed, speedChoices.value) ?? 'default')
const quickOptions = computed<QuickOptions>(() => {
  const reasoning = selectedOption(reasoningOverride.value, reasoningChoices.value)
  const speed = selectedOption(speedOverride.value, speedChoices.value)
  return {
    ...(hasReasoning.value && reasoning ? { reasoning } : {}),
    ...(hasSpeed.value && speed ? { speed } : {}),
  }
})
const modelSummary = computed(() => [
  ...(hasReasoning.value ? ['思考 ' + reasoningLabel(quickOptions.value.reasoning ?? savedReasoning.value)] : []),
  ...(hasSpeed.value ? ['速度 ' + speedLabel(quickOptions.value.speed ?? savedSpeed.value)] : []),
])
const modelRevision = computed(() => {
  const profile = modelProfile.value
  if (!profile) return ''
  const options = Object.entries({ ...profile.options, ...quickOptions.value }).sort(([a], [b]) => a.localeCompare(b))
  return JSON.stringify([profile.provider, profile.base_url, profile.model, options])
})
const targets = computed(() => [...new Map(props[scope.value].map(book => [book.id, book])).values()])
const caseCount = computed(() => targets.value.length)
const request = computed<TaskInput>(() => ({
  scope: scope.value,
  ids: targets.value.map(book => book.id),
  model: model.value,
  prompt: prompt.value.trim(),
  sources: useSources.value,
  options: quickOptions.value,
  modelRevision: modelRevision.value,
  modelSummary: modelSummary.value,
}))
const availableForPrompt = computed(() => !props.disabled && !work.value)
const canGenerate = computed(() => availableForPrompt.value && !!modelProfile.value && !!request.value.prompt &&
  caseCount.value > 0 && caseCount.value <= MAX_BOOKS)
const isStale = computed(() => {
  const past = submitted.value
  const current = request.value
  return past != null && (past.scope !== current.scope || past.model !== current.model ||
    past.modelRevision !== current.modelRevision || past.prompt !== current.prompt || past.sources !== current.sources ||
    past.ids.length !== current.ids.length || past.ids.some(id => !current.ids.includes(id)))
})
const reviewEnabled = computed(() => availableForPrompt.value && !isStale.value && !report.value &&
  !!plan.value?.token && !plan.value.cancelled)
const currentProgress = computed(() => progress.value.at(-1))
const actionable = computed(() => (plan.value?.books ?? []).filter(book => book.fields.length))
const totalChanges = computed(() => actionable.value.reduce((sum, book) => sum + book.fields.length, 0))
const chosen = computed(() => (plan.value?.books ?? []).flatMap(book => {
  const fields = book.fields.filter(item => checked.value.get(book.id)?.has(item.field)).map(item => item.field)
  return fields.length ? [{ id: book.id, fields }] : []
}))
const chosenFields = computed(() => chosen.value.reduce((sum, item) => sum + item.fields.length, 0))

function selectedOption<T extends string>(value: T | 'inherit' | undefined, choices: readonly { value: T }[]): T | undefined {
  return value && value !== 'inherit' && choices.some(option => option.value === value) ? value : undefined
}

watch(model, () => {
  reasoningOverride.value = 'inherit'
  speedOverride.value = 'inherit'
}, { flush: 'sync' })
watch([reasoningChoices, speedChoices], ([thinking, speeds]) => {
  if (!selectedOption(reasoningOverride.value, thinking)) reasoningOverride.value = 'inherit'
  if (!selectedOption(speedOverride.value, speeds)) speedOverride.value = 'inherit'
})

watch(() => props.configuration, config => {
  if (!config.profiles.some(item => item.id === model.value)) {
    model.value = config.profiles.find(item => item.id === config.default_id)?.id || config.profiles[0]?.id || ''
  }
}, { deep: true, immediate: true })

onMounted(() => {
  unsubscribe = window.comic?.onEvent((event, data) => {
    if (work.value && event === 'task-progress' && typeof data?.message === 'string') {
      if (data.message !== currentProgress.value) progress.value = [...progress.value.slice(-23), data.message]
    }
  })
})
onBeforeUnmount(() => unsubscribe?.())

function blank(value: unknown): boolean {
  if (Array.isArray(value)) return !value.length || value.every(blank)
  if (value && typeof value === 'object') return Object.values(value).every(blank)
  return value == null || (typeof value === 'string' && (!value.trim() || ['unknown', '未知', '待确认', '未确认'].includes(value.trim().toLowerCase())))
}

function formatValue(value: unknown) {
  if (blank(value)) return '未填写'
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object' && 'year' in value) {
    const { year, month, day } = value as Record<string, unknown>
    return [year && String(year) + '年', month && String(month) + '月', day && String(day) + '日'].filter(Boolean).join('')
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function sourceLabel(source: string) {
  try { return new URL(source).hostname.replace(/^www\./, '') }
  catch { return source }
}

function toggleExpanded(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}

function selectField(id: string, field: string, value: unknown) {
  if (!reviewEnabled.value) return
  const target = checked.value.get(id) ?? new Set<string>()
  if (value === true) target.add(field)
  else target.delete(field)
  checked.value.set(id, target)
}

function chooseBlankFields() {
  checked.value.clear()
  for (const book of actionable.value) {
    const empty = book.fields.filter(item => blank(item.before)).map(item => item.field)
    if (empty.length) checked.value.set(book.id, new Set(empty))
  }
}

function startTask(kind: Work, message: string) {
  work.value = kind
  cancelling.value = false
  problem.value = ''
  progress.value = [message]
  emit('task-state', true)
}

function endTask() {
  work.value = ''
  cancelling.value = false
  emit('task-state', false)
}

async function prepare() {
  if (!canGenerate.value || !modelProfile.value) return
  const input = request.value
  submitted.value = input
  submittedModel.value = modelProfile.value.name
  plan.value = null
  report.value = null
  checked.value.clear()
  expanded.value.clear()
  startTask('prepare', '正在检查本次整理的作品…')
  try {
    const response = await invoke<AgentPlan>('agent_prepare', {
      ids: input.ids, profile_id: input.model, prompt: input.prompt, use_sources: input.sources,
      ...(Object.keys(input.options).length ? { options: input.options } : {}),
    })
    plan.value = response
    const first = response.books.find(book => book.fields.length) || response.books.find(book => book.warnings.length)
    if (first) expanded.value.add(first.id)
    await nextTick()
    reviewAnchor.value?.scrollIntoView({ block: 'start' })
  } catch (error) { problem.value = errorMessage(error) }
  finally { endTask() }
}

async function apply() {
  if (!reviewEnabled.value || !plan.value?.token || !chosen.value.length) return
  const payload = { token: plan.value.token, books: chosen.value }
  startTask('apply', '正在保存已核对的草稿…')
  try {
    report.value = await invoke<ApplyResult>('agent_apply', payload)
    if (report.value.updated) emit('applied')
    await nextTick()
    reviewAnchor.value?.scrollIntoView({ block: 'start' })
  } catch (error) {
    // A commit may consume its plan before checking a later constraint.
    // Fetching another plan avoids displaying an actionable, expired token.
    if (plan.value) plan.value.token = null
    problem.value = errorMessage(error)
  } finally { endTask() }
}

async function cancel() {
  if (!work.value || cancelling.value) return
  cancelling.value = true
  try { await invoke('cancel') }
  catch (error) {
    problem.value = errorMessage(error)
    cancelling.value = false
  }
}

async function openChatGPTUsage() {
  try { await invoke('model_chatgpt_usage') }
  catch (error) { problem.value = errorMessage(error) }
}
</script>

<template>
  <section class="agent-workspace" aria-labelledby="agent-heading" data-testid="agent-workspace">
    <header class="agent-heading">
      <span class="agent-mark"><el-icon :size="23"><MagicStick /></el-icon></span>
      <div>
        <h1 id="agent-heading">Agent 整理</h1>
        <p>描述整理要求，为选定作品生成一份可核对的批量方案。</p>
      </div>
    </header>

    <div class="agent-scroll">
      <div class="agent-layout">
        <form id="agent-task" class="agent-card agent-composer" @submit.prevent="prepare">
          <div class="agent-card-heading">
            <h2>整理任务</h2>
            <span v-if="work" class="agent-running-badge" role="status"><el-icon class="is-loading"><Loading /></el-icon>{{ work === 'prepare' ? '正在整理' : '正在保存' }}</span>
          </div>
          <label for="agent-prompt-input">这批漫画要怎样整理？</label>
          <el-input
            id="agent-prompt-input"
            v-model="prompt"
            data-testid="agent-prompt"
            type="textarea"
            :autosize="{ minRows: 5, maxRows: 9 }"
            resize="none"
            :maxlength="1200"
            show-word-limit
            :disabled="!availableForPrompt"
            placeholder="例如：核对作者和译者，只补齐有依据的空白字段。清理标题中的括号备注。"
            @keydown.ctrl.enter.prevent="prepare"
            @keydown.meta.enter.prevent="prepare"
          />
          <div class="agent-examples" aria-label="任务示例">
            <button v-for="item in examples" :key="item.label" type="button" :disabled="!availableForPrompt" @click="prompt = item.prompt">
              {{ item.label }}
            </button>
          </div>
          <div class="agent-model-toolbar" role="group" aria-label="本次任务模型" title="在此为 Agent 设置独立模型参数" data-testid="agent-quick-model">
            <div class="agent-model-field">
              <div class="agent-model-label">
                <label for="agent-model-select">使用模型</label>
                <el-button link :icon="Setting" :disabled="!!work" data-testid="agent-open-model-settings"
                  @click="emit('model-settings')">模型管理</el-button>
              </div>
              <el-select id="agent-model-select" v-model="model" :disabled="!availableForPrompt"
                data-testid="agent-model" filterable
                :placeholder="configuration.profiles.length ? '选择模型' : '请先添加模型'">
                <el-option v-for="item in configuration.profiles" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </div>
            <div class="agent-model-field">
              <div class="agent-model-label"><label for="agent-reasoning-select">思考等级</label></div>
              <el-select id="agent-reasoning-select" v-model="reasoningOverride"
                :disabled="!availableForPrompt || !hasReasoning" data-testid="agent-reasoning">
                <el-option :label="hasReasoning ? '沿用配置（' + reasoningLabel(savedReasoning) + '）' : '由供应商决定'" value="inherit" />
                <el-option v-for="item in reasoningChoices" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </div>
            <div class="agent-model-field">
              <div class="agent-model-label"><label for="agent-speed-select">速度等级</label></div>
              <el-select id="agent-speed-select" v-model="speedOverride" :disabled="!availableForPrompt || !hasSpeed"
                data-testid="agent-speed" title="速度等级由供应商提供。优先档位可能使用更多额度。">
                <el-option :label="hasSpeed ? '沿用配置（' + speedLabel(savedSpeed) + '）' : '由供应商决定'" value="inherit" />
                <el-option v-for="item in speedChoices" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </div>
          </div>
          <div class="agent-actions">
            <span>每次最多 {{ MAX_BOOKS }} 本</span>
            <el-button
              v-if="work"
              :icon="VideoPause"
              :loading="cancelling"
              :disabled="cancelling"
              data-testid="agent-cancel"
              @click="cancel"
            >{{ cancelling ? '正在取消' : '取消任务' }}</el-button>
            <el-button
              v-else
              type="primary"
              native-type="submit"
              :icon="MagicStick"
              :disabled="!canGenerate"
              data-testid="agent-start"
            >生成整理方案</el-button>
          </div>
        </form>

        <aside class="agent-card agent-settings" aria-label="本次整理范围和来源">
          <div class="agent-card-heading"><h2><el-icon><Operation /></el-icon>任务范围</h2></div>
          <div class="agent-scopes" role="group" aria-label="整理范围">
            <button
              type="button"
              data-testid="agent-scope-selected"
              :class="{ 'is-active': scope === 'selected' }"
              :aria-pressed="scope === 'selected'"
              :disabled="!availableForPrompt"
              @click="scope = 'selected'; showTargets = false"
            ><el-icon><Collection /></el-icon><span>列表勾选</span><strong>{{ selected.length }}</strong></button>
            <button
              type="button"
              data-testid="agent-scope-filtered"
              :class="{ 'is-active': scope === 'filtered' }"
              :aria-pressed="scope === 'filtered'"
              :disabled="!availableForPrompt"
              @click="scope = 'filtered'; showTargets = false"
            ><el-icon><Filter /></el-icon><span>筛选结果</span><strong>{{ filtered.length }}</strong></button>
          </div>
          <p v-if="caseCount > MAX_BOOKS" class="agent-scope-note agent-attention" role="status">当前有 {{ caseCount }} 本，请缩小筛选结果或在列表勾选最多 {{ MAX_BOOKS }} 本。</p>
          <p v-else-if="!caseCount" class="agent-scope-note" role="status">{{ scope === 'selected' ? '请先回列表勾选作品，或切换到筛选结果。' : '当前筛选范围没有可编辑的作品。' }}</p>
          <template v-else>
            <button type="button" class="agent-target-toggle" :aria-expanded="showTargets" @click="showTargets = !showTargets">
              <el-icon><component :is="showTargets ? ArrowDown : ArrowRight" /></el-icon>
              {{ showTargets ? '收起' : '查看' }}本次 {{ caseCount }} 本作品
            </button>
            <ul v-if="showTargets" class="agent-targets">
              <li v-for="book in targets" :key="book.id">{{ displayTitle(book.metadata) }}</li>
            </ul>
          </template>

          <div v-if="modelProfile?.provider === 'chatgpt-plan'" class="agent-plan-usage">
            <span>使用 ChatGPT 套餐</span>
            <el-button link data-testid="agent-chatgpt-usage" @click="openChatGPTUsage">管理用量</el-button>
          </div>
          <el-checkbox v-model="useSources" :disabled="!availableForPrompt" data-testid="agent-use-sources">尝试读取已保存的作品来源</el-checkbox>
          <p class="agent-scope-note">开启后尝试读取已保存且受支持的站点链接。所选模型接收整理所需的作品文字资料。</p>
        </aside>
      </div>

      <section v-if="work || progress.length > 1 || problem" class="agent-card agent-activity" aria-label="任务进度">
        <p v-if="work" class="agent-progress" aria-live="polite"><el-icon class="is-loading"><Loading /></el-icon>{{ currentProgress }}</p>
        <el-alert v-if="problem" :title="problem" type="error" show-icon :closable="false" />
        <details v-if="progress.length > 1">
          <summary>查看处理记录</summary>
          <ul><li v-for="(message, index) in progress" :key="index">{{ message }}</li></ul>
        </details>
      </section>

      <section v-if="plan && submitted" ref="reviewAnchor" class="agent-card agent-review" aria-labelledby="agent-result-heading" data-testid="agent-result">
        <div class="agent-review-header">
          <div>
            <h2 id="agent-result-heading">整理方案</h2>
            <p data-testid="agent-result-settings">{{ [submittedModel, ...submitted.modelSummary].join(' · ') }} · 检查 {{ plan.books.length }} 本 · {{ actionable.length }} 本有建议 · {{ totalChanges }} 项字段</p>
          </div>
          <div v-if="totalChanges" class="agent-review-tools">
            <el-button link :disabled="!reviewEnabled" data-testid="agent-select-empty" @click="chooseBlankFields">勾选空白字段</el-button>
            <el-button link :disabled="!reviewEnabled || !chosenFields" @click="checked.clear()">取消勾选</el-button>
          </div>
        </div>
        <p class="agent-task-quote">{{ submitted.prompt }}</p>
        <el-alert v-if="plan.cancelled" title="任务已取消。请重新生成后再保存草稿。" type="warning" show-icon :closable="false" />
        <el-alert v-else-if="isStale && !report" title="本次任务设置或作品范围已变化，请重新生成方案。" type="warning" show-icon :closable="false" />
        <div v-if="report" class="agent-report" data-testid="agent-report" role="status">
          <div class="agent-report-summary"><el-icon><Check /></el-icon><strong>已保存 {{ report.updated }} 本草稿</strong><span v-if="report.skipped">跳过 {{ report.skipped }} 本</span><span v-if="report.cancelled">保存已取消</span></div>
          <ul v-if="report.errors?.length"><li v-for="entry in report.errors" :key="entry.id"><strong>{{ entry.title || '作品' }}</strong> {{ entry.message }}</li></ul>
        </div>
        <div v-if="!plan.books.length" class="agent-review-empty">本次没有可核对的作品。</div>
        <div v-else class="agent-book-list">
          <article v-for="book in plan.books" :key="book.id" class="agent-book" data-testid="agent-book" :data-book-id="book.id">
            <button
              type="button"
              class="agent-book-heading"
              :aria-expanded="expanded.has(book.id)"
              @click="toggleExpanded(book.id)"
            >
              <el-icon><component :is="expanded.has(book.id) ? ArrowDown : ArrowRight" /></el-icon>
              <strong>{{ book.title || '未命名作品' }}</strong>
              <span v-if="checked.get(book.id)?.size" class="agent-book-picked">已勾选 {{ checked.get(book.id)?.size }} 项</span>
              <span class="agent-book-count">{{ book.fields.length ? book.fields.length + ' 项建议' : '无修改建议' }}</span>
            </button>
            <div v-if="expanded.has(book.id)" class="agent-book-body">
              <p v-if="book.source" class="agent-book-source">使用已保存的 {{ sourceLabel(book.source) }} 资料</p>
              <ul v-if="book.warnings?.length" class="agent-book-warnings"><li v-for="(warning, index) in book.warnings" :key="index">{{ warning }}</li></ul>
              <div v-if="book.fields.length" class="agent-diff" role="table" :aria-label="(book.title || '作品') + ' 的字段对照'">
                <div class="agent-diff-header" role="row"><strong role="columnheader">选择字段</strong><strong role="columnheader">当前值</strong><strong role="columnheader">建议值</strong></div>
                <div
                  v-for="field in book.fields"
                  :key="field.field"
                  class="agent-diff-row"
                  role="row"
                  data-testid="agent-field"
                  :data-book-id="book.id"
                  :data-field="field.field"
                  :class="{ 'is-checked': checked.get(book.id)?.has(field.field) }"
                >
                  <div role="cell">
                    <el-checkbox :model-value="checked.get(book.id)?.has(field.field) || false" :disabled="!reviewEnabled" @update:model-value="selectField(book.id, field.field, $event)">{{ labels[field.field] || field.field }}</el-checkbox>
                  </div>
                  <div class="agent-diff-before" role="cell">{{ formatValue(field.before) }}</div>
                  <div class="agent-diff-after" role="cell">{{ formatValue(field.after) }}</div>
                </div>
              </div>
            </div>
          </article>
        </div>
        <footer v-if="totalChanges && !report" class="agent-review-footer">
          <span>已选 {{ chosen.length }} 本、{{ chosenFields }} 项。保存后进入待审核。</span>
          <el-button type="primary" :icon="Check" :loading="work === 'apply'" :disabled="!reviewEnabled || !chosenFields" data-testid="agent-apply" @click="apply">保存勾选项到草稿</el-button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.agent-workspace {
  --agent-ink: #243d33;
  --agent-muted: #687f70;
  --agent-line: #e0e8de;
  --agent-tint: #f2f6ef;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.agent-heading { display: flex; align-items: center; gap: 13px; margin-bottom: 17px; }
.agent-heading h1 { color: var(--agent-ink); }
.agent-heading p { margin: 5px 0 0; color: var(--agent-muted); line-height: 1.6; }
.agent-mark { width: 44px; height: 44px; flex: 0 0 44px; display: grid; place-content: center; background: #e5eee3; color: #398064; border: 1px solid #dce9da; border-radius: 11px; }
.agent-scroll { min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 0 2px 14px 0; scrollbar-width: thin; scrollbar-color: #cddcc9 transparent; }
.agent-layout { display: grid; grid-template-columns: minmax(0, 1fr) clamp(225px, 29%, 306px); align-items: start; gap: 16px; }
.agent-card { min-width: 0; padding: 19px 20px; border: 1px solid var(--agent-line); border-radius: 12px; background: #fff; box-shadow: 0 1px 2px #213b2804; }
.agent-card h2 { color: var(--agent-ink); font-size: 16px; font-weight: 650; margin: 0; line-height: 1.45; }
.agent-card-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 13px; }
.agent-card-heading h2 { display: flex; align-items: center; gap: 7px; }
.agent-card-heading h2 .el-icon { color: #659474; }
.agent-composer label { display: block; color: #516d5e; font-weight: 600; margin-bottom: 8px; }
.agent-composer :deep(.el-textarea__inner) { background: #fbfcfa; font-size: 13px; color: var(--agent-ink); line-height: 1.7; padding: 12px 13px 24px; }
.agent-composer :deep(.el-input__count) { background: #fbfcfa; color: var(--agent-muted); }
.agent-examples { display: flex; flex-wrap: wrap; gap: 8px; margin: 13px 0 15px; }
.agent-examples button { color: #547363; border: 1px solid #dfe9df; background: #f7faf6; border-radius: 7px; padding: 5px 11px; min-height: 28px; }
.agent-examples button:hover:not(:disabled) { color: #2d6248; border-color: #b9d2be; background: #eaf4e9; }
.agent-examples button:disabled { opacity: .62; }
.agent-model-toolbar { display: grid; grid-template-columns: minmax(0, 1.4fr) repeat(2, minmax(0, 1fr)); gap: 12px; padding: 11px 12px 12px; margin-bottom: 12px; border: 1px solid #e4ebe2; border-radius: 8px; background: #f8faf7; }
.agent-model-field { min-width: 0; }
.agent-model-label { display: flex; align-items: center; justify-content: space-between; gap: 5px; min-height: 22px; margin-bottom: 5px; }
.agent-model-label label { font-size: 11px; margin: 0; }
.agent-model-label .el-button { flex-shrink: 0; margin: 0; padding: 0; font-size: 11px; }
.agent-model-field .el-select { width: 100%; }
.agent-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; border-top: 1px solid #edf1eb; padding-top: 13px; color: var(--agent-muted); }
.agent-running-badge { display: inline-flex; align-items: center; gap: 5px; color: #4a7c62; font-size: 12px; }

.agent-settings { background: #fcfdfb; padding: 17px 15px; }
.agent-scopes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.agent-scopes button { display: grid; align-items: center; grid-template-columns: auto minmax(0, 1fr); gap: 4px 6px; text-align: left; color: #547363; background: #fff; border: 1px solid #dee9dd; border-radius: 8px; padding: 8px; min-height: 57px; }
.agent-scopes button .el-icon { color: #698676; }
.agent-scopes button strong { grid-column: 2; color: var(--agent-ink); font-size: 15px; }
.agent-scopes button.is-active { color: #2d654c; border-color: #a7c8af; background: #edf5ec; }
.agent-scopes button:hover:not(:disabled) { border-color: #90b69b; }
.agent-scopes button:disabled { opacity: .65; }
.agent-scope-note { margin: 9px 0 0; color: var(--agent-muted); font-size: 11px; line-height: 1.65; }
.agent-scope-note.agent-attention { color: #94652e; }
.agent-target-toggle { margin-top: 6px; padding: 3px 0; border: 0; background: transparent; color: #507663; display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.agent-target-toggle:hover { color: #29654b; }
.agent-targets { margin: 5px 0; padding-left: 21px; color: #55715f; max-height: 145px; overflow: auto; line-height: 1.68; overflow-wrap: anywhere; }
.agent-settings .el-checkbox { margin: 8px 0 0; height: auto; white-space: normal; }
.agent-settings :deep(.el-checkbox__label) { white-space: normal; line-height: 1.45; }
.agent-plan-usage { display: flex; align-items: baseline; justify-content: space-between; gap: 4px; margin: 12px 0 0; padding-top: 8px; border-top: 1px solid #e9eee7; color: var(--agent-muted); font-size: 11px; }

.agent-activity, .agent-review { margin-top: 15px; }
.agent-activity { display: grid; gap: 8px; padding-top: 13px; padding-bottom: 13px; color: #54715e; }
.agent-progress { margin: 0; display: flex; align-items: center; gap: 7px; }
.agent-activity summary { width: fit-content; cursor: pointer; color: #527763; font-size: 12px; }
.agent-activity ul { margin: 7px 0 0; padding-left: 23px; max-height: 135px; overflow: auto; color: var(--agent-muted); font-size: 12px; line-height: 1.55; }
.agent-review-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.agent-review-header p { color: var(--agent-muted); margin: 6px 0; }
.agent-review-tools { display: flex; flex-wrap: wrap; justify-content: end; gap: 4px 10px; }
.agent-review-tools .el-button { margin: 0; }
.agent-task-quote { overflow-wrap: anywhere; white-space: pre-wrap; margin: 8px 0 13px; padding: 9px 12px; border-left: 3px solid #b6d4bc; border-radius: 0 6px 6px 0; background: var(--agent-tint); color: #425d4f; line-height: 1.55; }
.agent-review .el-alert { margin-bottom: 12px; }
.agent-report { margin-bottom: 12px; padding: 12px; color: #416b53; border-radius: 7px; background: #edf5ec; }
.agent-report-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.agent-report ul { margin: 8px 0 0; padding-left: 19px; line-height: 1.6; overflow-wrap: anywhere; }
.agent-review-empty { padding: 13px 3px; color: var(--agent-muted); }
.agent-book-list { display: grid; gap: 7px; }
.agent-book { border: 1px solid #e4ebe2; border-radius: 8px; overflow: clip; }
.agent-book-heading { width: 100%; min-height: 39px; display: flex; align-items: center; text-align: left; gap: 7px; background: #fafcf9; padding: 8px 11px; color: #446a55; border: 0; }
.agent-book-heading:hover { background: #f0f7ee; }
.agent-book-heading .el-icon { flex-shrink: 0; }
.agent-book-heading strong { flex: 1; color: var(--agent-ink); font-size: 13px; overflow-wrap: anywhere; min-width: 0; }
.agent-book-count, .agent-book-picked { flex: 0 0 auto; font-size: 11px; }
.agent-book-count { color: var(--agent-muted); }
.agent-book-picked { background: #e9f3eb; padding: 2px 5px; color: #3a7557; border-radius: 4px; }
.agent-book-body { padding: 0 10px 9px; }
.agent-book-source, .agent-book-warnings { font-size: 11px; line-height: 1.6; margin: 7px 0; overflow-wrap: anywhere; }
.agent-book-source { color: #587761; }
.agent-book-warnings { color: #87643b; padding-left: 16px; }
.agent-diff { border-top: 1px solid #e9eee7; margin-top: 5px; }
.agent-diff-row, .agent-diff-header { display: grid; grid-template-columns: minmax(100px, 23%) minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 9px; padding: 8px 5px; }
.agent-diff-header { color: var(--agent-muted); font-size: 11px; }
.agent-diff-row { border-top: 1px solid #eef2ec; }
.agent-diff-row .el-checkbox { margin: 0; height: auto; white-space: normal; align-items: flex-start; }
.agent-diff-row .el-checkbox :deep(.el-checkbox__label) { white-space: normal; line-height: 1.4; }
.agent-diff-before, .agent-diff-after { padding: 4px 7px; min-height: 24px; border-radius: 4px; overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.45; color: #567164; }
.agent-diff-before { background: #f5f6f3; }
.agent-diff-after { background: #f0f7ef; color: #365f49; }
.agent-diff-row.is-checked .agent-diff-after { background: #e6f2e8; }
.agent-review-footer { position: sticky; bottom: 0; background: #fff; display: flex; justify-content: space-between; align-items: center; gap: 9px; margin-top: 13px; padding: 12px 0 6px; border-top: 1px solid #e6ede4; color: var(--agent-muted); }

@media (max-width: 1110px) {
  .agent-layout { gap: 11px; }
  .agent-card { padding-left: 15px; padding-right: 15px; }
  .agent-review-header, .agent-review-footer { flex-wrap: wrap; }
}
@media (max-width: 870px) {
  .agent-layout { grid-template-columns: minmax(0, 1fr); }
  .agent-settings { grid-row: 1; }
}
@media (max-width: 560px) {
  .agent-model-toolbar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .agent-model-field:first-child { grid-column: 1 / -1; }
}
</style>
