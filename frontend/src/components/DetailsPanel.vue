<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ArrowLeft, ArrowRight, Document, FolderOpened, Picture, Reading, Check, ZoomIn } from '@element-plus/icons-vue'
import type { Comic, Metadata } from '../types'
import { displayTitle, statusLabels, errorMessage, comicGenres, publicationFormats, ageRatings, customTags, classificationValues } from '../types'
import { invoke } from '../api'
const props = defineProps<{ comic: Comic | null; modelValue: Metadata | null; busy: boolean; dirty: boolean; reviewed: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: Metadata): void
  (e: 'update:reviewed', value: boolean): void
  (e: 'save'): void
  (e: 'approve'): void
  (e: 'error', message: string): void
}>()
const activeTab = ref('metadata')
const sections = ['identity', 'creators', 'classification', 'reading', 'sources']
const expanded = ref<string[]>(['identity', 'creators'])
const pageIndex = ref(0)
const pageData = ref('')
const pageName = ref('')
const pageError = ref('')
const pageBusy = ref(false)
const xmlText = ref('')
const xmlBusy = ref(false)
let pageRequest = 0
let xmlRequest = 0
let xmlTimer: ReturnType<typeof setTimeout> | undefined
const title = computed(() => props.modelValue ? displayTitle(props.modelValue) : '')
const isCover = computed(() => props.modelValue?.cover_index === pageIndex.value)
const legacyNames = computed(() => {
  const m = props.modelValue
  if (!m || !props.comic?.warning || !/升级|迁移|规范/.test(props.comic.warning)) return []
  return [{ label: '旧版原名', value: m.original_title }, { label: '旧版中文名', value: m.chinese_title }]
    .filter(item => item.value && item.value.trim() !== m.title.trim())
})
const exportedTags = computed(() => props.modelValue ? customTags(props.modelValue) : [])
const duplicatePencillers = computed(() => props.modelValue?.pencillers.filter(name => props.modelValue?.authors.some(author => author.trim() === name.trim())) || [])
function update<K extends keyof Metadata>(key: K, value: Metadata[K]) {
  if (!props.modelValue) return
  const next = { ...props.modelValue, [key]: value }
  if (key === 'characters') next.characters = classificationValues(value as string[])
  if (key === 'original_works') next.original_works = classificationValues(value as string[])
  if (key === 'age_rating' && value !== 'R18+') next.uncensored = false
  emit('update:modelValue', next)
}
async function loadPage() {
  const id = props.comic?.id
  const index = pageIndex.value
  const request = ++pageRequest
  if (!id) return
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
    if (request === xmlRequest) xmlText.value = result.xml
  } catch (error) {
    if (request === xmlRequest) xmlText.value = `补全资料后可预览 ComicInfo.xml\n\n${errorMessage(error)}`
  } finally { if (request === xmlRequest) xmlBusy.value = false }
}
function queueXml() {
  clearTimeout(xmlTimer)
  if (activeTab.value === 'xml') xmlTimer = setTimeout(loadXml, 220)
}
function changePage(step: number) {
  const max = (props.comic?.page_count || 1) - 1
  pageIndex.value = Math.max(0, Math.min(max, pageIndex.value + step))
}
async function openLocation(kind: 'source' | 'output') {
  if (!props.comic) return
  try { await window.comic.openLocation(kind, props.comic.id) }
  catch (error) { emit('error', errorMessage(error)) }
}
watch([() => props.comic?.id, () => props.comic?.fingerprint], () => {
  activeTab.value = 'metadata'
  expanded.value = ['identity', 'creators']
  pageIndex.value = Math.max(0, Math.min(props.modelValue?.cover_index || 0, (props.comic?.page_count || 1) - 1))
  pageData.value = ''
  pageError.value = ''
}, { immediate: true })
watch([() => props.comic?.id, () => props.comic?.fingerprint, pageIndex], loadPage, { immediate: true })
watch(activeTab, (value) => { if (value === 'xml') loadXml() })
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
          <img v-if="pageData" :src="pageData" alt="本地漫画页面" />
          <el-icon v-else :size="24"><Picture /></el-icon>
        </div>
        <div class="selected-summary-copy">
          <h2 :title="title">{{ title }}</h2>
          <p v-if="modelValue.series" class="native-title" :title="modelValue.series">{{ modelValue.series }}<span v-if="modelValue.volume"> · 卷 {{ modelValue.volume }}</span><span v-if="modelValue.number"> · 第 {{ modelValue.number }} 期 / 章</span></p>
          <div class="source-badges"><span>{{ comic.kind === 'cbz' ? 'CBZ' : '图片文件夹' }}</span><i></i><span>{{ comic.page_count }} 页</span></div>
          <button class="source-link" :title="comic.source" :disabled="busy" @click="openLocation('source')"><el-icon><FolderOpened /></el-icon><span>查看源位置</span></button>
        </div>
      </div>
      <el-tabs v-model="activeTab" class="details-tabs">
        <el-tab-pane label="基本资料" name="metadata">
          <div class="metadata-scroll">
            <el-alert v-if="comic.warning" :title="comic.warning" type="warning" :closable="false" show-icon class="source-warning" />
            <div v-if="legacyNames.length" class="legacy-name-note"><strong>旧版名称 · 供核对</strong><p v-for="item in legacyNames" :key="item.label">{{ item.label }}：{{ item.value }}</p></div>
            <div class="metadata-tools"><span>按实际资料填写，未知内容留待确认</span><el-button link data-testid="expand-metadata" :disabled="busy" @click="expanded = expanded.length === sections.length ? ['identity'] : [...sections]">{{ expanded.length === sections.length ? '收起分组' : '展开全部' }}</el-button></div>
            <el-form label-position="top" class="metadata-form" :disabled="busy">
              <el-collapse v-model="expanded" class="metadata-sections">
                <el-collapse-item name="identity">
                  <template #title><span class="section-number">01</span><span class="collapse-title">名称与系列</span></template>
                  <el-form-item label="作品名称" required><el-input data-testid="title" :model-value="modelValue.title" @update:model-value="update('title', $event)" placeholder="当前作品、卷册或章节的名称" /></el-form-item>
                  <el-form-item label="系列名称"><el-input data-testid="series" :model-value="modelValue.series" @update:model-value="update('series', $event)" placeholder="有实际系列关系时填写" /><p class="metadata-help">独立作品可留空，导出时使用作品名称组织 Kavita 系列。原作 IP 请填入“原作”分类。</p></el-form-item>
                  <el-form-item label="本地化系列名称"><el-input data-testid="localized-series" :model-value="modelValue.localized_series" @update:model-value="update('localized_series', $event)" placeholder="系列的另一种语言名称" /><p class="metadata-help">用于实际系列的译名，不用于单本作品的别名，也不与系列名称重复。</p></el-form-item>
                  <div class="metadata-triple-grid">
                    <el-form-item label="期号 / 章节编号"><el-input data-testid="number" :model-value="modelValue.number" @update:model-value="update('number', $event)" placeholder="如 12 或 12.5" /></el-form-item>
                    <el-form-item label="卷号"><el-input data-testid="volume" :model-value="modelValue.volume" @update:model-value="update('volume', $event)" placeholder="如 2" inputmode="numeric" /></el-form-item>
                    <el-form-item label="系列总数"><el-input data-testid="count" :model-value="modelValue.count" @update:model-value="update('count', $event)" placeholder="已知时填写" inputmode="numeric" /></el-form-item>
                  </div>
                  <p class="metadata-help section-help">整卷通常只填写卷号；按章整理时填写章节编号，不机械重复。</p>
                </el-collapse-item>
                <el-collapse-item name="creators">
                  <template #title><span class="section-number">02</span><span class="collapse-title">创作与出版</span></template>
                  <el-form-item label="作者" required><el-select data-testid="authors" :model-value="modelValue.authors" @update:model-value="update('authors', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入作者，按 Enter 添加" class="full-width" /></el-form-item>
                  <el-form-item label="独立绘画者"><el-select data-testid="pencillers" :model-value="modelValue.pencillers" @update:model-value="update('pencillers', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="与作者不同时填写" class="full-width" /><p class="metadata-help">作者兼任绘画时只填写作者。同名人员不会重复导出。</p><p v-if="duplicatePencillers.length" class="metadata-inline-warning">与作者重复：{{ duplicatePencillers.join('、') }}</p></el-form-item>
                  <el-form-item label="译者 / 汉化组"><el-input data-testid="translator" :model-value="modelValue.translator" @update:model-value="update('translator', $event)" placeholder="实际译者或团队名称" /><p class="metadata-help">原创版本留空；已翻译版本请核实实际译者，未知信息留待审核。</p></el-form-item>
                  <el-form-item label="出版方 / 发行方"><el-input data-testid="publisher" :model-value="modelValue.publisher" @update:model-value="update('publisher', $event)" placeholder="出版社、发行社团或机构" /><p class="metadata-help">作者与社团名称保留原始语言。</p></el-form-item>
                </el-collapse-item>
                <el-collapse-item name="classification">
                  <template #title><span class="section-number">03</span><span class="collapse-title">内容与分类</span></template>
                  <div class="form-grid">
                    <el-form-item label="内容类型"><el-select data-testid="genre" :model-value="modelValue.genre" @update:model-value="update('genre', $event)" filterable allow-create clearable placeholder="选择或输入类型"><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></el-form-item>
                    <el-form-item label="年龄分级" required><el-select data-testid="age-rating" :model-value="modelValue.age_rating" @update:model-value="update('age_rating', $event)"><el-option v-for="rating in ageRatings" :key="rating.value" :label="rating.label" :value="rating.value" /></el-select></el-form-item>
                  </div>
                  <div v-if="modelValue.age_rating === 'R18+'" class="correction-field" data-testid="correction-field"><div><strong>修正状态</strong><p>{{ modelValue.uncensored ? '导出为“修正状态:无修正”标签' : '默认有修正，不生成额外标签' }}</p></div><el-checkbox data-testid="uncensored" :model-value="modelValue.uncensored" @update:model-value="update('uncensored', Boolean($event))">无修正</el-checkbox></div>
                  <el-form-item label="题材与属性标签"><el-select data-testid="tags" :model-value="modelValue.tags" @update:model-value="update('tags', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="如恋爱、奇幻、日常，按 Enter 添加" class="full-width" /><p class="metadata-help">普通题材放在这里；汉化、色彩和年龄使用对应字段，不重复添加标签。</p></el-form-item>
                  <el-form-item label="原作"><el-select data-testid="original-works" :model-value="modelValue.original_works" @update:model-value="update('original_works', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="如东方Project、Fate，可填写多个" class="full-width" /></el-form-item>
                  <el-form-item label="角色"><el-select data-testid="characters" :model-value="modelValue.characters" @update:model-value="update('characters', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="每个角色分别添加，可用顿号分隔" class="full-width" /></el-form-item>
                  <div class="form-grid">
                    <el-form-item label="发行性质"><el-select data-testid="release-type" :model-value="modelValue.release_type" @update:model-value="update('release_type', $event)" clearable placeholder="可选"><el-option label="同人" value="同人" /><el-option label="商业" value="商业" /></el-select></el-form-item>
                    <el-form-item label="出版形态"><el-select data-testid="publication-format" :model-value="modelValue.publication_format" @update:model-value="update('publication_format', $event)" clearable filterable allow-create placeholder="可选"><el-option v-for="format in publicationFormats" :key="format" :label="format" :value="format" /></el-select></el-form-item>
                  </div>
                  <div class="custom-tag-preview"><span>自定义分类将导出为以下标签</span><div v-if="exportedTags.length"><el-tag v-for="tag in exportedTags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div><p v-else>填写原作、角色、发行性质或出版形态后显示。</p></div>
                </el-collapse-item>
                <el-collapse-item name="reading">
                  <template #title><span class="section-number">04</span><span class="collapse-title">发行与阅读</span></template>
                  <label class="date-group-label">发售日期</label>
                  <div class="metadata-triple-grid">
                    <el-form-item label="年"><el-input data-testid="year" :model-value="modelValue.year" @update:model-value="update('year', $event)" placeholder="如 2026" maxlength="4" inputmode="numeric" /></el-form-item>
                    <el-form-item label="月"><el-select data-testid="month" :model-value="modelValue.month" @update:model-value="update('month', $event)" clearable placeholder="未知"><el-option v-for="month in 12" :key="month" :label="`${month} 月`" :value="String(month)" /></el-select></el-form-item>
                    <el-form-item label="日"><el-select data-testid="day" :model-value="modelValue.day" @update:model-value="update('day', $event)" clearable placeholder="未知"><el-option v-for="day in 31" :key="day" :label="`${day} 日`" :value="String(day)" /></el-select></el-form-item>
                  </div>
                  <p class="metadata-help section-help">只填写已知部分，不补造月份或日期。</p>
                  <el-form-item label="当前版本语言"><el-select data-testid="language" :model-value="modelValue.language" @update:model-value="update('language', $event)" filterable allow-create><el-option label="日文 · ja" value="ja" /><el-option label="简体中文 · zh-Hans" value="zh-Hans" /><el-option label="繁体中文 · zh-Hant" value="zh-Hant" /><el-option label="英文 · en" value="en" /></el-select></el-form-item>
                  <el-form-item label="黑白 / 彩色"><el-select data-testid="black-and-white" :model-value="modelValue.black_and_white" @update:model-value="update('black_and_white', $event)"><el-option label="未确认" value="Unknown" /><el-option label="黑白" value="Yes" /><el-option label="彩色 / 非黑白" value="No" /></el-select><p class="metadata-help">不重复生成“全彩”标签；部分彩色可另加“部分彩色”标签。</p></el-form-item>
                  <el-form-item label="漫画阅读方向"><el-select data-testid="manga" :model-value="modelValue.manga" @update:model-value="update('manga', $event)"><el-option label="未确认" value="Unknown" /><el-option label="日式漫画 · 右向左" value="YesAndRightToLeft" /><el-option label="日式漫画" value="Yes" /><el-option label="非日式漫画" value="No" /></el-select></el-form-item>
                  <div class="page-count-field" data-testid="page-count"><div><strong>图片页数</strong><p>按实际图片计算，生成时重新核对</p></div><span>{{ comic.page_count }}<small> 页</small></span></div>
                </el-collapse-item>
                <el-collapse-item name="sources">
                  <template #title><span class="section-number">05</span><span class="collapse-title">简介与来源</span></template>
                  <el-form-item label="作品简介"><el-input data-testid="summary" :model-value="modelValue.summary" @update:model-value="update('summary', $event)" type="textarea" :rows="5" resize="vertical" placeholder="填写完整的官方简介或可靠译文" /></el-form-item>
                  <el-form-item label="作品来源链接"><el-input data-testid="web" :model-value="modelValue.web.join('\n')" @update:model-value="update('web', $event.split('\n'))" type="textarea" :rows="3" resize="vertical" placeholder="每行一个作品详情页链接" /><p class="metadata-help">可填写多个商品详情页或官网链接。</p></el-form-item>
                </el-collapse-item>
              </el-collapse>
            </el-form>
          </div>
        </el-tab-pane>
        <el-tab-pane label="页面预览" name="preview">
          <div class="page-preview-scroll">
            <div class="page-preview-toolbar">
              <el-button :icon="ArrowLeft" circle :disabled="busy || pageBusy || pageIndex === 0" @click="changePage(-1)" aria-label="上一页" />
              <div class="page-counter"><el-input-number :model-value="pageIndex + 1" @update:model-value="pageIndex = Math.max(0, Number($event || 1) - 1)" :min="1" :max="comic.page_count" :controls="false" :disabled="busy || pageBusy" /><span>/ {{ comic.page_count }}</span></div>
              <el-button :icon="ArrowRight" circle :disabled="busy || pageBusy || pageIndex + 1 >= comic.page_count" @click="changePage(1)" aria-label="下一页" />
            </div>
            <div class="large-page" v-loading="pageBusy">
              <el-image v-if="pageData" :src="pageData" fit="contain" :preview-src-list="[pageData]" preview-teleported :zoom-rate="1.2"><template #error><div class="page-placeholder"><el-icon :size="34"><Picture /></el-icon><span>此格式无法预览，仍可原样打包</span></div></template></el-image>
              <div v-else class="page-placeholder"><el-icon :size="34"><Picture /></el-icon><span>{{ pageError || '正在读取本地页面' }}</span></div>
            </div>
            <p class="page-filename" :title="pageName">{{ pageName }}</p>
            <el-button :type="isCover ? 'success' : 'default'" plain class="set-cover-button" :icon="isCover ? Check : Picture" :disabled="busy || pageBusy || !pageData" @click="update('cover_index', pageIndex)">{{ isCover ? '当前封面' : '将此页设为封面' }}</el-button>
            <div class="preview-tip"><el-icon><ZoomIn /></el-icon>点击页面可放大查看</div>
            <el-alert v-if="pageError" :title="pageError" type="warning" :closable="false" show-icon />
          </div>
        </el-tab-pane>
        <el-tab-pane label="ComicInfo.xml" name="xml"><div class="xml-tab" v-loading="xmlBusy"><p>以下内容将写入成品 CBZ 的根目录。</p><pre>{{ xmlText || '正在生成 XML 预览…' }}</pre></div></el-tab-pane>
      </el-tabs>
      <footer class="review-footer">
        <el-checkbox :model-value="reviewed" @update:model-value="emit('update:reviewed', Boolean($event))" :disabled="busy" class="review-checkbox">已核对资料、分级、页序与封面</el-checkbox>
        <div class="review-actions"><el-button :disabled="busy || !dirty" @click="emit('save')">保存草稿</el-button><el-button type="primary" :icon="Check" :disabled="busy || !reviewed" @click="emit('approve')">审核通过</el-button></div>
        <div class="review-bottom"><span>{{ dirty ? '修改后需重新审核' : '资料保存在本机' }}</span><el-button link :icon="FolderOpened" :disabled="busy || !comic.output" @click="openLocation('output')">打开成品</el-button></div>
      </footer>
    </template>
  </aside>
</template>
