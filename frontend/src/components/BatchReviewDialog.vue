<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check } from '@element-plus/icons-vue'
import type { ReviewSyncPlan, ReviewSyncPreviewItem } from '../types'

const props = defineProps<{ modelValue: boolean; plan: ReviewSyncPlan | null; busy: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm'): void
}>()
const confirmed = ref(false)
const canConfirm = computed(() => !!props.plan?.token && props.plan.ready > 0 && !props.plan.cancelled && !props.busy)
const fieldLabels: Record<string, string> = {
  title: '作品标题', series: '系列名称', number: '册编号', count: '系列总数', volume: '独立卷号',
  localized_series: '系列别名', localized_series_label: '别名标签', authors: '作者', pencillers: '绘画者',
  translator: '译者', publisher: '出版方', genre: '内容类型', age_rating: '年龄分级', tags: '题材与属性',
  original_works: '原作', characters: '角色', release_type: '发行性质', publication_format: '出版形态',
  release_methods: '发布方式', release_platforms: '发布平台', color: '色彩', uncensored: '修正状态',
  language: '语言', manga: 'ComicInfo Manga', reading_direction: '阅读方向',
  year: '发行年', month: '发行月', day: '发行日', summary: '简介', web: '来源链接', cover_index: '封面标记',
}
function fieldsText(item: ReviewSyncPreviewItem) {
  if (item.kind === 'folder' || item.kind === 'cbz') return '按本地完整资料同步'
  if (item.kind !== 'komga') return '来源待检查'
  return item.dirtyFields?.length
    ? item.dirtyFields.map(field => fieldLabels[field] || '其他已修改资料').join('、')
    : '未修改远端字段，无需重复写入'
}
function ageLabel(value: string) {
  return ({ Everyone: '全年龄', 'MA15+': 'MA15+', 'R18+': 'R18', Unknown: '待确认' } as Record<string, string>)[value] || value || '待补全'
}
watch([() => props.modelValue, () => props.plan?.token], () => { confirmed.value = false })
function confirm() { if (confirmed.value && canConfirm.value) emit('confirm') }
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="核对批量审核与同步" width="min(1180px, 96vw)" top="5vh" class="batch-review-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
    <template v-if="plan">
      <div class="review-plan-intro">
        <p>本次勾选 <strong>{{ plan.total }}</strong> 本作品，以下列出全部选中项，包括筛选后暂未显示的勾选作品。</p>
        <p>仅为 <strong>{{ plan.ready }}</strong> 本可通过作品执行审核并同步；<strong>{{ plan.blocked }}</strong> 本需处理的作品保留现有资料。确认前请逐本核对下表。</p>
      </div>
      <el-alert v-if="plan.cancelled" title="预检已取消，本次不能提交。已列出检查结果，请关闭后重新勾选检查。" type="warning" :closable="false" show-icon class="review-plan-alert" />
      <el-alert v-else-if="!plan.ready" title="没有可通过的作品。请按检查结果补全资料或处理来源问题，再重新检查。" type="warning" :closable="false" show-icon class="review-plan-alert" />
      <el-table :data="plan.items" row-key="id" max-height="420" class="review-plan-table" data-testid="batch-review-preview-table">
        <el-table-column label="作品 / 来源" min-width="180"><template #default="{ row }"><strong class="review-book-title">{{ row.title || '未命名作品' }}</strong><small class="review-book-source">{{ row.kind === 'komga' ? 'Komga 远端作品' : row.kind === 'cbz' ? '本地 CBZ' : row.kind === 'folder' ? '本地图片目录' : '来源待检查' }}</small></template></el-table-column>
        <el-table-column label="作者" min-width="125"><template #default="{ row }">{{ row.authors.join('、') || '待补全' }}</template></el-table-column>
        <el-table-column label="内容 / 分级" min-width="105"><template #default="{ row }"><span>{{ row.genre || '待补全' }}</span><small class="review-book-source">{{ ageLabel(row.ageRating) }}</small></template></el-table-column>
        <el-table-column label="待同步资料" min-width="180"><template #default="{ row }">{{ fieldsText(row) }}</template></el-table-column>
        <el-table-column label="检查结果" width="88"><template #default="{ row }"><el-tag :type="row.status === 'ready' ? 'success' : 'warning'" effect="plain" size="small">{{ row.status === 'ready' ? '可通过' : '需处理' }}</el-tag></template></el-table-column>
        <el-table-column label="说明" min-width="230"><template #default="{ row }"><span :class="{ 'review-blocked-reason': row.status === 'blocked' }">{{ row.message }}</span></template></el-table-column>
      </el-table>
      <div v-if="plan.warnings.length" class="review-plan-warnings"><p v-for="(warning, index) in plan.warnings" :key="index">{{ warning }}</p></div>
      <p class="review-plan-note">本地作品需已生成成品或导入 CBZ，并能在 Komga 中唯一匹配；此操作不会生成或上传漫画文件。系列共享资料仍需选全系列。提交时会再次检查，预检通过的作品也可能因资料或服务器变化而跳过。</p>
    </template>
    <template #footer>
      <div class="review-plan-footer">
        <el-checkbox data-testid="batch-review-confirm" v-model="confirmed" :disabled="!canConfirm">已核对可通过的 {{ plan?.ready || 0 }} 本资料，同意审核并同步</el-checkbox>
        <div><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button data-testid="batch-review-submit" type="primary" :icon="Check" :disabled="!confirmed || !canConfirm" @click="confirm">审核并同步 {{ plan?.ready || 0 }} 本</el-button></div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.batch-review-dialog) { display: flex; flex-direction: column; max-height: 90vh; }
:global(.batch-review-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.review-plan-intro { margin-bottom: 14px; color: #73856b; font-size: 12px; line-height: 1.8; }
.review-plan-intro p { margin: 0 0 5px; }
.review-plan-intro strong { color: #39775d; }
.review-plan-alert { margin-bottom: 13px; }
.review-plan-table { width: 100%; font-size: 12px; }
.review-plan-table :deep(.cell) { line-height: 1.7; overflow-wrap: anywhere; }
.review-book-title { display: block; color: #526d48; font-size: 12px; font-weight: 600; }
.review-book-source { display: block; color: #8b9a81; font-size: 11px; margin-top: 3px; }
.review-blocked-reason, .review-plan-warnings { color: #a47736; }
.review-plan-warnings p { margin: 7px 0 0; font-size: 11px; line-height: 1.7; }
.review-plan-note { margin: 13px 0 0; color: #8b9a81; font-size: 11px; line-height: 1.8; }
.review-plan-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 15px; text-align: left; }
.review-plan-footer .el-checkbox { height: auto; white-space: normal; }
.review-plan-footer :deep(.el-checkbox__label) { font-size: 12px; line-height: 1.7; white-space: normal; }
.review-plan-footer > div { margin-left: auto; flex-shrink: 0; }
</style>
