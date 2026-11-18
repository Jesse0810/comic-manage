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

type ChatGPTStatus = { connected: boolean; sharing: boolean; name: string; email: string; revocationConfirmed?: boolean }
type ChatGPTModel = { slug: string; name: string }
type ChatGPTAction = 'status' | 'signin' | 'disconnect'
const chatgptEndpoint = 'https://api.openai.com/v1'
const disconnected = (): ChatGPTStatus => ({ connected: false, sharing: false, name: '', email: '' })
const welcomePreference = 'comic-manage-seen-chatgpt-plan'
// Approved mark from developers.openai.com/assets/siwc/chatgpt-logo-white.svg.
const chatgptLogo = "data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjEiIGhlaWdodD0iMjEiIHZpZXdCb3g9IjAgMCAyMSAyMSIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTExLjgxMDIgMTkuMTYyNUMxMS4yMzI3IDE5LjE2MjUgMTAuNjg0MSAxOS4wNTI4IDEwLjE2NDMgMTguODMzNEM5LjY0NDU5IDE4LjYxMzkgOS4xODI1OSAxOC4zMDc4IDguNzc4MzQgMTcuOTE1MUM4LjMzOTQ0IDE4LjA2NTMgNy44ODMyMiAxOC4xNDA0IDcuNDA5NjcgMTguMTQwNEM2LjYzNTgyIDE4LjE0MDQgNS45MTk3MiAxNy45NDk4IDUuMjYxMzcgMTcuNTY4NkM0LjYwMzAyIDE3LjE4NzUgNC4wNzE3MiAxNi42Njc3IDMuNjY3NDcgMTYuMDA5NEMzLjI3NDc3IDE1LjM1MSAzLjA3ODQyIDE0LjYxNzYgMy4wNzg0MiAxMy44MDkxQzMuMDc4NDIgMTMuNDc0MiAzLjEyNDYyIDEzLjExMDMgMy4yMTcwMiAxMi43MTc2QzIuNzU1MDIgMTIuMjkwMyAyLjM5Njk3IDExLjc5OTQgMi4xNDI4NyAxMS4yNDVDMS44ODg3NyAxMC42NzkgMS43NjE3MiAxMC4wOSAxLjc2MTcyIDkuNDc3ODVDMS43NjE3MiA4Ljg1NDE1IDEuODk0NTQgOC4yNTM1NSAyLjE2MDE5IDcuNjc2MDVDMi40MjU4NCA3LjA5ODU1IDIuNzk1NDQgNi42MDE5IDMuMjY4OTkgNi4xODYxQzMuNzU0MDkgNS43NTg3NSA0LjMxNDI3IDUuNDY0MjIgNC45NDk1MiA1LjMwMjUyQzUuMDc2NTcgNC42NDQxNyA1LjM0MjIyIDQuMDU1MTIgNS43NDY0NyAzLjUzNTM3QzYuMTYyMjcgMy4wMDQwNyA2LjY3MDQ3IDIuNTg4MjcgNy4yNzEwNyAyLjI4Nzk3QzcuODcxNjcgMS45ODc2NyA4LjUxMjY5IDEuODM3NTIgOS4xOTQxNCAxLjgzNzUyQzkuNzcxNjQgMS44Mzc1MiAxMC4zMjAzIDEuOTQ3MjUgMTAuODQgMi4xNjY3QzExLjM1OTggMi4zODYxNSAxMS44MjE4IDIuNjkyMjIgMTIuMjI2IDMuMDg0OTJDMTIuNjY0OSAyLjkzNDc3IDEzLjEyMTEgMi44NTk3IDEzLjU5NDcgMi44NTk3QzE0LjM2ODUgMi44NTk3IDE1LjA4NDYgMy4wNTAyNyAxNS43NDMgMy40MzE0MkMxNi40MDEzIDMuODEyNTcgMTYuOTI2OSA0LjMzMjMyIDE3LjMxOTYgNC45OTA2N0MxNy43MjM4IDUuNjQ5MDIgMTcuOTI1OSA2LjM4MjQ1IDE3LjkyNTkgNy4xOTA5NUMxNy45MjU5IDcuNTI1OSAxNy44Nzk3IDcuODg5NzMgMTcuNzg3MyA4LjI4MjQyQzE4LjI0OTMgOC43MDk3NyAxOC42MDc0IDkuMjA2NDMgMTguODYxNSA5Ljc3MjM4QzE5LjExNTYgMTAuMzI2OCAxOS4yNDI2IDEwLjkxIDE5LjI0MjYgMTEuNTIyMkMxOS4yNDI2IDEyLjE0NTkgMTkuMTA5OCAxMi43NDY1IDE4Ljg0NDIgMTMuMzI0QzE4LjU3ODUgMTMuOTAxNSAxOC4yMDMxIDE0LjQwMzkgMTcuNzE4IDE0LjgzMTNDMTcuMjQ0NSAxNS4yNDcxIDE2LjY5MDEgMTUuNTM1OCAxNi4wNTQ4IDE1LjY5NzVDMTUuOTI3OCAxNi4zNTU5IDE1LjY1NjQgMTYuOTQ0OSAxNS4yNDA2IDE3LjQ2NDdDMTQuODM2MyAxNy45OTYgMTQuMzMzOSAxOC40MTE4IDEzLjczMzMgMTguNzEyMUMxMy4xMzI3IDE5LjAxMjQgMTIuNDkxNyAxOS4xNjI1IDExLjgxMDIgMTkuMTYyNVpNNy41MzA5NCAxNi45OTY5QzguMTA4NDQgMTYuOTk2OSA4LjYxMDg3IDE2Ljg3NTYgOS4wMzgyMiAxNi42MzMxTDEyLjI5NTMgMTQuNzYyQzEyLjQxMDggMTQuNjgxMSAxMi40Njg2IDE0LjU3MTQgMTIuNDY4NiAxNC40MzI4VjEyLjk0MjlMOC4yNzU5MiAxNS4zNTFDOC4wMjE4MiAxNS41MDEyIDcuNzY3NzIgMTUuNTAxMiA3LjUxMzYyIDE1LjM1MUw0LjIzOTE5IDEzLjQ2MjZDNC4yMzkxOSAxMy40OTczIDQuMjMzNDIgMTMuNTM3NyA0LjIyMTg3IDEzLjU4MzlDNC4yMjE4NyAxMy42MzAxIDQuMjIxODcgMTMuNjk5NCA0LjIyMTg3IDEzLjc5MThDNC4yMjE4NyAxNC4zODA4IDQuMzYwNDcgMTQuOTIzNyA0LjYzNzY3IDE1LjQyMDNDNC45MjY0MiAxNS45MDU0IDUuMzI0ODkgMTYuMjg2NiA1LjgzMzA5IDE2LjU2MzhDNi4zNDEyOSAxNi44NTI1IDYuOTA3MjQgMTYuOTk2OSA3LjUzMDk0IDE2Ljk5NjlaTTcuNzA0MTkgMTQuMTcyOUM3Ljc3MzQ5IDE0LjIwNzYgNy44MzcwMiAxNC4yMjQ5IDcuODk0NzcgMTQuMjI0OUM3Ljk1MjUyIDE0LjIyNDkgOC4wMTAyNyAxNC4yMDc2IDguMDY4MDIgMTQuMTcyOUw5LjM2NzM5IDEzLjQyNzlMNS4xOTIwNyAxMS4wMDI0QzQuOTM3OTcgMTAuODUyMyA0LjgxMDkyIDEwLjYyNzEgNC44MTA5MiAxMC4zMjY4VjYuNTY3MjVDNC4yMzM0MiA2LjgyMTM1IDMuNzcxNDIgNy4yMTQwNSAzLjQyNDkyIDcuNzQ1MzVDMy4wNzg0MiA4LjI2NTEgMi45MDUxNyA4Ljg0MjYgMi45MDUxNyA5LjQ3Nzg1QzIuOTA1MTcgMTAuMDQzOCAzLjA0OTU0IDEwLjU4NjYgMy4zMzgyOSAxMS4xMDY0QzMuNjI3MDQgMTEuNjI2MSA0LjAwMjQyIDEyLjAxODkgNC40NjQ0MiAxMi4yODQ1TDcuNzA0MTkgMTQuMTcyOVpNMTEuODEwMiAxOC4wMTkxQzEyLjQyMjQgMTguMDE5MSAxMi45NzY4IDE3Ljg4MDUgMTMuNDczNCAxNy42MDMzQzEzLjk3MDEgMTcuMzI2MSAxNC4zNjI4IDE2Ljk0NDkgMTQuNjUxNSAxNi40NTk4QzE0Ljk0MDMgMTUuOTc0NyAxNS4wODQ2IDE1LjQzMTkgMTUuMDg0NiAxNC44MzEzVjExLjA4OTFDMTUuMDg0NiAxMC45NTA1IDE1LjAyNjkgMTAuODQ2NSAxNC45MTE0IDEwLjc3NzJMMTMuNTk0NyAxMC4wMTQ5VjE0Ljg0ODZDMTMuNTk0NyAxNS4xNDg5IDEzLjQ2NzYgMTUuMzc0MSAxMy4yMTM1IDE1LjUyNDNMOS45MzkxMiAxNy40MTI3QzEwLjUwNTEgMTcuODE3IDExLjEyODggMTguMDE5MSAxMS44MTAyIDE4LjAxOTFaTTEyLjQ2ODYgMTEuNjc4MVY5LjMyMTkyTDEwLjUxMDggOC4yMTMxMkw4LjUzNTc5IDkuMzIxOTJWMTEuNjc4MUwxMC41MTA4IDEyLjc4NjlMMTIuNDY4NiAxMS42NzgxWk03LjQwOTY3IDYuMTUxNDVDNy40MDk2NyA1Ljg1MTE1IDcuNTM2NzIgNS42MjU5MiA3Ljc5MDgyIDUuNDc1NzdMMTEuMDY1MiAzLjU4NzM1QzEwLjQ5OTMgMy4xODMxIDkuODc1NTkgMi45ODA5NyA5LjE5NDE0IDIuOTgwOTdDOC41ODE5OSAyLjk4MDk3IDguMDI3NTkgMy4xMTk1NyA3LjUzMDk0IDMuMzk2NzdDNy4wMzQyOSAzLjY3Mzk3IDYuNjQxNTkgNC4wNTUxMiA2LjM1Mjg0IDQuNTQwMjJDNi4wNzU2NCA1LjAyNTMyIDUuOTM3MDQgNS41NjgxNyA1LjkzNzA0IDYuMTY4NzhWOS44OTM2NUM1LjkzNzA0IDEwLjAzMjMgNS45OTQ3OSAxMC4xNDIgNi4xMTAyOSAxMC4yMjI4TDcuNDA5NjcgMTAuOTg1MVY2LjE1MTQ1Wk0xNi4yMTA4IDE0LjQzMjhDMTYuNzg4MyAxNC4xNzg3IDE3LjI0NDUgMTMuNzg2IDE3LjU3OTQgMTMuMjU0N0MxNy45MjU5IDEyLjcyMzQgMTguMDk5MiAxMi4xNDU5IDE4LjA5OTIgMTEuNTIyMkMxOC4wOTkyIDEwLjk1NjIgMTcuOTU0OCAxMC40MTM0IDE3LjY2NjEgOS44OTM2NUMxNy4zNzczIDkuMzczOSAxNy4wMDE5IDguOTgxMiAxNi41Mzk5IDguNzE1NTVMMTMuMzAwMiA2Ljg0NDQ1QzEzLjIzMDkgNi43OTgyNSAxMy4xNjczIDYuNzgwOTIgMTMuMTA5NiA2Ljc5MjQ4QzEzLjA1MTggNi43OTI0OCAxMi45OTQxIDYuODA5OCAxMi45MzYzIDYuODQ0NDVMMTEuNjM3IDcuNTcyMUwxNS44Mjk2IDEwLjAxNDlDMTUuOTU2NyAxMC4wODQyIDE2LjA0OTEgMTAuMTc2NiAxNi4xMDY4IDEwLjI5MjFDMTYuMTc2MSAxMC4zOTYxIDE2LjIxMDggMTAuNTIzMSAxNi4yMTA4IDEwLjY3MzNWMTQuNDMyOFpNMTIuNzI4NCA1LjYzMTdDMTIuOTgyNSA1LjQ3IDEzLjIzNjYgNS40NyAxMy40OTA3IDUuNjMxN0wxNi43ODI1IDcuNTU0NzdDMTYuNzgyNSA3LjQ3MzkzIDE2Ljc4MjUgNy4zNjk5NyAxNi43ODI1IDcuMjQyOTJDMTYuNzgyNSA2LjY4ODUyIDE2LjY0MzkgNi4xNjMgMTYuMzY2NyA1LjY2NjM1QzE2LjEwMSA1LjE1ODE1IDE1LjcxNDEgNC43NTM5IDE1LjIwNTkgNC40NTM2QzE0LjcwOTMgNC4xNTMzIDE0LjEzMTggNC4wMDMxNSAxMy40NzM0IDQuMDAzMTVDMTIuODk1OSA0LjAwMzE1IDEyLjM5MzUgNC4xMjQ0MiAxMS45NjYxIDQuMzY2OTdMOC43MDkwNCA2LjIzODA4QzguNTkzNTQgNi4zMTg5MiA4LjUzNTc5IDYuNDI4NjUgOC41MzU3OSA2LjU2NzI1VjguMDU3MkwxMi43Mjg0IDUuNjMxN1oiIGZpbGw9IndoaXRlIi8+Cjwvc3ZnPg=="
const props = defineProps<{ modelValue: boolean; configuration: ModelConfiguration }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'updated', value: ModelConfiguration): void
}>()
const protocols: Record<ModelProtocol, { label: string; endpoint: string; authHost?: string }> = {
  openai: { label: 'OpenAI 兼容 · Chat Completions', endpoint: 'https://api.openai.com/v1', authHost: 'api.openai.com' },
  'chatgpt-plan': { label: 'ChatGPT · 账号授权', endpoint: chatgptEndpoint },
  'openai-responses': { label: 'OpenAI · Responses', endpoint: 'https://api.openai.com/v1', authHost: 'api.openai.com' },
  ollama: { label: 'Ollama · 本地接口', endpoint: 'http://127.0.0.1:11434' },
  anthropic: { label: 'Anthropic · Messages', endpoint: 'https://api.anthropic.com/v1', authHost: 'api.anthropic.com' },
  gemini: { label: 'Google · Gemini', endpoint: 'https://generativelanguage.googleapis.com/v1beta', authHost: 'generativelanguage.googleapis.com' },
}
const presets: { label: string; provider: ModelProtocol; endpoint?: string }[] = [
  { label: 'ChatGPT', provider: 'chatgpt-plan' },
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

const account = ref<ChatGPTStatus>(disconnected())
const availableModels = ref<ChatGPTModel[]>([])
const accountAction = ref<ChatGPTAction | ''>('')
const accountError = ref('')
const remoteRevocationUnknown = ref(false)
const usingChatGPT = computed(() => form.value.provider === 'chatgpt-plan')
let accountEpoch = 0
let welcomeShown = false


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
const validDraft = computed(() => !!form.value.name.trim() && !!form.value.model.trim() && (usingChatGPT.value
  ? account.value.sharing && !accountAction.value && availableModels.value.some(model => model.slug === form.value.model)
  : !!parseEndpoint() && (!keyRequired.value || hasKey.value)))
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
  if (provider === 'chatgpt-plan' || usingChatGPT.value) form.value.model = ''
  form.value.provider = provider
  form.value.base_url = protocols[provider].endpoint
}
function usePreset(preset: typeof presets[number]) {
  const model = preset.provider === form.value.provider ? form.value.model : ''
  form.value = { ...form.value, name: preset.label, provider: preset.provider, model, base_url: preset.endpoint || protocols[preset.provider].endpoint }
}
function profileInput() {
  return {
    profile: {
      ...(form.value.id ? { id: form.value.id } : {}),
      name: form.value.name.trim(),
      provider: form.value.provider,
      base_url: usingChatGPT.value ? chatgptEndpoint : normalizeEndpoint(form.value.base_url),
      model: form.value.model.trim(),
    },
    ...(!usingChatGPT.value && apiKey.value.trim() ? { api_key: apiKey.value.trim() } :
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


function showPlanWelcome() {
  if (welcomeShown) return
  welcomeShown = true
  try {
    if (localStorage.getItem(welcomePreference)) return
    localStorage.setItem(welcomePreference, '1')
  } catch {
    // The confirmation can still be shown once per window.
  }
  void ElMessageBox.alert('此模型的合资格刮削请求使用你的 ChatGPT 方案或额度。你可在 ChatGPT 设置中查看和管理用量。',
    '正在使用你的 ChatGPT 方案', { confirmButtonText: '知道了', type: 'success' }).catch(() => {})
}
async function accountRequest(action: ChatGPTAction) {
  if (accountAction.value || !props.modelValue || !usingChatGPT.value) return
  const epoch = ++accountEpoch
  accountAction.value = action
  accountError.value = ''
  const stillCurrent = () => epoch === accountEpoch && props.modelValue && usingChatGPT.value
  try {
    const method = { status: 'model_chatgpt_status', signin: 'model_chatgpt_sign_in', disconnect: 'model_chatgpt_disconnect' }[action]
    const result: ChatGPTStatus = await window.comic.invoke(method)
    if (!stillCurrent()) return
    account.value = result
    if (action === 'disconnect') remoteRevocationUnknown.value = result.revocationConfirmed === false
    else if (result.connected) remoteRevocationUnknown.value = false
    availableModels.value = []
    if (!result.connected || !result.sharing) return
    if (action === 'signin') showPlanWelcome()
    const response: { models: ChatGPTModel[] } = await window.comic.invoke('model_chatgpt_models')
    if (!stillCurrent()) return
    availableModels.value = response.models
    if (!response.models.length) accountError.value = '此账号暂未返回可用的模型。请检查方案和授权。'
  } catch (reason) {
    if (stillCurrent()) accountError.value = errorMessage(reason)
  } finally {
    if (stillCurrent()) accountAction.value = ''
  }
}
function stopSignIn() {
  ++accountEpoch
  accountAction.value = ''
  return window.comic.invoke('model_chatgpt_cancel')
}
async function cancelChatGPTSignIn() {
  if (accountAction.value !== 'signin') return
  try { await stopSignIn(); await accountRequest('status') }
  catch (reason) { accountError.value = errorMessage(reason) }
}
async function disconnectChatGPT() {
  if (accountAction.value) return
  try {
    await ElMessageBox.confirm('此电脑将清除已保存的 ChatGPT 授权。以后使用该方案时可重新登录。',
      '解除本机授权？', { type: 'warning', confirmButtonText: '解除授权', cancelButtonText: '取消' })
  } catch { return }
  await accountRequest('disconnect')
}
async function openChatGPTUsage() {
  try { await window.comic.invoke('model_chatgpt_usage') }
  catch (reason) { accountError.value = errorMessage(reason) }
}
watch([() => props.modelValue, usingChatGPT], ([visible, chatgpt]) => {
  if (visible && chatgpt) {
    void accountRequest('status')
    return
  }
  if (accountAction.value === 'signin') void stopSignIn().catch(() => {})
  else { ++accountEpoch; accountAction.value = '' }
  availableModels.value = []
  accountError.value = ''
  account.value = disconnected()
}, { immediate: true })

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
          <template v-if="usingChatGPT">
            <section class="chatgpt-account model-wide" aria-label="ChatGPT 账号" data-testid="model-chatgpt-account">
              <div class="chatgpt-account-heading">
                <strong>{{ account.connected ? (account.name || account.email || 'ChatGPT 账号') : '使用你的 ChatGPT 方案' }}</strong>
                <el-tag v-if="account.connected" :type="account.sharing ? 'success' : 'warning'" effect="plain" size="small">
                  {{ account.sharing ? '方案用量已授权' : '已登录 · 待授权方案用量' }}
                </el-tag>
              </div>
              <p v-if="account.connected && account.name && account.email" class="model-help chatgpt-identity">{{ account.email }}</p>
              <p v-if="!account.connected || !account.sharing" class="model-help">浏览器将打开 OpenAI 授权页。Comic Manage 使用授权的姓名和邮箱显示账号，并在本机加密保存授权。合资格的模型请求使用你允许的方案或额度。</p>
              <p v-else class="model-help">此连接的合资格模型请求使用你的 ChatGPT 方案或额度。</p>
              <div class="chatgpt-account-actions">
                <button v-if="(!account.connected || !account.sharing) && accountAction !== 'signin'" type="button"
                  class="chatgpt-sign-in" data-oai-siwc-button data-testid="model-chatgpt-sign-in"
                  :disabled="!!accountAction || !!busy" @click="accountRequest('signin')">
                  <img :src="chatgptLogo" alt="" width="21" height="21" /> Continue with ChatGPT
                </button>
                <template v-if="accountAction === 'signin'">
                  <span role="status">请在浏览器完成授权。</span>
                  <el-button link data-testid="model-chatgpt-cancel" @click="cancelChatGPTSignIn">取消授权</el-button>
                </template>
                <el-button v-if="account.connected || remoteRevocationUnknown" link data-testid="model-chatgpt-usage" @click="openChatGPTUsage">管理用量</el-button>
                <el-button v-if="account.connected" link data-testid="model-chatgpt-disconnect" :disabled="!!accountAction" @click="disconnectChatGPT">解除本机授权</el-button>
              </div>
              <p v-if="remoteRevocationUnknown" class="model-help" role="status">已清除本机授权。你可以在 ChatGPT 设置中核对账号端的授权状态。</p>
            </section>
            <el-form-item label="ChatGPT 模型" class="model-wide" required>
              <el-select data-testid="model-chatgpt-model" v-model="form.model" filterable
                :disabled="!account.sharing || !!accountAction" placeholder="选择账号可用的模型">
                <el-option v-for="model in availableModels" :key="model.slug" :value="model.slug" :label="model.name" />
              </el-select>
              <el-button v-if="account.sharing" link data-testid="model-chatgpt-refresh" :disabled="!!accountAction" @click="accountRequest('status')">刷新模型</el-button>
              <p v-if="account.sharing && form.model && !availableModels.some(model => model.slug === form.model) && !accountAction" class="model-help">已保存的模型在此账号下不可用，请重新选择。</p>
            </el-form-item>
          </template>
          <template v-else>
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
          </template>
        </el-form>
        <el-alert v-if="usingChatGPT && accountError" :title="accountError" type="error" :closable="false" show-icon class="model-feedback" data-testid="model-chatgpt-error" />
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

.chatgpt-account { margin-bottom: 12px; padding: 11px 13px; border: 1px solid #e4ebe0; border-radius: 7px; background: #f7f9f5; }
.chatgpt-account-heading, .chatgpt-account-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
.chatgpt-account-heading strong { font-size: 13px; color: #3c5741; overflow-wrap: anywhere; }
.chatgpt-identity { color: #426047; }
.chatgpt-account-actions { margin-top: 8px; color: #516c51; font-size: 12px; }
.chatgpt-account-actions .el-button { margin: 0; }
.chatgpt-sign-in { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; padding: 7px 13px; border: 1px solid #171717; border-radius: 7px; background: #171717; color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
.chatgpt-sign-in:hover:not(:disabled) { background: #303030; }
.chatgpt-sign-in:focus-visible { outline: 2px solid #587660; outline-offset: 2px; }
.chatgpt-sign-in:disabled { opacity: .57; cursor: default; }

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
