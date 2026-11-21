'use strict'

const fs = require('node:fs/promises')
const path = require('node:path')
const { randomUUID } = require('node:crypto')

const PROVIDERS = ['dmm', 'nhentai']
const KEY = /^[A-Za-z0-9_.=+/~-]{1,512}$/
const AFFILIATE_ID = /^[A-Za-z0-9_-]{1,240}-99[0-9]$/
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/

function configuration(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function checkProvider(provider) {
  if (!PROVIDERS.includes(provider)) throw new Error('请选择支持保存凭据的站点。')
}

function cleanCredential(value, pattern, name) {
  if (typeof value !== 'string' || value.length > 512 || /[\x00-\x1f\x7f]/.test(value) || !pattern.test(value.trim())) {
    throw new Error('请填写有效的 ' + name + '。')
  }
  return value.trim()
}

function credentialInput(input) {
  if (!configuration(input)) throw new Error('站点凭据格式无效。')
  const { provider, apiKey, affiliateId } = input
  checkProvider(provider)
  const fields = provider === 'dmm' ? ['provider', 'apiKey', 'affiliateId'] : ['provider', 'apiKey']
  if (Object.keys(input).some(field => !fields.includes(field))) throw new Error('站点凭据格式无效。')
  if (provider === 'dmm') {
    return {
      apiKey: cleanCredential(apiKey, KEY, 'DMM/FANZA API ID'),
      affiliateId: cleanCredential(affiliateId, AFFILIATE_ID, 'DMM/FANZA API 专用 Affiliate ID（末尾为 990–999）'),
    }
  }
  return { apiKey: cleanCredential(apiKey, KEY, 'nhentai API Key') }
}

class SiteConfigStore {
  constructor(dataDir, safeStorage) {
    this.file = path.join(dataDir, 'site-credentials.json')
    this.safeStorage = safeStorage
    this.pending = Promise.resolve()
  }

  async read() {
    let raw
    try {
      raw = await fs.readFile(this.file, 'utf8')
    } catch (error) {
      if (error.code === 'ENOENT') return { version: 1 }
      throw new Error('无法读取站点凭据，请检查本机资料目录的访问权限。')
    }

    try {
      const value = JSON.parse(raw)
      if (!configuration(value) || value.version !== 1
          || Object.keys(value).some(field => field !== 'version' && !PROVIDERS.includes(field))) throw new Error()
      return value
    } catch {
      throw new Error('本机站点凭据配置无法读取，请检查站点凭据配置文件。')
    }
  }

  async decrypt(provider, ciphertext) {
    if (typeof ciphertext !== 'string' || ciphertext.length > 16384 || !BASE64.test(ciphertext)) return undefined
    try {
      const { result } = await this.safeStorage.decryptStringAsync(Buffer.from(ciphertext, 'base64'))
      const decoded = JSON.parse(result)
      if (decoded?.provider === provider) return credentialInput(decoded)
    } catch {
      // Unreadable credentials are treated as unset and can be replaced in settings.
    }
    return undefined
  }

  async status(value) {
    const entries = await Promise.all(PROVIDERS.map(async provider => [
      provider, { configured: !!await this.decrypt(provider, value[provider]) },
    ]))
    return Object.fromEntries(entries)
  }

  async get() {
    await this.pending
    return this.status(await this.read())
  }

  async credentials(providers) {
    const selected = PROVIDERS.filter(provider => providers.includes(provider))
    if (!selected.length) return {}
    await this.pending
    const saved = await this.read().catch(() => null)
    if (!saved) return {}
    const credentials = {}
    for (const provider of selected) {
      const entry = await this.decrypt(provider, saved[provider])
      if (entry) credentials[provider] = entry
    }
    return credentials
  }

  change(update) {
    const operation = this.pending.then(async () => {
      const current = await this.read()
      await update(current)
      await this.write(current)
      return this.status(current)
    })
    this.pending = operation.catch(() => {})
    return operation
  }

  save(input) {
    return this.change(async current => {
      const credentials = credentialInput(input)
      const { provider } = input
      try {
        if (!await this.safeStorage.isAsyncEncryptionAvailable()
            || (process.platform === 'linux' && this.safeStorage.getSelectedStorageBackend?.() === 'basic_text')) {
          throw new Error()
        }
        const encrypted = await this.safeStorage.encryptStringAsync(JSON.stringify({ provider, ...credentials }))
        current[provider] = encrypted.toString('base64')
      } catch {
        throw new Error('系统密钥保护暂不可用，站点凭据尚未保存。')
      }
    })
  }

  clear(provider) {
    return this.change(current => {
      checkProvider(provider)
      delete current[provider]
    })
  }

  async write(value) {
    const temporary = this.file + '.' + randomUUID() + '.partial'
    try {
      await fs.mkdir(path.dirname(this.file), { recursive: true })
      await fs.writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' })
      await fs.rename(temporary, this.file)
    } catch {
      throw new Error('无法保存站点凭据，请检查本机资料目录的访问权限。')
    } finally {
      await fs.unlink(temporary).catch(() => {})
    }
  }
}

module.exports = { SiteConfigStore }
