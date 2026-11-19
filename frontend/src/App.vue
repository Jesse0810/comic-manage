<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Reading, Collection, Clock, CircleCheck, Box, Plus, FolderAdd, Search, Refresh, Download, FolderOpened, Lock, Monitor, Check, CopyDocument, Edit, Remove, Connection, Setting, FullScreen, Back, MagicStick } from '@element-plus/icons-vue'
import DetailsPanel from './components/DetailsPanel.vue'
import LibraryTable from './components/LibraryTable.vue'
import LibraryFieldToolbar from './components/LibraryFieldToolbar.vue'
import AgentWorkspace from './components/AgentWorkspace.vue'
import AgentSearchWorkspace from './components/AgentSearchWorkspace.vue'
import { activeLibraryConditions, matchesLibraryConditions, type LibraryCondition, type LibrarySort } from './library-fields'
import { buildLibraryEntries } from './library-groups'
import ImportDialog from './components/ImportDialog.vue'
import BatchEditDialog from './components/BatchEditDialog.vue'
import BatchReviewDialog from './components/BatchReviewDialog.vue'
import BatchReviewResultDialog from './components/BatchReviewResultDialog.vue'
import KomgaSettingsDialog from './components/KomgaSettingsDialog.vue'
import KomgaSyncResultDialog from './components/KomgaSyncResultDialog.vue'
import KomgaJobsDialog from './components/KomgaJobsDialog.vue'
import ExportPlanDialog from './components/ExportPlanDialog.vue'
import MetadataScrapeDialog from './components/MetadataScrapeDialog.vue'
import ModelSettingsDialog from './components/ModelSettingsDialog.vue'
import SiteSettingsDialog from './components/SiteSettingsDialog.vue'
import type { Comic, Metadata, Bootstrap, ComicStatus, ExportPlan, ImportResult, KomgaConfig, KomgaSyncResult, KomgaPullResult, ReviewSyncPlan, ReviewSyncResult, KomgaAutoSyncJob, KomgaAutoSyncJobs, ScrapeApplication, ScrapeFields, ModelConfiguration, SiteConfiguration } from './types'
import { clone, errorMessage, customTags, collectionMode, comicGenres, komgaAutoSyncLabels, scrapeFieldKeys } from './types'
import { invoke } from './api'

type Section = 'all' | ComicStatus
type SourceFilter = 'all' | 'local' | 'komga'
const comics = ref<Comic[]>([])
const outputRoot = ref('')
const dataDir = ref('')
const version = ref('')
const metadataRules = ref('')
const selectedId = ref('')
const editorExpanded = ref(false)
const form = ref<Metadata | null>(null)
const savedMetadata = ref('')
const reviewed = ref(false)
const selectedIds = ref<string[]>([])
const libraryTable = ref<{ clearSelection: () => void }>()
const section = ref<Section>('all')
const sourceFilter = ref<SourceFilter>('all')
const komgaScope = ref('')
const komgaLibraryName = ref('')
const search = ref('')
const author = ref('')
const loading = ref(true)
const busy = ref(false)
const cancelling = ref(false)
const taskTitle = ref('')
const taskMessage = ref('')
const startupError = ref('')
const available = ref(true)
const plan = ref<ExportPlan | null>(null)
const planVisible = ref(false)
const resultVisible = ref(false)
const resultTitle = ref('')
const resultSummary = ref('')
const resultDetail = ref('')
const settingsVisible = ref(false)
const komgaSettingsVisible = ref(false)
const komgaResultVisible = ref(false)
const komgaResult = ref<KomgaSyncResult | null>(null)
const autoSyncJobs = ref<KomgaAutoSyncJob[]>([])
const komgaJobsVisible = ref(false)
const autoSyncPendingCount = computed(() => autoSyncJobs.value.filter(job => job.status !== 'Synced' && (job.status !== 'Approved' || job.canRetry)).length)
const autoSyncByComic = computed(() => {
  const latest = new Map<string, KomgaAutoSyncJob>()
  for (const job of autoSyncJobs.value) {
    const previous = latest.get(job.comicId)
    if (!previous || (Date.parse(job.updatedAt) || 0) > (Date.parse(previous.updatedAt) || 0)) latest.set(job.comicId, job)
  }
  return latest
})
const pendingSyncIds = ref<string[]>([])
const pendingReviewIds = ref<string[]>([])
const pendingPull = ref(false)
const reviewPlan = ref<ReviewSyncPlan | null>(null)
const reviewPlanVisible = ref(false)
const reviewResult = ref<ReviewSyncResult | null>(null)
const reviewResultVisible = ref(false)
const reviewError = ref('')
const importVisible = ref(false)
const batchVisible = ref(false)
const batchIds = ref<string[]>([])
const scrapeVisible = ref(false)
const modelSettingsVisible = ref(false)
const siteSettingsVisible = ref(false)
const siteConfig = ref<SiteConfiguration | null>(null)
const siteConfigLoading = ref(false)
const agentMode = ref(false)
const agentTab = ref<'organize' | 'search'>('organize')
const agentVisited = ref(false)
const agentWorking = ref(false)
const fieldConditions = ref<LibraryCondition[]>([])
const librarySort = ref<LibrarySort>(null)
const activeConditions = computed(() => activeLibraryConditions(fieldConditions.value))
const modelConfiguration = ref<ModelConfiguration>({ profiles: [], default_id: '' })
let modelConfigLoad: Promise<void> | undefined
let unsubscribe: (() => void) | undefined

