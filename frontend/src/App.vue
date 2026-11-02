<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TableInstance } from 'element-plus'
import { Reading, Collection, Clock, CircleCheck, Box, Plus, ArrowDown, FolderAdd, DocumentAdd, Search, Refresh, Download, FolderOpened, Lock, Monitor, Check, Files, ArrowRight, CopyDocument, Edit, Remove } from '@element-plus/icons-vue'
import DetailsPanel from './components/DetailsPanel.vue'
import ImportDialog from './components/ImportDialog.vue'
import BatchEditDialog from './components/BatchEditDialog.vue'
import type { Comic, Metadata, Bootstrap, ComicStatus, ExportPlan, ImportResult } from './types'
import { clone, displayTitle, statusLabels, errorMessage } from './types'
import { invoke } from './api'

type Section = 'all' | ComicStatus
const comics = ref<Comic[]>([])
const roots = ref<string[]>([])
const outputRoot = ref('')
const dataDir = ref('')
const version = ref('')
const selectedId = ref('')
const form = ref<Metadata | null>(null)
const savedMetadata = ref('')
const reviewed = ref(false)
const selectedIds = ref<string[]>([])
const libraryTable = ref<TableInstance>()
const section = ref<Section>('all')
const search = ref('')
const author = ref('')
const loading = ref(true)
const busy = ref(false)
const cancelling = ref(false)
const taskTitle = ref('')
const taskMessage = ref('')
const startupError = ref('')
const available = ref(true)
const plan = ref<ExportPlan | null>(null)
const planVisible = ref(false)
const resultVisible = ref(false)
const resultTitle = ref('')
const resultSummary = ref('')
const resultDetail = ref('')
const settingsVisible = ref(false)
const importVisible = ref(false)
const importMode = ref<'folder' | 'cbz'>('folder')
const batchVisible = ref(false)
const batchIds = ref<string[]>([])
let unsubscribe: (() => void) | undefined

const current = computed(() => comics.value.find(c => c.id === selectedId.value) || null)
const dirty = computed(() => !!form.value && JSON.stringify(form.value) !== savedMetadata.value)
const counts = computed(() => ({
  all: comics.value.length,
  draft: comics.value.filter(c => c.status === 'draft').length,
  approved: comics.value.filter(c => c.status === 'approved').length,
  packaged: comics.value.filter(c => c.status === 'packaged').length,
}))
const authors = computed(() => [...new Set(comics.value.flatMap(c => c.metadata.authors))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
const filtered = computed(() => {
  const term = search.value.trim().normalize('NFKC').toLocaleLowerCase()
  return comics.value.filter(c => {
    const m = c.metadata
    const searchable = [m.original_title, m.chinese_title, ...m.authors, ...m.tags, m.translator].join(' ').normalize('NFKC').toLocaleLowerCase()
    return (section.value === 'all' || c.status === section.value)
      && (!author.value || m.authors.includes(author.value)) && (!term || searchable.includes(term))
  })
})
const sectionTitle = computed(() => ({ all: '我的漫画库', draft: '待审核作品', approved: '已审核作品', packaged: '已生成作品' })[section.value])
const exportIds = computed(() => selectedIds.value.length ? selectedIds.value : (selectedId.value ? [selectedId.value] : []))
const disabled = computed(() => busy.value || loading.value || !available.value)
const navigation = [
  { key: 'all' as Section, title: '全部漫画', icon: Collection },
  { key: 'draft' as Section, title: '待审核', icon: Clock },
  { key: 'approved' as Section, title: '已审核', icon: CircleCheck },
  { key: 'packaged' as Section, title: '已生成', icon: Box },
]

function selectData(comic: Comic | null) {
  selectedId.value = comic?.id || ''
  form.value = comic ? clone(comic.metadata) : null
  savedMetadata.value = JSON.stringify(form.value)
  reviewed.value = !!comic && comic.status !== 'draft'
}

function replaceComic(comic: Comic) {
  const index = comics.value.findIndex(c => c.id === comic.id)
  if (index >= 0) comics.value[index] = comic
  else comics.value.unshift(comic)
}

async function refresh(preferId = selectedId.value) {
  const data = await invoke<Bootstrap>('bootstrap')
  comics.value = data.comics
  roots.value = data.roots
  outputRoot.value = data.outputRoot
  dataDir.value = data.dataDir
  selectedIds.value = selectedIds.value.filter(id => comics.value.some(c => c.id === id))
  selectData(comics.value.find(c => c.id === preferId) || null)
}

function showResult(title: string, summary: string, details: string[] | string = '') {
  resultTitle.value = title
  resultSummary.value = summary
  resultDetail.value = Array.isArray(details) ? details.join('\n\n') : details
  resultVisible.value = true
}

function showError(message: string) {
  showResult('操作未完成', '请查看以下信息，处理后重试。', message)
}

async function saveCurrent(): Promise<boolean> {
  if (!current.value || !form.value || !available.value) return false
  busy.value = true
  taskTitle.value = '保存资料'
  taskMessage.value = '正在保存到本机资料库…'
  try {
    const comic = await invoke<Comic>('save', { id: current.value.id, metadata: clone(form.value) })
    replaceComic(comic)
    selectData(comic)
    ElMessage.success('草稿已保存，核对后可重新审核')
    return true
  } catch (error) { showError(errorMessage(error)); return false }
  finally { busy.value = false; taskMessage.value = '' }
}

async function resolveEdits(): Promise<boolean> {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前作品有未保存的修改。保存为草稿后继续，或放弃此次修改。', '保留作品修改', {
      confirmButtonText: '保存后继续', cancelButtonText: '放弃修改',
      distinguishCancelAndClose: true, closeOnClickModal: false, closeOnPressEscape: false,
      type: 'warning',
    })
    return await saveCurrent()
  } catch (action) {
    if (action === 'cancel') { selectData(current.value); return true }
    return false
  }
}

