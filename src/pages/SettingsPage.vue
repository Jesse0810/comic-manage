<template>
  <el-form v-if="settings" label-width="180px">
    <el-form-item label="默认漫画目录"><el-input v-model="settings.defaultLibraryDir" /></el-form-item>
    <el-form-item label="请求间隔(ms)"><el-input-number v-model="settings.requestIntervalMs" :min="200" /></el-form-item>
    <el-form-item label="User-Agent"><el-input v-model="settings.userAgent" /></el-form-item>
    <el-form-item label="保存封面"><el-switch v-model="settings.saveCover" /></el-form-item>
    <el-form-item label="数据库路径"><el-input v-model="settings.dbPath" /></el-form-item>
    <el-button type="primary" @click="save">保存</el-button>
  </el-form>
</template>
<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { useLibraryStore } from '../stores/library';
const store = useLibraryStore();
const settings = computed(() => store.settings);
onMounted(() => store.loadSettings());
const save = async () => {
  if (!settings.value) return;
  await store.saveSettings(settings.value);
  ElMessage.success('保存成功');
};
</script>
