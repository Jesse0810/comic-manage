<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElTree } from 'element-plus'
import type { TableInstance, TreeOptionProps } from 'element-plus'
import { FolderAdd, DocumentAdd, Folder, ArrowLeft, Check, Close } from '@element-plus/icons-vue'
import type { ImportCandidate, ImportPreview, ImportResult } from '../types'
import { comicGenres, errorMessage, displayTitle, colorLabels } from '../types'
import { invoke } from '../api'

type Mode = 'folder' | 'cbz'
interface FolderNode { path: string; name: string; hasChildren: boolean }
const props = defineProps<{ modelValue: boolean; mode: Mode; authors: string[]; message: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', result: ImportResult): void
}>()
const mode = ref<Mode>('folder')
const locations = ref<FolderNode[]>([])
const checkedPaths = ref<string[]>([])
const files = ref<string[]>([])
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
const paths = computed(() => mode.value === 'folder' ? checkedPaths.value : files.value)
const existingSelected = computed(() => preview.value?.items.filter(item => selected.value.includes(item.id) && item.import_status !== 'new').length || 0)
const sourceCount = computed(() => paths.value.length)
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
async function addLocations() {
  if (busy.value) return
  try {
    const chosen = await window.comic.chooseDirectories()
    if (!chosen.length) return
    const known = new Set(locations.value.map(node => node.path.toLocaleLowerCase()))
    const added = chosen.filter(path => !known.has(path.toLocaleLowerCase()))
    locations.value.push(...added.map(path => ({ path, name: baseName(path), hasChildren: true })))
    clearPreview()
    checkedPaths.value = [...new Set([...checkedPaths.value, ...added])]
    await nextTick()
    tree.value?.setCheckedKeys(checkedPaths.value)
  } catch (error) { errors.value = [errorMessage(error)] }
}
async function addFiles() {
  if (busy.value) return
  try {
    const chosen = await window.comic.chooseCbz()
    if (!chosen.length) return
    files.value = [...new Set([...files.value, ...chosen])]
    clearPreview()
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
  locations.value = locations.value.filter(item => item.path !== path)
  const prefix = path.replace(/[\\/]+$/, '') + '\\'
  checkedPaths.value = checkedPaths.value.filter(item => item !== path && !item.startsWith(prefix))
  nextTick(() => tree.value?.setCheckedKeys(checkedPaths.value))
  clearPreview()
}
function removeFile(path: string) { files.value = files.value.filter(item => item !== path); clearPreview() }
async function prepare() {
  if (!paths.value.length || busy.value) return
  setBusy(true, '读取并解析漫画资料')
  errors.value = []
  try {
    const result = await invoke<ImportPreview>('prepare_import', { paths: [...paths.value], recursive: true })
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
    if (!defaults.genre) { ElMessage.warning('请选择批量类型'); return }
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
  mode.value = props.mode
  locations.value = []; checkedPaths.value = []; files.value = []
  clearPreview()
  editAuthors.value = false; editGenre.value = false; defaultAuthors.value = []; defaultGenre.value = ''; overwriteExisting.value = false
})
watch(mode, clearPreview)
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="添加漫画" width="min(1080px, 94vw)" top="5vh" class="import-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" :before-close="beforeClose" destroy-on-close>
    <div class="import-steps"><span :class="{ active: !preview }">1 选择来源</span><i></i><span :class="{ active: !!preview }">2 核对并导入</span></div>
    <div class="import-dialog-scroll">
      <template v-if="!preview">
        <div class="import-source-heading"><el-radio-group v-model="mode" :disabled="busy"><el-radio-button value="folder">图片文件夹</el-radio-button><el-radio-button value="cbz">已有 CBZ</el-radio-button></el-radio-group><el-button v-if="mode === 'folder'" :icon="FolderAdd" :disabled="busy" @click="addLocations">选择多个文件夹</el-button><el-button v-else :icon="DocumentAdd" :disabled="busy" @click="addFiles">选择多个 CBZ</el-button></div>
        <p v-if="mode === 'folder'" class="import-help">可添加多个位置后自由勾选。勾选父文件夹会读取其中所有作品；只导入部分时，请展开并仅勾选目标子文件夹。</p>
        <p v-else class="import-help">可多选 CBZ 文件，也可以分多次添加。确认前会先解析并展示作品资料。</p>
        <div v-if="mode === 'folder'" class="folder-picker" :class="{ 'picker-disabled': busy }">
          <el-tree v-if="locations.length" ref="tree" data-testid="import-tree" :data="locations" node-key="path" :props="treeProps" lazy :load="loadFolders" show-checkbox check-strictly :expand-on-click-node="false" :check-on-click-node="false" :default-checked-keys="checkedPaths" @check="checkedChanged"><template #default="{ node, data }"><div class="folder-tree-node" :title="data.path"><el-icon><Folder /></el-icon><span>{{ node.level === 1 ? data.path : data.name }}</span><el-button v-if="node.level === 1" link :icon="Close" :disabled="busy" aria-label="移除此来源位置" @click.stop="removeLocation(data.path)" /></div></template></el-tree>
          <div v-else class="import-source-empty"><el-icon :size="32"><FolderAdd /></el-icon><strong>选择想整理的漫画文件夹</strong><span>支持本地磁盘与 NAS 共享目录</span><el-button type="primary" :disabled="busy" @click="addLocations">选择文件夹</el-button></div>
        </div>
        <div v-else class="cbz-picker"><div v-if="!files.length" class="import-source-empty"><el-icon :size="32"><DocumentAdd /></el-icon><strong>选择已有漫画 CBZ</strong><span>保留压缩包中的图片和已有元数据</span><el-button type="primary" :disabled="busy" @click="addFiles">选择 CBZ 文件</el-button></div><div v-for="file in files" :key="file" class="cbz-source-row"><span :title="file">{{ file }}</span><el-button link :icon="Close" :disabled="busy" aria-label="移除此 CBZ 来源" @click="removeFile(file)" /></div></div>
        <div class="import-source-summary">已勾选 {{ sourceCount }} 个{{ mode === 'folder' ? '文件夹' : '文件' }}<span>此步骤只读取来源，不会写入漫画库。</span></div>
      </template>
      <template v-else>
        <div class="import-preview-heading"><div><strong>发现 {{ preview.items.length }} 本漫画</strong><p>检查解析结果，取消勾选暂不需要的作品。新作品默认勾选，已入库或移出的作品需手动勾选。</p></div><el-button link :icon="ArrowLeft" :disabled="busy" @click="clearPreview">重新选择来源</el-button></div>
        <el-table ref="table" data-testid="import-table" :data="preview.items" row-key="id" max-height="260" class="import-preview-table" @selection-change="(rows: ImportCandidate[]) => selected = rows.map(row => row.id)"><el-table-column type="selection" width="42" :selectable="() => !busy" /><el-table-column label="作品名称 / 来源" min-width="230"><template #default="{ row }"><div class="import-title">{{ displayTitle(row.metadata) }}</div><div class="import-source-path" :title="row.source">{{ row.source }}</div><div v-if="row.warning" class="import-warning" :title="row.warning">{{ row.warning }}</div></template></el-table-column><el-table-column label="作者" min-width="120"><template #default="{ row }">{{ row.metadata.authors.join('、') || '待填写' }}</template></el-table-column><el-table-column label="内容 / 发售" width="112"><template #default="{ row }"><div>{{ row.metadata.genre || '待填写' }}</div><small>{{ row.metadata.year ? `${row.metadata.year}${row.metadata.month ? '.' + row.metadata.month.padStart(2, '0') + (row.metadata.day ? '.' + row.metadata.day.padStart(2, '0') : '') : ''}` : '—' }}</small></template></el-table-column><el-table-column label="译者 / 色彩" min-width="130"><template #default="{ row }"><div>{{ row.metadata.translator || '—' }}</div><small>{{ [colorLabels[row.metadata.black_and_white] || '色彩未确认', row.metadata.age_rating === 'R18+' && row.metadata.uncensored ? '无修正' : ''].filter(Boolean).join(' · ') }}</small></template></el-table-column><el-table-column label="入库状态" width="84"><template #default="{ row }"><span class="import-status" :class="row.import_status">{{ statusNames[row.import_status as keyof typeof statusNames] }}</span></template></el-table-column></el-table>
        <div v-if="!preview.items.length" class="import-no-items">没有发现可读取的漫画，请返回检查文件夹是否含有图片或 CBZ。</div>
        <div class="import-batch-section"><div class="import-batch-title"><strong>批量填写资料</strong><span>可选，将应用于本次勾选的新作品</span></div><div class="batch-fields"><div class="batch-field"><el-checkbox data-testid="import-enable-authors" v-model="editAuthors" :disabled="busy">统一作者</el-checkbox><el-select data-testid="import-authors" aria-label="导入时统一作者" v-model="defaultAuthors" multiple filterable allow-create default-first-option :reserve-keyword="false" :disabled="busy || !editAuthors" placeholder="输入作者后按 Enter，可添加多位"><el-option v-for="name in authors" :key="name" :label="name" :value="name" /></el-select></div><div class="batch-field"><el-checkbox data-testid="import-enable-genre" v-model="editGenre" :disabled="busy">统一内容类型</el-checkbox><el-select data-testid="import-genre" aria-label="导入时统一内容类型" v-model="defaultGenre" filterable allow-create :disabled="busy || !editGenre" placeholder="请选择内容类型"><el-option v-for="genre in comicGenres" :key="genre" :label="genre" :value="genre" /></el-select></div></div><el-checkbox data-testid="import-overwrite-existing" v-model="overwriteExisting" :disabled="busy || (!editAuthors && !editGenre)" class="overwrite-existing">对勾选的已入库 / 已移出作品也应用以上作者和内容类型</el-checkbox><p v-if="existingSelected" class="import-existing-note">已勾选 {{ existingSelected }} 本已有记录。{{ overwriteExisting && (editAuthors || editGenre) ? '作者和内容类型将按上方设置更新，作品需重新审核。' : '保留人工资料；移出的作品会恢复到列表。' }}</p></div>
        <p class="import-draft-note">自动提取的资料会以待审核状态入库。请核对名称、创作人员、年龄分级和封面，未知信息不自动补造。</p>
      </template>
      <div v-if="errors.length" class="import-errors"><strong>{{ errors.length }} 项提示</strong><p v-for="(error, index) in errors" :key="index">{{ error }}</p></div>
    </div>
    <template #footer><div class="import-footer"><span v-if="busy" class="import-progress">{{ message || task }}…</span><span v-else>{{ preview ? `已选择 ${selected.length} 本作品` : '先预览，确认后入库' }}</span><div><el-button v-if="busy" :disabled="cancelling" @click="cancel">{{ cancelling ? '正在取消…' : '取消任务' }}</el-button><template v-else><el-button @click="close">取消</el-button><el-button v-if="!preview" type="primary" :disabled="!sourceCount" @click="prepare">读取并预览</el-button><el-button v-else type="primary" :icon="Check" :disabled="!selected.length" @click="commit">导入选中 {{ selected.length }} 本</el-button></template></div></div></template>
  </el-dialog>
</template>
