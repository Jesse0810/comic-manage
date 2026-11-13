<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Connection } from '@element-plus/icons-vue'
import type { KomgaConfig, KomgaConnectionInput, KomgaLibrary, KomgaAutoSyncJob } from '../types'
import { errorMessage } from '../types'

const props = defineProps<{ modelValue: boolean; continueReview?: boolean; continueSync?: boolean; continuePull?: boolean; jobs?: KomgaAutoSyncJob[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'saved', value: KomgaConfig): void
  (e: 'jobs'): void
}>()
const emptyConfig = (): KomgaConfig => ({ url: '', libraryId: '', libraryName: '', hasApiKey: false, autoSyncEnabled: true })
const original = ref<KomgaConfig>(emptyConfig())
const url = ref('')
const apiKey = ref('')
const clearKey = ref(false)
const libraryId = ref('')
const autoSyncEnabled = ref(true)
const libraries = ref<KomgaLibrary[]>([])
const busy = ref(false)
const action = ref<'load' | 'test' | 'save' | ''>('')
const tested = ref(false)
const error = ref('')
let request = 0
const normalizeUrl = (value: string) => value.trim().replace(/\/+$/, '')
const urlChanged = computed(() => normalizeUrl(url.value) !== normalizeUrl(original.value.url))
const connectionChanged = computed(() => urlChanged.value || !!apiKey.value.trim())
const hasKey = computed(() => !clearKey.value && (!!apiKey.value.trim() || (original.value.hasApiKey && !urlChanged.value)))
const selectedLibrary = computed(() => libraries.value.find(library => library.id === libraryId.value))
const saveLabel = computed(() => clearKey.value ? '保存并清除密钥' : props.continueReview ? '保存并检查' : props.continueSync ? '保存并同步' : props.continuePull ? '保存并读取书库' : '保存配置')
const pendingJobs = computed(() => (props.jobs || []).filter(job => job.status !== 'Synced' && (job.status !== 'Approved' || job.canRetry)).length)

