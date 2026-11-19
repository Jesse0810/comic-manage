'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { createHash, generateKeyPairSync, sign } = require('node:crypto')
const { ChatGPTAuth } = require('../chatgpt-auth.cjs')

const issuer = 'https://auth.openai.com'
const authorizeUrl = issuer + '/api/accounts/authorize'
const tokenUrl = issuer + '/api/accounts/oauth/token'
const revokeUrl = issuer + '/api/accounts/oauth/revoke'
const jwksUrl = issuer + '/.well-known/jwks.json'
const discoveryUrl = issuer + '/.well-known/openid-configuration'
const resource = 'https://api.openai.com/v1'
const clientId = 'oaiapp_test_registration'
const granted = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
const keys = generateKeyPairSync('rsa', { modulusLength: 2048 })
const jwks = { keys: [{ ...keys.publicKey.export({ format: 'jwk' }), kid: 'test-auth-key', alg: 'RS256', use: 'sig' }] }
const realFetch = globalThis.fetch

function encode(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

async function fixture(t, options = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'comic-chatgpt-test-'))
  t.after(() => fs.rm(dir, { recursive: true, force: true }))
  const openRequests = []
  const tokenRequests = []
  const revocations = []
  let now = Date.now()
  const storage = {
    getSelectedStorageBackend: () => 'dpapi',
    isEncryptionAvailable: () => true,
    encryptString: text => Buffer.from([...text].reverse().join(''), 'utf8'),
    decryptString: buffer => [...buffer.toString('utf8')].reverse().join(''),
  }
  storage.isAsyncEncryptionAvailable = async () => true
  storage.encryptStringAsync = async text => storage.encryptString(text)
  storage.decryptStringAsync = async buffer => ({ result: storage.decryptString(buffer), shouldReEncrypt: false })

  function idToken() {
    const attempt = openRequests.at(-1)
    const iat = Math.floor(now / 1000)
    const payload = { iss: issuer, aud: [clientId], sub: 'mock-subject', nonce: attempt.searchParams.get('nonce'),
      exp: iat + 3600, iat, email: 'synthetic@example.test', name: 'Synthetic Account', email_verified: true,
      ...options.claims }
    const input = encode({ kid: 'test-auth-key', alg: 'RS256', typ: 'JWT' }) + '.' + encode(payload)
    const signed = sign('RSA-SHA256', Buffer.from(input), keys.privateKey)
    if (options.badSignature) signed[0] ^= 1
    return input + '.' + signed.toString('base64url')
  }

  async function fetch(urlInput, init = {}) {
    const url = String(urlInput instanceof Request ? urlInput.url : urlInput)
    const form = new URLSearchParams(init.body)
    if (url === discoveryUrl) return Response.json({
      issuer, authorization_endpoint: authorizeUrl, token_endpoint: tokenUrl,
      jwks_uri: jwksUrl, revocation_endpoint: revokeUrl,
    })
    if (url === jwksUrl) return options.jwksResponse?.() ?? Response.json(jwks)
    if (url === tokenUrl) {
      tokenRequests.push(form)
      if (form.get('grant_type') === 'refresh_token') {
        assert.equal(form.get('refresh_token'), 'refresh_fixture_1')
        assert.equal(form.get('scope'), null)
        if (options.refreshRefusal) return Response.json({ error: 'policy_denied' }, { status: 403 })
        if (options.refreshError) return Response.json({ error: 'invalid_grant' }, { status: 400 })
        return Response.json({ access_token: 'access_fixture_2', refresh_token: 'refresh_fixture_2',
          token_type: 'Bearer', expires_in: 3600, scope: granted })
      }
      const attempt = openRequests.at(-1)
      assert.equal(form.get('grant_type'), 'authorization_code')
      assert.equal(form.get('code'), 'local-test-code')
      assert.equal(form.get('redirect_uri'), attempt.searchParams.get('redirect_uri'))
      assert.equal(form.get('resource'), resource)
      assert.equal(form.get('client_id'), clientId)
      assert.equal(createHash('sha256').update(form.get('code_verifier')).digest('base64url'),
        attempt.searchParams.get('code_challenge'))
      return Response.json({ access_token: 'access_fixture_1', refresh_token: 'refresh_fixture_1',
        id_token: idToken(), token_type: 'Bearer', expires_in: 3600, scope: options.scope ?? granted })
    }
    if (url === resource + '/models') {
      assert.equal(new Headers(init.headers).get('authorization'), 'Bearer access_fixture_1')
      return options.modelResponse?.() ?? Response.json({ models: [
        { visibility: 'list', slug: 'gpt-test', display_name: 'Fixture Model' },
        { visibility: 'hidden', slug: 'internal-fixture', display_name: 'Internal' },
      ] })
    }
    if (url === revokeUrl) {
      revocations.push(form)
      return new Response(null, { status: options.revokeStatus ?? 200 })
    }
    throw new Error('Fixture blocked unexpected network')
  }

  const make = () => new ChatGPTAuth(dir, storage, { fetch, now: () => now, timeoutMs: 3000 })
  async function open(urlText) {
    const url = new URL(urlText)
    openRequests.push(url)
    assert.equal(url.origin + url.pathname, authorizeUrl)
    const query = url.searchParams
    assert.equal(query.get('code_challenge_method'), 'S256')
    assert.equal(query.get('response_type'), 'code')
    assert.equal(query.get('resource'), resource)
    assert.match(query.get('ext_agent_host_id'), /^urn:uuid:/)
    assert.notEqual(query.get('nonce'), query.get('state'))
    const callback = new URL(query.get('redirect_uri'))
    assert.equal(callback.protocol, 'http:')
    assert.equal(callback.hostname, '127.0.0.1')
    callback.searchParams.set('code', 'local-test-code')
    callback.searchParams.set('state', query.get('state'))
    if (query.get('client_id') === 'dynamic_agent_client') callback.searchParams.set('client_id', clientId)
    if (options.wrongStateFirst) {
      const forged = new URL(callback)
      forged.searchParams.set('state', 'forged')
      assert.equal((await realFetch(forged)).status, 400)
    }
    await realFetch(callback).then(response => response.arrayBuffer())
  }

  return { dir, storage, make, open, openRequests, tokenRequests, revocations, expire: () => { now += 3600_000 } }
}

