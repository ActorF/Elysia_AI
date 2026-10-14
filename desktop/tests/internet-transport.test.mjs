/** Verify public HTTPS authorization, pinned DNS, redirects, proxy intent, bounded responses and physical cancellation. */
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { PassThrough } from 'node:stream'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { InternetTransport, isPublicInternetAddress, parseInternetUrl } from '../dist-electron/internet-transport.js'
import { defaultInternetPolicy } from '../dist-electron/internet-policy-protocol.js'

const code = expected => error => error.code === expected
const readOptions = { accept: 'application/json', maxBytes: 1024 }
async function policy(t, changes = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-transport-'))
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-transport-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const service = new InternetPolicyService(path.join(directory, 'policy.json'))
  await service.update({ expectedRevision: 0, enabled: true, allowedDomains: ['api.github.com', 'en.wikipedia.org'],
    proxy: { mode: 'direct' }, timeoutMs: 1000, ...changes })
  return service
}
function synthetic(responses = [{ status: 200, headers: { 'content-type': 'application/json' }, body: '{}' }]) {
  const calls = [], resolutions = [], proxies = []
  let held = null
  const native = {
    async resolve(host, signal) { resolutions.push(host); assert.equal(signal.aborted, false); return ['140.82.114.5'] },
    async proxy(target) { proxies.push(target); throw new Error('unexpected proxy') },
    request(options, callback) {
      calls.push(options)
      const outgoing = new EventEmitter(), socket = new EventEmitter()
      socket.closed = false
      socket.destroy = () => { socket.destroyed = true }
      outgoing.destroy = () => { outgoing.destroyed = true; return outgoing }
      outgoing.end = () => queueMicrotask(() => {
        outgoing.emit('socket', socket)
        const spec = responses.shift() ?? { hold: true }
        const incoming = new PassThrough({ autoDestroy: false })
        incoming.statusCode = spec.status ?? 200
        incoming.headers = spec.headers ?? { 'content-type': 'application/json' }
        incoming.destroy = () => { incoming.destroyedByTransport = true; return incoming }
        held = { outgoing, incoming, socket,
          close: () => { incoming.emit('close'); outgoing.emit('close'); socket.closed = true; socket.emit('close') } }
        callback(incoming)
        if (!spec.hold) {
          incoming.once('end', () => held.close())
          incoming.end(spec.body ?? '{}')
        }
      })
      return outgoing
    },
  }
  return { native, calls, resolutions, proxies, get held() { return held } }
}
async function until(check) { for (let count = 0; count < 100; count += 1) { if (check()) return; await new Promise(resolve => setImmediate(resolve)) } assert.fail('fixture did not start') }

test('Strict URL preflight rejects non-HTTPS, credentials, private literals, disguised authorities and unauthorized subdomains', () => {
  const state = { ...defaultInternetPolicy(), allowedDomains: ['api.github.com'] }
  for (const value of ['http://api.github.com', 'https://api.github.com:444/', 'https://u:p@api.github.com/',
    'https://127.0.0.1/', 'https://[::1]/', 'https://api.github.com./', 'https://api.github.com/#secret',
    'https://api.github.com\\@evil.com/', 'https://api.github.com\n/', 'https://other.api.github.com/']) {
    assert.throws(() => parseInternetUrl(value, state))
  }
  assert.equal(parseInternetUrl('https://api.github.com:443/repos/a/b', state).href, 'https://api.github.com/repos/a/b')
})

test('Address classification rejects local/reserved IPv4, mapped IPv6, transitions and documentation ranges', () => {
  for (const address of ['0.0.0.0', '10.1.2.3', '100.64.0.1', '127.0.0.1', '169.254.169.254', '172.16.0.1', '192.168.1.1',
    '192.0.0.9', '192.0.2.1', '192.88.99.1', '198.18.0.1', '198.51.100.2', '203.0.113.2', '224.0.0.1', '255.255.255.255',
    '::1', '::ffff:127.0.0.1', '::ffff:7f00:1', 'fe80::1', 'fc00::1', 'ff02::1', '2001:db8::1', '2002:7f00:1::', '3fff::1']) {
    assert.equal(isPublicInternetAddress(address), false, address)
  }
  for (const address of ['140.82.114.5', '8.8.8.8', '1.1.1.1', '2606:4700:4700::1111', '2001:4860:4860::8888']) {
    assert.equal(isPublicInternetAddress(address), true, address)
  }
})

