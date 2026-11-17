<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, Collection, Connection, Files, Reading } from '@element-plus/icons-vue'
import type { Comic } from '../types'
import { collectionMode, displayTitle, statusLabels, versionLabels } from '../types'
import type { LibraryGroupRow, LibraryRow } from '../library-groups'
import { buildLibraryEntries, flattenLibraryRows, toggleComicSelection, updateVisibleSelection, visibleSelectableIds } from '../library-groups'

const props = withDefaults(defineProps<{
  comics: Comic[]
  allComics: Comic[]
  selectedIds: string[]
  currentId: string
  disabled: boolean
  canSelect: (comic: Comic) => boolean
  autoSyncLabel?: (comic: Comic) => string
  revealMatches?: boolean
  revealKey?: string
}>(), { autoSyncLabel: () => '', revealMatches: false, revealKey: '' })
const emit = defineEmits<{
  (e: 'selection-change', ids: string[]): void
  (e: 'row-click', comic: Comic): void
}>()

// Only explicit user choices survive filtering. Search expansion is temporary.
const expanded = ref(new Set<string>())
const filterCollapsed = ref(new Set<string>())
const entries = computed(() => buildLibraryEntries(props.comics, props.allComics))
const allEntries = computed(() => buildLibraryEntries(props.allComics, props.allComics))
const expandedKeys = computed(() => {
  const result = new Set(expanded.value)
  if (props.revealMatches) for (const entry of entries.value) {
    if (entry.kind === 'group' && !filterCollapsed.value.has(entry.key)) result.add(entry.key)
  }
  return result
})
const rows = computed(() => flattenLibraryRows(entries.value, expandedKeys.value))
const checkedIds = computed(() => new Set(props.selectedIds))
const visibleIds = computed(() => visibleSelectableIds(rows.value, comic => props.canSelect(comic)))
const visibleChecked = computed(() => visibleIds.value.filter(id => checkedIds.value.has(id)).length)
const allChecked = computed(() => visibleIds.value.length > 0 && visibleChecked.value === visibleIds.value.length)
const someChecked = computed(() => visibleChecked.value > 0 && !allChecked.value)

watch([() => props.revealMatches, () => props.revealKey], () => { filterCollapsed.value = new Set() })
watch(allEntries, value => {
  const keys = new Set(value.filter((entry): entry is LibraryGroupRow => entry.kind === 'group').map(entry => entry.key))
  expanded.value = new Set([...expanded.value].filter(key => keys.has(key)))
  filterCollapsed.value = new Set([...filterCollapsed.value].filter(key => keys.has(key)))
})

function toggleGroup(row: LibraryGroupRow) {
  if (props.disabled) return
  const next = new Set(expanded.value)
  const collapsed = new Set(filterCollapsed.value)
  if (expandedKeys.value.has(row.key)) { next.delete(row.key); collapsed.add(row.key) }
  else { next.add(row.key); collapsed.delete(row.key) }
  expanded.value = next
  filterCollapsed.value = collapsed
}
function clickRow(row: LibraryRow) {
  if (props.disabled) return
  if (row.kind === 'group') toggleGroup(row)
  else emit('row-click', row.comic)
}
function setChecked(comic: Comic, checked: boolean) {
  if (props.disabled || !props.canSelect(comic)) return
  emit('selection-change', toggleComicSelection(props.selectedIds, comic.id, checked))
}
function setAllChecked(checked: boolean) {
  if (props.disabled || !visibleIds.value.length) return
  emit('selection-change', updateVisibleSelection(props.selectedIds, visibleIds.value, checked))
}
function clearSelection() { emit('selection-change', []) }
function selectedCount(row: LibraryGroupRow) { return row.members.filter(comic => checkedIds.value.has(comic.id)).length }
function currentMember(row: LibraryGroupRow) { return row.members.find(comic => comic.id === props.currentId) }
function currentIsFiltered(row: LibraryGroupRow) { return !row.visibleMembers.some(comic => comic.id === props.currentId) }
function editionLabel(comic: Comic) { return comic.metadata.text_version || '版本未标注' }
function groupAuthors(row: LibraryGroupRow) { return [...new Set(row.members.flatMap(comic => comic.metadata.authors))].join('、') }
function groupVersions(row: LibraryGroupRow) { return [...new Set(row.members.map(editionLabel))].join(' · ') }
function groupSource(row: LibraryGroupRow) {
  const comic = row.members[0]
  if (comic.kind !== 'komga') return '本地作品'
  const remote = comic.remote
  let host = ''
  try { host = remote?.serverUrl ? new URL(remote.serverUrl).host : '' } catch { /* Keep the saved origin text in the tooltip. */ }
  return ['Komga', remote?.libraryName || remote?.libraryId || '远端书库', host].filter(Boolean).join(' · ')
}
function groupSourceTitle(row: LibraryGroupRow) {
  const comic = row.members[0]
  return comic.kind !== 'komga' ? '本地图片目录或 CBZ；与远端工作副本分组显示'
    : [comic.remote?.serverUrl, comic.remote?.libraryId].filter(Boolean).join(' · ')
}
function groupStatus(row: LibraryGroupRow) {
  return (['draft', 'approved', 'packaged'] as const).map(status => {
    const count = row.visibleMembers.filter(comic => comic.status === status).length
    return count ? `${count} ${statusLabels[status]}` : ''
  }).filter(Boolean)
}
function childLabels(row: LibraryRow) {
  if (row.kind !== 'comic') return []
  return row.groupKey ? [editionLabel(row.comic)] : versionLabels(row.comic.metadata)
}
function rowClass({ row }: { row: LibraryRow }) {
  if (row.kind === 'group') return `work-group-table-row${currentMember(row) ? ' work-group-current' : ''}`
  return `${row.groupKey ? 'grouped-version-row' : 'standalone-comic-row'}${row.comic.id === props.currentId ? ' current-row' : ''}`
}

