<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { Check, MagicStick, Search } from '@element-plus/icons-vue'
import type { Comic, Metadata, ModelAssistPreview, ModelConfiguration, ScrapeApplication, ScrapeFieldKey, ScrapeFields, ScrapePreview, ScrapeProvider } from '../types'
import { ageRatings, clone, comicGenres, displayTitle, errorMessage, releaseTypes, scrapeFieldKeys } from '../types'
import { invoke } from '../api'

const props = defineProps<{ modelValue: boolean; comic: Comic | null; metadata: Metadata | null; disabled: boolean; modelConfiguration: ModelConfiguration }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'apply', value: ScrapeApplication): void
  (e: 'model-settings'): void
}>()
const url = ref('')
const apiKey = ref('')
const affiliateId = ref('')
const reading = ref(false)
const error = ref('')
const sitePreview = ref<ScrapePreview | null>(null)
const siteBaseline = ref<Metadata | null>(null)
const modelPreview = ref<ModelAssistPreview | null>(null)
const modelBaseline = ref<Metadata | null>(null)
const previewMode = ref<'site' | 'model'>('site')
const preview = computed(() => previewMode.value === 'site' ? sitePreview.value : modelPreview.value)
const baseline = computed(() => previewMode.value === 'site' ? siteBaseline.value : modelBaseline.value)
const sourceUrl = computed(() => previewMode.value === 'site' ? sitePreview.value?.sourceUrl || '' : '')
const profileId = ref('')
const includeSite = ref(true)
const assisting = ref(false)
const working = computed(() => reading.value || assisting.value)
const selectedProfile = computed(() => props.modelConfiguration.profiles.find(profile => profile.id === profileId.value))
const selectedKeys = ref<string[]>([])
const sourceSelected = ref(false)
const overwriteConfirmed = ref(false)
let request = 0
let targetId = ''

const providerNames: Record<ScrapeProvider, string> = { ehentai: 'E-Hentai', nhentai: 'nhentai', dlsite: 'DLsite', dmm: 'DMM / FANZA' }
const providerHosts: Record<ScrapeProvider, string[]> = {
  ehentai: ['e-hentai.org', 'www.e-hentai.org'],
  nhentai: ['nhentai.net', 'www.nhentai.net'],
  dlsite: ['dlsite.com', 'www.dlsite.com'],
  dmm: ['dmm.com', 'dmm.co.jp', 'www.dmm.com', 'www.dmm.co.jp', 'book.dmm.com', 'book.dmm.co.jp'],
}
function providerForUrl(value: string): ScrapeProvider | null {
  try {
    const parsed = new URL(value.trim())
    if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) return null
    return (Object.keys(providerHosts) as ScrapeProvider[]).find(provider => providerHosts[provider].includes(parsed.hostname)) || null
  } catch { return null }
}
function sourceKey(value: string) {
  try {
    const parsed = new URL(value.trim())
    parsed.hash = ''
    parsed.hostname = parsed.hostname.replace(/^www\./, '')
    parsed.pathname = parsed.pathname.replace(/\/+$/, '') || '/'
    return parsed.href
  } catch { return value.trim() }
}
const provider = computed(() => providerForUrl(url.value))
const existingLinks = computed(() => [...new Set((props.metadata?.web || []).map(value => value.trim()).filter(value => providerForUrl(value)))])
const canRead = computed(() => !!provider.value && !working.value && !props.disabled && (provider.value !== 'dmm' || (!!apiKey.value.trim() && !!affiliateId.value.trim())))
const stale = computed(() => !!preview.value && !!baseline.value && JSON.stringify(props.metadata) !== JSON.stringify(baseline.value))
const sourceAlreadyPresent = computed(() => !!sourceUrl.value && (props.metadata?.web || []).some(value => sourceKey(value) === sourceKey(sourceUrl.value)))
const sourceWillApply = computed(() => !!sourceUrl.value && sourceSelected.value && !sourceAlreadyPresent.value)
const blocked = computed(() => props.disabled || working.value || stale.value)
const canAssist = computed(() => !!selectedProfile.value && !!props.comic && !!props.metadata && !working.value && !props.disabled)