test('Off policy rejects the same read before any DNS, proxy or HTTP operation', async t => {
  const service = await policy(t, { enabled: false }), fake = synthetic()
  await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions), code('disabled'))
  assert.equal(fake.resolutions.length + fake.calls.length + fake.proxies.length, 0)
})

test('Requests pin the checked address while preserving SNI/Host and send no cookie/auth/proxy environment', async t => {
  const service = await policy(t), fake = synthetic()
  const result = await new InternetTransport(service, { native: fake.native }).read('https://api.github.com/repos/a/b', readOptions)
  assert.equal(result.url, 'https://api.github.com/repos/a/b')
  assert.equal(Buffer.from(result.body).toString(), '{}')
  const sent = fake.calls[0]
  assert.equal(sent.hostname, '140.82.114.5')
  assert.equal(sent.servername, 'api.github.com')
  assert.equal(sent.headers.Host, 'api.github.com')
  assert.equal(sent.method, 'GET')
  assert.equal(sent.rejectUnauthorized, true)
  assert.equal(sent.headers.Authorization, undefined)
  assert.equal(sent.headers.Cookie, undefined)
  assert.equal(sent.agent, false)
  assert.equal(service.hasActiveWork(), false)
})

test('Mixed public/private DNS and all private answers are rejected before connection without fallback', async t => {
  const service = await policy(t)
  for (const addresses of [['127.0.0.1'], ['140.82.114.5', '10.0.0.1'], ['::ffff:127.0.0.1'], []]) {
    const fake = synthetic(); fake.native.resolve = async () => addresses
    await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions),
      code(addresses.length === 0 ? 'dns_failed' : 'private_address'))
    assert.equal(fake.calls.length, 0)
  }
})

test('Every redirect is reauthorized and re-resolved; denied, looped and disabled redirects make no next request', async t => {
  const service = await policy(t)
  const redirects = synthetic([{ status: 302, headers: { location: 'https://en.wikipedia.org/page' }, body: '' },
    { status: 200, headers: { 'content-type': 'application/json' }, body: '{"ok":true}' }])
  const result = await new InternetTransport(service, { native: redirects.native }).read('https://api.github.com/', readOptions)
  assert.equal(result.url, 'https://en.wikipedia.org/page')
  assert.deepEqual(redirects.resolutions, ['api.github.com', 'en.wikipedia.org'])
  for (const [location, options, expected] of [['https://evil.com/', readOptions, 'domain_denied'],
    ['http://api.github.com/', readOptions, 'invalid_url'], ['https://api.github.com/', readOptions, 'redirect_denied'],
    ['/next', { ...readOptions, maxRedirects: 0 }, 'redirect_denied']]) {
    const fake = synthetic([{ status: 302, headers: { location }, body: '' }])
    await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', options), code(expected))
    assert.equal(fake.calls.length, 1)
  }
})

test('Redirect hop limit and DNS rebinding stop before a newly unsafe destination is contacted', async t => {
  const service = await policy(t)
  const fake = synthetic(Array.from({ length: 4 }, (_, index) => ({ status: 302, headers: { location: `/hop${index}` }, body: '' })))
  await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions), code('redirect_denied'))
  assert.equal(fake.calls.length, 4)
  const rebound = synthetic([{ status: 302, headers: { location: '/next' }, body: '' }])
  let lookups = 0
  rebound.native.resolve = async () => ++lookups === 1 ? ['140.82.114.5'] : ['169.254.169.254']
  await assert.rejects(new InternetTransport(service, { native: rebound.native }).read('https://api.github.com/', readOptions), code('private_address'))
  assert.equal(rebound.calls.length, 1)
})

