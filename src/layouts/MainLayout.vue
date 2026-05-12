<template>
  <el-container style="height: 100vh">
    <el-aside width="220px">
      <el-menu router :default-active="$route.path">
        <el-menu-item index="/">Dashboard</el-menu-item>
        <el-menu-item index="/library">Library</el-menu-item>
        <el-menu-item index="/scan">Scan</el-menu-item>
        <el-menu-item index="/match">Match</el-menu-item>
        <el-menu-item index="/settings">Settings</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header>Comic Metadata Manager (MVP)</el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>
