<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ArrowLeft, ArrowRight, Document, FolderOpened, Picture, Reading, Check, ZoomIn } from '@element-plus/icons-vue'
import type { Comic, Metadata } from '../types'
import { displayTitle, statusLabels, errorMessage, comicGenres } from '../types'
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
const expanded = ref<string[]>([])
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
const extraCount = computed(() => {
  const m = props.modelValue
  return m ? [m.publisher, m.translator, m.genre, m.year, m.summary].filter(Boolean).length : 0
})
function update<K extends keyof Metadata>(key: K, value: Metadata[K]) {
  if (!props.modelValue) return
  const next = { ...props.modelValue, [key]: value }
  if (key === 'translated') next.language = value ? 'zh-Hans' : 'ja'
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
  expanded.value = []
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
          <p v-if="modelValue.translated" class="native-title" :title="modelValue.original_title">{{ modelValue.original_title || '日文原名待填写' }}</p>
          <div class="source-badges"><span>{{ comic.kind === 'cbz' ? 'CBZ' : '图片文件夹' }}</span><i></i><span>{{ comic.page_count }} 页</span></div>
          <button class="source-link" :title="comic.source" :disabled="busy" @click="openLocation('source')"><el-icon><FolderOpened /></el-icon><span>查看源位置</span></button>
        </div>
      </div>
      <el-tabs v-model="activeTab" class="details-tabs">
        <el-tab-pane label="基本资料" name="metadata">
          <div class="metadata-scroll">
            <el-alert v-if="comic.warning" :title="comic.warning" type="warning" :closable="false" show-icon class="source-warning" />
            <el-form label-position="top" class="metadata-form" :disabled="busy">
              <el-form-item label="日文原名" required><el-input :model-value="modelValue.original_title" @update:model-value="update('original_title', $event)" placeholder="完整保留作品原名" /></el-form-item>
              <el-form-item label="中文名称" :required="modelValue.translated"><el-input :model-value="modelValue.chinese_title" @update:model-value="update('chinese_title', $event)" placeholder="汉化作品填写中文名" /></el-form-item>
              <el-form-item label="作者" required><el-select :model-value="modelValue.authors" @update:model-value="update('authors', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入作者，按 Enter 添加" class="full-width" /></el-form-item>
              <div class="form-divider"><span>作品标记</span><span class="field-hint">按实际内容选择</span></div>
              <div class="flag-choices">
                <el-checkbox :model-value="modelValue.translated" @update:model-value="update('translated', Boolean($event))" border>汉化</el-checkbox>
                <el-checkbox :model-value="modelValue.uncensored" @update:model-value="update('uncensored', Boolean($event))" border>无修正</el-checkbox>
                <el-checkbox :model-value="modelValue.full_color" @update:model-value="update('full_color', Boolean($event))" border>全彩</el-checkbox>
              </div>
              <el-form-item label="标签"><el-select :model-value="modelValue.tags" @update:model-value="update('tags', $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入标签，按 Enter 添加" class="full-width" /></el-form-item>
              <el-collapse v-model="expanded" class="extra-metadata">
                <el-collapse-item name="publication">
                  <template #title><span class="collapse-title">出版与补充信息</span><span class="collapse-note">{{ extraCount ? `${extraCount} 项已填写` : '可选' }}</span></template>
                  <div class="form-grid">
                    <el-form-item label="社团 / 出版社"><el-input :model-value="modelValue.publisher" @update:model-value="update('publisher', $event)" placeholder="可选" /></el-form-item>
                    <el-form-item label="汉化组"><el-input :model-value="modelValue.translator" @update:model-value="update('translator', $event)" placeholder="可选" /></el-form-item>
                    <el-form-item label="类型"><el-select :model-value="modelValue.genre" @update:model-value="update('genre', $event)" filterable allow-create clearable placeholder="选择或输入类型"><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></el-form-item>
                    <el-form-item label="语言"><el-select :model-value="modelValue.language" @update:model-value="update('language', $event)" filterable allow-create><el-option label="日文 · ja" value="ja" /><el-option label="简体中文 · zh-Hans" value="zh-Hans" /><el-option label="繁体中文 · zh-Hant" value="zh-Hant" /><el-option label="英文 · en" value="en" /></el-select></el-form-item>
                    <el-form-item label="发行年份"><el-input :model-value="modelValue.year" @update:model-value="update('year', $event)" placeholder="例如：2026" maxlength="4" /></el-form-item>
                    <el-form-item label="发行月份"><el-select :model-value="modelValue.month || ''" @update:model-value="update('month', $event)" clearable placeholder="可选"><el-option v-for="month in 12" :key="month" :label="`${month} 月`" :value="String(month)" /></el-select></el-form-item>
                    <el-form-item label="年龄分级"><el-select :model-value="modelValue.age_rating" @update:model-value="update('age_rating', $event)" filterable allow-create><el-option label="未指定" value="Unknown" /><el-option label="全年龄" value="Everyone" /><el-option label="青少年" value="Teen" /><el-option label="17+" value="Mature 17+" /><el-option label="18+" value="Adults Only 18+" /></el-select></el-form-item>
                  </div>
                  <el-form-item label="作品简介"><el-input :model-value="modelValue.summary" @update:model-value="update('summary', $event)" type="textarea" :rows="3" resize="vertical" placeholder="写一段作品简介（可选）" /></el-form-item>
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
        <el-checkbox :model-value="reviewed" @update:model-value="emit('update:reviewed', Boolean($event))" :disabled="busy" class="review-checkbox">已核对作品、原名、作者、页序与封面</el-checkbox>
        <div class="review-actions"><el-button :disabled="busy || !dirty" @click="emit('save')">保存草稿</el-button><el-button type="primary" :icon="Check" :disabled="busy || !reviewed" @click="emit('approve')">审核通过</el-button></div>
        <div class="review-bottom"><span>{{ dirty ? '修改后需重新审核' : '资料保存在本机' }}</span><el-button link :icon="FolderOpened" :disabled="busy || !comic.output" @click="openLocation('output')">打开成品</el-button></div>
      </footer>
    </template>
  </aside>
</template>
