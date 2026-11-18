'use strict'

const fs = require('node:fs/promises')
const path = require('node:path')
const http = require('node:http')
const { createHash, createPublicKey, randomBytes, randomUUID, timingSafeEqual, verify } = require('node:crypto')

const ISSUER = 'https://auth.openai.com'
const RESOURCE = 'https://api.openai.com/v1'
const AUTHORIZE = ISSUER + '/api/accounts/authorize'
const TOKEN = ISSUER + '/api/accounts/oauth/token'
const REVOKE = ISSUER + '/api/accounts/oauth/revoke'
const JWKS = ISSUER + '/.well-known/jwks.json'
const CHATGPT_USAGE_URL = 'https://chatgpt.com/settings/usage'
const CALLBACK = '/auth/callback'
const SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
const MAX_JSON = 512 * 1024

class AuthError extends Error {
  constructor(message, code = 'CHATGPT_AUTH') {
    super(message)
    this.code = code
  }
}

function text(value, length = 256) {
  return typeof value === 'string' && value.length <= length && value.trim() && !/[\x00-\x1f\x7f]/.test(value)
    ? value.trim() : ''
}

function token(value) {
  return typeof value === 'string' && /^[A-Za-z0-9._~+/=-]{1,16384}$/.test(value) ? value : ''
}

function random() {
  return randomBytes(32).toString('base64url')
}

function sharing(value) {
  return !!value?.scopes?.includes('resource.invoke') && value.scopes.includes('chatgpt.tokens.use.direct')
}

function publicStatus(value) {
  const connected = !!value?.idToken && !value.requiresSignIn
  return {
    connected, sharing: connected && sharing(value),
    name: connected ? value.name : '', email: connected ? value.email : '',
  }
}

function identityError() { return new AuthError('无法验证 ChatGPT 账号授权，请重新登录。') }

async function readJSON(response) {
  if (Number(response.headers.get('content-length')) > MAX_JSON || !response.body) {
    throw new AuthError('ChatGPT 返回了无法读取的授权资料。', 'CHATGPT_RESPONSE')
  }
  let size = 0
  const chunks = []
  for await (const chunk of response.body) {
    size += chunk.length
    if (size > MAX_JSON) throw new AuthError('ChatGPT 返回的授权资料过大。', 'CHATGPT_RESPONSE')
    chunks.push(chunk)
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) }
  catch { throw new AuthError('ChatGPT 返回了无法读取的授权资料。', 'CHATGPT_RESPONSE') }
}

async function atomicWrite(file, value) {
  const temporary = file + '.' + randomUUID() + '.partial'
  try {
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(temporary, JSON.stringify(value) + '\n', { mode: 0o600, flag: 'wx' })
    await fs.rename(temporary, file)
  } finally {
    await fs.unlink(temporary).catch(() => {})
  }
}

function equalsState(actual, expected) {
  const actualBytes = Buffer.from(actual || '')
  const expectedBytes = Buffer.from(expected)
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes)
}

function listenForAuthorization(state, signal) {
  let resolveCallback
  let rejectCallback
  let received = false
  const callback = new Promise((resolve, reject) => { resolveCallback = resolve; rejectCallback = reject })
  callback.catch(() => {})
  const abort = () => rejectCallback(signal.reason)
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) abort()
  const server = http.createServer({ maxHeaderSize: 8192, requestTimeout: 10000, headersTimeout: 10000 }, (request, response) => {
    const origin = '127.0.0.1:' + server.address()?.port
    response.setHeader('Content-Type', 'text/html; charset=utf-8')
    response.setHeader('Cache-Control', 'no-store')
    response.setHeader('Referrer-Policy', 'no-referrer')
    response.setHeader('Content-Security-Policy', "default-src 'none'")
    const say = (status, message) => response.writeHead(status).end('<!doctype html><meta charset="utf-8"><p>' + message + '</p>')
    if (request.method !== 'GET' || request.headers.host !== origin || !request.url?.startsWith('/') || request.url.startsWith('//')) {
      say(404, '此页面只处理 Comic Manage 登录回调。')
      return
    }
    const url = new URL(request.url, 'http://' + origin)
    const query = url.searchParams
    if (url.pathname !== CALLBACK || query.getAll('state').length !== 1 || !equalsState(query.get('state'), state)) {
      say(400, '此授权回调无效。请返回 Comic Manage。')
      return
    }
    if (received || signal.aborted) {
      say(409, '此次授权已结束。请返回 Comic Manage。')
      return
    }
    received = true
    say(200, '已收到授权回调。请返回 Comic Manage 查看结果。')
    if (query.get('error')) {
      rejectCallback(new AuthError('ChatGPT 授权未获批准。', 'CHATGPT_DENIED'))
      return
    }
    const code = token(query.get('code'))
    const clientId = text(query.get('client_id'))
    if (!code || query.getAll('code').length !== 1 || query.getAll('client_id').length > 1) {
      rejectCallback(identityError())
      return
    }
    resolveCallback({ code, clientId })
  })
  const ready = new Promise((resolve, reject) => {
    server.once('error', () => reject(new AuthError('无法打开本机授权回调，请重试。', 'CHATGPT_NETWORK')))
    server.listen(0, '127.0.0.1', () => resolve('http://127.0.0.1:' + server.address().port + CALLBACK))
  })
  return { ready, callback, close() {
    signal.removeEventListener('abort', abort)
    server.close()
    server.closeAllConnections()
  } }
}

