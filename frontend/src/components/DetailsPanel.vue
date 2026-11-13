<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ArrowLeft, ArrowRight, Document, FolderOpened, Picture, Reading, Check, ZoomIn } from '@element-plus/icons-vue'
import type { Comic, Metadata } from '../types'
import { displayTitle, statusLabels, errorMessage, comicGenres, publicationFormats, releaseTypes, releaseMethods, releasePlatforms, colors, textVersions, readingDirections, ageRatings, customTags, classificationValues, collectionMode, collectionModes, aliasLanguages } from '../types'
import { invoke } from '../api'
const props = defineProps<{ comic: Comic | null; modelValue: Metadata | null; busy: boolean; dirty: boolean; reviewed: boolean; library?: Comic[]; readOnly?: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: Metadata): void
  (e: 'update:reviewed', value: boolean): void
  (e: 'save'): void
  (e: 'approve'): void
  (e: 'resolve', resolution: 'keep_local' | 'use_remote'): void
  (e: 'error', message: string): void
}>()
const activeTab = ref('metadata')
const sections = ['identity', 'creators', 'classification', 'release', 'reading', 'sources']
const expanded = ref<string[]>(['identity', 'creators'])
const pageIndex = ref(0)
const pageData = ref('')
const pageName = ref('')
const pageError = ref('')
const pageBusy = ref(false)
const xmlText = ref('')
const komgaText = ref('')
const xmlBusy = ref(false)
let pageRequest = 0
let xmlRequest = 0
let xmlTimer: ReturnType<typeof setTimeout> | undefined
const title = computed(() => props.modelValue ? displayTitle(props.modelValue) : '')
const isRemote = computed(() => props.comic?.kind === 'komga')
const editDisabled = computed(() => props.busy || !!props.readOnly)
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
    pageData.value = result.dataUrl
    pageName.value = result.name
  } catch (error) {
    if (request !== pageRequest) return
    pageData.value = ''
    pageError.value = errorMessage(error)
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
  const max = (props.comic?.page_count || 1) - 1
  pageIndex.value = Math.max(0, Math.min(max, pageIndex.value + step))
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
  activeTab.value = 'metadata'
  expanded.value = ['identity', 'creators']
  pageIndex.value = isRemote.value ? 0 : Math.max(0, Math.min(props.modelValue?.cover_index || 0, (props.comic?.page_count || 1) - 1))
  pageData.value = ''
  pageError.value = ''
}, { immediate: true })
watch([() => props.comic?.id, () => props.comic?.fingerprint, () => props.readOnly, pageIndex], loadPage, { immediate: true })
watch(activeTab, (value) => { if (['xml', 'komga'].includes(value)) loadXml() })
watch(() => props.modelValue, queueXml, { deep: true })
onBeforeUnmount(() => { clearTimeout(xmlTimer); pageRequest++; xmlRequest++ })
</script>

