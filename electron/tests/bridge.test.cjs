'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter, once } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { createHash } = require('node:crypto')
const vm = require('node:vm')
const { ServiceBridge, validateCall } = require(process.env.COMIC_MANAGE_BRIDGE_MODULE || '../bridge.cjs')
const { normalizeKomgaUrl } = require(process.env.COMIC_MANAGE_KOMGA_CONFIG_MODULE || '../komga-config.cjs')

const FAKE_SERVICE = String.raw`
import json, os, sys, threading, time
write_lock = threading.Lock()
cancel = threading.Event()

def emit(value, fragmented=False):
    raw = (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')
    with write_lock:
        if fragmented:
            # Split inside a UTF-8 character and before the newline. The bridge
            # must decode both streaming text and line boundaries correctly.
            offset = raw.index('漫'.encode('utf-8')) + 1
            sys.stdout.buffer.write(raw[:offset]); sys.stdout.buffer.flush()
            time.sleep(.02)
            sys.stdout.buffer.write(raw[offset:]); sys.stdout.buffer.flush()
        else:
            sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()

def handle(request):
    method, params = request['method'], request.get('params', {})
    identity = request['id']
    if method == 'bootstrap':
        emit({'id':identity, 'result':{'title':'漫画目录 / 日本語', 'roots':['中文路径']}}, True)
    elif method == 'page':
        time.sleep(.08 if params['index'] == 0 else .005)
        emit({'id':identity, 'result':{'index':params['index'], 'name':params['id']}})
    elif method == 'scan':
        emit({'event':'task-progress','data':{'message':'扫描中：春の旅'}})
        cancel.wait(10)
        emit({'id':identity, 'result':{'count':1, 'errors':[], 'cancelled':cancel.is_set()}})
    elif method in ('komga_sync', 'komga_pull', 'prepare_review_sync', 'commit_review_sync'):
        emit({'event':'task-progress','data':{'message':'拉取中：春の旅' if method == 'komga_pull' else '同步中：春の旅'}})
        cancel.wait(10)
        emit({'id':identity, 'result':{'synced':0, 'cancelled':cancel.is_set()}})
    elif method == 'cancel':
        cancel.set()
        emit({'id':identity, 'result':{'cancelled':True}})
    elif method == 'import' and params['paths'] == ['crash']:
        sys.stderr.write('fixture diagnostic: storage disconnected\n'); sys.stderr.flush()
        time.sleep(.03)
        os._exit(23)
    elif method == 'import' and params['paths'] == ['malformed']:
        with write_lock:
            sys.stdout.write('not-json\n'); sys.stdout.flush()
    elif method == 'save':
        emit({'id':identity, 'error':{'message':'作者信息不完整', 'code':'VALIDATION'}})
    else:
        emit({'id':identity, 'result':params})

for line in sys.stdin:
    threading.Thread(target=handle, args=(json.loads(line),), daemon=True).start()
`

function interpreter() {
  if (process.env.COMIC_MANAGE_PYTHON && fs.existsSync(process.env.COMIC_MANAGE_PYTHON)) return process.env.COMIC_MANAGE_PYTHON
  const root = path.resolve(__dirname, '..', '..')
  const candidates = [path.join(root, '.venv', 'Scripts', 'python.exe'), path.join(root, '.venv', 'bin', 'python')]
  const existing = candidates.find(candidate => fs.existsSync(candidate))
  if (existing) return existing
  for (const command of (process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'])) {
    const result = spawnSync(command, ['-c', 'import sys; print(sys.executable)'], { encoding:'utf8', windowsHide:true })
    if (result.status === 0 && fs.existsSync(result.stdout.trim())) return result.stdout.trim()
  }
  throw new Error('Bridge tests need Python 3.11+; set COMIC_MANAGE_PYTHON to its executable.')
}

async function fixture(t) {
  const temporaryBase = path.resolve(os.tmpdir())
  const root = fs.mkdtempSync(path.join(temporaryBase, 'comic-bridge-'))
  fs.writeFileSync(path.join(root, 'service_main.py'), FAKE_SERVICE, 'utf8')
  const previous = process.env.COMIC_MANAGE_PYTHON
  process.env.COMIC_MANAGE_PYTHON = interpreter()
  let bridge
  try { bridge = new ServiceBridge({root, dataDir:path.join(root, 'data')}) }
  finally {
    if (previous === undefined) delete process.env.COMIC_MANAGE_PYTHON
    else process.env.COMIC_MANAGE_PYTHON = previous
  }
  t.after(async () => {
    // Clean up only the child and temporary directory created by this fixture.
    // Do not depend on production stop() when testing abnormal process exits.
    const child = bridge.child
    if (child.exitCode === null && child.signalCode === null) {
      const closed = once(child, 'close').catch(() => {})
      child.stdin.end()
      const killTimer = setTimeout(() => child.kill(), 500)
      await closed
      clearTimeout(killTimer)
    }
    assert.equal(path.dirname(path.resolve(root)), temporaryBase)
    assert.ok(path.basename(root).startsWith('comic-bridge-'))
    fs.rmSync(root, {recursive:true, force:true})
  })
  return bridge
}

function eventOf(bridge, expectedName) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { bridge.off('event', listener); reject(new Error(`Missing event: ${expectedName}`)) }, 5000)
    const listener = (name, data) => {
      if (name === expectedName) { clearTimeout(timer); bridge.off('event', listener); resolve(data) }
    }
    bridge.on('event', listener)
  })
}