const fieldLabels: Partial<Record<ScrapeFieldKey, string>> = {
  title: '作品名称', authors: '作者', pencillers: '独立绘画者', publisher: '出版方 / 发行社团', translator: '译者 / 汉化组',
  original_works: '原作', characters: '角色', genre: '内容类型', tags: '题材与属性标签',
  language: '当前版本语言', release_type: '发行性质', release_platforms: '发布平台', summary: '作品简介', age_rating: '年龄分级',
}
const arrayFields = new Set<ScrapeFieldKey>(['authors', 'pencillers', 'original_works', 'characters', 'tags', 'release_platforms'])
const dateFields = new Set<ScrapeFieldKey>(['year', 'month', 'day'])
const unknownValues = new Set(['', 'unknown', 'n/a', 'none', 'null', '未知', '不明', '未确认', '待确认'])
function blank(value: unknown): boolean {
  return Array.isArray(value) ? !value.length || value.every(blank) : value == null || (typeof value === 'string' && unknownValues.has(value.trim().toLowerCase()))
}
function valueText(key: ScrapeFieldKey, value: unknown) {
  if (blank(value)) return '未填写 / 未确认'
  if (key === 'age_rating') return ageRatings.find(item => item.value === value)?.label || String(value)
  return Array.isArray(value) ? value.join('、') : String(value)
}
function dateText(value: Pick<Metadata, 'year' | 'month' | 'day'>) {
  if ([value.year, value.month, value.day].every(blank)) return '未填写 / 未确认'
  return `${blank(value.year) ? '年份未知' : `${value.year} 年`} · ${blank(value.month) ? '月份未知' : `${value.month} 月`} · ${blank(value.day) ? '日期未知' : `${value.day} 日`}`
}
interface FieldRow { key: string; label: string; before: string; after: string; patch: ScrapeFields; same: boolean; overwrite: boolean; note: string }
const rows = computed<FieldRow[]>(() => {
  const current = baseline.value
  const fields = preview.value?.fields
  if (!current || !fields) return []
  const result: FieldRow[] = []
  for (const key of scrapeFieldKeys) {
    if (dateFields.has(key) || !Object.prototype.hasOwnProperty.call(fields, key)) continue
    const candidate = fields[key]
    if (blank(candidate)) continue
    result.push({ key, label: fieldLabels[key] || key, before: valueText(key, current[key]), after: valueText(key, candidate),
      patch: { [key]: clone(candidate) }, same: JSON.stringify(current[key]) === JSON.stringify(candidate), overwrite: !blank(current[key]),
      note: arrayFields.has(key) ? '应用时替换本字段的全部条目' : '' })
  }
  if (fields.year) {
    const candidate = { year: fields.year, month: fields.month || '', day: fields.day || '' }
    const original = { year: current.year, month: current.month, day: current.day }
    result.push({ key: 'release_date', label: '发行日期', before: dateText(original), after: dateText(candidate), patch: candidate,
      same: JSON.stringify(original) === JSON.stringify(candidate), overwrite: ![current.year, current.month, current.day].every(blank),
      note: '年、月、日整组应用；来源未提供的月或日将留空' })
  }
  return result
})
const selectedRows = computed(() => rows.value.filter(row => selectedKeys.value.includes(row.key) && !row.same))
const overwriteRows = computed(() => selectedRows.value.filter(row => row.overwrite))
const applyCount = computed(() => selectedRows.value.length + Number(sourceWillApply.value))
const canApply = computed(() => !!preview.value && !!baseline.value && !!applyCount.value && !blocked.value && (!overwriteRows.value.length || overwriteConfirmed.value))

