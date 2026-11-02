'use strict'
const { spawn } = require('node:child_process')
const path = require('node:path')
const environment = { ...process.env }
delete environment.ELECTRON_RUN_AS_NODE
const child = spawn(require('electron'), [path.join(__dirname, '..', 'electron', 'smoke.cjs')], {
  cwd: path.resolve(__dirname, '..'), env: environment, stdio: 'inherit', windowsHide: true,
})
child.on('error', error => { console.error(error); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
