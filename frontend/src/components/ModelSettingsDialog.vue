<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Connection, Delete, Plus, Star } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { invoke } from '../api'
import { errorMessage } from '../types'
import type { ModelConfiguration, ModelProfile, ModelProtocol } from '../types'

interface ProfileDraft {
  id?: string
  name: string
  provider: ModelProtocol
  base_url: string
  model: string
}
const props = defineProps<{ modelValue: boolean; configuration: ModelConfiguration }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'updated', value: ModelConfiguration): void
}>()
const protocols: Record<ModelProtocol, { label: string; endpoint: string; authHost?: string }> = {
  openai: { label: 'OpenAI 兼容 · Chat Completions', endpoint: 'https://api.openai.com/v1', authHost: 'api.openai.com' },
  'openai-responses': { label: 'OpenAI · Responses', endpoint: 'https://api.openai.com/v1', authHost: 'api.openai.com' },
  ollama: { label: 'Ollama · 本地接口', endpoint: 'http://127.0.0.1:11434' },
  anthropic: { label: 'Anthropic · Messages', endpoint: 'https://api.anthropic.com/v1', authHost: 'api.anthropic.com' },
  gemini: { label: 'Google · Gemini', endpoint: 'https://generativelanguage.googleapis.com/v1beta', authHost: 'generativelanguage.googleapis.com' },
}
const presets: { label: string; provider: ModelProtocol; endpoint?: string }[] = [
  { label: 'Ollama', provider: 'ollama' },
  { label: 'LM Studio', provider: 'openai', endpoint: 'http://127.0.0.1:1234/v1' },
  { label: 'OpenAI', provider: 'openai' },
  { label: 'Anthropic', provider: 'anthropic' },
  { label: 'Gemini', provider: 'gemini' },
]
const emptyForm = (): ProfileDraft => ({ name: '', provider: 'openai', base_url: '', model: '' })
const form = ref<ProfileDraft>(emptyForm())
const apiKey = ref('')
const clearKey = ref(false)
const busy = ref<'load' | 'test' | 'save' | 'delete' | 'default' | ''>('')
const error = ref('')
const status = ref('')
let request = 0

const original = computed(() => props.configuration.profiles.find(profile => profile.id === form.value.id))
const normalizeEndpoint = (value: string) => value.trim().replace(/\/+$/, '')
const connectionChanged = computed(() => !!original.value &&
  (original.value.provider !== form.value.provider || normalizeEndpoint(original.value.base_url) !== normalizeEndpoint(form.value.base_url)))
const canReuseKey = computed(() => !!original.value?.has_key && !connectionChanged.value)
const hasKey = computed(() => !clearKey.value && (!!apiKey.value.trim() || canReuseKey.value))
const keyRequired = computed(() => {
  const host = protocols[form.value.provider].authHost
  return !!host && parseEndpoint()?.hostname === host
})
const validDraft = computed(() => !!form.value.name.trim() && !!form.value.model.trim() && !!parseEndpoint() && (!keyRequired.value || hasKey.value))
const isDefault = computed(() => !!original.value && original.value.id === props.configuration.default_id)

function parseEndpoint() {
  try {
    const parsed = new URL(normalizeEndpoint(form.value.base_url))
    if (!/^https?:\/\//i.test(form.value.base_url.trim()) || !['http:', 'https:'].includes(parsed.protocol) ||
        !parsed.hostname || parsed.username || parsed.password || parsed.search || parsed.hash) return null
    return parsed
  } catch { return null }
}
function resetFeedback() { error.value = ''; status.value = '' }
function selectProfile(profile?: ModelProfile) {
  form.value = profile ? { id: profile.id, name: profile.name, provider: profile.provider, base_url: profile.base_url, model: profile.model } : emptyForm()
  apiKey.value = ''
  clearKey.value = false
  resetFeedback()
}
function chooseProvider(provider: ModelProtocol) {
  if (provider === form.value.provider) return
  form.value.provider = provider
  form.value.base_url = protocols[provider].endpoint
}
function usePreset(preset: typeof presets[number]) {
  form.value = { ...form.value, name: preset.label, provider: preset.provider, base_url: preset.endpoint || protocols[preset.provider].endpoint }
}
function profileInput() {
  return {
    profile: {
      ...(form.value.id ? { id: form.value.id } : {}),
      name: form.value.name.trim(),
      provider: form.value.provider,
      base_url: normalizeEndpoint(form.value.base_url),
      model: form.value.model.trim(),
    },
    ...(apiKey.value.trim() ? { api_key: apiKey.value.trim() } :
      clearKey.value || (original.value?.has_key && connectionChanged.value) ? { clear_key: true } : {}),
  }
}
function activeProfile(config: ModelConfiguration) {
  return config.profiles.find(profile => profile.id === config.default_id) || config.profiles[0]
}