async function mainProcessFixture(options = {}) {
  const handlers = new Map()
  const calls = []
  const credentials = {url:'http://127.0.0.1:25600', apiKey:'fixture-private-key', libraryId:'fixture-library'}
  const config = {url:credentials.url, libraryId:credentials.libraryId, libraryName:'漫画书库', ...options.config}
  const externalUrls = []
  const localPaths = []
  const statPaths = []
  let connectionCalls = 0
  const ipcMain = new EventEmitter()
  ipcMain.removeHandler = channel => handlers.delete(channel)
  ipcMain.handle = (channel, handler) => handlers.set(channel, handler)

  class FakeBridge extends EventEmitter {
    async call(method, params) {
      calls.push({method, params})
      if (method === 'bootstrap') return {comics:options.comics || [], roots:[], outputRoot:'', dataDir:'fixture-data'}
      if (method === 'source_context') return options.contexts?.[params.id] || {kind:'folder'}
      return {accepted:true}
    }
  }
  class FakeConfigStore {
    async get() {
      if (options.configError) throw new Error('fixture invalid config')
      return {...config}
    }
    async connection() {
      connectionCalls += 1
      if (options.connectionError) throw new Error('fixture missing connection')
      return credentials
    }
    async credentials() { return {url:credentials.url, apiKey:credentials.apiKey} }
  }
  class FakeWindow extends EventEmitter {
    constructor() {
      super()
      this.webContents = new EventEmitter()
      this.webContents.mainFrame = {url:'comic://ui/index.html'}
      this.webContents.session = {setPermissionRequestHandler() {}}
      this.webContents.setWindowOpenHandler = () => {}
      this.webContents.send = () => {}
    }
    isDestroyed() { return false }
    async loadURL() {}
    show() {}
  }

  const fakeElectron = {
    app: {isPackaged:false}, BrowserWindow:FakeWindow, ipcMain, dialog:{}, safeStorage:{},
    shell: {
      async openExternal(url) { externalUrls.push(url) },
      async openPath(directory) { localPaths.push(directory); return '' },
    },
    protocol: {registerSchemesAsPrivileged() {}, async isProtocolHandled() { return true }},
  }
  const fakeRequire = name => {
    if (name === 'electron') return fakeElectron
    if (name === './bridge.cjs') return {ServiceBridge:FakeBridge, validateCall}
    if (name === './komga-config.cjs') return {KomgaConfigStore:FakeConfigStore, normalizeKomgaUrl}
    if (name === 'node:fs/promises') return {
      ...require(name), stat: async location => { statPaths.push(location); return {isDirectory:() => true} },
    }
    return require(name)
  }
  const module = {exports:{}}
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8')
  vm.runInNewContext(source, {
    module, require:fakeRequire, __dirname:path.resolve(__dirname, '..'),
    process:{env:{}, resourcesPath:'fixture-resources'},
  }, {filename:'main.cjs'})
  const desktop = await module.exports.createApplication({dataDir:'fixture-data', show:false})
  const event = {sender:desktop.window.webContents, senderFrame:desktop.window.webContents.mainFrame}
  return {
    calls, credentials, config, externalUrls, localPaths, statPaths, connectionCalls:() => connectionCalls,
    invoke: (channel, ...args) => handlers.get(channel)(event, ...args),
  }
}

