<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, Collection, Filter, Loading, Search, VideoPause } from '@element-plus/icons-vue'
import { invoke } from '../api'
import { displayTitle, errorMessage } from '../types'
import type { Comic, ScrapeProvider } from '../types'

const props = defineProps<{ selected: Comic[]; filtered: Comic[]; disabled: boolean }>()
const emit = defineEmits<{ applied: []; 'task-state': [working: boolean] }>()

type Scope = 'selected' | 'filtered'
type Work = 'search' | 'preview' | 'apply'
type MatchStatus = 'matched' | 'ambiguous' | 'unmatched' | 'error'
interface Candidate {
  id: string
  provider: ScrapeProvider
  title: string
  authors: string[]
  year?: string
  publisher?: string
  language?: string
  pageCount?: number
  reasons: string[]
  url?: string
}
interface SearchBook {
  id: string
  title: string
  authorSummary?: string
  matchedAuthors?: string[]
  status: MatchStatus
  recommendedId?: string
  candidates: Candidate[]
  warnings: string[]
}
interface SearchResult { token: string | null; books: SearchBook[]; cancelled: boolean }
interface Choice { id: string; candidateId: string }
interface Change { field: string; before: unknown; after: unknown }
interface BookPlan { id: string; title: string; fields: Change[]; warnings: string[]; source?: string }
interface Plan { token: string | null; books: BookPlan[]; cancelled: boolean }
interface ApplyResult {
  updated: number
  skipped: number
  errors: { id: string; title: string; message: string }[]
  cancelled: boolean
}

const MAX_BOOKS = 24
const providers: { id: ScrapeProvider; label: string }[] = [
  { id: 'dlsite', label: 'DLsite' },
  { id: 'dmm', label: 'DMM / FANZA' },
  { id: 'ehentai', label: 'E-Hentai' },
  { id: 'nhentai', label: 'nhentai' },
]
const statusLabels: Record<MatchStatus, string> = {
  matched: '已匹配', ambiguous: '待选择', unmatched: '未找到', error: '搜索失败',
}
const fields: Record<string, string> = {
  title: '作品名称', original_title: '漫画原名', authors: '作者', pencillers: '绘画者',
  publisher: '出版方', translator: '译者', genre: '内容类型', tags: '题材与属性',
  original_works: '原作品', characters: '角色', language: '语言', summary: '简介',
  series: '系列名称', publication_format: '出版形态', work_group: '作品组',
  text_version: '文字版本', color: '色彩', age_rating: '年龄分级',
  release_type: '发行性质', release_platforms: '发布平台', release_date: '发行日期',
  web: '来源链接', year: '发行年', month: '发行月', day: '发行日',
}

const scope = ref<Scope>('selected')
const sites = ref<ScrapeProvider[]>(['dlsite', 'ehentai', 'nhentai'])
const nhKey = ref('')
const dmmKey = ref('')
const dmmAffiliate = ref('')
const work = ref<Work | ''>('')
const cancelling = ref(false)
const message = ref('')
const problem = ref('')
const submitted = ref('')
const results = ref<SearchResult | null>(null)
const candidateSection = ref<HTMLElement | null>(null)
const reviewSection = ref<HTMLElement | null>(null)
const candidateBooks = ref(new Set<string>())
const reviewBooks = ref(new Set<string>())
const picked = ref(new Map<string, string>())
const previewFor = ref('')
const plan = ref<Plan | null>(null)
const checked = ref(new Map<string, Set<string>>())
const report = ref<ApplyResult | null>(null)
let unsubscribe: (() => void) | undefined

const targets = computed(() => Array.from(new Map(props[scope.value].map(book => [book.id, book])).values()))
const targetById = computed(() => new Map(targets.value.map(book => [book.id, book])))
const searchKey = computed(() => JSON.stringify([scope.value, targets.value.map(book => book.id).sort(), [...sites.value].sort()]))
const busy = computed(() => props.disabled || !!work.value)
const hasDmm = computed(() => sites.value.includes('dmm'))
const hasNhentai = computed(() => sites.value.includes('nhentai'))
const dmmReady = computed(() => !!dmmKey.value.trim() && !!dmmAffiliate.value.trim())
const canSearch = computed(() => !busy.value && targets.value.length > 0 && targets.value.length <= MAX_BOOKS &&
  sites.value.length > 0 && (!hasDmm.value || dmmReady.value))
