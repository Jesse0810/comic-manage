<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Connection } from '@element-plus/icons-vue'
import type { KomgaConfig, KomgaConnectionInput, KomgaLibrary } from '../types'
import { errorMessage } from '../types'

const props = defineProps<{ modelValue: boolean; continueSync?: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'saved', value: KomgaConfig): void
}>()
const emptyConfig = (): KomgaConfig => ({ url: '', libraryId: '', libraryName: '', hasApiKey: false })
const original = ref<KomgaConfig>(emptyConfig())
const url = ref('')
const apiKey = ref('')
const clearKey = ref(false)
const libraryId = ref('')
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
const saveLabel = computed(() => clearKey.value ? '保存并清除密钥' : props.continueSync ? '保存并同步' : '保存配置')

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
    saved = await window.comic.saveKomgaConfig({ ...connectionInput(), libraryId: libraryId.value, libraryName: selectedLibrary.value?.name || '' })
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
    <p class="connection-intro">连接你的 Komga 服务器，将已审核资料同步到指定书库。空的简介、标签和链接会清空远端对应内容。</p>
    <el-form label-position="top" :disabled="busy" class="connection-form" v-loading="action === 'load'">
      <el-form-item label="服务器地址" required><el-input data-testid="komga-url" v-model="url" placeholder="例如 http://nas:25600 或 https://komga.example.com" autocomplete="off" /><p class="connection-help">如部署在子路径，请包含完整路径；不要填写 /api/v1。</p></el-form-item>
      <el-form-item label="API Key" :required="!original.hasApiKey">
        <el-input data-testid="komga-api-key" v-model="apiKey" type="password" :disabled="busy || clearKey" :placeholder="original.hasApiKey && !urlChanged ? '已保存密钥；留空继续使用，填写则替换' : '输入此服务器的 API Key'" autocomplete="new-password" />
        <p class="connection-help">使用具备管理员权限的 Komga API Key。密钥加密保存在本机，已保存的内容不会回显。</p>
        <p v-if="urlChanged && original.hasApiKey" class="connection-warning">地址已改变，需要重新输入 API Key。</p>
        <el-checkbox v-if="original.hasApiKey" data-testid="komga-clear-key" v-model="clearKey" :disabled="busy">清除已保存的 API Key</el-checkbox>
      </el-form-item>
      <div class="connection-test"><el-button data-testid="komga-test" :icon="Connection" :loading="action === 'test'" :disabled="busy || clearKey" @click="testConnection">测试连接并读取书库</el-button><span v-if="tested && libraries.length" class="connection-ok" data-testid="komga-test-success">连接成功 · {{ libraries.length }} 个书库</span></div>
      <el-form-item label="目标书库" :required="!clearKey"><el-select data-testid="komga-library" v-model="libraryId" :disabled="busy || clearKey || !libraries.length" placeholder="先测试连接，再选择书库" class="full-width"><el-option v-for="library in libraries" :key="library.id" :label="library.name" :value="library.id" /></el-select><p class="connection-help">成品需已被此书库扫描。系列级资料只有选全系列时才会更新。</p></el-form-item>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="komga-config-error" />
    <template #footer><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button data-testid="komga-save" type="primary" :icon="Check" :loading="action === 'save'" :disabled="busy" @click="save">{{ saveLabel }}</el-button></template>
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
</style>
