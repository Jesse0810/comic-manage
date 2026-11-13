'use strict'

const { app, BrowserWindow, ipcMain, dialog, shell, protocol, safeStorage } = require('electron')
const { readFile, realpath, stat } = require('node:fs/promises')
const { mkdirSync } = require('node:fs')
const path = require('node:path')
const { createHash } = require('node:crypto')
const { ServiceBridge, validateCall } = require('./bridge.cjs')
const { KomgaConfigStore, normalizeKomgaUrl } = require('./komga-config.cjs')

protocol.registerSchemesAsPrivileged([{ scheme: 'comic', privileges: { standard: true, secure: true, supportFetchAPI: true } }])

function argument(name) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] : undefined
}

function defaultDataDirectory() {
  // Keep the v1 SQLite catalog in place; Chromium cache gets its own subdirectory.
  return process.platform === 'win32'
    ? path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'ComicManage', 'ComicManage')
    : path.join(app.getPath('appData'), 'ComicManage')
}

const mimeTypes = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'"

async function createApplication({ dataDir = defaultDataDirectory(), show = true } = {}) {
  const root = path.resolve(__dirname, '..')
  const rendererRoot = path.join(root, 'renderer-dist')
  const devURL = !app.isPackaged ? process.env.COMIC_MANAGE_DEV_URL : undefined
  if (devURL && new URL(devURL).origin !== 'http://127.0.0.1:5173') throw new Error('开发界面地址无效。')

  if (!protocol.isProtocolHandled || !(await protocol.isProtocolHandled('comic'))) {
    protocol.handle('comic', async request => {
      try {
        const url = new URL(request.url)
        if (url.hostname !== 'ui' || request.method !== 'GET') return new Response('Not found', { status: 404 })
        const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'index.html'
        const file = path.resolve(rendererRoot, relative)
        const actualRoot = await realpath(rendererRoot)
        const actualFile = await realpath(file)
        if (!actualFile.startsWith(actualRoot + path.sep)) return new Response('Forbidden', { status: 403 })
        return new Response(await readFile(actualFile), { headers: {
          'Content-Type': mimeTypes[path.extname(actualFile)] || 'application/octet-stream',
          'Content-Security-Policy': CSP,
        } })
      } catch { return new Response('Not found', { status: 404 }) }
    })
  }

  const bridge = new ServiceBridge({ root, dataDir, resourcesPath: process.resourcesPath, packaged: app.isPackaged })
  const komgaConfig = new KomgaConfigStore(dataDir, safeStorage)
  let requestQueue = Promise.resolve()
  const callService = (method, params = {}) => {
    if (method === 'cancel') return bridge.call(method, params)
    const request = requestQueue.then(() => bridge.call(method, params))
    requestQueue = request.catch(() => {})
    return request
  }
  const window = new BrowserWindow({
    width: 1500, height: 980, minWidth: 1120, minHeight: 740,
    title: 'Comic Manage · 漫画整理', backgroundColor: '#f6f8fa', show: false,
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true,
      spellcheck: false, devTools: !app.isPackaged },
  })
  let windowState = { dirty: false, busy: false }
  let allowClose = false
  const send = (event, data) => { if (!window.isDestroyed()) window.webContents.send('comic:event', event, data) }
  bridge.on('event', send)
  const trusted = event => {
    const expected = devURL || 'comic://ui/'
    if (window.isDestroyed() || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('请求来源无效。')
    const url = event.senderFrame.url
    if (devURL ? new URL(url).origin !== new URL(expected).origin : !url.startsWith(expected)) throw new Error('请求来源无效。')
  }
  const handle = (channel, callback) => {
    ipcMain.removeHandler(channel)
    ipcMain.handle(channel, (event, ...args) => { trusted(event); return callback(...args) })
  }
  handle('comic:call', async (method, params = {}) => {
    if (['komga_test', 'source_context'].includes(method)) throw new Error('此操作只允许主进程调用。')
    validateCall(method, params)
    if (method === 'bootstrap') {
      const result = await callService(method, params)
      try {
        const config = await komgaConfig.get()
        const scope = config.url && config.libraryId
          ? createHash('sha256').update(normalizeKomgaUrl(config.url) + '\n' + config.libraryId).digest('hex').slice(0, 20)
          : ''
        return { ...result, komgaScope: scope, komgaLibraryName: config.libraryName }
      } catch {
        return { ...result, komgaScope: '', komgaLibraryName: '' }
      }
    }
    if (method === 'komga_pull') {
      if (Object.keys(params).length) throw new Error('请使用当前配置的 Komga 书库。')
      return callService(method, await komgaConfig.connection())
    }
    if (method === 'komga_resolve') {
      if (Object.keys(params).some(key => !['id', 'resolution'].includes(key))) throw new Error('冲突处理参数无效。')
      return callService(method, { ...params, ...await komgaConfig.connection() })
    }
    if (['prepare_review_sync', 'commit_review_sync'].includes(method)) {
      const allowed = method === 'prepare_review_sync' ? 'ids' : 'token'
      if (Object.keys(params).some(key => key !== allowed)) throw new Error('批量审核只接受所选作品或本次检查凭证。')
      return callService(method, { ...params, ...await komgaConfig.connection() })
    }
    if (['approve', 'page'].includes(method)) {
      const allowed = method === 'approve' ? ['id', 'metadata'] : ['id', 'index']
      if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('作品请求参数无效。')
      const context = await callService('source_context', { id: params.id })
      if (context.kind === 'komga') {
        return callService(method, { ...params, ...await komgaConfig.connection() })
      }
    }
    if (method === 'komga_sync') {
      if (Object.keys(params).some(key => key !== 'ids')) throw new Error('同步只接受选中的作品。')
      return callService(method, { ...params, ...await komgaConfig.connection() })
    }
    return callService(method, params)
  })
  handle('comic:komga-config', () => komgaConfig.get())
  handle('comic:save-komga-config', input => komgaConfig.save(input))
  handle('comic:test-komga-config', async input => callService('komga_test', await komgaConfig.credentials(input)))
  handle('comic:open-komga-book', async id => {
    if (typeof id !== 'string' || !id || id.length > 32767) throw new Error('作品 ID 无效。')
    const context = await callService('source_context', { id })
    const config = await komgaConfig.get()
    const remote = context.remote
    if (context.kind !== 'komga' || !remote || !config.url || !config.libraryId
        || remote.serverUrl !== normalizeKomgaUrl(config.url) || remote.libraryId !== config.libraryId) {
      throw new Error('请先切换到这本漫画所属的 Komga 服务器和书库。')
    }
    await shell.openExternal(normalizeKomgaUrl(config.url) + '/book/' + encodeURIComponent(remote.bookId))
  })
  handle('comic:version', () => app.getVersion())
  handle('comic:choose-directory', async () => {
    const result = await dialog.showOpenDialog(window, { title: '选择本地或 NAS 目录', properties: ['openDirectory', 'createDirectory'] })
    return result.canceled ? null : result.filePaths[0]
  })
  handle('comic:choose-cbz', async () => {
    const result = await dialog.showOpenDialog(window, { title: '导入已有 CBZ', filters: [{ name: '漫画压缩包', extensions: ['cbz'] }], properties: ['openFile', 'multiSelections'] })
    return result.canceled ? [] : result.filePaths
  })
  handle('comic:choose-directories', async () => {
    const result = await dialog.showOpenDialog(window, { title: '选择一个或多个漫画文件夹', properties: ['openDirectory', 'multiSelections'] })
    return result.canceled ? [] : result.filePaths
  })
  handle('comic:choose-backup', async () => {
    const result = await dialog.showSaveDialog(window, { title: '备份资料库', defaultPath: `ComicManage-${new Date().toISOString().slice(0, 10)}.sqlite3`,
      filters: [{ name: 'SQLite 资料库', extensions: ['sqlite3'] }] })
    return result.canceled ? null : result.filePath
  })
  handle('comic:choose-komga-manifest', async () => {
    const result = await dialog.showSaveDialog(window, {
      title: '导出 Komga 同步清单',
      defaultPath: 'komga_notable_entities_manifest.json',
      filters: [{ name: 'Komga 同步清单', extensions: ['json'] }],
    })
    return result.canceled ? null : result.filePath
  })
  handle('comic:open-location', async (kind, id) => {
    if (!['source', 'output'].includes(kind) || typeof id !== 'string') throw new Error('打开位置参数无效。')
    const catalog = await callService('bootstrap')
    const comic = catalog.comics.find(item => item.id === id)
    if (!comic) throw new Error('作品记录不存在。')
    if (comic.kind === 'komga') throw new Error('远端作品请使用“在 Komga 中查看”。')
    const location = kind === 'source' ? comic.source : comic.output
    if (!location) throw new Error('此作品尚未生成成品。')
    const info = await stat(location)
    const directory = info.isDirectory() ? location : path.dirname(location)
    const failure = await shell.openPath(directory)
    if (failure) throw new Error(failure)
  })
  handle('comic:close', () => { allowClose = true; window.close() })
  const stateListener = (event, state) => {
    try { trusted(event); windowState = { dirty: state?.dirty === true, busy: state?.busy === true } } catch { /* ignore untrusted frames */ }
  }
  ipcMain.on('comic:window-state', stateListener)
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    if (devURL ? new URL(url).origin !== new URL(devURL).origin : !url.startsWith('comic://ui/')) event.preventDefault()
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  window.on('close', event => {
    if (!allowClose && (windowState.dirty || windowState.busy)) { event.preventDefault(); send('close-request', {}) }
  })
  window.on('closed', () => { ipcMain.removeListener('comic:window-state', stateListener) })
  window.once('ready-to-show', () => { if (show) window.show() })
  await window.loadURL(devURL || 'comic://ui/index.html')
  return { window, bridge, dataDir }
}

async function main() {
  const dataDir = path.resolve(argument('--data-dir') || defaultDataDirectory())
  mkdirSync(dataDir, { recursive: true })
  mkdirSync(path.join(dataDir, 'electron'), { recursive: true })
  app.setPath('userData', path.join(dataDir, 'electron'))
  if (!app.requestSingleInstanceLock()) { app.quit(); return }
  let desktop
  let stopping = false
  app.on('second-instance', () => {
    if (desktop && !desktop.window.isDestroyed()) { desktop.window.restore(); desktop.window.focus() }
  })
  app.on('before-quit', event => {
    if (stopping) return
    event.preventDefault()
    stopping = true
    Promise.resolve(desktop?.bridge.stop()).finally(() => app.exit(0))
  })
  app.on('window-all-closed', () => app.quit())
  await app.whenReady()
  try { desktop = await createApplication({ dataDir }) }
  catch (error) {
    dialog.showErrorBox('无法启动漫画整理', error.message)
    await desktop?.bridge.stop()
    app.exit(1)
  }
}

if (require.main === module) main()
module.exports = { createApplication, defaultDataDirectory, CSP }