test('request boundary rejects arbitrary methods, malformed records and unsafe numeric indexes', () => {
  const invalid = [
    ['shell:execute', {command:'anything'}],
    ['page', {id:'book',index:-1}],
    ['page', {id:'book',index:1.5}],
    ['page', {id:'book',index:Number.MAX_SAFE_INTEGER + 1}],
    ['save', {id:'book',metadata:[]}],
    ['approve', {id:'',metadata:{}}],
    ['xml', {metadata:{},pageCount:-1}],
    ['xml', {metadata:{},pageCount:0}],
    ['plan', {ids:['book',null],outputRoot:'C:\\Comics'}],
    ['import', {paths:[42]}],
    ['export', {token:''}],
    ['browse_folders', {path:42}],
    ['prepare_import', {paths:['C:\\Comics'],recursive:'yes'}],
    ['commit_import', {token:'token',ids:['book'],defaults:{original_title:'hidden'}}],
    ['commit_import', {token:'token',ids:['book'],defaults:{series:'不得在导入时修改'}}],
    ['commit_import', {token:'token',ids:['book'],defaults:{genre:''}}],
    ['commit_import', {token:'token',ids:['book'],overwriteExisting:1}],
    ['batch_edit', {ids:['book'],changes:{authors:'writer'}}],
    ['batch_edit', {ids:['book'],changes:{authors:[]}}],
    ['batch_edit', {ids:['book'],changes:{genre:' '}}],
    ['batch_edit', {ids:['book'],changes:null}],
    ['batch_edit', {ids:['book'],changes:{}}],
    ['batch_edit', {ids:['book'],changes:{title:'单本作品专有字段'}}],
    ['batch_edit', {ids:['book'],changes:{source:'C:\\other'}}],
    ['batch_edit', {ids:['book'],changes:{pencillers:[' ']}}],
    ['batch_edit', {ids:['book'],changes:{web:[null]}}],
    ['batch_edit', {ids:['book'],changes:{uncensored:0}}],
    ['batch_edit', {ids:['book'],changes:{black_and_white:'No'}}],
    ['batch_edit', {ids:['book'],changes:{genre:'同人志'}}],
    ['batch_edit', {ids:['book'],changes:{original_works:['原作甲','原作乙']}}],
    ['batch_edit', {ids:['book'],changes:{release_methods:['平台推测的方式']}}],
    ['batch_edit', {ids:['book'],changes:{age_rating:'R18'}}],
    ['remove', {ids:['book',null]}],
    ['bootstrap', null],
    ['scan', []],
    ['save', {id:'book',metadata:{summary:'x'.repeat(4 * 1024 * 1024)}}],
  ]
  for (const [method, params] of invalid) assert.throws(() => validateCall(method, params), undefined, method)
})

test('staged import and batch actions cross the same restricted bridge', async t => {
  const bridge = await fixture(t)
  const operations = [
    ['browse_folders', {path:'C:\\Comics'}],
    ['prepare_import', {paths:['C:\\Comics','D:\\漫画'],recursive:true}],
    ['commit_import', {token:'prepared',ids:['one','two'],defaults:{authors:['みずいろめがね'],genre:'漫画'},overwriteExisting:false}],
    ['batch_edit', {ids:['one','two'],changes:{genre:'漫画'}}],
    ['batch_edit', {ids:['one','two'],changes:{
      collection_mode:'series', series:'原作系列', localized_series:'系列译名',
      localized_series_label:'zh-Hans', number:'2.5', count:'12', volume:'7',
      authors:['みずいろめがね'], pencillers:['森野'], translator:'译者',
      original_works:['东方Project'], characters:['灵梦'], release_type:'同人发行',
      publication_format:'单行本', year:'2024', month:'2', day:'29',
      language:'zh-Hans', age_rating:'R18+', color:'部分彩色',
      release_methods:['会员订阅','单次购买'], release_platforms:['Fantia','自定义平台'], tags:['日常'],
      manga:'YesAndRightToLeft', reading_direction:'WEBTOON', uncensored:true, web:['https://example.org/comic'],
    }}],
    ['batch_edit', {ids:['one','two'],changes:{
      collection_mode:'oneshot', genre:'', series:'', localized_series:'',
      localized_series_label:'', translator:'', pencillers:[],
      original_works:[], characters:[], release_type:'', publication_format:'',
      web:[], tags:[], language:'', color:'', reading_direction:'', release_methods:[], release_platforms:[], uncensored:false,
    }}],
    ['remove', {ids:['one']}],
  ]
  for (const [method, params] of operations) assert.deepEqual(await bridge.call(method, params), params)
})