defineExpose({ clearSelection })
</script>

<template>
  <el-table :data="rows" row-key="key" height="100%" :row-class-name="rowClass" :class="{ 'table-disabled': disabled }" class="grouped-library-table" @row-click="clickRow" empty-text="没有匹配的漫画">
    <el-table-column width="43" align="center">
      <template #header>
        <el-checkbox data-testid="library-select-all" :model-value="allChecked" :indeterminate="someChecked" :disabled="disabled || !visibleIds.length" aria-label="全选当前可见版本" title="只选择当前展开显示的版本和独立作品；折叠或筛选不会取消已有勾选。" @change="setAllChecked($event === true)" @click.stop />
      </template>
      <template #default="scope">
        <el-checkbox v-if="scope.row.kind === 'comic'" data-testid="library-select-comic" :data-comic-id="scope.row.comic.id" :model-value="checkedIds.has(scope.row.comic.id)" :disabled="disabled || !canSelect(scope.row.comic)" :aria-label="`选择 ${displayTitle(scope.row.comic.metadata)}（${editionLabel(scope.row.comic)}）`" @change="setChecked(scope.row.comic, $event === true)" @click.stop />
      </template>
    </el-table-column>
    <el-table-column label="作品" min-width="240">
      <template #default="scope">
        <div v-if="scope.row.kind === 'group'" class="work-group-cell" data-testid="work-group-row" :data-group-key="scope.row.key">
          <el-button data-testid="work-group-toggle" :data-group-key="scope.row.key" class="work-group-toggle" link :disabled="disabled" :aria-expanded="expandedKeys.has(scope.row.key)" :aria-label="`${expandedKeys.has(scope.row.key) ? '折叠' : '展开'}作品组 ${scope.row.name}`" @click.stop="toggleGroup(scope.row)"><el-icon :class="{ expanded: expandedKeys.has(scope.row.key) }"><ArrowRight /></el-icon></el-button>
          <div class="work-group-icon"><el-icon :size="21"><Collection /></el-icon></div>
          <div class="work-group-copy">
            <strong :title="scope.row.title">{{ scope.row.title }}</strong>
            <span class="work-group-name" :title="scope.row.name">作品组 · {{ scope.row.name }}</span>
            <div class="work-group-summary"><span class="work-group-source" data-testid="work-group-source" :title="groupSourceTitle(scope.row)">{{ groupSource(scope.row) }}</span><span data-testid="work-group-version-count" :data-visible-count="scope.row.visibleMembers.length" :data-total-count="scope.row.members.length">{{ scope.row.visibleMembers.length === scope.row.members.length ? `${scope.row.members.length} 个版本` : `显示 ${scope.row.visibleMembers.length} / ${scope.row.members.length} 个版本` }}</span><span class="work-group-version-list">{{ groupVersions(scope.row) }}</span><span v-if="selectedCount(scope.row)" class="work-group-selected" data-testid="work-group-selected-count" title="折叠或筛选不会取消已选版本">已选 {{ selectedCount(scope.row) }}</span></div>
            <span v-if="currentMember(scope.row) && (!expandedKeys.has(scope.row.key) || currentIsFiltered(scope.row))" class="work-group-viewing" data-testid="work-group-current">正在查看：{{ editionLabel(currentMember(scope.row)!) }}{{ currentIsFiltered(scope.row) ? '（不在当前筛选）' : '' }}</span>
          </div>
        </div>
        <div v-else class="comic-cell" data-testid="comic-row" :data-comic-id="scope.row.comic.id" :data-group-key="scope.row.groupKey">
          <span v-if="scope.row.groupKey" class="version-branch" aria-hidden="true"></span>
          <div class="comic-file-icon" :class="scope.row.comic.kind"><el-icon :size="21"><component :is="scope.row.comic.kind === 'komga' ? Connection : scope.row.comic.kind === 'cbz' ? Files : Reading" /></el-icon></div>
          <div class="comic-titles"><strong>{{ displayTitle(scope.row.comic.metadata) }}</strong><div v-if="childLabels(scope.row).length" class="version-badges" data-testid="book-version-labels"><span v-for="label in childLabels(scope.row)" :key="label" :title="label">{{ label }}</span></div><span v-if="collectionMode(scope.row.comic.metadata) === 'series' && scope.row.comic.metadata.series && scope.row.comic.metadata.series !== scope.row.comic.metadata.title">{{ scope.row.comic.metadata.series }}</span><span v-else-if="scope.row.comic.kind !== 'komga'" class="comic-source" :title="scope.row.comic.source">{{ scope.row.comic.kind === 'cbz' ? 'CBZ 文件' : '本地图片目录' }}</span><span v-if="scope.row.comic.kind !== 'komga' && autoSyncLabel(scope.row.comic)" class="comic-auto-sync-label" data-testid="comic-auto-sync-status">Komga · {{ autoSyncLabel(scope.row.comic) }}</span><span v-if="scope.row.comic.kind === 'komga'" class="comic-source remote-source-label" :title="scope.row.comic.remote?.serverUrl">Komga · {{ scope.row.comic.remote?.libraryName || '远端书库' }}{{ !canSelect(scope.row.comic) ? ' · 只读缓存' : scope.row.comic.remote?.conflict ? ' · 存在冲突' : (scope.row.comic.remote?.hasLocalChanges || scope.row.comic.remote?.pendingSync) ? ' · 待同步' : '' }}</span></div>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="作者" min-width="105" show-overflow-tooltip><template #default="scope"><span v-if="scope.row.kind === 'group'" class="group-authors">{{ groupAuthors(scope.row) || '待核对' }}</span><span v-else :class="{ 'muted-text': !scope.row.comic.metadata.authors.length }">{{ scope.row.comic.metadata.authors.join('、') || '未填写' }}</span></template></el-table-column>
    <el-table-column label="页数" width="68" align="center"><template #default="scope"><span v-if="scope.row.kind === 'group'" class="group-page-summary">{{ scope.row.members.length }} 版</span><span v-else class="page-count">{{ scope.row.comic.page_count }}</span></template></el-table-column>
    <el-table-column label="状态" width="102"><template #default="scope"><div v-if="scope.row.kind === 'group'" class="group-status-summary"><span v-for="label in groupStatus(scope.row)" :key="label">{{ label }}</span></div><span v-else class="status-pill" :class="scope.row.comic.status">{{ statusLabels[scope.row.comic.status as keyof typeof statusLabels] }}</span></template></el-table-column>
  </el-table>