const current = computed(() => comics.value.find(c => c.id === selectedId.value) || null)
const dirty = computed(() => !!form.value && JSON.stringify(form.value) !== savedMetadata.value)
const sourceComics = computed(() => comics.value.filter(comic => sourceFilter.value === 'all' || (sourceFilter.value === 'local' ? comic.kind !== 'komga' : comic.kind === 'komga' && comic.remote?.scope === komgaScope.value)))
const counts = computed(() => ({
  all: sourceComics.value.length,
  draft: sourceComics.value.filter(c => c.status === 'draft').length,
  approved: sourceComics.value.filter(c => c.status === 'approved').length,
  packaged: sourceComics.value.filter(c => c.status === 'packaged').length,
}))
const authors = computed(() => [...new Set(sourceComics.value.flatMap(c => c.metadata.authors))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
const filtered = computed(() => {
  const term = search.value.trim().normalize('NFKC').toLocaleLowerCase()
  return sourceComics.value.filter(c => {
    const m = c.metadata
    const searchable = [m.title, m.series, m.localized_series, m.localized_series_label, m.collection_mode, m.work_group, m.text_version, m.original_title, m.chinese_title, ...m.authors, ...m.pencillers, m.publisher, m.translator, m.genre, ...m.tags, ...customTags(m), m.summary, ...m.web, m.number, m.volume, m.year, c.remote?.libraryName].join(' ').normalize('NFKC').toLocaleLowerCase()
    return (section.value === 'all' || c.status === section.value)
      && (!author.value || m.authors.includes(author.value)) && (!term || searchable.includes(term))
      && matchesLibraryConditions(c, activeConditions.value)
  })
})
const libraryWorkCount = computed(() => buildLibraryEntries(filtered.value, sourceComics.value).length)
const revealMatches = computed(() => !!search.value.trim() || !!author.value || section.value !== 'all' || activeConditions.value.length > 0)
const revealKey = computed(() => JSON.stringify([search.value.trim(), author.value, section.value, activeConditions.value]))
const filteredIds = computed(() => new Set(filtered.value.map(comic => comic.id)))
const checkedOutsideFilter = computed(() => selectedIds.value.filter(id => !filteredIds.value.has(id)).length)
const sectionTitle = computed(() => ({ all: '我的漫画库', draft: '待审核作品', approved: '已审核作品', packaged: '已生成作品' })[section.value])
const exportIds = computed(() => selectedIds.value.length ? selectedIds.value : (selectedId.value ? [selectedId.value] : []))
const agentUnavailable = computed(() => busy.value || loading.value || !available.value)
const disabled = computed(() => agentUnavailable.value || agentWorking.value)
const agentSelected = computed(() => {
  const ids = new Set(selectedIds.value)
  return comics.value.filter(comic => ids.has(comic.id) && canEditComic(comic) && !comic.remote?.conflict)
})
const agentFiltered = computed(() => filtered.value.filter(comic => canEditComic(comic) && !comic.remote?.conflict))
const selectedComics = computed(() => comics.value.filter(comic => exportIds.value.includes(comic.id)))
const selectionHasRemote = computed(() => selectedComics.value.some(comic => comic.kind === 'komga'))
const selectionHasInactive = computed(() => selectedComics.value.some(comic => !canEditComic(comic)))
const checkedSelectionHasInactive = computed(() => selectedIds.value.some(id => {
  const comic = comics.value.find(item => item.id === id)
  return !comic || !canEditComic(comic)
}))
const currentReadOnly = computed(() => !!current.value && !canEditComic(current.value))
function canEditComic(comic: Comic) { return comic.kind !== 'komga' || (!!komgaScope.value && comic.remote?.scope === komgaScope.value && comic.remote?.unavailable !== true) }
function clearLibrarySelection() {
  selectedIds.value = []
  libraryTable.value?.clearSelection()
}
async function changeSource(value: SourceFilter) {
  if (disabled.value || value === sourceFilter.value || !await resolveEdits()) return
  sourceFilter.value = value
  selectedIds.value = []
  libraryTable.value?.clearSelection()
  selectData(null)
  author.value = ''
}

const navigation = [
  { key: 'all' as Section, title: '全部漫画', icon: Collection },
  { key: 'draft' as Section, title: '待审核', icon: Clock },
  { key: 'approved' as Section, title: '已审核', icon: CircleCheck },
  { key: 'packaged' as Section, title: '已生成', icon: Box },
]

function selectData(comic: Comic | null) {
  if (!comic) editorExpanded.value = false
  selectedId.value = comic?.id || ''
  form.value = comic ? clone(comic.metadata) : null
  savedMetadata.value = JSON.stringify(form.value)
  reviewed.value = !!comic && comic.status !== 'draft'
}

function replaceComic(comic: Comic) {
  const index = comics.value.findIndex(c => c.id === comic.id)
  if (index >= 0) comics.value[index] = comic
  else comics.value.unshift(comic)
}

async function refresh(preferId = selectedId.value) {
  const data = await invoke<Bootstrap>('bootstrap')
  metadataRules.value = data.metadataRules || ''
  komgaScope.value = data.komgaScope || ''
  komgaLibraryName.value = data.komgaLibraryName || ''
  autoSyncJobs.value = data.autoSyncJobs || []
  comics.value = data.comics.filter(comic => comic.kind !== 'komga' || comic.remote?.scope === data.komgaScope)
  outputRoot.value = data.outputRoot
  dataDir.value = data.dataDir
  selectedIds.value = selectedIds.value.filter(id => comics.value.some(c => c.id === id && canEditComic(c)))
  selectData(comics.value.find(c => c.id === preferId) || null)
}

async function refreshAutoSyncJobs() {
  if (!available.value) return
  try { autoSyncJobs.value = (await invoke<KomgaAutoSyncJobs>('komga_jobs', {})).jobs }
  catch { /* A background status refresh must not interrupt editing. */ }
}
function autoSyncLabel(comic: Comic) {
  const job = autoSyncByComic.value.get(comic.id)
  return job ? job.status === 'Approved' && job.canRetry ? '成品已生成 · 待创建任务' : komgaAutoSyncLabels[job.status] || job.status : ''
}

function showResult(title: string, summary: string, details: string[] | string = '') {
  resultTitle.value = title
  resultSummary.value = summary
  resultDetail.value = Array.isArray(details) ? details.join('\n\n') : details
  resultVisible.value = true
}

function showError(message: string) {
  showResult('操作未完成', '请查看以下信息，处理后重试。', message)
}

async function loadModelConfiguration() {
  if (!modelConfigLoad) {
    modelConfigLoad = invoke<ModelConfiguration>('model_config_get').then(value => {
      modelConfiguration.value = value
    }).catch(error => { ElMessage.warning(errorMessage(error)) }).finally(() => { modelConfigLoad = undefined })
  }
  return modelConfigLoad
}
function openModelSettings() {
  modelSettingsVisible.value = true
}
async function loadSiteConfiguration() {
  if (siteConfigLoading.value) return
  siteConfigLoading.value = true
  try {
    const status = await invoke<SiteConfiguration>('site_config_get')
    if (!siteSettingsVisible.value) siteConfig.value = status
  } catch { /* The settings dialog gives a retry and shows the read error. */ }
  finally { siteConfigLoading.value = false }
}
function openSiteSettings() {
  siteSettingsVisible.value = true
}
function openMetadataScraper() {
  if (disabled.value || !current.value || !form.value || !canEditComic(current.value) || current.value.remote?.conflict) return
  scrapeVisible.value = true
}
function metadataSourceKey(value: string) {
  try {
    const parsed = new URL(value.trim())
    parsed.hash = ''
    parsed.hostname = parsed.hostname.replace(/^www\./, '')
    parsed.pathname = parsed.pathname.replace(/\/+$/, '') || '/'
    return parsed.href
  } catch { return value.trim() }
}
function applyScrapedMetadata(application: ScrapeApplication) {
  const comic = current.value
  if (disabled.value || !comic || !form.value || comic.id !== application.id || !canEditComic(comic) || comic.remote?.conflict) {
    ElMessage.warning('当前作品已变化或暂不可编辑，请重新打开读取窗口。')
    return
  }
  if (JSON.stringify(form.value) !== JSON.stringify(application.baseline)) {
    ElMessage.warning('当前草稿已变化，请更新对照后重新勾选。')
    return
  }
  const keys = scrapeFieldKeys.filter(key => Object.prototype.hasOwnProperty.call(application.patch, key))
  const patch = Object.fromEntries(keys.map(key => [key, clone(application.patch[key])])) as ScrapeFields
  const dateKeys = ['year', 'month', 'day']
  if (keys.some(key => dateKeys.includes(key)) && !dateKeys.every(key => Object.prototype.hasOwnProperty.call(patch, key))) {
    ElMessage.warning('发行日期需按年、月、日整组核对，请重新读取。')
    return
  }
  const next = { ...clone(form.value), ...patch }
  let sourceAdded = false
  if (application.sourceUrl) {
    try {
      const source = new URL(application.sourceUrl)
      if (!['https:', 'http:'].includes(source.protocol) || source.username || source.password) throw new Error()
      if (!next.web.some(value => metadataSourceKey(value) === metadataSourceKey(source.href))) {
        next.web = [...next.web, source.href]
        sourceAdded = true
      }
    } catch { ElMessage.warning('来源链接无效，请重新读取并核对。'); return }
  }
  const count = keys.filter(key => !dateKeys.includes(key)).length + Number(keys.some(key => dateKeys.includes(key))) + Number(sourceAdded)
  if (!count) return
  form.value = next
  reviewed.value = false
  scrapeVisible.value = false
  ElMessage.success(`已应用 ${count} 项到当前草稿，请保存并重新核对审核`)
}

async function saveCurrent(): Promise<boolean> {
  if (!current.value || !form.value || !available.value || !canEditComic(current.value)) return false
  busy.value = true
  taskTitle.value = '保存资料'
  taskMessage.value = '正在保存到本机资料库…'
  try {
    const comic = await invoke<Comic>('save', { id: current.value.id, metadata: clone(form.value) })
    replaceComic(comic)
    selectData(comic)
    ElMessage.success('草稿已保存，核对后可重新审核')
    return true
  } catch (error) { showError(errorMessage(error)); return false }
  finally { busy.value = false; taskMessage.value = '' }
}

async function resolveEdits(): Promise<boolean> {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前作品有未保存的修改。保存为草稿后继续，或放弃此次修改。', '保留作品修改', {
      confirmButtonText: '保存后继续', cancelButtonText: '放弃修改',
      distinguishCancelAndClose: true, closeOnClickModal: false, closeOnPressEscape: false,
      type: 'warning',
    })
    return await saveCurrent()
  } catch (action) {
    if (action === 'cancel') { selectData(current.value); return true }
    return false
  }
}