class ChatGPTAuth {
  constructor(dataDir, safeStorage, options = {}) {
    this.hostFile = path.join(dataDir, 'chatgpt-host.json')
    this.sessionFile = path.join(dataDir, 'chatgpt-session.json')
    this.safeStorage = safeStorage
    this.fetch = options.fetch || globalThis.fetch
    this.now = options.now || Date.now
    this.timeoutMs = options.timeoutMs || 180000
    this.pending = Promise.resolve()
    this.attempt = null
  }

  change(task) {
    const result = this.pending.then(task)
    this.pending = result.catch(() => {})
    return result
  }

  async request(url, init = {}, signal) {
    try {
      const timeout = AbortSignal.timeout(15000)
      return await this.fetch(url, { ...init, redirect: 'error', signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
    } catch {
      if (signal?.aborted) throw signal.reason
      throw new AuthError('暂时无法连接 ChatGPT 授权服务，请稍后重试。', 'CHATGPT_NETWORK')
    }
  }

  async form(url, values, signal) {
    return this.request(url, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(values) }, signal)
  }

  async exchange(values, signal) {
    const reply = await this.form(TOKEN, { ...values, resource: RESOURCE }, signal)
    if ([400, 401, 403].includes(reply.status)) {
      const failure = await readJSON(reply).catch(() => ({}))
      const code = typeof failure?.error === 'string' ? failure.error : failure?.error?.code
      if (['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
           'refresh_token_invalidated', 'refresh_token_reused'].includes(code)) {
        throw new AuthError('ChatGPT 授权已失效，请重新登录。', 'CHATGPT_LOGIN_REQUIRED')
      }
      throw new AuthError('ChatGPT 暂未接受本次授权，请检查账户或工作区权限。', 'CHATGPT_ACCESS')
    }
    if (!reply.ok) throw new AuthError('ChatGPT 授权服务暂时不可用。', 'CHATGPT_NETWORK')
    return readJSON(reply)
  }

  async hostId() {
    try {
      const { id } = JSON.parse(await fs.readFile(this.hostFile, 'utf8'))
      if (typeof id !== 'string' || !/^urn:uuid:[a-f0-9-]{36}$/u.test(id)) throw new Error()
      return id
    } catch (error) {
      if (error.code !== 'ENOENT') throw new AuthError('无法读取本机 ChatGPT 授权标识，请检查资料目录。')
    }
    const id = 'urn:uuid:' + randomUUID()
    await atomicWrite(this.hostFile, { id })
    return id
  }

  async read() {
    try {
      const saved = JSON.parse(await fs.readFile(this.sessionFile, 'utf8'))
      if (saved.version !== 1 || typeof saved.encrypted !== 'string' || saved.encrypted.length > 160000) throw new Error()
      const opened = await this.safeStorage.decryptStringAsync(Buffer.from(saved.encrypted, 'base64'))
      const value = JSON.parse(opened.result)
      if (!text(value.clientId) || !text(value.subject) || !Array.isArray(value.scopes)) throw new Error()
      return value
    } catch (error) {
      if (error.code === 'ENOENT') return null
      throw new AuthError('无法读取本机 ChatGPT 授权，请解除本机授权后重新登录。')
    }
  }

  async save(value) {
    try {
      if (!await this.safeStorage.isAsyncEncryptionAvailable()) throw new Error()
      const encrypted = await this.safeStorage.encryptStringAsync(JSON.stringify(value))
      await atomicWrite(this.sessionFile, { version: 1, encrypted: encrypted.toString('base64') })
    } catch { throw new AuthError('无法加密保存 ChatGPT 授权，请检查电脑的密钥保护。') }
  }

  async verifyIdentity(idToken, clientId, nonce, signal) {
    try {
      const pieces = idToken.split('.')
      if (pieces.length !== 3 || pieces.some(value => !/^[A-Za-z0-9_-]+$/.test(value))) throw identityError()
      const [encodedHeader, encodedClaims, signature] = pieces
      const header = JSON.parse(Buffer.from(encodedHeader, 'base64url'))
      const claims = JSON.parse(Buffer.from(encodedClaims, 'base64url'))
      if (header.alg !== 'RS256' || !text(header.kid)) throw identityError()
      const response = await this.request(JWKS, {}, signal)
      if (!response.ok) throw identityError()
      const { keys } = await readJSON(response)
      const key = keys.find(item => item.kid === header.kid && item.kty === 'RSA' &&
        (!item.alg || item.alg === 'RS256') && (!item.use || item.use === 'sig'))
      const valid = key && verify('RSA-SHA256', Buffer.from(encodedHeader + '.' + encodedClaims),
        createPublicKey({ key, format: 'jwk' }), Buffer.from(signature, 'base64url'))
      const audience = Array.isArray(claims.aud)
        ? claims.aud.includes(clientId) && (claims.aud.length === 1 || claims.azp === clientId)
        : claims.aud === clientId
      const now = this.now() / 1000
      if (!valid || !audience || claims.iss !== ISSUER || claims.nonce !== nonce || !text(claims.sub) ||
          !Number.isFinite(claims.iat) || claims.iat > now + 30 || !Number.isFinite(claims.exp) || claims.exp <= now || (claims.nbf !== undefined && (!Number.isFinite(claims.nbf) || claims.nbf > now + 30))) {
        throw identityError()
      }
      return { subject: claims.sub, name: text(claims.name), email: claims.email_verified === true ? text(claims.email) : '' }
    } catch {
      if (signal.aborted) throw signal.reason
      throw identityError()
    }
  }

  credentials(data) {
    const accessToken = token(data?.access_token)
    const refreshToken = token(data?.refresh_token)
    const scopes = text(data?.scope, 4096).split(/\s+/u).filter(Boolean)
    const lifetime = Number(data?.expires_in)
    if (!accessToken || !Number.isFinite(lifetime) || lifetime <= 0 ||
        (data.token_type && data.token_type.toLowerCase() !== 'bearer')) throw identityError()
    return { accessToken, refreshToken, scopes, expiresAt: this.now() + lifetime * 1000, requiresSignIn: false }
  }

  async get() {
    return publicStatus(await this.read())
  }

  cancel() {
    this.attempt?.abort(new AuthError('已取消 ChatGPT 登录。', 'CHATGPT_CANCELLED'))
  }

  async signIn(openExternal) {
    if (this.attempt) throw new AuthError('ChatGPT 授权正在进行。')
    const attempt = new AbortController()
    this.attempt = attempt
    const signal = attempt.signal
    const timer = setTimeout(() => attempt.abort(new AuthError('ChatGPT 登录已超时，请重试。', 'CHATGPT_TIMEOUT')), this.timeoutMs)
    const nonce = random()
    const verifier = random()
    const state = random()
    let listener
    try {
      const [host, previous] = await Promise.all([this.hostId(), this.read()])
      signal.throwIfAborted()
      listener = listenForAuthorization(state, signal)
      const redirect = await listener.ready
      signal.throwIfAborted()
      const query = new URLSearchParams({
        client_id: previous?.clientId || 'dynamic_agent_client', ext_agent_host_id: host,
        response_type: 'code', redirect_uri: redirect, scope: SCOPES, resource: RESOURCE,
        state, nonce, code_challenge_method: 'S256',
        code_challenge: createHash('sha256').update(verifier).digest('base64url'),
        ...(previous ? (previous.email ? { login_hint: previous.email } : {}) : { agent_name_hint: 'Comic Manage' }),
      })
      try { await openExternal(AUTHORIZE + '?' + query) }
      catch { throw new AuthError('无法打开系统浏览器，请检查默认浏览器设置。') }
      const callback = await listener.callback
      const clientId = previous?.clientId || callback.clientId
      if (!/^oaiapp_[A-Za-z0-9_-]{1,200}$/.test(clientId) || (callback.clientId && callback.clientId !== clientId)) throw identityError()
      const body = await this.exchange({ grant_type: 'authorization_code', client_id: clientId,
        code: callback.code, code_verifier: verifier, redirect_uri: redirect }, signal)
      const idToken = token(body.id_token)
      if (!idToken) throw identityError()
      const identity = await this.verifyIdentity(idToken, clientId, nonce, signal)
      if (previous && identity.subject !== previous.subject) throw identityError()
      const value = { ...identity, ...this.credentials(body), clientId, idToken }
      await this.change(async () => { signal.throwIfAborted(); await this.save(value) })
      return publicStatus(value)
    } finally {
      clearTimeout(timer)
      listener?.close()
      this.attempt = null
    }
  }

  accessToken() {
    return this.change(async () => {
      const account = await this.read()
      if (!account?.accessToken || account.requiresSignIn) {
        throw new AuthError('请先在模型管理中登录 ChatGPT。', 'CHATGPT_LOGIN_REQUIRED')
      }
      if (!sharing(account)) throw new AuthError('请授权使用 ChatGPT 套餐用量。', 'CHATGPT_SCOPE')
      if (account.expiresAt > this.now() + 60000) return account.accessToken
      if (!account.refreshToken) throw new AuthError('ChatGPT 授权已过期，请重新登录。', 'CHATGPT_LOGIN_REQUIRED')
      let body
      try {
        body = await this.exchange({ grant_type: 'refresh_token', client_id: account.clientId,
          refresh_token: account.refreshToken })
      } catch (error) {
        if (error.code === 'CHATGPT_LOGIN_REQUIRED') await this.save({ ...registration(account), requiresSignIn: true })
        throw error
      }
      const credentials = this.credentials({ ...body, scope: body.scope ?? account.scopes.join(' ') })
      if (!credentials.refreshToken) throw new AuthError('未收到新会话，请重新登录 ChatGPT。', 'CHATGPT_LOGIN_REQUIRED')
      const updated = { ...account, ...credentials }
      await this.save(updated)
      if (!sharing(updated)) throw new AuthError('请重新授权使用 ChatGPT 套餐用量。', 'CHATGPT_SCOPE')
      return updated.accessToken
    })
  }

  async modelList() {
    const accessToken = await this.accessToken()
    const response = await this.request(RESOURCE + '/models', { headers: { Authorization: 'Bearer ' + accessToken } })
    if (response.status === 429) throw new AuthError('ChatGPT 达到当前使用限制，请查看用量设置。', 'CHATGPT_RATE_LIMIT')
    if (!response.ok) throw new AuthError('无法获取 ChatGPT 套餐模型，请检查授权和套餐用量。', 'CHATGPT_MODELS')
    const result = await readJSON(response)
    if (!Array.isArray(result.models)) throw new AuthError('无法读取 ChatGPT 返回的模型列表。', 'CHATGPT_RESPONSE')
    const models = new Map()
    for (const item of result.models) {
      const slug = text(item?.slug)
      if (item?.visibility === 'list' && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(slug) && !models.has(slug)) {
        models.set(slug, { slug, name: text(item.display_name) || slug })
      }
    }
    return { models: [...models.values()] }
  }

  disconnect() {
    this.cancel()
    return this.change(async () => {
      let account
      try { account = await this.read() } catch {
        await fs.unlink(this.sessionFile).catch(error => { if (error.code !== 'ENOENT') throw error })
        return { ...publicStatus(null), revocationConfirmed: false }
      }
      let confirmed = true
      if (account?.refreshToken) {
        confirmed = false
        for (let retry = 0; retry < 2 && !confirmed; retry++) {
          if (retry) await new Promise(resolve => setTimeout(resolve, 250))
          try {
            const response = await this.form(REVOKE, { token: account.refreshToken,
              token_type_hint: 'refresh_token', client_id: account.clientId })
            confirmed = response.ok
            if (response.status < 500) break
          } catch { /* The local session is still cleared after the retry. */ }
        }
      }
      if (account) {
        try { await this.save(registration(account)) } catch {
          await fs.unlink(this.sessionFile).catch(error => { if (error.code !== 'ENOENT') throw error })
        }
      }
      return { ...publicStatus(null), ...(!confirmed ? { revocationConfirmed: false } : {}) }
    })
  }
}

function registration(account) {
  return { clientId: account.clientId, subject: account.subject,
    name: account.name, email: account.email, scopes: [] }
}

module.exports = { ChatGPTAuth, CHATGPT_USAGE_URL }
