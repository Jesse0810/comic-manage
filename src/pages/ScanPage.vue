<template>
  <el-space direction="vertical" fill>
    <el-input v-model="directory" placeholder="输入漫画目录（Windows路径也可）" />
    <el-button type="primary" @click="scan">扫描目录</el-button>
  </el-space>
</template>
<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useLibraryStore } from '../stores/library';
const store = useLibraryStore();
const directory = ref('');

const scan = async () => {
  await store.scanDirectory(directory.value);
  ElMessage.success('扫描完成');
};
</script>