test('browser sign-in verifies the callback and stores only protected credentials', async t => {
  const scenario = await fixture(t, { wrongStateFirst: true })
  const auth = scenario.make()
  assert.equal((await auth.get()).connected, false)
  const connected = await auth.signIn(scenario.open)
  assert.equal(connected.connected, true)
  assert.equal(connected.sharing, true)
  assert.equal(connected.email, 'synthetic@example.test')
  assert.equal(scenario.openRequests[0].searchParams.get('client_id'), 'dynamic_agent_client')
  assert.equal(scenario.openRequests[0].searchParams.get('agent_name_hint'), 'Comic Manage')
  assert.equal(scenario.tokenRequests.length, 1)
  assert.doesNotMatch(JSON.stringify(connected), /access_fixture|refresh_fixture|mock-subject/)
  const content = await Promise.all((await fs.readdir(scenario.dir)).map(name => fs.readFile(path.join(scenario.dir, name), 'utf8')))
  assert.doesNotMatch(content.join('\n'), /access_fixture|refresh_fixture/)
  const reopened = scenario.make()
  assert.equal((await reopened.get()).connected, true)
  assert.equal(await reopened.accessToken(), 'access_fixture_1')
  assert.deepEqual((await reopened.modelList()).models, [{ slug: 'gpt-test', name: 'Fixture Model' }])
  const disconnected = await reopened.disconnect()
  assert.equal(disconnected.connected, false)
  assert.equal(scenario.revocations.length, 1)
  assert.equal(scenario.revocations[0].get('token'), 'refresh_fixture_1')
  assert.equal(scenario.revocations[0].get('client_id'), clientId)
  assert.equal((await scenario.make().get()).connected, false)
  await assert.rejects(reopened.accessToken())
  await reopened.signIn(scenario.open)
  const first = scenario.openRequests[0].searchParams
  const second = scenario.openRequests[1].searchParams
  assert.equal(second.get('client_id'), clientId)
  assert.equal(second.get('ext_agent_host_id'), first.get('ext_agent_host_id'))
  assert.notEqual(second.get('state'), first.get('state'))
  assert.notEqual(second.get('nonce'), first.get('nonce'))
  assert.notEqual(second.get('code_challenge'), first.get('code_challenge'))
})


test('a decompressed model catalog larger than the OAuth response limit still returns visible options', async t => {
  const options = {
    // The mock returns the decoded body while retaining the compressed wire length.
    modelResponse: () => Response.json({ models: [
      {
        visibility: 'list', slug: 'gpt-test', display_name: 'Fixture Model',
        descriptions: 'x'.repeat(600 * 1024),
      },
      { visibility: 'list', slug: 'gpt-test', display_name: 'Duplicate model' },
      { visibility: 'hidden', slug: 'internal-fixture', display_name: 'Internal' },
    ] }, { headers: { 'Content-Encoding': 'gzip', 'Content-Length': '1024' } }),
  }
  const scenario = await fixture(t, options)
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  assert.deepEqual(await auth.modelList(), { models: [{ slug: 'gpt-test', name: 'Fixture Model' }] })
  assert.equal((await auth.get()).connected, true)
})

function catalogTooLarge(error) {
  assert.equal(error.code, 'CHATGPT_RESPONSE')
  assert.equal(error.message, 'ChatGPT 返回的模型列表过大，请稍后点「刷新模型」重试。')
  return true
}

