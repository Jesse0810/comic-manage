<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { LoadFunction, TableInstance, TreeOptionProps } from 'element-plus'
import { FolderAdd, Folder, ArrowLeft, Check, Close } from '@element-plus/icons-vue'
import type { ImportCandidate, ImportPreview, ImportResult } from '../types'
import { errorMessage, displayTitle, colorLabels, collectionMode, versionLabels } from '../types'
import { invoke } from '../api'
import BulkMetadataFields from './BulkMetadataFields.vue'

interface FolderEntry { path: string; name: string; hasChildren: boolean }
interface FolderNode extends FolderEntry { source: string; nodeKey: string }
const props = defineProps<{ modelValue: boolean; authors: string[]; message: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', result: ImportResult): void
}>()
const locations = ref<FolderNode[]>([])
const branchChoices = ref(new Map<string, { path: string; selected: boolean }>())
const table = ref<TableInstance>()
const preview = ref<ImportPreview | null>(null)
const selected = ref<string[]>([])
const errors = ref<string[]>([])
const busy = ref(false)
const cancelling = ref(false)
const task = ref('')
const prefill = ref<InstanceType<typeof BulkMetadataFields>>()
const overwriteExisting = ref(false)
const treeProps: TreeOptionProps = { label: 'name', isLeaf: (node) => !node.hasChildren }
const selectedCandidates = computed(() => {
  const ids = new Set(selected.value)
  return preview.value?.items.filter(item => ids.has(item.id)) || []
})
const existingSelected = computed(() => selectedCandidates.value.filter(item => item.import_status !== 'new').length)
const prefillTargets = computed(() => selectedCandidates.value.filter(item => overwriteExisting.value || item.import_status === 'new'))
const prefillModes = computed(() => prefillTargets.value.map(item => collectionMode(item.metadata)))
const prefillRatings = computed(() => prefillTargets.value.map(item => item.metadata.age_rating))
const hasDefaults = computed(() => Object.keys(prefill.value?.changes || {}).length > 0)
const folderSelection = computed(() => {
  const paths: string[] = []
  const excludedPaths: string[] = []
  for (const { path, selected } of branchChoices.value.values()) {
    (selected ? paths : excludedPaths).push(path)
  }
  return { paths, excludedPaths }
})
const sourceCount = computed(() => folderSelection.value.paths.length)
const partialBranches = computed(() => {
  const result = new Set<string>()
  for (const key of branchChoices.value.keys()) {
    for (let parent = parentKey(key); parent && !result.has(parent); parent = parentKey(parent)) result.add(parent)
  }
  return result
})
const statusNames = { new: '新作品', existing: '已入库', removed: '已移出' }

