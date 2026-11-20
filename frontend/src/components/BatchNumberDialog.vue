<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Check, Rank } from '@element-plus/icons-vue'
import { invoke } from '../api'
import type { Comic } from '../types'
import { displayTitle, errorMessage } from '../types'

const props = defineProps<{ modelValue: boolean; comics: Comic[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', count: number): void
}>()

const rows = ref<Comic[]>([])
const start = ref<number | undefined>(1)
const busy = ref(false)
const error = ref('')
const announcement = ref('')
const draggedId = ref('')
const insertion = ref<{ id: string; after: boolean } | null>(null)
const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const maxStart = computed(() => 2147483647 - Math.max(rows.value.length - 1, 0))
const validStart = computed(() => typeof start.value === 'number' && Number.isSafeInteger(start.value)
  && start.value >= 0 && start.value <= maxStart.value)
const changes = computed(() => validStart.value && rows.value.some((comic, index) => comic.metadata.number !== proposed(index)))

watch(() => props.modelValue, (visible) => {
  if (!visible) return
  rows.value = [...props.comics].sort((a, b) => collator.compare(displayTitle(a.metadata), displayTitle(b.metadata)))
  start.value = 1
  error.value = ''
  announcement.value = ''
  resetDrag()
}, { immediate: true })

function proposed(index: number) {
  return validStart.value ? String(start.value! + index) : ''
}

function move(index: number, destination: number, focus?: HTMLElement | null) {
  if (busy.value || index === destination || destination < 0 || destination >= rows.value.length) return
  const next = [...rows.value]
  const [comic] = next.splice(index, 1)
  next.splice(destination, 0, comic)
  rows.value = next
  announcement.value = '已将《' + displayTitle(comic.metadata) + '》移到第 ' + (destination + 1) + ' 位。'
  if (focus) void nextTick(() => {
    const target = focus.matches(':disabled') ? focus.closest('li')?.querySelector<HTMLElement>('.number-grip') : focus
    target?.focus()
  })
}

function moveFromControl(index: number, destination: number, event: Event) {
  move(index, destination, event.currentTarget as HTMLElement)
}

function beginDrag(event: DragEvent, id: string) {
  if (!event.dataTransfer || busy.value || rows.value.length < 2) {
    event.preventDefault()
    return
  }
  draggedId.value = id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', id)
}

function dragAfter(event: DragEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  return event.clientY > rect.top + rect.height / 2
}

function dragOver(event: DragEvent, id: string) {
  if (busy.value || !draggedId.value) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  insertion.value = draggedId.value === id ? null : { id, after: dragAfter(event) }
}

function drop(event: DragEvent, index: number) {
  if (busy.value || !draggedId.value) return
  event.preventDefault()
  const from = rows.value.findIndex(comic => comic.id === draggedId.value)
  const position = index + Number(dragAfter(event))
  if (from >= 0) move(from, position > from ? position - 1 : position)
  resetDrag()
}

function resetDrag() {
  draggedId.value = ''
  insertion.value = null
}

async function save() {
  const first = start.value
  if (busy.value || !changes.value || typeof first !== 'number') return
  busy.value = true
  emit('state', { busy: true, title: '批量保存册编号' })
  error.value = ''
  try {
    const result = await invoke<{ count: number }>('batch_number', { ids: rows.value.map(comic => comic.id), start: first })
    emit('update:modelValue', false)
    emit('completed', result.count)
  } catch (reason) {
    error.value = errorMessage(reason).replace(/^Error invoking remote method 'comic:call': (?:Error: )?/, '')
  } finally {
    busy.value = false
    emit('state', { busy: false, title: '' })
  }
}
</script>

<template>
  <el-dialog title="批量设置册编号" :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)"
    width="min(790px, 94vw)" top="5vh" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" destroy-on-close>
    <div data-testid="batch-number-dialog">
      <div class="number-toolbar">
        <div>
          <p>已勾选 {{ rows.length }} 本作品</p>
          <span v-if="rows.length > 1">初始按作品名排序。拖动把手或使用上下按钮调整顺序。</span>
        </div>
        <div class="number-start">
          <label for="batch-number-first">起始编号</label>
          <el-input-number id="batch-number-first" data-testid="batch-number-start" v-model="start"
            :min="0" :max="maxStart" :step="1" step-strictly :disabled="busy" controls-position="right" />
        </div>
      </div>
      <div class="number-board">
        <div class="number-columns" aria-hidden="true">
          <span></span><span>作品</span><span>原编号</span><span>新编号</span><span></span>
        </div>
        <ol class="number-list" aria-label="册编号顺序">
          <li v-for="(comic, index) in rows" :key="comic.id" class="number-row" data-testid="batch-number-row" :data-comic-id="comic.id"
            :class="{ dragging: draggedId === comic.id, 'insert-before': insertion?.id === comic.id && !insertion.after,
              'insert-after': insertion?.id === comic.id && insertion.after }"
            @dragover="dragOver($event, comic.id)" @drop="drop($event, index)">
            <button type="button" class="number-grip" data-testid="batch-number-drag" :disabled="busy || rows.length < 2"
              :draggable="!busy && rows.length > 1" :aria-label="'调整《' + displayTitle(comic.metadata) + '》的顺序'"
              title="拖动排序，也可按 Alt + ↑ / ↓ 调整"
              @dragstart="beginDrag($event, comic.id)" @dragend="resetDrag"
              @keydown.alt.up.prevent="moveFromControl(index, index - 1, $event)"
              @keydown.alt.down.prevent="moveFromControl(index, index + 1, $event)">
              <el-icon><Rank /></el-icon>
            </button>
            <div class="number-work">
              <strong :title="displayTitle(comic.metadata)">{{ displayTitle(comic.metadata) }}</strong>
              <span v-if="comic.metadata.series || comic.metadata.text_version">
                {{ [comic.metadata.series, comic.metadata.text_version].filter(Boolean).join(' · ') }}
              </span>
            </div>
            <span class="number-old" :title="comic.metadata.number" :aria-label="'原编号 ' + (comic.metadata.number || '未填写')">{{ comic.metadata.number || '—' }}</span>
            <span class="number-new" data-testid="batch-number-preview" :aria-label="'新编号 ' + (proposed(index) || '未填写')">{{ proposed(index) || '—' }}</span>
            <div class="number-controls">
              <el-button :icon="ArrowUp" text size="small" data-testid="batch-number-up" title="上移"
                :aria-label="'上移《' + displayTitle(comic.metadata) + '》'" :disabled="busy || index === 0"
                @click="moveFromControl(index, index - 1, $event)" />
              <el-button :icon="ArrowDown" text size="small" data-testid="batch-number-down" title="下移"
                :aria-label="'下移《' + displayTitle(comic.metadata) + '》'" :disabled="busy || index === rows.length - 1"
                @click="moveFromControl(index, index + 1, $event)" />
            </div>
          </li>
        </ol>
      </div>
      <span class="number-sr-only" role="status">{{ announcement }}</span>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="number-error" />
    </div>
    <template #footer>
      <div class="number-footer">
        <span>册编号有变化的作品将回到待审核。</span>
        <div>
          <el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button>
          <el-button :icon="Check" type="primary" data-testid="batch-number-save" :loading="busy" :disabled="!changes" @click="save">保存编号</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.number-toolbar,