const isStale = computed(() => !!submitted.value && submitted.value !== searchKey.value)
const choices = computed<Choice[]>(() => (results.value?.books ?? []).flatMap(book => {
  const candidateId = picked.value.get(book.id)
  return candidateId && book.candidates.some(candidate => candidate.id === candidateId) ? [{ id: book.id, candidateId }] : []
}))
const counts = computed(() => {
  const tally = { matched: 0, ambiguous: 0, unmatched: 0, error: 0 }
  for (const book of results.value?.books ?? []) tally[book.status]++
  return tally
})
const choiceKey = computed(() => JSON.stringify(choices.value))
const canChoose = computed(() => !busy.value && !isStale.value && !!results.value?.token)
const needsDmmDetails = computed(() => choices.value.some(choice => results.value?.books.some(book =>
  book.id === choice.id && book.candidates.some(item => item.id === choice.candidateId && item.provider === 'dmm'))))
const canPreview = computed(() => canChoose.value && !!choices.value.length && (!needsDmmDetails.value || dmmReady.value))
const reviewEnabled = computed(() => canChoose.value && !report.value && !!plan.value?.token &&
  !plan.value.cancelled && previewFor.value === choiceKey.value)
const offers = computed(() => (plan.value?.books ?? []).reduce((sum, book) => sum + book.fields.length, 0))
const chosenFields = computed(() => (plan.value?.books ?? []).flatMap(book => {
  const fields = book.fields.filter(change => checked.value.get(book.id)?.has(change.field)).map(change => change.field)
  return fields.length ? [{ id: book.id, fields }] : []
}))
const selectedCount = computed(() => chosenFields.value.reduce((sum, book) => sum + book.fields.length, 0))

onMounted(() => {
  unsubscribe = window.comic?.onEvent((event, data) => {
    if (work.value && event === 'task-progress' && typeof data?.message === 'string') message.value = data.message
  })
})
onBeforeUnmount(() => unsubscribe?.())