async function openAgentWorkspace() {
  if (agentMode.value || disabled.value || !await resolveEdits()) return
  agentVisited.value = true
  agentMode.value = true
  if (!siteConfig.value) void loadSiteConfiguration()
}
function selectLibrarySection(target: Section) {
  if (agentWorking.value) return
  agentMode.value = false
  section.value = target
}
async function refreshAfterAgent() {
  try { await refresh() }
  catch (reason) { ElMessage.warning('草稿已保存，但列表刷新失败。' + errorMessage(reason)) }
}

async function selectComic(comic: Comic) {
  if (disabled.value || comic.id === selectedId.value) return
  if (!await resolveEdits()) return
  selectData(comic)
}

async function runTask<T>(title: string, action: () => Promise<T>, completed: (result: T) => void) {
  if (disabled.value) return
  busy.value = true
  cancelling.value = false
  taskTitle.value = title
  taskMessage.value = '正在准备…'
  try {
    const result = await action()
    await refresh()
    completed(result)
  } catch (error) {
    try { await refresh() } catch { /* Service errors are reported below. */ }
    showError(errorMessage(error))
  } finally { busy.value = false; cancelling.value = false; taskMessage.value = '' }
}

async function addDirectory() {
  if (disabled.value || !await resolveEdits()) return
  importVisible.value = true
}

function dialogTask(state: { busy: boolean; title: string }) {
  busy.value = state.busy
  cancelling.value = false
  taskTitle.value = state.title
  taskMessage.value = state.busy ? '正在准备…' : ''
}

async function imported(result: ImportResult) {
  loading.value = true
  try {
    await refresh(selectedId.value || result.ids[0])
    showResult(result.cancelled ? '导入已取消' : '导入完成', `已导入 ${result.count} 本作品，${result.errors.length} 项未完成。${result.skipped ? `跳过 ${result.skipped} 项。` : ''}资料可在右侧继续核对。`, result.errors)
  } catch (error) { showError(errorMessage(error)) }
  finally { loading.value = false }
}

async function openBatchEditor() {
  if (disabled.value || !await resolveEdits()) return
  if (!exportIds.value.length) { ElMessage.info('请勾选或选择要编辑的作品'); return }
  if (selectionHasInactive.value) { ElMessage.warning('请切回作品所属的 Komga 连接后再批量编辑'); return }
  batchIds.value = [...exportIds.value]
  batchVisible.value = true
}

async function batchSaved(count: number) {
  loading.value = true
  try {
    await refresh()
    if (count) ElMessage.success(`已更新 ${count} 本作品，请重新审核`)
    else ElMessage.info('所选作品的资料没有变化')
  }
  catch (error) { showError(errorMessage(error)) }
  finally { loading.value = false }
}

async function removeComics() {
  if (disabled.value || selectionHasRemote.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(`将选中的 ${ids.length} 本作品移出漫画列表？源文件和已生成的 CBZ 都会保留。以后可通过添加漫画重新导入。`, '移出漫画列表', {
      confirmButtonText: '移出列表', cancelButtonText: '保留', type: 'warning', closeOnClickModal: false,
    })
  } catch { return }
  await runTask('移出漫画列表', () => invoke<{ count: number }>('remove', { ids }), (result) => {
    libraryTable.value?.clearSelection()
    selectedIds.value = []
    ElMessage.success(`已移出 ${result.count} 本作品，源文件已保留`)
  })
}

