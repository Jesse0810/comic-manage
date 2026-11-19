<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Check, Connection } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { invoke } from '../api'
import { errorMessage } from '../types'
import type { SiteConfiguration } from '../types'

const providers = [
  { id: 'dmm', name: 'DMM/FANZA', keyLabel: 'API ID', maximum: 512 },
  { id: 'nhentai', name: 'nhentai', keyLabel: 'API Key', maximum: 512 },
] as const
type Provider = typeof providers[number]['id']
type Task = 'load' | `${Provider}-save` | `${Provider}-clear`

const props = defineProps<{ modelValue: boolean; disabled?: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  updated: [value: SiteConfiguration]
}>()

const configuration = ref<SiteConfiguration | null>(null)
const apiKeys = reactive<Record<Provider, string>>({ dmm: '', nhentai: '' })
const affiliateId = ref('')
const task = ref<Task | ''>('')
const feedback = ref('')
const problem = ref('')
const busy = computed(() => !!task.value || props.disabled)
const canClose = computed(() => !task.value || task.value === 'load')
let session = 0

function clearInputs(provider: Provider) {
  apiKeys[provider] = ''
  if (provider === 'dmm') affiliateId.value = ''
}

function accept(result: SiteConfiguration) {
  configuration.value = result
  emit('updated', result)
}

function visibility(value: boolean) {
  if (value || canClose.value) emit('update:modelValue', value)
}

function canSave(provider: Provider) {
  return !busy.value && !!apiKeys[provider].trim() && (provider !== 'dmm' || !!affiliateId.value.trim())
}

async function load() {
  const current = session
  task.value = 'load'
  problem.value = ''
  try {
    const result = await invoke<SiteConfiguration>('site_config_get')
    if (session === current) accept(result)
  } catch (reason) {
    if (session === current) problem.value = errorMessage(reason)
  } finally {
    if (session === current) task.value = ''
  }
}

async function update(provider: Provider, action: 'save' | 'clear') {
  const current = session
  task.value = `${provider}-${action}`
  problem.value = ''
  feedback.value = ''
  try {
    const result = action === 'save'
      ? await invoke<SiteConfiguration>('site_config_save', {
        provider,
        apiKey: apiKeys[provider].trim(),
        ...(provider === 'dmm' ? { affiliateId: affiliateId.value.trim() } : {}),
      })
      : await invoke<SiteConfiguration>('site_config_clear', { provider })
    if (session === current) {
      accept(result)
      clearInputs(provider)
      feedback.value = action === 'save' ? '凭据已加密保存。' : '已清除保存的凭据。'
    }
  } catch (reason) {
    if (session === current) problem.value = errorMessage(reason)
  } finally {
    if (session === current) task.value = ''
  }
}

