'use strict'

const { app, BrowserWindow, ipcMain, dialog, shell, protocol } = require('electron')
const { readFile, realpath, stat } = require('node:fs/promises')
const { mkdirSync } = require('node:fs')
const path = require('node:path')
const { ServiceBridge, validateCall } = require('./bridge.cjs')

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
  handle('comic:call', async (method, params = {}) => { validateCall(method, params); return callService(method, params) })
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
