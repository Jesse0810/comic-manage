'use strict'

const { spawn } = require('node:child_process')
const { EventEmitter } = require('node:events')
const { createInterface } = require('node:readline')
const { existsSync } = require('node:fs')
const path = require('node:path')

const METHODS = Object.freeze(['bootstrap', 'scan', 'import', 'browse_folders', 'prepare_import', 'commit_import', 'remove', 'batch_edit', 'batch_number', 'save', 'approve', 'page', 'xml', 'scrape_preview', 'model_test', 'model_assist', 'agent_prepare', 'agent_apply', 'agent_search', 'agent_search_preview', 'plan', 'export', 'cancel', 'set_output', 'backup', 'export_komga', 'komga_test', 'komga_sync', 'komga_pull', 'komga_resolve', 'source_context', 'prepare_review_sync', 'commit_review_sync', 'komga_jobs', 'komga_jobs_retry', 'komga_jobs_run'])
const LONG_METHODS = new Set(['scan', 'import', 'prepare_import', 'commit_import', 'plan', 'export', 'komga_sync', 'komga_pull', 'prepare_review_sync', 'commit_review_sync', 'komga_jobs_run', 'komga_jobs_retry', 'agent_prepare', 'agent_apply', 'agent_search', 'agent_search_preview'])
const BATCH_TEXT_FIELDS = new Set([
  'genre', 'series', 'localized_series', 'number', 'count', 'volume', 'translator', 'publisher', 'work_group', 'text_version',
  'release_type', 'publication_format', 'year', 'month', 'day', 'language',
  'age_rating', 'color', 'reading_direction', 'manga', 'collection_mode', 'localized_series_label',
])
const BATCH_LIST_FIELDS = new Set(['authors', 'pencillers', 'original_works', 'characters', 'web', 'tags', 'release_methods', 'release_platforms'])
const BATCH_ENUMS = {
  collection_mode: ['oneshot', 'series'],
  genre: ['', '漫画', '小说', 'CG集', '画集', '设定集', '插画集'],
  release_type: ['', '同人发行', '商业出版', '个人独立发布'],
  age_rating: ['Unknown', 'Everyone', 'MA15+', 'R18+'],
  color: ['', '黑白', '全彩', '部分彩色'],
  reading_direction: ['', 'LEFT_TO_RIGHT', 'RIGHT_TO_LEFT', 'VERTICAL', 'WEBTOON'],
  text_version: ['', '有文字', '无文字'],
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
      if (field === 'original_works' && value.length > 1) throw new Error('原作最多填写一个名称。')
      if (field === 'release_methods' && value.some(item => !['公开发布', '单次购买', '会员订阅', '免费与付费并行'].includes(item.trim()))) throw new Error('发布方式无效。')
    } else if (field === 'uncensored') {
      if (typeof value !== 'boolean') throw new Error('无修正选项无效。')
    } else if (BATCH_TEXT_FIELDS.has(field)) {
      if (!validBatchText(value, true)) throw new Error(`批量字段 ${field} 的文本无效。`)
      if (Object.hasOwn(BATCH_ENUMS, field) && !BATCH_ENUMS[field].includes(value.trim())) throw new Error(`批量字段 ${field} 的选项无效。`)
    } else {
      throw new Error('批量字段无效。')
    }
  }
}

const AGENT_SELECTION_MAX = 24
const AGENT_TEXT_MAX = 1200
const isRecord = value => !!value && typeof value === 'object' && !Array.isArray(value)
const validAgentID = value => typeof value === 'string' && !!value && value.length <= 128 && !/[^A-Za-z0-9_-]/.test(value)

const SEARCH_SITES = new Set(['dlsite', 'dmm', 'ehentai', 'nhentai'])

