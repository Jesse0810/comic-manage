<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { KomgaSyncItem, ReviewSyncItem, ReviewSyncResult, ReviewSyncStatus } from '../types'
import KomgaSyncResultDialog from './KomgaSyncResultDialog.vue'

const props = defineProps<{ modelValue: boolean; result: ReviewSyncResult | null; error?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()
const syncDetailsVisible = ref(false)
const reviewLabels: Record<ReviewSyncStatus, string> = { approved: '审核通过', skipped: '审核跳过', failed: '审核失败' }
const reviewColors: Record<ReviewSyncStatus, 'success' | 'warning' | 'danger'> = { approved: 'success', skipped: 'warning', failed: 'danger' }
const syncById = computed(() => new Map((props.result?.sync?.books || []).map(item => [item.id, item])))
const seriesSkipped = computed(() => props.result?.sync?.series.filter(item => item.status === 'skipped').length || 0)
const seriesFailed = computed(() => props.result?.sync?.series.filter(item => item.status === 'failed').length || 0)
const cancelled = computed(() => props.result?.cancelled || props.result?.sync?.cancelled)
function syncItem(item: ReviewSyncItem): KomgaSyncItem | undefined { return syncById.value.get(item.id) }
function syncLabel(item: ReviewSyncItem) {
  const synced = syncItem(item)
  if (synced?.status === 'success') return synced.changed === false ? '无需写入' : '作品字段已写入'
  if (synced?.status === 'failed') return '同步失败'
  if (synced?.status === 'skipped') return '同步跳过'
  return item.status !== 'approved' ? '未同步' : cancelled.value ? '未同步（已取消）' : '未执行同步'
}
function syncColor(item: ReviewSyncItem): 'success' | 'warning' | 'danger' | 'info' {
  const synced = syncItem(item)
  return synced?.status === 'success' ? 'success' : synced?.status === 'failed' ? 'danger' : synced?.status === 'skipped' ? 'warning' : 'info'
}
watch(() => props.modelValue, () => { syncDetailsVisible.value = false })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" :title="cancelled ? '批量操作已取消' : '批量审核与同步结果'" width="min(1050px, 96vw)" top="5vh" destroy-on-close class="batch-review-result-dialog">
    <div data-testid="batch-review-results">
      <template v-if="result">
        <div class="batch-review-summary" data-testid="batch-review-summary"><div><strong>{{ result.reviewed }}</strong><span>审核通过</span></div><div><strong>{{ result.skipped }}</strong><span>审核跳过</span></div><div :class="{ 'has-failures': result.failed }"><strong>{{ result.failed }}</strong><span>审核失败</span></div></div>
        <p class="batch-review-result-note">本次检查 {{ result.total }} 本。审核与同步分别计算；审核通过的作品可能在同步阶段失败或跳过。已有成品保留。</p>
        <el-alert v-if="cancelled" title="操作已取消，已经完成的审核和远端更新保留。请核对逐本与系列结果；未完成项需重新检查后再执行。" type="warning" :closable="false" show-icon class="batch-result-alert" />
        <el-alert v-if="result.error" :title="result.error" type="error" :closable="false" show-icon class="batch-result-alert" />
        <div v-if="result.sync" class="batch-sync-summary" data-testid="batch-review-sync-summary">
          <p>作品同步：写入 {{ result.sync.updated }} 本，无需写入 {{ result.sync.unchanged || 0 }} 本，跳过 {{ result.sync.skipped }} 本，失败 {{ result.sync.failed }} 本。系列写入 {{ result.sync.seriesUpdated }} 项。</p>
          <el-button data-testid="batch-review-sync-details" link type="primary" @click="syncDetailsVisible = true">查看作品与系列同步明细</el-button>
          <p v-if="seriesSkipped || seriesFailed" class="batch-series-warning">部分系列资料未同步：跳过 {{ seriesSkipped }} 项，失败 {{ seriesFailed }} 项。作品字段写入成功不代表系列资料已更新，请查看同步明细。</p>
          <el-alert v-if="result.sync.error" :title="result.sync.error" type="error" :closable="false" show-icon class="batch-result-alert" />
        </div>
        <p v-else class="batch-review-result-note" data-testid="batch-review-no-sync">本次未开始 Komga 同步。请查看审核结果与错误说明，处理后重新检查。</p>
        <el-table :data="result.items" row-key="id" max-height="390" data-testid="batch-review-result-table" class="batch-review-result-table">
          <el-table-column label="作品" min-width="180"><template #default="{ row }">{{ row.title || '未命名作品' }}</template></el-table-column>
          <el-table-column label="审核结果" width="100"><template #default="{ row }"><el-tag :type="reviewColors[row.status as ReviewSyncStatus]" size="small" effect="plain">{{ reviewLabels[row.status as ReviewSyncStatus] }}</el-tag></template></el-table-column>
          <el-table-column label="作品同步结果" min-width="130"><template #default="{ row }"><el-tag :type="syncColor(row)" size="small" effect="plain">{{ syncLabel(row) }}</el-tag></template></el-table-column>
          <el-table-column label="说明" min-width="320"><template #default="{ row }"><p class="batch-row-message">{{ row.message }}</p><p v-if="syncItem(row)" class="batch-row-message">同步：{{ syncItem(row)?.message }}</p><p v-for="(warning, index) in syncItem(row)?.warnings || []" :key="index" class="batch-row-warning">{{ warning }}</p></template></el-table-column>
        </el-table>
      </template>
      <template v-else><el-alert :title="error || '未收到完整处理结果。'" type="error" :closable="false" show-icon /><p class="batch-review-result-note">已完成的更新可能保留。请检查当前列表和 Komga，再重新预检；软件不会自动再次同步。</p></template>
    </div>
    <template #footer><el-button data-testid="batch-review-done" type="primary" @click="emit('update:modelValue', false)">完成</el-button></template>
  </el-dialog>
  <KomgaSyncResultDialog v-model="syncDetailsVisible" :result="result?.sync || null" />
</template>

<style scoped>
:global(.batch-review-result-dialog) { display: flex; flex-direction: column; max-height: 90vh; }
:global(.batch-review-result-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.batch-review-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.batch-review-summary > div { padding: 12px 15px; border: 1px solid #e3eadf; background: #f7faf4; border-radius: 8px; }
.batch-review-summary strong { display: block; color: #39775d; font-size: 23px; font-weight: 600; }
.batch-review-summary span { color: #83917b; font-size: 11px; }
.batch-review-summary .has-failures strong { color: #bf6661; }
.batch-review-result-note, .batch-sync-summary p { margin: 0 0 12px; color: #82907a; font-size: 11px; line-height: 1.8; }
.batch-sync-summary { padding: 12px; margin-bottom: 14px; border: 1px solid #e3eadf; border-radius: 8px; }
.batch-sync-summary p { margin-bottom: 5px; }
.batch-sync-summary .el-button { height: auto; font-size: 12px; }
.batch-sync-summary .batch-series-warning, .batch-row-warning { color: #a47736; }
.batch-result-alert { margin-bottom: 12px; }
.batch-review-result-table :deep(.cell) { line-height: 1.7; overflow-wrap: anywhere; }
.batch-row-message, .batch-row-warning { margin: 2px 0; font-size: 11px; line-height: 1.7; }
</style>
