<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElTree } from 'element-plus'
import type { TableInstance, TreeOptionProps } from 'element-plus'
import { FolderAdd, Folder, ArrowLeft, Check, Close } from '@element-plus/icons-vue'
import type { ImportCandidate, ImportPreview, ImportResult } from '../types'
import { comicGenres, errorMessage, displayTitle, colorLabels, collectionMode, versionLabels } from '../types'
import { invoke } from '../api'

interface FolderNode { path: string; name: string; hasChildren: boolean }
const props = defineProps<{ modelValue: boolean; authors: string[]; message: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', result: ImportResult): void
}>()
const locations = ref<FolderNode[]>([])
const checkedPaths = ref<string[]>([])
const tree = ref<InstanceType<typeof ElTree>>()
const table = ref<TableInstance>()
const preview = ref<ImportPreview | null>(null)
const selected = ref<string[]>([])
const errors = ref<string[]>([])
const busy = ref(false)
const cancelling = ref(false)
const task = ref('')
const editAuthors = ref(false)
const editGenre = ref(false)
const defaultAuthors = ref<string[]>([])
const defaultGenre = ref('')
const overwriteExisting = ref(false)
const treeProps: TreeOptionProps = { label: 'name', isLeaf: (node) => !node.hasChildren }
const existingSelected = computed(() => preview.value?.items.filter(item => selected.value.includes(item.id) && item.import_status !== 'new').length || 0)
const sourceCount = computed(() => checkedPaths.value.length)
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
function pathKey(path: string) {
  const windowsPath = /^[a-z]:[\\/]/i.test(path) || /^(?:\\\\|\/\/)[^\\/]/.test(path)
  const normalized = (windowsPath ? path.replaceAll('\\', '/') : path).replace(/\/+$/, '') || '/'
  return windowsPath ? normalized.toLocaleLowerCase() : normalized
}
async function addLocations() {
  if (busy.value) return
  try {
    const chosen = await window.comic.chooseDirectories()
    if (!chosen.length) return
    const known = new Set(locations.value.map(node => pathKey(node.path)))
    const added = chosen.filter(path => {
      const key = pathKey(path)
      if (known.has(key)) return false
      known.add(key)
      return true
    })
    if (!added.length) return
    locations.value.push(...added.map(path => ({ path, name: baseName(path), hasChildren: true })))
    clearPreview()
    checkedPaths.value = [...new Set([...checkedPaths.value, ...added])]
    await nextTick()
    tree.value?.setCheckedKeys(checkedPaths.value)
  } catch (error) { errors.value = [errorMessage(error)] }
}
async function loadFolders(node: any, resolve: (nodes: FolderNode[]) => void) {
  if (node.level === 0) { resolve(locations.value); return }
  setBusy(true, '读取文件夹列表')
  try {
    const result = await invoke<{ children: FolderNode[]; errors: string[] }>('browse_folders', { path: node.data.path })
    resolve(result.children)
    if (result.errors.length) errors.value = result.errors
  } catch (error) { resolve([]); errors.value = [errorMessage(error)] }
  finally { setBusy(false) }
}
function checkedChanged(_data: FolderNode, state: { checkedKeys: (string | number)[] }) {
  checkedPaths.value = state.checkedKeys.map(String)
  clearPreview()
}
function removeLocation(path: string) {
  if (busy.value) return
  const key = pathKey(path)
  const prefix = key.endsWith('/') ? key : key + '/'
  locations.value = locations.value.filter(item => pathKey(item.path) !== key)
  checkedPaths.value = checkedPaths.value.filter(item => pathKey(item) !== key && !pathKey(item).startsWith(prefix))
  nextTick(() => tree.value?.setCheckedKeys(checkedPaths.value))
  clearPreview()
}
async function prepare() {
  if (!checkedPaths.value.length || busy.value) return
  setBusy(true, '读取并解析漫画资料')
  errors.value = []
  try {
    const result = await invoke<ImportPreview>('prepare_import', { paths: [...checkedPaths.value], recursive: true })
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
  const defaults: { authors?: string[]; genre?: string } = {}
  if (editAuthors.value) {
    defaults.authors = [...new Set(defaultAuthors.value.map(author => author.trim()).filter(Boolean))]
    if (!defaults.authors.length) { ElMessage.warning('请输入批量作者，按 Enter 添加'); return }
  }
  if (editGenre.value) {
    defaults.genre = defaultGenre.value.trim()
    if (!comicGenres.includes(defaults.genre)) { ElMessage.warning('请选择规范中的六类内容类型之一'); return }
  }
  setBusy(true, '导入选中的漫画')
  try {
    const result = await invoke<ImportResult>('commit_import', {
      token: preview.value.token, ids: [...selected.value], defaults, overwriteExisting: overwriteExisting.value,
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
  locations.value = []; checkedPaths.value = []
  clearPreview()
  editAuthors.value = false; editGenre.value = false; defaultAuthors.value = []; defaultGenre.value = ''; overwriteExisting.value = false
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
        <p v-if="locations.length" class="import-help">勾选父文件夹会读取其中所有作品；只导入部分时，请展开并仅勾选目标子文件夹。“有文字／无文字”子文件夹会识别为同一作品的不同版本。</p>
        <div v-if="locations.length" class="folder-picker" :class="{ 'picker-disabled': busy }">
          <el-tree ref="tree" data-testid="import-tree" :data="locations" node-key="path" :props="treeProps" lazy :load="loadFolders" show-checkbox check-strictly :expand-on-click-node="false" :check-on-click-node="false" :default-checked-keys="checkedPaths" @check="checkedChanged"><template #default="{ node, data }"><div class="folder-tree-node" :title="data.path"><el-icon><Folder /></el-icon><span>{{ node.level === 1 ? data.path : data.name }}</span><el-button v-if="node.level === 1" link :icon="Close" :disabled="busy" aria-label="移除此来源位置" @click.stop="removeLocation(data.path)" /></div></template></el-tree>
        </div>
        <div class="import-source-summary"><span class="import-selected-count">已勾选 {{ sourceCount }} 个文件夹</span></div>
      </template>
      <template v-else>
        <div class="import-preview-heading"><div><strong>发现 {{ preview.items.length }} 本漫画</strong><p>检查解析结果，取消勾选暂不需要的作品。新作品默认勾选，已入库或移出的作品需手动勾选。</p></div><el-button link :icon="ArrowLeft" :disabled="busy" @click="clearPreview">重新选择来源</el-button></div>
        <el-table ref="table" data-testid="import-table" :data="preview.items" row-key="id" max-height="260" class="import-preview-table" @selection-change="(rows: ImportCandidate[]) => selected = rows.map(row => row.id)"><el-table-column type="selection" width="42" :selectable="() => !busy" /><el-table-column label="作品名称 / 来源" min-width="230"><template #default="{ row }"><div class="import-title">{{ displayTitle(row.metadata) }}</div><div v-if="versionLabels(row.metadata).length" class="import-version-labels" data-testid="import-version-labels"><span v-for="label in versionLabels(row.metadata)" :key="label" :title="label">{{ label }}</span></div><div class="import-source-path" data-testid="import-collection-mode">{{ collectionMode(row.metadata) === 'series' ? `系列：${row.metadata.series} · 编号 ${row.metadata.number || '待确认'}` : '独立作品 · One-Shot' }}</div><div class="import-source-path" :title="row.source">{{ row.source }}</div><div v-if="row.warning" class="import-warning" :title="row.warning">{{ row.warning }}</div></template></el-table-column><el-table-column label="作者" min-width="120"><template #default="{ row }">{{ row.metadata.authors.join('、') || '待填写' }}</template></el-table-column><el-table-column label="内容 / 发售" width="112"><template #default="{ row }"><div>{{ row.metadata.genre || '待填写' }}</div><small>{{ row.metadata.year ? `${row.metadata.year}${row.metadata.month ? '.' + row.metadata.month.padStart(2, '0') + (row.metadata.day ? '.' + row.metadata.day.padStart(2, '0') : '') : ''}` : '—' }}</small></template></el-table-column><el-table-column label="译者 / 色彩" min-width="130"><template #default="{ row }"><div>{{ row.metadata.translator || '—' }}</div><small>{{ [colorLabels[row.metadata.color || ''] || '色彩待确认', row.metadata.age_rating === 'R18+' && row.metadata.uncensored ? '无修正' : ''].filter(Boolean).join(' · ') }}</small></template></el-table-column><el-table-column label="入库状态" width="84"><template #default="{ row }"><span class="import-status" :class="row.import_status">{{ statusNames[row.import_status as keyof typeof statusNames] }}</span></template></el-table-column></el-table>
        <div v-if="!preview.items.length" class="import-no-items">没有发现可读取的漫画，请返回检查所选文件夹。</div>
        <div class="import-batch-section"><div class="import-batch-title"><strong>批量填写资料</strong><span>可选，将应用于本次勾选的新作品</span></div><div class="batch-fields"><div class="batch-field"><el-checkbox data-testid="import-enable-authors" v-model="editAuthors" :disabled="busy">统一作者</el-checkbox><el-select data-testid="import-authors" aria-label="导入时统一作者" v-model="defaultAuthors" multiple filterable allow-create default-first-option :reserve-keyword="false" :disabled="busy || !editAuthors" placeholder="输入作者后按 Enter，可添加多位"><el-option v-for="name in authors" :key="name" :label="name" :value="name" /></el-select></div><div class="batch-field"><el-checkbox data-testid="import-enable-genre" v-model="editGenre" :disabled="busy">统一内容类型</el-checkbox><el-select data-testid="import-genre" aria-label="导入时统一内容类型" v-model="defaultGenre" :disabled="busy || !editGenre" placeholder="请选择内容类型"><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></div></div><el-checkbox data-testid="import-overwrite-existing" v-model="overwriteExisting" :disabled="busy || (!editAuthors && !editGenre)" class="overwrite-existing">对勾选的已入库 / 已移出作品也应用以上作者和内容类型</el-checkbox><p v-if="existingSelected" class="import-existing-note">已勾选 {{ existingSelected }} 本已有记录。{{ overwriteExisting && (editAuthors || editGenre) ? '作者和内容类型将按上方设置更新，作品需重新审核。' : '保留人工资料；移出的作品会恢复到列表。' }}</p></div>
        <p class="import-draft-note">自动资料以待审核状态入库。请逐本核对标题、作者、内容类型、年龄分级与真实标签；旧值不明确时保留草稿。可在详情或批量编辑中补充。</p>
      </template>
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
.batch-fields { gap: 12px; }
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
  .batch-fields { grid-template-columns: minmax(0, 1fr); }
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