function validateSearchRequest(method, params) {
  const discovery = method === 'agent_search'
  const allowed = discovery ? ['ids', 'providers'] : ['token', 'choices']
  if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('站点搜索请求包含不支持的参数。')
  if (discovery) {
    if (!Array.isArray(params.ids) || !params.ids.length || params.ids.length > AGENT_SELECTION_MAX
        || params.ids.some(id => !validAgentID(id)) || new Set(params.ids).size !== params.ids.length) {
      throw new Error('每次请选择 1 至 24 本不同的作品。')
    }
    if (!Array.isArray(params.providers) || !params.providers.length || params.providers.length > SEARCH_SITES.size
        || params.providers.some(site => !SEARCH_SITES.has(site)) || new Set(params.providers).size !== params.providers.length) {
      throw new Error('请选择要搜索的站点。')
    }
  } else if (!validAgentID(params.token) || !Array.isArray(params.choices) || !params.choices.length
      || params.choices.length > AGENT_SELECTION_MAX || params.choices.some(row => !isRecord(row)
        || Object.keys(row).sort().join(',') !== 'candidateId,id' || !validAgentID(row.id) || !validAgentID(row.candidateId))) {
    throw new Error('请从此次搜索中选择有效的作品候选。')
  }
}

function validateSearchCredentials(credentials) {
  if (!isRecord(credentials) || Object.keys(credentials).some(site => !['dmm', 'nhentai'].includes(site))) {
    throw new Error('站点凭据格式无效。')
  }
  for (const [site, value] of Object.entries(credentials)) {
    const keys = site === 'dmm' ? ['apiKey', 'affiliateId'] : ['apiKey']
    if (!isRecord(value) || Object.keys(value).some(key => !keys.includes(key))
        || Object.values(value).some(secret => typeof secret !== 'string' || !/^[A-Za-z0-9_.=+/~-]{1,512}$/.test(secret))) {
      throw new Error('站点 API 凭据格式无效。')
    }
  }
}