async function confirmClear(provider: typeof providers[number]) {
  if (busy.value) return
  const current = session
  try {
    await ElMessageBox.confirm(
      '再次使用这组凭据时，需要重新填写。',
      `清除 ${provider.name} 凭据？`,
      { type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (current === session && !busy.value) await update(provider.id, 'clear')
}

watch(() => props.modelValue, visible => {
  session++
  if (visible) {
    void load()
  } else {
    configuration.value = null
    task.value = ''
    feedback.value = ''
    problem.value = ''
    for (const provider of providers) clearInputs(provider.id)
  }
}, { immediate: true, flush: 'sync' })
</script>

<template>
  <el-dialog class="site-settings-dialog" title="站点凭据设置" width="min(660px, 94vw)" top="8vh"
    :model-value="modelValue" :close-on-click-modal="false" :close-on-press-escape="canClose" :show-close="canClose"
    @update:model-value="visibility" append-to-body destroy-on-close>
    <div aria-label="站点凭据" :aria-busy="!!task" data-testid="site-settings-dialog">
      <p class="credentials-intro">凭据加密保存在这台电脑，Agent 搜索和读取详情时自动使用。</p>

      <form v-for="provider in providers" :key="provider.id" class="credentials-provider"
        :aria-labelledby="'site-settings-heading-' + provider.id" @submit.prevent="canSave(provider.id) && update(provider.id, 'save')">
        <div class="credentials-heading">
          <h3 :id="'site-settings-heading-' + provider.id">{{ provider.name }}</h3>
          <el-tag v-if="configuration" size="small" effect="plain"
            :type="configuration[provider.id].configured ? 'success' : 'info'" :data-testid="'site-settings-' + provider.id + '-status'">
            {{ configuration[provider.id].configured ? '已保存' : '未配置' }}
          </el-tag>
        </div>
        <p v-if="provider.id === 'dmm'" class="credentials-help">请使用官方 API 的两项凭据。更换时需重新填写两项。</p>
        <p v-else class="credentials-help">可选。未配置时继续使用公开搜索。</p>
        <div class="credentials-fields" :class="{ 'credentials-pair': provider.id === 'dmm' }">
          <div class="credentials-field">
            <label :for="'site-settings-' + provider.id + '-input'">{{ provider.keyLabel }}</label>
            <el-input :id="'site-settings-' + provider.id + '-input'" v-model="apiKeys[provider.id]"
              type="password" autocomplete="new-password" :maxlength="provider.maximum" :disabled="busy"
              :data-testid="'site-settings-' + provider.id + '-key'"
              :placeholder="configuration?.[provider.id].configured ? '输入新凭据' : undefined" />
          </div>
          <div v-if="provider.id === 'dmm'" class="credentials-field">
            <label for="site-settings-affiliate-input">Affiliate ID</label>
            <el-input id="site-settings-affiliate-input" v-model="affiliateId" type="password"
              autocomplete="new-password" maxlength="244" :disabled="busy" data-testid="site-settings-dmm-affiliate"
              :placeholder="configuration?.dmm.configured ? '输入新凭据' : undefined" />
          </div>
        </div>
        <div class="credentials-actions">
          <el-button v-if="configuration?.[provider.id].configured" type="danger" link :disabled="busy"
            :loading="task === provider.id + '-clear'" :data-testid="'site-settings-' + provider.id + '-clear'"
            @click="confirmClear(provider)">清除凭据</el-button>
          <el-button type="primary" native-type="submit" :icon="Check" :disabled="!canSave(provider.id)"
            :loading="task === provider.id + '-save'" :data-testid="'site-settings-' + provider.id + '-save'">保存</el-button>
        </div>
      </form>

      <el-alert v-if="problem" :title="problem" type="error" :closable="false" show-icon
        class="credentials-feedback" data-testid="site-settings-feedback" />
      <el-alert v-else-if="feedback" :title="feedback" type="success" :closable="false" show-icon
        class="credentials-feedback" data-testid="site-settings-feedback" />
    </div>
    <template #footer>
      <div class="credentials-footer">
        <el-button v-if="problem && !configuration" :icon="Connection" :disabled="busy" @click="load">重新读取</el-button>
        <el-button :disabled="!canClose" data-testid="site-settings-close" @click="visibility(false)">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.site-settings-dialog) { display: flex; flex-direction: column; max-height: 84dvh; }
:global(.site-settings-dialog .el-dialog__body) { min-height: 0; overflow: auto; }
.credentials-intro { margin: 0 0 16px; color: #617361; line-height: 1.5; }
.credentials-provider { padding: 16px; border: 1px solid #e4ebe0; border-radius: 8px; background: #f7f9f5; }
.credentials-provider + .credentials-provider { margin-top: 14px; }
.credentials-heading { display: flex; align-items: center; gap: 10px; }
.credentials-heading h3 { margin: 0; color: #3c5741; font-size: 15px; }
.credentials-help { margin: 6px 0 14px; color: #617361; font-size: 12px; line-height: 1.5; }
.credentials-fields { display: grid; gap: 10px 14px; }
.credentials-fields.credentials-pair { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.credentials-field { display: grid; gap: 5px; min-width: 0; }
.credentials-field label { color: #426047; font-size: 12px; }
.credentials-actions, .credentials-footer { display: flex; justify-content: flex-end; gap: 10px; }
.credentials-actions { margin-top: 12px; }
.credentials-actions .el-button, .credentials-footer .el-button { margin: 0; }
.credentials-feedback { margin-top: 12px; overflow-wrap: anywhere; }
@media (max-width: 480px) {
  .credentials-fields.credentials-pair { grid-template-columns: minmax(0, 1fr); }
}
</style>