test('collection patches reject unknown modes, malformed labels and retired color fields', () => {
  const invalidChanges = [
    {collection_mode:'collection'},
    {collection_mode:'Series'},
    {collection_mode:''},
    {collection_mode:null},
    {collection_mode:true},
    {localized_series_label:['zh-Hans']},
    {localized_series_label:' '},
    {black_and_white:'No'},
    {Volume:'3'},
  ]
  for (const changes of invalidChanges) {
    assert.throws(() => validateCall('batch_edit', {ids:['book'], changes}), undefined, JSON.stringify(changes))
  }
  for (const collection_mode of ['oneshot', 'series']) {
    assert.doesNotThrow(() => validateCall('batch_edit', {
      ids:['book'], changes:{collection_mode, localized_series_label:'zh-Hant'},
    }))
  }
  assert.throws(() => validateCall('commit_import', {
    token:'prepared', ids:['book'], defaults:{collection_mode:'series'},
  }), /批量字段/)
})

test('Komga export validates its selected IDs and destination before sending a request', async t => {
  const bridge = await fixture(t)
  const invalid = [
    {},
    {path:'C:\\成品\\Komga.json'},
    {ids:'book', path:'C:\\成品\\Komga.json'},
    {ids:['book',42], path:'C:\\成品\\Komga.json'},
    {ids:['book']},
    {ids:['book'], path:''},
    {ids:['book'], path:42},
  ]
  for (const params of invalid) {
    await assert.rejects(bridge.call('export_komga', params), undefined, JSON.stringify(params))
  }
  assert.equal(bridge.sequence, 0)
  assert.equal(bridge.pending.size, 0)
  await assert.rejects(bridge.call('export_arbitrary_file', {ids:['book'],path:'C:\\anything'}), /不支持/)
  const params = {ids:['one','two'], path:'C:\\成品\\漫画系列-komga.json'}
  assert.deepEqual(await bridge.call('export_komga', params), params)
})

test('private Komga service calls validate connection text and reject unknown fields', () => {
  const valid = {url:'http://127.0.0.1:25600', apiKey:'fixture-key'}
  assert.doesNotThrow(() => validateCall('komga_test', valid))
  assert.doesNotThrow(() => validateCall('komga_sync', {ids:['book']}))
  assert.doesNotThrow(() => validateCall('komga_sync', {...valid, ids:['book'], libraryId:'library'}))
  const invalid = [
    ['komga_test', {}],
    ['komga_test', {url:valid.url}],
    ['komga_test', {apiKey:valid.apiKey}],
    ['komga_test', {...valid, url:42}],
    ['komga_test', {...valid, apiKey:' '}],
    ['komga_test', {...valid, libraryId:'library'}],
    ['komga_test', {...valid, timeout:1}],
    ['komga_sync', {...valid, ids:'book'}],
    ['komga_sync', {...valid, ids:['book'], libraryId:false}],
    ['komga_sync', {...valid, ids:['book'], unexpected:'hidden'}],
  ]
  for (const method of ['komga_test', 'komga_sync']) {
    const keys = method === 'komga_test' ? ['url', 'apiKey'] : ['url', 'apiKey', 'libraryId']
    for (const key of keys) {
      for (const value of ['', ' ', 'a\r\nb', 'a\0b', 'a'.repeat(8193)]) {
        invalid.push([method, {...valid, ...(method === 'komga_sync' ? {ids:['book']} : {}), [key]:value}])
      }
    }
  }
  for (const [method, params] of invalid) {
    assert.throws(() => validateCall(method, params), undefined, `${method}: ${Object.keys(params).join(',')}`)
  }
})

