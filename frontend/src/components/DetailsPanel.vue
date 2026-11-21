<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount, nextTick } from 'vue'
import { ArrowLeft, ArrowRight, Document, FolderOpened, Picture, Reading, Check, ZoomIn } from '@element-plus/icons-vue'
import type { Comic, Metadata } from '../types'
import { displayTitle, statusLabels, errorMessage, comicGenres, publicationFormats, releaseTypes, releaseMethods, releasePlatforms, colors, textVersions, readingDirections, ageRatings, customTags, classificationValues, collectionMode, collectionModes, aliasLanguages } from '../types'
import { invoke } from '../api'
const props = defineProps<{ comic: Comic | null; modelValue: Metadata | null; busy: boolean; dirty: boolean; reviewed: boolean; library?: Comic[]; readOnly?: boolean; metadataRules?: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: Metadata): void
  (e: 'update:reviewed', value: boolean): void
  (e: 'save'): void
  (e: 'approve'): void
  (e: 'scrape'): void
  (e: 'resolve', resolution: 'keep_local' | 'use_remote'): void
  (e: 'error', message: string): void
}>()
const activeTab = ref('metadata')
const sections = ['basic', 'identity', 'creators', 'classification', 'release', 'reading', 'sources']
const expanded = ref<string[]>(['basic'])
const showFieldHelp = ref(false)
const pageIndex = ref(0)
const pageData = ref('')
const pageName = ref('')
const pageError = ref('')
const pageBusy = ref(false)
const pageViewerVisible = ref(false)
const pageViewerOriginal = ref(false)
const pageViewerCanvas = ref<HTMLDivElement | null>(null)
const xmlText = ref('')
const komgaText = ref('')
const xmlBusy = ref(false)
let pageRequest = 0
let xmlRequest = 0
let xmlTimer: ReturnType<typeof setTimeout> | undefined
const title = computed(() => props.modelValue ? displayTitle(props.modelValue) : '')
const isRemote = computed(() => props.comic?.kind === 'komga')
const editDisabled = computed(() => props.busy || !!props.readOnly)
const rulesVersion = computed(() => props.metadataRules?.trim().replace(/^v\s*/i, '') || '')
const rulesLabel = computed(() => rulesVersion.value ? `元数据规范 v${rulesVersion.value}` : '元数据规范版本未读取')
const warningText = computed(() => (props.comic?.warning || '').replaceAll(
  '元数据规范已升级到 v1.2，原资料已自动备份。',
  '历史升级记录（v1.2）：原资料当时已自动备份。',
).replaceAll(
  '旧内容类型已保留或转换，请按 v1.2 六类内容类型重新核对。',
  '历史分类迁移提示（v1.2）：旧内容类型已保留或转换，请按当前元数据规范核对六类内容类型。',
))
// Only recognized migration/repair notes may collapse. Unknown and live warnings stay visible.
const warningParts = computed(() => (warningText.value.match(/[^。！？\n]+[。！？]?/gu) || []).map(part => part.trim().replace(/^[；;]+/u, '').trim()).filter(Boolean))
const historicalWarningPattern = /^(?:历史升级记录（v1\.2）|历史分类迁移提示（v1\.2）|请核对内容类型、发行分类、色彩和原作后重新审核。$|远端原始快照未改动，旧词保留供核对，实际选择新版值后才会写回。$|作品组或文字版本的标签输出需要更新，请核对后重新审核；源文件、成品及其他元数据保持原样。$|已按父作品目录修正文字版本子目录误识别，请核对后审核。$)/u
function isHistoricalWarning(part: string) {
  if (historicalWarningPattern.test(part)) return true
  if (!part.includes('原资料已自动备份')) return false
  return (part.startsWith('元数据规范已升级') && part.includes('核对作品与系列、年龄分级、译者'))
    || (/^已切换\s*Komga\s*元数据规范/u.test(part) && part.includes('核对独立作品或系列方式、本册编号、年龄分级与译者'))
}
const historicalWarnings = computed(() => warningParts.value.filter(isHistoricalWarning))
const activeWarningText = computed(() => warningParts.value.filter(part => !isHistoricalWarning(part)).join(' '))
const remoteReadOnlyMessage = computed(() => props.comic?.remote?.unavailable === true ? '远端作品当前不可用，仅可查看已缓存资料。请检查 Komga 后重新读取书库。' : '此作品来自其他 Komga 连接，目前仅可查看已缓存资料。请切回对应服务器与书库后编辑、审核或同步。')
const sharedSeriesRating = computed(() => {
  const rating = props.comic?.remote?.sharedMetadata?.ageRating
  return rating === null || rating === undefined ? '未设置' : rating === 0 ? '全年龄' : `${rating}+`
})
const isSeries = computed(() => !!props.modelValue && collectionMode(props.modelValue) === 'series')
const relatedMetadata = computed(() => {
  const current = props.modelValue
  if (!current || !isSeries.value || !current.series.trim()) return []
  const key = current.series.trim().normalize('NFKC').toLocaleLowerCase()
  return [current, ...(props.library || []).filter(comic => comic.id !== props.comic?.id && collectionMode(comic.metadata) === 'series' && (isRemote.value ? comic.kind === 'komga' && comic.remote?.scope === props.comic?.remote?.scope && comic.remote?.seriesId === props.comic?.remote?.seriesId : comic.kind !== 'komga' && comic.metadata.series.trim().normalize('NFKC').toLocaleLowerCase() === key)).map(comic => comic.metadata)]
})
const mixedRatings = computed(() => [...new Set(relatedMetadata.value.map(metadata => metadata.age_rating).filter(value => value !== 'Unknown'))])
const mixedGenres = computed(() => [...new Set(relatedMetadata.value.map(metadata => metadata.genre.trim()).filter(Boolean))])
const isCover = computed(() => props.modelValue?.cover_index === pageIndex.value)
const pageCount = computed(() => Math.max(0, props.comic?.page_count || 0))
const pageNavigationDisabled = computed(() => editDisabled.value || pageBusy.value || !pageCount.value)
const pagePlaceholder = computed(() => pageError.value || (pageBusy.value ? (isRemote.value ? '正在读取 Komga 页面…' : '正在读取本地页面…') : '此页面暂时无法预览'))
const legacyNames = computed(() => {
  const m = props.modelValue
  if (!m || !props.comic?.warning || !/升级|迁移|规范/.test(props.comic.warning)) return []
  return [{ label: '旧版原名', value: m.original_title }, { label: '旧版中文名', value: m.chinese_title }]
    .filter(item => item.value && item.value.trim() !== m.title.trim())
})
const exportedTags = computed(() => props.modelValue ? customTags(props.modelValue) : [])
const displayedTags = computed(() => (props.modelValue?.tags || []).filter(tag => !(props.modelValue?.text_version === '无文字' && tag.trim() === '无字版')))
const duplicatePencillers = computed(() => props.modelValue?.pencillers.filter(name => props.modelValue?.authors.some(author => author.trim() === name.trim())) || [])
const legacyOriginalWorks = computed(() => (props.modelValue?.original_works.length || 0) > 1)
const originalWork = computed(() => props.modelValue?.original_works.length === 1 ? props.modelValue.original_works[0] : '')
const legacyGenre = computed(() => !!props.modelValue?.genre && !comicGenres.includes(props.modelValue.genre))
function update<K extends keyof Metadata>(key: K, value: Metadata[K]) {
  if (!props.modelValue || editDisabled.value) return
  const next = { ...props.modelValue, [key]: value }
  if (key === 'characters') next.characters = classificationValues(value as string[])
  emit('update:modelValue', next)
}
function updateOriginalWork(value: string) {
  update('original_works', value.trim() ? [value] : [])
}
async function loadPage() {
  const id = props.comic?.id
  const index = pageIndex.value
  const request = ++pageRequest
  pageData.value = ''
  pageName.value = ''
  pageError.value = ''
  pageBusy.value = false
  if (!id) return
  if (isRemote.value && (props.readOnly || !props.comic?.page_count)) {
    pageData.value = ''
    pageName.value = ''
    pageBusy.value = false
    pageError.value = props.readOnly ? remoteReadOnlyMessage.value : 'Komga 尚未分析此作品的图片页数，可先在 Komga 中完成媒体分析。'
    return
  }
  pageBusy.value = true
  pageError.value = ''
  try {
    const result = await invoke('page', { id, index })
    if (request !== pageRequest || id !== props.comic?.id) return
    if (!result.dataUrl) throw new Error('此页面暂时无法预览')
    // Decode once before sharing the image between the panel and the large viewer.
    const image = new Image()
    image.src = result.dataUrl
    await image.decode()
    if (request !== pageRequest || id !== props.comic?.id || index !== pageIndex.value) return
    pageData.value = result.dataUrl
    pageName.value = result.name
  } catch (error) {
    if (request !== pageRequest || id !== props.comic?.id || index !== pageIndex.value) return
    pageData.value = ''
    pageError.value = error instanceof DOMException ? '此图片暂时无法显示，请重试；源文件不会修改。' : errorMessage(error)
  } finally { if (request === pageRequest) pageBusy.value = false }
}
async function loadXml() {
  if (!props.comic || !props.modelValue) return
  const request = ++xmlRequest
  xmlBusy.value = true
  try {
    const result = await invoke('xml', {
      id: props.comic.id, metadata: JSON.parse(JSON.stringify(props.modelValue)), pageCount: props.comic.page_count,
    })
    if (request === xmlRequest) {
      xmlText.value = result.xml
      komgaText.value = result.komga ? JSON.stringify(result.komga, null, 2) : ''
    }
  } catch (error) {
    if (request === xmlRequest) {
      xmlText.value = `补全资料后可预览 ComicInfo.xml\n\n${errorMessage(error)}`
      komgaText.value = `补全资料后可预览 Komga 补充资料\n\n${errorMessage(error)}`
    }
  } finally { if (request === xmlRequest) xmlBusy.value = false }
}
function queueXml() {
  clearTimeout(xmlTimer)
  if (['xml', 'komga'].includes(activeTab.value)) xmlTimer = setTimeout(loadXml, 220)
}
function changePage(step: number) {
  if (pageNavigationDisabled.value) return
  setPage(pageIndex.value + step + 1)
}
function setPage(value: number | undefined) {
  if (pageNavigationDisabled.value) return
  const page = Number.isFinite(value) ? Number(value) : 1
  pageIndex.value = Math.max(0, Math.min(Math.max(0, pageCount.value - 1), Math.trunc(page) - 1))
}
function openPageViewer() {
  if (!props.comic) return
  pageViewerOriginal.value = false
  pageViewerVisible.value = true
}
function onViewerKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, [contenteditable="true"]')) return
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    changePage(event.key === 'ArrowLeft' ? -1 : 1)
  }
}
async function openLocation(kind: 'source' | 'output') {
  if (!props.comic) return
  if (isRemote.value && props.readOnly) return
  try {
    if (isRemote.value) await window.comic.openKomgaBook(props.comic.id)
    else await window.comic.openLocation(kind, props.comic.id)
  }
  catch (error) { emit('error', errorMessage(error)) }
}
watch([() => props.comic?.id, () => props.comic?.fingerprint], () => {
  pageViewerVisible.value = false
  activeTab.value = 'metadata'
  expanded.value = ['basic']
  showFieldHelp.value = false
  pageIndex.value = isRemote.value ? 0 : Math.max(0, Math.min(props.modelValue?.cover_index || 0, (props.comic?.page_count || 1) - 1))
  pageData.value = ''
  pageError.value = ''
}, { immediate: true })
watch([() => props.comic?.id, () => props.comic?.fingerprint, () => props.readOnly, pageIndex], loadPage, { immediate: true })
watch(activeTab, (value) => { if (['xml', 'komga'].includes(value)) loadXml() })
watch(() => props.modelValue, queueXml, { deep: true })
watch([pageData, pageViewerOriginal], async () => {
  await nextTick()
  pageViewerCanvas.value?.scrollTo({ top: 0, left: 0 })
})
onBeforeUnmount(() => { clearTimeout(xmlTimer); pageRequest++; xmlRequest++ })
</script>