async function selectComic(comic: Comic) {
  if (disabled.value || comic.id === selectedId.value) return
  if (!await resolveEdits()) return
  selectData(comic)
}

async function runTask<T>(title: string, action: () => Promise<T>, completed: (result: T) => void) {
  if (disabled.value) return
  busy.value = true
  cancelling.value = false
  taskTitle.value = title
  taskMessage.value = '正在准备…'
  try {
    const result = await action()
    await refresh()
    completed(result)
  } catch (error) {
    try { await refresh() } catch { /* Service errors are reported below. */ }
    showError(errorMessage(error))
  } finally { busy.value = false; cancelling.value = false; taskMessage.value = '' }
}

async function addDirectory() {
  if (disabled.value || !await resolveEdits()) return
  importMode.value = 'folder'
  importVisible.value = true
}

async function importCbz() {
  if (disabled.value || !await resolveEdits()) return
  importMode.value = 'cbz'
  importVisible.value = true
}

function dialogTask(state: { busy: boolean; title: string }) {
  busy.value = state.busy
  cancelling.value = false
  taskTitle.value = state.title
  taskMessage.value = state.busy ? '正在准备…' : ''
}

async function imported(result: ImportResult) {
  loading.value = true
  try {
    await refresh(selectedId.value || result.ids[0])
    showResult(result.cancelled ? '导入已取消' : '导入完成', `已导入 ${result.count} 本作品，${result.errors.length} 项未完成。${result.skipped ? `跳过 ${result.skipped} 项。` : ''}资料可在右侧继续核对。`, result.errors)
  } catch (error) { showError(errorMessage(error)) }
  finally { loading.value = false }
}

async function openBatchEditor() {
  if (disabled.value || !await resolveEdits()) return
  if (!exportIds.value.length) { ElMessage.info('请勾选或选择要编辑的作品'); return }
  batchIds.value = [...exportIds.value]
  batchVisible.value = true
}

async function batchSaved(count: number) {
  loading.value = true
  try { await refresh(); ElMessage.success(`已更新 ${count} 本作品，请重新审核`) }
  catch (error) { showError(errorMessage(error)) }
  finally { loading.value = false }
}

