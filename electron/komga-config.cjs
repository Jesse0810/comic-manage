'use strict'

const fs = require('node:fs/promises')
const path = require('node:path')
const { randomUUID } = require('node:crypto')

function normalizeKomgaUrl(value) {
  if (typeof value !== 'string' || !value.trim() || value.length > 4096 || /[\r\n\0]/.test(value)) throw new Error('请填写 Komga 地址。')
  let url
  try { url = new URL(value.trim()) } catch { throw new Error('Komga 地址格式无效。') }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.href.includes('?') || url.href.includes('#')) {
    throw new Error('Komga 地址请使用 http 或 https，并去掉账号、查询参数和片段。')
  }
  return url.href.replace(/\/+$/, '')
}

function apiKey(value) {
  if (typeof value !== 'string' || !value.trim() || value.length > 8192 || /[\r\n\0]/.test(value)) {
    throw new Error('请填写有效的 Komga API Key。')
  }
  return value.trim()
}

function validateInput(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)
      || Object.keys(input).some(key => !['url', 'apiKey', 'clearKey', 'libraryId', 'libraryName'].includes(key))) {
    throw new Error('Komga 配置格式无效。')
  }
  if (input.apiKey !== undefined && typeof input.apiKey !== 'string') throw new Error('API Key 格式无效。')
  if (input.clearKey !== undefined && typeof input.clearKey !== 'boolean') throw new Error('清除密钥选项无效。')
  for (const key of ['libraryId', 'libraryName']) {
    if (input[key] !== undefined && (typeof input[key] !== 'string' || input[key].length > 4096 || /[\r\n\0]/.test(input[key]))) {
      throw new Error('Komga 书库信息无效。')
    }
  }
}

class KomgaConfigStore {
  constructor(dataDir, safeStorage) {
    this.file = path.join(dataDir, 'komga-connection.json')
    this.safeStorage = safeStorage
    this.pending = Promise.resolve()
  }

  async read() {
    try {
      const value = JSON.parse(await fs.readFile(this.file, 'utf8'))
      if (value.version !== 1 || typeof value.url !== 'string'
          || typeof value.apiKeyEncrypted !== 'string') throw new SyntaxError('Invalid connection file')
      return value
    } catch (error) {
      if (error.code === 'ENOENT' || error instanceof SyntaxError) {
        return { version: 1, url: '', libraryId: '', libraryName: '', apiKeyEncrypted: '' }
      }
      throw new Error('无法读取 Komga 配置，请检查本机资料目录的访问权限。')
    }
  }

  publicConfig(value) {
    return {
      url: value.url,
      libraryId: typeof value.libraryId === 'string' ? value.libraryId : '',
      libraryName: typeof value.libraryName === 'string' ? value.libraryName : '',
      hasApiKey: !!value.apiKeyEncrypted,
    }
  }

  get() { return this.read().then(value => this.publicConfig(value)) }

  async decrypt(value) {
    if (!value.apiKeyEncrypted) throw new Error('请先配置 Komga API Key。')
    try {
      let decrypted = await this.safeStorage.decryptStringAsync(Buffer.from(value.apiKeyEncrypted, 'base64'))
      if (decrypted.shouldReEncrypt) {
        decrypted = await this.safeStorage.decryptStringAsync(Buffer.from(value.apiKeyEncrypted, 'base64'))
      }
      return apiKey(decrypted.result)
    } catch {
      throw new Error('无法解密已保存的 API Key，请重新填写并保存。')
    }
  }

  async credentials(input) {
    validateInput(input)
    const url = normalizeKomgaUrl(input.url)
    if (input.clearKey) throw new Error('密钥已选择清除，请重新填写 API Key 后连接。')
    if (input.apiKey?.trim()) return { url, apiKey: apiKey(input.apiKey) }
    const saved = await this.read()
    if (url !== saved.url) throw new Error('更换 Komga 地址后请重新填写 API Key。')
    return { url, apiKey: await this.decrypt(saved) }
  }

  async connection() {
    const saved = await this.read()
    const credentials = await this.credentials({ url: saved.url })
    if (!saved.libraryId) throw new Error('请先选择 Komga 书库。')
    return { ...credentials, libraryId: saved.libraryId }
  }

  save(input) {
    const operation = this.pending.then(() => this.saveCurrent(input))
    this.pending = operation.catch(() => {})
    return operation
  }

  async saveCurrent(input) {
    validateInput(input)
    const url = normalizeKomgaUrl(input.url)
    const previous = await this.read()
    let encrypted = previous.apiKeyEncrypted
    if (input.clearKey) {
      encrypted = ''
    } else if (input.apiKey?.trim()) {
      if (!(await this.safeStorage.isAsyncEncryptionAvailable())) {
        throw new Error('系统密钥保护暂不可用，请稍后重试。')
      }
      try {
        encrypted = (await this.safeStorage.encryptStringAsync(apiKey(input.apiKey))).toString('base64')
      } catch {
        throw new Error('系统未能保护 API Key，请稍后重试。')
      }
    } else if (url !== previous.url) {
      throw new Error('更换 Komga 地址后请重新填写 API Key。')
    }
    const value = {
      version: 1, url,
      libraryId: input.libraryId ?? (url === previous.url ? previous.libraryId : ''),
      libraryName: input.libraryName ?? (url === previous.url ? previous.libraryName : ''),
      apiKeyEncrypted: encrypted,
    }
    await fs.mkdir(path.dirname(this.file), { recursive: true })
    const temporary = this.file + '.' + randomUUID() + '.partial'
    try {
      await fs.writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' })
      await fs.rename(temporary, this.file)
    } catch {
      throw new Error('无法保存 Komga 配置，请检查本机资料目录的访问权限。')
    } finally {
      await fs.unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error })
    }
    return this.publicConfig(value)
  }
}

module.exports = { KomgaConfigStore, normalizeKomgaUrl }
