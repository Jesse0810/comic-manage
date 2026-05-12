<template>
  <el-table :data="store.comics">
    <el-table-column prop="id" label="ID" width="80" />
    <el-table-column prop="title" label="Title" />
    <el-table-column prop="localPath" label="Local Path" />
    <el-table-column prop="status" label="Status" width="120" />
  </el-table>
</template>
<script setup lang="ts">
import { onMounted } from 'vue';
import { useLibraryStore } from '../stores/library';
const store = useLibraryStore();
onMounted(() => store.loadComics());
</script>