async function removeComics() {
  if (disabled.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(`将选中的 ${ids.length} 本作品移出漫画列表？源文件和已生成的 CBZ 都会保留。以后可通过添加漫画重新导入。`, '移出漫画列表', {
      confirmButtonText: '移出列表', cancelButtonText: '保留', type: 'warning', closeOnClickModal: false,
    })
  } catch { return }
  await runTask('移出漫画列表', () => invoke<{ count: number }>('remove', { ids }), (result) => {
    libraryTable.value?.clearSelection()
    selectedIds.value = []
    ElMessage.success(`已移出 ${result.count} 本作品，源文件已保留`)
  })
}

async function rescan() {
  if (disabled.value || !await resolveEdits()) return
  if (!roots.value.length) { ElMessage.info('请先添加一个漫画目录'); return }
  await runTask('重新扫描', () => invoke('scan', {}), (result: any) => {
    showResult(result.cancelled ? '扫描已取消' : '扫描完成', `已扫描 ${result.count} 本作品，${result.errors.length} 项未读取。`, result.errors)
  })
}

async function chooseOutput() {
  if (disabled.value) return false
  try {
    const path = await window.comic.chooseDirectory()
    if (!path) return false
    const result = await invoke('set_output', { path })
    outputRoot.value = result.outputRoot
    return true
  } catch (error) { showError(errorMessage(error)); return false }
}

async function approveCurrent() {
  if (!current.value || !form.value || disabled.value) return
  if (!reviewed.value) { ElMessage.warning('请先核对作品，并勾选审核确认'); return }
  const m = form.value
  if (!m.original_title.trim()) { ElMessage.warning('请填写完整日文原名'); return }
  if (m.translated && !m.chinese_title.trim()) { ElMessage.warning('汉化作品需要填写中文名称'); return }
  if (!m.authors.some(a => a.trim())) { ElMessage.warning('请填写至少一位作者'); return }
  await runTask('审核作品', () => invoke('approve', { id: current.value!.id, metadata: clone(form.value) }), () => {
    ElMessage.success('审核通过，可以生成 CBZ')
  })
}

async function prepareExport() {
  if (disabled.value || !await resolveEdits()) return
  const ids = [...exportIds.value]
  if (!ids.length) { ElMessage.info('请选择一本或多本已审核作品'); return }
  if (!outputRoot.value && !await chooseOutput()) return
  busy.value = true
  taskTitle.value = '检查生成计划'
  taskMessage.value = '正在检查审核状态、源文件与成品路径…'
  try {
    plan.value = await invoke<ExportPlan>('plan', { ids, outputRoot: outputRoot.value })
    planVisible.value = true
  } catch (error) { showError(errorMessage(error)) }
  finally { busy.value = false; taskMessage.value = '' }
}

async function confirmExport() {
  if (!plan.value || disabled.value) return
  const token = plan.value.token
  planVisible.value = false
  await runTask('生成并校验 CBZ', () => invoke('export', { token }), (result: any) => {
    showResult(result.cancelled ? '生成已取消' : '生成完成', `${result.completed.length} 本已生成并通过校验，${result.errors.length} 本失败。全部源文件已保留。`, [...result.errors, ...result.completed])
  })
  plan.value = null
}

async function cancelTask() {
  if (!busy.value || cancelling.value) return
  cancelling.value = true
  taskMessage.value = '正在取消，等待当前文件操作结束…'
  try { await invoke('cancel') }
  catch (error) { ElMessage.warning(errorMessage(error)) }
}

async function backup() {
  if (disabled.value || !await resolveEdits()) return
  try {
    const path = await window.comic.chooseBackup()
    if (!path) return
    await runTask('备份资料库', () => invoke('backup', { path }), () => {
      showResult('备份完成', '已备份目录索引、元数据和审核记录，不包含漫画图片。', path)
    })
  } catch (error) { showError(errorMessage(error)) }
}

