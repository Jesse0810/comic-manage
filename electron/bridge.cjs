'use strict'

const { spawn } = require('node:child_process')
const { EventEmitter } = require('node:events')
const { createInterface } = require('node:readline')
const { existsSync } = require('node:fs')
const path = require('node:path')

const METHODS = Object.freeze(['bootstrap', 'scan', 'import', 'browse_folders', 'prepare_import', 'commit_import', 'remove', 'batch_edit', 'save', 'approve', 'page', 'xml', 'plan', 'export', 'cancel', 'set_output', 'backup'])
const LONG_METHODS = new Set(['scan', 'import', 'prepare_import', 'commit_import', 'plan', 'export'])
const BATCH_TEXT_FIELDS = new Set([
  'genre', 'series', 'localized_series', 'number', 'count', 'volume', 'translator',
  'release_type', 'publication_format', 'year', 'month', 'day', 'language',
  'age_rating', 'black_and_white', 'manga',
])
const BATCH_LIST_FIELDS = new Set(['authors', 'pencillers', 'original_works', 'characters', 'web'])
const BATCH_ENUMS = {
  release_type: ['', '同人', '商业'],
  age_rating: ['Unknown', 'Everyone', 'MA15+', 'R18+'],
  black_and_white: ['Unknown', 'Yes', 'No'],
  manga: ['Unknown', 'Yes', 'No', 'YesAndRightToLeft'],
}

function validBatchText(value, allowEmpty = false) {
  return typeof value === 'string' && value.length <= 32767 && !value.includes('\0')
    && (value === '' ? allowEmpty : value.trim().length > 0)
}

function validateBatchEdit(changes) {
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) throw new Error('批量资料无效。')
  const entries = Object.entries(changes)
  if (!entries.length) throw new Error('请选择要批量修改的资料。')

  for (const [field, value] of entries) {
    if (BATCH_LIST_FIELDS.has(field)) {
      if (!Array.isArray(value) || value.length > 1000 || (field === 'authors' && value.length === 0)
          || value.some(item => !validBatchText(item))) throw new Error(`批量字段 ${field} 的列表无效。`)
    } else if (field === 'uncensored') {
      if (typeof value !== 'boolean') throw new Error('无修正选项无效。')
    } else if (BATCH_TEXT_FIELDS.has(field)) {
      if (!validBatchText(value, field !== 'language')) throw new Error(`批量字段 ${field} 的文本无效。`)
      if (Object.hasOwn(BATCH_ENUMS, field) && !BATCH_ENUMS[field].includes(value.trim())) throw new Error(`批量字段 ${field} 的选项无效。`)
    } else {
      throw new Error('批量字段无效。')
    }
  }
}

function validateCall(method, params) {
  if (!METHODS.includes(method)) throw new Error('不支持的操作。')
  if (params === null || typeof params !== 'object' || Array.isArray(params)) throw new Error('请求参数无效。')
  if (Buffer.byteLength(JSON.stringify(params), 'utf8') > 4 * 1024 * 1024) throw new Error('资料过大，无法提交。')
  const string = (key, required = true) => {
    const value = params[key]
    if ((!required && value === undefined) || (typeof value === 'string' && value.length > 0 && value.length < 32768)) return
    throw new Error(`参数 ${key} 无效。`)
  }
  const strings = (key, required = true) => {
    if (!required && params[key] === undefined) return
    if (!Array.isArray(params[key]) || params[key].length > 10000 || params[key].some(v => typeof v !== 'string' || !v || v.length > 32767)) throw new Error(`参数 ${key} 无效。`)
  }
  if (['save', 'approve', 'page'].includes(method)) string('id')
  if (['save', 'approve', 'xml'].includes(method) && (!params.metadata || typeof params.metadata !== 'object' || Array.isArray(params.metadata))) throw new Error('元数据无效。')
  if (method === 'page' && (!Number.isSafeInteger(params.index) || params.index < 0)) throw new Error('页码无效。')
  if (method === 'xml' && (!Number.isSafeInteger(params.pageCount) || params.pageCount < 1)) throw new Error('页数无效。')
  if (method === 'scan') strings('roots', false)
  if (['import', 'prepare_import'].includes(method)) strings('paths')
  if (method === 'browse_folders') string('path')
  if (method === 'prepare_import' && params.recursive !== undefined && typeof params.recursive !== 'boolean') throw new Error('递归选项无效。')
  if (['remove', 'batch_edit', 'commit_import'].includes(method)) strings('ids')
  if (method === 'commit_import') {
    string('token')
    if (params.overwriteExisting !== undefined && typeof params.overwriteExisting !== 'boolean') throw new Error('覆盖选项无效。')
    const fields = params.defaults
    if (fields === null || (fields !== undefined && (typeof fields !== 'object' || Array.isArray(fields)))) throw new Error('批量资料无效。')
    if (fields) {
      if (Object.keys(fields).some(key => !['authors', 'genre'].includes(key))) throw new Error('批量字段无效。')
      if (fields.authors !== undefined && (!Array.isArray(fields.authors) || fields.authors.length > 1000 || fields.authors.some(author => typeof author !== 'string' || !author.trim() || author.length > 32767))) throw new Error('作者无效。')
      if (fields.genre !== undefined && (typeof fields.genre !== 'string' || !fields.genre.trim() || fields.genre.length > 32767)) throw new Error('类型无效。')
    }
  }
  if (method === 'batch_edit') validateBatchEdit(params.changes)
  if (method === 'plan') { strings('ids'); string('outputRoot') }
  if (method === 'export') string('token')
  if (['set_output', 'backup'].includes(method)) string('path')
}

