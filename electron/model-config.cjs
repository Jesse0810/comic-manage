'use strict'

const fs = require('node:fs/promises')
const path = require('node:path')
const { randomUUID } = require('node:crypto')

const PROVIDERS = new Set(['openai', 'openai-responses', 'ollama', 'anthropic', 'gemini'])
const PROFILE_ID = /^[a-f0-9-]{36}$/
const MAX_PROFILES = 32
const PUBLIC_FIELDS = ['id', 'name', 'provider', 'base_url', 'model']

function record(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function clean(value, max, label) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error(`请填写有效的${label}。`)
  }
  return value.trim()
}

function cleanKey(value) {
  if (typeof value !== 'string' || !/^[\x20-\x7e]{1,4096}$/.test(value)) throw new Error('模型 API Key 格式无效。')
  return value.trim()
}

function checkId(id) {
  if (typeof id !== 'string' || !PROFILE_ID.test(id)) throw new Error('请选择有效的模型配置。')
  return id
}

function profileInput(value) {
  if (!record(value) || Object.keys(value).some(key => !PUBLIC_FIELDS.includes(key))) {
    throw new Error('模型配置格式无效。')
  }
  const name = clean(value.name, 120, '配置名称')
  if (!PROVIDERS.has(value.provider)) throw new Error('请选择支持的模型协议。')
  const address = clean(value.base_url, 2048, '接口地址')
  let url
  try { url = new URL(address) } catch { throw new Error('模型接口地址格式无效。') }
  if (!/^https?:\/\//i.test(address) || /[\s\\]/.test(address)
      || !['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || address.includes('?') || address.includes('#') || !url.hostname) {
    throw new Error('模型接口地址须以 http 或 https 开头，且不含账号、查询参数或片段。')
  }
  return {
    id: value.id === undefined || value.id === '' ? '' : checkId(value.id),
    name,
    provider: value.provider,
    base_url: url.href.replace(/\/+$/, ''),
    model: clean(value.model, 256, '模型 ID'),
  }
}

function settingsInput(value) {
  if (!record(value) || Object.keys(value).some(key => !['profile', 'api_key', 'clear_key', 'make_default'].includes(key))
      || (value.api_key !== undefined && typeof value.api_key !== 'string')
      || (value.clear_key !== undefined && typeof value.clear_key !== 'boolean')
      || (value.make_default !== undefined && typeof value.make_default !== 'boolean')) {
    throw new Error('模型配置请求格式无效。')
  }
  const key = value.api_key?.trim() ? cleanKey(value.api_key) : ''
  if (key && value.clear_key) throw new Error('请先取消清除密钥选项，再填写新密钥。')
  return { profile: profileInput(value.profile), key, clear: value.clear_key === true, makeDefault: value.make_default === true }
}

function publicSettings(value) {
  const profiles = value.profiles.map(({ encrypted_key, ...profile }) => ({ ...profile, has_key: !!encrypted_key }))
  return { profiles, default_id: value.default_id }
}

class ModelConfigStore {
  constructor(dataDir, safeStorage) {
    this.file = path.join(dataDir, 'model-connections.json')
    this.safeStorage = safeStorage
    this.pending = Promise.resolve()
  }

  async read() {
    let raw
    try { raw = await fs.readFile(this.file, 'utf8') } catch (error) {
      if (error.code === 'ENOENT') return { version: 1, profiles: [], default_id: '' }
      throw new Error('无法读取模型配置，请检查本机资料目录的访问权限。')
    }
    try {
      const value = JSON.parse(raw)
      if (value.version !== 1 || !Array.isArray(value.profiles) || value.profiles.length > MAX_PROFILES) throw new Error()
      const profiles = value.profiles.map(({ encrypted_key, ...saved }) => {
        const profile = profileInput(saved)
        if (!profile.id || typeof encrypted_key !== 'string' || encrypted_key.length > 65536
            || !/^[A-Za-z0-9+/]*={0,2}$/.test(encrypted_key)) throw new Error()
        return { ...profile, encrypted_key }
      })
      const ids = new Set(profiles.map(profile => profile.id))
      if (ids.size !== profiles.length || (profiles.length ? !ids.has(value.default_id) : value.default_id !== '')) throw new Error()
      return { version: 1, profiles, default_id: value.default_id }
    } catch {
      throw new Error('本机模型配置无法读取，请检查模型配置文件。')
    }
  }

  async get() {
    return publicSettings(await this.read())
  }

  async decrypt(encrypted) {
    if (!encrypted) return ''
    try {
      const key = await this.safeStorage.decryptStringAsync(Buffer.from(encrypted, 'base64'))
      return cleanKey(key.result)
    } catch {
      throw new Error('无法解密已保存的模型 API Key，请重新填写并保存。')
    }
  }

  retainedKey(previous, input) {
    if (!previous?.encrypted_key || input.clear) return ''
    if (previous.base_url !== input.profile.base_url || previous.provider !== input.profile.provider) {
      throw new Error('更换接口地址或协议后，请输入新密钥或选择清除原密钥。')
    }
    return previous.encrypted_key
  }

  async testCredentials(value) {
    const input = settingsInput(value)
    const current = await this.read()
    const previous = current.profiles.find(profile => profile.id === input.profile.id)
    if (input.profile.id && !previous) throw new Error('该模型配置已被删除。')
    const api_key = input.key || await this.decrypt(this.retainedKey(previous, input))
    return { profile: this.requestProfile(input.profile), api_key }
  }

  async connection(id) {
    const current = await this.read()
    const stored = current.profiles.find(profile => profile.id === checkId(id))
    if (!stored) throw new Error('该模型配置已被删除。')
    return { profile: this.requestProfile(stored), api_key: await this.decrypt(stored.encrypted_key), name: stored.name }
  }

  requestProfile({ provider, base_url, model }) {
    return { provider, base_url, model }
  }

  change(update) {
    const operation = this.pending.then(async () => {
      const value = await this.read()
      await update(value)
      await this.write(value)
      return publicSettings(value)
    })
    this.pending = operation.catch(() => {})
    return operation
  }

  save(value) {
    return this.change(async current => {
      const input = settingsInput(value)
      const profiles = current.profiles
      const previous = profiles.find(profile => profile.id === input.profile.id)
      if (input.profile.id && !previous) throw new Error('该模型配置已被删除。')
      if (!previous && profiles.length >= MAX_PROFILES) throw new Error('最多保存 32 个模型配置。')
      let encrypted_key
      if (input.key) {
        try {
          if (!(await this.safeStorage.isAsyncEncryptionAvailable())) throw new Error()
          encrypted_key = (await this.safeStorage.encryptStringAsync(input.key)).toString('base64')
        } catch {
          throw new Error('系统密钥保护暂不可用，模型配置尚未保存。')
        }
      } else {
        encrypted_key = this.retainedKey(previous, input)
      }
      const profile = { ...input.profile, id: previous?.id || randomUUID(), encrypted_key }
      if (previous) profiles[profiles.indexOf(previous)] = profile
      else profiles.push(profile)
      if (!current.default_id || input.makeDefault) current.default_id = profile.id
    })
  }

  delete(id) {
    return this.change(current => {
      checkId(id)
      const index = current.profiles.findIndex(profile => profile.id === id)
      if (index < 0) throw new Error('该模型配置已被删除。')
      current.profiles.splice(index, 1)
      if (current.default_id === id) current.default_id = current.profiles[0]?.id || ''
    })
  }

  setDefault(id) {
    return this.change(current => {
      checkId(id)
      if (!current.profiles.some(profile => profile.id === id)) throw new Error('该模型配置已被删除。')
      current.default_id = id
    })
  }

  async write(value) {
    const temporary = this.file + '.' + randomUUID() + '.partial'
    try {
      await fs.mkdir(path.dirname(this.file), { recursive: true })
      await fs.writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' })
      await fs.rename(temporary, this.file)
    } catch {
      throw new Error('无法保存模型配置，请检查本机资料目录的访问权限。')
    } finally {
      await fs.unlink(temporary).catch(() => {})
    }
  }
}

module.exports = { ModelConfigStore }