<template>
  <aside class="details-panel" :class="{ 'details-empty': !comic, 'details-previewing': activeTab === 'preview' }">
    <div class="panel-heading">
      <div class="panel-label"><el-icon><Document /></el-icon><span>作品资料</span><span class="metadata-rules-version" data-testid="metadata-rules-version" :data-rules-version="rulesVersion || undefined" :title="rulesVersion ? '当前软件采用的元数据字段规则，与软件版本及历史迁移记录分别显示' : '等待读取当前元数据规则版本'">{{ rulesLabel }}</span></div>
      <div class="panel-heading-actions"><span v-if="comic" class="status-pill" :class="dirty ? 'edited' : comic.status">{{ dirty ? '未保存' : statusLabels[comic.status] }}</span><span v-else class="quiet-label">未选择</span><slot name="heading-actions" /></div>
    </div>
    <div v-if="!comic || !modelValue" class="details-empty-body">
      <div class="empty-book"><el-icon :size="31"><Reading /></el-icon></div>
      <h3>选择一本，开始整理</h3>
      <p>从漫画列表选择作品<br />在这里编辑资料、预览页面并完成审核</p>
      <div class="empty-steps"><span>编辑资料</span><span class="step-dot">·</span><span>人工确认</span><span class="step-dot">·</span><span>生成 CBZ</span></div>
    </div>
    <template v-else>
      <div class="selected-summary">
        <button type="button" class="mini-cover" data-testid="page-viewer-open-cover" v-loading="pageBusy" @click="openPageViewer" title="打开大图预览" aria-label="打开漫画大图预览">
          <img v-if="pageData" :src="pageData" alt="漫画页面预览" />
          <el-icon v-else :size="24"><Picture /></el-icon>
        </button>
        <div class="selected-summary-copy">
          <h2 :title="title">{{ title }}</h2>
          <p v-if="isSeries && modelValue.series" class="native-title" :title="modelValue.series">{{ modelValue.series }}<span v-if="modelValue.number"> · 第 {{ modelValue.number }} 册</span></p>
          <p v-else class="native-title">独立作品 · One-Shot</p>
          <div class="source-badges"><span :title="comic.remote?.libraryName">{{ isRemote ? `Komga · ${comic.remote?.libraryName || '远端书库'}` : comic.kind === 'cbz' ? 'CBZ' : '图片文件夹' }}</span><i></i><span>{{ comic.page_count }} 页</span></div>
          <button class="source-link" :title="isRemote ? '在当前 Komga 服务器中查看作品' : comic.source" :disabled="editDisabled" @click="openLocation('source')"><el-icon><FolderOpened /></el-icon><span>{{ isRemote ? '在 Komga 中查看' : '查看源位置' }}</span></button>
        </div>
      </div>
      <el-tabs v-model="activeTab" class="details-tabs">
        <el-tab-pane label="基本资料" name="metadata">
          <div class="metadata-scroll" :class="{ 'show-field-help': showFieldHelp }">
            <el-alert v-if="readOnly" :title="remoteReadOnlyMessage" type="info" :closable="false" show-icon class="source-warning" data-testid="komga-inactive-scope" />
            <el-alert v-if="isRemote && (comic.remote?.hasLocalChanges || comic.remote?.pendingSync)" :title="comic.remote?.conflict ? '本机修改与远端更新发生冲突；读取书库已保留本机修改，请先核对提示。' : '有待同步的本机修改；再次读取书库会保留这些修改。'" type="warning" :closable="false" show-icon class="source-warning" data-testid="komga-local-changes" />
            <div v-if="isRemote && comic.remote?.conflict && !readOnly" class="remote-conflict-actions" data-testid="komga-conflict-actions"><el-button data-testid="komga-keep-local" size="small" :disabled="busy" @click="emit('resolve', 'keep_local')">保留本机修改并重新核对</el-button><el-button data-testid="komga-use-remote" size="small" :disabled="busy" @click="emit('resolve', 'use_remote')">采用远端资料</el-button></div>
            <div v-if="warningText || legacyNames.length" class="record-notices" data-testid="metadata-record-warning">
              <el-alert v-if="activeWarningText" :title="activeWarningText" type="warning" :closable="false" show-icon class="source-warning" />
              <details v-if="historicalWarnings.length || legacyNames.length" :key="comic.id" class="historical-notice" data-testid="metadata-history">
                <summary data-testid="metadata-history-toggle"><span>历史迁移资料待核对</span><span class="history-open-label">展开记录</span><span class="history-close-label">收起记录</span></summary>
                <div class="historical-notice-content"><p v-for="(warning, index) in historicalWarnings" :key="index">{{ warning }}</p><div v-if="legacyNames.length" class="legacy-name-note"><strong>旧版名称 · 供核对</strong><p v-for="item in legacyNames" :key="item.label">{{ item.label }}：{{ item.value }}</p></div></div>
              </details>
            </div>
            <div class="metadata-tools"><span>未知内容可留空</span><div><el-button link data-testid="scrape-open" :disabled="editDisabled || !!comic.remote?.conflict" :title="comic.remote?.conflict ? '请先解决远端资料冲突，再读取补充资料' : '从作品链接读取或使用模型整理资料，逐项核对后应用到草稿'" @click="emit('scrape')">读取作品资料</el-button><el-button link data-testid="toggle-metadata-help" :aria-pressed="showFieldHelp" @click="showFieldHelp = !showFieldHelp">{{ showFieldHelp ? '收起说明' : '填写说明' }}</el-button><el-button link data-testid="expand-metadata" :disabled="busy" @click="expanded = expanded.length === sections.length ? ['basic'] : [...sections]">{{ expanded.length === sections.length ? '收起分组' : '展开全部' }}</el-button></div></div>
            <el-form label-position="top" class="metadata-form" :disabled="editDisabled">
              <el-collapse v-model="expanded" class="metadata-sections">
                <el-collapse-item name="basic">
                  <template #title><span class="section-number">01</span><span class="collapse-title" data-testid="section-basic">常用资料</span></template>
                  <div class="editor-field-grid basic-field-grid" data-testid="basic-fields">
                    <el-form-item class="editor-wide" label="作品名称 · Title" required><el-input data-testid="title" :model-value="modelValue.title" @update:model-value="update('title', $event)" placeholder="优先保留作品原始标题" /><p class="metadata-help">保留原文有助于检索，中文标题不是必填项；系列别名请填在下方。</p></el-form-item>
                    <el-form-item label="作品组织方式" required><el-select data-testid="collection-mode" :disabled="isRemote" :model-value="collectionMode(modelValue)" @update:model-value="update('collection_mode', $event)"><el-option v-for="mode in collectionModes" :key="mode.value" :label="mode.label" :value="mode.value" /></el-select><p class="metadata-help">{{ isRemote ? '远端组织方式由 Komga 的目录和书库设置决定，此处编辑资料不会移动作品。' : '独立作品导出到 _oneshots；请在 Komga 书库选项把 One-Shots directory 设为 /_oneshots。多卷系列按系列归入同一目录。' }}</p></el-form-item>
                    <el-form-item label="作者 · Writer" required><el-select data-testid="authors" :title="modelValue.authors.join('、')" :model-value="modelValue.authors" @update:model-value="update('authors', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入姓名后按 Enter" class="full-width" /></el-form-item>
                    <el-form-item label="内容类型 · Genre" required><el-select data-testid="genre" :model-value="modelValue.genre" @update:model-value="update('genre', $event)" clearable placeholder="请选择内容类型"><el-option v-if="legacyGenre" :label="`待核对：${modelValue.genre}`" :value="modelValue.genre" disabled /><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></el-form-item>
                    <el-form-item label="年龄分级 · AgeRating" required><el-select data-testid="age-rating" :model-value="modelValue.age_rating" @update:model-value="update('age_rating', $event)"><el-option v-for="rating in ageRatings" :key="rating.value" :label="rating.label" :value="rating.value" /></el-select></el-form-item>
                    <div v-if="modelValue.age_rating === 'R18+'" class="correction-field" data-testid="correction-field"><div><strong>修正状态</strong><p>{{ modelValue.uncensored ? '导出为“修正状态:无修正”标签' : '默认有修正，不生成额外标签' }}</p></div><el-checkbox data-testid="uncensored" :model-value="modelValue.uncensored" @update:model-value="update('uncensored', Boolean($event))">无修正</el-checkbox></div>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="identity">
                  <template #title><span class="section-number">02</span><span class="collapse-title" data-testid="section-identity">系列与版本</span></template>
                  <div class="editor-field-grid">
                  <el-form-item class="editor-wide" label="系列名称" :required="isSeries && !isRemote"><el-input data-testid="series" :model-value="modelValue.series" @update:model-value="update('series', $event)" :disabled="!isSeries" placeholder="多卷系列填写共同名称" /><p class="metadata-help">{{ isSeries ? '同一系列使用一致名称；原作 IP 请填入“原作”分类。' : 'One-Shot 不向 XML 写入系列、编号和总数；已有本地值保留，切回系列后可继续编辑。' }}</p></el-form-item>
                  <div class="form-grid">
                    <el-form-item label="册编号 · Number"><el-input data-testid="number" :model-value="modelValue.number" @update:model-value="update('number', $event)" :disabled="!isSeries" placeholder="已知系列内编号时填写" /></el-form-item>
                    <el-form-item label="系列总册数 · Count"><el-input data-testid="count" :model-value="modelValue.count" @update:model-value="update('count', $event)" :disabled="!isSeries" placeholder="已知时填写" inputmode="numeric" /></el-form-item>
                  </div>
                  <p class="metadata-help section-help">每个 CBZ 是完整一卷时，用 Number 排序；数字或小数可用于数值排序，不从旧卷号自动推测。</p>
                  <el-form-item label="独立卷号 · Volume"><el-input data-testid="volume" :model-value="modelValue.volume" @update:model-value="update('volume', $event)" :readonly="isRemote" :disabled="isRemote" clearable placeholder="来源明确提供卷号时填写，可留空" /><p class="metadata-help">{{ isRemote ? 'Komga 没有独立 Volume 编辑接口，此处只保留本机值。' : '与 Number 含义不同，不自动重复填写。Komga 专用库应关闭“将卷号附加到系列标题”。' }}</p></el-form-item>
                  <el-form-item label="系列别名"><el-input data-testid="localized-series" :model-value="modelValue.localized_series" @update:model-value="update('localized_series', $event)" :disabled="!isSeries" placeholder="系列的另一种语言名称" /><p class="metadata-help">仅多卷系列使用。别名与标签保存在本机，供 Komga alternateTitles API 使用，不写入 ComicInfo.xml。导入 CBZ 不会自动创建此别名。</p></el-form-item>
                  <el-form-item label="别名标签" :required="isSeries && !!modelValue.localized_series.trim()"><el-select data-testid="localized-series-label" :model-value="modelValue.localized_series_label" @update:model-value="update('localized_series_label', $event)" :disabled="!isSeries" filterable allow-create clearable placeholder="例如 zh-Hans、zh-Hant 或原名"><el-option v-for="language in aliasLanguages" :key="language.value" :label="language.label" :value="language.value" /></el-select><p class="metadata-help">用于标识此别名的语言或用途，与当前卷册的语言独立。</p><p v-if="isRemote && (comic.remote?.sharedMetadata?.alternateTitles?.length || 0) > 1" class="metadata-help">远端另有其他系列别名；未编辑的别名会保留。</p></el-form-item>
                  <div class="version-link-fields" data-testid="version-association">
                    <h4>版本关联 <span>可选</span></h4>
                    <p class="metadata-help">仅在需要关联同一作品的不同版本时填写。作品组不是 Series，文字版本不是卷号；不会修改真实系列、编号、语言、译者或色彩。</p>
                    <el-form-item class="editor-wide" label="作品组"><el-input data-testid="work-group" :model-value="modelValue.work_group || ''" @update:model-value="update('work_group', $event)" clearable placeholder="同一作品的各版本使用完全一致的组名" /><p class="metadata-help">可用稳定的作品原名；同名但不同作者、社团的作品请补充标识。没有版本关联需求时留空。</p></el-form-item>
                    <el-form-item label="文字版本"><el-select data-testid="text-version" :model-value="modelValue.text_version || ''" @update:model-value="update('text_version', $event)" clearable placeholder="无版本区分需求时留空"><el-option v-for="version in textVersions" :key="version" :label="version" :value="version" /></el-select><p class="metadata-help">区分是否保留对白、旁白等画面文字。日文原版和中文汉化版都可有文字；不默认选择，也不跟随其他字段变化。</p></el-form-item>
                  </div>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="creators">
                  <template #title><span class="section-number">03</span><span class="collapse-title" data-testid="section-creators">绘画、翻译与出版</span></template>
                  <div class="editor-field-grid">
                  <el-form-item label="独立绘画者"><el-select data-testid="pencillers" :model-value="modelValue.pencillers" @update:model-value="update('pencillers', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="与作者不同时填写" class="full-width" /><p class="metadata-help">作者兼任绘画时只填写作者。同名人员不会重复导出。</p><p v-if="duplicatePencillers.length" class="metadata-inline-warning">与作者重复：{{ duplicatePencillers.join('、') }}</p></el-form-item>
                  <el-form-item label="译者 / 汉化组"><el-input data-testid="translator" :model-value="modelValue.translator" @update:model-value="update('translator', $event)" placeholder="实际译者或团队名称" /><p class="metadata-help">原创版本留空；已翻译版本请核实实际译者，未知信息留待审核。</p></el-form-item>
                  <el-form-item label="出版方 / 发行社团"><el-input data-testid="publisher" :model-value="modelValue.publisher" @update:model-value="update('publisher', $event)" placeholder="出版社、发行社团或机构的原始名称" /><p class="metadata-help">Komga 中为系列共享信息，修改后需选全系列同步。</p></el-form-item>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="classification">
                  <template #title><span class="section-number">04</span><span class="collapse-title" data-testid="section-classification">色彩与分类</span></template>
                  <div class="editor-field-grid">
                  <p v-if="legacyGenre" class="metadata-inline-warning" data-testid="legacy-genre-warning">旧内容类型“{{ modelValue.genre }}”不属于规范中的六类。请核对后选择；保存其他字段时保留此旧值。</p>
                  <p v-if="isRemote" class="metadata-help section-help" data-testid="komga-remote-field-origin">远端的出版方、语言、总数和阅读方向是系列共享资料。内容类型与分级读取逐册辅助实体；没有逐册记录时保留空白或未确认，不把系列值当作本册值。</p>
                  <p v-if="isRemote && comic.remote?.sharedMetadata" class="metadata-help metadata-evidence section-help" data-testid="komga-shared-series">系列共享值（仅供参考）：年龄 {{ sharedSeriesRating }}；类型 {{ comic.remote.sharedMetadata.genres?.join('、') || '未设置' }}。</p>
                  <p class="metadata-help section-help" data-testid="komga-series-aggregation">Komga 的内容类型和年龄分级属于系列：同系列类型合并，年龄取最高值。本机保留每本作品各自的资料；逐册筛选需要另通过 API 建立实体。</p>
                  <p v-if="mixedRatings.length > 1 || mixedGenres.length > 1" class="metadata-inline-warning" data-testid="komga-mixed-series">本机同系列检测到{{ mixedRatings.length > 1 ? '不同年龄分级' : '' }}{{ mixedRatings.length > 1 && mixedGenres.length > 1 ? '、' : '' }}{{ mixedGenres.length > 1 ? '多种内容类型' : '' }}；Komga 系列展示会汇总这些值。</p>
                  <el-form-item label="色彩"><el-select data-testid="color" :model-value="modelValue.color" @update:model-value="update('color', $event)" clearable placeholder="未确认可留空"><el-option v-for="color in colors" :key="color" :label="color" :value="color" /></el-select><p class="metadata-help">以正文判断，封面不参与；正文兼有黑白和彩色页时选“部分彩色”。</p></el-form-item>
                  <el-form-item class="editor-wide" label="题材与属性标签 · Tags" required><el-select data-testid="tags" :model-value="displayedTags" @update:model-value="update('tags', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="如恋爱、奇幻、日常，按 Enter 添加" class="full-width" /><p class="metadata-help">普通标签与下方已确认分类生成的标签合计至少一条。未知信息保留草稿，不填写“无”来凑数。文字版本为“无文字”时不重复显示普通“无字版”。</p></el-form-item>
                  <el-form-item label="原作"><el-input data-testid="original-work" :model-value="originalWork" @update:model-value="updateOriginalWork" clearable :placeholder="legacyOriginalWorks ? '填写后将旧列表替换为一个原作' : '如东方Project；没有或无法确认时留空'" /><p v-if="legacyOriginalWorks" class="metadata-inline-warning" data-testid="legacy-original-works-warning">旧资料包含多个原作：{{ modelValue.original_works.join('、') }}。请核对并改为一个；修改其他字段不会截断此列表。<el-button link data-testid="clear-legacy-original-works" :disabled="editDisabled" @click="update('original_works', [])">清空旧原作列表</el-button></p></el-form-item>
                  <el-form-item class="editor-wide" label="角色"><el-select data-testid="characters" :model-value="modelValue.characters" @update:model-value="update('characters', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="每个角色分别添加，可用顿号分隔" class="full-width" /><p class="metadata-help">写入 Characters，供 Komga 1.29+ 自动导入角色实体；同时保留“角色:”标签副本。</p></el-form-item>
                  <div class="custom-tag-preview" data-testid="custom-tag-preview"><span>已确认分类生成的可移植标签，与普通标签一同保存</span><div v-if="exportedTags.length"><el-tag v-for="tag in exportedTags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div><p v-else>填写适用的原作、角色、版本关联、发行、平台或色彩信息后显示。</p></div>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="release">
                  <template #title><span class="section-number">05</span><span class="collapse-title" data-testid="section-release">发行与平台</span></template>
                  <div class="editor-field-grid">
                  <div class="form-grid">
                    <el-form-item label="发行性质"><el-select data-testid="release-type" :model-value="modelValue.release_type" @update:model-value="update('release_type', $event)" clearable placeholder="未知可留空"><el-option v-for="type in releaseTypes" :key="type" :label="type" :value="type" /></el-select></el-form-item>
                    <el-form-item label="出版形态"><el-select data-testid="publication-format" :model-value="modelValue.publication_format" @update:model-value="update('publication_format', $event)" clearable filterable allow-create placeholder="选择或输入出版形态"><el-option v-for="format in publicationFormats" :key="format" :label="format" :value="format" /></el-select></el-form-item>
                  </div>
                  <el-form-item label="发布方式"><el-select data-testid="release-methods" :model-value="modelValue.release_methods" @update:model-value="update('release_methods', $event)" multiple clearable placeholder="可多选，来源不明时留空" class="full-width"><el-option v-for="method in releaseMethods" :key="method" :label="method" :value="method" /></el-select></el-form-item>
                  <el-form-item label="发布平台"><el-select data-testid="release-platforms" :model-value="modelValue.release_platforms" @update:model-value="update('release_platforms', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" clearable placeholder="可多选，也可输入新平台后按 Enter" class="full-width"><el-option v-for="platform in releasePlatforms" :key="platform" :label="platform" :value="platform" /></el-select><p class="metadata-help">只记录实际发布、销售或订阅平台，不由平台推断发行性质与发布方式。</p></el-form-item>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="reading">
                  <template #title><span class="section-number">06</span><span class="collapse-title" data-testid="section-reading">日期与阅读</span></template>
                  <div class="editor-field-grid">
                  <label class="date-group-label">发售日期</label>
                  <div class="metadata-triple-grid">
                    <el-form-item label="年"><el-input data-testid="year" :model-value="modelValue.year" @update:model-value="update('year', $event)" placeholder="如 2026" maxlength="4" inputmode="numeric" /></el-form-item>
                    <el-form-item label="月"><el-select data-testid="month" :model-value="modelValue.month" @update:model-value="update('month', $event)" clearable placeholder="未知"><el-option v-for="month in 12" :key="month" :label="`${month} 月`" :value="String(month)" /></el-select></el-form-item>
                    <el-form-item label="日"><el-select data-testid="day" :model-value="modelValue.day" @update:model-value="update('day', $event)" clearable placeholder="未知"><el-option v-for="day in 31" :key="day" :label="`${day} 日`" :value="String(day)" /></el-select></el-form-item>
                  </div>
                  <p class="metadata-help section-help">只填写已知部分；Komga 可能把缺失的月、日显示为 1 月、1 日，本机不会补写未知日期。</p>
                  <el-form-item label="当前版本语言"><el-select data-testid="language" :model-value="modelValue.language" @update:model-value="update('language', $event)" filterable allow-create clearable placeholder="未知或不适用时留空"><el-option label="日文 · ja" value="ja" /><el-option label="简体中文 · zh-Hans" value="zh-Hans" /><el-option label="繁体中文 · zh-Hant" value="zh-Hant" /><el-option label="英文 · en" value="en" /></el-select><p class="metadata-help">填写正文语言代码；Komga 中为系列共享信息。</p></el-form-item>
                  <el-form-item label="ComicInfo 漫画属性 · Manga"><el-select data-testid="manga" :model-value="modelValue.manga" @update:model-value="update('manga', $event)" :disabled="isRemote"><el-option label="未指定" value="Unknown" /><el-option label="漫画 · 不指定方向" value="Yes" /><el-option label="从左向右" value="No" /><el-option label="从右向左" value="YesAndRightToLeft" /></el-select><p class="metadata-help">{{ isRemote ? '远端没有独立 Manga 字段，请使用下方 Komga 阅读方向。' : '保留 ComicInfo 原生属性，不表达页面长宽，也不自动改写下方平台方向。' }}</p></el-form-item>
                  <el-form-item label="Komga 阅读方向"><el-select data-testid="reading-direction" :model-value="modelValue.reading_direction" @update:model-value="update('reading_direction', $event)" clearable placeholder="未指定"><el-option v-for="direction in readingDirections" :key="direction.value" :label="direction.label" :value="direction.value" /></el-select><p class="metadata-help">作为 Komga 系列共享字段同步。可选择纵向或 Webtoon；清空表示未指定方向，不新增页面版式分类。</p></el-form-item>
                  <div class="page-count-field" data-testid="page-count"><div><strong>图片页数</strong><p>{{ isRemote ? '来自 Komga 的媒体分析，只读显示' : '本机按图片计算；Komga 也会分析 CBZ 实际页数' }}</p></div><span>{{ comic.page_count }}<small> 页</small></span></div>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="sources">
                  <template #title><span class="section-number">07</span><span class="collapse-title" data-testid="section-sources">简介与来源</span></template>
                  <div class="editor-field-grid">
                  <el-form-item class="editor-wide" label="作品简介"><el-input data-testid="summary" :model-value="modelValue.summary" @update:model-value="update('summary', $event)" type="textarea" :rows="5" resize="vertical" placeholder="填写完整的官方简介或可靠译文" /></el-form-item>
                  <el-form-item class="editor-wide" label="作品来源链接"><el-input data-testid="web" :model-value="modelValue.web.join('\n')" @update:model-value="update('web', $event.split('\n'))" type="textarea" :rows="3" resize="vertical" placeholder="每行一个作品详情页链接" /><p class="metadata-help">可填写多个商品详情页或官网链接。</p></el-form-item>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </el-form>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="isRemote ? '远端页面' : '页面预览'" name="preview">
          <div class="page-preview-scroll" data-testid="page-preview">
            <div class="page-preview-toolbar" data-testid="page-toolbar">
              <div class="page-navigation">
                <el-button data-testid="page-previous" :icon="ArrowLeft" circle size="small" :disabled="pageNavigationDisabled || pageIndex === 0" @click="changePage(-1)" aria-label="上一页" />
                <div class="page-counter"><el-input-number data-testid="page-number" :model-value="pageIndex + 1" @update:model-value="setPage" :min="1" :max="Math.max(1, pageCount)" :controls="false" size="small" :disabled="pageNavigationDisabled" aria-label="预览页码" /><span>/ {{ pageCount }}</span></div>
                <el-button data-testid="page-next" :icon="ArrowRight" circle size="small" :disabled="pageNavigationDisabled || pageIndex + 1 >= pageCount" @click="changePage(1)" aria-label="下一页" />
              </div>
              <el-button data-testid="page-viewer-open" :icon="ZoomIn" size="small" @click="openPageViewer">大图预览</el-button>
            </div>
            <div class="large-page" v-loading="pageBusy" :aria-busy="pageBusy">
              <button v-if="pageData" class="page-image-open" type="button" @click="openPageViewer" aria-label="打开当前页面大图预览"><img data-testid="page-preview-image" :src="pageData" :alt="`${title} · 第 ${pageIndex + 1} 页`" draggable="false" /></button>
              <div v-else class="page-placeholder" role="status"><el-icon :size="34"><Picture /></el-icon><span>{{ pagePlaceholder }}</span><el-button v-if="pageError && !pageBusy && !readOnly" size="small" @click="loadPage">重新读取</el-button></div>
            </div>
            <div class="preview-page-info"><p class="page-filename" :title="pageName">{{ pageName || `第 ${pageIndex + 1} 页` }}</p><el-button v-if="!isRemote" data-testid="page-set-cover" :type="isCover ? 'success' : 'default'" plain size="small" :icon="isCover ? Check : Picture" :disabled="editDisabled || pageBusy || !pageData" @click="update('cover_index', pageIndex)">{{ isCover ? '当前封面' : '设为封面' }}</el-button></div>
            <p class="preview-note">{{ isRemote ? '页面来自当前 Komga 连接，图片和封面保留在服务器。' : '封面标记写入 ComicInfo；Komga 使用首张图片，生成时保留原页序。' }}</p>
          </div>
        </el-tab-pane>
        <el-tab-pane v-if="!isRemote" label="ComicInfo.xml" name="xml"><div class="xml-tab" v-loading="xmlBusy"><p>以下内容写入成品 CBZ 根目录。自定义分类通过 Tags 携带；Komga 实体索引与系列别名还需 API 同步。</p><pre>{{ xmlText || '正在生成 XML 预览…' }}</pre></div></el-tab-pane>
        <el-tab-pane :label="isRemote ? 'Komga 待同步字段' : 'Komga 补充资料'" name="komga"><div class="xml-tab" v-loading="xmlBusy"><p>{{ isRemote ? '以下仅预览相对远端基准修改的字段。保存草稿只保存在本机，审核后点击“同步到 Komga”才会回写；没有修改时不发送更新。' : '以下实体和系列别名需通过 Komga API 写入。这里是待同步资料预览，保存本机资料或生成 CBZ 不代表已同步到服务器。' }}</p><pre data-testid="komga-payload">{{ komgaText || '正在生成 Komga 补充资料预览…' }}</pre></div></el-tab-pane>
      </el-tabs>
      <footer class="review-footer">
        <el-checkbox :model-value="reviewed" @update:model-value="emit('update:reviewed', Boolean($event))" :disabled="editDisabled" class="review-checkbox">{{ isRemote ? '已逐本核对标题、作者、类型、分级与标签' : '已核对资料、分级、页序与封面' }}</el-checkbox>
        <div class="review-actions"><el-button :disabled="editDisabled || !dirty" @click="emit('save')">保存草稿</el-button><el-button type="primary" :icon="Check" :disabled="editDisabled || !reviewed || !!comic.remote?.conflict" @click="emit('approve')">审核通过</el-button></div>
        <div class="review-bottom"><span>{{ dirty ? '修改后需重新审核' : '资料保存在本机' }}</span><el-button v-if="!isRemote" link :icon="FolderOpened" :disabled="busy || !comic.output" @click="openLocation('output')">打开成品</el-button><span v-else>{{ (comic.remote?.hasLocalChanges || comic.remote?.pendingSync) ? '等待同步到 Komga' : '远端资料已缓存' }}</span></div>
      </footer>
      <el-dialog v-model="pageViewerVisible" class="page-viewer-dialog" width="calc(100vw - 32px)" top="16px" append-to-body destroy-on-close :close-on-click-modal="false" :close-on-press-escape="true">
        <template #header="{ titleId }"><div class="page-viewer-heading"><h2 :id="titleId" data-testid="page-viewer-title" :title="title">{{ title }}</h2><span>第 {{ pageIndex + 1 }} 页 / 共 {{ pageCount }} 页</span></div></template>
        <div class="page-viewer-body" data-testid="page-viewer" @keydown="onViewerKeydown">
          <div ref="pageViewerCanvas" class="page-viewer-canvas" :class="{ 'is-original': pageViewerOriginal }" v-loading="pageBusy" :aria-busy="pageBusy">
            <img v-if="pageData" data-testid="page-viewer-image" :src="pageData" :alt="`${title} · 第 ${pageIndex + 1} 页`" draggable="false" />
            <div v-else class="page-placeholder" role="status"><el-icon :size="38"><Picture /></el-icon><span>{{ pagePlaceholder }}</span><el-button v-if="pageError && !pageBusy && !readOnly" size="small" @click="loadPage">重新读取</el-button></div>
          </div>
        </div>
        <template #footer>
          <div class="page-viewer-tools" @keydown="onViewerKeydown">
            <p class="page-filename" :title="pageName">{{ pageName }}</p>
            <div class="page-navigation">
              <el-button data-testid="viewer-page-previous" :icon="ArrowLeft" size="small" :disabled="pageNavigationDisabled || pageIndex === 0" @click="changePage(-1)" aria-label="大图上一页">上一页</el-button>
              <div class="page-counter"><el-input-number data-testid="viewer-page-number" :model-value="pageIndex + 1" @update:model-value="setPage" :min="1" :max="Math.max(1, pageCount)" :controls="false" size="small" :disabled="pageNavigationDisabled" aria-label="大图页码" /><span>/ {{ pageCount }}</span></div>
              <el-button data-testid="viewer-page-next" :icon="ArrowRight" size="small" :disabled="pageNavigationDisabled || pageIndex + 1 >= pageCount" @click="changePage(1)" aria-label="大图下一页">下一页</el-button>
            </div>
            <el-button data-testid="page-viewer-size" size="small" :aria-pressed="pageViewerOriginal" :disabled="!pageData || pageBusy" @click="pageViewerOriginal = !pageViewerOriginal">{{ pageViewerOriginal ? '适应窗口' : '原始大小' }}</el-button>
            <el-button v-if="!isRemote" data-testid="viewer-set-cover" :type="isCover ? 'success' : 'default'" plain size="small" :icon="isCover ? Check : Picture" :disabled="editDisabled || pageBusy || !pageData" @click="update('cover_index', pageIndex)">{{ isCover ? '当前封面' : '设为封面' }}</el-button>
            <el-button data-testid="page-viewer-close" size="small" @click="pageViewerVisible = false">关闭 · Esc</el-button>
          </div>
        </template>
      </el-dialog>
    </template>
  </aside>