test('preload exposes synchronization and settings testing through separate allowlisted channels', async () => {
  let api
  const calls = []
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'preload.cjs'), 'utf8'), {
    require: name => {
      assert.equal(name, 'electron')
      return {
        contextBridge: {exposeInMainWorld: (_name, value) => { api = value }},
        ipcRenderer: {invoke: async (...args) => { calls.push(args); return {accepted:true} }},
      }
    },
  }, {filename:'preload.cjs'})
  await assert.rejects(api.invoke('komga_test', {url:'http://localhost', apiKey:'fixture-key'}), /不支持/)
  await assert.rejects(api.invoke('source_context', {id:'remote'}), /不支持/)
  await assert.rejects(api.invoke('arbitrary_method', {}), /不支持/)
  assert.equal(calls.length, 0)
  await api.invoke('komga_sync', {ids:['book']})
  await api.invoke('komga_pull', {})
  await api.invoke('komga_resolve', {id:'remote',resolution:'keep_local'})
  await api.invoke('prepare_review_sync', {ids:['one','two']})
  await api.invoke('commit_review_sync', {token:'review-token'})
  await api.openKomgaBook('remote')
  await api.testKomgaConfig({url:'http://localhost', apiKey:'fixture-key'})
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ['comic:call', 'komga_sync', {ids:['book']}],
    ['comic:call', 'komga_pull', {}],
    ['comic:call', 'komga_resolve', {id:'remote',resolution:'keep_local'}],
    ['comic:call', 'prepare_review_sync', {ids:['one','two']}],
    ['comic:call', 'commit_review_sync', {token:'review-token'}],
    ['comic:open-komga-book', 'remote'],
    ['comic:test-komga-config', {url:'http://localhost', apiKey:'fixture-key'}],
  ])
})

test('renderer sync accepts only IDs and the main process injects stored credentials', async () => {
  const main = await mainProcessFixture()
  for (const extra of [{url:'http://elsewhere.invalid'}, {apiKey:'renderer-key'}, {libraryId:'renderer-library'}, {unexpected:true}]) {
    await assert.rejects(main.invoke('comic:call', 'komga_sync', {ids:['book'], ...extra}), /同步只接受|参数无效/)
  }
  await assert.rejects(main.invoke('comic:call', 'komga_test', {url:'http://localhost', apiKey:'renderer-key'}), /主进程/)
  assert.equal(main.calls.length, 0)
  assert.equal(main.connectionCalls(), 0)

  await main.invoke('comic:call', 'komga_sync', {ids:['one','two']})
  assert.equal(main.connectionCalls(), 1)
  assert.deepEqual(JSON.parse(JSON.stringify(main.calls)), [{
    method:'komga_sync', params:{ids:['one','two'], ...main.credentials},
  }])
  await main.invoke('comic:test-komga-config', {url:main.credentials.url})
  assert.deepEqual(JSON.parse(JSON.stringify(main.calls[1])), {
    method:'komga_test', params:{url:main.credentials.url, apiKey:main.credentials.apiKey},
  })
})

test('remote library and conflict requests validate their private service parameters', () => {
  const connection = {url:'http://127.0.0.1:25600', apiKey:'fixture-key', libraryId:'library'}
  for (const params of [{}, connection]) assert.doesNotThrow(() => validateCall('komga_pull', params))
  for (const resolution of ['keep_local', 'use_remote']) {
    assert.doesNotThrow(() => validateCall('komga_resolve', {id:'remote',resolution}))
    assert.doesNotThrow(() => validateCall('komga_resolve', {id:'remote',resolution,...connection}))
  }
  assert.doesNotThrow(() => validateCall('source_context', {id:'remote'}))
  assert.doesNotThrow(() => validateCall('xml', {id:'remote',metadata:{title:'远端作品'},pageCount:0}))
  const invalid = [
    ['komga_pull', {ids:['remote']}],
    ['komga_pull', {...connection, libraryId:42}],
    ['komga_pull', {...connection, apiKey:'bad\nkey'}],
    ['komga_resolve', {resolution:'keep_local'}],
    ['komga_resolve', {id:'remote'}],
    ['komga_resolve', {id:'remote',resolution:'overwrite'}],
    ['komga_resolve', {id:'remote',resolution:'use_remote',untrusted:true}],
    ['source_context', {}],
    ['source_context', {id:'remote',url:connection.url}],
  ]
  for (const [method, params] of invalid) assert.throws(() => validateCall(method, params), undefined, method)
})

