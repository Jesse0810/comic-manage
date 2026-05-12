<template>
  <el-row :gutter="16">
    <el-col :span="8"><el-card>总数: {{ store.comics.length }}</el-card></el-col>
    <el-col :span="8"><el-card>待匹配: {{ store.pendingCount }}</el-card></el-col>
    <el-col :span="8"><el-card>已匹配: {{ store.matchedCount }}</el-card></el-col>
  </el-row>
</template>
<script setup lang="ts">
import { onMounted } from 'vue';
import { useLibraryStore } from '../stores/library';
const store = useLibraryStore();
onMounted(() => store.loadComics());
</script>