.number-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.number-toolbar { margin-bottom: 14px; align-items: end; }
.number-toolbar p { margin: 0 0 4px; font-weight: 600; color: var(--el-text-color-primary); }
.number-toolbar span, .number-footer > span { font-size: 12px; }
.number-footer > div { flex-shrink: 0; }
.number-start { display: flex; align-items: center; gap: 8px; flex-shrink: 0; white-space: nowrap; }
.number-start :deep(.el-input-number) { width: 140px; }
.number-board { border: 1px solid var(--el-border-color); border-radius: 8px; background: var(--el-bg-color); overflow: auto;
  overscroll-behavior: contain; max-height: max(180px, min(54vh, calc(88vh - 205px))); }
.number-columns, .number-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) minmax(58px, 82px) minmax(58px, 82px) 68px;
  gap: 9px;
  align-items: center;
  padding: 8px 12px;
}
.number-columns { position: sticky; top: 0; z-index: 1; background: var(--el-fill-color-light); font-size: 12px; }
.number-columns > span:nth-child(3), .number-columns > span:nth-child(4) { text-align: center; }
.number-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.number-row { position: relative; min-height: 52px; border-top: 1px solid var(--el-border-color-lighter); }
.number-row:focus-within { background: var(--el-fill-color-lighter); }
.number-row.dragging { opacity: .45; }
.number-row.insert-before { box-shadow: inset 0 2px var(--el-color-primary); }
.number-row.insert-after { box-shadow: inset 0 -2px var(--el-color-primary); }
.number-grip {
  width: 28px; height: 28px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 4px;
  color: var(--el-text-color-secondary); background: transparent; font-size: 17px; cursor: grab;
}
.number-grip:active { cursor: grabbing; }
.number-grip:disabled { cursor: default; opacity: .4; }
.number-work { display: grid; gap: 3px; }
.number-work strong, .number-work > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.number-work strong { font-size: 13px; font-weight: 600; color: var(--el-text-color-primary); }
.number-work > span { font-size: 11px; }
.number-old, .number-new { justify-self: center; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.number-new { color: var(--el-color-primary); background: var(--el-color-primary-light-9); padding: 2px 8px; border-radius: 4px; }
.number-controls { display: flex; justify-content: flex-end; gap: 2px; }
.number-controls :deep(.el-button) { margin: 0; padding: 0; min-width: 30px; height: 30px; }
.number-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.number-error { margin-top: 12px; }
</style>