function validateAgentRequest(method, params) {
  if (!isRecord(params)) throw new Error('Agent 请求格式无效。')
  if (method === 'agent_search' || method === 'agent_search_preview') return validateSearchRequest(method, params)
  const prepare = method === 'agent_prepare'
  const allowed = prepare ? ['ids', 'profile_id', 'prompt', 'use_sources'] : ['token', 'books']
  if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('Agent 请求包含不支持的参数。')
  if (prepare) {
    if (!Array.isArray(params.ids) || !params.ids.length || params.ids.length > AGENT_SELECTION_MAX
        || params.ids.some(id => !validAgentID(id))) throw new Error('每次请选择 1 至 24 本作品。')
    if (!validAgentID(params.profile_id) || typeof params.prompt !== 'string'
        || !params.prompt.trim() || [...params.prompt].length > AGENT_TEXT_MAX || params.prompt.includes('\0')
        || typeof params.use_sources !== 'boolean') throw new Error('请选择模型并填写有效的整理任务。')
    return
  }
  if (!validAgentID(params.token) || !Array.isArray(params.books) || !params.books.length
      || params.books.length > AGENT_SELECTION_MAX || params.books.some(book => !isRecord(book)
        || Object.keys(book).some(key => !['id', 'fields'].includes(key)) || !validAgentID(book.id)
        || !Array.isArray(book.fields) || !book.fields.length || book.fields.length > 40
        || book.fields.some(field => !validAgentID(field)))) throw new Error('请选择计划中要应用的字段。')
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
  if (['save', 'approve', 'page', 'komga_resolve', 'source_context'].includes(method)) string('id')
  if (['save', 'approve', 'xml'].includes(method) && (!params.metadata || typeof params.metadata !== 'object' || Array.isArray(params.metadata))) throw new Error('元数据无效。')
  if (method === 'page' && (!Number.isSafeInteger(params.index) || params.index < 0)) throw new Error('页码无效。')
  if (method === 'xml' && (!Number.isSafeInteger(params.pageCount) || params.pageCount < 0
      || (params.pageCount === 0 && (typeof params.id !== 'string' || !params.id)))) throw new Error('页数无效。')
  if (method === 'scan') strings('roots', false)
  if (['import', 'prepare_import'].includes(method)) strings('paths')
  if (method === 'browse_folders') string('path')
  if (method === 'scrape_preview') {
    if (Object.keys(params).some(key => !['url', 'apiKey', 'affiliateId'].includes(key))
        || typeof params.url !== 'string' || !params.url || params.url.length > 2048) throw new Error('请提供作品的 HTTPS 链接。')
    for (const key of ['apiKey', 'affiliateId']) {
      if (params[key] !== undefined && (typeof params[key] !== 'string' || !/^[A-Za-z0-9_.=+/~-]{1,512}$/.test(params[key]))) {
        throw new Error('站点 API 凭据格式无效。')
      }
    }
  }
  if (['model_test', 'model_assist'].includes(method)) {
    const allowed = method === 'model_test' ? ['profile', 'api_key'] : ['profile', 'api_key', 'id', 'metadata', 'siteFields']
    const isRecord = value => !!value && typeof value === 'object' && !Array.isArray(value)
    if (Object.keys(params).some(key => !allowed.includes(key)) || !isRecord(params.profile)
        || Object.keys(params.profile).sort().join(',') !== 'base_url,model,provider'
        || typeof params.api_key !== 'string' || !/^[\x20-\x7e]*$/.test(params.api_key)
        || params.api_key.length > (params.profile.provider === 'chatgpt-plan' ? 16384 : 4096)) throw new Error('模型服务请求参数无效。')
    if (method === 'model_assist') {
      string('id')
      if (!isRecord(params.metadata) || (params.siteFields !== undefined && !isRecord(params.siteFields))) throw new Error('作品资料无效。')
    }
  }
  if (['agent_prepare', 'agent_apply', 'agent_search', 'agent_search_preview'].includes(method)) {
    const { komga_scope, ...rest } = params
    if (typeof komga_scope !== 'string' || !/^(?:[a-f0-9]{20})?$/.test(komga_scope)) throw new Error('Agent 书库范围无效。')
    if (method === 'agent_search' || method === 'agent_search_preview') {
      const { credentials = {}, ...request } = rest
      validateSearchRequest(method, request)
      validateSearchCredentials(credentials)
    } else if (method === 'agent_apply') validateAgentRequest(method, rest)
    else {
      const { profile, api_key, ...request } = rest
      validateAgentRequest(method, { ...request, profile_id: 'trusted-profile' })
      if (!isRecord(profile) || Object.keys(profile).sort().join(',') !== 'base_url,model,provider'
          || typeof api_key !== 'string' || !/^[\x20-\x7e]*$/.test(api_key)
          || api_key.length > (profile.provider === 'chatgpt-plan' ? 16384 : 4096)) throw new Error('Agent 模型配置无效。')
    }
  }
  if (method === 'prepare_import') {
    if (params.recursive !== undefined && typeof params.recursive !== 'boolean') throw new Error('递归选项无效。')
    strings('excludedPaths', false)
  }
  if (['remove', 'batch_edit', 'batch_number', 'commit_import', 'export_komga', 'komga_sync', 'prepare_review_sync', 'komga_jobs_retry'].includes(method)) strings('ids')
  if (method === 'komga_jobs' && Object.keys(params).length) throw new Error('同步任务列表不接受额外参数。')
  if (method === 'komga_jobs_retry' && (!params.ids.length || Object.keys(params).some(key => !['ids', 'url', 'libraryId'].includes(key)))) throw new Error('重试任务参数无效。')
  if (method === 'prepare_review_sync' && !params.ids.length) throw new Error('请勾选要批量审核的作品。')
  if (method === 'commit_review_sync') string('token')
  if (method === 'source_context' && Object.keys(params).some(key => key !== 'id')) throw new Error('作品来源参数无效。')
  if (method === 'komga_resolve' && !['keep_local', 'use_remote'].includes(params.resolution)) throw new Error('冲突处理方式无效。')
  if (['komga_test', 'komga_sync', 'komga_pull', 'komga_resolve', 'prepare_review_sync', 'commit_review_sync'].includes(method)) {
    const allowed = method === 'komga_test' ? ['url', 'apiKey']
      : method === 'komga_sync' ? ['ids', 'url', 'apiKey', 'libraryId']
      : method === 'komga_resolve' ? ['id', 'resolution', 'url', 'apiKey', 'libraryId']
      : method === 'prepare_review_sync' ? ['ids', 'url', 'apiKey', 'libraryId']
      : method === 'commit_review_sync' ? ['token', 'url', 'apiKey', 'libraryId']
      : ['url', 'apiKey', 'libraryId']
    if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('Komga 请求参数无效。')
    for (const key of ['url', 'apiKey', 'libraryId']) {
      if (params[key] !== undefined && (typeof params[key] !== 'string' || !params[key].trim()
          || params[key].length > 8192 || /[\r\n\0]/.test(params[key]))) throw new Error('Komga 连接参数无效。')
    }
    if (method === 'komga_test') { string('url'); string('apiKey') }
  }
  if (method === 'commit_import') {
    string('token')
    if (params.overwriteExisting !== undefined && typeof params.overwriteExisting !== 'boolean') throw new Error('覆盖选项无效。')
    const fields = params.defaults
    if (fields !== undefined) {
      if (!isRecord(fields)) throw new Error('批量资料无效。')
      const { summary, cover_index, ...batchFields } = fields
      if (Object.keys(batchFields).length) validateBatchEdit(batchFields)
      if (Object.hasOwn(fields, 'genre') && !fields.genre) throw new Error('请选择有效的导入内容类型。')
      if (Object.hasOwn(fields, 'summary') && (typeof summary !== 'string' || summary.length > 32767 || summary.includes('\0'))) throw new Error('作品简介无效。')
      if (Object.hasOwn(fields, 'cover_index') && (!Number.isInteger(cover_index) || cover_index < 0 || cover_index > 2147483646)) throw new Error('封面页码无效。')
    }
  }
  if (method === 'batch_number') {
    if (Object.keys(params).some(key => !['ids', 'start', 'komga_scope'].includes(key))
        || params.ids.length < 2 || new Set(params.ids).size !== params.ids.length
        || !Number.isInteger(params.start) || params.start < 0 || params.start + params.ids.length - 1 > 2147483647
        || (params.komga_scope !== undefined && (typeof params.komga_scope !== 'string' || !/^(?:[a-f0-9]{20})?$/.test(params.komga_scope)))) {
      throw new Error('请选择至少两本不同作品，编号须在 0 到 2147483647 之间。')
    }
  }
  if (method === 'batch_edit') validateBatchEdit(params.changes)
  if (method === 'plan') { strings('ids'); string('outputRoot') }
  if (method === 'export') {
    string('token')
    strings('ids', false)
    strings('confirmedIds', false)
    if (params.ids && !params.ids.length) throw new Error('请选择要生成的作品。')
  }
  if (['plan', 'export'].includes(method) && params.comparison !== undefined) {
    const value = params.comparison
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).sort().join(',') !== 'apiKey,libraryId,url'
        || Object.values(value).some(item => typeof item !== 'string' || !item.trim() || item.length > 8192
            || /[\r\n\0]/.test(item))) throw new Error('Komga 查重连接参数无效。')
  }
  if (method === 'export' && params.autoSync !== undefined) {
    if (!params.autoSync || typeof params.autoSync !== 'object' || Array.isArray(params.autoSync)
        || Object.keys(params.autoSync).sort().join(',') !== 'libraryId,url'
        || ['url', 'libraryId'].some(key => typeof params.autoSync[key] !== 'string' || !params.autoSync[key].trim() || params.autoSync[key].length > 8192 || /[\r\n\0]/.test(params.autoSync[key]))) throw new Error('自动同步配置无效。')
  }
  if (method === 'komga_jobs_run') {
    if (Object.keys(params).sort().join(',') !== 'apiKey,libraryId,url'
        || ['url', 'apiKey', 'libraryId'].some(key => typeof params[key] !== 'string' || !params[key].trim() || params[key].length > 8192 || /[\r\n\0]/.test(params[key]))) throw new Error('自动同步连接参数无效。')
  }
  if (['set_output', 'backup', 'export_komga'].includes(method)) string('path')
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

module.exports = { ServiceBridge, validateCall, validateAgentRequest, METHODS, pythonCommand }