async function rescan() {
  if (disabled.value || !await resolveEdits()) return
  if (!comics.value.some(comic => comic.kind !== 'komga')) { ElMessage.info('请先添加一个漫画目录'); return }
  await runTask('重新扫描', () => invoke('scan', {}), (result: any) => {
    showResult(result.cancelled ? '扫描已取消' : '扫描完成', `已扫描 ${result.count} 本作品，${result.errors.length} 项未读取。`, result.errors)
  })
}

async function chooseOutput() {
  if (disabled.value) return false
  try {
    const path = await window.comic.chooseDirectory()
    if (!path) return false
    const result = await invoke('set_output', { path })
    outputRoot.value = result.outputRoot
    return true
  } catch (error) { showError(errorMessage(error)); return false }
}

async function approveCurrent() {
  if (!current.value || !form.value || disabled.value || !canEditComic(current.value)) return
  if (!reviewed.value) { ElMessage.warning('请先核对作品，并勾选审核确认'); return }
  const remote = current.value.kind === 'komga'
  const m = form.value
  if (!m.title.trim()) { ElMessage.warning('请填写作品原始标题，无需强制填写中文标题'); return }
  if (!m.authors.some(author => author.trim())) { ElMessage.warning('请在“作者与出版”中填写至少一位真实作者'); return }
  if (!comicGenres.includes(m.genre)) { ElMessage.warning('请在“内容与分级”中选择实际内容类型：漫画、小说、CG集、画集、设定集或插画集'); return }
  if (!['Everyone', 'MA15+', 'R18+'].includes(m.age_rating)) { ElMessage.warning('请在“内容与分级”中确认本册年龄分级：全年龄、MA15+ 或 R18'); return }
  if (![...m.tags, ...customTags(m)].some(tag => tag.trim())) { ElMessage.warning('请填写至少一条真实题材标签或已确认分类；未知信息请保存草稿，不填写“无”凑数'); return }
  if (m.original_works.length > 1) { ElMessage.warning('旧资料包含多个原作，请在“内容与分级”中核对并改为一个'); return }
  const translators = m.translator.split(/[,，;；\n]/).map(value => value.trim()).filter(Boolean)
  if (translators.some(value => ['未知汉化', '未知漢化', '中国翻訳', '中国翻译', '中國翻譯', '未知翻译'].includes(value))) { ElMessage.warning('请在“作者与出版”中补充已知翻译版本的实际译者或汉化组'); return }
  if (!remote) {
    if (collectionMode(m) === 'series') {
      if (!m.series.trim()) { ElMessage.warning('多卷系列请填写系列名称；独立作品请选择 One-Shot 模式'); return }
      if (m.localized_series.trim() && !m.localized_series_label.trim()) { ElMessage.warning('系列别名需要填写标签，例如 zh-Hans 或原名'); return }
    }
  }
  await runTask('审核作品', () => invoke('approve', { id: current.value!.id, metadata: clone(form.value) }), () => {
    ElMessage.success(remote ? '审核通过，可以同步到 Komga' : '审核通过，可以生成 CBZ')
  })
}

async function prepareExport() {
  if (disabled.value || selectionHasRemote.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) { ElMessage.info('请选择一本或多本已审核作品'); return }
  if (!outputRoot.value && !await chooseOutput()) return
  busy.value = true
  taskTitle.value = '检查生成计划'
  taskMessage.value = '正在检查源文件、成品路径和当前 Komga 书库…'
  try {
    plan.value = await invoke<ExportPlan>('plan', { ids, outputRoot: outputRoot.value })
    planVisible.value = true
  } catch (error) { showError(errorMessage(error)) }
  finally { busy.value = false; taskMessage.value = '' }
}

async function confirmExport(selection: { ids: string[]; confirmedIds: string[] }) {
  if (!plan.value || disabled.value) return
  const token = plan.value.token
  planVisible.value = false
  await runTask('生成并校验 CBZ', () => invoke('export', { token, ...selection }), (result: any) => {
    const paths = new Set<string>((result.completed || []).map((path: string) => path.replace(/\\/g, '/')))
    const queued = autoSyncJobs.value.filter(job => job.outputPath && paths.has(job.outputPath.replace(/\\/g, '/')))
    const autoSyncErrors: string[] = Array.isArray(result.autoSyncErrors) ? result.autoSyncErrors : []
    const syncSummary = queued.length ? ' Komga 实体同步状态单独保存，可在左下角“Komga 连接设置”的“同步任务”查看；成品生成完成不代表服务器已同步。' : ''
    const syncWarning = autoSyncErrors.length ? ` 自动同步有 ${autoSyncErrors.length} 项问题，相关任务未能创建；已生成并通过校验的成品仍然有效。请查看原因，修复后重试同步，无需重新打包。` : ''
    showResult(result.cancelled ? '生成已取消' : '生成完成', `${result.completed.length} 本已生成并通过校验，${result.errors.length} 本生成失败。全部源文件已保留。${syncWarning}${syncSummary}`, [...result.errors, ...autoSyncErrors.map(reason => `自动同步任务：${reason}`), ...result.completed])
  })
  plan.value = null
}

async function cancelTask() {
  if (!busy.value || cancelling.value) return
  cancelling.value = true
  taskMessage.value = '正在取消，等待当前操作结束…'
  try { await invoke('cancel') }
  catch (error) { ElMessage.warning(errorMessage(error)) }
}

async function backup() {
  if (disabled.value || !await resolveEdits()) return
  try {
    const path = await window.comic.chooseBackup()
    if (!path) return
    await runTask('备份资料库', () => invoke('backup', { path }), () => {
      showResult('备份完成', '已备份目录索引、元数据和审核记录，不包含漫画图片。', path)
    })
  } catch (error) { showError(errorMessage(error)) }
}

async function openKomgaSettings() {
  if (disabled.value || !await resolveEdits()) return
  pendingReviewIds.value = []
  pendingSyncIds.value = []
  pendingPull.value = true
  komgaSettingsVisible.value = true
}

function openKomgaJobs() {
  if (disabled.value) return
  pendingReviewIds.value = []; pendingSyncIds.value = []; pendingPull.value = false
  komgaSettingsVisible.value = false
  komgaJobsVisible.value = true
}