test('public library pull and conflict resolution use the current saved connection', async () => {
  const main = await mainProcessFixture()
  for (const params of [{url:'http://other.invalid'}, {libraryId:'other'}, {apiKey:'renderer-key'}, {ids:['book']}]) {
    await assert.rejects(main.invoke('comic:call', 'komga_pull', params), /当前配置|参数无效/)
  }
  for (const extra of [{url:'http://other.invalid'}, {libraryId:'other'}, {apiKey:'renderer-key'}, {untrusted:true}]) {
    await assert.rejects(main.invoke('comic:call', 'komga_resolve', {id:'remote',resolution:'keep_local',...extra}), /参数无效/)
  }
  await assert.rejects(main.invoke('comic:call', 'source_context', {id:'remote'}), /主进程/)
  assert.equal(main.calls.length, 0)
  assert.equal(main.connectionCalls(), 0)

  await main.invoke('comic:call', 'komga_pull', {})
  await main.invoke('comic:call', 'komga_resolve', {id:'remote',resolution:'use_remote'})
  assert.deepEqual(JSON.parse(JSON.stringify(main.calls)), [
    {method:'komga_pull',params:main.credentials},
    {method:'komga_resolve',params:{id:'remote',resolution:'use_remote',...main.credentials}},
  ])
  assert.equal(main.connectionCalls(), 2)
})

test('batch review preview and execution use only server-owned credentials and a frozen token', async () => {
  const main = await mainProcessFixture()
  const connection = {url:'http://127.0.0.1:25600',apiKey:'fixture-key',libraryId:'library'}
  for (const [method, params] of [
    ['prepare_review_sync',{ids:['one','two']}],
    ['commit_review_sync',{token:'prepared-review-token'}],
  ]) {
    assert.doesNotThrow(() => validateCall(method, params))
    assert.doesNotThrow(() => validateCall(method, {...params,...connection}))
    for (const extra of [{url:'http://other.invalid'}, {apiKey:'renderer-key'}, {libraryId:'other'}, {metadata:{title:'injected'}}, {confirmed:true}]) {
      await assert.rejects(main.invoke('comic:call',method,{...params,...extra}), /批量审核|参数无效/)
    }
  }
  for (const [method, params] of [
    ['prepare_review_sync',{}], ['prepare_review_sync',{ids:[]}],
    ['prepare_review_sync',{ids:['one',null]}], ['prepare_review_sync',{ids:['one'],token:'injected'}],
    ['commit_review_sync',{}], ['commit_review_sync',{token:''}],
    ['commit_review_sync',{token:'prepared-review-token',ids:['other']}],
  ]) assert.throws(() => validateCall(method,params), undefined, method)
  assert.equal(main.calls.length,0)
  await main.invoke('comic:call','prepare_review_sync',{ids:['one','two']})
  await main.invoke('comic:call','commit_review_sync',{token:'prepared-review-token'})
  assert.deepEqual(JSON.parse(JSON.stringify(main.calls)),[
    {method:'prepare_review_sync',params:{ids:['one','two'],...main.credentials}},
    {method:'commit_review_sync',params:{token:'prepared-review-token',...main.credentials}},
  ])
  const broken = await mainProcessFixture({connectionError:true})
  await assert.rejects(broken.invoke('comic:call','prepare_review_sync',{ids:['one']}), /missing connection/)
  assert.equal(broken.calls.length,0)
})

for (const [method, params] of [
  ['prepare_review_sync',{ids:['one']}], ['commit_review_sync',{token:'review-token'}],
]) {
  test(`${method} remains cancellable without an ordinary response deadline`, async t => {
    const bridge = await fixture(t)
    const waiting = bridge.call(method,params)
    await eventOf(bridge,'task-progress')
    assert.equal([...bridge.pending.values()].every(request=>!request.timer),true)
    await bridge.call('cancel')
    assert.equal((await waiting).cancelled,true)
  })
}

