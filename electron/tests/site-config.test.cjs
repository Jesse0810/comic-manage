'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { createCipheriv, createDecipheriv, randomBytes } = require('node:crypto')
const { SiteConfigStore } = require('../site-config.cjs')

const dmm = { provider: 'dmm', apiKey: 'dmm-fixture-key', affiliateId: 'sample-affiliate-990' }
const nhentai = { provider: 'nhentai', apiKey: 'nh-fixture-token' }
const unset = { dmm: { configured: false }, nhentai: { configured: false } }

async function fixture(t) {
  const prefix = path.join(path.resolve(os.tmpdir()), 'comic-sites-test-')
  const folder = await fs.mkdtemp(prefix)
  assert.ok(path.resolve(folder).startsWith(prefix))
  t.after(() => fs.rm(folder, { recursive: true, force: true }))

  const key = randomBytes(32)
  const storage = {
    isAsyncEncryptionAvailable: async () => true,
    encryptStringAsync: async plaintext => {
      const iv = randomBytes(12)
      const cipher = createCipheriv('aes-256-gcm', key, iv)
      const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()])
      return Buffer.concat([iv, cipher.getAuthTag(), ciphertext])
    },
    decryptStringAsync: async value => {
      const decipher = createDecipheriv('aes-256-gcm', key, value.subarray(0, 12))
      decipher.setAuthTag(value.subarray(12, 28))
      return { result: Buffer.concat([decipher.update(value.subarray(28)), decipher.final()]).toString('utf8') }
    },
  }
  return { folder, storage, store: new SiteConfigStore(folder, storage) }
}

test('saved credentials survive reload while settings only return configured flags', async t => {
  const { folder, storage, store } = await fixture(t)
  assert.deepEqual(await store.get(), unset)
  await Promise.all([store.save(dmm), store.save(nhentai)])
  const reopened = new SiteConfigStore(folder, storage)
  assert.deepEqual(await reopened.get(), { dmm: { configured: true }, nhentai: { configured: true } })

  const raw = await fs.readFile(store.file, 'utf8')
  for (const secret of [dmm.apiKey, dmm.affiliateId, nhentai.apiKey]) assert.ok(!raw.includes(secret))
  assert.deepEqual(await reopened.credentials(['dmm']), {
    dmm: { apiKey: dmm.apiKey, affiliateId: dmm.affiliateId },
  })
  assert.deepEqual(await reopened.credentials(['dlsite', 'nhentai', 'ehentai']), { nhentai: { apiKey: nhentai.apiKey } })
  assert.deepEqual(await reopened.credentials(['dlsite', 'ehentai']), {})
  assert.deepEqual(await fs.readdir(folder), ['site-credentials.json'])
})

test('replacement and removal affect only the named provider', async t => {
  const { store } = await fixture(t)
  await store.save(dmm)
  await store.save(nhentai)
  await store.save({ provider: 'dmm', apiKey: ' replacement-key ', affiliateId: 'changed-999' })
  assert.deepEqual(await store.credentials(['dmm', 'nhentai']), {
    dmm: { apiKey: 'replacement-key', affiliateId: 'changed-999' }, nhentai: { apiKey: nhentai.apiKey },
  })
  assert.deepEqual(await store.clear('dmm'), { dmm: { configured: false }, nhentai: { configured: true } })
  assert.deepEqual(await store.credentials(['dmm', 'nhentai']), { nhentai: { apiKey: nhentai.apiKey } })
  assert.deepEqual(await store.clear('nhentai'), unset)
})

test('malformed requests never replace saved credentials', async t => {
  const { store } = await fixture(t)
  await store.save(dmm)
  const previous = await fs.readFile(store.file, 'utf8')
  for (const invalid of [
    null, [], {},
    { ...dmm, affiliateId: 'wrong-100' },
    { ...dmm, apiKey: 'key\r\nExtra: header' },
    { ...dmm, apiKey: '' },
    { ...dmm, base_url: 'https://third-party.test/' },
    { ...nhentai, affiliateId: dmm.affiliateId },
    { ...nhentai, apiKey: 'bad key' },
    { ...nhentai, apiKey: 'a'.repeat(513) },
    { ...nhentai, provider: 'dlsite' },
  ]) {
    await assert.rejects(store.save(invalid))
  }
  await assert.rejects(store.clear('ehentai'))
  assert.equal(await fs.readFile(store.file, 'utf8'), previous)
})

test('failed system encryption preserves the previously saved settings and redacts errors', async t => {
  const { storage, store } = await fixture(t)
  await store.save(dmm)
  const first = await fs.readFile(store.file, 'utf8')
  storage.isAsyncEncryptionAvailable = async () => false
  await assert.rejects(store.save(nhentai), /系统密钥保护/)
  storage.isAsyncEncryptionAvailable = async () => true
  storage.encryptStringAsync = async () => { throw new Error(nhentai.apiKey) }
  await assert.rejects(store.save(nhentai), error => /系统密钥保护/.test(error.message) && !error.message.includes(nhentai.apiKey))
  assert.equal(await fs.readFile(store.file, 'utf8'), first)
  assert.deepEqual(await store.credentials(['dmm', 'nhentai']), { dmm: { apiKey: dmm.apiKey, affiliateId: dmm.affiliateId } })
})

test('an unreadable entry can be replaced and does not block another site', async t => {
  const { store } = await fixture(t)
  await store.save(dmm)
  await store.save(nhentai)
  const saved = JSON.parse(await fs.readFile(store.file, 'utf8'))
  saved.dmm = saved.nhentai
  await fs.writeFile(store.file, JSON.stringify(saved))

  assert.deepEqual(await store.get(), { dmm: { configured: false }, nhentai: { configured: true } })
  assert.deepEqual(await store.credentials(['dmm', 'nhentai']), { nhentai: { apiKey: nhentai.apiKey } })
  await store.save(dmm)
  assert.equal((await store.get()).dmm.configured, true)
})

test('a broken settings file does not block public searches or leak its contents', async t => {
  const { store } = await fixture(t)
  const raw = '{"version":100,"nhentai":"sensitive-fixture-value"}'
  await fs.writeFile(store.file, raw)
  assert.deepEqual(await store.credentials(['dmm', 'nhentai']), {})
  await assert.rejects(store.get(), error => /无法读取/.test(error.message) && !error.message.includes('sensitive-fixture-value'))
  await assert.rejects(store.save(nhentai), /无法读取/)
  assert.equal(await fs.readFile(store.file, 'utf8'), raw)
})
