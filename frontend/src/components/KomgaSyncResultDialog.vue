<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { KomgaSyncResult, KomgaSyncStatus } from '../types'
const props = defineProps<{ modelValue: boolean; result: KomgaSyncResult | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()
const tab = ref('books')
const filter = ref<'all' | KomgaSyncStatus>('all')
const labels: Record<KomgaSyncStatus, string> = { success: '成功', skipped: '跳过', failed: '失败' }
const colors: Record<KomgaSyncStatus, 'success' | 'warning' | 'danger'> = { success: 'success', skipped: 'warning', failed: 'danger' }
const source = computed(() => props.result ? tab.value === 'books' ? props.result.books : props.result.series : [])
const rows = computed(() => source.value.filter(item => filter.value === 'all' || item.status === filter.value))
const seriesFailures = computed(() => props.result?.series.filter(item => item.status === 'failed').length || 0)
watch(() => props.modelValue, visible => { if (visible) { tab.value = 'books'; filter.value = 'all' } })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" :title="result?.cancelled ? 'Komga 同步已取消' : 'Komga 同步结果'" width="min(880px, 94vw)" top="7vh" destroy-on-close class="komga-sync-result-dialog">
    <template v-if="result">
      <div class="sync-summary" data-testid="komga-sync-summary"><div><strong>{{ result.updated }}</strong><span>作品成功</span></div><div><strong>{{ result.skipped }}</strong><span>作品跳过</span></div><div :class="{ 'has-failures': result.failed }"><strong>{{ result.failed }}</strong><span>作品失败</span></div><div><strong>{{ result.seriesUpdated }}</strong><span>系列成功</span></div></div>
      <p v-if="result.unchanged" class="sync-result-note">{{ result.unchanged }} 本作品字段没有修改，已核对，无需写入。</p>
      <p v-if="result.cancelled" class="sync-result-note">已完成的更改保留。未完成的作品与系列请查看下方结果，可重新选择后同步。</p>
      <p v-else class="sync-result-note">逐册资料与系列资料分别处理。系列名称、类型、分级及别名等系列资料需选全系列才会更新。<span v-if="seriesFailures">有 {{ seriesFailures }} 项系列更新失败，请查看“系列资料”。</span></p>
      <div class="sync-result-toolbar"><el-radio-group v-model="tab" size="small"><el-radio-button value="books">作品资料 · {{ result.books.length }}</el-radio-button><el-radio-button value="series" data-testid="komga-series-results">系列资料 · {{ result.series.length }}</el-radio-button></el-radio-group><el-select v-model="filter" size="small" aria-label="筛选同步结果" class="sync-result-filter"><el-option label="全部结果" value="all" /><el-option v-for="(label, status) in labels" :key="status" :label="label" :value="status" /></el-select></div>
      <el-table :data="rows" max-height="420" class="sync-result-table" data-testid="komga-sync-results" empty-text="没有符合条件的结果"><el-table-column :label="tab === 'books' ? '作品' : '系列'" prop="title" min-width="170" show-overflow-tooltip /><el-table-column label="结果" width="82"><template #default="{ row }"><el-tag :type="colors[row.status as KomgaSyncStatus]" size="small" effect="plain">{{ labels[row.status as KomgaSyncStatus] }}</el-tag></template></el-table-column><el-table-column label="说明" min-width="280"><template #default="{ row }"><span class="sync-result-message">{{ row.message }}</span><p v-for="warning in row.warnings || []" :key="warning" class="sync-item-warning">{{ warning }}</p></template></el-table-column></el-table>
    </template>
    <template #footer><el-button type="primary" @click="emit('update:modelValue', false)">完成</el-button></template>
  </el-dialog>
</template>

<style scoped>
:global(.komga-sync-result-dialog) { display: flex; flex-direction: column; max-height: 86vh; }
:global(.komga-sync-result-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; }
.sync-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.sync-summary > div { padding: 13px 15px; border: 1px solid #e3eadf; background: #f7faf4; border-radius: 8px; }
.sync-summary strong { display: block; color: #39775d; font-size: 23px; font-weight: 600; line-height: 1.3; }
.sync-summary span { color: #83917b; font-size: 11px; }
.sync-summary .has-failures strong { color: #bf6661; }
.sync-result-note { margin: 0 0 16px; color: #82907a; font-size: 11px; line-height: 1.8; }
.sync-result-note > span { color: #a47736; }
.sync-result-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.sync-result-filter { width: 125px; }
.sync-result-message { font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.sync-item-warning { color: #a47736; font-size: 11px; line-height: 1.6; margin: 4px 0 0; }
@media (max-width: 560px) { .sync-summary { gap: 6px; }.sync-summary > div { padding: 10px; }.sync-result-toolbar { flex-wrap: wrap; } }
</style>
