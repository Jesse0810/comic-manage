<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check } from '@element-plus/icons-vue'
import type { CollectionMode } from '../types'
import { errorMessage } from '../types'
import BulkMetadataFields from './BulkMetadataFields.vue'
import { invoke } from '../api'

const props = defineProps<{ modelValue: boolean; ids: string[]; authors: string[]; modes?: CollectionMode[]; ratings?: string[]; hasRemote?: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', count: number): void
}>()

const editor = ref<InstanceType<typeof BulkMetadataFields>>()
const busy = ref(false)
const error = ref('')
const selectedCount = computed(() => Object.keys(editor.value?.changes || {}).length)
watch(() => props.modelValue, visible => {
  if (!visible) return
  error.value = ''
  editor.value?.reset()
})

async function save() {
  if (busy.value || !props.ids.length || !selectedCount.value) return
  error.value = ''
  const changes = editor.value?.validatedChanges()
  if (!changes) return
  busy.value = true
  emit('state', { busy: true, title: '批量保存作品资料' })
  try {
    const result = await invoke<{ count: number }>('batch_edit', { ids: [...props.ids], changes })
    emit('completed', result.count)
    emit('update:modelValue', false)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false; emit('state', { busy: false, title: '' }) }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="批量编辑资料" width="min(820px, 94vw)" top="5vh" class="batch-edit-dialog" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" destroy-on-close>
    <div class="bulk-intro">
      <p>为选中的 <strong>{{ ids.length }} 本作品</strong>统一修改资料。每项单独勾选，只保存勾选的字段。</p>
      <span>勾选可选字段后留空，即清空该字段。多值内容会替换原列表。</span>
    </div>
    <BulkMetadataFields ref="editor" mode="batch" :authors="authors" :modes="modes" :ratings="ratings" :has-remote="hasRemote" :busy="busy" />
    <div class="bulk-status">
      <p>资料发生变化的作品将回到待审核状态，已有成品记录保留。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="batch-edit-error" />
    </div>
    <template #footer>
      <div class="bulk-footer"><span>作品名称、页数与封面保持原样</span><div><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :icon="Check" :loading="busy" :disabled="!selectedCount || !ids.length" @click="save">保存 {{ ids.length }} 本作品</el-button></div></div>
    </template>
  </el-dialog>
</template>

<style scoped>
.bulk-intro { color: #7d9072; font-size: 12px; line-height: 1.8; margin-bottom: 14px; }
.bulk-intro p { margin: 0 0 3px; }
.bulk-intro strong { color: #527348; font-weight: 600; }
.bulk-intro > span { font-size: 11px; }
.bulk-status { margin-top: 12px; }
.bulk-status > p { font-size: 11px; color: #8c9b80; margin: 0; line-height: 1.7; }
.bulk-status .batch-edit-error { margin-top: 8px; max-height: 82px; overflow: auto; }
.bulk-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.bulk-footer > span { font-size: 11px; color: #96a48b; text-align: left; }
.bulk-footer > div { flex-shrink: 0; }
@media (max-width: 620px) {
  .bulk-footer > span { display: none; }
  .bulk-footer { justify-content: flex-end; }
}
</style>