async function closeRequested() {
  if (busy.value) { ElMessage.warning('任务进行中，请先取消并等待当前操作结束'); return }
  if (await resolveEdits()) await window.comic.closeWindow()
}

function sizeText(bytes: number) {
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

watch(form, () => { if (dirty.value) reviewed.value = false }, { deep: true, flush: 'sync' })
watch([dirty, busy], () => window.comic?.setWindowState({ dirty: dirty.value, busy: busy.value }), { immediate: true })
onMounted(async () => {
  if (!window.comic) {
    available.value = false
    startupError.value = '请通过 Comic Manage 桌面程序打开。此界面需要连接本机漫画服务。'
    loading.value = false
    return
  }
  unsubscribe = window.comic.onEvent((event, data) => {
    if (event === 'task-progress') taskMessage.value = data.message
    if (event === 'close-request') closeRequested()
    if (event === 'service-exit') {
      available.value = false
      startupError.value = data.message || '本机漫画服务已停止，请重启 Comic Manage。'
    }
  })
  try { await refresh(); version.value = await window.comic.getVersion() }
  catch (error) { available.value = false; startupError.value = errorMessage(error) }
  finally { loading.value = false }
})
onBeforeUnmount(() => unsubscribe?.())
</script>

<template>
  <div class="desktop-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-icon"><el-icon :size="25"><Reading /></el-icon></div><div><strong>漫画整理</strong><span>COMIC MANAGE</span></div></div>
      <div class="workspace-label">我的工作区</div>
      <nav class="navigation"><button v-for="item in navigation" :key="item.key" :class="{ active: section === item.key }" @click="section = item.key"><el-icon :size="18"><component :is="item.icon" /></el-icon><span>{{ item.title }}</span><b>{{ counts[item.key] }}</b></button></nav>
      <div class="sidebar-note"><div class="sidebar-note-icon"><el-icon><Lock /></el-icon></div><div><strong>你的漫画，只在本机</strong><p>先确认资料，再生成成品。<br />所有原始文件都会保留。</p></div></div>
      <div class="sidebar-bottom"><button :disabled="disabled" @click="backup"><el-icon><CopyDocument /></el-icon>备份资料库</button><button @click="settingsVisible = true"><el-icon><Monitor /></el-icon>工作区信息</button><div class="version-row"><span>本地桌面版</span><span v-if="version">v{{ version }}</span></div></div>
    </aside>

    <main class="workspace">
      <header class="workspace-header"><div><div class="eyebrow">LIBRARY / 漫画管理</div><h1>{{ sectionTitle }}<span class="heading-count">{{ counts[section] }}</span></h1><p>整理作品，保留完整的故事。</p></div><div class="header-actions"><el-button :icon="Refresh" :disabled="disabled" @click="rescan">重新扫描</el-button><el-dropdown trigger="click" :disabled="disabled" @command="(command: string) => command === 'folder' ? addDirectory() : importCbz()"><el-button type="primary" :icon="Plus" :disabled="disabled">添加漫画<el-icon class="dropdown-arrow"><ArrowDown /></el-icon></el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="folder" :icon="FolderAdd">选择图片文件夹</el-dropdown-item><el-dropdown-item command="cbz" :icon="DocumentAdd">导入已有 CBZ</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></header>

      <el-alert v-if="startupError" :title="startupError" description="请关闭并重新打开桌面程序。漫画源文件和本机资料库不会被删除。" type="error" :closable="false" show-icon class="startup-error" />

      <div class="work-area">
        <section class="library-panel">
          <div class="library-toolbar"><el-input v-model="search" clearable :prefix-icon="Search" placeholder="搜索中日文标题、作者或标签" :disabled="disabled" class="search-input" /><el-select v-model="author" clearable filterable placeholder="全部作者" :disabled="disabled" class="author-select"><el-option v-for="name in authors" :key="name" :label="name" :value="name" /></el-select></div>
          <div class="library-summary"><span>{{ filtered.length }} 本作品<span v-if="selectedIds.length" class="selected-count">已选择 {{ selectedIds.length }} 本</span></span><div class="library-batch-actions"><el-button link :icon="Edit" :disabled="disabled || !exportIds.length" @click="openBatchEditor">批量编辑</el-button><el-button link :icon="Remove" :disabled="disabled || !exportIds.length" @click="removeComics">移出列表</el-button></div><span v-if="counts.draft && !exportIds.length" class="review-reminder"><i></i>{{ counts.draft }} 本等待审核</span><span v-else-if="!exportIds.length" class="review-reminder clear"><el-icon><Check /></el-icon>资料已整理</span></div>

          <div class="table-area" v-loading="loading">
            <el-table v-if="comics.length" v-show="filtered.length > 0" ref="libraryTable" :data="filtered" row-key="id" :current-row-key="selectedId" highlight-current-row height="100%" :class="{ 'table-disabled': disabled }" @row-click="selectComic" @selection-change="(rows: Comic[]) => selectedIds = rows.map(row => row.id)" empty-text="没有匹配的漫画">
              <el-table-column type="selection" width="43" :reserve-selection="true" :selectable="() => !disabled" />
              <el-table-column label="作品" min-width="240"><template #default="scope"><div class="comic-cell"><div class="comic-file-icon" :class="scope.row.kind"><el-icon :size="21"><component :is="scope.row.kind === 'cbz' ? Files : Reading" /></el-icon></div><div class="comic-titles"><strong>{{ displayTitle(scope.row.metadata) }}</strong><span v-if="scope.row.metadata.translated && scope.row.metadata.original_title">{{ scope.row.metadata.original_title }}</span><span v-else class="comic-source" :title="scope.row.source">{{ scope.row.kind === 'cbz' ? 'CBZ 文件' : '本地图片目录' }}</span></div></div></template></el-table-column>
              <el-table-column label="作者" min-width="105" show-overflow-tooltip><template #default="scope"><span :class="{ 'muted-text': !scope.row.metadata.authors.length }">{{ scope.row.metadata.authors.join('、') || '未填写' }}</span></template></el-table-column>
              <el-table-column label="页数" width="60" align="center"><template #default="scope"><span class="page-count">{{ scope.row.page_count }}</span></template></el-table-column>
              <el-table-column label="状态" width="92"><template #default="scope"><span class="status-pill" :class="scope.row.status">{{ statusLabels[scope.row.status as ComicStatus] }}</span></template></el-table-column>
            </el-table>
            <div v-if="!filtered.length && !loading" class="library-empty"><div class="library-empty-icon"><el-icon :size="38"><Collection /></el-icon><span class="empty-icon-spark"></span></div><template v-if="comics.length"><h2>没有找到匹配的作品</h2><p>试试其他标题、作者或标签。</p><el-button plain @click="search = ''; author = ''; section = 'all'">清除筛选</el-button></template><template v-else><span class="empty-eyebrow">让你的漫画库井然有序</span><h2>从第一本漫画开始</h2><p>勾选多个本地或 NAS 文件夹，也可以导入已有 CBZ。<br />先预览解析资料，再确认要入库的作品。</p><el-button type="primary" :icon="FolderAdd" :disabled="disabled" @click="addDirectory">添加漫画目录</el-button><button class="empty-import-link" :disabled="disabled" @click="importCbz">或导入已有 CBZ<el-icon><ArrowRight /></el-icon></button></template></div>
          </div>

          <footer class="library-footer"><div class="output-location"><div class="output-icon"><el-icon :size="18"><FolderOpened /></el-icon></div><div class="output-copy"><span>成品目录</span><button v-if="outputRoot" :title="outputRoot" :disabled="disabled" @click="chooseOutput">{{ outputRoot }}</button><button v-else class="output-unset" :disabled="disabled" @click="chooseOutput">选择一个独立目录…</button></div><el-button link :disabled="disabled" @click="chooseOutput">{{ outputRoot ? '更改' : '选择' }}</el-button></div><el-button type="primary" :icon="Download" :disabled="disabled || !exportIds.length" @click="prepareExport">生成 CBZ<span v-if="selectedIds.length > 1" class="export-count">{{ selectedIds.length }}</span></el-button></footer>
        </section>

        <DetailsPanel :comic="current" v-model="form" v-model:reviewed="reviewed" :busy="disabled" :dirty="dirty" @save="saveCurrent" @approve="approveCurrent" @error="showError" />
      </div>

      <div v-if="busy && !importVisible && !batchVisible" class="task-bar"><div class="task-indicator"></div><div class="task-copy"><strong>{{ taskTitle }}</strong><span :title="taskMessage">{{ taskMessage }}</span></div><el-button link :disabled="cancelling" @click="cancelTask">{{ cancelling ? '正在取消…' : '取消任务' }}</el-button></div>
      <footer class="workspace-footnote"><span class="connection-dot" :class="{ offline: !available }"></span><span>{{ available ? '本机资料库已连接' : '本机服务不可用' }}</span><span class="footnote-divider">/</span><span>原图保留 · 人工审核 · 无损打包</span><span v-if="dirty" class="unsaved-note">当前作品有未保存修改</span></footer>
    </main>

    <ImportDialog v-model="importVisible" :mode="importMode" :authors="authors" :message="taskMessage" @state="dialogTask" @completed="imported" />
    <BatchEditDialog v-model="batchVisible" :ids="batchIds" :authors="authors" @state="dialogTask" @completed="batchSaved" />

    <el-dialog v-model="planVisible" title="确认生成计划" width="min(940px, 90vw)" :close-on-click-modal="false" destroy-on-close class="plan-dialog"><template v-if="plan"><div class="dialog-intro"><div class="dialog-icon"><el-icon :size="24"><Box /></el-icon></div><div><h3>即将生成 {{ plan.items.length }} 本漫画</h3><p>先检查成品路径，确认后将逐本打包并校验。</p></div></div><el-alert title="只新增文件，不覆盖已有成品；所有源文件保留。" type="info" :closable="false" show-icon /><el-table :data="plan.items" max-height="330" class="plan-table"><el-table-column label="作品" prop="title" min-width="160" /><el-table-column label="成品路径" min-width="320"><template #default="scope"><span class="plan-path" :title="scope.row.source">{{ scope.row.destination }}</span><div class="plan-source">源：{{ scope.row.source }}</div></template></el-table-column><el-table-column label="页数" prop="pageCount" width="60" align="center" /><el-table-column label="附件" prop="attachmentCount" width="60" align="center" /></el-table><p class="plan-note">图片不转码，完成后检查 CRC、SHA-256、页数和 ComicInfo.xml。原始内容约 {{ sizeText(plan.totalBytes) }}，请保留足够空间。</p></template><template #footer><el-button @click="planVisible = false">返回检查</el-button><el-button type="primary" :icon="Check" @click="confirmExport">确认生成并校验</el-button></template></el-dialog>

    <el-dialog v-model="resultVisible" :title="resultTitle" width="min(700px, 90vw)" destroy-on-close><p class="result-summary">{{ resultSummary }}</p><el-input v-if="resultDetail" :model-value="resultDetail" readonly type="textarea" :rows="10" resize="none" class="result-details" /><template #footer><el-button type="primary" @click="resultVisible = false">知道了</el-button></template></el-dialog>
    <el-dialog v-model="settingsVisible" title="工作区信息" width="min(640px, 90vw)"><div class="workspace-info"><label>本机资料库</label><p>{{ dataDir || '尚未连接' }}</p><label>已添加的目录</label><p v-for="root in roots" :key="root">{{ root }}</p><p v-if="!roots.length" class="muted-text">还没有添加目录</p><label>成品目录</label><p>{{ outputRoot || '尚未设置' }}</p></div><template #footer><el-button type="primary" @click="settingsVisible = false">关闭</el-button></template></el-dialog>
  </div>
</template>