function setBusy(value: boolean, nextAction: typeof action.value = '') {
  busy.value = value
  action.value = nextAction
  emit('state', { busy: value, title: value ? ({ load: '读取 Komga 配置', test: '测试 Komga 连接', save: '保存 Komga 配置', '': '' })[nextAction] : '' })
}
function connectionInput(): KomgaConnectionInput {
  return { url: normalizeUrl(url.value), ...(clearKey.value ? { clearKey: true } : apiKey.value.trim() ? { apiKey: apiKey.value.trim() } : {}) }
}
function validUrl() {
  try {
    const parsed = new URL(normalizeUrl(url.value))
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error()
    return true
  } catch { error.value = '请输入完整的 http:// 或 https:// 服务器地址，不要在地址中填写密钥或账号。'; return false }
}
async function load() {
  const currentRequest = ++request
  error.value = ''; apiKey.value = ''; clearKey.value = false; tested.value = false
  original.value = emptyConfig(); url.value = ''; libraryId.value = ''; libraries.value = []
  setBusy(true, 'load')
  try {
    const config = await window.comic.komgaConfig()
    if (currentRequest !== request || !props.modelValue) return
    original.value = config
    url.value = config.url
    libraryId.value = config.libraryId
    autoSyncEnabled.value = config.autoSyncEnabled !== false
    libraries.value = config.libraryId ? [{ id: config.libraryId, name: config.libraryName || config.libraryId }] : []
  } catch (reason) { if (currentRequest === request) error.value = errorMessage(reason) }
  finally { if (currentRequest === request) setBusy(false) }
}
async function testConnection() {
  if (busy.value) return
  error.value = ''
  if (!validUrl()) return
  if (!hasKey.value) { error.value = urlChanged.value && original.value.hasApiKey ? '服务器地址已改变，请重新输入这个服务器的 API Key。' : '请先填写 API Key。'; return }
  setBusy(true, 'test')
  try {
    const result = await window.comic.testKomgaConfig(connectionInput())
    libraries.value = result.libraries
    if (!result.libraries.some(library => library.id === libraryId.value)) libraryId.value = result.libraries.length === 1 ? result.libraries[0]!.id : ''
    tested.value = true
    if (!result.libraries.length) error.value = '连接成功，但没有可用书库。请先在 Komga 中创建书库，并检查 API Key 的权限。'
  } catch (reason) { tested.value = false; error.value = errorMessage(reason) }
  finally { setBusy(false) }
}
async function save() {
  if (busy.value) return
  error.value = ''
  if (!validUrl()) return
  if (urlChanged.value && !apiKey.value.trim() && !clearKey.value) { error.value = '服务器地址已改变，请重新输入这个服务器的 API Key。'; return }
  if (!clearKey.value && !hasKey.value) { error.value = '请填写 API Key，然后测试连接。'; return }
  if (!clearKey.value && (connectionChanged.value || !original.value.libraryId) && !tested.value) { error.value = '请先测试连接，再选择要同步的书库。'; return }
  if (!clearKey.value && !selectedLibrary.value) { error.value = '请选择目标书库。'; return }
  let saved: KomgaConfig | undefined
  setBusy(true, 'save')
  try {
    saved = await window.comic.saveKomgaConfig({ ...connectionInput(), libraryId: libraryId.value, libraryName: selectedLibrary.value?.name || '', autoSyncEnabled: autoSyncEnabled.value })
    apiKey.value = ''
  } catch (reason) { error.value = errorMessage(reason) }
  finally { setBusy(false) }
  if (saved) { emit('update:modelValue', false); emit('saved', saved) }
}
watch([url, apiKey, clearKey], () => { tested.value = false; error.value = '' })
watch(urlChanged, changed => { if (changed) { libraryId.value = ''; libraries.value = [] } })
watch(() => props.modelValue, visible => {
  if (visible) void load()
  else { apiKey.value = ''; clearKey.value = false; tested.value = false }
})
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="连接 Komga" width="min(620px, 94vw)" top="5vh" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close class="komga-settings-dialog">
    <p class="connection-intro">连接 Komga 后，可直接读取书库、编辑资料，审核后将修改同步回去。明确清空简介、标签或链接时，会清空远端对应内容。</p>
    <el-form label-position="top" :disabled="busy" class="connection-form" v-loading="action === 'load'">
      <el-form-item label="服务器地址" required><el-input data-testid="komga-url" v-model="url" placeholder="例如 http://nas:25600 或 https://komga.example.com" autocomplete="off" /><p class="connection-help">如部署在子路径，请包含完整路径；不要填写 /api/v1。</p></el-form-item>
      <el-form-item label="API Key" :required="!original.hasApiKey">
        <el-input data-testid="komga-api-key" v-model="apiKey" type="password" :disabled="busy || clearKey" :placeholder="original.hasApiKey && !urlChanged ? '已保存密钥；留空继续使用，填写则替换' : '输入此服务器的 API Key'" autocomplete="new-password" />
        <p class="connection-help">使用具备管理员权限的 Komga API Key。密钥加密保存在本机，已保存的内容不会回显。</p>
        <p v-if="urlChanged && original.hasApiKey" class="connection-warning">地址已改变，需要重新输入 API Key。</p>
        <el-checkbox v-if="original.hasApiKey" data-testid="komga-clear-key" v-model="clearKey" :disabled="busy">清除已保存的 API Key</el-checkbox>
      </el-form-item>
      <div class="connection-test"><el-button data-testid="komga-test" :icon="Connection" :loading="action === 'test'" :disabled="busy || clearKey" @click="testConnection">测试连接并读取书库</el-button><span v-if="tested && libraries.length" class="connection-ok" data-testid="komga-test-success">连接成功 · {{ libraries.length }} 个书库</span></div>
      <el-form-item label="目标书库" :required="!clearKey"><el-select data-testid="komga-library" v-model="libraryId" :disabled="busy || clearKey || !libraries.length" placeholder="先测试连接，再选择书库" class="full-width"><el-option v-for="library in libraries" :key="library.id" :label="library.name" :value="library.id" /></el-select><p class="connection-help">可直接读取此书库中的作品，无需逐本本地导入。系列级资料只有选全系列时才会更新。</p></el-form-item>
      <div class="auto-sync-setting" data-testid="komga-auto-sync-setting">
        <div class="auto-sync-heading"><strong>生成后自动同步实体</strong><el-switch data-testid="komga-auto-sync" v-model="autoSyncEnabled" aria-label="生成后自动同步实体" /></div>
        <p class="connection-help">新审核的本地作品生成并校验 CBZ 后，等待 Komga 扫描入库，再自动同步自定义分类和角色。成品目录需位于这个书库的监控目录中。</p>
        <p class="connection-help">自动任务不改标题、作者等原生资料；这些资料先随 CBZ 导入。现有远端作品仍可编辑、审核并手动同步。</p>
        <p class="connection-help">任务保存在本机；软件运行时重试，重新启动后继续。关闭开关会暂停自动处理，不会删除任务。</p>
      </div>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="komga-config-error" />
    <template #footer><div class="connection-footer"><el-button data-testid="komga-auto-jobs" :disabled="busy" @click="emit('jobs')">同步任务<span v-if="pendingJobs">（{{ pendingJobs }}）</span></el-button><span class="footer-space"></span><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button data-testid="komga-save" type="primary" :icon="Check" :loading="action === 'save'" :disabled="busy" @click="save">{{ saveLabel }}</el-button></div></template>
  </el-dialog>
</template>

<style scoped>
:global(.komga-settings-dialog) { display: flex; flex-direction: column; max-height: 90vh; }
:global(.komga-settings-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; }
.connection-intro { margin: 0 0 18px; color: #6e8270; font-size: 12px; line-height: 1.8; }
.connection-form { max-height: calc(85vh - 170px); overflow-y: auto; padding-right: 4px; }
.connection-help { font-size: 11px; line-height: 1.7; color: #8b9a87; margin: 6px 0 0; }
.connection-warning { width: 100%; font-size: 11px; color: #a47736; margin: 5px 0 0; }
.connection-test { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: -2px 0 18px; }
.connection-ok { color: #39775d; font-size: 11px; }
.connection-form :deep(.el-form-item) { margin-bottom: 17px; }
.auto-sync-setting { margin-top: 4px; border: 1px solid #e1e9de; background: #f6f9f4; border-radius: 10px; padding: 13px 15px; }
.auto-sync-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; color: #45674f; font-size: 13px; }
.connection-footer { display: flex; align-items: center; gap: 8px; }
.connection-footer :deep(.el-button + .el-button) { margin-left: 0; }
.footer-space { flex: 1; }
</style>
