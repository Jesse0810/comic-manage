import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  root: 'frontend',
  base: './',
  plugins: [vue()],
  build: { outDir: '../renderer-dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
})
