'use strict'
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const destination = path.join(root, 'build', 'frontend-licenses')
fs.mkdirSync(destination, { recursive: true })
for (const name of ['vue', '@vue/runtime-core', '@vue/runtime-dom', '@vue/reactivity', '@vue/shared', 'element-plus', '@element-plus/icons-vue', '@popperjs/core', '@ctrl/tinycolor', 'lodash-unified', 'lodash-es', '@vueuse/core', '@vueuse/shared', '@vueuse/metadata']) {
  const directory = path.join(root, 'node_modules', name)
  if (!fs.existsSync(directory)) continue
  for (const file of fs.readdirSync(directory).filter(file => /^(license|copying)/i.test(file))) {
    fs.copyFileSync(path.join(directory, file), path.join(destination, name.replaceAll('/', '-') + '-' + file))
  }
}