async function executeKomgaSync(ids: string[]) {
  if (disabled.value || !ids.length) return
  if (ids.some(id => { const comic = comics.value.find(item => item.id === id); return comic && !canEditComic(comic) })) { ElMessage.warning('所选作品不属于当前可用的 Komga 书库，请切回对应连接'); return }
  busy.value = true
  cancelling.value = false
  komgaResultVisible.value = false
  taskTitle.value = '同步到 Komga'
  taskMessage.value = '正在匹配作品并读取服务器资料…'
  try {
    komgaResult.value = await invoke<KomgaSyncResult>('komga_sync', { ids })
    try { await refresh() } catch (reason) { ElMessage.warning(`同步已返回结果，本机列表刷新失败：${errorMessage(reason)}`) }
    komgaResultVisible.value = true
  } catch (reason) { showError(errorMessage(reason)) }
  finally { busy.value = false; cancelling.value = false; taskMessage.value = '' }
}

async function syncKomga() {
  if (disabled.value || selectionHasInactive.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) return
  let config: KomgaConfig | undefined
  busy.value = true
  taskTitle.value = '准备 Komga 同步'
  taskMessage.value = '正在读取连接配置…'
  try { config = await window.comic.komgaConfig() }
  catch (reason) { showError(errorMessage(reason)) }
  finally { busy.value = false; taskMessage.value = '' }
  if (!config) return
  if (!config.url || !config.libraryId || !config.hasApiKey) {
    pendingReviewIds.value = []
    pendingSyncIds.value = ids
    pendingPull.value = false
    komgaSettingsVisible.value = true
    return
  }
  await executeKomgaSync(ids)
}

function reviewSelectionAvailable(ids: string[]) {
  return ids.every(id => {
    const comic = comics.value.find(item => item.id === id)
    return !!comic && canEditComic(comic)
  })
}

async function executePrepareReview(ids: string[]) {
  if (disabled.value || !ids.length) return
  if (!reviewSelectionAvailable(ids)) { ElMessage.warning('所选作品不属于当前可用的 Komga 书库，请切回对应连接后重新勾选'); return }
  busy.value = true
  cancelling.value = false
  reviewPlan.value = null
  reviewPlanVisible.value = false
  reviewResultVisible.value = false
  taskTitle.value = '检查批量审核与同步'
  taskMessage.value = '正在逐本检查必填资料、来源与远端状态…'
  try {
    reviewPlan.value = await invoke<ReviewSyncPlan>('prepare_review_sync', { ids: [...ids] })
    try { await refresh() } catch (reason) { ElMessage.warning(`预检已返回，本机列表刷新失败：${errorMessage(reason)}`) }
    reviewPlanVisible.value = true
  } catch (reason) {
    try { await refresh() } catch { /* Keep the original preflight error. */ }
    showError(errorMessage(reason))
  } finally { busy.value = false; cancelling.value = false; taskMessage.value = '' }
}

async function batchReviewSync() {
  if (disabled.value || !selectedIds.value.length || checkedSelectionHasInactive.value || !await resolveEdits()) return
  const ids = [...selectedIds.value]
  if (!ids.length || !reviewSelectionAvailable(ids)) return
  let config: KomgaConfig | undefined
  busy.value = true
  taskTitle.value = '准备批量审核与同步'
  taskMessage.value = '正在读取连接配置…'
  try { config = await window.comic.komgaConfig() }
  catch (reason) { showError(errorMessage(reason)) }
  finally { busy.value = false; taskMessage.value = '' }
  if (!config) return
  if (!config.url || !config.libraryId || !config.hasApiKey) {
    pendingReviewIds.value = ids
    pendingSyncIds.value = []
    pendingPull.value = false
    komgaSettingsVisible.value = true
    return
  }
  await executePrepareReview(ids)
}

async function commitReviewSync() {
  const plan = reviewPlan.value
  if (disabled.value || !plan?.token || !plan.ready || plan.cancelled) return
  const token = plan.token
  reviewPlanVisible.value = false
  reviewPlan.value = null
  reviewResult.value = null
  reviewError.value = ''
  busy.value = true
  cancelling.value = false
  taskTitle.value = '批量审核并同步'
  taskMessage.value = '正在重新检查资料、审核可通过作品并同步到 Komga…'
  try {
    reviewResult.value = await invoke<ReviewSyncResult>('commit_review_sync', { token })
  } catch (reason) {
    reviewError.value = errorMessage(reason)
  } finally {
    try { await refresh() } catch (reason) { ElMessage.warning(`批量操作后列表刷新失败：${errorMessage(reason)}`) }
    reviewResultVisible.value = true
    busy.value = false
    cancelling.value = false
    taskMessage.value = ''
  }
}

async function executeKomgaPull() {
  if (disabled.value) return
  busy.value = true
  cancelling.value = false
  taskTitle.value = '读取 Komga 书库'
  taskMessage.value = '正在读取书库作品与远端元数据…'
  try {
    const result = await invoke<KomgaPullResult>('komga_pull', {})
    await refresh()
    sourceFilter.value = 'komga'
    section.value = 'all'
    author.value = ''; search.value = ''; fieldConditions.value = []; selectedIds.value = []
    libraryTable.value?.clearSelection()
    selectData(comics.value.find(comic => result.ids.includes(comic.id) && canEditComic(comic)) || sourceComics.value[0] || null)
    const summary = `读取 ${result.total} 本：新增 ${result.added} 本，更新 ${result.updated} 本，保留现有资料 ${result.kept} 本。${result.retired ? `已清理 ${result.retired} 本已确认入库的本地待上传记录。` : ''}`
    if (result.errors.length) showResult(result.cancelled ? '书库读取已取消' : '书库读取完成', summary, result.errors)
    else if (result.cancelled) ElMessage.info(`读取已取消。${summary}`)
    else ElMessage.success(summary)
  } catch (reason) { showError(errorMessage(reason)) }
  finally { busy.value = false; cancelling.value = false; taskMessage.value = '' }
}

async function komgaConfigured(config: KomgaConfig) {
  const reviewIds = [...pendingReviewIds.value]
  const ids = [...pendingSyncIds.value]
  const shouldPull = pendingPull.value
  pendingReviewIds.value = []; pendingSyncIds.value = []; pendingPull.value = false
  loading.value = true
  try { await refresh() }
  catch (reason) { showError(errorMessage(reason)); return }
  finally { loading.value = false }
  if (config.url && config.libraryId && config.hasApiKey) {
    if (reviewIds.length) await executePrepareReview(reviewIds)
    else if (ids.length) await executeKomgaSync(ids)
    else if (shouldPull) await executeKomgaPull()
    else ElMessage.success('Komga 配置已保存')
  } else ElMessage.success('API Key 已清除')
}