function clearPreview() { preview.value = null; selected.value = []; errors.value = [] }
function setBusy(value: boolean, title = '') {
  busy.value = value
  task.value = title
  cancelling.value = false
  emit('state', { busy: value, title })
}
function close() { if (!busy.value) emit('update:modelValue', false) }
function beforeClose(done: () => void) { if (!busy.value) done(); else ElMessage.info('请先取消任务，等待当前操作结束') }
function baseName(path: string) { return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path }
function isWindowsPath(path: string) { return /^[a-z]:[\\/]/i.test(path) || /^(?:\\\\|\/\/)[^\\/]/.test(path) }
function pathKey(path: string) {
  const windows = isWindowsPath(path)
  const value = (windows ? path.replaceAll('\\', '/') : path).replace(/\/+$/, '') || '/'
  return windows ? value.toLowerCase() : value
}
function folderNode(entry: FolderEntry, source = pathKey(entry.path)): FolderNode {
  return { ...entry, source, nodeKey: JSON.stringify([source, pathKey(entry.path)]) }
}
function parentKey(key: string) {
  if (key === '/') return ''
  const slash = key.lastIndexOf('/')
  return slash === 0 ? '/' : slash > 0 ? key.slice(0, slash) : ''
}
function includesPath(root: string, key: string) {
  return root === key || key.startsWith(root === '/' ? root : root + '/')
}
function inheritedSelection(key: string) {
  for (let cursor = key; cursor; cursor = parentKey(cursor)) {
    const choice = branchChoices.value.get(cursor)
    if (choice) return choice.selected
  }
  return false
}
function selectBranch(path: string, selected: boolean) {
  if (busy.value) return
  const key = pathKey(path)
  for (const current of branchChoices.value.keys()) {
    if (includesPath(key, current)) branchChoices.value.delete(current)
  }
  if (selected !== inheritedSelection(parentKey(key))) branchChoices.value.set(key, { path, selected })
  clearPreview()
}
function isBranchChecked(path: string) {
  const key = pathKey(path)
  return !partialBranches.value.has(key) && inheritedSelection(key)
}
async function addLocations() {
  if (busy.value) return
  try {
    const chosen = await window.comic.chooseDirectories()
    const known = new Set(locations.value.map(node => pathKey(node.path)))
    for (const path of chosen) {
      const key = pathKey(path)
      if (known.has(key)) continue
      known.add(key)
      locations.value.push(folderNode({ path, name: baseName(path), hasChildren: true }))
      selectBranch(path, true)
    }
  } catch (error) { errors.value = [errorMessage(error)] }
}
const loadFolders: LoadFunction = async (node, resolve, stopLoading) => {
  if (node.level === 0) { resolve(locations.value); return }
  if (busy.value) { stopLoading(); return }
  const folder = node.data as FolderNode
  setBusy(true, '读取文件夹列表')
  try {
    const result = await invoke<{ children: FolderEntry[]; errors: string[] }>('browse_folders', { path: folder.path })
    // Keep the picked drive or share spelling across the lazy tree so rules inherit through mapped drives.
    const windows = isWindowsPath(folder.path)
    const separator = windows && folder.path.includes('\\') ? '\\' : '/'
    const parent = folder.path.replace(windows ? /[\\/]+$/ : /\/+$/, '')
    resolve(result.children.map(child => folderNode({ ...child, path: parent + separator + child.name }, folder.source)))
    if (result.errors.length) errors.value = result.errors
  } catch (error) { stopLoading(); errors.value = [errorMessage(error)] }
  finally { setBusy(false) }
}
function removeLocation(path: string) {
  if (busy.value) return
  const key = pathKey(path)
  locations.value = locations.value.filter(item => pathKey(item.path) !== key)
  const remaining = locations.value.map(item => {
    const node = pathKey(item.path)
    return { path: item.path, node, selected: inheritedSelection(node) }
  }).sort((first, second) => first.node.length - second.node.length)
  for (const current of branchChoices.value.keys()) {
    if (!remaining.some(root => includesPath(root.node, current))) branchChoices.value.delete(current)
  }
  for (const root of remaining) {
    if (root.selected !== inheritedSelection(root.node)) {
      branchChoices.value.set(root.node, { path: root.path, selected: root.selected })
    }
  }
  clearPreview()
}
async function prepare() {
  if (!sourceCount.value || busy.value) return
  setBusy(true, '读取并解析漫画资料')
  errors.value = []
  try {
    const result = await invoke<ImportPreview>('prepare_import', { ...folderSelection.value, recursive: true })
    preview.value = result
    errors.value = result.errors
    selected.value = []
    await nextTick()
    for (const item of result.items) if (item.import_status === 'new') table.value?.toggleRowSelection(item, true)
    if (result.cancelled) ElMessage.info('读取已取消，可导入已读取并勾选的作品')
  } catch (error) { errors.value = [errorMessage(error)] }
  finally { setBusy(false) }
}
async function commit() {
  if (!preview.value || !selected.value.length || busy.value) return
  const defaults = prefillTargets.value.length ? prefill.value?.validatedChanges() : {}
  if (!defaults) return
  setBusy(true, '导入选中的漫画')
  try {
    const result = await invoke<ImportResult>('commit_import', {
      token: preview.value.token, ids: [...selected.value], defaults, overwriteExisting: overwriteExisting.value && hasDefaults.value,
    })
    emit('completed', result)
    emit('update:modelValue', false)
  } catch (error) { errors.value = [errorMessage(error)] }
  finally { setBusy(false) }
}
async function cancel() {
  if (!busy.value || cancelling.value) return
  cancelling.value = true
  try { await invoke('cancel') }
  catch (error) { errors.value = [errorMessage(error)] }
}
watch(() => props.modelValue, (visible) => {
  if (!visible) return
  locations.value = []; branchChoices.value.clear()
  clearPreview()
  overwriteExisting.value = false
  prefill.value?.reset()
})
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="添加漫画" width="min(1080px, 94vw)" top="24px" class="import-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" :before-close="beforeClose" destroy-on-close>
    <div class="import-steps"><span :class="{ active: !preview }">1 选择来源</span><i></i><span :class="{ active: !!preview }">2 核对并导入</span></div>
    <div class="import-dialog-scroll">
      <template v-if="!preview">
        <div class="import-source-chooser" :class="{ 'is-empty': !locations.length }" data-testid="import-source-chooser">
          <div v-if="!locations.length" class="import-folder-icon"><el-icon :size="26"><FolderAdd /></el-icon></div>
          <div class="import-source-copy"><strong>{{ locations.length ? '选择要读取的文件夹' : '从漫画文件夹开始' }}</strong><p>{{ locations.length ? '可以继续追加来源，再勾选想导入的目录。' : '支持本地磁盘与 NAS，一次可选择一个或多个文件夹。' }}</p></div>
          <el-button data-testid="import-pick-folders" class="import-pick-folders" type="primary" :icon="FolderAdd" :disabled="busy" @click="addLocations">选择文件夹</el-button>
        </div>
        <p v-if="locations.length" class="import-help">勾选父文件夹会选中下面的所有目录，展开后可取消勾选个别分支。“有文字／无文字”子文件夹会识别为同一作品的不同版本。</p>
        <div v-if="locations.length" class="folder-picker" :class="{ 'picker-disabled': busy }">
          <el-tree data-testid="import-tree" :data="locations" node-key="nodeKey" :props="treeProps" lazy :load="loadFolders" :expand-on-click-node="false">
            <template #default="{ node, data }">
              <div class="folder-tree-node" :title="data.path">
                <el-checkbox :model-value="isBranchChecked(data.path)" :indeterminate="partialBranches.has(pathKey(data.path))"
                  :disabled="busy" :aria-label="'选择文件夹 ' + data.name" @click.stop @change="selectBranch(data.path, $event === true)" />
                <el-icon><Folder /></el-icon>
                <span>{{ node.level === 1 ? data.path : data.name }}</span>
                <el-button v-if="node.level === 1" link :icon="Close" :disabled="busy" aria-label="移除此来源位置" @click.stop="removeLocation(data.path)" />
              </div>
            </template>
          </el-tree>
        </div>
        <div class="import-source-summary"><span class="import-selected-count">已选 {{ sourceCount }} 个目录范围{{ folderSelection.excludedPaths.length ? `，跳过 ${folderSelection.excludedPaths.length} 个分支` : '' }}</span></div>
      </template>
      <template v-else>
        <div class="import-preview-heading"><div><strong>发现 {{ preview.items.length }} 本漫画</strong><p>检查解析结果，取消勾选暂不需要的作品。新作品默认勾选，已入库或移出的作品需手动勾选。</p></div><el-button link :icon="ArrowLeft" :disabled="busy" @click="clearPreview">重新选择来源</el-button></div>
        <el-table ref="table" data-testid="import-table" :data="preview.items" row-key="id" max-height="260" class="import-preview-table" @selection-change="(rows: ImportCandidate[]) => selected = rows.map(row => row.id)"><el-table-column type="selection" width="42" :selectable="() => !busy" /><el-table-column label="作品名称 / 来源" min-width="230"><template #default="{ row }"><div class="import-title">{{ displayTitle(row.metadata) }}</div><div v-if="versionLabels(row.metadata).length" class="import-version-labels" data-testid="import-version-labels"><span v-for="label in versionLabels(row.metadata)" :key="label" :title="label">{{ label }}</span></div><div class="import-source-path" data-testid="import-collection-mode">{{ collectionMode(row.metadata) === 'series' ? `系列：${row.metadata.series} · 编号 ${row.metadata.number || '待确认'}` : '独立作品 · One-Shot' }}</div><div class="import-source-path" :title="row.source">{{ row.source }}</div><div v-if="row.warning" class="import-warning" :title="row.warning">{{ row.warning }}</div></template></el-table-column><el-table-column label="作者" min-width="120"><template #default="{ row }">{{ row.metadata.authors.join('、') || '待填写' }}</template></el-table-column><el-table-column label="内容 / 发售" width="112"><template #default="{ row }"><div>{{ row.metadata.genre || '待填写' }}</div><small>{{ row.metadata.year ? `${row.metadata.year}${row.metadata.month ? '.' + row.metadata.month.padStart(2, '0') + (row.metadata.day ? '.' + row.metadata.day.padStart(2, '0') : '') : ''}` : '—' }}</small></template></el-table-column><el-table-column label="译者 / 色彩" min-width="130"><template #default="{ row }"><div>{{ row.metadata.translator || '—' }}</div><small>{{ [colorLabels[row.metadata.color || ''] || '色彩待确认', row.metadata.age_rating === 'R18+' && row.metadata.uncensored ? '无修正' : ''].filter(Boolean).join(' · ') }}</small></template></el-table-column><el-table-column label="入库状态" width="84"><template #default="{ row }"><span class="import-status" :class="row.import_status">{{ statusNames[row.import_status as keyof typeof statusNames] }}</span></template></el-table-column></el-table>
        <div v-if="!preview.items.length" class="import-no-items">没有发现可读取的漫画，请返回检查所选文件夹。</div>
      </template>
      <section v-show="preview?.items.length" class="import-batch-section">
        <div class="import-batch-title">
          <strong>批量填写资料</strong>
          <span>作品名称和译名沿用各自的资料。勾选字段将在导入时应用于 {{ prefillTargets.length }} 本作品。</span>
        </div>
        <BulkMetadataFields ref="prefill" mode="import" :authors="authors" :modes="prefillModes" :ratings="prefillRatings" :busy="busy" />
        <el-checkbox data-testid="import-overwrite-existing" v-model="overwriteExisting" :disabled="busy || (!hasDefaults && !overwriteExisting)" class="overwrite-existing">对勾选的已入库 / 已移出作品也应用预填字段</el-checkbox>
        <p v-if="existingSelected" class="import-existing-note">已勾选 {{ existingSelected }} 本已有记录。{{ overwriteExisting && hasDefaults ? '勾选字段将按上方设置更新，资料变化后需重新审核。' : '保留人工资料；移出的作品会恢复到列表。' }}</p>
      </section>
      <p v-if="preview" class="import-draft-note">新作品以待审核状态入库，请逐本核对资料。</p>
      <div v-if="errors.length" class="import-errors"><strong>{{ errors.length }} 项提示</strong><p v-for="(error, index) in errors" :key="index">{{ error }}</p></div>
    </div>
    <template #footer><div class="import-footer"><span v-if="busy" class="import-progress">{{ message || task }}…</span><span v-else>{{ preview ? `已选择 ${selected.length} 本作品` : '先预览，确认后入库' }}</span><div><el-button v-if="busy" :disabled="cancelling" @click="cancel">{{ cancelling ? '正在取消…' : '取消任务' }}</el-button><template v-else><el-button @click="close">取消</el-button><el-button v-if="!preview" type="primary" :disabled="!sourceCount" @click="prepare">读取并预览</el-button><el-button v-else type="primary" :icon="Check" :disabled="!selected.length" @click="commit">导入选中 {{ selected.length }} 本</el-button></template></div></div></template>
  </el-dialog>