function redact(value: string, secrets: string[]) {
  return secrets.reduce((text, secret) => secret ? text.replaceAll(secret, '[已隐藏]') : text, value)
}
function normalizedDate(fields: ScrapeFields): Pick<Metadata, 'year' | 'month' | 'day'> | null {
  const year = typeof fields.year === 'string' ? fields.year.trim() : ''
  const month = typeof fields.month === 'string' && !blank(fields.month) ? fields.month.trim() : ''
  const day = typeof fields.day === 'string' && !blank(fields.day) ? fields.day.trim() : ''
  if (!/^\d{1,4}$/.test(year) || Number(year) < 1 || (month && (!/^\d{1,2}$/.test(month) || Number(month) < 1 || Number(month) > 12)) || (day && (!month || !/^\d{1,2}$/.test(day) || Number(day) < 1 || Number(day) > 31))) return null
  if (month && day) {
    const date = new Date(0)
    date.setUTCFullYear(Number(year), Number(month) - 1, Number(day))
    if (date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day)) return null
  }
  return { year: String(Number(year)), month: month ? String(Number(month)) : '', day: day ? String(Number(day)) : '' }
}
function sanitizeFields(raw: ScrapeFields, notices: string[], secrets: string[] = []) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('候选结果无法识别，请重试。')
  const fields: ScrapeFields = {}
  const warnings = Array.isArray(notices) ? notices.filter(value => typeof value === 'string').map(value => redact(value, secrets)) : []
  for (const key of scrapeFieldKeys) {
    if (dateFields.has(key)) continue
    const value = raw[key]
    const normalized = arrayFields.has(key)
      ? Array.isArray(value) ? [...new Set(value.filter(item => typeof item === 'string').map(item => item.trim()).filter(item => !blank(item)))] : undefined
      : typeof value === 'string' ? value.trim() : undefined
    if (normalized === undefined || blank(normalized)) continue
    if (key === 'genre' && !comicGenres.includes(normalized as string)) { warnings.push('来源的内容类型不属于当前规范，未提供该字段；可在资料面板中核对。'); continue }
    if (key === 'age_rating' && !ageRatings.some(rating => rating.value === normalized && rating.value !== 'Unknown')) continue
    if (key === 'release_type' && !releaseTypes.includes(normalized as string)) continue
    if (key === 'original_works' && Array.isArray(normalized) && normalized.length > 1) { warnings.push('来源列出多个原作，请在资料面板中人工核对。'); continue }
    ;(fields as Record<string, unknown>)[key] = normalized
  }
  if (['year', 'month', 'day'].some(key => !blank((raw as Record<string, unknown>)[key]))) {
    const date = normalizedDate(raw)
    if (date) Object.assign(fields, date)
    else warnings.push('来源的发行日期不完整或无效，未提供日期应用，以免混合已有年月日。')
  }
  return { fields, warnings: [...new Set(warnings)] }
}
function sanitize(result: ScrapePreview, secrets: string[]): ScrapePreview {
  if (!result || !providerHosts[result.provider] || providerForUrl(result.sourceUrl) !== result.provider) throw new Error('读取结果缺少有效来源，请重新读取。')
  return { provider: result.provider, sourceUrl: result.sourceUrl, ...sanitizeFields(result.fields, result.warnings, secrets) }
}
function resetSelection() {
  selectedKeys.value = []
  sourceSelected.value = false
  overwriteConfirmed.value = false
}
function selectPreview(mode: typeof previewMode.value) {
  previewMode.value = mode
  resetSelection()
}
function clearResult() {
  request++
  reading.value = false
  assisting.value = false
  sitePreview.value = null
  siteBaseline.value = null
  modelPreview.value = null
  modelBaseline.value = null
  selectPreview('site')
  includeSite.value = true
  error.value = ''
}
function close() { emit('update:modelValue', false) }
function compareAgain() {
  if (!props.metadata) return
  if (previewMode.value === 'site') siteBaseline.value = clone(props.metadata)
  else modelBaseline.value = clone(props.metadata)
  resetSelection()
}
function selectEmptyFields() { selectedKeys.value = rows.value.filter(row => !row.same && !row.overwrite).map(row => row.key) }
function clearSelection() { selectedKeys.value = []; sourceSelected.value = false }
async function read() {
  if (!canRead.value || !props.metadata || props.comic?.id !== targetId) return
  clearResult()
  const currentRequest = ++request
  const currentProvider = provider.value!
  const credentials = currentProvider === 'nhentai' || currentProvider === 'dmm' ? apiKey.value.trim() : ''
  const affiliate = currentProvider === 'dmm' ? affiliateId.value.trim() : ''
  const secrets = [credentials, affiliate].filter(Boolean)
  const params = { url: url.value.trim(), ...(credentials ? { apiKey: credentials } : {}), ...(affiliate ? { affiliateId: affiliate } : {}) }
  reading.value = true
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const result = await Promise.race([
      invoke<ScrapePreview>('scrape_preview', params),
      new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error('读取超时，请稍后重试。')), 45000) }),
    ])
    if (currentRequest !== request || !props.modelValue || props.comic?.id !== targetId || !props.metadata) return
    sitePreview.value = sanitize(result, secrets)
    siteBaseline.value = clone(props.metadata)
  } catch (reason) {
    if (currentRequest === request && props.modelValue) error.value = redact(errorMessage(reason), secrets)
  } finally {
    clearTimeout(timer)
    if (currentRequest === request) reading.value = false
  }
}
function modelEvidence(source: ScrapeFields & Partial<Pick<Metadata, 'original_title' | 'chinese_title'>>) {
  const excluded = new Set<ScrapeFieldKey>(['summary', 'age_rating', 'original_works', 'characters'])
  const keys = [...scrapeFieldKeys.filter(key => !excluded.has(key)), 'original_title', 'chinese_title'] as const
  return Object.fromEntries(keys.filter(key => !blank(source[key])).map(key => [key, source[key]]))
}
async function assist() {
  const profile = selectedProfile.value
  if (!canAssist.value || !props.metadata || !profile || props.comic?.id !== targetId) return
  const currentRequest = ++request
  const draft = clone(props.metadata)
  const metadata = modelEvidence(draft)
  const siteFields = includeSite.value && sitePreview.value ? modelEvidence(sitePreview.value.fields) : undefined
  assisting.value = true
  error.value = ''
  try {
    const result = await invoke<ModelAssistPreview>('model_assist_preview', {
      id: targetId, profile_id: profile.id, metadata, ...(siteFields ? { siteFields } : {}),
    })
    if (request !== currentRequest || !props.modelValue || props.comic?.id !== targetId) return
    modelPreview.value = { ...sanitizeFields(result.fields, result.warnings), model_name: result.model_name || profile.name }
    modelBaseline.value = draft
    selectPreview('model')
  } catch (reason) {
    if (request === currentRequest && props.modelValue) error.value = errorMessage(reason)
  } finally {
    if (request === currentRequest) assisting.value = false
  }
}
function apply() {
  if (!canApply.value || !baseline.value || props.comic?.id !== targetId) return
  const patch = Object.assign({}, ...selectedRows.value.map(row => row.patch)) as ScrapeFields
  emit('apply', { id: targetId, baseline: clone(baseline.value), patch: clone(patch), ...(sourceWillApply.value ? { sourceUrl: sourceUrl.value } : {}) })
}
watch(url, () => clearResult(), { flush: 'sync' })
function ensureProfile() {
  const config = props.modelConfiguration
  if (!config.profiles.some(profile => profile.id === profileId.value)) {
    profileId.value = config.profiles.find(profile => profile.id === config.default_id)?.id || config.profiles[0]?.id || ''
  }
}
watch(() => props.modelConfiguration, ensureProfile, { deep: true })
watch(provider, () => { apiKey.value = ''; affiliateId.value = '' }, { flush: 'sync' })
watch(selectedKeys, () => { overwriteConfirmed.value = false }, { deep: true, flush: 'sync' })
watch(() => props.modelValue, visible => {
  clearResult()
  apiKey.value = ''; affiliateId.value = ''
  targetId = visible ? props.comic?.id || '' : ''
  profileId.value = ''
  if (visible) ensureProfile()
  url.value = visible ? existingLinks.value[0] || '' : ''
}, { flush: 'sync', immediate: true })
watch(() => props.comic?.id, id => { if (props.modelValue && id !== targetId) close() }, { flush: 'sync' })
onBeforeUnmount(() => { clearResult(); apiKey.value = ''; affiliateId.value = ''; url.value = '' })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="读取作品资料" width="min(980px, 96vw)" top="4vh" class="metadata-scrape-dialog" :close-on-click-modal="false" destroy-on-close>
    <div class="scrape-intro"><strong :title="metadata ? displayTitle(metadata) : ''">{{ metadata ? displayTitle(metadata) : '当前作品' }}</strong><p>可从网站读取或用模型整理资料。勾选核对过的字段，应用到当前草稿。</p></div>
    <el-form label-position="top" class="scrape-form" @submit.prevent="read">
      <el-form-item label="作品详情页链接">
        <div class="scrape-url-row"><el-select v-if="existingLinks.length" data-testid="scrape-existing-url" :model-value="existingLinks.includes(url) ? url : ''" @update:model-value="url = String($event)" :disabled="working" placeholder="选择已有链接"><el-option v-for="link in existingLinks" :key="link" :value="link" :label="link" /></el-select><el-input data-testid="scrape-url" v-model="url" :disabled="working" clearable autocomplete="off" placeholder="粘贴 E-Hentai、nhentai、DLsite 或 DMM / FANZA 的作品详情页链接" @keyup.enter="read" /><el-button data-testid="scrape-read" type="primary" :icon="Search" :loading="reading" :disabled="!canRead" @click="read">{{ reading ? '读取中' : '读取资料' }}</el-button></div>
        <p class="scrape-help">{{ provider ? `当前来源：${providerNames[provider]}` : url.trim() ? '暂不支持这个地址；请填写上述站点的 http(s) 作品详情页链接。' : '填入已确认的作品链接，即可获取网站资料。' }}</p>
      </el-form-item>
      <div v-if="provider === 'nhentai' || provider === 'dmm'" class="scrape-credentials">
        <el-form-item :label="provider === 'dmm' ? 'API ID' : 'API Key（可选）'" :required="provider === 'dmm'"><el-input data-testid="scrape-api-key" v-model="apiKey" type="password" autocomplete="new-password" :disabled="working" :placeholder="provider === 'dmm' ? '输入 DMM API ID' : '公开详情可匿名读取，受访问限制时再填写'" /></el-form-item>
        <el-form-item v-if="provider === 'dmm'" label="Affiliate ID" required><el-input data-testid="scrape-affiliate-id" v-model="affiliateId" type="password" autocomplete="new-password" :disabled="working" placeholder="输入 DMM Affiliate ID" /></el-form-item>
        <p class="scrape-help">凭据仅用于当前弹窗的读取，关闭后清除；不保存在本机配置或漫画资料中。</p>
      </div>
    </el-form>
    <section class="scrape-model" aria-label="模型辅助">
      <div class="scrape-model-header">
        <strong><el-icon><MagicStick /></el-icon>模型辅助</strong>
        <el-button data-testid="scrape-model-settings" link :disabled="working" @click="emit('model-settings')">模型管理</el-button>
      </div>
      <template v-if="modelConfiguration.profiles.length">
        <div class="scrape-model-controls">
          <el-select data-testid="scrape-model-profile" v-model="profileId" :disabled="working" aria-label="辅助模型" placeholder="选择模型">
            <el-option v-for="profile in modelConfiguration.profiles" :key="profile.id" :label="profile.name" :value="profile.id" />
          </el-select>
          <el-checkbox v-if="sitePreview && Object.keys(sitePreview.fields).length" data-testid="scrape-model-use-site" v-model="includeSite" :disabled="working">加入网站资料</el-checkbox>
          <el-button data-testid="scrape-model-run" :icon="MagicStick" :loading="assisting" :disabled="!canAssist" @click="assist">生成候选</el-button>
        </div>
        <p v-if="selectedProfile" class="scrape-help">生成候选时，草稿中的标题、作者、分类等文字字段，以及已勾选的网站资料会发往 <b>{{ selectedProfile.name }}</b> 的服务（{{ selectedProfile.base_url }}）。请核对模型建议。</p>
      </template>
      <p v-else class="scrape-help">在模型管理中添加服务，即可根据作品名称、草稿或网站资料生成候选。</p>
    </section>
    <el-alert v-if="disabled" title="当前作品暂不可编辑，请先处理任务、连接状态或远端资料冲突。" type="warning" :closable="false" show-icon class="scrape-alert" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="scrape-alert" data-testid="scrape-error" />
    <p v-if="working" class="scrape-reading" role="status">{{ reading ? '正在读取网站资料…' : '正在生成模型候选…' }}</p>
    <template v-if="preview">
      <div v-if="sitePreview && modelPreview" class="scrape-preview-tabs" aria-label="候选来源">
        <el-button data-testid="scrape-preview-site" :type="previewMode === 'site' ? 'primary' : 'default'" size="small"
          :aria-pressed="previewMode === 'site'" :disabled="working" @click="selectPreview('site')">网站资料</el-button>
        <el-button data-testid="scrape-preview-model" :type="previewMode === 'model' ? 'primary' : 'default'" size="small"
          :aria-pressed="previewMode === 'model'" :disabled="working" @click="selectPreview('model')">模型候选</el-button>
      </div>
      <div v-if="previewMode === 'site' && sitePreview" class="scrape-result-source" data-testid="scrape-source"><span>{{ providerNames[sitePreview.provider] }}</span><span :title="sitePreview.sourceUrl">{{ sitePreview.sourceUrl }}</span></div>
      <div v-else-if="modelPreview" class="scrape-result-source" data-testid="scrape-model-result"><span>模型候选</span><span :title="modelPreview.model_name">{{ modelPreview.model_name }}</span></div>
      <div v-if="preview.warnings.length" class="scrape-warnings" data-testid="scrape-warnings"><p v-for="(warning, index) in preview.warnings" :key="index">{{ warning }}</p></div>
      <div v-if="stale" class="scrape-stale" data-testid="scrape-stale"><span>当前草稿已变化，请更新对照后重新勾选。</span><el-button size="small" @click="compareAgain">更新对照</el-button></div>
      <div class="scrape-selection-tools"><span>{{ rows.filter(row => !row.same).length }} 项资料可核对</span><div><el-button data-testid="scrape-select-empty" link :disabled="blocked" @click="selectEmptyFields">仅勾选空字段</el-button><el-button data-testid="scrape-clear-selection" link :disabled="blocked" @click="clearSelection">取消勾选</el-button></div></div>
      <div class="scrape-comparison" role="table" aria-label="读取资料与当前草稿对照" data-testid="scrape-comparison">
        <div class="scrape-comparison-head" role="row"><span role="columnheader">应用字段</span><span role="columnheader">当前草稿</span><span role="columnheader">{{ previewMode === 'site' ? '读取结果' : '模型候选' }}</span></div>
        <div v-for="row in rows" :key="row.key" class="scrape-field-row" :class="{ 'is-same': row.same }" role="row" data-testid="scrape-field-row" :data-field="row.key">
          <div class="scrape-field-name" role="cell"><el-checkbox :data-testid="`scrape-field-${row.key}`" :model-value="selectedKeys.includes(row.key)" :disabled="blocked || row.same" @update:model-value="selectedKeys = $event ? [...selectedKeys.filter(key => key !== row.key), row.key] : selectedKeys.filter(key => key !== row.key)">{{ row.label }}</el-checkbox><small>{{ row.same ? '与草稿相同' : row.overwrite ? '替换已有值' : '补充空字段' }}</small></div>
          <div class="scrape-value" role="cell"><span class="scrape-mobile-label">当前草稿</span><p>{{ row.before }}</p></div><div class="scrape-value scrape-incoming-value" role="cell"><span class="scrape-mobile-label">{{ previewMode === 'site' ? '读取结果' : '模型候选' }}</span><p>{{ row.after }}</p><small v-if="row.note">{{ row.note }}</small></div>
        </div>
        <div v-if="!rows.length" class="scrape-no-fields" data-testid="scrape-empty-result">{{ previewMode === 'site' ? '没有读到可应用的元数据。可以核对链接或改用其他来源，来源链接仍可单独加入草稿。' : '模型没有提供可应用的字段。可先补充网站资料再试。' }}</div>
      </div>
      <div v-if="sourceUrl" class="scrape-source-choice"><el-checkbox data-testid="scrape-append-source" v-model="sourceSelected" :disabled="blocked || sourceAlreadyPresent">{{ sourceAlreadyPresent ? '来源链接已在当前草稿中' : '将本次来源链接追加到草稿' }}</el-checkbox><p>保留已有链接，不会替换来源列表。</p></div>
      <div v-if="overwriteRows.length" class="scrape-overwrite-confirm"><el-checkbox data-testid="scrape-confirm-overwrite" v-model="overwriteConfirmed" :disabled="blocked">已核对，同意替换 {{ overwriteRows.length }} 项已有资料</el-checkbox><p>{{ overwriteRows.map(row => row.label).join('、') }}</p></div>
    </template>
    <template #footer><div class="scrape-footer"><span>应用后请保存草稿，人工核对后再审核。</span><div><el-button data-testid="scrape-close" @click="close">取消</el-button><el-button data-testid="scrape-apply" type="primary" :icon="Check" :disabled="!canApply" @click="apply">应用 {{ applyCount }} 项到草稿</el-button></div></div></template>
  </el-dialog>
