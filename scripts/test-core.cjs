'use strict'
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const { mkdirSync } = require('node:fs')
const { pythonCommand } = require('../electron/bridge.cjs')
const root = path.resolve(__dirname, '..')
const temporary = path.join(root, 'build', 'test-temp')
mkdirSync(temporary, { recursive: true })
const environment = { ...process.env, PYTHONPATH: path.join(root, 'src'), PYTHONUTF8: '1', TEMP: temporary, TMP: temporary }
for (const suite of ['test_core', 'test_service', 'test_filename_parser', 'test_import', 'test_metadata_rules', 'test_metadata_v12', 'test_komga', 'test_komga_manifest', 'test_komga_sync', 'test_komga_remote', 'test_komga_media', 'test_komga_v12', 'test_batch_review']) {
  const result = spawnSync(pythonCommand(root), ['-m', 'unittest', `tests.${suite}`, '-v'], { cwd: root, env: environment, stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status || 1)
}