test('approve and page inject credentials only for a remotely sourced work', async () => {
  const local = await mainProcessFixture({connectionError:true})
  const metadata = {title:'local draft'}
  await local.invoke('comic:call', 'approve', {id:'local',metadata})
  await local.invoke('comic:call', 'page', {id:'local',index:0})
  assert.equal(local.connectionCalls(), 0)
  assert.deepEqual(JSON.parse(JSON.stringify(local.calls)), [
    {method:'source_context',params:{id:'local'}},
    {method:'approve',params:{id:'local',metadata}},
    {method:'source_context',params:{id:'local'}},
    {method:'page',params:{id:'local',index:0}},
  ])

  const remote = await mainProcessFixture({contexts:{remote:{kind:'komga'}}})
  await remote.invoke('comic:call', 'approve', {id:'remote',metadata})
  await remote.invoke('comic:call', 'page', {id:'remote',index:0})
  assert.equal(remote.connectionCalls(), 2)
  assert.deepEqual(JSON.parse(JSON.stringify(remote.calls)), [
    {method:'source_context',params:{id:'remote'}},
    {method:'approve',params:{id:'remote',metadata,...remote.credentials}},
    {method:'source_context',params:{id:'remote'}},
    {method:'page',params:{id:'remote',index:0,...remote.credentials}},
  ])
  for (const method of ['approve', 'page']) {
    const params = method === 'approve' ? {id:'remote',metadata} : {id:'remote',index:0}
    await assert.rejects(remote.invoke('comic:call', method, {...params,apiKey:'renderer-key'}), /请求参数无效/)
  }
})

test('bootstrap labels the active Komga scope without exposing credentials', async () => {
  const main = await mainProcessFixture({config:{url:'  http://127.0.0.1:25600///  '}})
  const result = await main.invoke('comic:call', 'bootstrap')
  const expectedScope = createHash('sha256').update('http://127.0.0.1:25600\nfixture-library').digest('hex').slice(0, 20)
  assert.equal(result.komgaScope, expectedScope)
  assert.equal(result.komgaLibraryName, '漫画书库')
  assert.equal(JSON.stringify(result).includes(main.credentials.apiKey), false)
  assert.equal(main.connectionCalls(), 0)
  main.config.libraryId = 'another-library'
  assert.notEqual((await main.invoke('comic:call', 'bootstrap')).komgaScope, result.komgaScope)
  main.config.libraryId = ''
  assert.equal((await main.invoke('comic:call', 'bootstrap')).komgaScope, '')
  const invalidConfig = await mainProcessFixture({configError:true})
  const fallback = await invalidConfig.invoke('comic:call', 'bootstrap')
  assert.equal(fallback.komgaScope, '')
  assert.equal(fallback.komgaLibraryName, '')
  assert.deepEqual(JSON.parse(JSON.stringify(fallback.comics)), [])
})

test('remote book links are confined to the current scope and use fixed credential-free URLs', async () => {
  const serverUrl = 'http://127.0.0.1:25600'
  const remote = {serverUrl,libraryId:'fixture-library',bookId:'book/id?apiKey=not-a-secret'}
  const main = await mainProcessFixture({contexts:{remote:{kind:'komga',remote}}})
  await main.invoke('comic:open-komga-book', 'remote')
  assert.deepEqual(main.externalUrls, [serverUrl + '/book/' + encodeURIComponent(remote.bookId)])
  const opened = new URL(main.externalUrls[0])
  assert.equal(opened.origin, serverUrl)
  assert.equal(opened.search, '')
  assert.equal(opened.hash, '')
  assert.equal(main.externalUrls[0].includes(main.credentials.apiKey), false)
  assert.deepEqual(main.localPaths, [])

  for (const context of [
    {kind:'folder'},
    {kind:'komga',remote:{...remote,serverUrl:'http://other.invalid'}},
    {kind:'komga',remote:{...remote,libraryId:'other-library'}},
  ]) {
    const outside = await mainProcessFixture({contexts:{remote:context}})
    await assert.rejects(outside.invoke('comic:open-komga-book', 'remote'), /所属的 Komga/)
    assert.deepEqual(outside.externalUrls, [])
  }
})

test('remote works cannot be passed to filesystem location handlers', async () => {
  const main = await mainProcessFixture({comics:[{id:'remote',kind:'komga',source:'komga://fixture/book',output:'C:\\must-not-open'}]})
  for (const kind of ['source','output']) {
    await assert.rejects(main.invoke('comic:open-location', kind, 'remote'), /远端作品/)
  }
  assert.deepEqual(main.statPaths, [])
  assert.deepEqual(main.localPaths, [])
  const local = await mainProcessFixture({comics:[{id:'local',kind:'folder',source:'C:\\Comics\\Local'}]})
  await local.invoke('comic:open-location', 'source', 'local')
  assert.deepEqual(local.statPaths, ['C:\\Comics\\Local'])
  assert.deepEqual(local.localPaths, ['C:\\Comics\\Local'])
})