function pythonCommand(root) {
  const candidates = [process.env.COMIC_MANAGE_PYTHON, path.join(root, '.venv', 'Scripts', 'python.exe'), path.join(root, '.venv', 'bin', 'python')]
  const executable = candidates.find(p => p && existsSync(p))
  if (!executable) throw new Error('未找到开发用 Python。请创建 .venv，或设置 COMIC_MANAGE_PYTHON。便携版不需要安装 Python。')
  return executable
}

class ServiceBridge extends EventEmitter {
  constructor({ root, dataDir, resourcesPath, packaged = false }) {
    super()
    this.pending = new Map()
    this.sequence = 0
    this.stopped = false
    const executable = packaged
      ? path.join(resourcesPath, 'service', 'ComicManageService.exe')
      : pythonCommand(root)
    const args = packaged ? ['--data-dir', dataDir] : ['-u', path.join(root, 'service_main.py'), '--data-dir', dataDir]
    this.child = spawn(executable, args, { cwd: packaged ? path.dirname(executable) : root, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } })
    let diagnostic = ''
    this.child.stderr.setEncoding('utf8')
    this.child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-4096) })
    this.lines = createInterface({ input: this.child.stdout, crlfDelay: Infinity })
    this.lines.on('line', line => {
      try {
        const message = JSON.parse(line)
        if (message.event) { this.emit('event', message.event, message.data); return }
        const pending = this.pending.get(String(message.id))
        if (!pending) return
        this.pending.delete(String(message.id))
        clearTimeout(pending.timer)
        if (message.error) pending.reject(new Error(message.error.message || '后台处理失败。'))
        else pending.resolve(message.result)
      } catch { this.fail(new Error('后台返回了无效数据，请重新启动应用。')) }
    })
    this.child.on('error', error => this.fail(new Error(`无法启动文件服务：${error.message}`)))
    this.child.stdin.on('error', error => this.fail(new Error(`文件服务通信已中断：${error.message}`)))
    this.child.on('exit', code => {
      this.stopped = true
      const error = new Error(`文件服务已退出（${code ?? '未知'}）。${diagnostic.trim()}`)
      this.fail(error)
      this.emit('event', 'service-exit', { message: error.message })
    })
  }

  call(method, params = {}) {
    try { validateCall(method, params) } catch (error) { return Promise.reject(error) }
    if (this.stopped || !this.child.stdin.writable) return Promise.reject(new Error('文件服务不可用，请重启应用。'))
    const id = String(++this.sequence)
    return new Promise((resolve, reject) => {
      // NAS jobs can legitimately take hours. They are cancelled explicitly.
      const timer = LONG_METHODS.has(method) ? undefined : setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('文件服务响应超时；网络目录可能不可用。请等待或取消当前任务。'))
      }, 120000)
      this.pending.set(id, { resolve, reject, timer })
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(error) }
      })
    })
  }

  fail(error) {
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error) }
    this.pending.clear()
  }

  async stop() {
    if (this.stopped) return
    if (this.stopping) return this.stopping
    this.stopping = new Promise(resolve => {
      const timer = setTimeout(() => {
        this.fail(new Error('文件服务关闭超时。'))
        if (!this.stopped) this.child.kill()
        resolve()
      }, 5000)
      this.child.once('exit', () => { clearTimeout(timer); resolve() })
      // Cancellation is best-effort; a stuck NAS read must not block app shutdown.
      this.call('cancel').catch(() => {}).finally(() => {
        if (!this.child.stdin.destroyed) this.child.stdin.end()
      })
      setTimeout(() => { if (!this.child.stdin.destroyed) this.child.stdin.end() }, 300).unref()
    })
    return this.stopping
  }
}

module.exports = { ServiceBridge, validateCall, METHODS, pythonCommand }
