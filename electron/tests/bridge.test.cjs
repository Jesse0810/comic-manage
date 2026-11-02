'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { once } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { ServiceBridge, validateCall } = require(process.env.COMIC_MANAGE_BRIDGE_MODULE || '../bridge.cjs')

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

test('request boundary rejects arbitrary methods, malformed records and unsafe numeric indexes', () => {
  const invalid = [
    ['shell:execute', {command:'anything'}],
    ['page', {id:'book',index:-1}],
    ['page', {id:'book',index:1.5}],
    ['page', {id:'book',index:Number.MAX_SAFE_INTEGER + 1}],
    ['save', {id:'book',metadata:[]}],
    ['approve', {id:'',metadata:{}}],
    ['xml', {metadata:{},pageCount:0}],
    ['plan', {ids:['book',null],outputRoot:'C:\\Comics'}],
    ['import', {paths:[42]}],
    ['export', {token:''}],
    ['bootstrap', null],
    ['scan', []],
    ['save', {id:'book',metadata:{summary:'x'.repeat(4 * 1024 * 1024)}}],
  ]
  for (const [method, params] of invalid) assert.throws(() => validateCall(method, params), undefined, method)
})

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
