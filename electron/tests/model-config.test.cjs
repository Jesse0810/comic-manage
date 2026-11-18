'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { ModelConfigStore } = require('../model-config.cjs')

async function fixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'comic-model-config-'))
  t.after(() => fs.rm(dir, { recursive: true, force: true }))
  const storage = {
    isAsyncEncryptionAvailable: async () => true,
    encryptStringAsync: async text => Buffer.from([...text].reverse().join(''), 'utf8'),
    decryptStringAsync: async buffer => ({ result: [...buffer.toString('utf8')].reverse().join('') }),
  }
  return { dir, storage, store: new ModelConfigStore(dir, storage) }
}

const cloud = { name: '共享模型', provider: 'openai', base_url: 'https://example.test/v1/', model: 'text-model' }
const local = { name: '本机模型', provider: 'ollama', base_url: 'http://127.0.0.1:11434', model: 'small:latest' }

test('multiple profiles persist; only the selected credential leaves the store', async t => {
  const { dir, storage, store } = await fixture(t)
  const original = await store.save({ profile: cloud, api_key: 'fake-model-api-key' })
  const firstId = original.default_id
  assert.deepEqual(original.profiles, [{ ...cloud, base_url: 'https://example.test/v1', id: firstId, has_key: true }])
  const saved = await store.save({ profile: local, make_default: true })
  const secondId = saved.default_id
  assert.notEqual(secondId, firstId)
  assert.equal(saved.profiles[1].has_key, false)
  assert(!JSON.stringify(saved).includes('fake-model-api-key'))
  const disk = await fs.readFile(path.join(dir, 'model-connections.json'), 'utf8')
  assert(!disk.includes('fake-model-api-key'))
  assert.match(disk, /encrypted_key/)
  const restored = new ModelConfigStore(dir, storage)
  assert.deepEqual(await restored.get(), saved)
  assert.equal((await restored.connection(firstId)).api_key, 'fake-model-api-key')
  assert.equal((await restored.connection(secondId)).api_key, '')
  assert.equal((await restored.setDefault(firstId)).default_id, firstId)
  assert.equal((await restored.delete(firstId)).default_id, secondId)
  await assert.rejects(restored.connection(firstId), /已被删除/)
  assert.deepEqual(await restored.delete(secondId), { profiles: [], default_id: '' })
})

test('saved keys follow the same endpoint and protocol, and test edits stay transient', async t => {
  const { store } = await fixture(t)
  const { default_id: id } = await store.save({ profile: cloud, api_key: 'original-test-only-key' })
  const edited = { ...cloud, id, model: 'text-model-v2' }
  const testResult = await store.testCredentials({ profile: edited })
  assert.deepEqual(testResult, {
    api_key: 'original-test-only-key',
    profile: { provider: 'openai', base_url: 'https://example.test/v1', model: 'text-model-v2' },
  })
  assert.equal((await store.get()).profiles[0].model, 'text-model')
  for (const profile of [{ ...edited, provider: 'openai-responses' }, { ...edited, base_url: 'https://other.test/v1' }]) {
    await assert.rejects(store.testCredentials({ profile }), /更换接口地址或协议/)
    await assert.rejects(store.save({ profile }), /更换接口地址或协议/)
  }
  const target = { ...edited, base_url: 'https://other.test/v1' }
  assert.equal((await store.testCredentials({ profile: target, api_key: 'replacement-key' })).api_key, 'replacement-key')
  assert.equal((await store.connection(id)).api_key, 'original-test-only-key')
  assert.equal((await store.save({ profile: target, clear_key: true })).profiles[0].has_key, false)
  assert.equal((await store.connection(id)).api_key, '')
})

test('concurrent updates serialize and invalid configurations do not replace good values', async t => {
  const { dir, storage, store } = await fixture(t)
  await Promise.all([store.save({ profile: cloud }), store.save({ profile: local })])
  const previous = await store.get()
  assert.equal(previous.profiles.length, 2)
  for (const base_url of ['file:///C:/data', 'https://user:pass@example.test/v1', 'https://example.test/v1?k=x', 'https://example.test/#']) {
    await assert.rejects(store.save({ profile: { ...cloud, base_url } }), /接口地址/)
  }
  storage.isAsyncEncryptionAvailable = async () => false
  await assert.rejects(store.save({ profile: cloud, api_key: 'cannot-save' }), /系统密钥保护/)
  assert.deepEqual(await store.get(), previous)
  await fs.writeFile(path.join(dir, 'model-connections.json'), '{')
  await assert.rejects(store.save({ profile: local }), /本机模型配置无法读取/)
  assert.equal(await fs.readFile(path.join(dir, 'model-connections.json'), 'utf8'), '{')
})
