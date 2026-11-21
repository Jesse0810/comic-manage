<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import type { KomgaAutoSyncJob, KomgaAutoSyncJobs, KomgaConfig } from '../types'
import { errorMessage, komgaAutoSyncLabels } from '../types'
import { invoke } from '../api'

const props = defineProps<{ modelValue: boolean; jobs: KomgaAutoSyncJob[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'updated', jobs: KomgaAutoSyncJob[]): void
  (e: 'state', value: { busy: boolean; title: string }): void
}>()
const busy = ref(false)
const error = ref('')
const selectedIds = ref<string[]>([])
const config = ref<KomgaConfig | null>(null)
const jobs = computed(() => props.jobs || [])
const summary = computed(() => ({
  synced: jobs.value.filter(job => job.status === 'Synced').length,
  pending: jobs.value.filter(job => ['PendingIndex', 'PendingSync', 'RetryableFailure', 'Validated', 'Packaging'].includes(job.status) || (job.status === 'Approved' && job.canRetry)).length,
  attention: jobs.value.filter(job => ['NeedsManualReview', 'PermanentFailure'].includes(job.status)).length,
}))
const normalizedUrl = (url: string) => url.trim().replace(/\/+$/, '')
function currentScope(job: KomgaAutoSyncJob) {
  return !!(config.value?.hasApiKey && config.value.url && config.value.libraryId) && normalizedUrl(job.serverUrl) === normalizedUrl(config.value!.url) && job.libraryId === config.value!.libraryId
}
function retryable(job: KomgaAutoSyncJob) {
  const configured = !!(config.value?.hasApiKey && config.value.url && config.value.libraryId)
  return configured && job.canRetry && (currentScope(job) || (job.status === 'Approved' && !job.serverUrl && !job.libraryId))
}
function statusLabel(job: KomgaAutoSyncJob) { return job.status === 'Approved' && job.canRetry ? '成品已生成 · 待创建任务' : komgaAutoSyncLabels[job.status] || job.status }
const retryIds = computed(() => selectedIds.value.filter(id => jobs.value.some(job => job.id === id && retryable(job))))
function setBusy(value: boolean, title = '') { busy.value = value; emit('state', { busy: value, title }) }
function timeText(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
function statusType(job: KomgaAutoSyncJob): 'success' | 'warning' | 'danger' | 'info' {
  return job.status === 'Synced' ? 'success' : job.status === 'PermanentFailure' ? 'danger' : ['NeedsManualReview', 'RetryableFailure'].includes(job.status) ? 'warning' : 'info'
}
async function load() {
  if (busy.value) return
  error.value = ''
  setBusy(true, '读取 Komga 自动同步任务')
  try {
    const [result, savedConfig] = await Promise.all([invoke<KomgaAutoSyncJobs>('komga_jobs', {}), window.comic.komgaConfig()])
    emit('updated', result.jobs)
    config.value = savedConfig
  } catch (reason) { error.value = errorMessage(reason) }
  finally { setBusy(false) }
}
async function retry() {
  const ids = [...retryIds.value]
  if (busy.value || !ids.length) return
  error.value = ''
  setBusy(true, '重新检查 Komga 自动同步任务')
  try {
    const result = await invoke<KomgaAutoSyncJobs & { count: number }>('komga_jobs_retry', { ids })
    emit('updated', result.jobs)
    selectedIds.value = []
    ElMessage.success(`已安排重新检查 ${result.count} 项，无需重新打包`)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { setBusy(false) }
}
watch(() => props.modelValue, visible => { if (visible) { selectedIds.value = []; void load() } })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="Komga 自动同步任务" width="min(1080px, 95vw)" top="5vh" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close class="komga-jobs-dialog">
    <div class="jobs-intro"><p>CBZ 生成并校验后，等待 Komga 扫描入库，再对账自定义实体。同步状态与作品审核、成品是否有效分别保存。</p><p>软件运行时自动处理当前连接的任务；重启后继续。等待入库或网络失败时，无需重新打包。</p></div>
    <div class="jobs-summary" data-testid="komga-auto-jobs-summary"><span>已同步 <strong>{{ summary.synced }}</strong></span><span>等待或重试 <strong>{{ summary.pending }}</strong></span><span>需处理 <strong>{{ summary.attention }}</strong></span><el-button data-testid="komga-jobs-refresh" :icon="Refresh" :loading="busy" :disabled="busy" @click="load">刷新状态</el-button></div>
    <el-alert v-if="config && config.autoSyncEnabled === false" title="自动处理已暂停。可回到 Komga 连接设置开启，任务会继续保留。" type="info" :closable="false" show-icon class="jobs-alert" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="jobs-alert" data-testid="komga-jobs-error" />
    <el-table :data="jobs" row-key="id" max-height="430" v-loading="busy" data-testid="komga-auto-jobs-table" @selection-change="(rows: KomgaAutoSyncJob[]) => selectedIds = rows.map(job => job.id)" empty-text="还没有自动同步任务。新审核并生成的本地漫画会出现在这里。">
      <el-table-column type="selection" width="42" :selectable="(job: KomgaAutoSyncJob) => !busy && retryable(job)" />
      <el-table-column label="作品与成品" min-width="230"><template #default="scope"><div class="job-title">{{ scope.row.title }}</div><div v-if="scope.row.relativePath || scope.row.outputPath" class="job-path" :title="scope.row.outputPath">{{ scope.row.relativePath || scope.row.outputPath }}</div><div v-if="scope.row.serverUrl" class="job-scope" :title="`${scope.row.serverUrl} · ${scope.row.libraryId}`">{{ currentScope(scope.row) ? '当前连接' : '其他连接' }} · {{ scope.row.libraryId }}</div><div v-else-if="scope.row.status === 'Approved' && scope.row.canRetry" class="job-scope">尚未绑定书库；重新检查将使用当前保存的连接</div><div v-else class="job-scope">完成生成并校验后关联目标书库</div></template></el-table-column>
      <el-table-column label="同步状态" min-width="175"><template #default="scope"><el-tag :type="statusType(scope.row)" effect="light">{{ statusLabel(scope.row) }}</el-tag><div class="job-meta">已尝试 {{ scope.row.attempts }} 次</div><div v-if="scope.row.bookId" class="job-meta" :title="scope.row.bookId">Book · {{ scope.row.bookId }}</div></template></el-table-column>
      <el-table-column label="最近结果与后续处理" min-width="290"><template #default="scope"><div v-if="scope.row.lastError" class="job-error">{{ scope.row.lastError }}</div><div v-else-if="scope.row.status === 'Synced'" class="job-ok">已回读核验实体和锁定状态</div><div v-else-if="scope.row.status === 'Approved' && scope.row.canRetry" class="job-meta">成品已生成，可从现有 CBZ 创建任务；无需重新打包。</div><div v-else-if="scope.row.status === 'Approved'" class="job-meta">审核已完成；请生成并校验 CBZ。</div><div v-else-if="scope.row.status === 'PendingIndex'" class="job-meta">正在等待唯一的路径与标题匹配。请确认成品目录由 Komga 监控。</div><div v-if="scope.row.nextAttemptAt" class="job-meta">下次检查：{{ timeText(scope.row.nextAttemptAt) }}</div><div v-if="scope.row.syncedAt" class="job-meta">最近成功：{{ timeText(scope.row.syncedAt) }}</div><div v-if="scope.row.updatedAt" class="job-meta">更新：{{ timeText(scope.row.updatedAt) }}</div></template></el-table-column>
    </el-table>
    <p class="jobs-help">多个匹配、标题不一致或资料冲突会暂停自动写入。核对后可勾选任务重新检查；作品已修改时，请先重新审核并生成。其他服务器或书库的任务需切回对应连接后处理。</p>
    <template #footer><div class="jobs-footer"><el-button data-testid="komga-jobs-retry" :disabled="busy || !retryIds.length" @click="retry">重新检查所选<span v-if="retryIds.length"> {{ retryIds.length }} 项</span></el-button><el-button :disabled="busy" type="primary" @click="emit('update:modelValue', false)">关闭</el-button></div></template>
  </el-dialog>
</template>

<style scoped>
:global(.komga-jobs-dialog) { max-height: 90vh; display: flex; flex-direction: column; }
:global(.komga-jobs-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; }
.jobs-intro { color: #778777; line-height: 1.7; font-size: 12px; margin-bottom: 15px; }
.jobs-intro p { margin: 4px 0; }
.jobs-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 20px; margin: 0 0 14px; font-size: 12px; color: #6f816f; }
.jobs-summary strong { color: #3d6550; margin-left: 6px; }
.jobs-summary .el-button { margin-left: auto; }
.jobs-alert { margin-bottom: 12px; }
.job-title { font-weight: 600; color: #3e5e48; margin-bottom: 5px; }
.job-path { overflow-wrap: anywhere; color: #7a8c79; font-size: 11px; line-height: 1.65; }
.job-scope, .job-meta { margin-top: 5px; font-size: 11px; line-height: 1.6; color: #8a9985; overflow-wrap: anywhere; }
.job-error { color: #a16f3d; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.job-ok { color: #3d795c; font-size: 12px; }
.jobs-help { margin: 14px 0 0; color: #8a9985; font-size: 11px; line-height: 1.8; }
.jobs-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
</style>
