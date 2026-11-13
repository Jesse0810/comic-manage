<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import type { CollectionMode, Metadata } from '../types'
import { ageRatings, aliasLanguages, classificationValues, collectionModes, comicGenres, colors, errorMessage, publicationFormats, readingDirections, releaseMethods, releasePlatforms, releaseTypes } from '../types'
import { invoke } from '../api'

const props = defineProps<{ modelValue: boolean; ids: string[]; authors: string[]; modes?: CollectionMode[]; ratings?: string[]; hasRemote?: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', count: number): void
}>()

type BatchKey = 'authors' | 'genre' | 'collection_mode' | 'series' | 'localized_series' | 'localized_series_label' | 'number' | 'count' | 'volume'
  | 'pencillers' | 'translator' | 'publisher' | 'original_works' | 'characters' | 'release_type' | 'publication_format'
  | 'age_rating' | 'year' | 'month' | 'day' | 'language' | 'color' | 'manga' | 'reading_direction' | 'release_methods' | 'release_platforms' | 'tags' | 'web' | 'uncensored'
type BatchValues = Pick<Metadata, BatchKey>
type MultiKey = 'authors' | 'pencillers' | 'characters' | 'release_methods' | 'release_platforms' | 'tags'
type TextKey = Exclude<BatchKey, MultiKey | 'original_works' | 'web' | 'uncensored'>
type Option = { label: string; value: string }
type Field = {
  label: string; hint?: string; placeholder?: string; clearable?: boolean; wide?: boolean
} & (
  | { key: TextKey; kind: 'text' | 'select'; options?: Option[]; allowCreate?: boolean }
  | { key: MultiKey; kind: 'multi'; options?: Option[]; allowCreate?: boolean }
  | { key: 'original_works'; kind: 'original' }
  | { key: 'web'; kind: 'web' }
  | { key: 'uncensored'; kind: 'boolean' }
)
const options = (items: string[]): Option[] => items.map(value => ({ label: value, value }))
const groups: { id: string; label: string; fields: Field[] }[] = [
  { id: 'identity', label: '名称与系列', fields: [
    { key: 'collection_mode', label: '作品组织方式', kind: 'select', options: collectionModes, hint: 'One-Shot 输出到 _oneshots；多卷系列需逐册确认系列名称与 Number。' },
    { key: 'series', label: '系列名称', kind: 'text', clearable: true, placeholder: '例如：系列原名', hint: '仅系列模式导出此名称。独立作品请修改组织方式为 One-Shot。' },
    { key: 'localized_series', label: '系列别名', kind: 'text', clearable: true, placeholder: '仅填写系列的另一种名称', hint: '仅多卷系列使用；保存在本机，需通过 API 写入 alternateTitles，不写入 XML。' },
    { key: 'localized_series_label', label: '别名标签', kind: 'select', options: aliasLanguages, allowCreate: true, clearable: true, placeholder: 'zh-Hans、zh-Hant 或原名', hint: '别名非空时须有标签；与当前卷册语言独立。' },
    { key: 'number', label: '册编号 · Number', kind: 'text', clearable: true, placeholder: '系列内的编号或顺序', hint: '填写后所选作品使用同一编号；连续编号请逐本编辑，不从 Volume 推测。' },
    { key: 'count', label: '系列总数 · Count', kind: 'text', clearable: true, placeholder: '已知时填写总册数' },
    { key: 'volume', label: '独立卷号 · Volume', kind: 'text', clearable: true, placeholder: '来源明确提供独立卷号时填写', hint: '仅本地作品可编辑，不自动重复 Number；Komga 没有独立 Volume 编辑接口。' },
  ] },
  { id: 'creators', label: '作者与出版', fields: [
    { key: 'authors', label: '作者', kind: 'multi', allowCreate: true, placeholder: '输入原文姓名后按 Enter，可添加多位', hint: '作者至少填写一位；填写后将替换原作者列表。' },
    { key: 'pencillers', label: '绘画者', kind: 'multi', allowCreate: true, clearable: true, placeholder: '输入姓名后按 Enter', hint: '仅填写与作者不同的绘画者；留空可清空。' },
    { key: 'publisher', label: '出版方 / 发行方', kind: 'text', clearable: true, placeholder: '保留出版方原始名称', hint: 'Komga 中为系列共享字段；远端同步需选全系列。' },
    { key: 'translator', label: '译者 / 汉化组', kind: 'text', clearable: true, placeholder: '去除外层中括号，直接填写名称', hint: '原创可留空；“未知汉化”等占位仍需核对后才能审核。', wide: true },
  ] },
  { id: 'classification', label: '内容与分级', fields: [
    { key: 'genre', label: '内容类型', kind: 'select', options: options(comicGenres), clearable: true, placeholder: '请选择六类之一', hint: '仅使用漫画、小说、CG集、画集、设定集、插画集；清空后保留待补全草稿。' },
    { key: 'age_rating', label: '年龄分级', kind: 'select', options: ageRatings, hint: '确认本册实际分级；Komga 系列年龄仅作为共享参考。' },
    { key: 'color', label: '色彩', kind: 'select', options: options(colors), clearable: true, placeholder: '未确认可留空', hint: '以正文为准，不含封面；黑白和彩色页并存时选“部分彩色”。' },
    { key: 'tags', label: '题材与属性标签', kind: 'multi', allowCreate: true, clearable: true, placeholder: '如恋爱、奇幻、日常，按 Enter 添加', hint: '只填写普通标签，替换原列表；可清空为草稿。原作、发行、平台、色彩等请勾选对应分类，不在此填写分类前缀。' },
    { key: 'original_works', label: '原作', kind: 'original', clearable: true, placeholder: '一个原作 IP，未知时留空', hint: '替换为一个原作；未勾选时完整保留原有列表，旧多值需逐本核对。' },
    { key: 'characters', label: '角色', kind: 'multi', allowCreate: true, clearable: true, placeholder: '每个角色分别添加', hint: '写入 Characters 和“角色:”标签；填写后替换原列表。' },
    { key: 'uncensored', label: '修正状态', kind: 'boolean', hint: '仅当所选作品最终分级全部为 R18 时显示；“有修正”为默认，不生成标签。' },
  ] },
  { id: 'release', label: '发行与平台', fields: [
    { key: 'release_type', label: '发行性质', kind: 'select', options: options(releaseTypes), clearable: true, placeholder: '未知可留空' },
    { key: 'publication_format', label: '出版形态', kind: 'select', options: options(publicationFormats), allowCreate: true, clearable: true, placeholder: '选择或输入出版形态' },
    { key: 'release_methods', label: '发布方式', kind: 'multi', options: options(releaseMethods), clearable: true, placeholder: '可多选，未知时留空', hint: '替换原发布方式列表，不按平台推断。' },
    { key: 'release_platforms', label: '发布平台', kind: 'multi', options: options(releasePlatforms), allowCreate: true, clearable: true, placeholder: '可多选，也可输入新平台', hint: '填写后替换原平台列表；留空可清空。' },
  ] },
  { id: 'reading', label: '日期与阅读', fields: [
    { key: 'year', label: '发行年', kind: 'text', clearable: true, placeholder: '例如：2024' },
    { key: 'month', label: '发行月', kind: 'text', clearable: true, placeholder: '1–12' },
    { key: 'day', label: '发行日', kind: 'text', clearable: true, placeholder: '1–31，需配合有效年月', hint: '仅填写已知部分，不补造日期。' },
    { key: 'language', label: '语言', kind: 'text', clearable: true, placeholder: 'ja / zh-Hans / zh-Hant', hint: '使用正文语言代码，未知或不适用时可清空。' },
    { key: 'manga', label: 'ComicInfo Manga', kind: 'select', options: [{ label: '未指定', value: 'Unknown' }, { label: '漫画 · 不指定方向', value: 'Yes' }, { label: '从左向右', value: 'No' }, { label: '从右向左', value: 'YesAndRightToLeft' }], hint: '只编辑本地原生属性，与平台方向分别保存。' },
    { key: 'reading_direction', label: 'Komga 阅读方向', kind: 'select', options: readingDirections, clearable: true, placeholder: '未指定', hint: '系列共享字段；支持纵向和 Webtoon，清空表示未指定。' },
  ] },
  { id: 'sources', label: '来源链接', fields: [
    { key: 'web', label: '来源链接', kind: 'web', clearable: true, wide: true, placeholder: '每行一条完整的 https:// 或 http:// 链接', hint: '填写后替换原链接列表；留空可清空。链接中的逗号会保留。' },
  ] },
]
const fields = groups.flatMap(group => group.fields)
function initialValues(): BatchValues {
  return {
    authors: [], genre: '', collection_mode: 'oneshot', series: '', localized_series: '', localized_series_label: 'zh-Hans', number: '', count: '', volume: '', pencillers: [], translator: '', publisher: '',
    original_works: [], characters: [], release_type: '', publication_format: '', age_rating: 'Unknown',
    year: '', month: '', day: '', language: '', color: '', manga: 'Unknown', reading_direction: '', release_methods: [], release_platforms: [], tags: [], web: [], uncensored: false,
  }
}
const values = reactive<BatchValues>(initialValues())
const enabled = reactive(Object.fromEntries(fields.map(field => [field.key, false])) as Record<BatchKey, boolean>)
const defaultExpanded = ['creators', 'classification']
const expanded = ref<string[]>([...defaultExpanded])
const busy = ref(false)
const error = ref('')
const showCorrection = computed(() => enabled.age_rating ? values.age_rating === 'R18+' : !!props.ratings?.length && props.ratings.every(rating => rating === 'R18+'))
const selectedFields = computed(() => fields.filter(field => enabled[field.key] && !unavailableField(field.key) && (field.key !== 'uncensored' || showCorrection.value)))
const clearingFields = computed(() => selectedFields.value.filter(isClearing))
const webText = computed({
  get: () => values.web.join('\n'),
  set: (value: string) => { values.web = value.split(/\r?\n/) },
})
const originalWorkText = computed({ get: () => values.original_works[0] || '', set: (value: string) => { values.original_works = value.trim() ? [value] : [] } })
const allExpanded = computed(() => expanded.value.length === groups.length)
const seriesKeys: BatchKey[] = ['series', 'number', 'count', 'localized_series', 'localized_series_label']
const changingToOneShot = computed(() => enabled.collection_mode && values.collection_mode === 'oneshot')
const seriesFieldsDisabled = computed(() => enabled.collection_mode ? values.collection_mode === 'oneshot' : !!props.modes?.includes('oneshot'))
function unavailableField(key: BatchKey) { return (!!props.hasRemote && ['collection_mode', 'volume', 'manga'].includes(key)) || (seriesFieldsDisabled.value && seriesKeys.includes(key)) }
function disabledField(key: BatchKey) { return busy.value || unavailableField(key) }
function displayedFields(group: typeof groups[number]) { return group.fields.filter(field => field.key !== 'uncensored' || showCorrection.value) }
watch(seriesFieldsDisabled, value => { if (value) seriesKeys.forEach(key => { enabled[key] = false }) }, { flush: 'sync' })
watch(showCorrection, value => { if (!value) enabled.uncensored = false }, { flush: 'sync' })
function testId(key: BatchKey) { return key.replaceAll('_', '-') }
function isClearing(field: Field) {
  if (!field.clearable) return false
  const value = values[field.key]
  return Array.isArray(value) ? !value.some(item => item.trim()) : typeof value === 'string' && !value.trim()
}
function updateText(key: TextKey, value: string) { Object.assign(values, { [key]: value ?? '' }) }
function updateMulti(key: MultiKey, value: string[]) {
  values[key] = key === 'characters' ? classificationValues(value) : value
}
function toggleAll() { expanded.value = allExpanded.value ? [...defaultExpanded] : groups.map(group => group.id) }
function fieldError(key: BatchKey, message: string) {
  const group = groups.find(item => item.fields.some(field => field.key === key))
  if (group && !expanded.value.includes(group.id)) expanded.value.push(group.id)
  error.value = message
  ElMessage.warning(message)
}
watch(() => props.modelValue, (visible) => {
  if (!visible) return
  Object.assign(values, initialValues())
  fields.forEach(field => { enabled[field.key] = false })
  expanded.value = [...defaultExpanded]
  error.value = ''
})
async function save() {
  if (busy.value || !props.ids.length || !selectedFields.value.length) return
  error.value = ''
  if (enabled.authors && !values.authors.some(name => name.trim())) { fieldError('authors', '请输入至少一位作者，按 Enter 添加'); return }
  const managedPrefixes = new Set(['原作', '角色', '发行性质', '出版形态', '发布方式', '发布平台', '色彩', '修正状态', '内容类型', '年龄分级', '汉化状态'])
  if (enabled.tags && values.tags.some(tag => /[:：]/.test(tag) && managedPrefixes.has(tag.split(/[:：]/, 1)[0].trim()))) {
    fieldError('tags', '题材与属性中只填写普通标签；请勾选对应分类字段填写原作、发行、平台、色彩等信息'); return
  }
  if (enabled.series && !values.series.trim() && enabled.localized_series && values.localized_series.trim()) {
    fieldError('localized_series', '填写系列别名时需要系列名称；如要清空系列，请同时清空系列别名'); return
  }
  if (enabled.localized_series && values.localized_series.trim() && enabled.localized_series_label && !values.localized_series_label.trim()) {
    fieldError('localized_series_label', '系列别名非空时，请填写别名标签，例如 zh-Hans 或原名'); return
  }
  const changes: Partial<BatchValues> = {}
  for (const field of selectedFields.value) {
    const value = values[field.key]
    const normalized = Array.isArray(value) ? [...new Set(value.map(item => item.trim()).filter(Boolean))] : typeof value === 'string' ? value.trim() : value
    Object.assign(changes, { [field.key]: normalized })
  }
  busy.value = true
  emit('state', { busy: true, title: '批量保存作品资料' })
  try {
    const result = await invoke<{ count: number }>('batch_edit', { ids: [...props.ids], changes })
    emit('completed', result.count)
    emit('update:modelValue', false)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false; emit('state', { busy: false, title: '' }) }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="批量编辑资料" width="min(820px, 94vw)" top="5vh" class="batch-edit-dialog" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" destroy-on-close>
    <div class="bulk-intro">
      <p>为选中的 <strong>{{ ids.length }} 本作品</strong>统一修改资料。每项单独勾选，只保存勾选的字段。</p>
      <span>勾选可选字段后留空，即清空该字段。多值内容会替换原列表。</span>
    </div>
    <div class="bulk-toolbar">
      <span>已勾选 <strong data-testid="batch-selected-count">{{ selectedFields.length }}</strong> 项<span v-if="clearingFields.length" class="bulk-clear-count"> · 其中 {{ clearingFields.length }} 项将清空</span></span>
      <el-button data-testid="batch-expand-all" link :disabled="busy" @click="toggleAll">{{ allExpanded ? '收起分组' : '展开全部' }}</el-button>
    </div>
    <div class="bulk-scroll" data-testid="batch-fields-scroll">
      <p v-if="hasRemote" class="bulk-field-hint">包含 Komga 作品：组织方式、Volume 和原生 Manga 不在此修改。批量编辑只保存本机草稿，逐本核对审核后再同步。</p>
      <p v-if="changingToOneShot" class="bulk-field-hint">已选择 One-Shot：系列、编号、总数和别名的批量选项已停用；原有本地资料保留，导出时省略。</p>
      <p v-else-if="seriesFieldsDisabled" class="bulk-field-hint">{{ hasRemote ? '选中的作品包含 One-Shot，系列名称、编号和别名不可批量修改。' : '选中的作品包含 One-Shot。如需统一填写系列、编号或别名，请先勾选“修改作品组织方式”并选择“多卷系列”。' }}</p>
      <el-collapse v-model="expanded" class="bulk-groups">
        <el-collapse-item v-for="group in groups" :key="group.id" :name="group.id">
          <template #title><span class="bulk-group-title" :data-testid="`batch-section-${group.id}`">{{ group.label }}</span><span class="bulk-group-count">{{ displayedFields(group).filter(field => enabled[field.key]).length }} / {{ displayedFields(group).length }}</span></template>
          <div class="bulk-grid">
            <div v-for="field in displayedFields(group)" :key="field.key" class="bulk-field" :class="{ 'is-enabled': enabled[field.key], 'is-wide': field.wide }">
              <div class="bulk-field-heading">
                <el-checkbox :data-testid="`batch-enable-${testId(field.key)}`" v-model="enabled[field.key]" :disabled="disabledField(field.key)">修改{{ field.label }}</el-checkbox>
                <span v-if="enabled[field.key] && isClearing(field)" class="bulk-clear-label">将清空</span>
              </div>
              <el-select v-if="field.kind === 'multi'" :data-testid="`batch-${testId(field.key)}`" :aria-label="`批量修改${field.label}`" :model-value="values[field.key]" @update:model-value="updateMulti(field.key, $event)" multiple filterable :allow-create="field.allowCreate" default-first-option :reserve-keyword="false" :clearable="field.clearable" :disabled="disabledField(field.key) || !enabled[field.key]" :placeholder="field.placeholder">
                <template v-if="field.key === 'authors' || field.key === 'pencillers'"><el-option v-for="name in props.authors" :key="name" :label="name" :value="name" /></template>
                <template v-else><el-option v-for="option in field.options || []" :key="option.value" :label="option.label" :value="option.value" /></template>
              </el-select>
              <el-input v-else-if="field.kind === 'original'" data-testid="batch-original-work" aria-label="批量修改原作" v-model="originalWorkText" clearable :disabled="disabledField(field.key) || !enabled[field.key]" :placeholder="field.placeholder" />
              <el-select v-else-if="field.kind === 'select'" :data-testid="`batch-${testId(field.key)}`" :aria-label="`批量修改${field.label}`" :model-value="values[field.key]" @update:model-value="updateText(field.key, $event)" :filterable="field.allowCreate" :allow-create="field.allowCreate" default-first-option :clearable="field.clearable" :disabled="busy || !enabled[field.key]" :placeholder="field.placeholder || '请选择'">
                <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-select v-else-if="field.kind === 'boolean'" data-testid="batch-uncensored" aria-label="批量修改修正状态" v-model="values.uncensored" :disabled="busy || !enabled.uncensored">
                <el-option label="有修正 / 不标记无修正" :value="false" />
                <el-option label="无修正（仅 R18）" :value="true" />
              </el-select>
              <el-input v-else-if="field.kind === 'web'" data-testid="batch-web" aria-label="批量修改来源链接" v-model="webText" type="textarea" :rows="3" resize="vertical" :disabled="busy || !enabled.web" :placeholder="field.placeholder" />
              <el-input v-else :data-testid="`batch-${testId(field.key)}`" :aria-label="`批量修改${field.label}`" :model-value="values[field.key]" @update:model-value="updateText(field.key, $event)" :clearable="field.clearable" :disabled="busy || !enabled[field.key]" :placeholder="field.placeholder" />
              <p v-if="field.hint" class="bulk-field-hint">{{ field.hint }}</p>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
    <div class="bulk-status">
      <p>资料发生变化的作品将回到待审核状态，已有成品记录保留。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="batch-edit-error" />
    </div>
    <template #footer>
      <div class="bulk-footer"><span>作品名称、页数与封面保持原样</span><div><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :icon="Check" :loading="busy" :disabled="!selectedFields.length || !ids.length" @click="save">保存 {{ ids.length }} 本作品</el-button></div></div>
    </template>
  </el-dialog>
</template>

<style scoped>
.bulk-intro { color: #7d9072; font-size: 12px; line-height: 1.8; margin-bottom: 14px; }
.bulk-intro p { margin: 0 0 3px; }
.bulk-intro strong { color: #527348; font-weight: 600; }
.bulk-intro > span { font-size: 11px; }
.bulk-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; border: 1px solid #e3eadb; border-radius: 7px; background: #f8faf5; font-size: 12px; color: #728965; }
.bulk-clear-count, .bulk-clear-label { color: #a47736; }
.bulk-scroll { max-height: max(180px, calc(90vh - 295px)); overflow: auto; overscroll-behavior: contain; margin-top: 10px; padding-right: 5px; scrollbar-gutter: stable; }
.bulk-groups { border-top: 0; }
.bulk-group-title { color: #526d48; font-weight: 600; font-size: 12px; }
.bulk-group-count { color: #9aa88f; font-size: 11px; margin-left: 10px; font-weight: 400; }
.bulk-groups :deep(.el-collapse-item__header) { height: 42px; }
.bulk-groups :deep(.el-collapse-item__content) { padding-bottom: 14px; }
.bulk-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 11px; padding-top: 4px; }
.bulk-field { min-width: 0; padding: 10px 12px; border: 1px solid #e8eddf; border-radius: 8px; background: #fafbf8; }
.bulk-field.is-enabled { border-color: #c8d8ba; background: #f6f9f1; }
.bulk-field.is-wide { grid-column: 1 / -1; }
.bulk-field-heading { display: flex; align-items: center; justify-content: space-between; gap: 7px; margin-bottom: 6px; }
.bulk-field-heading .el-checkbox { height: 22px; margin-right: 0; }
.bulk-field-heading :deep(.el-checkbox__label) { font-size: 12px; }
.bulk-clear-label { flex-shrink: 0; font-size: 10px; }
.bulk-field > .el-select { display: block; width: 100%; }
.bulk-field-hint { font-size: 10px; line-height: 1.65; color: #95a28a; margin: 7px 0 0; }
.bulk-inline-error { font-size: 11px; line-height: 1.6; color: var(--el-color-danger); margin: 6px 0 0; }
.bulk-status { margin-top: 12px; }
.bulk-status > p { font-size: 11px; color: #8c9b80; margin: 0; line-height: 1.7; }
.bulk-status .batch-edit-error { margin-top: 8px; max-height: 82px; overflow: auto; }
.bulk-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.bulk-footer > span { font-size: 11px; color: #96a48b; text-align: left; }
.bulk-footer > div { flex-shrink: 0; }
@media (max-width: 620px) {
  .bulk-grid { grid-template-columns: minmax(0, 1fr); }
  .bulk-footer > span { display: none; }
  .bulk-footer { justify-content: flex-end; }
}
</style>
