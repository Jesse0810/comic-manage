<template>
  <el-space direction="vertical" fill>
    <el-input v-model="keyword" placeholder="输入标题关键词" />
    <el-button type="primary" @click="search">搜索候选</el-button>
    <el-table :data="store.candidates">
      <el-table-column prop="sourceName" label="Source" width="120" />
      <el-table-column prop="title" label="Title" />
      <el-table-column prop="score" label="Score" width="100" />
    </el-table>
  </el-space>
</template>
<script setup lang="ts">
import { ref } from 'vue';
import { useLibraryStore } from '../stores/library';
const store = useLibraryStore();
const keyword = ref('');
const search = async () => store.search(keyword.value);
</script>