</template>

<style scoped>
:global(.metadata-scrape-dialog) { display: flex; flex-direction: column; max-height: 92vh; max-height: 92dvh; }
:global(.metadata-scrape-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.scrape-intro { margin-bottom: 16px; color: #73856b; font-size: 12px; line-height: 1.7; }
.scrape-intro strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #3c5741; font-size: 14px; font-weight: 600; }
.scrape-intro p { margin: 5px 0 0; }
.scrape-form :deep(.el-form-item) { margin-bottom: 12px; }
.scrape-form :deep(.el-form-item__label) { font-size: 12px; margin-bottom: 6px; }
.scrape-url-row { display: flex; align-items: center; gap: 8px; width: 100%; }
.scrape-url-row > .el-select { width: 145px; flex: 0 0 145px; }
.scrape-url-row > .el-input { min-width: 0; flex: 1; }
.scrape-url-row > .el-button { flex: 0 0 auto; margin: 0; }
.scrape-help { width: 100%; margin: 6px 0 0; color: #83917e; font-size: 11px; line-height: 1.6; }
.scrape-credentials { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; padding: 12px; border: 1px solid #e5ebe1; border-radius: 7px; background: #f8faf6; margin-bottom: 12px; }
.scrape-credentials > .scrape-help { grid-column: 1 / -1; margin-top: 0; }
.scrape-model { margin: 4px 0 12px; padding: 10px 12px; border: 1px solid #e5ebe1; border-radius: 7px; background: #f8faf6; }
.scrape-model-header, .scrape-model-controls { display: flex; align-items: center; gap: 10px; }
.scrape-model-header { justify-content: space-between; }
.scrape-model-header strong { display: inline-flex; align-items: center; gap: 6px; color: #3c5741; font-size: 13px; }
.scrape-model-header .el-button { margin: 0; }
.scrape-model-controls { flex-wrap: wrap; margin-top: 6px; }
.scrape-model-controls > .el-select { width: min(240px, 100%); }
.scrape-model-controls > .el-button { margin: 0 0 0 auto; }
.scrape-model .scrape-help { overflow-wrap: anywhere; }
.scrape-preview-tabs { display: flex; gap: 8px; margin-top: 12px; }
.scrape-preview-tabs .el-button { margin: 0; }
.scrape-alert { margin: 0 0 10px; }
.scrape-alert :deep(.el-alert__title) { font-size: 12px; overflow-wrap: anywhere; }
.scrape-reading { color: #7c8d74; font-size: 12px; margin: 14px 0; }
.scrape-result-source { display: flex; align-items: center; gap: 10px; margin: 15px 0 9px; min-width: 0; font-size: 11px; color: #79906e; }
.scrape-result-source > span:first-child { flex: 0 0 auto; font-weight: 600; }
.scrape-result-source > span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scrape-warnings { padding: 7px 11px; border: 1px solid #e9e2d1; border-radius: 6px; background: #fcfaf4; color: #92764e; margin-bottom: 10px; font-size: 11px; line-height: 1.7; overflow-wrap: anywhere; }
.scrape-warnings p { margin: 3px 0; }
.scrape-stale { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; color: #a47736; font-size: 12px; padding: 9px 0; }
.scrape-selection-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 4px 0 8px; color: #82917a; font-size: 11px; }
.scrape-selection-tools > div { display: flex; align-items: center; gap: 12px; }
.scrape-selection-tools .el-button { margin: 0; font-size: 11px; }
.scrape-comparison { border: 1px solid #e4eae0; border-radius: 7px; overflow: hidden; }
.scrape-comparison-head, .scrape-field-row { display: grid; grid-template-columns: minmax(128px, .7fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; padding: 10px 12px; }
.scrape-comparison-head { color: #708466; background: #f6f8f3; font-size: 11px; }
.scrape-field-row { border-top: 1px solid #edf1e9; }
.scrape-field-name, .scrape-value { min-width: 0; }
.scrape-field-name :deep(.el-checkbox) { max-width: 100%; height: auto; min-height: 20px; }
.scrape-field-name :deep(.el-checkbox__label) { white-space: normal; font-size: 12px; line-height: 1.5; color: #526b48; }
.scrape-field-name > small { display: block; margin: 4px 0 0 24px; color: #9aa58f; font-size: 10px; }
.scrape-value p { max-height: 160px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; color: #7b8871; font-size: 12px; line-height: 1.7; }
.scrape-incoming-value p { color: #476442; }
.scrape-value > small { display: block; margin-top: 5px; color: #9aa58f; font-size: 10px; line-height: 1.6; }
.scrape-mobile-label { display: none; }
.scrape-field-row.is-same { background: #fcfdfb; }
.scrape-field-row.is-same .scrape-value p { color: #9aa58f; }
.scrape-no-fields { padding: 20px 14px; color: #8b9a81; font-size: 12px; line-height: 1.8; }
.scrape-source-choice, .scrape-overwrite-confirm { margin-top: 12px; }
.scrape-source-choice :deep(.el-checkbox__label), .scrape-overwrite-confirm :deep(.el-checkbox__label) { white-space: normal; font-size: 12px; line-height: 1.6; }
.scrape-source-choice :deep(.el-checkbox), .scrape-overwrite-confirm :deep(.el-checkbox) { height: auto; }
.scrape-source-choice p, .scrape-overwrite-confirm p { margin: 4px 0 0 24px; color: #99a48e; font-size: 11px; line-height: 1.6; }
.scrape-overwrite-confirm { border-top: 1px solid #e9eee4; padding-top: 12px; }
.scrape-overwrite-confirm p { color: #a28a61; }
.scrape-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; text-align: left; }
.scrape-footer > span { color: #94a087; font-size: 11px; }
.scrape-footer > div { margin-left: auto; flex: 0 0 auto; }
@media (max-width: 640px) {
  .scrape-url-row { flex-wrap: wrap; }
  .scrape-url-row > .el-select { flex-basis: 100%; width: 100%; }
  .scrape-credentials { grid-template-columns: minmax(0, 1fr); }
  .scrape-comparison-head { display: none; }
  .scrape-field-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; }
  .scrape-field-row:first-child { border-top: 0; }
  .scrape-field-name { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .scrape-field-name > small { flex-shrink: 0; margin: 0; }
  .scrape-mobile-label { display: block; color: #9aa58f; font-size: 10px; margin-bottom: 4px; }
}
</style>