async function resolveRemoteConflict(resolution: 'keep_local' | 'use_remote') {
  if (disabled.value || !current.value?.remote?.conflict || !canEditComic(current.value)) return
  const id = current.value.id
  if (resolution === 'keep_local') {
    if (!await resolveEdits()) return
  } else {
    try { await ElMessageBox.confirm('采用远端资料会放弃这本作品尚未同步的本机修改，包括当前未保存的输入。确认后将重新以远端资料建立草稿。', '采用远端资料', { confirmButtonText: '采用远端资料', cancelButtonText: '保留本机修改', type: 'warning', closeOnClickModal: false }) }
    catch { return }
  }
  await runTask('处理远端资料冲突', () => invoke('komga_resolve', { id, resolution }), () => {
    ElMessage.success(resolution === 'keep_local' ? '已保留本机修改并更新远端基准，请重新核对审核' : '已采用远端资料，请重新核对审核')
  })
}

async function exportKomga() {
  if (disabled.value || selectionHasRemote.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) return
  try {
    const path = await window.comic.chooseKomgaManifest()
    if (!path) return
    await runTask('导出 Komga 同步清单', () => invoke('export_komga', { ids, path }), (result: any) => {
      showResult('清单已导出', `已导出 ${result.count} 本作品的实体和 ${result.seriesCount} 个系列的别名。扫描成品后可使用 sync-komga.ps1 预览并同步到 Komga。`, path)
    })
  } catch (error) { showError(errorMessage(error)) }
}

async function closeRequested() {
  if (busy.value || agentWorking.value) { ElMessage.warning('任务进行中，请先取消并等待当前操作结束'); return }
  if (await resolveEdits()) await window.comic.closeWindow()
}


watch(form, () => { if (dirty.value) reviewed.value = false }, { deep: true, flush: 'sync' })
watch(selectedId, () => { scrapeVisible.value = false }, { flush: 'sync' })
watch([dirty, busy, agentWorking], () => window.comic?.setWindowState({ dirty: dirty.value, busy: busy.value || agentWorking.value }), { immediate: true })
onMounted(async () => {
  if (!window.comic) {
    available.value = false
    startupError.value = '请通过 Comic Manage 桌面程序打开。此界面需要连接本机漫画服务。'
    loading.value = false
    return
  }
  void loadModelConfiguration()
  unsubscribe = window.comic.onEvent((event, data) => {
    if (event === 'task-progress') taskMessage.value = data.message
    if (event === 'komga-jobs-updated') {
      if (Array.isArray(data?.jobs)) autoSyncJobs.value = data.jobs
      else void refreshAutoSyncJobs()
    }
    if (event === 'close-request') closeRequested()
    if (event === 'service-exit') {
      available.value = false
      startupError.value = data.message || '本机漫画服务已停止，请重启 Comic Manage。'
    }
  })
  try { await refresh(); version.value = await window.comic.getVersion() }
  catch (error) { available.value = false; startupError.value = errorMessage(error) }
  finally { loading.value = false }
})
onBeforeUnmount(() => unsubscribe?.())
</script>