function providerName(provider: ScrapeProvider) {
  return providers.find(site => site.id === provider)?.label ?? provider
}
function sourceName(source: string) {
  return providers.find(site => site.id === source)?.label ?? source
}
function authorsFor(book: SearchBook) {
  return book.authorSummary || book.matchedAuthors?.join('、') || targetById.value.get(book.id)?.metadata.authors.join('、') || ''
}
function localTitle(book: Comic) {
  return book.metadata.original_title.trim() || displayTitle(book.metadata)
}
function toggleExpanded(set: Set<string>, id: string) {
  if (set.has(id)) set.delete(id)
  else set.add(id)
}
function selectCandidate(book: SearchBook, candidateId: string) {
  if (!canChoose.value) return
  if (candidateId) picked.value.set(book.id, candidateId)
  else picked.value.delete(book.id)
}
function chooseField(id: string, field: string, value: unknown) {
  if (!reviewEnabled.value) return
  const found = checked.value.get(id) ?? new Set<string>()
  if (value === true) found.add(field)
  else found.delete(field)
  checked.value.set(id, found)
}
function blank(value: unknown): boolean {
  if (Array.isArray(value)) return !value.length || value.every(blank)
  if (value && typeof value === 'object') return Object.values(value).every(blank)
  return value == null || (typeof value === 'string' &&
    (!value.trim() || ['unknown', '未知', '待确认', '未确认'].includes(value.trim().toLowerCase())))
}
function format(value: unknown) {
  if (blank(value)) return '未填写'
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object' && 'year' in value) {
    const { year, month, day } = value as Record<string, unknown>
    return [year && String(year) + '年', month && String(month) + '月', day && String(day) + '日'].filter(Boolean).join('')
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
function selectEmpty() {
  if (!reviewEnabled.value) return
  checked.value.clear()
  for (const book of plan.value?.books ?? []) {
    const empty = book.fields.filter(change => blank(change.before)).map(change => change.field)
    if (empty.length) checked.value.set(book.id, new Set(empty))
  }
}
function credentials() {
  const apiKey = nhKey.value.trim()
  return {
    ...(hasNhentai.value && apiKey ? { nhentai: { apiKey } } : {}),
    ...(hasDmm.value && dmmReady.value ? { dmm: { apiKey: dmmKey.value.trim(), affiliateId: dmmAffiliate.value.trim() } } : {}),
  }
}
function safeText(value: string) {
  return [nhKey.value, dmmKey.value, dmmAffiliate.value]
    .map(secret => secret.trim()).filter(Boolean)
    .reduce((visible, secret) => visible.split(secret).join('•••'), value)
}
function start(kind: Work, status: string) {
  problem.value = ''
  message.value = status
  cancelling.value = false
  work.value = kind
  emit('task-state', true)
}
function finish() {
  work.value = ''
  cancelling.value = false
  emit('task-state', false)
}
async function search() {
  if (!canSearch.value) return
  const request = { ids: targets.value.map(book => book.id), providers: [...sites.value], credentials: credentials() }
  submitted.value = searchKey.value
  results.value = plan.value = report.value = null
  candidateBooks.value.clear()
  picked.value.clear()
  checked.value.clear()
  start('search', '正在按原名和作者搜索作品…')
  try {
    const response = await invoke<SearchResult>('agent_search', request)
    results.value = response
    for (const book of response.books) {
      if (book.status === 'matched' && book.recommendedId && book.candidates.some(item => item.id === book.recommendedId)) {
        picked.value.set(book.id, book.recommendedId)
      }
    }
    const uncertain = response.books.find(book => book.status === 'ambiguous' && book.candidates.length)
    if (uncertain) candidateBooks.value.add(uncertain.id)
    await nextTick()
    candidateSection.value?.scrollIntoView({ block: 'start' })
  } catch (error) { problem.value = safeText(errorMessage(error)) }
  finally { finish() }
}
async function preview() {
  if (!canPreview.value || !results.value?.token) return
  const request = { token: results.value.token, choices: choices.value, credentials: credentials() }
  previewFor.value = choiceKey.value
  plan.value = report.value = null
  checked.value.clear()
  reviewBooks.value.clear()
  start('preview', '正在读取已选候选的作品资料…')
  try {
    const response = await invoke<Plan>('agent_search_preview', request)
    plan.value = response
    const first = response.books.find(book => book.fields.length) || response.books.find(book => book.warnings.length)
    if (first) reviewBooks.value.add(first.id)
    await nextTick()
    reviewSection.value?.scrollIntoView({ block: 'start' })
  } catch (error) { problem.value = safeText(errorMessage(error)) }
  finally { finish() }
}
async function apply() {
  if (!reviewEnabled.value || !plan.value?.token || !chosenFields.value.length) return
  const request = { token: plan.value.token, books: chosenFields.value }
  start('apply', '正在保存勾选的资料字段…')
  try {
    report.value = await invoke<ApplyResult>('agent_apply', request)
    if (report.value.updated) emit('applied')
    await nextTick()
    reviewSection.value?.scrollIntoView({ block: 'start' })
  } catch (error) {
    if (plan.value) plan.value.token = null
    problem.value = safeText(errorMessage(error))
  } finally { finish() }
}
async function cancel() {
  if (!work.value || cancelling.value) return
  cancelling.value = true
  try { await invoke('cancel') }
  catch (error) {
    problem.value = safeText(errorMessage(error))
    cancelling.value = false
  }
}
</script>

<template>
  <section class="site-agent" aria-labelledby="site-agent-heading" data-testid="agent-search-workspace">
    <header class="site-heading">
      <span class="site-icon"><el-icon :size="22"><Search /></el-icon></span>
      <div><h1 id="site-agent-heading">站点搜索</h1><p>按原名和作者找作品，核对候选后批量填入草稿。</p></div>
    </header>
    <div class="site-scroll">
      <form class="site-card" @submit.prevent="search">
        <div class="site-setup">
          <section aria-labelledby="site-scope-heading">
            <h2 id="site-scope-heading">搜索范围</h2>
            <div class="site-scopes">
              <button type="button" data-testid="agent-search-scope-selected" :aria-pressed="scope === 'selected'" :disabled="busy" @click="scope = 'selected'">
                <el-icon><Collection /></el-icon><span>列表勾选</span><strong>{{ selected.length }} 本</strong>
              </button>
              <button type="button" data-testid="agent-search-scope-filtered" :aria-pressed="scope === 'filtered'" :disabled="busy" @click="scope = 'filtered'">
                <el-icon><Filter /></el-icon><span>筛选结果</span><strong>{{ filtered.length }} 本</strong>
              </button>
            </div>
            <p v-if="!targets.length" class="site-note">请从列表勾选作品，或切换到筛选结果。</p>
            <p v-else-if="targets.length > MAX_BOOKS" class="site-note warning">每批最多 {{ MAX_BOOKS }} 本，请缩小范围。</p>
            <details v-else class="site-targets">
              <summary>查看本次 {{ targets.length }} 本作品</summary>
              <ul><li v-for="book in targets" :key="book.id">{{ localTitle(book) }}<span v-if="book.metadata.authors.length"> · {{ book.metadata.authors.join('、') }}</span></li></ul>
            </details>
          </section>
          <section aria-labelledby="site-providers-heading">
            <h2 id="site-providers-heading">搜索站点</h2>
            <p class="site-note site-provider-note">搜索请求会向所选站点发送原名和作者。</p>
            <el-checkbox-group v-model="sites" :disabled="busy" class="site-providers">
              <el-checkbox v-for="site in providers" :key="site.id" :value="site.id" :data-testid="'agent-search-provider-' + site.id">{{ site.label }}</el-checkbox>
            </el-checkbox-group>
            <div v-if="hasDmm" class="site-credentials">
              <label for="agent-dmm-id">DMM API ID</label>
              <el-input id="agent-dmm-id" v-model="dmmKey" :disabled="busy" type="password" autocomplete="off" maxlength="512" data-testid="agent-search-dmm-key" />
              <label for="agent-dmm-affiliate">Affiliate ID</label>
              <el-input id="agent-dmm-affiliate" v-model="dmmAffiliate" :disabled="busy" type="password" autocomplete="off" maxlength="512" data-testid="agent-search-dmm-affiliate" />
              <p class="site-note">DMM 需要两项官方凭据。凭据仅在当前窗口用于搜索和详情读取。</p>
            </div>
            <details v-if="hasNhentai" class="site-targets site-nh">
              <summary>nhentai API Key（可选）</summary>
              <el-input v-model="nhKey" :disabled="busy" type="password" autocomplete="off" maxlength="512" aria-label="nhentai API Key" data-testid="agent-search-nh-key" />
            </details>
          </section>
        </div>
        <footer class="site-actions">
          <span class="site-note">每次最多 {{ MAX_BOOKS }} 本</span>
          <el-button type="primary" :icon="Search" :disabled="!canSearch" :loading="work === 'search'" native-type="submit" data-testid="agent-search-start">搜索候选</el-button>
        </footer>
      </form>

      <div v-if="work || problem" class="site-activity" data-testid="agent-search-activity" role="status">
        <div v-if="work" class="site-running"><el-icon class="is-loading"><Loading /></el-icon><span>{{ message }}</span><el-button link :icon="VideoPause" :loading="cancelling" :disabled="cancelling" data-testid="agent-search-cancel" @click="cancel">取消任务</el-button></div>
        <el-alert v-if="problem" :title="problem" type="error" show-icon :closable="false" />
      </div>

      <section v-if="results" ref="candidateSection" class="site-card site-results" aria-labelledby="site-matches-heading" data-testid="agent-search-results">
        <div class="site-summary">
          <h2 id="site-matches-heading">匹配结果</h2>
          <p class="site-note">已匹配 {{ counts.matched }} 本 · 待选择 {{ counts.ambiguous }} 本 · 未找到 {{ counts.unmatched }} 本<span v-if="counts.error"> · 搜索失败 {{ counts.error }} 本</span></p>
        </div>
        <el-alert v-if="results.cancelled" :title="results.token ? '搜索已取消，可以继续核对已完成的作品。' : '搜索已取消。请重新搜索后再读取候选。'" type="warning" :closable="false" show-icon />
        <el-alert v-else-if="isStale" title="搜索范围或站点已变化，请重新搜索。" type="warning" :closable="false" show-icon />
        <article v-for="book in results.books" :key="book.id" class="site-book" data-testid="agent-search-book" :data-book-id="book.id">
          <button type="button" class="site-book-heading" :aria-expanded="candidateBooks.has(book.id)" @click="toggleExpanded(candidateBooks, book.id)">
            <span class="site-caret" aria-hidden="true">{{ candidateBooks.has(book.id) ? '▾' : '▸' }}</span>
            <span class="site-heading-text"><strong>{{ book.title }}</strong><small v-if="authorsFor(book)">{{ authorsFor(book) }}</small></span>
            <span v-if="picked.get(book.id)" class="site-picked">已选</span>
            <span class="site-state" :class="book.status">{{ statusLabels[book.status] }}</span>
          </button>
          <div v-if="candidateBooks.has(book.id)" class="site-book-body">
            <ul v-if="book.warnings?.length" class="site-warnings"><li v-for="(warning, index) in book.warnings" :key="index">{{ safeText(warning) }}</li></ul>
            <div v-if="book.candidates.length" class="site-candidate-group" role="radiogroup" :aria-label="book.title + ' 的候选'">
              <label v-for="item in book.candidates" :key="item.id" class="site-candidate" :class="{ chosen: picked.get(book.id) === item.id }" data-testid="agent-search-candidate" :data-candidate-id="item.id">
                <input type="radio" :name="'site-candidate-' + book.id" :value="item.id" :checked="picked.get(book.id) === item.id" :disabled="!canChoose" @change="selectCandidate(book, item.id)">
                <span>
                  <strong>{{ providerName(item.provider) }} · {{ item.title }}</strong>
                  <small v-if="item.authors?.length || item.publisher || item.year || item.language || item.pageCount">
                    {{ [item.authors?.join('、'), item.publisher, item.year, item.language, item.pageCount ? item.pageCount + ' 页' : ''].filter(Boolean).join(' · ') }}
                  </small>
                  <small v-if="item.reasons?.length" class="site-reasons">{{ item.reasons.join(' · ') }}</small>
                  <small v-if="item.url" class="site-candidate-url">{{ item.url }}</small>
                </span>
              </label>
              <label class="site-candidate site-skip"><input type="radio" :name="'site-candidate-' + book.id" :checked="!picked.get(book.id)" :disabled="!canChoose" data-testid="agent-search-skip" @change="selectCandidate(book, '')">跳过此作品</label>
            </div>
            <p v-else-if="!book.warnings?.length" class="site-note">这本作品没有可选择的候选。</p>
          </div>
        </article>
        <footer class="site-actions site-sticky">
          <span class="site-note">{{ needsDmmDetails && !dmmReady ? '请填写 DMM 凭据后读取详情。' : '已选 ' + choices.length + ' 本，未选择的作品会跳过。' }}</span>
          <el-button type="primary" :disabled="!canPreview" :loading="work === 'preview'" data-testid="agent-search-preview" @click="preview">读取详情并预览字段</el-button>
        </footer>
      </section>

      <section v-if="plan" ref="reviewSection" class="site-card site-results" aria-labelledby="site-drafts-heading" data-testid="agent-search-review">
        <div class="site-summary site-review-summary">
          <div><h2 id="site-drafts-heading">草稿预览</h2><p class="site-note">{{ plan.books.length }} 本 · {{ offers }} 项候选字段</p></div>
          <div class="site-review-tools">
            <el-button link :disabled="!reviewEnabled" data-testid="agent-search-select-empty" @click="selectEmpty">勾选空白字段</el-button>
            <el-button link :disabled="!reviewEnabled || !selectedCount" data-testid="agent-search-clear-fields" @click="checked.clear()">取消勾选</el-button>
          </div>
        </div>
        <el-alert v-if="plan.cancelled" title="详情读取已取消。请重新读取后再保存草稿。" type="warning" :closable="false" show-icon />
        <el-alert v-else-if="!report && (isStale || previewFor !== choiceKey)" title="已选候选或搜索范围有变化，请重新读取详情。" type="warning" :closable="false" show-icon />
        <div v-if="report" class="site-report" role="status" data-testid="agent-search-report">
          <strong>已保存 {{ report.updated }} 本草稿</strong><span v-if="report.skipped"> · 跳过 {{ report.skipped }} 本</span><span v-if="report.cancelled"> · 保存已取消</span>
          <ul v-if="report.errors?.length" class="site-warnings"><li v-for="(item, index) in report.errors" :key="index">{{ item.title }} · {{ safeText(item.message) }}</li></ul>
        </div>
        <article v-for="book in plan.books" :key="book.id" class="site-book" data-testid="agent-search-draft" :data-book-id="book.id">
          <button type="button" class="site-book-heading" :aria-expanded="reviewBooks.has(book.id)" @click="toggleExpanded(reviewBooks, book.id)">
            <span class="site-caret" aria-hidden="true">{{ reviewBooks.has(book.id) ? '▾' : '▸' }}</span>
            <span class="site-heading-text"><strong>{{ book.title }}</strong><small v-if="book.source">{{ sourceName(book.source) }}</small></span>
            <span v-if="checked.get(book.id)?.size" class="site-picked">勾选 {{ checked.get(book.id)?.size }} 项</span>
            <span class="site-note">{{ book.fields.length ? book.fields.length + ' 项候选' : '无可填字段' }}</span>
          </button>
          <div v-if="reviewBooks.has(book.id)" class="site-book-body">
            <ul v-if="book.warnings?.length" class="site-warnings"><li v-for="(warning, index) in book.warnings" :key="index">{{ safeText(warning) }}</li></ul>
            <div v-if="book.fields.length" class="site-diff" role="table" :aria-label="book.title + ' 的字段对照'">
              <div class="site-diff-line site-diff-header" role="row"><strong role="columnheader">选择字段</strong><strong role="columnheader">当前值</strong><strong role="columnheader">候选值</strong></div>
              <div v-for="change in book.fields" :key="change.field" class="site-diff-line" role="row" data-testid="agent-search-field" :data-field="change.field">
                <div role="cell"><el-checkbox :model-value="checked.get(book.id)?.has(change.field) || false" :disabled="!reviewEnabled" @update:model-value="chooseField(book.id, change.field, $event)">{{ fields[change.field] || change.field }}</el-checkbox></div>
                <div role="cell" class="site-current">{{ format(change.before) }}</div>
                <div role="cell" class="site-new">{{ format(change.after) }}</div>
              </div>
            </div>
          </div>
        </article>
        <footer v-if="!report" class="site-actions site-sticky">
          <span class="site-note">已选 {{ chosenFields.length }} 本 · {{ selectedCount }} 项资料 · 保存后待审核</span>
          <el-button type="primary" :icon="Check" :disabled="!reviewEnabled || !selectedCount" :loading="work === 'apply'" data-testid="agent-search-apply" @click="apply">保存勾选项到草稿</el-button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.site-agent {
  --site-ink: #243d33;
  --site-muted: #617b6a;
  --site-line: #dee9de;
  --site-tint: #f0f6ee;
  display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0;
}
.site-heading { display: flex; align-items: center; gap: 13px; margin-bottom: 15px; }
.site-icon { width: 44px; height: 44px; display: grid; place-items: center; background: #e6f0e4; color: #398064; border: 1px solid #dce9da; border-radius: 11px; }
.site-heading h1 { color: var(--site-ink); margin: 0; }
.site-heading p { margin: 4px 0 0; color: var(--site-muted); }
.site-scroll { min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 0 2px 16px 0; scrollbar-width: thin; scrollbar-color: #cddcc9 transparent; }
.site-card { padding: 16px 18px 8px; border: 1px solid var(--site-line); border-radius: 12px; background: #fff; }
.site-card h2 { font-size: 16px; color: var(--site-ink); margin: 0; line-height: 1.45; }
.site-setup { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 20px; }
.site-setup > section { min-width: 0; }
.site-scopes { margin-top: 10px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.site-scopes button {
  min-width: 0; min-height: 49px; padding: 7px 9px; display: grid; align-items: center;
  grid-template-columns: auto 1fr; gap: 3px 6px; text-align: left;
  border: 1px solid var(--site-line); border-radius: 7px; background: #fbfdf9; color: var(--site-muted);
}
.site-scopes strong { grid-column: 2; color: var(--site-ink); white-space: nowrap; font-size: 13px; }
.site-scopes button[aria-pressed='true'] { border-color: #a7c8af; background: #edf5ec; color: #2d654c; }
.site-scopes button:disabled { opacity: .65; }
.site-note { margin: 6px 0; color: var(--site-muted); line-height: 1.5; font-size: 11px; }
.site-note.warning { color: #896333; }
.site-targets { color: var(--site-muted); font-size: 11px; margin-top: 9px; }
.site-targets summary { cursor: pointer; width: fit-content; color: #47765d; }
.site-targets ul { padding-left: 20px; margin: 6px 0 0; max-height: 113px; overflow: auto; line-height: 1.6; overflow-wrap: anywhere; }
.site-targets .el-input { margin-top: 5px; width: 100%; }
.site-providers { display: flex; flex-wrap: wrap; gap: 3px 14px; }
.site-providers :deep(.el-checkbox) { margin: 0; height: 26px; }
.site-credentials { margin-top: 8px; display: grid; grid-template-columns: max-content minmax(0, 1fr); align-items: center; gap: 6px 8px; color: var(--site-muted); font-size: 11px; }
.site-credentials p { grid-column: 1 / -1; }
.site-actions { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--site-line); }
.site-activity, .site-results { margin-top: 12px; }
.site-activity { display: grid; gap: 6px; }
.site-running { min-height: 32px; padding: 4px 9px; display: flex; gap: 7px; align-items: center; background: var(--site-tint); color: #476954; border-radius: 7px; }
.site-running span { flex: 1; }
.site-summary { margin-bottom: 10px; }
.site-review-summary { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 8px; }
.site-review-tools { display: flex; flex-wrap: wrap; gap: 6px; }
.site-review-tools .el-button { margin: 0; }
.site-results .el-alert { margin: 9px 0; }
.site-book { margin-top: 7px; border: 1px solid var(--site-line); border-radius: 8px; overflow: hidden; }
.site-book-heading { width: 100%; display: flex; gap: 8px; align-items: center; padding: 8px 10px; background: #fafcf9; border: 0; text-align: left; color: #466d55; }
.site-book-heading:hover { background: #f0f7ee; }
.site-heading-text { flex: 1; min-width: 0; color: var(--site-ink); overflow-wrap: anywhere; }
.site-heading-text small { display: block; margin-top: 2px; color: var(--site-muted); }
.site-caret { flex: 0 0 auto; }
.site-state, .site-picked { flex: 0 0 auto; padding: 3px 6px; border-radius: 4px; font-size: 10px; background: #f1f2ef; color: #607565; }
.site-state.ambiguous { background: #fbf2e2; color: #845f24; }
.site-state.error { background: #f9e9e8; color: #8c4e4a; }
.site-state.matched, .site-picked { background: #e8f3e9; color: #336e51; }
.site-book-body { padding: 3px 10px 9px; }
.site-warnings { margin: 5px 0; padding-left: 16px; color: #86663d; font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.site-candidate-group { display: grid; gap: 5px; padding-top: 5px; }
.site-candidate { display: flex; align-items: flex-start; gap: 7px; padding: 6px 8px; border: 1px solid var(--site-line); border-radius: 6px; color: #355946; background: #fff; cursor: pointer; }
.site-candidate.chosen { background: var(--site-tint); border-color: #b9d3bd; }
.site-candidate input { accent-color: #397b5a; flex-shrink: 0; margin-top: 3px; }
.site-candidate span { min-width: 0; overflow-wrap: anywhere; }
.site-candidate span small { display: block; margin-top: 3px; color: var(--site-muted); font-size: 10px; }
.site-candidate span .site-reasons { color: #42705a; }
.site-candidate span .site-candidate-url { user-select: text; }
.site-skip { border-color: transparent; width: fit-content; padding-top: 3px; padding-bottom: 3px; }
.site-sticky { position: sticky; bottom: 0; padding-bottom: 4px; background: #fff; }
.site-report { padding: 9px 10px; border-radius: 6px; background: #edf5ec; color: #356d52; }
.site-diff { margin-top: 3px; }
.site-diff-line { display: grid; grid-template-columns: minmax(92px, 23%) minmax(0, 1fr) minmax(0, 1fr); gap: 7px; align-items: start; border-top: 1px solid #edf2eb; padding: 6px 3px; }
.site-diff-header { color: var(--site-muted); font-size: 11px; }
.site-diff-line .el-checkbox { margin: 0; height: auto; }
.site-diff-line :deep(.el-checkbox__label) { white-space: normal; }
.site-diff-line .site-current, .site-diff-line .site-new { white-space: pre-wrap; overflow-wrap: anywhere; padding: 4px 7px; border-radius: 4px; background: #f5f6f3; color: #597165; }
.site-diff-line .site-new { background: #eef6ee; color: #385f49; }
@media (max-width: 850px) { .site-setup { grid-template-columns: 1fr; gap: 12px; } }
</style>