</template>

<style scoped>
.details-panel { container: comic-details / inline-size; }
.panel-heading { min-height: 44px; padding: 10px 16px; gap: 8px; }
.panel-label { flex-wrap: wrap; min-width: 0; gap: 6px; }
.panel-heading-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 7px; }
.panel-heading-actions :deep(.el-button) { margin: 0; }
.selected-summary { gap: 11px; padding: 11px 16px; align-items: center; }
.mini-cover { width: 48px; height: 64px; }
.selected-summary-copy { flex: 1; padding-top: 0; }
.selected-summary-copy h2 { font-size: 14px; line-height: 1.4; margin-bottom: 2px; }
.native-title { margin-bottom: 3px; line-height: 1.4; }
.source-badges { gap: 5px; }
.source-link { margin-top: 3px; }
.details-tabs { padding: 0 16px; }
.details-tabs :deep(.el-tabs__item) { height: 35px; }
.metadata-scroll { width: 100%; max-width: 1080px; margin: 0 auto; padding-top: 10px; }
.metadata-scroll:not(.show-field-help) .metadata-help:not(.metadata-evidence) { display: none; }
.metadata-tools { margin-bottom: 3px; gap: 7px; flex-wrap: wrap; }
.metadata-tools > span { font-size: 10px; }
.metadata-tools > div { display: flex; align-items: center; gap: 11px; flex-shrink: 0; margin-left: auto; }
.metadata-tools :deep(.el-button + .el-button) { margin-left: 0; }
.editor-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; align-items: start; }
.editor-field-grid > :deep(.el-form-item) { min-width: 0; margin-bottom: 12px; }
.editor-field-grid > .editor-wide, .editor-field-grid > .form-grid, .editor-field-grid > .version-link-fields,
.editor-field-grid > .metadata-help, .editor-field-grid > .metadata-inline-warning,
.editor-field-grid > .custom-tag-preview, .editor-field-grid > .correction-field,
.editor-field-grid > .page-count-field, .editor-field-grid > .date-group-label,
.editor-field-grid > .metadata-triple-grid { grid-column: 1 / -1; }
.editor-field-grid > .form-grid { padding-top: 0; gap: 0 12px; }
.metadata-form :deep(.el-form-item__label) { margin-bottom: 5px !important; }
.metadata-form :deep(.el-select__selected-item) { min-width: 0; max-width: 100%; }
.metadata-form :deep(.el-select__tags-text) { overflow: hidden; text-overflow: ellipsis; }
.metadata-form :deep(.el-tag) { max-width: 100%; }
.metadata-sections :deep(.el-collapse-item__header) { height: 38px; padding: 0 8px; }
.metadata-sections :deep(.el-collapse-item__title) { display: flex; flex: 1; align-items: center; gap: 10px; min-width: 0; }
.metadata-sections :deep(.collapse-title) { margin: 0; line-height: 1.4; }
.metadata-sections :deep(.el-collapse-item__content) { padding: 8px 8px 2px; }
.metadata-sections :deep(.section-number) { height: 21px; width: 21px; flex: 0 0 21px; }
.metadata-help { margin-top: 5px; line-height: 1.5; }
.metadata-help.section-help { margin: 0 0 12px; }
.source-warning { margin-bottom: 8px; }
.record-notices { margin-bottom: 8px; }
.record-notices > .source-warning:last-child { margin-bottom: 0; }
.historical-notice { border: 1px solid #e9e2d1; border-radius: 6px; background: #fcfaf4; overflow: hidden; }
.historical-notice summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 7px 10px; color: #92764e; font-size: 11px; line-height: 1.5; cursor: pointer; list-style: none; }
.historical-notice summary::-webkit-details-marker { display: none; }
.historical-notice summary:focus-visible { outline: 2px solid #91ad83; outline-offset: -2px; }
.history-open-label, .history-close-label { flex-shrink: 0; color: #778d67; }
.history-close-label, .historical-notice[open] .history-open-label { display: none; }
.historical-notice[open] .history-close-label { display: inline; }
.historical-notice-content { border-top: 1px solid #eee6d5; padding: 2px 10px 9px; color: #927d60; font-size: 11px; line-height: 1.65; overflow-wrap: anywhere; }
.historical-notice-content > p { margin: 6px 0 0; }
.historical-notice .legacy-name-note { margin: 7px 0 0; padding: 7px 9px; }
.review-footer { padding: 9px 16px 7px; }
.review-checkbox { min-height: 18px; margin-bottom: 6px; }
.review-checkbox :deep(.el-checkbox__label) { font-size: 11px; line-height: 1.5; }
.review-actions { gap: 8px; }
.review-actions :deep(.el-button) { height: 31px; }
.review-bottom { margin-top: 3px; min-height: 18px; }
.review-bottom :deep(.el-button) { height: 22px; padding-top: 0; padding-bottom: 0; }
.metadata-rules-version { margin-left: 2px; color: #7a8f72; font-size: 10px; font-weight: 400; white-space: nowrap; }
.remote-conflict-actions { display: flex; flex-wrap: wrap; gap: 7px; margin: -5px 0 15px; }
.remote-conflict-actions .el-button + .el-button { margin-left: 0; }
.source-badges > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.version-link-fields { margin-top: 17px; padding-top: 14px; border-top: 1px solid #e3eadf; }
.version-link-fields h4 { margin: 0 0 8px; color: #526d48; font-size: 12px; font-weight: 600; }
.version-link-fields h4 span { margin-left: 7px; color: #91a086; font-size: 10px; font-weight: 400; }
.version-link-fields > .metadata-help { margin-bottom: 13px; }
.version-link-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
.version-link-fields > h4, .version-link-fields > .metadata-help, .version-link-fields > .editor-wide { grid-column: 1 / -1; }
.version-link-fields > :deep(.el-form-item) { min-width: 0; margin-bottom: 12px; }
@container comic-details (max-width: 379px) {
  .editor-field-grid, .version-link-fields { grid-template-columns: minmax(0, 1fr); }
  .panel-heading, .selected-summary { padding-left: 12px; padding-right: 12px; }
  .details-tabs { padding-left: 12px; padding-right: 12px; }
  .review-footer { padding-left: 12px; padding-right: 12px; }
  .metadata-rules-version { font-size: 9px; }
}
@container comic-details (min-width: 640px) {
  .selected-summary { padding-top: 9px; padding-bottom: 9px; }
  .metadata-scroll { padding-left: 2px; padding-right: 7px; }
  .review-footer { display: grid; grid-template-columns: minmax(0, 1fr) 242px; gap: 2px 18px; align-items: center; }
  .review-checkbox { margin: 0; }
  .review-bottom { grid-column: 1 / -1; margin-top: 0; }
}
.mini-cover { appearance: none; padding: 0; }
.mini-cover img { object-fit: contain; }
.details-previewing .selected-summary { display: block; padding-top: 7px; padding-bottom: 5px; }
.details-previewing .selected-summary-copy { padding-top: 0; }
.details-previewing .selected-summary-copy h2 { margin: 0; font-size: 12px; line-height: 1.5; }
.details-previewing .mini-cover, .details-previewing .native-title, .details-previewing .source-badges, .details-previewing .source-link { display: none; }
.mini-cover:focus-visible, .page-image-open:focus-visible { outline: 2px solid #579178; outline-offset: 2px; }
.page-preview-scroll { display: flex; flex-direction: column; gap: 8px; min-height: 0; overflow: hidden; padding: 10px 0 8px; }
.page-preview-toolbar { flex: 0 0 auto; gap: 8px; flex-wrap: wrap; margin: 0; }
.page-preview-toolbar > .el-button { width: auto; padding: 0 9px; }
.page-navigation { display: flex; flex: 0 0 auto; align-items: center; justify-content: center; gap: 8px; }
.page-navigation .el-button + .el-button { margin-left: 0; }
.page-counter { gap: 6px; color: #718477; font-size: 11px; white-space: nowrap; }
.page-counter :deep(.el-input-number) { width: 54px; }
.large-page { display: flex; flex: 1 1 0; align-items: center; justify-content: center; min-height: 0; width: 100%; background: #edf1eb; }
.page-image-open { display: block; width: 100%; height: 100%; min-width: 0; min-height: 0; border: 0; padding: 0; background: transparent; cursor: zoom-in; }
.page-image-open img, .page-viewer-canvas > img { display: block; width: 100%; height: 100%; min-width: 0; min-height: 0; object-fit: contain; }
.large-page .page-placeholder, .page-viewer-canvas .page-placeholder { width: 100%; min-height: 0; max-height: 100%; overflow: auto; box-sizing: border-box; gap: 10px; padding: 16px; text-align: center; color: #7b8f77; }
.page-placeholder .el-button { flex: 0 0 auto; }
.preview-page-info { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; min-width: 0; }
.preview-page-info .page-filename { flex: 1; min-width: 0; margin: 0; font-size: 10px; }
.preview-page-info .el-button { flex: 0 0 auto; margin-left: 0; }
.preview-note { flex: 0 0 auto; margin: 0; color: #7e8e7b; font-size: 10px; line-height: 1.5; }
:global(.page-viewer-dialog) { display: flex; flex-direction: column; height: calc(100vh - 32px); height: calc(100dvh - 32px); max-width: none; max-height: calc(100dvh - 32px); padding: 0; overflow: hidden; border-radius: 12px; }
:global(.page-viewer-dialog .el-dialog__header) { flex: 0 0 auto; margin: 0; padding: 12px 52px 11px 16px; border-bottom: 1px solid #e5eae2; }
:global(.page-viewer-dialog .el-dialog__headerbtn) { top: 8px; right: 8px; }
:global(.page-viewer-dialog .el-dialog__body) { display: flex; flex: 1 1 0; min-height: 0; overflow: hidden; padding: 10px 16px; }
:global(.page-viewer-dialog .el-dialog__footer) { flex: 0 0 auto; padding: 10px 16px 12px; border-top: 1px solid #e5eae2; }
.page-viewer-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 16px; min-width: 0; }
.page-viewer-heading h2 { flex: 1 1 240px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0; font-size: 15px; font-weight: 600; line-height: 1.6; color: #3c5741; }
.page-viewer-heading > span { flex: 0 0 auto; font-size: 11px; color: #7b8e77; }
.page-viewer-body { display: flex; flex: 1; min-width: 0; min-height: 0; }
.page-viewer-canvas { display: flex; flex: 1 1 0; justify-content: center; align-items: center; min-width: 0; min-height: 0; overflow: hidden; border: 1px solid #e1e7dd; border-radius: 6px; background: #edf1eb; }
.page-viewer-canvas.is-original { display: block; overflow: auto; }
.page-viewer-canvas.is-original > img { width: auto; height: auto; max-width: none; max-height: none; margin: 0 auto; }
.page-viewer-tools { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 8px 12px; min-width: 0; }
.page-viewer-tools > .page-filename { flex: 1 1 120px; min-width: 0; margin: 0 auto 0 0; text-align: left; font-size: 10px; }
.page-viewer-tools > .el-button { margin-left: 0; }
@media (max-width: 640px) {
  .page-viewer-tools > .page-filename { flex-basis: 100%; }
  .page-viewer-tools .page-navigation { flex: 1 1 100%; }
  :global(.page-viewer-dialog .el-dialog__body) { padding: 8px; }
  :global(.page-viewer-dialog .el-dialog__footer) { padding: 8px; }
}
</style>
