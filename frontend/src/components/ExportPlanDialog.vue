<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Box, Check } from '@element-plus/icons-vue'
import type { ExportPlan } from '../types'

type MatchStatus = 'new' | 'existing' | 'possible_version'
interface EditionDetails {
  authors?: string[]
  pageCount?: number
  language?: string
  textVersion?: string
  number?: string
  series?: string
}
interface RemoteMatch extends EditionDetails {
  bookId: string
  title: string
  reasons?: string[]
}
interface LibraryCheck {
  state: 'checked' | 'unconfigured' | 'error'
  libraryName?: string
  message?: string
}
type PlanItem = ExportPlan['items'][number] & {
  bytes?: number
  local?: EditionDetails
  komga?: { status: MatchStatus; matches: RemoteMatch[]; matchCount: number }
}
type Plan = Omit<ExportPlan, 'items'> & { items: PlanItem[]; komga?: LibraryCheck }
interface ExportSelection { ids: string[]; confirmedIds: string[] }

const props = defineProps<{ modelValue: boolean; plan: Plan | null; busy: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm', value: ExportSelection): void
}>()
const selected = ref<string[]>([])
const reviewed = ref(false)
const state = computed(() => props.plan?.komga?.state || 'unconfigured')
const items = computed(() => props.plan?.items || [])
const selectedItems = computed(() => {
  const ids = new Set(selected.value)
  return items.value.filter(item => ids.has(item.id) && selectable(item))
})
const needsReview = (item: PlanItem) => state.value === 'checked' && (item.komga?.status === 'existing' || item.komga?.status === 'possible_version')
const flaggedItems = computed(() => selectedItems.value.filter(needsReview))
const flaggedCount = computed(() => items.value.filter(needsReview).length)
const warnings = computed(() => [...new Set(selectedItems.value.flatMap(item => item.warnings || []))])
const skipped = computed(() => items.value.length - selectedItems.value.length)
const canConfirm = computed(() => !!props.plan?.token && !props.busy && selectedItems.value.length > 0 && (!flaggedItems.value.length || reviewed.value))
const statuses = {
  new: { text: '未发现匹配', type: 'success' },
  existing: { text: '疑似已入库', type: 'warning' },
  possible_version: { text: '可能是不同版本', type: 'warning' },
} as const

function selectable(item: PlanItem) {
  return state.value === 'unconfigured' || (state.value === 'checked' && !!item.komga && Object.hasOwn(statuses, item.komga.status))
}
function resetSelection() {
  selected.value = items.value.filter(item => selectable(item) && (state.value === 'unconfigured' || item.komga?.status === 'new')).map(item => item.id)
  reviewed.value = false
}
function toggle(item: PlanItem, checked: boolean) {
  if (props.busy || !selectable(item)) return
  const next = new Set(selected.value)
  if (checked) next.add(item.id)
  else next.delete(item.id)
  selected.value = [...next]
  reviewed.value = false
}
function clearSelection() {
  selected.value = []
  reviewed.value = false
}
function leave() { if (!props.busy) emit('update:modelValue', false) }
function updateVisibility(value: boolean) { if (!value) leave() }
function confirm() {
  if (!canConfirm.value) return
  emit('confirm', { ids: selectedItems.value.map(item => item.id), confirmedIds: flaggedItems.value.map(item => item.id) })
}
function editionText(edition?: EditionDetails, pageCount?: number) {
  const pages = pageCount ?? edition?.pageCount
  return [edition?.authors?.join('、'), edition?.language, edition?.textVersion, edition?.number && `第 ${edition.number} 册`, pages ? `${pages} 页` : ''].filter(Boolean).join(' · ')
}
function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}
const sizeNote = computed(() => {
  if (!selectedItems.value.length || !props.plan) return ''
  const bytes = selectedItems.value.every(item => typeof item.bytes === 'number')
    ? selectedItems.value.reduce((sum, item) => sum + (item.bytes || 0), 0)
    : skipped.value ? undefined : props.plan.totalBytes
  return typeof bytes === 'number' ? `预计源内容 ${formatSize(bytes)}` : ''
})

