'use strict'
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const { mkdirSync } = require('node:fs')
const { pythonCommand } = require('../electron/bridge.cjs')
const root = path.resolve(__dirname, '..')
const temporary = path.join(root, 'build', 'test-temp')
mkdirSync(temporary, { recursive: true })
const environment = { ...process.env, PYTHONPATH: path.join(root, 'src'), PYTHONUTF8: '1', TEMP: temporary, TMP: temporary }
for (const suite of ['test_core', 'test_service', 'test_filename_parser', 'test_import']) {
  const result = spawnSync(pythonCommand(root), ['-m', 'unittest', `tests.${suite}`, '-v'], { cwd: root, env: environment, stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status || 1)
}