for (const method of ['komga_sync', 'komga_pull']) {
  test(`${method} has no ordinary 120-second response deadline and remains cancellable`, async t => {
    const bridge = await fixture(t)
    const scheduledDelays = []
    const realSetTimeout = global.setTimeout
    t.mock.method(global, 'setTimeout', (callback, delay, ...args) => {
      scheduledDelays.push(delay)
      return realSetTimeout(callback, delay, ...args)
    })
    const progress = eventOf(bridge, 'task-progress')
    const request = {url:'http://127.0.0.1:25600', apiKey:'fixture-key', libraryId:'library'}
    const task = bridge.call(method, method === 'komga_sync' ? {...request,ids:['book']} : request)
    assert.equal((await progress).message, method === 'komga_pull' ? '拉取中：春の旅' : '同步中：春の旅')
    assert.equal(scheduledDelays.includes(120000), false)
    assert.deepEqual(await bridge.call('cancel'), {cancelled:true})
    assert.equal(scheduledDelays.includes(120000), true)
    assert.equal((await task).cancelled, true)
    assert.equal(bridge.pending.size, 0)
  })
}

test('invalid calls fail before data is sent to the service', async t => {
  const bridge = await fixture(t)
  await assert.rejects(bridge.call('open_arbitrary_file', {path:'secret'}), /不支持/)
  assert.equal(bridge.pending.size, 0)
  assert.equal(bridge.sequence, 0)
})

test('streaming protocol handles fragmented UTF-8 and preserves Chinese/Japanese text', async t => {
  const bridge = await fixture(t)
  assert.deepEqual(await bridge.call('bootstrap'), {title:'漫画目录 / 日本語', roots:['中文路径']})
  assert.equal(bridge.pending.size, 0)
})

test('request IDs correlate concurrent out-of-order replies', async t => {
  const bridge = await fixture(t)
  const completionOrder = []
  const first = bridge.call('page', {id:'first',index:0}).then(result => { completionOrder.push('first'); return result })
  const second = bridge.call('page', {id:'second',index:1}).then(result => { completionOrder.push('second'); return result })
  const results = await Promise.all([first, second])
  assert.deepEqual(results, [{index:0,name:'first'}, {index:1,name:'second'}])
  assert.deepEqual(completionOrder, ['second','first'])
})

test('progress events and cancellation work while a long-running request is pending', async t => {
  const bridge = await fixture(t)
  const progress = eventOf(bridge, 'task-progress')
  const scan = bridge.call('scan', {roots:['中文目录']})
  assert.equal((await progress).message, '扫描中：春の旅')
  assert.deepEqual(await bridge.call('cancel'), {cancelled:true})
  assert.deepEqual(await scan, {count:1,errors:[],cancelled:true})
  assert.equal(bridge.pending.size, 0)
})

test('service validation errors reject only their own request', async t => {
  const bridge = await fixture(t)
  await assert.rejects(bridge.call('save', {id:'book',metadata:{}}), /作者信息不完整/)
  assert.equal((await bridge.call('bootstrap')).title, '漫画目录 / 日本語')
})

test('an unexpected exit rejects pending requests and reports diagnostics', async t => {
  const bridge = await fixture(t)
  const progress = eventOf(bridge, 'task-progress')
  const scanRejection = assert.rejects(bridge.call('scan', {roots:['hold']}), /文件服务已退出/)
  await progress
  const exited = eventOf(bridge, 'service-exit')
  const crashRejection = assert.rejects(bridge.call('import', {paths:['crash']}), /文件服务已退出/)
  await Promise.all([scanRejection, crashRejection])
  const diagnostic = await exited
  assert.match(diagnostic.message, /23/)
  assert.match(diagnostic.message, /storage disconnected/)
  assert.equal(bridge.pending.size, 0)
  await assert.rejects(bridge.call('bootstrap'), /文件服务不可用/)
})

test('malformed stdout rejects every in-flight request rather than leaving promises unresolved', async t => {
  const bridge = await fixture(t)
  const progress = eventOf(bridge, 'task-progress')
  const pending = assert.rejects(bridge.call('scan', {roots:['hold']}), /无效数据/)
  await progress
  const invalid = assert.rejects(bridge.call('import', {paths:['malformed']}), /无效数据/)
  await Promise.all([pending, invalid])
  assert.equal(bridge.pending.size, 0)
})
