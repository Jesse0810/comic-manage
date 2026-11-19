<script setup lang="ts">
import { computed, ref } from 'vue'
import { Close, Filter, Plus, Sort, SortDown, SortUp } from '@element-plus/icons-vue'
import {
  isPresenceOperator, libraryFilterField, libraryFilterFields, libraryOperators, librarySortFields, validLibraryCondition,
} from '../library-fields'
import type { LibraryCondition, LibraryFilterKey, LibraryOperator, LibrarySort, LibrarySortKey } from '../library-fields'

interface DraftCondition extends LibraryCondition { id: number }
const props = defineProps<{ conditions: LibraryCondition[]; sort: LibrarySort; disabled: boolean }>()
const emit = defineEmits<{
  (event: 'update:conditions', value: LibraryCondition[]): void
  (event: 'update:sort', value: LibrarySort): void
}>()

const filterOpen = ref(false)
const sortOpen = ref(false)
const draft = ref<DraftCondition[]>([])
const maximumConditions = 20
let nextId = 1
const valid = computed(() => draft.value.every(validLibraryCondition))
const sortLabel = computed(() => librarySortFields.find(item => item.key === props.sort?.key)?.label || '')
const sortTitle = computed(() => props.sort ? `${sortLabel.value} · ${props.sort.direction === 'asc' ? '升序' : '降序'}` : '选择排序字段')

function newCondition(): DraftCondition {
  return { id: nextId++, field: 'title', operator: 'contains', value: '', upper: '' }
}
function openConditions() {
  sortOpen.value = false
  draft.value = props.conditions.length
    ? props.conditions.map(condition => ({ ...condition, id: nextId++ }))
    : [newCondition()]
  filterOpen.value = true
}
function addCondition() {
  if (draft.value.length < maximumConditions) draft.value.push(newCondition())
}
function changeField(condition: DraftCondition, field: LibraryFilterKey) {
  condition.field = field
  condition.operator = libraryOperators(field)[0].value
  condition.value = ''
  condition.upper = ''
}
function changeOperator(condition: DraftCondition, operator: LibraryOperator) {
  condition.operator = operator
  if (isPresenceOperator(operator)) condition.value = ''
  if (operator !== 'between') condition.upper = ''
}
function applyConditions() {
  if (props.disabled || !valid.value) return
  emit('update:conditions', draft.value.map(({ id, ...condition }) => condition))
  filterOpen.value = false
}
function changeSortKey(key: LibrarySortKey) {
  emit('update:sort', { key, direction: props.sort?.direction || 'asc' })
}
function changeSortDirection(direction: 'asc' | 'desc') {
  if (props.sort) emit('update:sort', { key: props.sort.key, direction })
}
</script>