<template>
  <div class="desktop-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-icon"><el-icon :size="25"><Reading /></el-icon></div><div><strong>漫画整理</strong><span>COMIC MANAGE</span></div></div>
      <div class="workspace-label">我的工作区</div>
      <nav class="navigation">
        <button v-for="item in navigation" :key="item.key" :class="{ active: !agentMode && section === item.key }" :disabled="agentWorking" @click="selectLibrarySection(item.key)"><el-icon :size="18"><component :is="item.icon" /></el-icon><span>{{ item.title }}</span><b>{{ counts[item.key] }}</b></button>
        <button data-testid="agent-open" class="agent-nav" :class="{ active: agentMode }" :aria-current="agentMode ? 'page' : undefined" :disabled="disabled" @click="openAgentWorkspace"><el-icon :size="18"><MagicStick /></el-icon><span>Agent</span></button>
      </nav>
      <div class="sidebar-note"><div class="sidebar-note-icon"><el-icon><Lock /></el-icon></div><div><strong>原图保留，资料有序</strong><p>审核后生成成品，<br />可同步资料到你的 Komga。</p></div></div>
      <div class="sidebar-bottom"><button :disabled="disabled" @click="backup"><el-icon><CopyDocument /></el-icon>备份资料库</button><button data-testid="komga-settings" :disabled="disabled" @click="openKomgaSettings"><el-icon><Setting /></el-icon>Komga 连接设置<span v-if="autoSyncPendingCount" class="sidebar-job-count" :title="`${autoSyncPendingCount} 项同步任务等待或需要处理`">{{ autoSyncPendingCount }}</span></button><button data-testid="model-settings-open" :disabled="disabled" @click="openModelSettings"><el-icon><Connection /></el-icon>模型管理</button><button data-testid="site-settings-open" :disabled="disabled" @click="openSiteSettings"><el-icon><Lock /></el-icon>站点 API 设置</button><button @click="settingsVisible = true"><el-icon><Monitor /></el-icon>工作区信息</button><div class="version-row"><span>本地桌面版</span><span v-if="version">v{{ version }}</span></div></div>
    </aside>

    <main class="workspace" :class="{ 'workspace-editor-expanded': editorExpanded && !agentMode }">
      <header v-show="!agentMode && !editorExpanded" class="workspace-header">
        <h1>{{ sectionTitle }}<span class="heading-count" title="按独立版本计数">{{ counts[section] }}</span></h1>
        <div class="header-actions">
          <el-button :icon="Refresh" :disabled="disabled" @click="rescan">重新扫描</el-button>
          <el-button data-testid="add-comics" type="primary" :icon="Plus" :disabled="disabled" @click="addDirectory">添加漫画</el-button>
        </div>
      </header>

      <el-alert v-if="startupError" :title="startupError" description="请关闭并重新打开桌面程序。漫画源文件和本机资料库不会被删除。" type="error" :closable="false" show-icon class="startup-error" />

      <div v-if="agentMode" class="agent-modes" role="group" aria-label="Agent 工作方式">
        <el-button data-testid="agent-tab-organize" :type="agentTab === 'organize' ? 'primary' : 'default'"
          :aria-pressed="agentTab === 'organize'" :disabled="agentWorking" @click="agentTab = 'organize'">批量整理</el-button>
        <el-button data-testid="agent-tab-search" :type="agentTab === 'search' ? 'primary' : 'default'"
          :aria-pressed="agentTab === 'search'" :disabled="agentWorking" @click="agentTab = 'search'">站点搜索</el-button>
      </div>
      <AgentWorkspace v-if="agentVisited" v-show="agentMode && agentTab === 'organize'" :selected="agentSelected" :filtered="agentFiltered"
        :configuration="modelConfiguration" :disabled="agentUnavailable" @model-settings="openModelSettings"
        @task-state="agentWorking = $event" @applied="refreshAfterAgent" />
      <AgentSearchWorkspace v-if="agentVisited" v-show="agentMode && agentTab === 'search'" :selected="agentSelected"
        :filtered="agentFiltered" :disabled="agentUnavailable" :site-config="siteConfig" :site-config-loading="siteConfigLoading"
        @site-settings="openSiteSettings" @task-state="agentWorking = $event" @applied="refreshAfterAgent" />

      <div v-show="!agentMode" class="work-area">
        <section v-show="!editorExpanded" class="library-panel">
          <div class="library-source-bar"><el-select data-testid="source-filter" :model-value="sourceFilter" :disabled="disabled" @update:model-value="changeSource" aria-label="作品来源"><el-option label="全部来源" value="all" /><el-option label="本地作品" value="local" /><el-option label="当前 Komga 书库" value="komga" /></el-select><span :title="komgaLibraryName">{{ komgaScope ? `Komga · ${komgaLibraryName || '当前书库'}` : '配置 Komga 后可直接读取书库' }}</span><LibraryFieldToolbar v-model:conditions="fieldConditions" v-model:sort="librarySort" :disabled="disabled" /></div>
          <div class="library-toolbar"><el-input v-model="search" clearable :prefix-icon="Search" placeholder="搜索作品、系列、作者或分类" :disabled="disabled" class="search-input" /><el-select v-model="author" clearable filterable placeholder="全部作者" :disabled="disabled" class="author-select"><el-option v-for="name in authors" :key="name" :label="name" :value="name" /></el-select></div>
          <div class="library-summary"><span class="library-count-summary">{{ libraryWorkCount }} 部作品 · {{ filtered.length }} 个版本<span v-if="selectedIds.length" class="selected-count">已选择 {{ selectedIds.length }} 本</span><span v-if="checkedOutsideFilter" class="hidden-selection-count">（筛选外 {{ checkedOutsideFilter }} 本）</span><el-button v-if="selectedIds.length" data-testid="clear-library-selection" link :disabled="disabled" @click="clearLibrarySelection">取消勾选</el-button></span><div class="library-batch-actions"><el-button link :icon="Edit" :disabled="disabled || !exportIds.length || selectionHasInactive" @click="openBatchEditor">批量编辑</el-button><el-button data-testid="batch-review-sync" link :icon="CircleCheck" :disabled="disabled || !selectedIds.length || checkedSelectionHasInactive" :title="!selectedIds.length ? '先勾选一本或多本作品；仅选中当前行不会执行批量审核' : checkedSelectionHasInactive ? '请切回所选作品所属的可用 Komga 连接' : '先逐本检查并确认，只审核和同步可通过的作品'" @click="batchReviewSync">批量审核并同步</el-button><el-button data-testid="komga-sync" link :icon="Connection" :disabled="disabled || !exportIds.length || selectionHasInactive" title="同步已审核资料；系列级字段需选全系列" @click="syncKomga">同步到 Komga</el-button><el-button link :icon="Remove" :disabled="disabled || !exportIds.length || selectionHasRemote" title="远端作品在 Komga 中管理，此处不删除服务器内容" @click="removeComics">移出列表</el-button></div><span v-if="counts.draft && !exportIds.length" class="review-reminder"><i></i>{{ counts.draft }} 本等待审核</span><span v-else-if="!exportIds.length" class="review-reminder clear"><el-icon><Check /></el-icon>资料已整理</span></div>

          <div class="table-area" v-loading="loading">
            <LibraryTable v-if="comics.length" v-show="filtered.length > 0" ref="libraryTable" v-model:sort="librarySort" :comics="filtered" :all-comics="sourceComics" :selected-ids="selectedIds" :current-id="selectedId" :disabled="disabled" :can-select="canEditComic" :auto-sync-label="autoSyncLabel" :reveal-matches="revealMatches" :reveal-key="revealKey" @row-click="selectComic" @selection-change="(ids: string[]) => selectedIds = ids" />
            <div v-if="!filtered.length && !loading" class="library-empty"><div class="library-empty-icon"><el-icon :size="38"><Collection /></el-icon><span class="empty-icon-spark"></span></div><template v-if="sourceFilter === 'komga' && !sourceComics.length"><h2>直接整理 Komga 书库</h2><p>读取书库中的作品和资料，编辑并审核后直接同步。<br />再次读取会保留尚未同步的本机修改。</p><p>请从左下角“Komga 连接设置”读取书库。</p></template><template v-else-if="comics.length"><h2>没有找到匹配的作品</h2><p>试试其他标题、作者或标签。</p><el-button plain @click="search = ''; author = ''; fieldConditions = []; section = 'all'; changeSource('all')">清除筛选</el-button></template><template v-else><h2>从第一本漫画开始</h2><p>可以选择一个或多个本地或 NAS 文件夹。<br />预览后确认要入库的作品。</p><el-button type="primary" :icon="FolderAdd" :disabled="disabled" @click="addDirectory">添加漫画目录</el-button></template></div>
          </div>

          <footer class="library-footer"><div v-if="selectionHasRemote" class="output-location remote-output-info"><div class="output-icon"><el-icon :size="18"><Connection /></el-icon></div><div class="output-copy"><span>Komga 远端作品</span><span>保存草稿 → 审核 → 同步到 Komga，无需本地打包</span></div></div><div v-else class="output-location"><div class="output-icon"><el-icon :size="18"><FolderOpened /></el-icon></div><div class="output-copy"><span>成品目录</span><button v-if="outputRoot" :title="outputRoot" :disabled="disabled" @click="chooseOutput">{{ outputRoot }}</button><button v-else class="output-unset" :disabled="disabled" @click="chooseOutput">选择一个独立目录…</button></div><el-button link :disabled="disabled" @click="chooseOutput">{{ outputRoot ? '更改' : '选择' }}</el-button></div><el-button type="primary" :icon="Download" :disabled="disabled || !exportIds.length || selectionHasRemote" :title="selectionHasRemote ? '选中包含远端作品，不能生成本地 CBZ' : '生成已审核的本地作品'" @click="prepareExport">生成 CBZ<span v-if="selectedIds.length > 1" class="export-count">{{ selectedIds.length }}</span></el-button></footer>
        </section>

        <DetailsPanel :metadata-rules="metadataRules" :read-only="currentReadOnly" :library="comics" :comic="current" v-model="form" v-model:reviewed="reviewed" :busy="disabled" :dirty="dirty" @save="saveCurrent" @approve="approveCurrent" @scrape="openMetadataScraper" @resolve="resolveRemoteConflict" @error="showError">
          <template #heading-actions>
            <el-button v-if="current" data-testid="editor-layout-toggle" size="small" plain
              :icon="editorExpanded ? Back : FullScreen" :aria-pressed="editorExpanded"
              @click="editorExpanded = !editorExpanded">
              {{ editorExpanded ? '返回列表' : '展开编辑' }}
            </el-button>
          </template>
        </DetailsPanel>
      </div>

      <div v-if="busy && !importVisible && !batchVisible && !komgaSettingsVisible" class="task-bar"><div class="task-indicator"></div><div class="task-copy"><strong>{{ taskTitle }}</strong><span :title="taskMessage">{{ taskMessage }}</span></div><el-button link :disabled="cancelling" @click="cancelTask">{{ cancelling ? '正在取消…' : '取消任务' }}</el-button></div>
      <footer class="workspace-footnote"><span class="connection-dot" :class="{ offline: !available }"></span><span>{{ available ? '本机资料库已连接' : '本机服务不可用' }}</span><span class="footnote-divider">/</span><span>原图保留 · 人工审核 · 无损打包</span><span v-if="dirty" class="unsaved-note">当前作品有未保存修改</span></footer>
    </main>

    <KomgaSettingsDialog v-model="komgaSettingsVisible" :jobs="autoSyncJobs" :continue-review="pendingReviewIds.length > 0" :continue-sync="pendingSyncIds.length > 0" :continue-pull="pendingPull" @state="dialogTask" @saved="komgaConfigured" @jobs="openKomgaJobs" />
    <KomgaJobsDialog v-model="komgaJobsVisible" :jobs="autoSyncJobs" @updated="autoSyncJobs = $event" @state="dialogTask" />
    <KomgaSyncResultDialog v-model="komgaResultVisible" :result="komgaResult" />
    <BatchReviewDialog v-model="reviewPlanVisible" :plan="reviewPlan" :busy="disabled" @confirm="commitReviewSync" />
    <BatchReviewResultDialog v-model="reviewResultVisible" :result="reviewResult" :error="reviewError" />
    <MetadataScrapeDialog v-model="scrapeVisible" :comic="current" :metadata="form" :disabled="disabled || currentReadOnly || !!current?.remote?.conflict"
      :model-configuration="modelConfiguration" @model-settings="openModelSettings" @apply="applyScrapedMetadata" />
    <ModelSettingsDialog v-model="modelSettingsVisible" :configuration="modelConfiguration" @updated="modelConfiguration = $event" />
    <SiteSettingsDialog v-model="siteSettingsVisible" :disabled="agentWorking" @updated="siteConfig = $event" />
    <ImportDialog v-model="importVisible" :authors="authors" :message="taskMessage" @state="dialogTask" @completed="imported" />
    <BatchEditDialog :has-remote="comics.some(comic => batchIds.includes(comic.id) && comic.kind === 'komga')" v-model="batchVisible" :ids="batchIds" :authors="authors" :modes="comics.filter(comic => batchIds.includes(comic.id)).map(comic => collectionMode(comic.metadata))" :ratings="comics.filter(comic => batchIds.includes(comic.id)).map(comic => comic.metadata.age_rating)" @state="dialogTask" @completed="batchSaved" />

    <ExportPlanDialog v-model="planVisible" :plan="plan" :busy="disabled" @confirm="confirmExport" />

    <el-dialog v-model="resultVisible" :title="resultTitle" width="min(700px, 90vw)" destroy-on-close><p class="result-summary">{{ resultSummary }}</p><el-input v-if="resultDetail" :model-value="resultDetail" readonly type="textarea" :rows="10" resize="none" class="result-details" /><template #footer><el-button type="primary" @click="resultVisible = false">知道了</el-button></template></el-dialog>
    <el-dialog v-model="settingsVisible" title="工作区信息" width="min(640px, 90vw)"><div class="workspace-info"><label>本机资料库</label><p>{{ dataDir || '尚未连接' }}</p><label>成品目录</label><p>{{ outputRoot || '尚未设置' }}</p></div><p class="muted-text">高级用法：勾选作品后可导出 Komga 同步清单，用于独立脚本处理。</p><template #footer><el-button data-testid="komga-export-manifest" :disabled="disabled || !exportIds.length || selectionHasRemote" @click="settingsVisible = false; exportKomga()">导出 Komga 同步清单</el-button><el-button type="primary" @click="settingsVisible = false">关闭</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.agent-modes { display: flex; gap: 8px; margin-bottom: 12px; flex: 0 0 auto; }