</template>

<style scoped>
.work-group-cell { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 2px 0; }
.work-group-toggle { flex: 0 0 19px; width: 19px; padding: 0 !important; color: #7e967c; }
.work-group-toggle .el-icon { transition: transform .16s ease; }
.work-group-toggle .el-icon.expanded { transform: rotate(90deg); }
.work-group-icon { width: 38px; height: 44px; display: grid; place-items: center; border-radius: 8px; color: #6b916d; background: #e8f0e1; flex-shrink: 0; border: 1px solid #dae5d3; }
.work-group-copy { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.work-group-copy > strong { font-size: 13px; color: #3d5b44; font-weight: 600; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.work-group-name { font-size: 10px; color: #83977c; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.work-group-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 5px 9px; font-size: 10px; color: #82937a; line-height: 1.5; }
.work-group-version-list { color: #72896b; }
.work-group-selected { padding: 0 5px; border-radius: 4px; background: #deebd5; color: #507642; font-weight: 550; }
.work-group-viewing { align-self: flex-start; color: #527f59; background: #edf4e8; padding: 1px 5px; border-radius: 4px; font-size: 10px; }
.group-authors { color: #7c9077; font-size: 11px; }
.group-page-summary { color: #809276; font-size: 11px; white-space: nowrap; }
.group-status-summary { display: flex; flex-direction: column; gap: 4px; font-size: 10px; color: #8b947c; white-space: nowrap; }
:deep(.work-group-table-row > .el-table__cell) { background: #f3f7ef; }
:deep(.work-group-current > .el-table__cell) { background: #edf4e7; }
:deep(.work-group-table-row:hover > .el-table__cell) { background: #ebf2e5 !important; }
:deep(.grouped-version-row .comic-cell) { padding-left: 26px; position: relative; }
.version-branch { width: 11px; height: 11px; border-left: 1px solid #d5e1cd; border-bottom: 1px solid #d5e1cd; position: absolute; left: 4px; top: calc(50% - 8px); border-bottom-left-radius: 3px; }
.version-badges { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.version-badges > span { max-width: 100%; padding: 1px 5px; border: 1px solid #dfe8d7; border-radius: 4px; background: #f2f7ed; color: #6d8662; font-size: 10px; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.comic-auto-sync-label { color: #8a9d80; font-size: 10px; }
.remote-source-label { max-width: 100%; }
@media (max-width: 1250px) {
  .work-group-cell { gap: 7px; }
  .work-group-icon { width: 31px; height: 38px; }
  .work-group-name { font-size: 9px; }
}
</style>
