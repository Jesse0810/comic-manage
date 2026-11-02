<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { comicGenres, errorMessage } from '../types'
import { invoke } from '../api'
const props = defineProps<{ modelValue: boolean; ids: string[]; authors: string[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'state', value: { busy: boolean; title: string }): void
  (e: 'completed', count: number): void
}>()
const editAuthors = ref(false)
const editGenre = ref(false)
const authors = ref<string[]>([])
const genre = ref('')
const busy = ref(false)
const error = ref('')
watch(() => props.modelValue, (visible) => {
  if (visible) { editAuthors.value = false; editGenre.value = false; authors.value = []; genre.value = ''; error.value = '' }
})
async function save() {
  if (busy.value || !props.ids.length) return
  const changes: { authors?: string[]; genre?: string } = {}
  if (editAuthors.value) {
    changes.authors = [...new Set(authors.value.map(name => name.trim()).filter(Boolean))]
    if (!changes.authors.length) { ElMessage.warning('请输入作者，按 Enter 添加'); return }
  }
  if (editGenre.value) {
    changes.genre = genre.value.trim()
    if (!changes.genre) { ElMessage.warning('请选择类型'); return }
  }
  if (!Object.keys(changes).length) return
  busy.value = true
  emit('state', { busy: true, title: '批量保存作者和类型' })
  try {
    const result = await invoke<{ count: number }>('batch_edit', { ids: [...props.ids], changes })
    emit('completed', result.count)
    emit('update:modelValue', false)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false; emit('state', { busy: false, title: '' }) }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)" title="批量编辑资料" width="min(640px, 92vw)" class="batch-edit-dialog" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" destroy-on-close>
    <p class="batch-edit-intro">为选中的 <strong>{{ ids.length }} 本作品</strong>统一填写作者或类型。勾选要修改的字段后填写内容。</p>
    <div class="batch-fields"><div class="batch-field"><el-checkbox data-testid="batch-enable-authors" v-model="editAuthors" :disabled="busy">修改作者</el-checkbox><el-select data-testid="batch-authors" aria-label="批量修改作者" v-model="authors" multiple filterable allow-create default-first-option :reserve-keyword="false" :disabled="busy || !editAuthors" placeholder="输入作者后按 Enter，可添加多位"><el-option v-for="name in props.authors" :key="name" :label="name" :value="name" /></el-select></div><div class="batch-field"><el-checkbox data-testid="batch-enable-genre" v-model="editGenre" :disabled="busy">修改类型</el-checkbox><el-select data-testid="batch-genre" aria-label="批量修改类型" v-model="genre" filterable allow-create :disabled="busy || !editGenre" placeholder="请选择类型"><el-option v-for="item in comicGenres" :key="item" :label="item" :value="item" /></el-select></div></div>
    <el-alert title="保存后，所选作品将回到待审核状态。其他资料与已有成品记录会保留。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="batch-edit-error" />
    <template #footer><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :icon="Check" :loading="busy" :disabled="!editAuthors && !editGenre" @click="save">保存 {{ ids.length }} 本作品</el-button></template>
  </el-dialog>
</template>