</template>

<style scoped>
:global(.import-dialog) { display: flex; flex-direction: column; max-height: calc(100vh - 48px); max-height: calc(100dvh - 48px); overflow: hidden; padding: 0; border-radius: 12px; }
:global(.import-dialog .el-dialog__header) { flex: 0 0 auto; margin: 0; padding: 18px 46px 15px 20px; }
:global(.import-dialog .el-dialog__headerbtn) { top: 11px; right: 10px; }
:global(.import-dialog .el-dialog__body) { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; padding: 0 20px; overflow: hidden; }
:global(.import-dialog .el-dialog__footer) { flex: 0 0 auto; padding: 16px 20px 18px; }
.import-steps { gap: 10px; margin-bottom: 16px; }
.import-steps span { padding: 5px 9px; }
.import-dialog-scroll { flex: 0 1 auto; min-height: 0; overflow-x: hidden; overflow-y: auto; padding-right: 3px; }
.import-source-chooser { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-width: 0; padding: 2px 0 0; }
.import-source-chooser.is-empty { flex-direction: column; justify-content: center; gap: 14px; padding: 28px 24px; text-align: center; border: 1px dashed #c6d7c1; border-radius: 10px; background: #f6f9f3; }
.import-folder-icon { display: grid; place-items: center; width: 48px; height: 48px; border: 1px solid #dce8d6; border-radius: 12px; color: #427359; background: #edf4e8; }
.import-source-copy { flex: 1; min-width: 0; }
.import-source-copy strong { display: block; color: #385d48; font-size: 14px; font-weight: 600; line-height: 1.6; }
.import-source-copy p { margin: 5px 0 0; color: #788a75; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.import-pick-folders.el-button { flex: 0 0 auto; min-height: 36px; padding: 0 18px; margin: 0; border-color: #32725a; color: #fff; background: #32725a; font-weight: 500; }
.import-pick-folders.el-button:not(:disabled):hover { color: #fff; background: #285f49; border-color: #285f49; }
.import-pick-folders.el-button:focus-visible { outline: 2px solid #32725a; outline-offset: 3px; }
.import-pick-folders.el-button:disabled { border-color: #a4bdae; background: #a4bdae; color: #fff; }
.is-empty .import-pick-folders { margin-top: 4px; }
.import-help { margin: 13px 0 12px; color: #71866a; font-size: 12px; line-height: 1.75; }
.folder-picker { min-height: 92px; max-height: min(320px, 36vh); overflow: auto; padding: 8px; border: 1px solid #e0e8da; border-radius: 8px; background: #fcfdf9; }
.folder-picker :deep(.el-tree) { min-width: 0; color: #536e4e; background: transparent; }
.folder-picker :deep(.el-tree-node__content) { height: 35px; min-width: 0; border-radius: 4px; }
.folder-picker :deep(.el-tree-node__label) { flex: 1; min-width: 0; }
.folder-tree-node { min-width: 0; }
.folder-tree-node > .el-checkbox { flex: none; margin-right: 0; }
.folder-tree-node > span { flex: 1; min-width: 0; }
.folder-tree-node > .el-button { margin-left: 3px; }
.picker-disabled :deep(.el-tree) { pointer-events: none; opacity: .65; }
.import-source-summary { gap: 5px 12px; padding: 12px 1px 0; line-height: 1.7; }
.import-source-summary > .import-selected-count { color: #496e48; font-size: 12px; }
.import-source-summary > span { color: #84957d; }
.import-preview-heading { gap: 12px; margin-bottom: 12px; }
.import-preview-heading > div { min-width: 0; }
.import-preview-heading p { overflow-wrap: anywhere; }
.import-source-path { max-width: 100%; }
.import-batch-section { margin-top: 14px; padding: 13px 14px 10px; }
.import-batch-section .overwrite-existing { margin-top: 11px; }
.import-batch-section .overwrite-existing :deep(.el-checkbox__label) { white-space: normal; }
.import-footer { flex-wrap: wrap; gap: 10px 16px; }
.import-footer > span { min-width: 0; color: #788d70; font-size: 12px; line-height: 1.6; }
.import-footer > div { display: flex; gap: 8px; margin-left: auto; }
.import-footer > div .el-button + .el-button { margin-left: 0; }
.import-errors { max-height: 130px; }
@media (max-width: 640px) {
  :global(.import-dialog .el-dialog__header) { padding-left: 16px; padding-top: 14px; }
  :global(.import-dialog .el-dialog__body) { padding: 0 16px; }
  :global(.import-dialog .el-dialog__footer) { padding: 13px 16px 15px; }
  .import-source-chooser:not(.is-empty) { flex-wrap: wrap; gap: 10px; }
  .import-source-chooser.is-empty { padding: 24px 18px; }
  .import-source-copy { flex-basis: 200px; }
  .is-empty .import-source-copy { flex-basis: auto; }
  .import-preview-heading { flex-wrap: wrap; }
  .import-footer > span { flex-basis: 100%; }
  .import-footer .import-progress { max-width: 100%; }
}
@media (max-height: 600px) {
  .import-source-chooser.is-empty { flex-direction: row; justify-content: flex-start; gap: 14px; padding: 20px; text-align: left; flex-wrap: wrap; }
  .import-source-chooser.is-empty .import-source-copy { flex: 1 1 180px; }
  .import-source-chooser.is-empty .import-folder-icon { display: none; }
  .is-empty .import-pick-folders { margin-top: 0; }
  .folder-picker { max-height: 180px; }
}
.import-version-labels { display: flex; flex-wrap: wrap; gap: 4px; margin: 3px 0; min-width: 0; }
.import-version-labels > span { max-width: 100%; padding: 1px 5px; border: 1px solid #dfe8d7; border-radius: 4px; background: #f2f7ed; color: #6d8662; font-size: 10px; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