.agent-modes .el-button { margin: 0; }
.navigation .agent-nav { margin-top: 8px; }
.navigation button:disabled { opacity: .65; cursor: default; }
.library-source-bar { display: flex; align-items: center; gap: 10px; padding: 13px 19px 0; }
.library-source-bar > .el-select { width: 165px; flex-shrink: 0; }
.library-source-bar > span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #8b9b83; font-size: 11px; }
.remote-source-label { color: #5f8c86 !important; }
.remote-output-info .output-copy > span:last-child { color: #6c8676; font-size: 11px; }
.comic-file-icon.komga { color: #5b8d87; background: #edf6f3; }
.comic-auto-sync-label { color: #64856d; font-size: 10px; line-height: 1.6; margin-top: 4px; }
.sidebar-job-count { margin-left: auto; min-width: 19px; padding: 1px 5px; border-radius: 6px; font-size: 10px; background: #365e4d; color: #d4e8d6; }
.library-count-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 5px 8px; }
.library-count-summary .selected-count { margin-left: 0; }
.hidden-selection-count { color: #879580; }
.library-count-summary .el-button { font-size: 11px; }
.library-batch-actions { flex-wrap: wrap; justify-content: flex-end; gap: 8px 12px; }
.version-badges { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.version-badges > span { max-width: 100%; padding: 1px 5px; border: 1px solid #dfe8d7; border-radius: 4px; background: #f2f7ed; color: #6d8662; font-size: 10px; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 1300px) { .library-source-bar { gap: 8px; padding: 12px 14px 0; }.library-source-bar > span { display: none; }.library-source-bar > .el-select { flex: 1; } }
</style>
