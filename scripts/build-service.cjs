'use strict'
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const { pythonCommand } = require('../electron/bridge.cjs')
const root = path.resolve(__dirname, '..')
const environment = { ...process.env, PYTHONUTF8: '1' }
const result = spawnSync(pythonCommand(root), [path.join(root, 'scripts', 'build_service.py')], { cwd: root, env: environment, stdio: 'inherit' })
process.exit(result.status ?? 1)