watch([() => props.modelValue, () => props.plan?.token, state], () => {
  if (props.modelValue) resetSelection()
  else clearSelection()
}, { immediate: true })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="updateVisibility"
    title="确认生成计划" width="min(1230px, 96vw)" top="4vh" class="export-plan-dialog"
    :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
    <template v-if="plan">
      <div class="export-heading">
        <span class="export-heading-icon"><el-icon :size="20"><Box /></el-icon></span>
        <div>
          <h3>{{ selectedItems.length ? `即将生成 ${selectedItems.length} 本漫画` : '核对本次漫画' }}</h3>
          <p v-if="state === 'checked'">已比对 Komga 书库<strong>「{{ plan.komga?.libraryName || '当前书库' }}」</strong>的现有漫画。请核对标题、版本和成品路径。</p>
          <p v-else-if="state === 'unconfigured'">本次仅在成品目录生成 CBZ。请核对成品路径。</p>
          <p v-else>本次选择 {{ items.length }} 本漫画，书库比对待完成。</p>
        </div>
      </div>
      <el-alert v-if="state === 'error'" :title="plan.komga?.message || '无法完成 Komga 书库比对。请核对连接及书库后重试。'"
        type="error" :closable="false" show-icon class="export-alert" data-testid="export-library-error" />
      <el-alert v-else-if="state === 'checked' && flaggedCount"
        :title="`${flaggedCount} 本找到相似作品，已默认跳过。核对版本后，可自行勾选继续生成。`"
        type="warning" :closable="false" show-icon class="export-alert" data-testid="export-duplicate-warning" />
      <el-alert v-for="warning in warnings" :key="warning" :title="warning" type="warning" :closable="false"
        show-icon class="export-alert" />

      <div class="export-selection">
        <span>已选 <strong>{{ selectedItems.length }}</strong> / {{ items.length }} 本<span v-if="skipped">，跳过 {{ skipped }} 本</span></span>
        <div v-if="state !== 'error'">
          <el-button link :disabled="busy" data-testid="export-select-unmatched" @click="resetSelection">{{ state === 'checked' ? '只选未匹配作品' : '全选' }}</el-button>
          <el-button link :disabled="busy || !selectedItems.length" data-testid="export-clear-selection" @click="clearSelection">清空</el-button>
        </div>
      </div>

      <el-table :data="items" row-key="id" max-height="410" class="export-plan-table" data-testid="export-plan-table">
        <el-table-column label="生成" width="62" align="center">
          <template #default="{ row }">
            <el-checkbox data-testid="export-select-item" :data-comic-id="row.id" :aria-label="`生成 ${row.title}`"
              :model-value="selected.includes(row.id)" :disabled="busy || !selectable(row)" @update:model-value="toggle(row, $event === true)" />
          </template>
        </el-table-column>
        <el-table-column label="本地作品" min-width="200">
          <template #default="{ row }">
            <strong class="export-item-title">{{ row.title }}</strong>
            <small class="export-detail">{{ editionText(row.local, row.pageCount) }}</small>
            <small v-if="row.local?.series" class="export-detail">{{ row.local.series }}</small>
            <small v-if="row.attachmentCount" class="export-detail">{{ row.attachmentCount }} 项附件</small>
          </template>
        </el-table-column>
        <el-table-column v-if="state !== 'unconfigured'" label="Komga 比对" min-width="290">
          <template #default="{ row }">
            <div v-if="row.komga && statuses[row.komga.status as MatchStatus]" class="export-match">
              <el-tag :type="statuses[row.komga.status as MatchStatus].type" effect="plain" size="small" data-testid="export-match-status">
                {{ statuses[row.komga.status as MatchStatus].text }}
              </el-tag>
              <div v-for="match in row.komga.matches" :key="match.bookId" class="export-remote-book">
                <strong class="export-item-title">{{ match.title }}</strong>
                <small v-if="editionText(match)" class="export-detail">{{ editionText(match) }}</small>
                <small v-if="match.series && match.series !== match.title" class="export-detail">{{ match.series }}</small>
                <small v-if="match.reasons?.length" class="export-reasons">{{ match.reasons.join(' · ') }}</small>
              </div>
              <small v-if="row.komga.matchCount > row.komga.matches.length" class="export-detail">
                另有 {{ row.komga.matchCount - row.komga.matches.length }} 本可能匹配
              </small>
            </div>
            <span v-else class="export-detail">比对未完成</span>
          </template>
        </el-table-column>
        <el-table-column label="成品路径" min-width="280">
          <template #default="{ row }">
            <span class="export-path">{{ row.destination }}</span>
            <small class="export-detail" :title="row.source">源文件夹：{{ row.source }}</small>
          </template>
        </el-table-column>
      </el-table>

      <p class="export-note">
        生成独立的 CBZ 文件并校验，保留源文件和已入库成品。<span v-if="sizeNote">{{ sizeNote }}，请预留空间。</span>
      </p>
    </template>

    <template #footer>
      <div class="export-footer">
        <el-checkbox v-if="flaggedItems.length" v-model="reviewed" data-testid="export-confirm-duplicates" :disabled="busy">
          已核对 {{ flaggedItems.length }} 本可能重复的作品，确认继续生成
        </el-checkbox>
        <div>
          <el-button :disabled="busy" @click="leave">返回检查</el-button>
          <el-button data-testid="export-plan-submit" type="primary" :icon="Check" :disabled="!canConfirm" @click="confirm">确认生成并校验</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.export-plan-dialog) { display: flex; flex-direction: column; max-height: 92vh; }
:global(.export-plan-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.export-heading { display: flex; align-items: flex-start; gap: 13px; margin-bottom: 16px; }
.export-heading-icon { display: grid; flex: 0 0 38px; place-items: center; height: 38px; border-radius: 9px; background: #edf4e8; color: #427a58; }
.export-heading h3 { margin: 0 0 5px; font-size: 17px; line-height: 1.4; color: #294839; }
.export-heading p { margin: 0; color: #687e68; font-size: 12px; line-height: 1.65; }
.export-heading strong { color: #375943; }
.export-alert { margin: 0 0 11px; }
.export-selection { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 10px; margin: 13px 0 8px; color: #5b765e; font-size: 12px; }
.export-selection strong { color: #326245; }
.export-plan-table { width: 100%; font-size: 12px; }
.export-plan-table :deep(.cell) { line-height: 1.65; overflow-wrap: anywhere; }
.export-plan-table :deep(.el-table__cell) { vertical-align: top; padding: 10px 0; }
.export-plan-table :deep(.el-checkbox) { height: 23px; }
.export-item-title { display: block; color: #365440; font-weight: 600; }
.export-detail { display: block; margin-top: 4px; color: #697d68; font-size: 11px; }
.export-reasons { display: block; margin-top: 4px; color: #8d6637; font-size: 11px; }
.export-remote-book { margin-top: 7px; padding: 7px 10px; border: 1px solid #e5ebe1; border-radius: 6px; background: #f7faf5; }
.export-path { color: #507258; }
.export-note { margin: 11px 0 0; color: #687e68; line-height: 1.7; font-size: 12px; }
.export-footer { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 9px 16px; text-align: left; }
.export-footer > div { margin-left: auto; flex-shrink: 0; }
.export-footer .el-checkbox { height: auto; white-space: normal; }
.export-footer :deep(.el-checkbox__label) { white-space: normal; line-height: 1.6; font-size: 12px; }
</style>
