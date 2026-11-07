'use strict'
const { spawn } = require('node:child_process')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js')], { cwd: root, stdio: 'inherit' })
let desktop
let quitting = false
function stop() { if (quitting) return; quitting = true; vite.kill(); desktop?.kill() }
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
vite.on('exit', () => { if (!quitting) { stop(); process.exitCode = 1 } })
;(async () => {
  for (let attempt = 0; attempt < 100 && !quitting; attempt++) {
    try {
      const response = await fetch('http://127.0.0.1:5173')
      if (response.ok) break
    } catch { /* Vite is starting */ }
    if (attempt === 99) throw new Error('开发界面未能启动。')
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  if (quitting) return
  const environment = { ...process.env, COMIC_MANAGE_DEV_URL: 'http://127.0.0.1:5173' }
  delete environment.ELECTRON_RUN_AS_NODE
  desktop = spawn(require('electron'), ['.', ...process.argv.slice(2)], { cwd: root, stdio: 'inherit', env: environment })
  desktop.on('exit', code => { stop(); process.exitCode = code || 0 })
})().catch(error => { console.error(error); stop(); process.exitCode = 1 })
