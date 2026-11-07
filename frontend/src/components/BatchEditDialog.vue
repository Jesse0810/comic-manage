<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import type { Metadata } from '../types'
import { ageRatings, classificationValues, comicGenres, errorMessage, publicationFormats } from '../types'
import { invoke } from '../api'

const props = defineProps<{ modelValue: boolean; ids: string[]; authors: string[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', count: number): void
}>()

type BatchKey = 'authors' | 'genre' | 'series' | 'localized_series' | 'number' | 'count' | 'volume'
  | 'pencillers' | 'translator' | 'original_works' | 'characters' | 'release_type' | 'publication_format'
  | 'age_rating' | 'year' | 'month' | 'day' | 'language' | 'black_and_white' | 'manga' | 'web' | 'uncensored'
type BatchValues = Pick<Metadata, BatchKey>
type MultiKey = 'authors' | 'pencillers' | 'original_works' | 'characters'
type TextKey = Exclude<BatchKey, MultiKey | 'web' | 'uncensored'>
type Option = { label: string; value: string }
type Field = {
  label: string; hint?: string; placeholder?: string; clearable?: boolean; wide?: boolean
} & (
  | { key: TextKey; kind: 'text' | 'select'; options?: Option[]; allowCreate?: boolean }
  | { key: MultiKey; kind: 'multi' }
  | { key: 'web'; kind: 'web' }
  | { key: 'uncensored'; kind: 'boolean' }
)
const options = (items: string[]): Option[] => items.map(value => ({ label: value, value }))
const groups: { id: string; label: string; fields: Field[] }[] = [
  { id: 'common', label: '常用资料', fields: [
    { key: 'authors', label: '作者', kind: 'multi', placeholder: '输入作者后按 Enter，可添加多位', hint: '作者至少填写一位；填写后将替换原作者列表。' },
    { key: 'genre', label: '内容类型', kind: 'select', options: options(comicGenres), allowCreate: true, clearable: true, placeholder: '选择或输入内容类型' },
    { key: 'series', label: '系列名称', kind: 'text', clearable: true, placeholder: '例如：系列原名', hint: '同系列可统一填写；留空时导出以各作品名称作为系列。' },
    { key: 'release_type', label: '发行性质', kind: 'select', options: options(['同人', '商业']), clearable: true, placeholder: '选择同人或商业' },
    { key: 'publication_format', label: '出版形态', kind: 'select', options: options(publicationFormats), clearable: true, placeholder: '选择出版形态' },
    { key: 'age_rating', label: '年龄分级', kind: 'select', options: ageRatings, hint: '未确认可选“未确认”；修正状态单独勾选修改。' },
  ] },
  { id: 'creators', label: '系列与创作补充', fields: [
    { key: 'localized_series', label: '系列译名', kind: 'text', clearable: true, placeholder: '仅填写系列的翻译名称', hint: '填写译名的作品须有系列名称；清空系列时也应勾选清空系列译名。' },
    { key: 'pencillers', label: '绘画者', kind: 'multi', clearable: true, placeholder: '输入姓名后按 Enter', hint: '仅填写与作者不同的绘画者；留空可清空。' },
    { key: 'translator', label: '译者 / 汉化组', kind: 'text', clearable: true, placeholder: '去除外层中括号，直接填写名称', hint: '原创可留空；“未知汉化”等占位仍需核对后才能审核。', wide: true },
  ] },
  { id: 'classification', label: '原作与角色', fields: [
    { key: 'original_works', label: '原作', kind: 'multi', clearable: true, placeholder: '输入原作后按 Enter', hint: '多个名称可用顿号分隔；替换原列表，导出为“原作:”标签。' },
    { key: 'characters', label: '角色', kind: 'multi', clearable: true, placeholder: '输入角色后按 Enter', hint: '多个名称可用顿号分隔；替换原列表，导出为“角色:”标签。' },
  ] },
  { id: 'numbering', label: '编号与日期', fields: [
    { key: 'number', label: '系列内编号', kind: 'text', clearable: true, placeholder: '例如：1 或 1.5', hint: '会为所选作品填写相同编号；连续编号请逐本编辑。' },
    { key: 'count', label: '系列总数', kind: 'text', clearable: true, placeholder: '已知时填写总册数' },
    { key: 'volume', label: '卷号', kind: 'text', clearable: true, placeholder: '有明确卷号时填写', hint: '不会根据系列编号自动填入。' },
    { key: 'year', label: '发行年', kind: 'text', clearable: true, placeholder: '例如：2024' },
    { key: 'month', label: '发行月', kind: 'text', clearable: true, placeholder: '1–12' },
    { key: 'day', label: '发行日', kind: 'text', clearable: true, placeholder: '1–31，需配合有效年月' },
  ] },
  { id: 'reading', label: '阅读与来源', fields: [
    { key: 'language', label: '语言', kind: 'text', placeholder: 'ja / zh-Hans / zh-Hant', hint: '使用语言代码，不能留空。' },
    { key: 'black_and_white', label: '色彩', kind: 'select', options: [{ label: '未确认', value: 'Unknown' }, { label: '黑白', value: 'Yes' }, { label: '彩色 / 非黑白', value: 'No' }] },
    { key: 'manga', label: '漫画与阅读方向', kind: 'select', options: [{ label: '未确认', value: 'Unknown' }, { label: '漫画', value: 'Yes' }, { label: '非漫画', value: 'No' }, { label: '漫画 · 从右向左', value: 'YesAndRightToLeft' }] },
    { key: 'uncensored', label: '修正状态', kind: 'boolean', hint: '“无修正”要求所选作品的最终分级均为 R18；可同时勾选修改年龄分级。' },
    { key: 'web', label: '来源链接', kind: 'web', clearable: true, wide: true, placeholder: '每行一条完整的 https:// 或 http:// 链接', hint: '填写后替换原链接列表；留空可清空。链接中的逗号会保留。' },
  ] },
]
const fields = groups.flatMap(group => group.fields)
function initialValues(): BatchValues {
  return {
    authors: [], genre: '', series: '', localized_series: '', number: '', count: '', volume: '', pencillers: [], translator: '',
    original_works: [], characters: [], release_type: '', publication_format: '', age_rating: 'Unknown',
    year: '', month: '', day: '', language: '', black_and_white: 'Unknown', manga: 'Unknown', web: [], uncensored: false,
  }
}
const values = reactive<BatchValues>(initialValues())
const enabled = reactive(Object.fromEntries(fields.map(field => [field.key, false])) as Record<BatchKey, boolean>)
const expanded = ref<string[]>(['common'])
const busy = ref(false)
const error = ref('')
const selectedFields = computed(() => fields.filter(field => enabled[field.key]))
const clearingFields = computed(() => selectedFields.value.filter(isClearing))
const webText = computed({
  get: () => values.web.join('\n'),
  set: (value: string) => { values.web = value.split(/\r?\n/) },
})
const allExpanded = computed(() => expanded.value.length === groups.length)
const correctionConflict = computed(() => enabled.uncensored && values.uncensored && enabled.age_rating && values.age_rating !== 'R18+')
function testId(key: BatchKey) { return key.replaceAll('_', '-') }
function isClearing(field: Field) {
  if (!field.clearable) return false
  const value = values[field.key]
  return Array.isArray(value) ? !value.some(item => item.trim()) : typeof value === 'string' && !value.trim()
}
function updateText(key: TextKey, value: string) { Object.assign(values, { [key]: value ?? '' }) }
function updateMulti(key: MultiKey, value: string[]) {
  values[key] = key === 'characters' || key === 'original_works' ? classificationValues(value) : value
}
function toggleAll() { expanded.value = allExpanded.value ? ['common'] : groups.map(group => group.id) }
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
  expanded.value = ['common']
  error.value = ''
})
async function save() {
  if (busy.value || !props.ids.length || !selectedFields.value.length) return
  error.value = ''
  if (enabled.authors && !values.authors.some(name => name.trim())) { fieldError('authors', '请输入至少一位作者，按 Enter 添加'); return }
  if (enabled.language && !values.language.trim()) { fieldError('language', '语言不能为空，请填写 ja、zh-Hans 等语言代码'); return }
  if (correctionConflict.value) { fieldError('uncensored', '无修正仅适用于 R18 作品，请调整年龄分级或修正状态'); return }
  if (enabled.series && !values.series.trim() && enabled.localized_series && values.localized_series.trim()) {
    fieldError('localized_series', '填写系列译名时需要系列名称；如要清空系列，请同时清空系列译名'); return
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
      <el-collapse v-model="expanded" class="bulk-groups">
        <el-collapse-item v-for="group in groups" :key="group.id" :name="group.id">
          <template #title><span class="bulk-group-title">{{ group.label }}</span><span class="bulk-group-count">{{ group.fields.filter(field => enabled[field.key]).length }} / {{ group.fields.length }}</span></template>
          <div class="bulk-grid">
            <div v-for="field in group.fields" :key="field.key" class="bulk-field" :class="{ 'is-enabled': enabled[field.key], 'is-wide': field.wide }">
              <div class="bulk-field-heading">
                <el-checkbox :data-testid="`batch-enable-${testId(field.key)}`" v-model="enabled[field.key]" :disabled="busy">修改{{ field.label }}</el-checkbox>
                <span v-if="enabled[field.key] && isClearing(field)" class="bulk-clear-label">将清空</span>
              </div>
              <el-select v-if="field.kind === 'multi'" :data-testid="`batch-${testId(field.key)}`" :aria-label="`批量修改${field.label}`" :model-value="values[field.key]" @update:model-value="updateMulti(field.key, $event)" multiple filterable allow-create default-first-option :reserve-keyword="false" :disabled="busy || !enabled[field.key]" :placeholder="field.placeholder">
                <el-option v-for="name in field.key === 'authors' || field.key === 'pencillers' ? props.authors : []" :key="name" :label="name" :value="name" />
              </el-select>
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
              <p v-if="field.key === 'uncensored' && correctionConflict" class="bulk-inline-error">当前勾选的年龄分级不是 R18，请调整后保存。</p>
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