test('Wrong types, compressed responses and excessive advertised/streamed sizes destroy and fully drain requests', async t => {
  const service = await policy(t)
  for (const [headers, body, expected] of [[{ 'content-type': 'text/html' }, '{}', 'invalid_response'],
    [{ 'content-type': 'application/json', 'content-encoding': 'gzip' }, '{}', 'invalid_response'],
    [{ 'content-type': 'application/json', 'content-length': '9999' }, '{}', 'limit_exceeded'],
    [{ 'content-type': 'application/json' }, 'x'.repeat(1025), 'limit_exceeded']]) {
    const fake = synthetic([{ headers, body }])
    await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions), code(expected))
    assert.equal(service.hasActiveWork(), false)
  }
})

test('Off cancels an in-flight request but keeps its lease until both response and actual socket close', async t => {
  const service = await policy(t), fake = synthetic([{ hold: true }])
  const reading = new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions)
  const rejection = assert.rejects(reading, error => ['cancelled', 'policy_changed'].includes(error.code))
  await until(() => fake.held !== null)
  const state = service.getState()
  await service.update({ expectedRevision: state.revision, enabled: false, allowedDomains: state.allowedDomains, proxy: state.proxy, timeoutMs: state.timeoutMs })
  assert.equal(fake.held.outgoing.destroyed, true)
  assert.equal(fake.held.socket.destroyed, true)
  assert.equal(service.hasActiveWork(), true)
  fake.held.incoming.emit('close'); fake.held.outgoing.emit('close')
  assert.equal(service.hasActiveWork(), true)
  fake.held.socket.emit('close')
  await rejection; await service.whenIdle()
})

test('Cancelled DNS retains its physical lease and cannot initiate HTTP after its late resolver result', async t => {
  const service = await policy(t), fake = synthetic()
  let resolved, signal
  fake.native.resolve = async (_host, activeSignal) => { signal = activeSignal; return new Promise(resolve => { resolved = resolve }) }
  const abort = new AbortController()
  const reading = new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', { ...readOptions, signal: abort.signal })
  const rejection = assert.rejects(reading, error => ['cancelled', 'policy_changed'].includes(error.code))
  await until(() => resolved !== undefined)
  abort.abort()
  assert.equal(signal.aborted, true)
  assert.equal(service.hasActiveWork(), true)
  resolved(['140.82.114.5'])
  await rejection
  assert.equal(fake.calls.length, 0)
  assert.equal(service.hasActiveWork(), false)
})

test('One total timeout includes resolver time and releases only after resolver cancellation drains', async t => {
  const service = await policy(t), fake = synthetic()
  let released
  fake.native.resolve = async (_host, signal) => new Promise(resolve => {
    signal.addEventListener('abort', () => { released = () => resolve(['140.82.114.5']) }, { once: true })
  })
  const reading = new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions)
  const rejection = assert.rejects(reading, code('timeout'))
  await new Promise(resolve => setTimeout(resolve, 1100))
  assert.equal(service.hasActiveWork(), true)
  assert.equal(typeof released, 'function')
  released(); await rejection
  assert.equal(fake.calls.length, 0)
})

test('Explicit local proxy receives only the pinned target and abort signal before any HTTPS request', async t => {
  const service = await policy(t, { proxy: { mode: 'local-http', port: 7890 } }), fake = synthetic()
  fake.native.proxy = async target => { fake.proxies.push(target); throw new Error('private proxy diagnostic') }
  await assert.rejects(new InternetTransport(service, { native: fake.native }).read('https://api.github.com/', readOptions), error => {
    assert.doesNotMatch(error.message, /private proxy/u)
    return error.code === 'network_failed'
  })
  assert.equal(fake.proxies[0].port, 7890)
  assert.equal(fake.proxies[0].address, '140.82.114.5')
  assert.equal(fake.proxies[0].hostname, 'api.github.com')
  assert.equal(fake.calls.length, 0)
})