async function load() {
  const currentRequest = ++request
  selectProfile(activeProfile(props.configuration))
  busy.value = 'load'
  try {
    const result = await invoke<ModelConfiguration>('model_config_get')
    if (currentRequest !== request || !props.modelValue) return
    emit('updated', result)
    selectProfile(activeProfile(result))
  } catch (reason) {
    if (currentRequest === request) error.value = errorMessage(reason)
  } finally {
    if (currentRequest === request) busy.value = ''
  }
}
async function testConnection() {
  if (busy.value || !validDraft.value) return
  const currentRequest = ++request
  const input = profileInput()
  const secret = apiKey.value.trim()
  resetFeedback()
  busy.value = 'test'
  try {
    const result = await invoke<{ latency_ms?: number }>('model_profile_test', input)
    if (currentRequest !== request || !props.modelValue) return
    const seconds = typeof result?.latency_ms === 'number' ? Math.max(.1, result.latency_ms / 1000).toFixed(1) : ''
    status.value = seconds ? '连接成功，模型已响应（' + seconds + ' 秒）。' : '连接成功，模型已响应。'
  } catch (reason) {
    if (currentRequest === request) error.value = secret ? errorMessage(reason).replaceAll(secret, '[已隐藏]') : errorMessage(reason)
  } finally {
    if (currentRequest === request) busy.value = ''
  }
}
async function updateProfile(action: 'save' | 'delete' | 'default') {
  if (busy.value || (action === 'save' ? !validDraft.value : !original.value)) return
  const profileId = form.value.id
  const oldIds = new Set(props.configuration.profiles.map(profile => profile.id))
  const currentRequest = ++request
  const secret = apiKey.value.trim()
  resetFeedback()
  busy.value = action
  try {
    const result = action === 'save'
      ? await invoke<ModelConfiguration>('model_profile_save', profileInput())
      : await invoke<ModelConfiguration>(action === 'delete' ? 'model_profile_delete' : 'model_profile_default', { id: profileId })
    if (currentRequest !== request || !props.modelValue) return
    emit('updated', result)
    const saved = result.profiles.find(profile => profile.id === profileId) || result.profiles.find(profile => !oldIds.has(profile.id))
    if (action === 'save') selectProfile(saved)
    if (action === 'delete') selectProfile(activeProfile(result))
    status.value = { save: '模型配置已保存。', delete: '模型配置已删除。', default: '已设为默认模型。' }[action]
  } catch (reason) {
    if (currentRequest === request) error.value = secret ? errorMessage(reason).replaceAll(secret, '[已隐藏]') : errorMessage(reason)
  } finally {
    if (currentRequest === request) busy.value = ''
  }
}
async function confirmDelete() {
  if (!original.value || busy.value) return
  try {
    await ElMessageBox.confirm('删除后需要重新填写连接信息和密钥。', '删除「' + original.value.name + '」？',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await updateProfile('delete')
}

const canClose = computed(() => !busy.value || busy.value === 'load' || busy.value === 'test')
function visibility(value: boolean) {
  if (value || canClose.value) emit('update:modelValue', value)
}
watch([() => form.value.provider, () => form.value.base_url], () => { apiKey.value = ''; clearKey.value = false }, { flush: 'sync' })
watch([form, apiKey, clearKey], resetFeedback, { deep: true, flush: 'sync' })
watch(() => props.modelValue, visible => {
  if (visible) void load()
  else {
    request++
    busy.value = ''
    selectProfile()
  }
}, { immediate: true, flush: 'sync' })
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="visibility" title="模型管理" width="min(960px, 96vw)" top="5vh"
    class="model-settings-dialog" :close-on-click-modal="false" :close-on-press-escape="canClose" :show-close="canClose" append-to-body destroy-on-close>
    <div class="model-settings-layout">
      <aside class="model-profiles">
        <div class="model-list-header">
          <strong>模型配置</strong>
          <el-button data-testid="model-profile-new" :icon="Plus" size="small" :disabled="!!busy" @click="selectProfile()">新增</el-button>
        </div>
        <ul class="model-profile-list" data-testid="model-profile-list">
          <li v-for="profile in configuration.profiles" :key="profile.id">
            <button type="button" class="model-profile-option" :class="{ 'is-active': profile.id === form.id }"
              data-testid="model-profile-option" :data-profile-id="profile.id" :aria-pressed="profile.id === form.id" :disabled="!!busy"
              @click="selectProfile(profile)">
              <span class="model-profile-line">
                <strong :title="profile.name">{{ profile.name }}</strong>
                <el-tag v-if="profile.id === configuration.default_id" effect="plain" size="small">默认</el-tag>
              </span>
              <span class="model-profile-target" :title="profile.model">{{ profile.model }}</span>
            </button>
          </li>
        </ul>
        <p v-if="!configuration.profiles.length" class="model-empty">新增本地模型或在线模型。</p>
      </aside>
      <section class="model-editor" :aria-busy="!!busy">
        <div class="model-editor-heading">
          <h3>{{ original ? '编辑模型' : '新增模型' }}</h3>
          <el-button v-if="original" data-testid="model-profile-default" :icon="Star" size="small" :disabled="!!busy || isDefault"
            :loading="busy === 'default'" @click="updateProfile('default')">{{ isDefault ? '默认模型' : '设为默认' }}</el-button>
        </div>
        <div v-if="!original" class="model-presets" aria-label="快速填入服务地址">
          <el-button v-for="preset in presets" :key="preset.label" size="small" plain :disabled="!!busy" @click="usePreset(preset)">{{ preset.label }}</el-button>
        </div>
        <el-form label-position="top" class="model-form" :disabled="!!busy" @submit.prevent="updateProfile('save')">
          <el-form-item label="显示名称" required>
            <el-input data-testid="model-profile-name" v-model="form.name" maxlength="80" placeholder="例如 书库整理模型" />
          </el-form-item>
          <el-form-item label="接口类型" required>
            <el-select data-testid="model-profile-provider" :model-value="form.provider" @update:model-value="chooseProvider($event)">
              <el-option v-for="(protocol, key) in protocols" :key="key" :label="protocol.label" :value="key" />
            </el-select>
          </el-form-item>
          <el-form-item label="服务地址" class="model-wide" required>
            <el-input data-testid="model-profile-endpoint" v-model="form.base_url" autocomplete="off" maxlength="600"
              :placeholder="protocols[form.provider].endpoint" />
            <p class="model-help">本机或局域网服务可用 HTTP，公网服务使用 HTTPS。API Key 请单独填写。</p>
          </el-form-item>
          <el-form-item label="模型 ID" class="model-wide" required>
            <el-input data-testid="model-profile-model" v-model="form.model" autocomplete="off" maxlength="240" placeholder="填写服务端提供的模型名称或 ID" />
          </el-form-item>
          <el-form-item label="API Key" class="model-wide" :required="keyRequired">
            <el-input data-testid="model-profile-key" v-model="apiKey" type="password" autocomplete="new-password" maxlength="2048" :disabled="clearKey"
              :placeholder="canReuseKey ? '已保存，留空沿用' : keyRequired ? '输入此服务的 API Key' : '需要鉴权时填写'" />
            <el-checkbox v-if="canReuseKey && !apiKey.trim() && !keyRequired" data-testid="model-profile-clear-key" v-model="clearKey">清除已保存的 API Key</el-checkbox>
            <p v-if="original?.has_key && connectionChanged" class="model-help">保存新服务地址或接口时会清除旧密钥。如需鉴权，请填写新服务的 API Key。</p>
            <p v-else class="model-help">API Key 加密保存在这台电脑。</p>
          </el-form-item>
        </el-form>
        <el-alert v-if="error" data-testid="model-profile-result" :title="error" type="error" :closable="false" show-icon class="model-feedback" />
        <el-alert v-else-if="status" data-testid="model-profile-result" :title="status" type="success" :closable="false" show-icon class="model-feedback" />
        <div class="model-editor-actions">
          <el-button v-if="original" data-testid="model-profile-delete" :icon="Delete" link type="danger" :disabled="!!busy" @click="confirmDelete">删除配置</el-button>
          <div>
            <el-button data-testid="model-profile-test" :icon="Connection" :loading="busy === 'test'" :disabled="!!busy || !validDraft"
              title="向服务发送测试文字" @click="testConnection">测试连接</el-button>
            <el-button data-testid="model-profile-save" :icon="Check" type="primary" :loading="busy === 'save'" :disabled="!!busy || !validDraft"
              @click="updateProfile('save')">保存</el-button>
          </div>
        </div>
      </section>
    </div>
    <template #footer>
      <div class="model-settings-footer">
        <span>默认模型会在生成元数据候选时优先选中。</span>
        <el-button data-testid="model-settings-close" :disabled="!canClose" @click="visibility(false)">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.model-settings-dialog) { display: flex; flex-direction: column; max-height: 90dvh; }
:global(.model-settings-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.model-settings-layout { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(0, 2.22fr); gap: 22px; }
.model-profiles { min-width: 0; padding: 12px; border: 1px solid #e5ebe1; border-radius: 8px; background: #f7f9f5; }
.model-list-header, .model-editor-heading, .model-editor-actions, .model-settings-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.model-list-header { padding: 3px 2px 10px; }
.model-list-header strong, .model-editor-heading h3 { color: #3c5741; font-size: 14px; font-weight: 600; margin: 0; }
.model-profile-list { display: grid; align-content: start; gap: 6px; margin: 2px 0 0; padding: 0; list-style: none; max-height: 46dvh; overflow: auto; }
.model-profile-list li { min-width: 0; }
.model-profile-option { display: grid; gap: 4px; width: 100%; padding: 10px; border: 1px solid #e9eee6; border-radius: 7px; color: #3c5741; background: #fff; text-align: left; cursor: pointer; }
.model-profile-option:disabled { cursor: default; }
.model-profile-option.is-active { border-color: #b5c9b0; background: #eaf1e7; }
.model-profile-line { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.model-profile-line strong, .model-profile-target { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.model-profile-target { color: #718469; font-size: 11px; }
.model-empty { margin: 9px 2px; color: #73856b; font-size: 12px; }
.model-editor { min-width: 0; }
.model-editor-heading { min-height: 27px; margin: 2px 0 13px; }
.model-presets { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 12px; }
.model-presets .el-button { margin: 0; }
.model-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px 14px; }
.model-form .el-select { width: 100%; }
.model-form :deep(.el-form-item) { margin-bottom: 13px; }
.model-form :deep(.el-form-item__label) { margin-bottom: 6px; line-height: 1.45; }
.model-wide { grid-column: 1 / -1; }
.model-help { width: 100%; margin: 6px 0 0; color: #718469; font-size: 11px; line-height: 1.55; overflow-wrap: anywhere; }
.model-feedback { margin: 0 0 10px; overflow-wrap: anywhere; }
.model-editor-actions { flex-wrap: wrap; }
.model-editor-actions > div { margin-left: auto; }
.model-settings-footer { color: #718469; font-size: 11px; text-align: left; }
@media (max-width: 670px) {
  .model-settings-layout { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .model-profile-list { max-height: 20dvh; }
}
@media (max-width: 460px) {
  .model-form { grid-template-columns: minmax(0, 1fr); }
}
</style>