test('a model catalog with an oversized declared content length is rejected', async t => {
  const scenario = await fixture(t, {
    modelResponse: () => new Response('{"models":[]}', {
      headers: { 'Content-Type': 'application/json', 'Content-Length': String(16 * 1024 * 1024 + 1) },
    }),
  })
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  await assert.rejects(auth.modelList(), catalogTooLarge)
  assert.equal((await auth.get()).connected, true)
})

test('a streamed model catalog cannot exceed the limit when the length is unavailable', async t => {
  const chunk = new Uint8Array(256 * 1024)
  let remaining = 65
  const scenario = await fixture(t, { modelResponse: () => new Response(new ReadableStream({
    pull(controller) {
      if (remaining > 0) {
        remaining -= 1
        controller.enqueue(chunk)
      } else {
        controller.close()
      }
    },
  }), { headers: { 'Content-Type': 'application/json' } }) })
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  await assert.rejects(auth.modelList(), catalogTooLarge)
  assert.equal((await auth.get()).connected, true)
})

test('the model catalog allowance does not increase the OAuth response limit', async t => {
  const scenario = await fixture(t, {
    jwksResponse: () => Response.json({ ...jwks, descriptions: 'x'.repeat(600 * 1024) }),
  })
  const auth = scenario.make()
  await assert.rejects(auth.signIn(scenario.open), /无法验证 ChatGPT 账号授权/)
  assert.equal((await auth.get()).connected, false)
})

test('concurrent access renews expiring tokens once and persists the rotation', async t => {
  const scenario = await fixture(t)
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  scenario.expire()
  assert.deepEqual(await Promise.all([auth.accessToken(), auth.accessToken(), auth.accessToken()]),
    ['access_fixture_2', 'access_fixture_2', 'access_fixture_2'])
  assert.equal(scenario.tokenRequests.length, 2)
  assert.equal(await scenario.make().accessToken(), 'access_fixture_2')
  await auth.disconnect()
  assert.equal(scenario.revocations[0].get('token'), 'refresh_fixture_2')
})

test('invalid grant clears expired credentials and requires another login', async t => {
  const scenario = await fixture(t, { refreshError: true })
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  scenario.expire()
  await assert.rejects(auth.accessToken(), error => !/access_fixture|refresh_fixture/.test(error.message))
  assert.equal((await auth.get()).connected, false)
  assert.equal((await scenario.make().get()).connected, false)
})

test('ID tokens with a bad signature, nonce, or audience cannot connect an account', async t => {
  for (const options of [{ badSignature: true }, { claims: { nonce: 'wrong' } }, { claims: { aud: ['wrong'] } }]) {
    const scenario = await fixture(t, options)
    const auth = scenario.make()
    await assert.rejects(auth.signIn(scenario.open), error => !/access_fixture|refresh_fixture/.test(error.message))
    assert.equal(scenario.tokenRequests.length, 1)
    assert.equal((await auth.get()).connected, false)
  }
})

test('account identity without plan permission cannot be used for a request', async t => {
  const scenario = await fixture(t, { scope: 'openid profile email offline_access' })
  const auth = scenario.make()
  const connected = await auth.signIn(scenario.open)
  assert.equal(connected.connected, true)
  assert.equal(connected.sharing, false)
  await assert.rejects(auth.accessToken())
  await assert.rejects(auth.modelList())
})

test('cancel closes the callback listener without exchanging or saving credentials', async t => {
  const scenario = await fixture(t)
  const auth = scenario.make()
  let openBrowser
  const browserOpened = new Promise(resolve => { openBrowser = resolve })
  const pending = auth.signIn(openBrowser)
  const rejection = assert.rejects(pending)
  const opened = new URL(await browserOpened)
  await auth.cancel()
  await rejection
  const callback = new URL(opened.searchParams.get('redirect_uri'))
  callback.searchParams.set('state', opened.searchParams.get('state'))
  callback.searchParams.set('code', 'local-test-code')
  callback.searchParams.set('client_id', clientId)
  await assert.rejects(realFetch(callback))
  assert.deepEqual(scenario.tokenRequests, [])
  assert.equal((await auth.get()).connected, false)
})

test('reauthorizing a different identity cannot overwrite the saved account', async t => {
  const options = {}
  const scenario = await fixture(t, options)
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  options.claims = { sub: 'unrelated-fixture-account', email: 'other@example.test' }
  await assert.rejects(auth.signIn(scenario.open))
  assert.equal((await auth.get()).email, 'synthetic@example.test')
  assert.equal(await auth.accessToken(), 'access_fixture_1')
})

test('temporary account policy refusal preserves registration and encrypted tokens', async t => {
  const options = { refreshRefusal: true }
  const scenario = await fixture(t, options)
  const auth = scenario.make()
  await auth.signIn(scenario.open)
  scenario.expire()
  await assert.rejects(auth.accessToken(), error => error.code === 'CHATGPT_ACCESS')
  assert.equal((await auth.get()).connected, true)
  options.refreshRefusal = false
  assert.equal(await auth.accessToken(), 'access_fixture_2')
})