<template>
  <aside class="details-panel" :class="{ 'details-empty': !comic }">
    <div class="panel-heading">
      <div class="panel-label"><el-icon><Document /></el-icon><span>作品资料</span></div>
      <span v-if="comic" class="status-pill" :class="dirty ? 'edited' : comic.status">{{ dirty ? '未保存' : statusLabels[comic.status] }}</span>
      <span v-else class="quiet-label">未选择</span>
    </div>
    <div v-if="!comic || !modelValue" class="details-empty-body">
      <div class="empty-book"><el-icon :size="31"><Reading /></el-icon></div>
      <h3>选择一本，开始整理</h3>
      <p>从漫画列表选择作品<br />在这里编辑资料、预览页面并完成审核</p>
      <div class="empty-steps"><span>编辑资料</span><span class="step-dot">·</span><span>人工确认</span><span class="step-dot">·</span><span>生成 CBZ</span></div>
    </div>
    <template v-else>
      <div class="selected-summary">
        <div class="mini-cover" v-loading="pageBusy" @click="activeTab = 'preview'" title="查看页面">
          <img v-if="pageData" :src="pageData" alt="漫画页面预览" />
          <el-icon v-else :size="24"><Picture /></el-icon>
        </div>
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
          <div class="metadata-scroll">
            <el-alert v-if="readOnly" :title="remoteReadOnlyMessage" type="info" :closable="false" show-icon class="source-warning" data-testid="komga-inactive-scope" />
            <el-alert v-if="isRemote && (comic.remote?.hasLocalChanges || comic.remote?.pendingSync)" :title="comic.remote?.conflict ? '本机修改与远端更新发生冲突；读取书库已保留本机修改，请先核对提示。' : '有待同步的本机修改；再次读取书库会保留这些修改。'" type="warning" :closable="false" show-icon class="source-warning" data-testid="komga-local-changes" />
            <div v-if="isRemote && comic.remote?.conflict && !readOnly" class="remote-conflict-actions" data-testid="komga-conflict-actions"><el-button data-testid="komga-keep-local" size="small" :disabled="busy" @click="emit('resolve', 'keep_local')">保留本机修改并重新核对</el-button><el-button data-testid="komga-use-remote" size="small" :disabled="busy" @click="emit('resolve', 'use_remote')">采用远端资料</el-button></div>
            <el-alert v-if="comic.warning" :title="comic.warning" type="warning" :closable="false" show-icon class="source-warning" />
            <div v-if="legacyNames.length" class="legacy-name-note"><strong>旧版名称 · 供核对</strong><p v-for="item in legacyNames" :key="item.label">{{ item.label }}：{{ item.value }}</p></div>
            <div class="metadata-tools"><span>按实际资料填写，未知内容留待确认</span><el-button link data-testid="expand-metadata" :disabled="busy" @click="expanded = expanded.length === sections.length ? ['identity'] : [...sections]">{{ expanded.length === sections.length ? '收起分组' : '展开全部' }}</el-button></div>
            <el-form label-position="top" class="metadata-form" :disabled="editDisabled">
              <el-collapse v-model="expanded" class="metadata-sections">
                <el-collapse-item name="identity">
                  <template #title><span class="section-number">01</span><span class="collapse-title" data-testid="section-identity">名称与系列</span></template>
                  <el-form-item label="作品名称 · Title" required><el-input data-testid="title" :model-value="modelValue.title" @update:model-value="update('title', $event)" placeholder="优先保留作品原始标题" /><p class="metadata-help">保留原文有助于检索，中文标题不是必填项；系列别名请填在下方。</p></el-form-item>
                  <el-form-item label="作品组织方式" required><el-select data-testid="collection-mode" :disabled="isRemote" :model-value="collectionMode(modelValue)" @update:model-value="update('collection_mode', $event)"><el-option v-for="mode in collectionModes" :key="mode.value" :label="mode.label" :value="mode.value" /></el-select><p class="metadata-help">{{ isRemote ? '远端组织方式由 Komga 的目录和书库设置决定，此处编辑资料不会移动作品。' : '独立作品导出到 _oneshots；请在 Komga 书库选项把 One-Shots directory 设为 /_oneshots。多卷系列按系列归入同一目录。' }}</p></el-form-item>
                  <el-form-item label="系列名称" :required="isSeries && !isRemote"><el-input data-testid="series" :model-value="modelValue.series" @update:model-value="update('series', $event)" :disabled="!isSeries" placeholder="多卷系列填写共同名称" /><p class="metadata-help">{{ isSeries ? '同一系列使用一致名称；原作 IP 请填入“原作”分类。' : 'One-Shot 不向 XML 写入系列、编号和总数；已有本地值保留，切回系列后可继续编辑。' }}</p></el-form-item>
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
                    <el-form-item label="作品组"><el-input data-testid="work-group" :model-value="modelValue.work_group || ''" @update:model-value="update('work_group', $event)" clearable placeholder="同一作品的各版本使用完全一致的组名" /><p class="metadata-help">可用稳定的作品原名；同名但不同作者、社团的作品请补充标识。没有版本关联需求时留空。</p></el-form-item>
                    <el-form-item label="文字版本"><el-select data-testid="text-version" :model-value="modelValue.text_version || ''" @update:model-value="update('text_version', $event)" clearable placeholder="无版本区分需求时留空"><el-option v-for="version in textVersions" :key="version" :label="version" :value="version" /></el-select><p class="metadata-help">区分是否保留对白、旁白等画面文字。日文原版和中文汉化版都可有文字；不默认选择，也不跟随其他字段变化。</p></el-form-item>
                  </div>
                </el-collapse-item>
                <el-collapse-item name="creators">
                  <template #title><span class="section-number">02</span><span class="collapse-title" data-testid="section-creators">作者与出版</span></template>
                  <el-form-item label="作者 · Writer" required><el-select data-testid="authors" :model-value="modelValue.authors" @update:model-value="update('authors', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="保留原文姓名，按 Enter 添加" class="full-width" /></el-form-item>
                  <el-form-item label="独立绘画者"><el-select data-testid="pencillers" :model-value="modelValue.pencillers" @update:model-value="update('pencillers', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="与作者不同时填写" class="full-width" /><p class="metadata-help">作者兼任绘画时只填写作者。同名人员不会重复导出。</p><p v-if="duplicatePencillers.length" class="metadata-inline-warning">与作者重复：{{ duplicatePencillers.join('、') }}</p></el-form-item>
                  <el-form-item label="译者 / 汉化组"><el-input data-testid="translator" :model-value="modelValue.translator" @update:model-value="update('translator', $event)" placeholder="实际译者或团队名称" /><p class="metadata-help">原创版本留空；已翻译版本请核实实际译者，未知信息留待审核。</p></el-form-item>
                  <el-form-item label="出版方 / 发行社团"><el-input data-testid="publisher" :model-value="modelValue.publisher" @update:model-value="update('publisher', $event)" placeholder="出版社、发行社团或机构的原始名称" /><p class="metadata-help">Komga 中为系列共享信息，修改后需选全系列同步。</p></el-form-item>
                </el-collapse-item>
                <el-collapse-item name="classification">
                  <template #title><span class="section-number">03</span><span class="collapse-title" data-testid="section-classification">内容与分级</span></template>
                  <div class="form-grid">
                    <el-form-item label="内容类型 · Genre" required><el-select data-testid="genre" :model-value="modelValue.genre" @update:model-value="update('genre', $event)" clearable placeholder="请选择内容类型"><el-option v-if="legacyGenre" :label="`待核对：${modelValue.genre}`" :value="modelValue.genre" disabled /><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></el-form-item>
                    <el-form-item label="年龄分级 · AgeRating" required><el-select data-testid="age-rating" :model-value="modelValue.age_rating" @update:model-value="update('age_rating', $event)"><el-option v-for="rating in ageRatings" :key="rating.value" :label="rating.label" :value="rating.value" /></el-select></el-form-item>
                  </div>
                  <p v-if="legacyGenre" class="metadata-inline-warning" data-testid="legacy-genre-warning">旧内容类型“{{ modelValue.genre }}”不属于规范中的六类。请核对后选择；保存其他字段时保留此旧值。</p>
                  <p v-if="isRemote" class="metadata-help section-help" data-testid="komga-remote-field-origin">远端的出版方、语言、总数和阅读方向是系列共享资料。内容类型与分级读取逐册辅助实体；没有逐册记录时保留空白或未确认，不把系列值当作本册值。</p>
                  <p v-if="isRemote && comic.remote?.sharedMetadata" class="metadata-help section-help" data-testid="komga-shared-series">系列共享值（仅供参考）：年龄 {{ sharedSeriesRating }}；类型 {{ comic.remote.sharedMetadata.genres?.join('、') || '未设置' }}。</p>
                  <p class="metadata-help section-help" data-testid="komga-series-aggregation">Komga 的内容类型和年龄分级属于系列：同系列类型合并，年龄取最高值。本机保留每本作品各自的资料；逐册筛选需要另通过 API 建立实体。</p>
                  <p v-if="mixedRatings.length > 1 || mixedGenres.length > 1" class="metadata-inline-warning" data-testid="komga-mixed-series">本机同系列检测到{{ mixedRatings.length > 1 ? '不同年龄分级' : '' }}{{ mixedRatings.length > 1 && mixedGenres.length > 1 ? '、' : '' }}{{ mixedGenres.length > 1 ? '多种内容类型' : '' }}；Komga 系列展示会汇总这些值。</p>
                  <div v-if="modelValue.age_rating === 'R18+'" class="correction-field" data-testid="correction-field"><div><strong>修正状态</strong><p>{{ modelValue.uncensored ? '导出为“修正状态:无修正”标签' : '默认有修正，不生成额外标签' }}</p></div><el-checkbox data-testid="uncensored" :model-value="modelValue.uncensored" @update:model-value="update('uncensored', Boolean($event))">无修正</el-checkbox></div>
                  <el-form-item label="色彩"><el-select data-testid="color" :model-value="modelValue.color" @update:model-value="update('color', $event)" clearable placeholder="未确认可留空"><el-option v-for="color in colors" :key="color" :label="color" :value="color" /></el-select><p class="metadata-help">以正文判断，封面不参与；正文兼有黑白和彩色页时选“部分彩色”。</p></el-form-item>
                  <el-form-item label="题材与属性标签 · Tags" required><el-select data-testid="tags" :model-value="displayedTags" @update:model-value="update('tags', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="如恋爱、奇幻、日常，按 Enter 添加" class="full-width" /><p class="metadata-help">普通标签与下方已确认分类生成的标签合计至少一条。未知信息保留草稿，不填写“无”来凑数。文字版本为“无文字”时不重复显示普通“无字版”。</p></el-form-item>
                  <el-form-item label="原作"><el-input data-testid="original-work" :model-value="originalWork" @update:model-value="updateOriginalWork" clearable :placeholder="legacyOriginalWorks ? '填写后将旧列表替换为一个原作' : '如东方Project；没有或无法确认时留空'" /><p v-if="legacyOriginalWorks" class="metadata-inline-warning" data-testid="legacy-original-works-warning">旧资料包含多个原作：{{ modelValue.original_works.join('、') }}。请核对并改为一个；修改其他字段不会截断此列表。<el-button link data-testid="clear-legacy-original-works" :disabled="editDisabled" @click="update('original_works', [])">清空旧原作列表</el-button></p></el-form-item>
                  <el-form-item label="角色"><el-select data-testid="characters" :model-value="modelValue.characters" @update:model-value="update('characters', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="每个角色分别添加，可用顿号分隔" class="full-width" /><p class="metadata-help">写入 Characters，供 Komga 1.29+ 自动导入角色实体；同时保留“角色:”标签副本。</p></el-form-item>
                  <div class="custom-tag-preview" data-testid="custom-tag-preview"><span>已确认分类生成的可移植标签，与普通标签一同保存</span><div v-if="exportedTags.length"><el-tag v-for="tag in exportedTags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div><p v-else>填写适用的原作、角色、版本关联、发行、平台或色彩信息后显示。</p></div>
                </el-collapse-item>
                <el-collapse-item name="release">
                  <template #title><span class="section-number">04</span><span class="collapse-title" data-testid="section-release">发行与平台</span></template>
                  <div class="form-grid">
                    <el-form-item label="发行性质"><el-select data-testid="release-type" :model-value="modelValue.release_type" @update:model-value="update('release_type', $event)" clearable placeholder="未知可留空"><el-option v-for="type in releaseTypes" :key="type" :label="type" :value="type" /></el-select></el-form-item>
                    <el-form-item label="出版形态"><el-select data-testid="publication-format" :model-value="modelValue.publication_format" @update:model-value="update('publication_format', $event)" clearable filterable allow-create placeholder="选择或输入出版形态"><el-option v-for="format in publicationFormats" :key="format" :label="format" :value="format" /></el-select></el-form-item>
                  </div>
                  <el-form-item label="发布方式"><el-select data-testid="release-methods" :model-value="modelValue.release_methods" @update:model-value="update('release_methods', $event)" multiple clearable placeholder="可多选，来源不明时留空" class="full-width"><el-option v-for="method in releaseMethods" :key="method" :label="method" :value="method" /></el-select></el-form-item>
                  <el-form-item label="发布平台"><el-select data-testid="release-platforms" :model-value="modelValue.release_platforms" @update:model-value="update('release_platforms', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" clearable placeholder="可多选，也可输入新平台后按 Enter" class="full-width"><el-option v-for="platform in releasePlatforms" :key="platform" :label="platform" :value="platform" /></el-select><p class="metadata-help">只记录实际发布、销售或订阅平台，不由平台推断发行性质与发布方式。</p></el-form-item>
                </el-collapse-item>
                <el-collapse-item name="reading">
                  <template #title><span class="section-number">05</span><span class="collapse-title" data-testid="section-reading">日期与阅读</span></template>
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
                </el-collapse-item>
                <el-collapse-item name="sources">
                  <template #title><span class="section-number">06</span><span class="collapse-title" data-testid="section-sources">简介与来源</span></template>
                  <el-form-item label="作品简介"><el-input data-testid="summary" :model-value="modelValue.summary" @update:model-value="update('summary', $event)" type="textarea" :rows="5" resize="vertical" placeholder="填写完整的官方简介或可靠译文" /></el-form-item>
                  <el-form-item label="作品来源链接"><el-input data-testid="web" :model-value="modelValue.web.join('\n')" @update:model-value="update('web', $event.split('\n'))" type="textarea" :rows="3" resize="vertical" placeholder="每行一个作品详情页链接" /><p class="metadata-help">可填写多个商品详情页或官网链接。</p></el-form-item>
                </el-collapse-item>
              </el-collapse>
            </el-form>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="isRemote ? '远端页面' : '页面预览'" name="preview">
          <div class="page-preview-scroll">
            <div class="page-preview-toolbar">
              <el-button :icon="ArrowLeft" circle :disabled="editDisabled || pageBusy || pageIndex === 0" @click="changePage(-1)" aria-label="上一页" />
              <div class="page-counter"><el-input-number :model-value="pageIndex + 1" @update:model-value="pageIndex = Math.max(0, Number($event || 1) - 1)" :min="1" :max="Math.max(1, comic.page_count)" :controls="false" :disabled="editDisabled || pageBusy || !comic.page_count" /><span>/ {{ comic.page_count }}</span></div>
              <el-button :icon="ArrowRight" circle :disabled="editDisabled || pageBusy || pageIndex + 1 >= comic.page_count" @click="changePage(1)" aria-label="下一页" />
            </div>
            <div class="large-page" v-loading="pageBusy">
              <el-image v-if="pageData" :src="pageData" fit="contain" :preview-src-list="[pageData]" preview-teleported :zoom-rate="1.2"><template #error><div class="page-placeholder"><el-icon :size="34"><Picture /></el-icon><span>{{ isRemote ? '此页面暂时无法预览，可在 Komga 中查看' : '此格式无法预览，仍可原样打包' }}</span></div></template></el-image>
              <div v-else class="page-placeholder"><el-icon :size="34"><Picture /></el-icon><span>{{ pageError || (isRemote ? '正在读取 Komga 页面' : '正在读取本地页面') }}</span></div>
            </div>
            <p class="page-filename" :title="pageName">{{ pageName }}</p>
            <el-button v-if="!isRemote" :type="isCover ? 'success' : 'default'" plain class="set-cover-button" :icon="isCover ? Check : Picture" :disabled="busy || pageBusy || !pageData" @click="update('cover_index', pageIndex)">{{ isCover ? '当前封面' : '将此页设为封面' }}</el-button>
            <p v-if="!isRemote" class="metadata-help">此操作仅设置 ComicInfo 封面标记。Komga 自动使用实际首张图片，不读取该标记；生成时保持原有页序。</p><p v-else class="metadata-help">页面通过当前 Komga 连接读取，图片和封面保持在服务器。</p>
            <div class="preview-tip"><el-icon><ZoomIn /></el-icon>点击页面可放大查看</div>
            <el-alert v-if="pageError" :title="pageError" type="warning" :closable="false" show-icon />
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
    </template>
  </aside>
</template>

<style scoped>
.remote-conflict-actions { display: flex; flex-wrap: wrap; gap: 7px; margin: -5px 0 15px; }
.remote-conflict-actions .el-button + .el-button { margin-left: 0; }
.source-badges > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.version-link-fields { margin-top: 17px; padding-top: 14px; border-top: 1px solid #e3eadf; }
.version-link-fields h4 { margin: 0 0 8px; color: #526d48; font-size: 12px; font-weight: 600; }
.version-link-fields h4 span { margin-left: 7px; color: #91a086; font-size: 10px; font-weight: 400; }
.version-link-fields > .metadata-help { margin-bottom: 13px; }
</style>
