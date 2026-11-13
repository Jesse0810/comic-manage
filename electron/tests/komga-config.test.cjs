'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { createCipheriv, createDecipheriv, randomBytes } = require('node:crypto')
const { KomgaConfigStore, normalizeKomgaUrl } = require('../komga-config.cjs')

function secureStorage() {
  const key = randomBytes(32)
  return {
    isAsyncEncryptionAvailable: async () => true,
    encryptStringAsync: async text => {
      const iv = randomBytes(12)
      const cipher = createCipheriv('aes-256-gcm', key, iv)
      const encrypted = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()])
      return Buffer.concat([iv, cipher.getAuthTag(), encrypted])
    },
    decryptStringAsync: async data => {
      const decipher = createDecipheriv('aes-256-gcm', key, data.subarray(0, 12))
      decipher.setAuthTag(data.subarray(12, 28))
      return { result: Buffer.concat([decipher.update(data.subarray(28)), decipher.final()]).toString('utf8'), shouldReEncrypt: false }
    },
  }
}

async function fixture(t) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'komga-config-'))
  assert.ok(path.resolve(folder).startsWith(path.join(path.resolve(os.tmpdir()), 'komga-config-')))
  t.after(() => fs.rm(folder, { recursive: true, force: true }))
  const storage = secureStorage()
  return { folder, storage, store: new KomgaConfigStore(folder, storage) }
}

test('Komga secrets persist encrypted and only a saved-key flag returns to the renderer', async t => {
  const { folder, storage, store } = await fixture(t)
  const result = await store.save({ url: 'HTTP://localhost:25600/komga/', apiKey: 'private-key', libraryId: 'lib', libraryName: '书库' })
  assert.deepEqual(result, { url: 'http://localhost:25600/komga', libraryId: 'lib', libraryName: '书库', hasApiKey: true, autoSyncEnabled: true })
  const contents = await fs.readFile(store.file, 'utf8')
  assert.equal(contents.includes('private-key'), false)
  assert.equal(JSON.stringify(await store.get()).includes('private-key'), false)
  const reopened = new KomgaConfigStore(folder, storage)
  assert.deepEqual(await reopened.connection(), { url: result.url, libraryId: 'lib', apiKey: 'private-key' })
})

test('changing a Komga URL cannot forward an existing saved API key', async t => {
  const { store } = await fixture(t)
  await store.save({ url: 'http://localhost:25600', apiKey: 'old-key', libraryId: 'one' })
  await assert.rejects(store.credentials({ url: 'http://localhost:25601' }), /重新填写/)
  await assert.rejects(store.save({ url: 'http://localhost:25601' }), /重新填写/)
  assert.equal((await store.get()).url, 'http://localhost:25600')
  await store.save({ url: 'http://localhost:25601', apiKey: 'new-key', libraryId: 'two' })
  assert.equal((await store.connection()).apiKey, 'new-key')
})

test('blank keys preserve the credential, explicit clear removes it, and failed encryption preserves old settings', async t => {
  const { store, storage } = await fixture(t)
  await store.save({ url: 'http://localhost', apiKey: 'key', libraryId: 'one' })
  await store.save({ url: 'http://localhost/', apiKey: '', libraryId: 'two' })
  assert.equal((await store.connection()).apiKey, 'key')
  storage.encryptStringAsync = async () => { throw new Error('Encryption unavailable') }
  await assert.rejects(store.save({ url: 'http://localhost', apiKey: 'replacement' }), /未能保护/)
  assert.equal((await store.connection()).apiKey, 'key')
  await store.save({ url: 'http://localhost', clearKey: true })
  assert.equal((await store.get()).hasApiKey, false)
  await assert.rejects(store.connection(), /配置 Komga API Key/)
})

test('configuration saves serialize and invalid URL or header inputs are rejected', async t => {
  const { store } = await fixture(t)
  await Promise.all([
    store.save({ url: 'http://localhost', apiKey: 'key', libraryId: 'one' }),
    store.save({ url: 'http://localhost/', libraryId: 'two' }),
  ])
  assert.equal((await store.connection()).libraryId, 'two')
  for (const url of ['file:///tmp', 'http://user:password@localhost', 'https://localhost/?', 'https://localhost/#', 'https://localhost/\n']) {
    assert.throws(() => normalizeKomgaUrl(url))
  }
  await assert.rejects(store.credentials({ url: 'http://localhost', apiKey: 'key\r\nOther: injected' }), /有效/)
  await assert.rejects(store.credentials({ url: 'http://localhost', clearKey: true }), /清除/)
})

test('automatic synchronization preference persists independently of encrypted credentials', async t => {
  const { folder, storage, store } = await fixture(t)
  await store.save({url:'http://localhost',apiKey:'secret',libraryId:'actual-id',autoSyncEnabled:false})
  assert.equal((await new KomgaConfigStore(folder, storage).get()).autoSyncEnabled, false)
  await store.save({url:'http://localhost',libraryName:'保留关闭'})
  assert.equal((await store.get()).autoSyncEnabled, false)
  await store.save({url:'http://localhost',autoSyncEnabled:true})
  assert.equal((await store.get()).autoSyncEnabled, true)
  assert.equal((await store.connection()).apiKey, 'secret')
  for (const value of [null, 'yes', 1, {}]) await assert.rejects(store.save({url:'http://localhost',autoSyncEnabled:value}), /选项无效/)
})