<template>
  <div class="library-field-controls">
    <el-button
      data-testid="library-filter-open"
      :icon="Filter"
      :type="conditions.length ? 'primary' : 'default'"
      :plain="conditions.length > 0"
      :disabled="disabled"
      :aria-label="conditions.length ? `按字段筛选，已应用 ${conditions.length} 条条件` : '按字段筛选'"
      @click="openConditions"
    >筛选<span v-if="conditions.length" class="library-filter-count">{{ conditions.length }}</span></el-button>
    <el-popover v-model:visible="sortOpen" :width="244" placement="bottom-end" trigger="click">
      <template #reference>
        <el-button
          data-testid="library-sort-open"
          :icon="sort ? sort.direction === 'asc' ? SortUp : SortDown : Sort"
          :type="sort ? 'primary' : 'default'"
          :plain="!!sort"
          :disabled="disabled"
          :title="sortTitle"
          :aria-label="`排序，${sortTitle}`"
        >排序</el-button>
      </template>
      <div class="library-sort-panel">
        <span class="field-control-label">排序字段</span>
        <el-select
          data-testid="library-sort-field"
          aria-label="排序字段"
          :model-value="sort?.key || ''"
          :teleported="false"
          :disabled="disabled"
          placeholder="默认顺序"
          @update:model-value="changeSortKey"
        >
          <el-option v-for="field in librarySortFields" :key="field.key" :value="field.key" :label="field.label" />
        </el-select>
        <el-radio-group data-testid="library-sort-direction" aria-label="排序方向" :model-value="sort?.direction || 'asc'" :disabled="!sort || disabled" size="small" @update:model-value="changeSortDirection">
          <el-radio-button value="asc">升序</el-radio-button>
          <el-radio-button value="desc">降序</el-radio-button>
        </el-radio-group>
        <el-button v-if="sort" data-testid="library-sort-reset" link :disabled="disabled" @click="emit('update:sort', null); sortOpen = false">恢复默认顺序</el-button>
      </div>
    </el-popover>
  </div>

  <el-dialog v-model="filterOpen" title="按字段筛选" width="min(680px, 94vw)" top="10vh" append-to-body destroy-on-close :close-on-click-modal="false">
    <p class="field-filter-note">作品需满足全部条件。现有的搜索、作者和来源筛选同时生效。</p>
    <div class="field-filter-draft">
      <div v-for="(condition, index) in draft" :key="condition.id" class="field-filter-row" data-testid="library-filter-row">
        <el-select
          data-testid="library-filter-field" :model-value="condition.field" :aria-label="`条件 ${index + 1} 的字段`"
          filterable placeholder="选择字段" @update:model-value="changeField(condition, $event)"
        >
          <el-option v-for="field in libraryFilterFields" :key="field.key" :value="field.key" :label="field.label" />
        </el-select>
        <el-select
          data-testid="library-filter-operator" :model-value="condition.operator" :aria-label="`条件 ${index + 1} 的规则`"
          @update:model-value="changeOperator(condition, $event)"
        >
          <el-option v-for="operator in libraryOperators(condition.field)" :key="operator.value" :value="operator.value" :label="operator.label" />
        </el-select>
        <div class="field-filter-values" :class="{ 'field-filter-values-range': condition.operator === 'between' }">
          <el-select
            v-if="!isPresenceOperator(condition.operator) && libraryFilterField(condition.field).kind === 'choice'"
            v-model="condition.value" data-testid="library-filter-value" :aria-label="`条件 ${index + 1} 的值`"
            placeholder="选择值" filterable
          >
            <el-option v-for="option in libraryFilterField(condition.field).choices" :key="option.value" :value="option.value" :label="option.label" />
          </el-select>
          <template v-else-if="!isPresenceOperator(condition.operator)">
            <el-input
              v-model="condition.value" data-testid="library-filter-value" :aria-label="`条件 ${index + 1} 的值`"
              :inputmode="libraryFilterField(condition.field).kind === 'number' ? 'numeric' : 'text'" :maxlength="200"
              :placeholder="condition.operator === 'between' ? '下限' : condition.field === 'language' ? '如 ja 或 zh-Hans' : '输入值'"
              @keyup.enter="applyConditions"
            />
            <template v-if="condition.operator === 'between'">
              <span aria-hidden="true">至</span>
              <el-input v-model="condition.upper" data-testid="library-filter-upper" :aria-label="`条件 ${index + 1} 的上限`" inputmode="numeric" maxlength="8" placeholder="上限" @keyup.enter="applyConditions" />
            </template>
          </template>
        </div>
        <el-button
          data-testid="library-filter-remove" :icon="Close" link :aria-label="`移除条件 ${index + 1}`" title="移除此条件"
          @click="draft.splice(index, 1)"
        />
        <p v-if="condition.operator === 'between' && condition.value && condition.upper && !validLibraryCondition(condition)" class="field-filter-error" role="status">输入有效整数，下限应小于或等于上限。</p>
      </div>
      <el-button data-testid="library-filter-add" :icon="Plus" link :disabled="draft.length >= maximumConditions" @click="addCondition">添加条件</el-button>
    </div>
    <template #footer>
      <div class="field-filter-footer">
        <el-button data-testid="library-filter-clear" :disabled="!draft.length" link @click="draft = []">清除条件</el-button>
        <div>
          <el-button @click="filterOpen = false">取消</el-button>
          <el-button data-testid="library-filter-apply" type="primary" :disabled="disabled || !valid" @click="applyConditions">应用筛选</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.library-field-controls { display: flex; align-items: center; gap: 7px; flex: 0 0 auto; }
.library-field-controls .el-button { margin: 0; padding: 8px 10px; font-size: 12px; }
.library-filter-count { margin-left: 4px; font-weight: 650; font-variant-numeric: tabular-nums; }
.library-sort-panel { display: grid; gap: 9px; }
.library-sort-panel > .el-button { justify-self: start; }
.field-control-label { color: #41644d; font-size: 12px; font-weight: 600; }
.field-filter-note { margin: 0 0 14px; color: #647a61; font-size: 13px; }
.field-filter-draft { max-height: 53vh; overflow-y: auto; padding: 1px 3px 4px; }
.field-filter-row { display: grid; grid-template-columns: minmax(96px, 1fr) 118px minmax(132px, 1.5fr) 24px; align-items: center; gap: 8px; padding: 10px 8px; margin-bottom: 7px; border-radius: 7px; background: #f6f9f4; }
.field-filter-values { min-width: 0; }
.field-filter-values > .el-select { width: 100%; }
.field-filter-values-range { display: flex; gap: 6px; align-items: center; }
.field-filter-values-range > span { font-size: 11px; color: #647a61; flex-shrink: 0; }
.field-filter-error { grid-column: 1 / -1; margin: 0; padding-left: 1px; color: #aa5351; font-size: 12px; }
.field-filter-footer { display: flex; align-items: center; justify-content: space-between; }
</style>
